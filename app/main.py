"""FastAPI 入口（CLAUDE.md §5）。

本文件是**组合根**（composition root）：各层能力在这里装配成可运行的应用，
因此它是少数几个允许同时 import 多个层的文件。
鉴权（M1）、天气工具（M2）、知识库入库（M3）、Agent 编排（M4）逐层加入，
每加一层能力只需多挂一个 router。
"""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.api.deps import bind_trace_id
from app.api.middleware import rate_limit_middleware
from app.api.v1 import api_router
from app.core import metrics as metrics_module
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import client_ip_var, get_logger, setup_logging
from app.services import knowledge_service
from app.tools.weather import close_client
from app.worker.tasks import schedule_guarded

setup_logging(settings.log_level)
logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """应用生命周期钩子。

    启动时：**补偿上次没跑完的入库任务**。M3-Q3 选的是「进程内后台任务」，
    进程重启会让正在解析/向量化的文档无声卡住——这是该方案的已知代价，
    这里就是它的补丁：把它们重置为 pending 并重新投递。

    关闭时：释放天气接口的 HTTP 连接池 —— 不释放会在进程退出时留下未关闭的 socket，
    在反复重启的开发环境里表现为偶发的端口占用告警。
    """
    logger.info("应用启动")

    stale_ids = await knowledge_service.recover_stale_documents()
    for document_id in stale_ids:
        schedule_guarded(
            f"process_document:{document_id}",
            knowledge_service.process_document(document_id),
        )
    if stale_ids:
        logger.info("启动补偿已重新投递 %s 篇文档", len(stale_ids))

    yield

    await close_client()
    logger.info("应用关闭，外部连接池已释放")


app = FastAPI(
    title="瓜葛天气助手",
    description="天气查询 + 日常问答，你值得拥有的靠谱AI助手",
    version="0.5.0-M6",
    lifespan=lifespan,
)

register_exception_handlers(app)


@app.middleware("http")
async def trace_and_access_log(request: Request, call_next):
    """每个请求分配 trace_id，并把访问日志与请求关联起来。"""
    trace_id = bind_trace_id(request.headers.get("X-Trace-Id"))
    # 绑定客户端 IP 到上下文，供审计日志等「需要来源」的代码读取（见 core/logging.py）
    client_ip_var.set(request.client.host if request.client else "-")
    started = time.perf_counter()

    response = await call_next(request)

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Trace-Id"] = trace_id
    logger.info(
        "request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    # 指标埋点：请求计数与耗时。path 用**路由模板**（如 /conversations/{id}/messages）
    # 而不是原始路径——原始路径里的会话/文档 id 是高基数 label，会让指标序列无限膨胀。
    route = request.scope.get("route")
    metric_path = getattr(route, "path", request.url.path)
    metrics_module.inc_counter(
        "http_requests_total",
        "HTTP 请求总数",
        labels={
            "method": request.method,
            "path": metric_path,
            "status": str(response.status_code),
        },
    )
    metrics_module.observe_summary(
        "http_request_duration_seconds",
        "HTTP 请求耗时（秒）",
        value=duration_ms / 1000,
    )
    return response


# 限流中间件注册在 trace 之后（内层）：429 响应也会被记进访问日志，且能拿到 trace_id。
app.middleware("http")(rate_limit_middleware)


app.include_router(api_router, prefix="/api/v1")
