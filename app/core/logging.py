"""【L1】结构化日志 + 请求追踪 ID。

为什么用 ContextVar 而不是把 trace_id 一路当参数传：
FastAPI 的请求在同一个协程上下文里执行，ContextVar 写入一次即可被该请求的
所有日志读取到；若靠参数传递，则每层函数签名都要带上它，侵入性极大。
"""

import json
import logging
import sys
from contextvars import ContextVar

# 默认值为 "-"，保证非请求上下文（如脚本、启动阶段）也能取到值
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")

# 客户端 IP：由入口中间件绑定，供审计日志等需要「来源」的代码读取。
# 与 trace_id 一样，用 ContextVar 而不是当参数一路传，避免侵入各层函数签名。
client_ip_var: ContextVar[str] = ContextVar("client_ip", default="-")

_EXTRA_FIELDS = ("method", "path", "status_code", "duration_ms")


class JsonFormatter(logging.Formatter):
    """把日志输出成一行一个 JSON，便于后续被日志系统直接采集。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": trace_id_var.get(),
            "message": record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """初始化根 logger。重复调用时先清空 handler，避免日志打印多份。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn 自带的访问日志是纯文本，与 JSON 混在一起不好采集，交给中间件统一记录
    logging.getLogger("uvicorn.access").setLevel("WARNING")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
