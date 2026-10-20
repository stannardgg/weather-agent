"""【L2】知识库数据访问 —— 数据库的唯一出口（CLAUDE.md §11）。

**所有查询都带 `user_id` 条件**（M3-Q4 决策：每用户私有）。这不是可有可无的过滤，
而是本项目最重要的一条越权防线——把隔离做在数据访问层，比散落在各处的
「查完再判断一下归属」可靠得多：忘了判断还能跑，忘了传 user_id 则函数签名直接报错。

事务边界约定同 `crud/user.py`：写操作在函数内部自己 commit。
"""

# 注意：SQLAlchemy 的 delete 必须起别名。本模块同时导出了自定义的 `delete(db, document)`
# 协程（文档物理删除），若不别名，模块级 `delete` 名字会被下面那个函数覆盖，
# 导致 `delete_chunks` 里调用到的其实是「删除整篇文档」的协程，签名不匹配直接 TypeError。
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    IN_PROGRESS_STATUSES,
    Document,
    DocumentChunk,
)


async def get_by_id(
    db: AsyncSession, document_id: int, user_id: int
) -> Document | None:
    """按 id 取文档，且必须属于该用户。

    注意这里用 select 而不是 `db.get(Document, id)`：后者拿不到 user_id 过滤，
    调用方一不小心就会返回别人的文档。
    """
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_by_id_any_user(db: AsyncSession, document_id: int) -> Document | None:
    """按 id 取文档，**不过滤用户**。

    只给后台任务用（它没有「当前用户」的上下文，只能凭 document_id 继续处理）。
    禁止在路由层调用——那会绕过用户隔离。
    """
    result = await db.execute(select(Document).where(Document.id == document_id))
    return result.scalar_one_or_none()


async def get_by_hash(db: AsyncSession, user_id: int, file_hash: str) -> Document | None:
    """查重：同一用户是否已传过内容完全相同的文件（§8.1 的 file_hash 查重）。"""
    result = await db.execute(
        select(Document).where(
            Document.user_id == user_id, Document.file_hash == file_hash
        )
    )
    return result.scalar_one_or_none()


async def create(
    db: AsyncSession,
    *,
    user_id: int,
    filename: str,
    file_hash: str,
    stored_path: str,
    content_type: str | None,
    size: int,
) -> Document:
    document = Document(
        user_id=user_id,
        filename=filename,
        file_hash=file_hash,
        stored_path=stored_path,
        content_type=content_type,
        size=size,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    return document


async def list_by_user(
    db: AsyncSession, user_id: int, *, page: int, page_size: int
) -> list[Document]:
    result = await db.execute(
        select(Document)
        .where(Document.user_id == user_id)
        .order_by(Document.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.scalars().all())


async def count_by_user(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(
        select(func.count()).select_from(Document).where(Document.user_id == user_id)
    )
    return int(result.scalar_one())


async def list_all(db: AsyncSession) -> list[Document]:
    """列出全部文档（**仅给 reindex 使用**：重建索引是全库行为，不属于某个用户）。"""
    result = await db.execute(select(Document).order_by(Document.id))
    return list(result.scalars().all())


async def list_in_progress(db: AsyncSession) -> list[Document]:
    """列出卡在解析/向量化中的文档（启动补偿用，见 M3-Q3 的连带后果）。"""
    result = await db.execute(
        select(Document).where(Document.status.in_(IN_PROGRESS_STATUSES))
    )
    return list(result.scalars().all())


async def update_status(
    db: AsyncSession,
    document: Document,
    status: str,
    *,
    chunk_count: int | None = None,
    error_message: str | None = None,
) -> Document:
    document.status = status
    if chunk_count is not None:
        document.chunk_count = chunk_count
    # error_message 显式传入时才覆盖：成功路径要能把它清空（传 None 表示「清掉」），
    # 因此不能写成「为 None 就跳过」，否则失败过的文档修好后仍带着旧错误信息。
    document.error_message = error_message
    await db.commit()
    await db.refresh(document)
    return document


async def delete(db: AsyncSession, document: Document) -> None:
    """物理删除（§7 约定：documents 不引入软删除字段）。

    chunk 记录由 `Document.chunks` 上的 cascade 一并删除；ForeignKey 上的
    ondelete="CASCADE" 是数据库层的第二道保险。
    """
    await db.delete(document)
    await db.commit()


async def add_chunks(
    db: AsyncSession,
    *,
    document_id: int,
    texts: list[str],
    meta: dict | None = None,
) -> list[DocumentChunk]:
    """批量写入切分片段，返回带 id 的记录列表（vector_id 依赖 document_id，此处生成）。"""
    chunks = [
        DocumentChunk(
            document_id=document_id,
            chunk_index=index,
            content=text,
            vector_id=f"doc{document_id}-chunk{index}",
            char_count=len(text),
            meta=meta,
        )
        for index, text in enumerate(texts)
    ]
    db.add_all(chunks)
    await db.commit()
    return chunks


async def delete_chunks(db: AsyncSession, document_id: int) -> None:
    """删除某文档的全部片段（覆盖重解析 / 重建索引前先清旧数据）。"""
    await db.execute(
        sa_delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
    )
    await db.commit()


async def list_chunks(db: AsyncSession, document_id: int, limit: int | None = None) -> list[DocumentChunk]:
    statement = (
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
    )
    if limit is not None:
        statement = statement.limit(limit)
    result = await db.execute(statement)
    return list(result.scalars().all())


async def count_chunks(db: AsyncSession, document_id: int) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
    )
    return int(result.scalar_one())


async def list_chunks_by_vector_ids(
    db: AsyncSession, vector_ids: list[str], user_id: int
) -> list[DocumentChunk]:
    """按 `vector_id` 批量取回片段**正文**（M4 检索链路用），并连表校验归属。

    为什么必须连 `documents` 表再过滤一次 `user_id`：向量检索那一步已经带过
    `where={"user_id": ...}` 了，但那是**向量库**里的过滤。正文在关系库这边，
    两边的过滤条件应该是独立成立的——只信一边，等于把用户隔离压在一个过滤条件上。
    这类「双保险」在有越权风险的路径上值得多写一行 JOIN。

    过滤掉不存在的 vector_id 是正常情况：文档可能在上传后又被覆盖重解析，
    旧向量已被删除（`delete_document`）但同一批 id 仍可能被上一次检索持有。
    """
    if not vector_ids:
        return []
    result = await db.execute(
        select(DocumentChunk)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            DocumentChunk.vector_id.in_(vector_ids),
            Document.user_id == user_id,
        )
    )
    return list(result.scalars().all())
