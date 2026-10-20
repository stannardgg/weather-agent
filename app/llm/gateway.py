"""【L1】LLM 网关 —— D6「OpenAI 兼容统一网关」的落地实现（M4 落地，Q2A 选 langchain-openai）。

## 为什么要有这一层

上层（Agent 节点）只认 `messages` 进、文本/用量出，**不认任何具体厂商**。
换供应商（DeepSeek ↔ 通义 ↔ 本地 vLLM）只需要改 `.env` 三个值，代码一行不动——
这就是 D6 选「统一网关」而不是「绑定某家 SDK」的全部价值。

## 为什么用 `langchain-openai` 而不是直接用 `openai` SDK

Q2A 的决策。差别在于拿到的是 LangChain 的 `BaseChatModel` 接口：消息对象
（`SystemMessage`/`HumanMessage`）、流式增量、`usage_metadata` 都已标准化，
而且 LangGraph 能直接读它们的回调流（`stream_mode="messages"`）来逐 token 推送，
这一层是 SSE 流式能工作的前提。

## 测试替身

`set_chat_model_override()` 允许测试注入一个假模型（如 `GenericFakeChatModel`），
这样**整条链路（意图路由 → 检索 → 组装 → 流式生成）可以完全离线跑单元测试**，
不必联网也不消耗额度。这与 M3 的 `PlaceholderEmbedder` 是同一个思路：
把一个外部依赖做成可替换的接缝，让「结构对不对」能和「真实效果好不好」分开验证。

⚠️ 必须说清楚它验证不了什么：假模型**不产生真实语义**，所以它只能证明
「prompt 组装正确、路由走对、token 逐段流出、用量被记录」，不能证明
「模型答得准」。后者必须有真实密钥才能验证。
"""

from typing import TYPE_CHECKING, Any

from app.core import metrics as metrics_module
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.schemas.common import ErrorCode

if TYPE_CHECKING:  # 仅供类型检查；运行时按需延迟导入（§11 第 11 条）
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import BaseMessage

logger = get_logger(__name__)

# 官方文档（api-docs.deepseek.com，2026-10-12 核对）当前提供的模型名。
# 只用于在报错信息里给候选，不参与任何逻辑判断。
KNOWN_DEEPSEEK_MODELS = ("deepseek-flash", "deepseek-v4-pro")


class LLMGatewayError(AppError):
    """LLM 调用失败。默认按上游不可用处理（503：稍后重试可能就好）。"""

    def __init__(self, message: str, http_status: int = 503) -> None:
        super().__init__(message, code=ErrorCode.INTERNAL_ERROR, http_status=http_status)


class LLMNotConfiguredError(LLMGatewayError):
    """密钥/地址/模型名没配。这是**配置问题**，不是服务故障，所以要说得非常具体。"""

    def __init__(self) -> None:
        super().__init__(
            "尚未配置大模型凭据，问答功能不可用。请在 .env 里填好 LLM_API_KEY"
            "（获取入口 https://platform.deepseek.com/api_keys），"
            f"并确认 LLM_BASE_URL 与 LLM_MODEL 正确（当前模型名：{settings.llm_model or '未设置'}）。"
            "注意：这个密钥与和风天气的凭据无关，是两家不同的服务商。",
            http_status=503,
        )


_model: "BaseChatModel | None" = None
_override: "BaseChatModel | None" = None


def _build_chat_model() -> "BaseChatModel":
    """按配置构造真实模型。**只在第一次真正调用时构造**（惰性）。"""
    if not settings.llm_configured:
        raise LLMNotConfiguredError()
    # 延迟导入：langchain-openai 会拉进 openai SDK 与 tiktoken，放模块顶层拖慢启动
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        # 用 SecretStr 包一层：SDK 的类型契约要求如此，且它能在 repr/日志里
        # 把密钥显示成 ********** —— 直接传裸 str 一旦被打印到异常栈里就是泄露。
        api_key=SecretStr(settings.llm_api_key),
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        # 有限重试（M6-Q4）：显式写出来而不是用默认值，且走配置项——默认行为会变，
        # 写下来才能对照排查。SDK 只对网络异常与 5xx/429 重试（内部指数退避），
        # 4xx 不重试，与本项目 §11 第 2 条一致。
        max_retries=settings.llm_max_retries,
        # 流式响应默认不返回 token 用量，必须显式打开，否则用量计量在流式路径上会全是 None
        stream_usage=True,
    )


def get_chat_model() -> "BaseChatModel":
    """取模型单例。测试注入的替身优先。"""
    global _model
    if _override is not None:
        return _override
    if _model is None:
        _model = _build_chat_model()
    return _model


def set_chat_model_override(model: "BaseChatModel | None") -> None:
    """注入测试替身（传 None 撤销）。**仅供测试与自检脚本使用。**"""
    global _override
    _override = model


def reset_chat_model() -> None:
    """清空缓存的真实模型（测试改完配置后需要重新构造）。"""
    global _model
    _model = None


# ---------------------------------------------------------------------------
# 调用封装
# ---------------------------------------------------------------------------


def _record_llm_metrics(usage: dict[str, int]) -> None:
    """一次成功调用：调用次数 +1，并把 token 用量按类型累加到指标。"""
    metrics_module.inc_counter("llm_calls_total", "LLM 调用总数")
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            metrics_module.inc_counter(
                "llm_tokens_total",
                "LLM token 消耗",
                value=value,
                labels={"type": key},
            )


