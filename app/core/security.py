"""【L1】JWT 签发/校验 + 密码哈希。

为什么密码用 bcrypt 而不是 SHA256：SHA256 很快，而「快」正是暴力破解者需要的。
bcrypt 故意慢（成本因子可调），把暴力破解的时间成本拉高数个数量级。

注意 bcrypt 只取密码前 72 字节，所以 schemas 里把密码长度上限设为 72，
否则超长密码会被静默截断，出现「设置了 100 位密码但只有前 72 位有效」的隐患。
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import settings
from app.core.exceptions import AuthError

_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    """生成加盐哈希。同一个密码每次调用结果都不同（盐随机）。"""
    raw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(
            password.encode("utf-8")[:_BCRYPT_MAX_BYTES], hashed.encode("utf-8")
        )
    except ValueError:
        # 哈希串格式非法（例如历史脏数据）时按「校验失败」处理，而不是抛 500
        return False


def create_access_token(subject: str, expires_minutes: int | None = None) -> str:
    """签发 access token。subject 放用户 ID，不放入任何敏感信息。"""
    minutes = expires_minutes or settings.access_token_expire_minutes
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """校验并解码 token。

    `algorithms` 必须显式白名单：若省略，攻击者可把 header 里的 alg 改成 "none"
    来伪造未签名 token（历史上真实发生过的漏洞）。
    """
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("登录已过期，请重新登录") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("身份凭证无效") from exc
