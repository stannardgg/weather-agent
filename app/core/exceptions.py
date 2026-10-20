"""【L1】统一异常与错误处理（CLAUDE.md §7 错误码约定）。

为什么要在入口统一兜底：如果每个路由自己 try/except，返回体的形状就会各不相同，
前端得为每个接口写一套解析逻辑。集中注册 handler 后，任何异常都会被翻译成
{code, message, data, trace_id} 这同一个形状。
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, trace_id_var
from app.schemas.common import ErrorCode

logger = get_logger(__name__)


class AppError(Exception):
    """业务异常的基类。抛出它即表示「这是一个已知的、可预期的问题」。"""

    def __init__(
        self,
        message: str,
        code: int = ErrorCode.BIZ_ERROR,
        http_status: int = 400,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status


class NotFoundError(AppError):
    """资源不存在。"""

    def __init__(self, message: str = "资源不存在") -> None:
        super().__init__(message, code=ErrorCode.NOT_FOUND, http_status=404)


class AuthError(AppError):
    """鉴权失败（M1 引入 JWT 后启用）。"""

    def __init__(self, message: str = "身份校验失败") -> None:
        super().__init__(message, code=ErrorCode.AUTH_INVALID, http_status=401)


def _body(code: int, message: str, data: object = None) -> dict[str, object]:
    return {"code": code, "message": message, "data": data, "trace_id": trace_id_var.get()}


def register_exception_handlers(app: FastAPI) -> None:
    """把所有异常收敛到统一响应结构。"""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        # 业务异常属预期内，用 warning 级别，避免污染 error 日志
        logger.warning("业务异常: %s", exc.message, extra={"path": request.url.path})
        return JSONResponse(status_code=exc.http_status, content=_body(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(part) for part in first.get("loc", []) if part != "body")
        message = f"参数校验失败: {field or '未知字段'} {first.get('msg', '')}".strip()
        logger.warning("参数校验失败: %s", message, extra={"path": request.url.path})
        return JSONResponse(
            status_code=422, content=_body(ErrorCode.PARAM_ERROR, message)
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # 框架层异常（404 路由不存在、405 方法不允许、401 未带凭证等）也要落进统一错误码
        if exc.status_code == 404:
            code = ErrorCode.NOT_FOUND
        elif exc.status_code == 401:
            code = ErrorCode.AUTH_REQUIRED
        elif exc.status_code == 403:
            code = ErrorCode.FORBIDDEN
        elif exc.status_code < 500:
            code = ErrorCode.PARAM_ERROR
        else:
            code = ErrorCode.INTERNAL_ERROR
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(code, str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # 未预期异常必须打完整堆栈，否则线上无法定位
        logger.exception("未处理异常: %s", exc, extra={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            content=_body(ErrorCode.INTERNAL_ERROR, "服务内部错误，请稍后重试"),
        )
