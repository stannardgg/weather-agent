"""【L2】用户数据访问 —— 数据库的唯一出口（CLAUDE.md §11）。

事务边界约定：写操作（create / update / delete）在函数内部自己 commit，
调用方（services 层）不需要感知事务细节，避免事务边界散落在多个文件里。
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def get_by_id(db: AsyncSession, user_id: int) -> User | None:
    return await db.get(User, user_id)


async def get_by_username(db: AsyncSession, username: str) -> User | None:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def create(
    db: AsyncSession, *, username: str, email: str, hashed_password: str
) -> User:
    user = User(username=username, email=email, hashed_password=hashed_password)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def count(db: AsyncSession) -> int:
    result = await db.execute(select(func.count()).select_from(User))
    return int(result.scalar_one())
