"""【L5】问答路由（CLAUDE.md §7）。

本层只做三件事：参数校验（Pydantic 完成）、调用 service、包装响应。
不含业务规则，也不碰数据库（§4 第 4 条）。

## 一个路由同时服务两种返回形态

§7 的约定是「流式与非流式共用同一路由：请求体加 `stream: bool`」。
`true` 返回 SSE（`text/event-stream`），`false` 返回统一 JSON 信封。

因此 `response_model` 只描述 JSON 那条分支——**`/docs` 里看到的是非流式形态**，
流式的事件结构写在 `schemas/chat.py` 的文档里（那边才是契约的权威描述）。
这是共用路由必然的取舍，写在代码里比让人自己去猜要好。
"""

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import CurrentUser, DBSession
from app.schemas.chat import ChatAnswer, ChatRequest
from app.schemas.common import Envelope, envelope
from app.services import chat_service

router = APIRouter(tags=["chat"])

SSE_HEADERS = {
    # 逐 token 推送的东西绝不能被缓存：一旦命中缓存，第二个用户会拿到第一个人的回答
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # 反向代理（Nginx 等）默认会攒够一块再转发，那样「流式」在浏览器里会变成
    # 「等半天然后一次性出现」。本机直连看不出来，一放到代理后面就暴露。
    "X-Accel-Buffering": "no",
}


def sse_frame(event: BaseModel) -> str:
    """把事件对象帧化成 SSE 文本。

    SSE 的格式要求每条消息以**空行**结束（即 `\\n\\n`）；少这一个空行，浏览器会把
    后续事件一直挂起不交付——表现是「服务端明明推了，前端一个都收不到」。
    """
    name = getattr(event, "event", "message")
    return f"event: {name}\ndata: {event.model_dump_json()}\n\n"


async def _sse_stream(prepared: chat_service.PreparedChat) -> AsyncIterator[str]:
    async for event in chat_service.stream_events(prepared):
        yield sse_frame(event)


@router.post(
    "/chat",
    response_model=Envelope[ChatAnswer],
    summary="提问（stream=true 走 SSE 流式）",
)
async def chat(
    payload: ChatRequest, db: DBSession, current_user: CurrentUser
) -> Any:
    """提问。`stream=false` 返回完整 JSON；`stream=true` 返回 SSE 事件流。

    会话归属在 service 层校验，「不存在」与「属于别人」统一返回 404（§7）。
    """
    prepared = await chat_service.prepare(
        db,
        current_user,
        question=payload.question,
        conversation_id=payload.conversation_id,
        city=payload.city,
        date=payload.date,
    )

    if not payload.stream:
        return envelope(await chat_service.answer(db, prepared))

    # 注意：这里返回的生成器要等到响应体被消费时才执行，所以
    # 请求级的 db 会话**不能**带进去——service 会在生成器内部自建会话
    # （理由见 services/chat_service.py 的模块说明）。
    return StreamingResponse(
        _sse_stream(prepared),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
