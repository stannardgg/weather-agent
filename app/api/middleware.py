"""【L5】HTTP 中间件（M6 起：限流）。

与 `main.py` 里的 `trace_and_access_log` 分开，职责单一：只做「放不放行」。
它注册在 trace 中间件**之后**（即内层），所以 429 响应也会被 trace 记进访问日志，
且能拿到已绑定的 `trace_id`。

## 分组档位

| 分组 | 覆盖路径 | 目的 |
| --- | --- | --- |
| auth | `/api/v1/auth/*`（登录/注册） | 防暴力破解（最严） |
| chat | `/api/v1/chat` | 防烧 LLM 配额（中等） |
| default | 其余 `/api/v1/*` | 防无差别刷（最宽） |
| （不限流） | `/api/v1/health`、`/api/v1/metrics` | 探针与监控高频调用，限了会误伤 |
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import trace_id_var
from app.core.rate_limit import SlidingWindowLimiter
from app.schemas.common import ErrorCode

# 模块级单例：进程生命周期内共享同一份计数
_limiter = SlidingWindowLimiter()

# 健康检查与指标端点不限流（探针/监控高频轮询，限了会误伤）
_EXEMPT_PATHS = {"/api/v1/health", "/api/v1/health/ready", "/api/v1/metrics"}


def _group_of(path: str) -> tuple[str, int, float] | None:
    """返回 `(分组名, 上限, 窗口秒数)`；None 表示该路径不限流。"""
    if path in _EXEMPT_PATHS:
        return None
    if path.startswith("/api/v1/auth/"):
        return "auth", settings.rate_limit_auth_limit, settings.rate_limit_auth_window_seconds
    if path.startswith("/api/v1/chat"):
        return "chat", settings.rate_limit_chat_limit, settings.rate_limit_chat_window_seconds
    return "default", settings.rate_limit_default_limit, settings.rate_limit_default_window_seconds


def _client_ip(request: Request) -> str:
    """取客户端 IP。代理部署时优先信 X-Forwarded-For 的第一个（真正的客户端）。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def rate_limit_middleware(request: Request, call_next):
    """按「IP + 分组」做滑动窗口限流；超限返回 429 统一信封。"""
    if not settings.rate_limit_enabled:
        return await call_next(request)

    rule = _group_of(request.url.path)
    if rule is None:
        return await call_next(request)

    group, limit, window = rule
    key = f"{_client_ip(request)}:{group}"
    if _limiter.allow(key, limit, window):
        return await call_next(request)

    return JSONResponse(
        status_code=429,
        content={
            "code": ErrorCode.RATE_LIMITED,
            "message": f"请求过于频繁（{group} 档：{window} 秒内最多 {limit} 次），请稍后再试",
            "data": None,
            "trace_id": trace_id_var.get(),
        },
    )
