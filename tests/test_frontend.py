"""【L6】前端 API 客户端单测（M5 落地）。

用 `httpx.MockTransport` 模拟后端，**不碰真实网络**。覆盖：

1. 登录拿 token、注册只返回用户信息（不含 token）；
2. 非流式 `chat` 与 SSE 流式 `chat_stream` 的事件解析；
3. 统一错误翻译（HTTP 非 2xx → `BackendError`、业务 code != 0 → `BackendError`、
   连接失败 → `BackendError`）；
4. 会话三件套、知识库六件套的请求路径 / 方法 / 参数正确性。

前端模块用的是扁平 import（`from api_client import ...`、`from views import ...`），
所以这里先把 `frontend/` 目录塞进 `sys.path` 再导入。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
if str(_FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(_FRONTEND_DIR))

from api_client import BackendClient, BackendError  # noqa: E402


def _env(data):
    """后端统一信封 `{code, message, data, trace_id}`。"""
    return {"code": 0, "message": "ok", "data": data, "trace_id": "t"}


@pytest.fixture
def make_client():
    created: list[BackendClient] = []

    def _make(handler) -> BackendClient:
        transport = httpx.MockTransport(handler)
        client = BackendClient("http://test/api/v1", transport=transport)
        created.append(client)
        return client

    yield _make
    for client in created:
        client.close()


def _json(payload, status=200) -> httpx.Response:
    return httpx.Response(status, json=payload)


# -- 鉴权 ----------------------------------------------------------------


def test_login_returns_token(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/auth/login"
        assert json.loads(request.content) == {"username": "alice", "password": "secret"}
        return _json(_env({"access_token": "tok123", "token_type": "bearer", "expires_in": 3600}))

    client = make_client(handler)
    assert client.login("alice", "secret") == "tok123"


def test_register_returns_user_without_token(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/auth/register"
        body = json.loads(request.content)
        assert body["email"] == "a@b.com"
        return _json(_env({
            "id": 1, "username": "alice", "email": "a@b.com",
            "is_active": True, "created_at": "2026-10-12T00:00:00",
        }))

    client = make_client(handler)
    data = client.register("alice", "a@b.com", "secret123")
    assert data["username"] == "alice"
    # 后端注册不直接发 token，前端要再 login 一次（M5 关键修正点）
    assert "access_token" not in data


# -- 问答 ----------------------------------------------------------------


def test_chat_non_stream_sends_context(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/chat"
        body = json.loads(request.content)
        assert body["stream"] is False
        assert body["conversation_id"] == 5
        assert body["city"] == "上海"
        assert body["date"] == "2026-10-20"
        return _json(_env({
            "conversation_id": 5, "message_id": 6, "answer": "晴",
            "intent": "weather", "intent_source": "rule", "sources": [],
        }))

    client = make_client(handler)
    data = client.chat("tok", "上海天气", conversation_id=5, city="上海", date="2026-10-20")
    assert data["answer"] == "晴"


def test_chat_stream_parses_event_sequence(make_client):
    sse = (
        'event: meta\n'
        'data: {"event": "meta", "conversation_id": 7, "intent": "knowledge", '
        '"intent_source": "rule", "sources": []}\n\n'
        'event: delta\n'
        'data: {"event": "delta", "text": "你好"}\n\n'
        'event: delta\n'
        'data: {"event": "delta", "text": "，世界"}\n\n'
        'event: done\n'
        'data: {"event": "done", "conversation_id": 7, "message_id": 9, "answer": "你好，世界"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/chat"
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode("utf-8"))

    client = make_client(handler)
    events = list(client.chat_stream("tok", "你好"))

    assert [name for name, _ in events] == ["meta", "delta", "delta", "done"]
    assert events[0][1]["conversation_id"] == 7
    assert events[1][1]["text"] == "你好"
    assert events[2][1]["text"] == "，世界"
    assert events[3][1]["answer"] == "你好，世界"


def test_chat_stream_error_event(make_client):
    sse = 'event: error\ndata: {"event": "error", "code": 5000, "message": "上游超时"}\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode("utf-8"))

    client = make_client(handler)
    events = list(client.chat_stream("tok", "问题"))
    assert events == [("error", {"event": "error", "code": 5000, "message": "上游超时"})]


def test_chat_stream_skips_malformed_data_frame(make_client):
    # 一个坏帧（data 不是合法 JSON）不该毁掉整条流，后面的好帧仍要解析出来
    sse = (
        'event: delta\n'
        'data: {not-json}\n\n'
        'event: delta\n'
        'data: {"event": "delta", "text": "ok"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode("utf-8"))

    client = make_client(handler)
    events = list(client.chat_stream("tok", "问题"))
    assert [name for name, _ in events] == ["delta"]
    assert events[0][1]["text"] == "ok"


# -- 统一错误翻译 ----------------------------------------------------------


def test_http_error_extracts_backend_message(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": 3004, "message": "会话不存在", "data": None, "trace_id": "-"})

    client = make_client(handler)
    with pytest.raises(BackendError) as exc:
        client.delete_conversation("tok", 1)
    assert "会话不存在" in exc.value.message
    assert exc.value.status_code == 404


def test_business_code_nonzero_raises(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return _json({"code": 2000, "message": "登录已过期", "data": None})

    client = make_client(handler)
    with pytest.raises(BackendError) as exc:
        client.login("alice", "bad")
    assert exc.value.code == 2000
    assert "登录已过期" in exc.value.message


def test_connection_error_raises(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = make_client(handler)
    with pytest.raises(BackendError) as exc:
        client.login("a", "b")
    assert "无法连接" in exc.value.message


# -- 会话三件套 ------------------------------------------------------------


def test_list_conversations_sends_pagination(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/conversations"
        assert request.url.params["page"] == "2"
        assert request.url.params["page_size"] == "20"
        return _json(_env({"items": [], "total": 0, "page": 2, "page_size": 20}))

    client = make_client(handler)
    client.list_conversations("tok", page=2, page_size=20)


def test_list_messages(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/conversations/3/messages"
        return _json(_env({"items": [{"role": "user", "content": "hi"}], "total": 1}))

    client = make_client(handler)
    data = client.list_messages("tok", 3)
    assert data["items"][0]["content"] == "hi"


def test_delete_conversation(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/api/v1/conversations/9"
        return _json(_env(None))

    client = make_client(handler)
    client.delete_conversation("tok", 9)  # 不抛即成功


# -- 知识库六件套 ----------------------------------------------------------


def test_upload_document_sends_file_with_auth(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/knowledge/documents"
        assert request.headers["authorization"] == "Bearer tok"
        assert b"hello" in request.content  # multipart 里应包含文件内容
        return _json(_env({"document": {"id": 1, "status": "pending"}, "deduplicated": False}))

    client = make_client(handler)
    result = client.upload_document("tok", "a.txt", b"hello")
    assert result["document"]["id"] == 1
    assert result["deduplicated"] is False


def test_update_document_uses_put(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/api/v1/knowledge/documents/4"
        return _json(_env({"document": {"id": 4, "status": "pending"}, "deduplicated": False}))

    client = make_client(handler)
    client.update_document("tok", 4, "b.txt", b"x")


def test_get_and_delete_document(make_client):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        if request.method == "GET":
            return _json(_env({"id": 2, "filename": "a.txt", "status": "ready", "chunk_count": 3}))
        return _json(_env(None))

    client = make_client(handler)
    detail = client.get_document("tok", 2)
    assert detail["status"] == "ready"
    assert seen["method"] == "GET"

    client.delete_document("tok", 2)
    assert seen["method"] == "DELETE"


def test_reindex(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/knowledge/reindex"
        return _json(_env({"total": 3, "dispatched": 3, "message": "ok"}))

    client = make_client(handler)
    assert client.reindex("tok")["total"] == 3


# --------------------------------------------------------------------------- 对话页渲染
#
# 下面这组不测网络，而是测**对话页的渲染时序**：提交问题后、「思考中」占位、
# 首个 delta 与 done 覆盖之间的先后关系。用假的 st 与假的 client 盯住这个顺序，
# 是因为真实渲染要靠浏览器看，而这段时序恰恰是「用户以为卡死」的高发区。


def _fake_streamlit(calls: list):
    """构造一个最小可用的 st 替身，只实现 `_run_stream` 会用到的那几处。"""

    class FakePlaceholder:
        def markdown(self, text, **_kwargs):
            calls.append(("markdown", text))

        def error(self, text, **_kwargs):
            calls.append(("error", text))

    class FakeStreamlit:
        # 用真的 dict：被 `_run_stream` 以 get/setdefault 使用
        session_state: dict = {}

        @staticmethod
        def empty():
            return FakePlaceholder()

    return FakeStreamlit


def test_run_stream_shows_thinking_hint_before_first_delta(monkeypatch):
    from views import chat as chat_view

    calls: list[tuple[str, str]] = []
    fake_st = _fake_streamlit(calls)
    monkeypatch.setattr(chat_view, "st", fake_st)

    class FakeClient:
        @staticmethod
        def chat_stream(*_args, **_kwargs):
            yield "meta", {"conversation_id": 1}
            yield "delta", {"text": "你"}
            yield "delta", {"text": "好"}
            yield "done", {"answer": "你好呀", "conversation_id": 1}

    chat_view._run_stream(FakeClient(), "tok", "你好", city=None, date=None)

    # 第一笔渲染必须是「思考中」——这是提交后到首字之间唯一的反馈
    assert calls[0][0] == "markdown"
    assert "思考中" in calls[0][1]
    # 之后才轮到流式内容，并以 done 的完整 answer 收尾
    assert any("你" in text for _, text in calls[1:])
    assert calls[-1] == ("markdown", "你好呀")
    assert fake_st.session_state["conversation_id"] == 1
    assert fake_st.session_state["messages"] == [{"role": "assistant", "content": "你好呀"}]


def test_run_stream_error_replaces_thinking_hint(monkeypatch):
    """中途失败时必须用错误提示顶掉「思考中」，不能让它留在屏幕上。"""
    from views import chat as chat_view

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(chat_view, "st", _fake_streamlit(calls))

    class FakeClient:
        @staticmethod
        def chat_stream(*_args, **_kwargs):
            yield "meta", {"conversation_id": 1}
            yield "error", {"message": "上游超时"}

    chat_view._run_stream(FakeClient(), "tok", "你好", city=None, date=None)

    assert calls[0][0] == "markdown" and "思考中" in calls[0][1]
    assert calls[-1][0] == "error" and "上游超时" in calls[-1][1]
    # 出错时不该往历史里塞助手消息
    assert not chat_view.st.session_state.get("messages")


def test_run_stream_clears_thinking_hint_when_nothing_arrives(monkeypatch):
    """一条 delta 都没有、也没报错时，必须显式清空占位，否则看起来就是卡死。"""
    from views import chat as chat_view

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(chat_view, "st", _fake_streamlit(calls))

    class FakeClient:
        @staticmethod
        def chat_stream(*_args, **_kwargs):
            yield "meta", {"conversation_id": 3}
            # 没有 delta、没有 done —— 正常契约下不该发生，但要保证不留假状态
            return

    chat_view._run_stream(FakeClient(), "tok", "你好", city=None, date=None)

    assert calls == [("markdown", chat_view._THINKING_HTML), ("markdown", "")]


def test_thinking_hint_classes_are_defined_in_theme():
    """「思考中」占位用的 class 必须在主题 CSS 里真的有定义。

    这是一类「不报错、只失效」的问题：类名在一边改了、另一边没改，页面照样能跑，
    只是动效和配色静默消失。用断言把它钉住。
    """
    from theme import CSS
    from views import chat as chat_view

    assert ".pb-thinking {" in CSS
    assert ".pb-thinking-dot {" in CSS
    assert 'class="pb-thinking"' in chat_view._THINKING_HTML
    assert 'class="pb-thinking-dot"' in chat_view._THINKING_HTML


def test_run_stream_never_appends_block_cursor(monkeypatch):
    """流式渲染不得在文本末尾拼「▌」这类方块光标。

    用户反馈「每条回答末尾都拖一个黑色方块」。这里把整轮渲染的每一次写入都过一遍，
    确保没有任何一次带上方块字符（包括中间态）——防的是「以后有人又加回来」。
    """
    from views import chat as chat_view

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(chat_view, "st", _fake_streamlit(calls))

    class FakeClient:
        @staticmethod
        def chat_stream(*_args, **_kwargs):
            yield "meta", {"conversation_id": 1}
            yield "delta", {"text": "气温 "}
            yield "delta", {"text": "32.71"}
            yield "delta", {"text": "°C"}
            yield "done", {"answer": "气温 32.71°C", "conversation_id": 1}

    chat_view._run_stream(FakeClient(), "tok", "天气", city=None, date=None)

    for kind, text in calls:
        assert "▌" not in text, f"渲染里出现了方块光标：{kind} -> {text!r}"
    # 中间的 delta 帧必须是「纯已流出文本」，不带任何后缀
    assert ("markdown", "气温 ") in calls
    assert ("markdown", "气温 32.71") in calls
    assert calls[-1] == ("markdown", "气温 32.71°C")

