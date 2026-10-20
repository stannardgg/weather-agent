"""【L4】工具调用循环：让模型自己决定「这一次该查什么」（C 阶段落地）。

## 与 router 模式的区别

**router 模式**（M4）：先由规则/模型判意图，再按三条硬编码的边把请求送到对应节点。
模型对「要不要查天气、要不要查知识库」没有话语权——它是被送到分支里的文本生成器。

**tool_loop 模式**（本文件）：把工具清单交给模型，由它自己决定调哪个、调几次、
参数怎么填。加第三个工具不再需要改图。

两者不是替代关系，而是**并存可切换**（`AGENT_MODE`）：router 是确定性路径，
行为可预测、零额外 token；tool_loop 灵活，但每轮多一次决策调用的开销。
默认值见 `core/config.py` 的说明。

## 一个刻意的架构选择：本节点只取数，不产出答案

直觉写法是「循环到模型不再调工具，把它最后一轮的文本当答案」。本实现**不这么做**，
原因有两个，都是硬伤：

1. **失去流式**。SSE 的逐字输出依赖 `stream_mode="messages"` 从 `generate` 节点捕获
   token。若答案在循环里生成，就只能整体返回，前端要么整段蹦出来，要么自己假装打字。
2. **绕开短路**。`compose` 的「检索为空就固定文案、模型根本没机会编」是防幻觉的
   硬保证。答案若在循环里产生，这道保证就被绕过去了。

所以本节点的产物是 **state 里的 `weather` / `chunks`**，之后仍走
`compose → generate`。代价是决策轮那次生成被丢弃（多花一次 output token），
换来流式与短路全部复用——这个交换在"每轮问答只多几十 token"的量级下是划算的。

## 失败时不能比 router 更差

模型可能不调工具（判断错误）、可能调了但参数不对、可能根本不支持工具调用
（自建模型 / 部分兼容 OpenAI 协议的网关）。这三种情况都要有兜底：
**按意图用规则补一次取数**，行为退回 router 模式。降级不是失败，是保底。
"""

from typing import Any

from app.agent import extract
from app.agent.state import RETRIEVAL_INTENTS, WEATHER_INTENTS, AgentState
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.llm import gateway, prompts
from app.rag import retriever
from app.tools.builtin import KNOWLEDGE_TOOL, WEATHER_TOOL, build_default_registry
from app.tools.registry import ToolContext, ToolResult
from app.tools.weather import WeatherError, get_weather

logger = get_logger(__name__)


class ToolLoopError(AppError):
    """接线上文缺失（如没有数据库会话）。属于代码错误，不是用户输入的错。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, http_status=500)


def _with_notes(state: AgentState, *items: str) -> list[str]:
    """累积过程信息（与 nodes.py 同一套约定，刻意不共用私有函数：
    那个是模块私有实现，跨模块 import 下划线函数会让重构变成连锁反应）。"""
    notes = list(state.get("notes") or [])
    notes.extend(items)
    return notes


def _normalize_weather_args(
    arguments: dict[str, Any], question: str, state: AgentState
) -> dict[str, Any]:
    """补齐/修正天气工具的参数。

    模型常见的两类偏差：
    - 用户说了「明天」却把 date 原样传成 `"明天"`——`get_weather` 只认 YYYY-MM-DD；
    - 用户没说城市，模型自作主张猜一个。

    前者在这里换算（相对日期换算需要用 L4 的 `extract`，L3 的工具层不能反向依赖）；
    后者**不补**——猜城市比让用户补一句更糟，工具会返回「缺少 city」让模型自己问。
    """
    normalized = dict(arguments)
    date_value = normalized.get("date")
    if isinstance(date_value, str) and date_value.strip():
        text = date_value.strip()
        # 只处理「不是标准格式」的情况；标准格式原样放行，避免自己再解析一遍
        if not (len(text) == 10 and text[4] == "-" and text[7] == "-"):
            normalized["date"] = extract.extract_date(text) or state.get("date")
    elif state.get("date"):
        # 调用方在请求体里显式指定的日期优先级最高（与 router 模式的约定一致）
        normalized["date"] = state["date"]
    return normalized


async def _rule_fallback(
    state: AgentState, context: ToolContext, notes: list[str]
) -> dict[str, Any]:
    """按意图用规则补一次取数（router 模式的等价行为）。

    触发条件：模型没取到该取的数据。**只补缺的那一半**——混合意图下天气取到了、
    知识库没取到，就只补知识库。补的前提是意图本身需要它。
    """
    intent = state.get("intent") or ""
    question = state.get("question") or ""
    weather = None
    chunks: list[Any] = []

    if intent in WEATHER_INTENTS:
        city = state.get("city") or extract.extract_city(question)
        if city:
            try:
                weather = await get_weather(city, state.get("date") or extract.extract_date(question))
            except WeatherError as exc:
                notes.append(f"规则兜底：天气查询失败（{city}）：{exc.message}")
            else:
                notes.append(f"规则兜底：天气已取到（{city}）")
        else:
            notes.append("规则兜底：未能识别城市，跳过天气")

    if intent in RETRIEVAL_INTENTS:
        chunks = list(
            await retriever.retrieve(context.db, question, user_id=context.user_id)
        )
        notes.append(f"规则兜底：知识库检索到 {len(chunks)} 条片段")

    return {"weather": weather, "chunks": chunks, "notes": notes}


def _build_decision_messages(state: AgentState) -> list[Any]:
    """决策轮的输入：系统提示 + 历史 + 当前问题。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    messages: list[Any] = [SystemMessage(content=prompts.TOOL_DECISION_PROMPT)]
    messages.extend(state.get("history") or [])
    messages.append(HumanMessage(content=state.get("question") or ""))
    return messages


