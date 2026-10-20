"""【M6-Q1】限流器与限流中间件单测。

覆盖三个层次：
1. `SlidingWindowLimiter` 的滑动窗口逻辑（放行 / 拒绝 / 窗口滑动 / 重置 / key 隔离）；
2. `_group_of` 的档位划分（auth / chat / default / 豁免）；
3. 中间件端到端：超过上限返回 429 统一信封。

测试默认关闭限流（见 conftest 的 `_disable_rate_limit`），本文件里需要时显式开启。
"""

import time

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api import middleware as middleware_module
from app.core.config import settings
from app.core.rate_limit import SlidingWindowLimiter
from app.schemas.common import ErrorCode


# --- SlidingWindowLimiter ---------------------------------------------------


def test_allow_until_limit_then_reject():
    limiter = SlidingWindowLimiter()
    assert limiter.allow("k", 3, 60) is True
    assert limiter.allow("k", 3, 60) is True
    assert limiter.allow("k", 3, 60) is True
    assert limiter.allow("k", 3, 60) is False  # 第 4 次超限


def test_keys_are_independent():
    limiter = SlidingWindowLimiter()
    assert limiter.allow("a", 1, 60) is True
    assert limiter.allow("b", 1, 60) is True  # 不同 key 互不影响


def test_window_slides_over_time():
    limiter = SlidingWindowLimiter()
    assert limiter.allow("k", 1, 0.01) is True
    assert limiter.allow("k", 1, 0.01) is False  # 窗口内超限
    time.sleep(0.02)  # 等窗口滑过
    assert limiter.allow("k", 1, 0.01) is True  # 旧请求过期，重新放行


def test_reset_clears_all():
    limiter = SlidingWindowLimiter()
    limiter.allow("k", 1, 60)
    assert limiter.allow("k", 1, 60) is False
    limiter.reset()
    assert limiter.allow("k", 1, 60) is True


# --- 档位划分 ----------------------------------------------------------------


def test_group_of_maps_paths():
    assert middleware_module._group_of("/api/v1/auth/login")[0] == "auth"
    assert middleware_module._group_of("/api/v1/auth/register")[0] == "auth"
    assert middleware_module._group_of("/api/v1/chat")[0] == "chat"
    assert middleware_module._group_of("/api/v1/knowledge/documents")[0] == "default"
    assert middleware_module._group_of("/api/v1/conversations")[0] == "default"


def test_group_of_exempts_health_and_metrics():
    assert middleware_module._group_of("/api/v1/health") is None
    assert middleware_module._group_of("/api/v1/health/ready") is None
    assert middleware_module._group_of("/api/v1/metrics") is None


# --- 中间件端到端 -------------------------------------------------------------


async def test_rate_limit_returns_429_after_limit(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_auth_limit", 1)
    monkeypatch.setattr(settings, "rate_limit_auth_window_seconds", 60)
    middleware_module._limiter.reset()

    app = FastAPI()
    app.middleware("http")(middleware_module.rate_limit_middleware)

    @app.get("/api/v1/auth/login")
    async def login():
        return JSONResponse({"ok": True})

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/api/v1/auth/login")
        second = await client.get("/api/v1/auth/login")

    assert first.status_code == 200
    assert second.status_code == 429
    body = second.json()
    assert body["code"] == ErrorCode.RATE_LIMITED
    assert "过于频繁" in body["message"]


async def test_disabled_rate_limit_never_rejects(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(settings, "rate_limit_auth_limit", 1)
    middleware_module._limiter.reset()

    app = FastAPI()
    app.middleware("http")(middleware_module.rate_limit_middleware)

    @app.get("/api/v1/auth/login")
    async def login():
        return JSONResponse({"ok": True})

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/api/v1/auth/login")
        second = await client.get("/api/v1/auth/login")

    assert first.status_code == 200
    assert second.status_code == 200  # 关闭限流后不再拒绝
