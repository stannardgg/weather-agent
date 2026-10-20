"""【L2】ORM 表模型。

这里集中 import 所有 model：Alembic 的 --autogenerate 依赖 Base.metadata，
若某个模型没有被 import 过，它对应的表在迁移脚本里会「凭空消失」。
"""

from app.db.base import Base
from app.models.audit import AuditLog
from app.models.conversation import Conversation, Message
from app.models.document import Document, DocumentChunk
from app.models.user import User

__all__ = [
    "Base",
    "User",
    "Conversation",
    "Message",
    "Document",
    "DocumentChunk",
    "AuditLog",
]
