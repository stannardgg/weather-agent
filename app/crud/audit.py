"""【L2】审计日志的数据访问（M6-Q2）。

与其它 crud 模块一致：写操作在函数内部 commit。这里只有一个 `record()`，
因为审计日志是**只追加**的——没有修改、没有删除（否则「审计」就失去意义了）。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import client_ip_var, trace_id_var
from app.models.audit import AuditLog


async def record(
    db: AsyncSession,
    *,
    action: str,
    user_id: int | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    detail: dict | None = None,
) -> AuditLog:
    """写一条审计日志。ip 与 trace_id 从当前请求上下文自动取（无需调用方传）。"""
    log = AuditLog(
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        ip=client_ip_var.get(),
        trace_id=trace_id_var.get(),
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log


async def list_recent(
    db: AsyncSession,
    *,
    user_id: int | None = None,
    action: str | None = None,
    limit: int = 100,
) -> list[AuditLog]:
    """按时间倒序列出审计日志，可按 user_id / action 过滤。

    供测试断言与未来的「审计查询页」使用。
    """
    stmt = select(AuditLog)
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    stmt = stmt.order_by(AuditLog.id.desc()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
