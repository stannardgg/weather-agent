"""【L2】健康探测的数据访问。

单独放一个文件而不是塞进 user.py：健康检查是基础设施职责，不属于任何业务表。
放在 crud 层是为了满足 CLAUDE.md §11 —— L5 接入层不得直接写 SQL。
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def ping(db: AsyncSession) -> bool:
    """最轻量的连通性探测：能执行成功就说明数据库可用。"""
    try:
        await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
