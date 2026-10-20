"""【L5】鉴权路由（CLAUDE.md §7）。

本层只做三件事：参数校验（由 Pydantic 完成）、调用 service、包装统一响应。
不含任何业务规则与 SQL。
"""

from typing import Any

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DBSession
from app.schemas.common import Envelope, envelope
from app.schemas.user import (
    TokenResponse,
    UserLoginRequest,
    UserPublic,
    UserRegisterRequest,
)
from app.services import audit_service, user_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=Envelope[UserPublic],
    status_code=status.HTTP_201_CREATED,
    summary="注册",
)
async def register(
    payload: UserRegisterRequest, db: DBSession
) -> Envelope[Any]:
    user = await user_service.register(
        db, username=payload.username, email=payload.email, password=payload.password
    )
    return envelope(UserPublic.model_validate(user), message="注册成功")


@router.post("/login", response_model=Envelope[TokenResponse], summary="登录，返回 JWT")
async def login(payload: UserLoginRequest, db: DBSession) -> Envelope[Any]:
    user = await user_service.authenticate(
        db, username=payload.username, password=payload.password
    )
    token, expires_in = user_service.issue_access_token(user)
    await audit_service.record(
        db, action="login", user_id=user.id, target_type="user", target_id=user.id
    )
    return envelope(TokenResponse(access_token=token, expires_in=expires_in))


@router.get("/me", response_model=Envelope[UserPublic], summary="当前用户信息")
async def me(current_user: CurrentUser) -> Envelope[Any]:
    return envelope(UserPublic.model_validate(current_user))
