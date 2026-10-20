"""【M6-Q4】LLM 弹性：最终失败时的友好兜底测试。

验证「模型最终不可用」时，用户拿到的是友好文案（`prompts.LLM_UNAVAILABLE_ANSWER`），
而不是「请检查 LLM_API_KEY」这类运维细节，更不是厂商异常的类型名。

这里的「最终失败」指 SDK 的有限重试（`max_retries`）已经耗尽——那层重试在真实的
`ChatOpenAI` 里发生，测试里用 `FailingModel` 直接模拟「重试后仍然失败」这个终态。
与 test_chat.py 的 `ScriptedModel` 是同一套「测试替身」思路：把外部依赖做成可替换的
接缝，离线验证「结构对不对」，不碰真实网络。
"""

import pytest

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from app.core.exceptions import AppError
from app.crud import user as user_crud
from app.llm import gateway, prompts
from app.schemas.chat import ChatErrorEvent
from app.services import chat_service


class FailingModel(GenericFakeChatModel):
    """`_generate` 直接抛错——`ainvoke` 与 `astream` 都从它派生，会一起失败。

    选 `_generate` 这个接缝而不是分别覆写 `ainvoke`/`astream`，是刻意为之：这样
    「流式」与「非流式」两条路径都命中同一个失败点，保证测试断言的是**失败兜底逻辑**，
    而不是某一套调用封装的特异行为。
    """

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("模拟上游 LLM 不可用（重试耗尽后的最终失败）")


async def _make_user(db, username: str):
    """直接经 crud 造用户，绕开注册接口（这里只关心问答链路，不关心注册）。"""
    return await user_crud.create(
        db,
        username=username,
        email=f"{username}@test.com",
        hashed_password="x",
    )


async def test_answer_returns_friendly_fallback_when_llm_fails(session_factory):
    """非流式：LLM 最终失败 → 503 + 友好文案，且不泄露技术细节。"""
    gateway.set_chat_model_override(FailingModel(messages=iter([])))
    try:
        async with session_factory() as db:
            user = await _make_user(db, "llm_fail_nonstream")
            # 「你好」走规则判为 chat（不触达分类用的 LLM），直接进入 generate 节点
            prepared = await chat_service.prepare(db, user, question="你好")

            with pytest.raises(AppError) as exc_info:
                await chat_service.answer(db, prepared)

        assert exc_info.value.http_status == 503
        assert exc_info.value.message == prompts.LLM_UNAVAILABLE_ANSWER
        # 友好文案必须把运维细节挡住：既不能出现密钥字眼，也不能出现异常类名
        assert "API_KEY" not in exc_info.value.message
        assert "RuntimeError" not in exc_info.value.message
    finally:
        gateway.set_chat_model_override(None)


async def test_stream_yields_friendly_error_event_when_llm_fails(
    session_factory, monkeypatch
):
    """流式：LLM 最终失败 → 产出 error 事件，带同样的友好文案。"""
    # 流式路径自建会话（chat_service.AsyncSessionLocal），把它指到测试会话工厂
    monkeypatch.setattr(chat_service, "AsyncSessionLocal", session_factory)
    gateway.set_chat_model_override(FailingModel(messages=iter([])))
    try:
        async with session_factory() as db:
            user = await _make_user(db, "llm_fail_stream")
            prepared = await chat_service.prepare(db, user, question="你好")

            events = [event async for event in chat_service.stream_events(prepared)]

        error_events = [e for e in events if isinstance(e, ChatErrorEvent)]
        assert error_events, "流式失败时应当产出一个 error 事件"
        err = error_events[-1]
        assert err.message == prompts.LLM_UNAVAILABLE_ANSWER
        assert "API_KEY" not in err.message
        assert "RuntimeError" not in err.message
    finally:
        gateway.set_chat_model_override(None)
