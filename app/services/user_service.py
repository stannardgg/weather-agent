"""【L3】用户业务逻辑：注册、登录、鉴权。

只承载业务规则（用户名是否重复、密码对不对、账号是否禁用），
既不写 SQL（下压给 crud），也不碰 HTTP（上交给路由）。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError, AuthError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.crud import user as user_crud
from app.models.user import User
from app.schemas.common import ErrorCode
from app.services import audit_service

logger = get_logger(__name__)


class UsernameTakenError(AppError):
    def __init__(self, username: str) -> None:
        super().__init__(
            f"用户名 {username} 已被占用", code=ErrorCode.BIZ_ERROR, http_status=409
        )


class EmailTakenError(AppError):
    def __init__(self, email: str) -> None:
        super().__init__(
            f"邮箱 {email} 已被注册", code=ErrorCode.BIZ_ERROR, http_status=409
        )


class CredentialsError(AppError):
    def __init__(self) -> None:
        # 刻意不区分「用户不存在」与「密码错误」，避免攻击者据此枚举账号
        super().__init__(
            "用户名或密码错误", code=ErrorCode.AUTH_INVALID, http_status=401
        )


class InactiveUserError(AppError):
    def __init__(self) -> None:
        super().__init__("账号已被禁用", code=ErrorCode.FORBIDDEN, http_status=403)


async def register(
    db: AsyncSession, *, username: str, email: str, password: str
) -> User:
    if await user_crud.get_by_username(db, username):
        raise UsernameTakenError(username)
    if await user_crud.get_by_email(db, email):
        raise EmailTakenError(email)

    user = await user_crud.create(
        db, username=username, email=email, hashed_password=hash_password(password)
    )
    logger.info("新用户注册成功: id=%s username=%s", user.id, user.username)
    await audit_service.record(
        db, action="register", user_id=user.id, target_type="user", target_id=user.id
    )
    return user


async def authenticate(db: AsyncSession, *, username: str, password: str) -> User:
    user = await user_crud.get_by_username(db, username)
    if user is None or not verify_password(password, user.hashed_password):
        logger.warning(
            "登录失败: username=%s 原因=%s",
            username,
            "用户不存在" if user is None else "密码错误",
        )
        raise CredentialsError()
    if not user.is_active:
        raise InactiveUserError()
    return user


def issue_access_token(user: User) -> tuple[str, int]:
    """签发 token，返回 (token, 有效期秒数)。"""
    token = create_access_token(subject=str(user.id))
    return token, settings.access_token_expire_minutes * 60


async def get_current_user(db: AsyncSession, token: str) -> User:
    """校验 token 并取出对应用户。用户被删除或禁用时立即失效。"""
    payload = decode_access_token(token)
    subject = payload.get("sub")
    if not subject or not str(subject).isdigit():
        raise AuthError("身份凭证内容不合法")

    user = await user_crud.get_by_id(db, int(subject))
    if user is None:
        raise AuthError("用户不存在或已被删除")
    if not user.is_active:
        raise InactiveUserError()
    return user
