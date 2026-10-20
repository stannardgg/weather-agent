"""【L4】Agent 各节点的实现（M4 落地，Q1B 的分支节点都在这里）。

节点分工：

    classify_intent       判意图（规则优先 + LLM 兜底，见 router.py）
    fetch_weather         调天气工具（L3）
    retrieve_knowledge    向量检索（L3）
    compose               组装 prompt；顺带做「不该问模型就别问」的短路判断
    generate              调模型流式生成

外加三个**条件边函数**（LangGraph 里负责「下一步走哪个节点」的纯函数）：
`route_after_classify` / `route_after_weather` / `route_after_compose`。

## 两条设计要点

**1. 短路（short_circuit）：让「不编造」成为代码保证，而不是提示词请求。**
§8.3 要求检索为空时必须回答「知识库中未找到相关内容」。如果只是把这句话写进系统提示、
然后照常调用模型，那就是在「请求」模型不要说谎——它这次听了，下次不一定。
这里改成：需要知识库却一条都没检索到时，`compose` 直接把答案定好并短路到 END，
`generate` 根本不执行。模型没有机会编。

**2. 节点不碰数据库。**
检索节点只是把 service 放进来的会话**原样转交**给 L3 的检索器；
SQL 只存在于 `crud/`（§4 第 3 条、§11）。天气与检索失败都不抛异常而是记进 notes：
混合意图（C3）下天气挂了不该连带把知识库那半边的回答也弄没。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import extract, guardrail, router
from app.agent.state import (
    RETRIEVAL_INTENTS,
    WEATHER_INTENTS,
    AgentState,
    Intent,
)
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.llm import gateway, prompts
from app.rag import retriever
from app.tools.weather import WeatherError, get_weather

logger = get_logger(__name__)

# 节点名集中定义：条件边的 path_map 与 add_node 都引用它们，
# 写成字符串字面量的话，改名时漏掉一处就是「运行到那一步才报 KeyError」。
NODE_CLASSIFY = "classify_intent"
NODE_WEATHER = "fetch_weather"
NODE_RETRIEVE = "retrieve_knowledge"
NODE_COMPOSE = "compose"
NODE_GENERATE = "generate"
# C 阶段新增：模型自主调用工具取数（见 tool_loop.py）。它与上面三个取数节点是
# **并列**关系，由 AGENT_MODE 决定走哪边——两条路最终都汇合到 compose。
NODE_TOOL_LOOP = "tool_loop"

# 条件边 `route_after_compose` 的两个出口。名字起成「路由键」而不是节点名，
# 是因为「短路结束」在 LangGraph 里对应 END 常量而不是某个节点（见 graph.py 的映射表）。
ROUTE_SHORT_CIRCUIT = "short_circuit"
ROUTE_GENERATE = NODE_GENERATE

# --- 输入护栏（C 阶段）---
NODE_GUARD = "guard_input"
ROUTE_BLOCKED = "blocked"
ROUTE_CONTINUE = "continue"


class AgentStateError(AppError):
    """状态不完整（如检索节点拿不到数据库会话）。属于代码接线错误，不是用户输入的错。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, http_status=500)


def _with_notes(state: AgentState, *items: str) -> list[str]:
    """累积过程信息。

    刻意手工拼接而不用 LangGraph 的 `Annotated[list, operator.add]` 归约器：
    归约器要求该字段在入口 state 里就有初值，少一处初始化就会报运行时错，
    而 notes 是「可有可无的调试信息」——它不该有能力让整条链路起不来。
    """
    notes = list(state.get("notes") or [])
    notes.extend(items)
    return notes


# ---------------------------------------------------------------------------
# 节点
# ---------------------------------------------------------------------------


async def classify_intent(state: AgentState) -> dict[str, Any]:
    """节点①：判意图。"""
    question = state.get("question") or ""
    intent, source = await router.classify(question)
    return {"intent": intent.value, "intent_source": source.value}