async def run_tool_loop(state: AgentState) -> dict[str, Any]:
    """节点：模型自主调用工具取数，把结果写回 state。"""
    db = state.get("db")
    user_id = state.get("user_id")
    if db is None or user_id is None:
        raise ToolLoopError("工具循环缺少数据库会话或用户标识（应由 service 层注入）")

    context = ToolContext(db=db, user_id=user_id)
    registry = build_default_registry()
    notes: list[str] = []
    trace: list[dict[str, Any]] = []
    usage: dict[str, int] = {}
    weather: Any = None
    chunks: list[Any] = []

    model = gateway.get_chat_model()
    try:
        bound = model.bind_tools(registry.openai_schemas())
    except (AttributeError, NotImplementedError) as exc:
        # 自建模型或兼容网关可能不实现 bind_tools。降级到规则取数，
        # 而不是让整个问答 500——工具调用是增强，不是前提。
        logger.warning("模型不支持工具调用，降级为规则取数：%s", type(exc).__name__)
        notes.append(f"当前模型不支持工具调用（{type(exc).__name__}），已降级为规则取数")
        fallback = await _rule_fallback(state, context, notes)
        fallback["tool_trace"] = trace
        return fallback

    from langchain_core.messages import ToolMessage

    messages = _build_decision_messages(state)
    max_rounds = max(1, settings.agent_max_tool_iterations)

    for round_index in range(max_rounds):
        reply = await bound.ainvoke(messages)
        usage = gateway.merge_usage(usage, gateway.usage_of(reply))

        calls = list(getattr(reply, "tool_calls", None) or [])
        if not calls:
            notes.append(f"第 {round_index + 1} 轮：模型未请求工具，取数结束")
            break

        # 把模型的「我要调这些工具」这条消息放进对话，否则下一轮 ToolMessage
        # 会成为没有 parent 的孤儿消息，部分厂商会直接 400。
        messages.append(reply)

        for call_index, call in enumerate(calls):
            name = str(call.get("name") or "")
            raw_args = call.get("args") or {}
            arguments = raw_args if isinstance(raw_args, dict) else {}
            if name == WEATHER_TOOL:
                arguments = _normalize_weather_args(arguments, state.get("question") or "", state)

            result: ToolResult = await registry.execute(name, arguments, context)
            trace.append(
                {
                    "round": round_index + 1,
                    "tool": name,
                    "ok": result.ok,
                    "error": result.error,
                }
            )

            if result.ok and result.data is not None:
                if name == WEATHER_TOOL:
                    weather = result.data
                elif name == KNOWLEDGE_TOOL:
                    chunks = list(result.data)

            messages.append(
                ToolMessage(
                    content=result.content,
                    # tool_call_id 必须与模型给出的 id 对应，否则厂商侧无法把
                    # 结果与请求配对（LangChain 允许空串，但真实 API 会拒绝）。
                    tool_call_id=str(call.get("id") or f"call_{round_index}_{call_index}"),
                )
            )

            notes.append(
                f"工具 {name}：{'成功' if result.ok else '失败（' + str(result.error) + '）'}"
            )
    else:
        # for-else 的语义正是这里要的：只有「一轮没 break」才会执行，
        # 也就是用满了轮次上限。此时不能再无限调下去，否则模型会陷入
        # 「查不到就换个说法再查」的死循环，每次都在烧钱。
        notes.append(f"达到工具调用轮次上限（{max_rounds}），停止继续取数")
        logger.warning("工具调用达到上限 user_id=%s rounds=%s", user_id, max_rounds)

    # --- 兜底：模型漏取的数据，用规则补上 ---
    intent = state.get("intent") or ""
    need_weather = intent in WEATHER_INTENTS and weather is None
    need_chunks = intent in RETRIEVAL_INTENTS and not chunks
    if need_weather or need_chunks:
        fallback = await _rule_fallback(state, context, notes)
        if need_weather:
            weather = fallback.get("weather")
        if need_chunks:
            chunks = fallback.get("chunks") or []
        notes = fallback["notes"]

    return {
        "weather": weather,
        "chunks": chunks,
        "usage": usage,
        "tool_trace": trace,
        "notes": notes,
    }
