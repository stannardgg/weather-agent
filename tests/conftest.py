"""测试夹具：用内存 SQLite + 依赖覆盖，让测试不碰真实数据库文件。

关键点：内存库必须配 StaticPool。默认连接池会为每个连接新建一个内存数据库，
导致「建表的连接」和「查询的连接」看到的其实是两个空库。
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app

# 必须导入 models 包，否则 Base.metadata 里没有表，create_all 建不出任何东西。
# 注意这里用 `from app import models as _models` 而不是 `import app.models`：
# 后者会把本地名字 app 重新绑定成「包」对象，覆盖上面导入的 FastAPI 实例。
from app import models as _models  # noqa: F401


@pytest.fixture(autouse=True)
def _disable_rate_limit(monkeypatch):
    """测试默认关闭限流。

    理由：限流中间件的计数器是进程级单例，跨测试累积；而 ASGITransport 下所有测试
    共享同一个客户端 IP，auth 档「10 次/分钟」会被 test_auth.py 的几十个请求迅速打满，
    之后所有请求都拿 429（data=None），表现为一堆 'NoneType' is not subscriptable。
    限流本身在 tests/test_rate_limit.py 里单独测（那里显式开启并用小阈值）。
    """
    monkeypatch.setattr(settings, "rate_limit_enabled", False)


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def client(session_factory):
    async def _override_get_db():
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
