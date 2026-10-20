"""【L2】ORM 声明基类与公共字段。

Alembic 通过 Base.metadata 感知全部表结构，因此所有 model 都必须继承这里的 Base。
"""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有表模型的基类。"""


class TimestampMixin:
    """统一的时间戳字段。

    为什么用 server_default=func.now() 而不是 Python 的 datetime.now()：
    时间由数据库生成，避免应用服务器与数据库时区/时钟不一致导致的脏数据。
    SQLite 的 CURRENT_TIMESTAMP 返回 UTC，全库统一按 UTC 存储。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
