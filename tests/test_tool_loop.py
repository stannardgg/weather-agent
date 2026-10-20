"""C 阶段：工具注册表与工具调用循环的测试。

## 这些用例证明什么，不证明什么

证明：工具契约（参数校验、错误降级、身份注入）、注册表的隔离性、
循环的轮次控制、模型不支持工具时的降级、图在不同 AGENT_MODE 下的路由。

不证明：模型「选得对不对」。假模型按脚本返回 tool_calls，它没有语义判断能力。
工具选择准确率只能靠 `evals/` 下的金标集 + 真实模型跑，见 scripts/eval_agent.py。

## 为什么假模型要自己写一个

`GenericFakeChatModel` 的脚本是纯文本，无法返回 `tool_calls`；而本阶段的核心
恰好是「模型返回 tool_calls 后链路怎么走」。所以这里实现一个最小的
BaseChatModel 子类：按脚本依次吐出 AIMessage，并把每次收到的消息记下来供断言。
"""

from typing import Any

import pytest
import pytest_asyncio
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent import nodes, tool_loop
from app.agent.nodes import NODE_TOOL_LOOP
from app.agent.state import Intent
from app.core.config import settings
from app.llm import gateway
from app.rag.retriever import RetrievedChunk
from app.schemas.weather import ResolvedLocation, WeatherResult
from app.tools import builtin
from app.tools.registry import ToolContext, ToolRegistry, ToolSpec
from app.tools.weather import CityNotFoundError


class ToolCallingModel(BaseChatModel):
    """按脚本返回 AIMessage 的假模型，支持 `bind_tools`。

    脚本用尽后重复最后一条：这样「模型一直要调工具」的场景可以只写一条脚本，
    用来验证轮次上限是否真的把死循环截断了。
    """

    script: list[AIMessage]
    calls: list[list[Any]] = []
    bound_tools: list[list[dict]] = []

    @property
    def _llm_type(self) -> str:
        return "test-tool-calling"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        index = min(len(self.calls), len(self.script) - 1)
        self.calls.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.script[index])])

    def bind_tools(self, tools, **kwargs):
        self.bound_tools.append(list(tools))
        return self


class NoToolSupportModel(ToolCallingModel):
    """不实现工具绑定的模型（模拟自建 / 部分兼容网关）。"""

    def bind_tools(self, tools, **kwargs):
        raise NotImplementedError("this model does not support tool calling")


def tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def fake_weather(city: str) -> WeatherResult:
    return WeatherResult(
        provider="test",
        query_city=city,
        resolved=ResolvedLocation(name=city, lat=39.9, lon=116.4),
    )


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """每个用例前后清掉模型替身，避免串味。"""
    gateway.set_chat_model_override(None)
    yield
    gateway.set_chat_model_override(None)


# ---------------------------------------------------------------------------
# 工具契约
# ---------------------------------------------------------------------------


async def test_unknown_tool_returns_error_instead_of_raising():
    """模型幻觉出一个不存在的工具名时，应返回可读错误让它自我纠正。"""
    registry = ToolRegistry()
    result = await registry.execute("no_such_tool", {}, ToolContext(db=None, user_id=1))

    assert result.ok is False
    assert result.error == "unknown_tool"
    assert "不存在" in result.content


def test_duplicate_tool_name_is_a_programming_error():
    """重名必须立刻炸：两个同名工具时，模型选哪个是不确定的。"""
    registry = ToolRegistry()
    spec = ToolSpec(
        name="dup",
        description="",
        parameters={"type": "object", "properties": {}},
        handler=lambda args, ctx: None,  # type: ignore[arg-type,return-value]
    )
    registry.register(spec)
    with pytest.raises(ValueError, match="工具名重复"):
        registry.register(spec)


async def test_tool_exception_is_converted_to_failed_result():
    """执行器抛异常时不能让整轮对话失败——工具失败是运行时常态。"""

    async def boom(args, ctx):
        raise RuntimeError("上游抖动")

    registry = ToolRegistry(
        (ToolSpec(name="boom", description="", parameters={}, handler=boom),)
    )
    result = await registry.execute("boom", {}, ToolContext(db=None, user_id=1))

    assert result.ok is False
    assert result.error == "RuntimeError"


