"""【L3-L4】问答业务编排（M4 落地）。

这一层把「HTTP 请求」与「LangGraph 状态机」缝在一起，职责只有三件事：

1. **会话与历史的读写在请求内完成**（建会话、取历史、落用户消息、改标题）；
2. **跑图**，把历史与用户标识喂进去，把答案与依据取出来；
3. **按契约回吐**：非流式给一个 `ChatAnswer`，流式逐条 yield SSE 事件对象。

## 两个关键设计点

### 点一：流式路径必须自建数据库会话

SSE 的响应体是一个「边算边发」的生成器，它在**路由函数返回之后**才开始被消费。
此时请求级的 `get_db` 会话是否还开着，取决于框架版本与响应类型——这是一个不该赌的细节。
所以：

- 请求内能做完的事（找会话、取历史、落用户消息）**全部在 `prepare()` 里先做完**；
- 生成器内部另开一个会话（`AsyncSessionLocal`），供图执行与最终落库使用。

这与 `knowledge_service.process_document` 的做法一致：后台/脱离请求生命周期的数据库访问，
一律自建会话（§11 硬约定第 7 条）。

### 点二：用户消息先落库，助手消息成功后再落库

顺序反过来（等答完再一起写）会有两种坏情况：答到一半连接断了，用户那句话就凭空消失，
他再问一次会以为是自己的问题；而先写用户消息、答完再写助手消息，最坏情况只是留下一条
「有问无答」的记录——用户能直观看到上一条没答上，重问即可。
所以宁可留下这个中间态，也不要丢用户输入。

## 本层不做的事

不写 SQL（一律经 `crud/`）、不拼 prompt（在 `llm/prompts.py`）、不判意图（在 `agent/router.py`）。
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import graph as graph_module
from app.agent.nodes import NODE_COMPOSE
from app.agent.state import AgentState
from app.core.config import settings
from app.core.exceptions import AppError, NotFoundError
from app.crud import conversation as conversation_crud
from app.db.session import AsyncSessionLocal
from app.llm import gateway, prompts
from app.models.conversation import Conversation
from app.models.user import User
from app.rag.retriever import RetrievedChunk
from app.schemas.chat import (
    ChatAnswer,
    ChatDeltaEvent,
    ChatDoneEvent,
    ChatErrorEvent,
    ChatMetaEvent,
    ChatSource,
    TokenUsage,
)
from app.schemas.common import ErrorCode
from app.services import audit_service

logger = logging.getLogger(__name__)

# 知识库片段回传给前端的摘要长度。前端只用来做「依据」列表的预览，
# 全文已经有 `/knowledge/documents/{id}` 可以看，这里不必重复搬运。
SNIPPET_MAX_CHARS = 180


@dataclass
class PreparedChat:
    """请求内已经做完的部分：会话身份 + 图入口状态 + 落库用的自建会话工厂。

    刻意**不持有请求级的 `db`**：流式路径下那个会话可能在生成器执行时已经关闭，
    持有它会诱导出「在某些框架版本上偶发失败」的代码。
    """

    conversation_id: int
    question: str
    state: AgentState
    intent: str = ""
    intent_source: str = ""
    sources: list[ChatSource] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 请求内阶段
# ---------------------------------------------------------------------------


def to_langchain_messages(rows: list[Any]) -> list[Any]:
    """把数据库里的历史消息转成 LangChain 的消息对象。

    `tool` 角色的消息刻意丢弃：它们没有能直接映射到对话历史的形态（工具返回值
    已经体现在最终回答里），硬塞进去只会让模型看到一堆它没发起过的「工具输出」。
    """
    # 延迟导入（§11 第 11 条）：实测 langchain_core.messages 首次导入约 371ms
    from langchain_core.messages import AIMessage, HumanMessage

    converted: list[Any] = []
    for row in rows:
        if row.role == conversation_crud.ROLE_USER:
            converted.append(HumanMessage(content=row.content))
        elif row.role == conversation_crud.ROLE_ASSISTANT:
            converted.append(AIMessage(content=row.content))
    return converted


def derive_title(question: str) -> str:
    """用首条消息的前 20 字当会话标题（§7 约定）。"""
    text = " ".join((question or "").split())
    if len(text) <= conversation_crud.TITLE_MAX_CHARS:
        return text or conversation_crud.DEFAULT_TITLE
    return text[: conversation_crud.TITLE_MAX_CHARS] + "…"


async def prepare(
    db: AsyncSession,
    user: User,
    *,
    question: str,
    conversation_id: int | None = None,
    city: str | None = None,
    date: str | None = None,
) -> PreparedChat:
    """请求内阶段：确定会话、取历史、落用户消息、组装图的入口状态。

    会话归属的判断与 §7 的约定一致：**「不存在」与「属于别人」返回同一个 404**，
    不给出可区分的错误，避免变成枚举别人会话 ID 的探测接口。
    """
    # 显式标注成 Optional：两个分支返回的类型不同（新建的会话一定存在，
    # 查出来的可能是 None），不标注的话 mypy 会按第一个分支把它固定成 Conversation，
    # 于是第二分支的 None 判断被当成"永远不会成立"，真正的 404 分支就失去类型保护。
    conversation: Conversation | None
    if conversation_id is None:
        conversation = await conversation_crud.create_conversation(db, user.id)
    else:
        conversation = await conversation_crud.get_conversation(db, conversation_id, user.id)
        if conversation is None:
            raise NotFoundError(f"会话不存在：{conversation_id}")

    # 历史要在写本次提问**之前**取，否则刚写进去的这条会重复出现在历史里
    history_rows = await conversation_crud.list_messages(
        db, conversation.id, limit=settings.chat_history_max_messages
    )
    is_first_message = len(history_rows) == 0

    await conversation_crud.add_message(
        db, conversation.id, role=conversation_crud.ROLE_USER, content=question
    )
    if is_first_message:
        # 首条消息落库后才有意义改标题：此时「新会话」这个名字才第一次可以被替换
        await conversation_crud.update_title(db, conversation, derive_title(question))

    state: AgentState = {
        "question": question,
        "history": to_langchain_messages(history_rows),
        "user_id": user.id,
        # 这里先放请示的会话，流式路径会在生成器里换成自建会话（见模块说明点一）
        "db": db,
        "city": city,
        "date": date,
        "notes": [],
    }
    return PreparedChat(conversation_id=conversation.id, question=question, state=state)


# ---------------------------------------------------------------------------
# 依据整理
# ---------------------------------------------------------------------------


def sources_from(state: AgentState) -> list[ChatSource]:
    """把图跑出来的天气与检索片段整理成「依据」列表（§8.3 的来源标注）。"""
    sources: list[ChatSource] = []

    weather = state.get("weather")
    if weather is not None:
        label = weather.resolved.name
        if weather.target is not None:
            label = f"{label} {weather.target.date}"
        sources.append(
            ChatSource(
                type="weather",
                label=label,
                snippet=f"数据源 {weather.provider}"
                + ("，来自缓存" if weather.cached else "，本次实时获取"),
            )
        )

    for chunk in state.get("chunks") or []:
        sources.append(_source_of(chunk))
    return sources


def _source_of(chunk: RetrievedChunk) -> ChatSource:
    snippet = chunk.content.strip()
    if len(snippet) > SNIPPET_MAX_CHARS:
        snippet = snippet[:SNIPPET_MAX_CHARS] + "…"
    return ChatSource(
        type="knowledge",
        label=chunk.label,
        snippet=snippet,
        score=round(chunk.score, 4),
        document_id=chunk.document_id,
    )


def _provenance(state: AgentState, sources: list[ChatSource]) -> dict[str, Any]:
    """写进 `messages.tool_calls` 那一列的运行痕迹。

    为什么要存它：M5 的前端要能显示「这条回答依据了哪几个片段」「这轮是不是短路的」，
    而这些信息在图跑完就没了。`tool_calls` 是 JSON 列，存什么结构都不用改表（§6）。
    """
    return {
        "intent": state.get("intent"),
        "intent_source": state.get("intent_source"),
        "short_circuit": bool(state.get("short_circuit")),
        "notes": list(state.get("notes") or []),
        "sources": [source.model_dump(exclude_none=True) for source in sources],
    }


def _usage_of(state: AgentState) -> TokenUsage | None:
    usage = state.get("usage") or {}
    if not usage:
        # 上游流式响应可能不返回用量。**存 NULL 而不是 0**：0 会被当成「确实零消耗」，
        # 而事实是「不知道」——两者在计费对账时含义完全不同。
        return None
    return TokenUsage(
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
    )


async def _persist_answer(
    db: AsyncSession, conversation_id: int, state: AgentState
) -> tuple[int, list[ChatSource]]:
    """落库助手回答，返回 `(message_id, sources)`。"""
    sources = sources_from(state)
    message = await conversation_crud.add_message(
        db,
        conversation_id,
        role=conversation_crud.ROLE_ASSISTANT,
        content=state.get("answer") or "",
        tool_calls=_provenance(state, sources),
        tokens=(state.get("usage") or {}).get("total_tokens"),
    )
    return message.id, sources


# ---------------------------------------------------------------------------
# 非流式
# ---------------------------------------------------------------------------


async def answer(db: AsyncSession, prepared: PreparedChat) -> ChatAnswer:
    """跑完图并落库，返回完整答案（`stream=false` 走这条路）。

    非流式路径直接用请求级会话：整段逻辑都在请求生命周期内，没有点一那个问题。
    """
    state: AgentState = {**prepared.state, "db": db}
    try:
        result = await graph_module.get_graph().ainvoke(state)
    except gateway.LLMGatewayError as exc:
        # LLM 最终失败（SDK 的有限重试已耗尽，M6-Q4）：技术原因记日志，给用户友好兜底。
        # 不把「请检查 LLM_API_KEY」这类运维细节抛给终端用户——那不是他能处理的事。
        logger.warning(
            "LLM 调用失败 conversation_id=%s：%s", prepared.conversation_id, exc.message
        )
        raise AppError(
            prompts.LLM_UNAVAILABLE_ANSWER,
            code=ErrorCode.INTERNAL_ERROR,
            http_status=503,
        ) from exc
    message_id, sources = await _persist_answer(db, prepared.conversation_id, result)

    logger.info(
        "问答完成 conversation_id=%s 意图=%s(%s) 短路=%s 依据=%s 字数=%s",
        prepared.conversation_id,
        result.get("intent"),
        result.get("intent_source"),
        result.get("short_circuit"),
        len(sources),
        len(result.get("answer") or ""),
    )
    await audit_service.record(
        db,
        action="chat",
        user_id=prepared.state.get("user_id"),
        target_type="conversation",
        target_id=prepared.conversation_id,
        detail={"intent": result.get("intent"), "short_circuit": bool(result.get("short_circuit"))},
    )
    return ChatAnswer(
        conversation_id=prepared.conversation_id,
        message_id=message_id,
        answer=result.get("answer") or "",
        intent=result.get("intent") or "",
        intent_source=result.get("intent_source") or "",
        sources=sources,
        short_circuit=bool(result.get("short_circuit")),
        notes=list(result.get("notes") or []),
        usage=_usage_of(result),
    )


# ---------------------------------------------------------------------------
# 流式
# ---------------------------------------------------------------------------


async def stream_events(
    prepared: PreparedChat,
) -> AsyncIterator[ChatMetaEvent | ChatDeltaEvent | ChatDoneEvent | ChatErrorEvent]:
    """逐条产出 SSE 事件对象（框架层负责把它们帧化成 `event:`/`data:` 文本）。

    事件顺序：`meta` → `delta`* → `done`；中途失败则把最后一个换成 `error`。
    见 `schemas/chat.py` 的说明。

    为什么要先 `meta` 再 `delta`：`meta` 里有 `conversation_id` 与「依据」，
    前端拿到就能把侧边栏与依据列表渲染出来，不必等整段回答。所以它在
    `compose` 节点跑完（此时天气与检索结果都已确定）就发出去，
    而不是等 `done` 时一起给。
    """
    merged: AgentState = {**prepared.state}
    meta_sent = False
    delta_sent = False

    # 自建会话（见模块说明点一）。**整个生成器共用这一个**：
    # 图执行期间检索节点要读库，最终还要写助手消息，用同一个会话才能看到同一份数据。
    async with AsyncSessionLocal() as session:
        merged["db"] = session
        try:
            async for mode, payload in graph_module.get_graph().astream(
                {**merged}, stream_mode=["updates", "messages"]
            ):
                if mode == "updates":
                    for node, update in payload.items():
                        if isinstance(update, dict):
                            # cast 是必要的：LangGraph 的 updates 载荷声明成
                            # dict[str, Any]，但它的语义就是"某节点返回的状态增量"，
                            # 静态上无法证明每个 key 都属于 AgentState。
                            merged.update(cast(AgentState, update))
                        # compose 跑完 = 天气与检索都已定案，此刻发 meta 最有信息量
                        if node == NODE_COMPOSE and not meta_sent:
                            meta_sent = True
                            yield ChatMetaEvent(
                                conversation_id=prepared.conversation_id,
                                intent=merged.get("intent") or "",
                                intent_source=merged.get("intent_source") or "",
                                sources=sources_from(merged),
                            )
                    continue

                # mode == "messages"：只转发**生成节点**的 token。
                # 意图分类的 LLM 兜底调用也会产生 token 事件，不过滤的话会把
                # 「weather」这种分类结果混进用户看到的回答里。
                chunk, meta = payload
                if meta.get("langgraph_node") != "generate":
                    continue
                text = _text_of(chunk)
                if text:
                    delta_sent = True
                    yield ChatDeltaEvent(text=text)

            if not delta_sent and merged.get("answer"):
                # 短路路径（检索为空 / 天气取数失败）答案在 compose 里就定好了，
                # `generate` 根本没执行，所以一个 token 事件都不会有。
                # 这里把它当成唯一一个 delta 补发出去——否则「只认 delta 渲染」的前端
                # 会显示一片空白，而 done 事件里明明带着完整答案。
                yield ChatDeltaEvent(text=merged["answer"])

            if not meta_sent:
                # 兜底：正常流程不会走到这里（compose 一定会执行）。真走到了说明
                # 图的接线出了问题，宁可发一个字段不全的 meta 也不让前端一直等它。
                logger.warning("流式问答未经过 compose 节点，meta 事件在收尾时补发")
                yield ChatMetaEvent(
                    conversation_id=prepared.conversation_id,
                    intent=merged.get("intent") or "",
                    intent_source=merged.get("intent_source") or "",
                    sources=sources_from(merged),
                )

            message_id, sources = await _persist_answer(
                session, prepared.conversation_id, merged
            )
            await audit_service.record(
                session,
                action="chat",
                user_id=merged.get("user_id"),
                target_type="conversation",
                target_id=prepared.conversation_id,
                detail={"intent": merged.get("intent"), "short_circuit": bool(merged.get("short_circuit"))},
            )
            yield ChatDoneEvent(
                conversation_id=prepared.conversation_id,
                message_id=message_id,
                answer=merged.get("answer") or "",
                short_circuit=bool(merged.get("short_circuit")),
                notes=list(merged.get("notes") or []),
                usage=_usage_of(merged),
            )
        except gateway.LLMGatewayError as exc:
            # LLM 最终失败（M6-Q4）：技术原因记日志，给用户友好兜底。放在 AppError 之前，
            # 因为 LLMGatewayError 是它的子类——顺序反了会先进 AppError 分支，兜底失效。
            logger.warning(
                "流式问答 LLM 失败 conversation_id=%s：%s", prepared.conversation_id, exc.message
            )
            yield ChatErrorEvent(
                code=ErrorCode.INTERNAL_ERROR, message=prompts.LLM_UNAVAILABLE_ANSWER
            )
        except AppError as exc:
            # 已知错误：把可读原因发给前端。**HTTP 状态码此时已经发出去了（200），
            # 唯一能表达「这轮没答完」的方式就是这个 error 事件**——少了它，
            # 前端会把截断的回答当成完整回答存下来。
            logger.warning("流式问答失败 conversation_id=%s：%s", prepared.conversation_id, exc.message)
            yield ChatErrorEvent(code=exc.code, message=exc.message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("流式问答出现未预期异常 conversation_id=%s", prepared.conversation_id)
            yield ChatErrorEvent(
                code=ErrorCode.INTERNAL_ERROR, message=f"服务内部错误，请稍后重试（{type(exc).__name__}）"
            )


def _text_of(chunk: Any) -> str:
    """取 token 文本。转调 `llm/gateway.text_of`，集中一处处理「内容块」等新式结构。"""
    return gateway.text_of(chunk)
