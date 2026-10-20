"""【L2】知识库文档与切分片段表（M3 落地，CLAUDE.md §6 定稿）。

字段形状由 M3-Q1~Q4 的决策直接决定，逐条对应关系写在字段注释里。

## 与 §6 草稿的差异（定稿时补齐）

- `documents` 增加 `content_type` 与 `error_message`：前者用于前端图标与排错，
  后者承载 `status=failed` **为什么**失败（否则用户只看到「失败」两个字无从下手）。
- 去重唯一键定为 **(user_id, file_hash)** 而不是 file_hash 单列：M3-Q4 选了
  「每用户私有」，不同用户上传同一个文件应当各自拥有一份记录，否则 B 会看到 A 的文件。
- `documents.status` 增加索引：重复上传查重、启动补偿扫「卡住的任务」都要按它过滤。
- `document_chunks` 增加 `char_count` 与 `(document_id, chunk_index)` 唯一约束。
- `vector_id` 生成规则：`doc{document_id}-chunk{chunk_index}`——**确定性**的，
  不是 uuid。理由是重建索引（reindex）时同一片段得到同一个 id，
  向量库里的旧向量能被准确覆盖，不会留下一堆孤儿向量。
"""

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

# status 取值（前端据此显示进度，见 §6）
STATUS_PENDING = "pending"
STATUS_PARSING = "parsing"
STATUS_EMBEDDING = "embedding"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

# 「正在处理中」的状态集合：启动补偿要把它们重置回 pending（M3-Q3 选的进程内后台任务，
# 进程重启会让这些状态永久卡住）
IN_PROGRESS_STATUSES = (STATUS_PARSING, STATUS_EMBEDDING)


class Document(Base, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        # 同一用户下同一份文件只留一条记录（M3-Q4 每用户私有的连带结果）
        UniqueConstraint("user_id", "file_hash", name="uq_documents_user_file_hash"),
        Index("ix_documents_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # 文件内容的 sha256（十六进制）。用于去重：重复上传不重复消耗 embedding
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 保存到磁盘后的相对路径，删除文档时要按它清理文件
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=STATUS_PENDING, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        lazy="selectin",  # 异步下不可懒加载，见 db/session.py 的说明
        order_by="DocumentChunk.chunk_index",
    )


class DocumentChunk(Base, TimestampMixin):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunks_document_index"),
        Index("ix_chunks_vector_id", "vector_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 向量库里的对应 id。确定性生成：doc{document_id}-chunk{chunk_index}
    vector_id: Mapped[str] = mapped_column(String(64), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # 预留的附加信息（§6 原本就设计了 meta 列）。M3-Q2 选的是递归切分，
    # 没有标题层级信息可存；这里记下切分参数，便于判断「改过参数后是否该重建索引」。
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    document: Mapped["Document"] = relationship(back_populates="chunks")
