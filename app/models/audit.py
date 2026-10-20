"""【L2】审计日志表（M6-Q2 方案 A）。

记录「谁在何时做了什么」，供事后追溯（误删文档、越权操作、暴力破解）。

## 为什么 user_id 不加外键、允许为空

审计日志是**只追加的历史快照**，不该随业务数据生命周期变化：

1. **用户删除后审计必须保留**——加 `ondelete="CASCADE"` 会让删用户连带删掉他的全部
   审计记录，恰恰违背「可追溯」这个目的；`SET NULL` 又依赖 SQLite 的
   `PRAGMA foreign_keys=ON`（测试环境没开）。所以这里干脆不加外键，只存 user_id 快照。
2. **登录失败、注册前**的操作没有可关联的 user_id，所以允许为空。

## 为什么 detail 用 JSON 列

与 `messages.tool_calls` 同一个理由：审计事件类型会随业务增长（今天记文件名，
明天可能要记「旧值→新值」），用 JSON 列存附加信息，加字段不用改表。
"""

from sqlalchemy import JSON, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 不做外键、允许为空：审计是「谁做了某事」的快照，用户删除后仍要保留（见模块说明）。
    # 查询索引由 __table_args__ 里的复合索引 (user_id, created_at) 承担，这里不再单独建
    # 单列索引——复合索引的最左前缀已覆盖「按 user_id 查」的场景，多建一个纯属冗余。
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 动作名（snake_case）：register / login / chat / upload_document / ...
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    # 目标类型与 id（可空）：如 (document, 12) 表示「对 12 号文档做了什么」
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 附加信息（文件名、结果等），JSON 列，加字段不改表
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 客户端 IP 与请求 trace_id，用于把审计与访问日志串起来
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} user={self.user_id} action={self.action!r}>"