async def guard_input(state: AgentState) -> dict[str, Any]:
    """节点⓪：输入护栏。命中劫持句式就短路，请求**不进模型**。

    放在最前面而不是塞进 classify_intent：拦截的语义是"这次请求根本不处理"，
    与"判成什么意图"是两件事。混在一个节点里，之后想单独统计拦截率或换成
    外部审核服务时，会发现这段逻辑已经和分类纠缠在一起。
    """
    question = state.get("question") or ""
    if not guardrail.is_blocked(question):
        return {"guardrail_blocked": False}

    # 拦截时才统计命中明细（正常请求不该为一条日志多扫一遍全部正则）
    hits, patterns = guardrail.score_injection(question)

    # 只记命中的规则编号/片段，**不把原文整段打进日志**：那段文本本身就是攻击载荷，
    # 原样落盘等于把攻击串写进日志系统（还可能被日志采集器二次转发）。
    logger.warning("输入护栏拦截 user_id=%s 命中=%s 规则=%s", state.get("user_id"), hits, patterns)
    return {
        "guardrail_blocked": True,
        "short_circuit": True,
        "answer": guardrail.GUARDRAIL_BLOCKED_ANSWER,
        "notes": _with_notes(
            state, f"输入护栏：命中 {hits} 条劫持句式，已短路（请求未进入模型）"
        ),
    }


async def fetch_weather(state: AgentState) -> dict[str, Any]:
    """节点②：查天气。

    城市与日期的来源优先级：**调用方显式传入 > 从问题里猜**
    （`extract.py` 的说明解释了为什么显式传入更可靠）。
    """
    question = state.get("question") or ""
    city = state.get("city") or extract.extract_city(question)
    date_str = state.get("date") or extract.extract_date(question)

    if not city:
        # 不抛异常：混合意图下还要继续走检索那半边，不该在这里中断整条链路
        return {
            "weather": None,
            "notes": _with_notes(state, "未能从问题中识别出城市，跳过天气查询"),
        }

    try:
        result = await get_weather(city, date_str)
    except WeatherError as exc:
        logger.warning("天气查询失败 city=%s date=%s：%s", city, date_str, exc.message)
        return {
            "weather": None,
            "notes": _with_notes(state, f"天气查询失败（{city}）：{exc.message}"),
        }
    return {
        "weather": result,
        "notes": _with_notes(state, f"天气已取到（{city}，日期={date_str or '不限'}）"),
    }


async def retrieve_knowledge(state: AgentState) -> dict[str, Any]:
    """节点③：向量检索。

    注意这一行：`db` 与 `user_id` 都是 service 放进 state 的，节点自己不构造会话、
    也不 import `crud`。检索器内部会带上 `where={"user_id": user_id}`（§8.2 硬约束）。
    """
    db: AsyncSession | None = state.get("db")
    user_id = state.get("user_id")
    if db is None or user_id is None:
        raise AgentStateError("检索节点缺少数据库会话或用户标识（应由 service 层注入）")

    chunks = await retriever.retrieve(db, state.get("question") or "", user_id=user_id)
    return {
        "chunks": chunks,
        "notes": _with_notes(state, f"知识库检索到 {len(chunks)} 条相关片段"),
    }


async def compose(state: AgentState) -> dict[str, Any]:
    """节点④：组装 prompt，并决定要不要短路。"""
    intent = state.get("intent") or Intent.CHAT.value
    question = state.get("question") or ""
    weather = state.get("weather")
    chunks = list(state.get("chunks") or [])

    # --- 短路一：需要知识库但一条都没检索到 → 固定文案，不经模型（§8.3）---
    if intent in RETRIEVAL_INTENTS and not chunks:
        return {
            "messages": [],
            "short_circuit": True,
            "answer": prompts.NO_KNOWLEDGE_ANSWER,
            "notes": _with_notes(state, "知识库无相关内容，已短路为固定文案（未经模型）"),
        }

    # --- 短路二：只问天气但没取到 → 固定文案。模型手里没数据，只能编 ---
    if intent == Intent.WEATHER.value and weather is None:
        return {
            "messages": [],
            "short_circuit": True,
            "answer": prompts.WEATHER_UNAVAILABLE_ANSWER,
            "notes": _with_notes(state, "天气取数失败，已短路为固定文案（未经模型）"),
        }

    weather_text = prompts.format_weather(weather) if weather is not None else ""
    if not weather_text and intent in WEATHER_INTENTS:
        # 混合意图下天气挂了但知识库有内容：仍然走模型回答，但**明确告诉它天气没取到**，
        # 否则它会顺着上下文把知识库里的天气描述当成实时天气来说。
        weather_text = "（本次未能取到天气数据。请直接说明没能查到天气，不要推测或引用其它来源的数字。）"

    chunks_text = prompts.format_chunks(chunks, max_chars=settings.chat_max_retrieved_chars)
    context_section = prompts.build_context_section(
        weather_text=weather_text, chunks_text=chunks_text
    )
    messages = prompts.build_messages(
        question=question,
        context_section=context_section,
        history=list(state.get("history") or []),
    )
    return {
        "messages": messages,
        "short_circuit": False,
        "answer": "",
        "notes": _with_notes(
            state,
            f"已组装 prompt（{len(messages)} 条消息，参考资料 {len(chunks_text)} 字符）",
        ),
    }


