"""横切层：用户相关的请求 / 响应契约。

密码长度上限设为 72：bcrypt 只取前 72 字节，不限制的话超长密码会被静默截断。
邮箱不用 EmailStr，是为了避免为一个校验多引入 email-validator 依赖（§2.3）。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

USERNAME_PATTERN = r"^[A-Za-z0-9_]+$"
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class UserRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=USERNAME_PATTERN)
    email: str = Field(min_length=5, max_length=255, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=8, max_length=72)


class UserLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=72)


class UserPublic(BaseModel):
    """对外暴露的用户信息 —— 刻意不含 hashed_password。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
