"""【L2】异步数据库引擎与会话。

本文件集中处理异步 SQLAlchemy 最容易踩的两个坑：

1. `expire_on_commit=False` —— 默认情况下 commit 后对象会失效，再次读属性会触发
   一次懒加载查询；而在异步上下文里懒加载不可用，直接抛 MissingGreenlet。
2. 关系字段一律 `lazy="selectin"`（见 models）—— 同理，异步下不能懒加载，
   必须在查询时就把关联数据一并取回。

另：SQLite 默认不校验外键，这里通过 PRAGMA 打开，否则 ondelete="CASCADE" 不生效。
"""

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.sql_echo,
    pool_pre_ping=True,
)


if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI 依赖：每个请求一个会话。

    这里不自动 commit —— 事务边界由 crud 层显式控制，避免「忘记提交」与
    「意外提交半个事务」两种问题混在一起难以定位。
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