async def test_weather_tool_rejects_missing_city_without_calling_upstream(monkeypatch):
    """缺城市时不打上游接口：既浪费额度，也会把「没识别出城市」变成超时错误。"""
    called = False

    async def should_not_call(city, date=None):
        nonlocal called
        called = True
        return fake_weather(city)

    monkeypatch.setattr(builtin, "get_weather", should_not_call)
    result = await builtin._query_weather({}, ToolContext(db=None, user_id=1))

    assert result.ok is False
    assert result.error == "missing_city"
    assert called is False


async def test_knowledge_tool_injects_user_id_from_context(monkeypatch):
    """模型只能填业务参数；user_id 必须来自服务端上下文，否则就是越权入口。"""
    seen: dict[str, Any] = {}

    async def fake_retrieve(db, query, *, user_id, **kwargs):
        seen["user_id"] = user_id
        seen["query"] = query
        return [
            RetrievedChunk(
                chunk_id="c1",
                document_id=1,
                chunk_index=0,
                source="员工手册.pdf",
                content="年假按工龄计算。",
                distance=0.1,
            )
        ]

    monkeypatch.setattr(builtin.retriever, "retrieve", fake_retrieve)
    result = await builtin._search_knowledge(
        {"query": "年假"}, ToolContext(db=object(), user_id=42)
    )

    assert result.ok is True
    assert seen == {"user_id": 42, "query": "年假"}
    assert "员工手册.pdf" in result.content


# ---------------------------------------------------------------------------
# 循环本身
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def loop_state(session_factory):
    async with session_factory() as session:
        yield {
            "question": "北京今天天气怎么样",
            "user_id": 1,
            "db": session,
            "history": [],
            "notes": [],
            "intent": Intent.WEATHER.value,
        }


async def test_loop_executes_tool_call_and_collects_structured_data(loop_state, monkeypatch):
    """模型调一次天气工具后停止，state 里应拿到结构化结果与调用轨迹。"""

    async def fake_get_weather(city, date=None):
        return fake_weather(city)

    monkeypatch.setattr(builtin, "get_weather", fake_get_weather)
    model = ToolCallingModel(
        script=[tool_call(builtin.WEATHER_TOOL, {"city": "北京"}), AIMessage(content="够了")]
    )
    gateway.set_chat_model_override(model)

    result = await tool_loop.run_tool_loop(loop_state)

    assert result["weather"].resolved.name == "北京"
    assert result["tool_trace"] == [
        {"round": 1, "tool": builtin.WEATHER_TOOL, "ok": True, "error": None}
    ]
    # 工具清单确实交给了模型（否则「自主选择」无从谈起）
    assert [tool["function"]["name"] for tool in model.bound_tools[0]] == [
        builtin.WEATHER_TOOL,
        builtin.KNOWLEDGE_TOOL,
    ]


async def test_loop_stops_at_iteration_limit(loop_state, monkeypatch):
    """模型反复要调工具时必须被截断，否则就是一台烧钱的死循环。"""
    monkeypatch.setattr(settings, "agent_max_tool_iterations", 2)

    async def fake_get_weather(city, date=None):
        return fake_weather(city)

    monkeypatch.setattr(builtin, "get_weather", fake_get_weather)
    # 脚本只有一条，用尽后重复 → 模拟「模型停不下来」
    model = ToolCallingModel(script=[tool_call(builtin.WEATHER_TOOL, {"city": "北京"})])
    gateway.set_chat_model_override(model)

    result = await tool_loop.run_tool_loop(loop_state)

    assert len(model.calls) == 2
    assert any("轮次上限" in note for note in result["notes"])


async def test_loop_falls_back_to_rules_when_model_skips_tools(loop_state, monkeypatch):
    """模型判断「不需要工具」时，不能因此让天气问题变成「取不到数据」。"""
    called = {"weather": 0}

    async def fake_get_weather(city, date=None):
        called["weather"] += 1
        return fake_weather(city)

    monkeypatch.setattr(tool_loop, "get_weather", fake_get_weather)
    model = ToolCallingModel(script=[AIMessage(content="我觉得不用查")])
    gateway.set_chat_model_override(model)

    result = await tool_loop.run_tool_loop(loop_state)

    assert called["weather"] == 1
    assert result["weather"] is not None
    assert any("规则兜底" in note for note in result["notes"])


