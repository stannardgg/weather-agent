"""【L3】审计日志的安全包装（M6-Q2）。

唯一职责：把「记录审计」包一层 try/except，让**审计写入失败绝不拖垮主流程**。

为什么要这么设计：审计是「尽力而为」的旁路记录。如果某次审计写入因为表结构变化、
数据库抖动等失败，绝不应该反过来把一个本该成功的登录/问答/删除变成 500。所以这里
统一吞掉异常并打一条 warning 日志——宁可丢一条审计，也不能让业务为审计殉葬。
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import audit as audit_crud

logger = logging.getLogger(__name__)


async def record(
    db: AsyncSession,
    *,
    action: str,
    user_id: int | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    detail: dict | None = None,
) -> None:
    """记录审计。失败只记 warning，不影响主流程。"""
    try:
        await audit_crud.record(
            db,
            action=action,
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
    except Exception:  # noqa: BLE001 —— 审计失败绝不让业务失败
        logger.warning(
            "审计日志写入失败 action=%s user_id=%s", action, user_id, exc_info=True
        )
