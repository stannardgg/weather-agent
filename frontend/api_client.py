"""【L6】后端 API 的客户端封装（M5 落地）。

前端（Streamlit）只通过这个模块访问后端，不直接散落 `httpx` 调用。理由与后端的
`core/config.py`、`crud/` 一样：把「后端返回什么形状、SSE 怎么解析、错误怎么翻译」
集中在一处，前端页面只做展示与交互。

## 三个设计点

1. **同步 httpx**：Streamlit 的脚本是「每次交互重跑一遍」的模型，跑在普通线程里，
   不需要也不应该用异步。所以这里用 `httpx.Client`（同步），而不是后端的 `AsyncClient`。

2. **SSE 逐事件 yield**：`chat_stream` 返回一个生成器，逐个 yield `(事件名, data)`。
   前端拿到后自己决定怎么渲染——`meta` 拿会话与依据、`delta` 逐字拼、`done` 用完整
   `answer` 覆盖兜底、`error` 显示可读原因。把「解析 SSE」与「渲染」分开，
   渲染策略（delta + done 兜底，M5-Q2）才能独立演进。

3. **统一错误翻译**：后端的统一信封是 `{"code", "message", "data", "trace_id"}`，
   code != 0 即业务错误。这里把「非 2xx」「code != 0」「SSE 里的 error 事件」都翻译成
   `BackendError`，前端 catch 后直接 `st.error(...)`，不必每个调用点自己判断。
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import httpx

# 默认后端地址。可用环境变量 API_BASE_URL 覆盖（部署到不同机器时改这里即可）。
DEFAULT_BASE_URL = "http://127.0.0.1:8000/api/v1"


class BackendError(Exception):
    """后端调用失败（HTTP 非 2xx 或业务 code != 0）。

    携带 `code`（业务错误码）与 `status_code`（HTTP 状态码），前端可据此给更具体的提示。
    """

    def __init__(self, message: str, *, code: int = -1, status_code: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class BackendClient:
    """后端 API 客户端。`transport` 仅供测试注入 `httpx.MockTransport`。"""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, *, transport: Any = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=60.0, transport=transport)

    # -- 鉴权 ----------------------------------------------------------------

    def register(self, username: str, email: str, password: str) -> dict[str, Any]:
        """注册，返回后端回传的用户信息（`UserPublic`）。

        注意：后端注册**不直接返回 token**（返回的是用户信息），所以前端要
        注册成功后再调 `login` 拿 token——两个动作分开，语义更清楚。
        """
        return self._request("POST", "/auth/register", json={
            "username": username, "email": email, "password": password,
        })

    def login(self, username: str, password: str) -> str:
        data = self._request("POST", "/auth/login", json={
            "username": username, "password": password,
        })
        return str(data["access_token"])

    # -- 问答 ----------------------------------------------------------------

    def chat_stream(
        self,
        token: str,
        question: str,
        *,
        conversation_id: int | None = None,
        city: str | None = None,
        date: str | None = None,
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """流式提问，逐个 yield `(事件名, data)`。

        事件名 ∈ {meta, delta, done, error}。后端契约见 `schemas/chat.py`：
        `meta → delta* → done`，失败时最后一项换成 `error`。
        """
        payload: dict[str, Any] = {"question": question, "stream": True}
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        if city:
            payload["city"] = city
        if date:
            payload["date"] = date

        headers = {"Authorization": f"Bearer {token}"}
        try:
            with self._client.stream("POST", "/chat", json=payload, headers=headers) as response:
                self._raise_for_response(response)
                yield from self._iter_sse(response)
        except httpx.HTTPError as exc:
            raise BackendError(f"无法连接后端服务（{self.base_url}）：{exc}") from exc

    def chat(
        self,
        token: str,
        question: str,
        *,
        conversation_id: int | None = None,
        city: str | None = None,
        date: str | None = None,
    ) -> dict[str, Any]:
        """非流式提问，返回完整 `ChatAnswer`（对应后端 data 字段）。"""
        payload: dict[str, Any] = {"question": question, "stream": False}
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        if city:
            payload["city"] = city
        if date:
            payload["date"] = date
        return self._request("POST", "/chat", json=payload, token=token)

    # -- 会话 ----------------------------------------------------------------

    def list_conversations(self, token: str, *, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        return self._request("GET", "/conversations", token=token, params={
            "page": page, "page_size": page_size,
        })

    def search_conversations(
        self, token: str, query: str, *, page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        """按关键词搜会话（会话标题或聊天内容命中）。"""
        return self._request("GET", "/conversations/search", token=token, params={
            "q": query, "page": page, "page_size": page_size,
        })

    def list_messages(self, token: str, conversation_id: int) -> dict[str, Any]:
        return self._request("GET", f"/conversations/{conversation_id}/messages", token=token)

    def delete_conversation(self, token: str, conversation_id: int) -> None:
        self._request("DELETE", f"/conversations/{conversation_id}", token=token)

    # -- 知识库 --------------------------------------------------------------

    def upload_document(self, token: str, filename: str, content: bytes) -> dict[str, Any]:
        return self._request(
            "POST",
            "/knowledge/documents",
            token=token,
            files={"file": (filename, content)},
        )

    def update_document(self, token: str, document_id: int, filename: str, content: bytes) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/knowledge/documents/{document_id}",
            token=token,
            files={"file": (filename, content)},
        )

    def list_documents(self, token: str, *, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        return self._request("GET", "/knowledge/documents", token=token, params={
            "page": page, "page_size": page_size,
        })

    def get_document(self, token: str, document_id: int) -> dict[str, Any]:
        return self._request("GET", f"/knowledge/documents/{document_id}", token=token)

    def delete_document(self, token: str, document_id: int) -> None:
        self._request("DELETE", f"/knowledge/documents/{document_id}", token=token)

    def reindex(self, token: str) -> dict[str, Any]:
        return self._request("POST", "/knowledge/reindex", token=token)

    # -- 内部 ----------------------------------------------------------------

    def _headers(self, token: str | None) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(
                method, path, json=json, files=files, params=params,
                headers=self._headers(token),
            )
        except httpx.HTTPError as exc:
            raise BackendError(f"无法连接后端服务（{self.base_url}）：{exc}") from exc

        self._raise_for_response(response)
        body = response.json()
        code = body.get("code", 0)
        if code != 0:
            raise BackendError(
                str(body.get("message") or "后端返回错误"),
                code=int(code),
                status_code=response.status_code,
            )
        return body.get("data") or {}

    @staticmethod
    def _raise_for_response(response: httpx.Response) -> None:
        if response.is_success:
            return
        # 统一信封里可能带了更可读的 message（如 404「会话不存在」），优先取它
        message = f"请求失败（HTTP {response.status_code}）"
        try:
            payload = response.json()
            message = str(payload.get("message") or message)
        except (ValueError, AttributeError):
            pass
        raise BackendError(message, status_code=response.status_code)

    @staticmethod
    def _iter_sse(response: httpx.Response) -> Iterator[tuple[str, dict[str, Any]]]:
        """把 SSE 原文解析成 `(事件名, data)` 序列。

        后端帧格式是 `event: {name}\\ndata: {json}\\n\\n`（尾随空行是规范要求，
        缺了浏览器会把事件挂起）。这里**按规范解析**——与 `scripts/smoke_m4.py`
        的做法一致，不依赖第三方 SSE 库替我们容错。
        """
        event_name: str | None = None
        data_str: str | None = None
        for raw_line in response.iter_lines():
            line = raw_line.rstrip("\n")
            if line == "":
                # 空行 = 一个事件结束
                if event_name is not None and data_str is not None:
                    try:
                        yield event_name, json.loads(data_str)
                    except ValueError:
                        # data 不是合法 JSON：跳过并继续，别让一个坏帧毁掉整条流
                        pass
                event_name = None
                data_str = None
                continue
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_str = line[len("data:"):].strip()

    def close(self) -> None:
        self._client.close()


def default_client() -> BackendClient:
    """按环境变量构造默认客户端。Streamlit 每次重跑脚本都会调用它。"""
    import os

    return BackendClient(os.environ.get("API_BASE_URL", DEFAULT_BASE_URL))