async def test_loop_degrades_when_model_cannot_bind_tools(loop_state, monkeypatch):
    """自建模型不支持工具调用时，应降级为规则取数而不是 500。"""

    async def fake_get_weather(city, date=None):
        return fake_weather(city)

    monkeypatch.setattr(tool_loop, "get_weather", fake_get_weather)
    gateway.set_chat_model_override(NoToolSupportModel(script=[AIMessage(content="")]))

    result = await tool_loop.run_tool_loop(loop_state)

    assert result["weather"] is not None
    assert any("不支持工具调用" in note for note in result["notes"])


async def test_loop_reports_weather_failure_as_data_gap(loop_state, monkeypatch):
    """天气查询失败不应抛异常，而应留下「没取到」的事实交给 compose 短路。"""

    async def failing_weather(city, date=None):
        raise CityNotFoundError("找不到城市")

    monkeypatch.setattr(builtin, "get_weather", failing_weather)
    monkeypatch.setattr(tool_loop, "get_weather", failing_weather)
    model = ToolCallingModel(
        script=[tool_call(builtin.WEATHER_TOOL, {"city": "不存在市"}), AIMessage(content="")]
    )
    gateway.set_chat_model_override(model)

    result = await tool_loop.run_tool_loop(loop_state)

    assert result["weather"] is None
    assert result["tool_trace"][0]["ok"] is False
    # 兜底也失败，但仍要留下说明，而不是静默返回空
    assert any("天气" in note for note in result["notes"])


async def test_loop_accumulates_tool_round_usage(loop_state, monkeypatch):
    """决策轮的 token 也要计入用量，否则账单对不上。"""

    async def fake_get_weather(city, date=None):
        return fake_weather(city)

    monkeypatch.setattr(builtin, "get_weather", fake_get_weather)
    model = ToolCallingModel(
        script=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": builtin.WEATHER_TOOL, "args": {"city": "北京"}, "id": "c1", "type": "tool_call"}
                ],
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            ),
            AIMessage(
                content="",
                usage_metadata={"input_tokens": 20, "output_tokens": 2, "total_tokens": 22},
            ),
        ]
    )
    gateway.set_chat_model_override(model)

    result = await tool_loop.run_tool_loop(loop_state)

    assert result["usage"] == {"prompt_tokens": 30, "completion_tokens": 7, "total_tokens": 37}


# ---------------------------------------------------------------------------
# 图的路由
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("intent", [Intent.WEATHER.value, Intent.KNOWLEDGE.value, Intent.MIXED.value])
def test_tool_loop_mode_routes_data_intents_to_loop(intent, monkeypatch):
    monkeypatch.setattr(settings, "agent_mode", "tool_loop")
    assert nodes.route_after_classify({"intent": intent}) == NODE_TOOL_LOOP


def test_tool_loop_mode_keeps_chat_on_the_cheap_path(monkeypatch):
    """闲聊不调工具：为一句「你好」触发一次工具决策调用是纯浪费。"""
    monkeypatch.setattr(settings, "agent_mode", "tool_loop")
    assert nodes.route_after_classify({"intent": Intent.CHAT.value}) == nodes.NODE_COMPOSE


def test_router_mode_is_unchanged(monkeypatch):
    """默认模式下路由行为必须与 C 阶段之前完全一致（回归保护）。"""
    monkeypatch.setattr(settings, "agent_mode", "router")
    assert nodes.route_after_classify({"intent": Intent.WEATHER.value}) == nodes.NODE_WEATHER
    assert nodes.route_after_classify({"intent": Intent.KNOWLEDGE.value}) == nodes.NODE_RETRIEVE
    assert nodes.route_after_classify({"intent": Intent.MIXED.value}) == nodes.NODE_WEATHER
    assert nodes.route_after_classify({"intent": Intent.CHAT.value}) == nodes.NODE_COMPOSE