async def ainvoke_text(messages: list["BaseMessage"]) -> tuple[str, dict[str, int]]:
    """非流式调用，返回 `(文本, 用量)`。意图分类的 LLM 兜底用它。

    分类只需要一个词，流式没有意义，反而要多写一套增量拼接。
    """
    model = get_chat_model()
    try:
        reply = await model.ainvoke(messages)
    except Exception as exc:  # noqa: BLE001 —— 厂商异常类型不稳定，统一翻译
        metrics_module.inc_counter("llm_errors_total", "LLM 调用失败总数")
        raise _translate(exc) from exc
    usage = usage_of(reply)
    _record_llm_metrics(usage)
    return text_of(reply), usage


async def astream_chunks(messages: list["BaseMessage"]):
    """流式调用，逐段产出**原始 chunk**。

    为什么产出原始 chunk 而不是只产出文本：用量信息（`usage_metadata`）挂在
    最后一个 chunk 上，只 yield 文本的话调用方就拿不到 token 计数了。
    调用方用 `text_of()` / `usage_of()` 各取所需。

    为什么用「异步生成器」而不是「收一个回调」：LangGraph 的
    `stream_mode="messages"` 是靠 LangChain 的回调管理器捕获 token 的，
    而回调挂在**当前上下文**上。异步生成器在调用方的上下文里执行，
    所以逐段 yield 的同时 LangGraph 仍能看到每一段——这是 SSE 能真正逐字
    吐出的前提（已用假模型实测确认）。
    """
    model = get_chat_model()
    usage: dict[str, int] = {}
    try:
        async for chunk in model.astream(messages):
            chunk_usage = usage_of(chunk)
            if chunk_usage:
                usage = chunk_usage
            yield chunk
    except Exception as exc:  # noqa: BLE001
        metrics_module.inc_counter("llm_errors_total", "LLM 调用失败总数")
        raise _translate(exc) from exc
    else:
        # 流正常结束才记「成功调用」：中途失败只记 llm_errors_total，不算成功调用
        _record_llm_metrics(usage)


def text_of(message: Any) -> str:
    """从消息/chunk 里取出**正文文本**。

    三种情况都要处理：
    - 老式字符串内容（最常见）；
    - 新式「内容块」列表（多模态模型会返回 `[{"type": "text", "text": ...}]`）；
    - 空内容（工具调用、纯思考块）。

    刻意**只取 `content`，不取 `reasoning_content`**：DeepSeek 的思考模式会把推理过程
    放在单独的字段里，那是「模型的草稿」不是「给用户的答案」，混进 SSE 会让用户
    看到一堆没整理过的推理。想要它就单独取，不要在这里顺手带上。
    """
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in ("text", "text_delta"):
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return ""


def usage_of(message: Any) -> dict[str, int]:
    """取出 token 用量。取不到时返回空字典（而不是 0）。

    返回空字典而不是填 0 是有意的：0 会被当成「用量确实是零」写进数据库，
    而空字典让上层能区分「上游没给」与「真的是零」——前者应该存 NULL。
    """
    raw = getattr(message, "usage_metadata", None)
    if not isinstance(raw, dict):
        return {}
    picked = {
        "prompt_tokens": raw.get("input_tokens"),
        "completion_tokens": raw.get("output_tokens"),
        "total_tokens": raw.get("total_tokens"),
    }
    return {key: int(value) for key, value in picked.items() if isinstance(value, int)}


def _translate(exc: Exception) -> AppError:
    """把厂商异常翻译成带**处置建议**的自家异常。

    为什么要逐类翻译：只回一句「LLM 调用失败」，用户看到 502 完全不知道该改什么。
    这几类错误的处置动作完全不同——改密钥、改模型名、等配额、稍后重试。
    """
    if isinstance(exc, AppError):
        return exc

    name = type(exc).__name__
    status = getattr(exc, "status_code", None)

    if status == 401 or name == "AuthenticationError":
        return LLMGatewayError(
            "大模型拒绝了这次调用（401 鉴权失败）。请检查 .env 的 LLM_API_KEY："
            "是否复制完整、是否已过期或被删除。",
            http_status=503,
        )
    if status == 403 or name == "PermissionDeniedError":
        return LLMGatewayError(
            "大模型返回 403（无权访问）。请检查该 API Key 是否被限制了可用模型或来源。"
        )
    if status == 404 or name == "NotFoundError":
        return LLMGatewayError(
            f"大模型返回 404，通常是**模型名不存在**。当前 LLM_MODEL={settings.llm_model}，"
            f"DeepSeek 目前可用的模型名：{'、'.join(KNOWN_DEEPSEEK_MODELS)}。"
            "注意旧名 deepseek-chat / deepseek-reasoner 已下线。"
        )
    if status == 402 or name == "InsufficientQuotaError":
        return LLMGatewayError("大模型账户余额不足（402）。请到服务商控制台充值后重试。")
    if status == 429 or name == "RateLimitError":
        return LLMGatewayError("大模型触发限流（429），请降低频率或稍后重试。")
    if name in ("APITimeoutError", "TimeoutError") or status == 504:
        return LLMGatewayError(
            f"调用大模型超时（当前上限 {settings.llm_timeout_seconds} 秒）。"
            "可调大 LLM_TIMEOUT_SECONDS，或稍后重试。"
        )
    if name in ("APIConnectionError", "ConnectError") or status in (502, 503):
        return LLMGatewayError(
            f"无法连接大模型服务（{settings.llm_base_url}）。请检查网络与 LLM_BASE_URL 是否写错。"
        )
    if status == 400 or name == "BadRequestError":
        return LLMGatewayError(
            f"大模型拒绝了请求参数（400）：{exc}。"
            "常见原因：LLM_MODEL 写了不存在的模型名，或消息长度超出上下文上限。"
        )
    logger.exception("未翻译的 LLM 异常：%s", name)
    return LLMGatewayError(f"调用大模型失败：{name}: {exc}")
