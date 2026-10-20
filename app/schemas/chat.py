"""横切层：问答（chat）与会话的请求 / 响应契约（CLAUDE.md §7，M4 落地）。

## 为什么 SSE 的事件体也做成 Pydantic 模型

SSE（Server-Sent Events，服务端把回答一个字一个字推给前端的通道）本身只是
「`event:` 一行 + `data:` 一行」的纯文本格式，不强制任何结构。但**事件里的 JSON
是我们自己定的契约**，前端要按它解析。把它也写成模型有三个好处：
字段名改动会被类型检查发现、`/docs` 里能看到事件长什么样、序列化统一走一处
（`model_dump_json`），不会出现某个事件里字段名拼错前缀这类问题。

## 事件顺序（一条流里的固定形状）

    1. event: meta    —— 会话与意图信息，**先于任何文本**发出去
    2. event: delta   —— 文本增量，0..N 次
    3. event: done    —— 正常结束，带 message_id 与 token 用量
       或
       3'. event: error —— 中途失败，带错误码与可读原因

`meta` 之所以必须最先发：前端拿到 `conversation_id` 才能立刻把 URL 或侧边栏定下来
（新建会话时尤其重要），而要等全部答完才知道会话 id 的话，用户会看到几十秒的空白。
"""

from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.config import settings

# SSE 事件名（契约的一部分，前端按它们分支）
# SSE 事件名。声明成 Final[Literal[...]] 而不是裸 str 是为了让 mypy 能确认
# 「event 字段的默认值与它的 Literal 类型一致」——这两个值一旦写岔（比如把
# SSE_DELTA 拿来当 done 的默认值），前端的事件分发会静默失效，运行时不报错。
SSE_META: Final[Literal["meta"]] = "meta"
SSE_DELTA: Final[Literal["delta"]] = "delta"
SSE_DONE: Final[Literal["done"]] = "done"
SSE_ERROR: Final[Literal["error"]] = "error"

# 来源类型：weather = 实时天气接口；knowledge = 知识库检索片段
SourceType = Literal["weather", "knowledge"]


class ChatRequest(BaseModel):
    """`POST /api/v1/chat` 的请求体。"""

    question: str = Field(..., description="用户问题")
    # 不传 = 新建会话；传了必须属于当前用户，否则 404（不区分「不存在」与「别人的」）
    conversation_id: int | None = Field(default=None, description="继续哪个会话；不传则新建")
    # 与 /chat 共用一个路由（§7 约定）：true 走 SSE，false 返回完整 JSON
    stream: bool = Field(default=False, description="true 返回 SSE 流，false 返回完整 JSON")
    # 显式指定城市/日期时**优先于**从问题里猜（app/agent/extract.py 说明了两者差别）
    city: str | None = Field(default=None, max_length=60)
    date: str | None = Field(default=None, description="YYYY-MM-DD，不传则从问题里抽")

    @field_validator("question")
    @classmethod
    def _clean_question(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("问题不能为空")
        limit = settings.chat_max_question_chars
        if len(text) > limit:
            raise ValueError(f"问题过长（{len(text)} 字），上限为 {limit} 字")
        return text

    @field_validator("date")
    @classmethod
    def _check_date(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise ValueError("日期格式应为 YYYY-MM-DD，例如 2026-10-20") from exc


class ChatSource(BaseModel):
    """答案的依据来源，回传给前端做「依据」展示（§8.3 要求标注来源）。"""

    type: SourceType
    label: str  # 天气：城市名；知识库：`文件名#第N段`
    snippet: str | None = None  # 片段摘要（天气侧留空）
    score: float | None = None  # 余弦相似度，越大越相关（仅知识库侧有）
    document_id: int | None = None


class TokenUsage(BaseModel):
    """token 用量。字段可空：上游流式响应里**可能**不返回用量，那就存 NULL 而不是 0。"""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ChatAnswer(BaseModel):
    """非流式（`stream=false`）时 `data` 里的内容。"""

    conversation_id: int
    message_id: int
    answer: str
    intent: str
    intent_source: str
    sources: list[ChatSource] = Field(default_factory=list)
    short_circuit: bool = False
    notes: list[str] = Field(default_factory=list)
    usage: TokenUsage | None = None


class ChatMetaEvent(BaseModel):
    """SSE `meta` 事件：流一开始就把会话身份与意图告诉前端。"""

    event: Literal["meta"] = SSE_META
    conversation_id: int
    intent: str
    intent_source: str
    sources: list[ChatSource] = Field(default_factory=list)


class ChatDeltaEvent(BaseModel):
    """SSE `delta` 事件：一小段文本。"""

    event: Literal["delta"] = SSE_DELTA
    text: str


class ChatDoneEvent(BaseModel):
    """SSE `done` 事件：正常收尾。"""

    event: Literal["done"] = SSE_DONE
    conversation_id: int
    message_id: int
    answer: str
    short_circuit: bool = False
    notes: list[str] = Field(default_factory=list)
    usage: TokenUsage | None = None


class ChatErrorEvent(BaseModel):
    """SSE `error` 事件：中途失败。

    必须有它：流已经开了 HTTP 200，之后发生的错误**不可能**再用状态码表达，
    只能靠一个显式事件告诉前端「这轮没答完」。少了它，前端会把截断的回答当成完整回答。
    """

    event: Literal["error"] = SSE_ERROR
    code: int
    message: str


class MessageOut(BaseModel):
    """历史消息（`GET /conversations/{id}/messages`）。"""

    id: int
    role: str
    content: str
    tool_calls: dict[str, Any] | None = None
    tokens: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationOut(BaseModel):
    """会话摘要（`GET /conversations`）。

    刻意**不带消息内容**：列表页只需要标题与时间，带上最后一条消息就得为每个会话
    多查一次；真要看内容，前端点进去调 `/messages`。这让列表接口的响应体大小
    与会话长度无关——否则用了半年的账号拉一次列表可能返回几 MB。
    """

    id: int
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationMessagesOut(BaseModel):
    """`GET /conversations/{id}/messages` 的返回体。

    为什么要分页：一轮问答就是两条消息，聊上一百轮就有两百条。全量返回会让响应体
    随对话长度线性膨胀（并且每次翻旧会话都要重新付这份流量）。分页字段与 §7 的
    「列表接口统一分页」保持一致，只是把 `items` 换成了消息。
    """

    conversation: ConversationOut
    items: list[MessageOut] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50
