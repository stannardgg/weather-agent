"""【L5】指标端点（M6-Q3）。

`GET /metrics` 返回 Prometheus 文本格式。这是唯一一个**不返回统一 JSON 信封**的接口：
Prometheus 的抓取格式是纯文本 `name{labels} value`，套信封反而没法被识别。

端点不鉴权：它不泄露业务数据，只暴露进程级计数，且监控系统主动抓取时没有登录态可带。
内网场景下这是常规做法；若需要限制访问，可在反向代理层做 IP 白名单。
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.core import metrics as metrics_module
from app.core.config import settings
from app.core.exceptions import NotFoundError

router = APIRouter(prefix="/metrics", tags=["metrics"])

# Prometheus 文本格式的标准 Content-Type
_METRICS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("", summary="指标（Prometheus 文本格式）")
async def metrics() -> PlainTextResponse:
    if not settings.metrics_enabled:
        raise NotFoundError("指标端点未启用（配置项 METRICS_ENABLED=false）")
    return PlainTextResponse(metrics_module.render(), media_type=_METRICS_CONTENT_TYPE)
