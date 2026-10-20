"""横切层：统一响应结构与错误码（CLAUDE.md §7）。

所有接口的返回体都必须是 {code, message, data, trace_id} 这一个形状，
前端只需写一份解析逻辑，不用为每个接口单独判断。
"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ErrorCode:
    """错误码约定：0 成功；1xxx 参数；2xxx 鉴权；3xxx 业务；5xxx 服务端。"""

    OK = 0
    PARAM_ERROR = 1000
    AUTH_REQUIRED = 2000
    AUTH_INVALID = 2001
    FORBIDDEN = 2003
    BIZ_ERROR = 3000
    NOT_FOUND = 3004
    RATE_LIMITED = 3009
    INTERNAL_ERROR = 5000


class Envelope(BaseModel, Generic[T]):
    """统一响应结构（CLAUDE.md §7）。

    它被声明在路由的 response_model 上，这样 /docs 里能看到真实的返回结构，
    而不是一个空对象。T 由各路由指定，例如 Envelope[UserPublic]。
    """

    code: int = ErrorCode.OK
    message: str = "ok"
    data: T | None = None
    trace_id: str = "-"


def envelope(data: Any = None, message: str = "ok", code: int = ErrorCode.OK) -> Envelope[Any]:
    """构造统一响应对象。trace_id 从当前请求上下文自动取，无需调用方传参。"""
    from app.core.logging import trace_id_var

    return Envelope(code=code, message=message, data=data, trace_id=trace_id_var.get())


def paginated(items: list[Any], total: int, page: int, page_size: int) -> dict[str, Any]:
    """列表接口的统一分页结构（CLAUDE.md §7）。"""
    return {"items": items, "total": total, "page": page, "page_size": page_size}
