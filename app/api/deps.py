"""【L5】依赖注入（CLAUDE.md §4：L5 只做参数校验、鉴权、调用下层与返回结果）。

M1 起加入数据库会话与当前用户两个依赖。

本文件刻意不含任何 SQL：会话在这里只是「把会话对象交出去」，具体查库一律
下压到 services → crud，以满足 §4 第 3 条「L4 / L5 不得直接连数据库」。
"""

import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthError
from app.core.logging import trace_id_var
from app.db.session import get_db
from app.models.user import User
from app.services import user_service


def new_trace_id() -> str:
    """生成 32 位十六进制追踪 ID。"""
    return uuid.uuid4().hex


def bind_trace_id(incoming: str | None = None) -> str:
    """把追踪 ID 绑定到当前请求上下文，并返回最终使用的值。"""
    trace_id = incoming or new_trace_id()
    trace_id_var.set(trace_id)
    return trace_id


DBSession = Annotated[AsyncSession, Depends(get_db)]


# auto_error=False：缺少请求头时不由 FastAPI 抛默认异常，改由我们抛统一错误结构
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    db: DBSession,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ] = None,
) -> User:
    """从 Authorization: Bearer <token> 解析出当前用户。"""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthError("缺少身份凭证（请求头应形如 Authorization: Bearer <token>）")
    return await user_service.get_current_user(db, credentials.credentials)


CurrentUser = Annotated[User, Depends(get_current_user)]