async def generate(state: AgentState) -> dict[str, Any]:
    """节点⑤：调模型。

    用 `astream` 而不是 `ainvoke`，是为了让 LangGraph 的 `stream_mode="messages"`
    能逐 token 捕获到它们，SSE 才能真正「一个字一个字地吐」。
    这里同时把增量拼成完整文本存回 state（`updates` 模式会给到），
    落库与 token 计量都用它——**不依赖客户端把流拼回去**。

    **异常刻意不吞**：模型调用失败（密钥没配、限流、超时）必须让上层知道。
    在这里翻译成一句「生成失败」然后正常返回，会让一次失败的问答看起来像成功，
    而且会被原样写进历史——这正是 §9.3.4「假成功」那类问题的翻版。
    """
    messages = list(state.get("messages") or [])
    if not messages:
        # 正常路径不会走到这里（短路已经在 compose 之后被分流掉了），
        # 保留判断是为了在接线改错时给出直白线索，而不是发一个空 prompt 给模型。
        raise AgentStateError("generate 节点收到了空的 messages（短路分流可能接错了）")

    parts: list[str] = []
    # 流式路径下 usage_metadata 挂在**最后一个 chunk** 上，而且是累计值而非增量，
    # 所以中途只记录、不累加；最后再与 state 里已有的用量（工具决策轮消耗的）
    # 合并。少了这一步，tool_loop 模式下的 token 计量会漏掉决策轮。
    stream_usage: dict[str, int] = {}
    async for chunk in gateway.astream_chunks(messages):
        text = gateway.text_of(chunk)
        if text:
            parts.append(text)
        got = gateway.usage_of(chunk)
        if got:
            stream_usage = got

    usage = gateway.merge_usage(dict(state.get("usage") or {}), stream_usage)
    answer = "".join(parts)

    # 输出护栏：空回答兜底 + 引用一致性留痕。放在这里而不是 service 层，
    # 是因为它是「生成」这一步的产物质量检查，与落库无关。
    answer, guard_notes = guardrail.check_answer(
        answer, has_sources=bool(state.get("weather") or state.get("chunks"))
    )
    return {
        "answer": answer,
        "usage": usage,
        "notes": _with_notes(
            state,
            f"模型输出 {len(answer)} 字，用量={usage or '上游未返回'}",
            *guard_notes,
        ),
    }


# ---------------------------------------------------------------------------
# 条件边
# ---------------------------------------------------------------------------


def route_after_guard(state: AgentState) -> str:
    """护栏命中就结束（答案已定，模型没机会看到这次提问），否则继续判意图。"""
    return ROUTE_BLOCKED if state.get("guardrail_blocked") else ROUTE_CONTINUE


def route_after_classify(state: AgentState) -> str:
    """按意图分流：天气 → 天气节点；知识库 → 检索节点；混合 → 先天气再检索；闲聊 → 直接组装。

    `AGENT_MODE=tool_loop` 时，三种「需要外部数据」的意图统一改走 tool_loop 节点，
    由模型自己决定调哪些工具；闲聊仍然直连 compose——为一句「你好」去调一次
    工具决策模型是纯浪费。
    """
    intent = state.get("intent") or Intent.CHAT.value

    if settings.agent_mode == "tool_loop" and intent != Intent.CHAT.value:
        return NODE_TOOL_LOOP

    if intent == Intent.WEATHER.value:
        return NODE_WEATHER
    if intent == Intent.KNOWLEDGE.value:
        return NODE_RETRIEVE
    if intent == Intent.MIXED.value:
        # 混合意图先查天气：天气失败也不影响后面继续检索（fetch_weather 不抛异常）
        return NODE_WEATHER
    return NODE_COMPOSE


def route_after_weather(state: AgentState) -> str:
    """查完天气后：混合意图还要继续检索，其余直接去组装。"""
    if state.get("intent") == Intent.MIXED.value:
        return NODE_RETRIEVE
    return NODE_COMPOSE


def route_after_compose(state: AgentState) -> str:
    """短路了就结束（答案已定，不给模型编造的机会），否则去生成。

    返回的是**路由键**而不是节点名，因为「结束」不是一个节点——
    它由 graph.py 映射到 LangGraph 的 END 常量，避免在本文件里 import 框架符号
    （§11 第 11 条的延迟导入约定）。
    """
    return ROUTE_SHORT_CIRCUIT if state.get("short_circuit") else ROUTE_GENERATE
