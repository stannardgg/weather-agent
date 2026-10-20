"""【L3】知识库业务逻辑：上传 → 解析 → 切分 → 向量化 → 入库（CLAUDE.md §8.1）。

本文件是 M3 的核心，四个决策（M3-Q1~Q4）都落在这里：
- **Q4A 每用户私有** → 所有对外操作的入口都带 `user`，数据访问层强制按 user_id 过滤。
- **Q3A 进程内后台任务** → `upload_document` 只负责建记录并投递，真正的重活在
  `process_document` 里跑；它**自建数据库会话**（后台任务没有请求级会话可复用）。
- **Q2A 递归字符切分** → 交给 `rag/splitter.py`。
- **Q1C 占位向量** → 交给 `rag/embedder.py`，此处不关心用的是真模型还是替身。

## 幂等与失败语义

- `process_document` 可被安全重复投递：开始时先清掉旧的片段与向量，状态为 ready 的直接跳过。
- 任何失败都落到 `documents.status=failed` + `error_message`，**不留半成品状态**：
  否则用户看到的就是「一直在解析中」，无从下手也无人知道出了什么事。
"""

import hashlib
import logging
import re
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError, NotFoundError
from app.crud import document as document_crud
from app.db.session import AsyncSessionLocal
from app.models.document import (
    STATUS_EMBEDDING,
    STATUS_FAILED,
    STATUS_PARSING,
    STATUS_PENDING,
    STATUS_READY,
    Document,
)
from app.models.user import User
from app.rag import loader as loader_module
from app.rag.embedder import get_embedder
from app.rag.loader import DocumentParseError, UnsupportedFileTypeError, extract_text
from app.rag.splitter import split_text
from app.rag.vectorstore import get_store
from app.schemas.common import ErrorCode
from app.schemas.knowledge import ChunkOut, DocumentDetail, DocumentOut
from app.services import audit_service

logger = logging.getLogger(__name__)

# 文件落盘名的最大长度（含扩展名）。数据库列是 String(255)，
# 而 Windows 的完整路径还有 260 字符上限，所以对文件名本身收紧到 80。
MAX_STORED_NAME = 80


class DocumentTooLargeError(AppError):
    def __init__(self, size: int) -> None:
        limit = settings.max_upload_size_mb
        super().__init__(
            f"文件过大：{size / 1024 / 1024:.1f} MB，上限 {limit} MB（配置项 MAX_UPLOAD_SIZE_MB）",
            code=ErrorCode.PARAM_ERROR,
            http_status=413,
        )


def sanitize_filename(filename: str) -> str:
    """把用户提供的文件名洗成安全的落盘名。

    必须挡住的两种攻击 / 事故：
    1. **路径穿越**：`../../windows/system32/x.dll` 这类文件名会让保存操作写到项目外。
       先按正反斜杠切成最后一段，再滤掉剩余的危险字符。
    2. **Windows 保留字符与结尾点**：`a<b>.txt`、`abc.` 在 Windows 上会创建失败或行为诡异。
    """
    # 不能用 Path(...).name：在 POSIX 上反斜杠不是分隔符，`..\..\x` 会被原样保留。
    # 直接按两种分隔符切最后一段，才与平台无关。
    name = re.split(r"[\\/]", filename or "")[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip().strip(".")
    if not name:
        name = "unnamed"
    # 截断时保留扩展名，否则后续按后缀判断类型的逻辑会失效
    if len(name) > MAX_STORED_NAME:
        stem, dot, suffix = name.rpartition(".")
        keep = MAX_STORED_NAME - (len(suffix) + 1 if dot else 0)
        name = f"{stem[:keep]}.{suffix}" if dot else name[:MAX_STORED_NAME]
    return name


async def read_upload(upload) -> bytes:
    """带上限地读完上传内容。

    为什么不用 `await upload.read()` 一次读完：那样会先把整个文件放进内存，
    再检查大小——上传一个 2GB 文件就能把服务打爆。这里只多读 1 字节，
    刚好够判断「超限」，内存占用被限制在 MAX_UPLOAD_SIZE_MB 附近。
    """
    limit = settings.max_upload_size_mb * 1024 * 1024
    content = await upload.read(limit + 1)
    if len(content) > limit:
        raise DocumentTooLargeError(len(content))
    return content


def _storage_path(user_id: int, file_hash: str, safe_name: str) -> Path:
    """落盘路径：`{UPLOAD_DIR}/{user_id}/{hash前16位}_{安全文件名}`。

    带 hash 前缀的原因是**确定性且不会撞名**：同一内容重复上传得到同一路径，
    覆盖写入天然幂等；不同内容即使同名也各自独立。
    """
    return Path(settings.upload_dir) / str(user_id) / f"{file_hash[:16]}_{safe_name}"


async def list_documents(
    db: AsyncSession, user: User, *, page: int, page_size: int
) -> tuple[list[DocumentOut], int]:
    documents = await document_crud.list_by_user(db, user.id, page=page, page_size=page_size)
    total = await document_crud.count_by_user(db, user.id)
    return [DocumentOut.model_validate(document) for document in documents], total


async def get_document_detail(
    db: AsyncSession, user: User, document_id: int
) -> DocumentDetail:
    document = await document_crud.get_by_id(db, document_id, user.id)
    if document is None:
        # 注意：这里对「不存在」与「属于别人」返回同一个错误。
        # 若区分开，攻击者就能靠错误信息差异探测出哪些 id 真实存在（用户枚举）。
        raise NotFoundError(f"文档 {document_id} 不存在")

    chunks = await document_crud.list_chunks(
        db, document_id, limit=settings.document_preview_chunks
    )
    total = await document_crud.count_chunks(db, document_id)
    detail = DocumentDetail.model_validate(document)
    detail.chunks = [ChunkOut.model_validate(chunk) for chunk in chunks]
    detail.total_chunks = total
    return detail


async def upload_document(
    db: AsyncSession,
    user: User,
    *,
    filename: str,
    content: bytes,
    content_type: str | None,
    dispatch,
) -> tuple[DocumentOut, bool]:
    """保存文件并投递入库任务。返回 (文档, 是否命中查重)。

    `dispatch` 由调用方注入（路由层传的是 FastAPI BackgroundTasks 的包装），
    这样本函数不依赖 Web 框架，测试里可以直接传一个空函数同步验证。
    """
    safe_name = sanitize_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in loader_module.SUPPORTED_SUFFIXES:
        raise UnsupportedFileTypeError(suffix)
    if not content:
        raise DocumentParseError(f"{safe_name}：文件内容为空")

    file_hash = hashlib.sha256(content).hexdigest()

    # 查重（§8.1）：同一用户重复上传同一份文件，直接返回已有记录，
    # 不重复解析、不重复消耗 embedding 额度。
    existing = await document_crud.get_by_hash(db, user.id, file_hash)
    if existing is not None:
        logger.info("命中查重，直接返回已有文档 id=%s", existing.id)
        return DocumentOut.model_validate(existing), True

    path = _storage_path(user.id, file_hash, safe_name)
    # 落盘也是阻塞 IO，但只在请求里写一个最大 20MB 的文件，代价可控；
    # 真正耗时的是后续解析，那部分已经交给后台任务。
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

    document = await document_crud.create(
        db,
        user_id=user.id,
        filename=safe_name,
        file_hash=file_hash,
        stored_path=str(path),
        content_type=content_type,
        size=len(content),
    )
    logger.info("文档已接收 id=%s name=%s size=%s bytes", document.id, safe_name, len(content))
    dispatch(document.id)
    await audit_service.record(
        db,
        action="upload_document",
        user_id=user.id,
        target_type="document",
        target_id=document.id,
        detail={"filename": safe_name, "size": len(content)},
    )
    return DocumentOut.model_validate(document), False


async def process_document(document_id: int) -> None:
    """完整入库流程。运行在**请求之外**，因此自建数据库会话。

    重复投递是安全的（幂等）：开头会清掉该文档已有的片段与向量，再重新生成。
    这让「失败后重试」「重建索引」与「首次入库」共用同一条代码路径。
    """
    async with AsyncSessionLocal() as db:
        document = await document_crud.get_by_id_any_user(db, document_id)
        if document is None:
            logger.warning("后台任务找不到文档 id=%s（可能已被删除），跳过", document_id)
            return
        if document.status == STATUS_READY:
            logger.info("文档 id=%s 已是 ready，跳过重复处理", document_id)
            return

        try:
            await document_crud.update_status(
                db, document, STATUS_PARSING, error_message=None
            )
            text = await extract_text(Path(document.stored_path), document.filename)

            chunks = split_text(text)
            if not chunks:
                # 解析出了文本却切不出任何块，说明切分参数有问题（如 CHUNK_SIZE 为 0）。
                # 这种情况必须报错，不能静默变成「0 块的 ready 文档」。
                raise DocumentParseError(
                    f"{document.filename}：解析出 {len(text)} 个字符，但切分后没有任何片段，"
                    "请检查 CHUNK_SIZE / CHUNK_OVERLAP 配置"
                )
            # 切分完成，进入向量化阶段（§6 的 status 取值：pending/parsing/embedding/ready/failed）
            await document_crud.update_status(
                db, document, STATUS_EMBEDDING, error_message=None
            )

            # 先清旧数据再写新数据：覆盖重解析与重建索引都走这条路
            await get_store().delete_document(document_id)
            await document_crud.delete_chunks(db, document_id)

            entry_meta = {
                "splitter": "recursive",
                "chunk_size": settings.chunk_size,
                "chunk_overlap": settings.chunk_overlap,
            }
            chunk_rows = await document_crud.add_chunks(
                db, document_id=document_id, texts=chunks, meta=entry_meta
            )

            embedder = get_embedder()
            batch_size = settings.embedding_batch_size
            for start in range(0, len(chunk_rows), batch_size):
                batch = chunk_rows[start : start + batch_size]
                vectors = await embedder.embed_documents([row.content for row in batch])
                await get_store().add(
                    ids=[row.vector_id for row in batch],
                    embeddings=vectors,
                    metadatas=[
                        {
                            "document_id": document_id,
                            "user_id": document.user_id,
                            "chunk_index": row.chunk_index,
                            "source": document.filename,
                        }
                        for row in batch
                    ],
                )

            await document_crud.update_status(
                db,
                document,
                STATUS_READY,
                chunk_count=len(chunk_rows),
                error_message=None,
            )
            logger.info(
                "入库完成 id=%s 片段=%s 字符=%s 向量化方式=%s",
                document_id,
                len(chunk_rows),
                len(text),
                embedder.name,
            )
        except AppError as exc:
            # 业务层已给出可读原因，直接落库展示给用户
            logger.warning("文档入库失败 id=%s: %s", document_id, exc)
            await document_crud.update_status(
                db, document, STATUS_FAILED, error_message=str(exc)
            )
        except Exception as exc:  # noqa: BLE001 —— 兜底：绝不让文档卡在中间状态
            logger.exception("文档入库出现未预期错误 id=%s", document_id)
            await document_crud.update_status(
                db,
                document,
                STATUS_FAILED,
                error_message=f"未预期的错误：{type(exc).__name__}: {exc}",
            )


async def delete_document(db: AsyncSession, user: User, document_id: int) -> None:
    """物理删除文档：向量库 → 关系库 → 磁盘文件（§7 约定）。"""
    document = await document_crud.get_by_id(db, document_id, user.id)
    if document is None:
        raise NotFoundError(f"文档 {document_id} 不存在")

    stored_path = document.stored_path

    # 顺序很关键：**先删向量**。若先删数据库记录再删向量，一旦删向量失败，
    # 就留下一批没有任何记录指向的孤儿向量——它们会继续被检索命中，
    # 用户看到「来源是已删除的文档」这种灵异现象，且很难清理。
    await get_store().delete_document(document_id)
    await document_crud.delete(db, document)

    # 磁盘文件删除失败不影响业务结果（记录和向量都已清掉），记警告即可，
    # 不因为一个删不掉的文件让整个删除请求失败。
    try:
        Path(stored_path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("删除文件失败（不影响文档删除）path=%s: %s", stored_path, exc)

    logger.info("文档已删除 id=%s", document_id)
    await audit_service.record(
        db,
        action="delete_document",
        user_id=user.id,
        target_type="document",
        target_id=document_id,
        detail={"filename": document.filename},
    )


async def update_document(
    db: AsyncSession,
    user: User,
    document_id: int,
    *,
    filename: str,
    content: bytes,
    content_type: str | None,
    dispatch,
) -> DocumentOut:
    """覆盖重解析（§7 的 PUT 语义）：用新文件替换旧内容并重跑入库。

    实现方式是「删旧建新」而不是原地更新：解析与切分的结果全部依赖文件内容，
    原地更新需要逐字段对比哪些 chunk 变了——那套增量的复杂度换不来实际收益，
    直接重跑一次同一路径更简单，也天然与失败重试共用逻辑。
    """
    document = await document_crud.get_by_id(db, document_id, user.id)
    if document is None:
        raise NotFoundError(f"文档 {document_id} 不存在")

    safe_name = sanitize_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in loader_module.SUPPORTED_SUFFIXES:
        raise UnsupportedFileTypeError(suffix)
    if not content:
        raise DocumentParseError(f"{safe_name}：文件内容为空")

    file_hash = hashlib.sha256(content).hexdigest()
    old_path = Path(document.stored_path)
    new_path = _storage_path(user.id, file_hash, safe_name)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    new_path.write_bytes(content)

    # 更新记录本身（走 crud 层统一出口，避免在服务层拼 SQL）
    document.filename = safe_name
    document.file_hash = file_hash
    document.stored_path = str(new_path)
    document.content_type = content_type
    document.size = len(content)
    document.chunk_count = 0
    document.error_message = None
    document.status = STATUS_PENDING
    await db.commit()
    await db.refresh(document)

    if old_path != new_path:
        try:
            old_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除被替换的旧文件失败 path=%s: %s", old_path, exc)

    # 旧的片段与向量由 process_document 开头统一清理
    dispatch(document.id)
    await audit_service.record(
        db,
        action="update_document",
        user_id=user.id,
        target_type="document",
        target_id=document_id,
        detail={"filename": safe_name, "size": len(content)},
    )
    return DocumentOut.model_validate(document)


async def reindex(dispatch) -> tuple[int, int]:
    """全量重建索引（§7 的 POST /knowledge/reindex）。

    典型用途：换了向量模型或改了切分参数。此时旧向量与新向量不在同一坐标系，
    必须整体重算——这也是 `vectorstore.add` 会校验维度一致的原因。

    返回 (文档总数, 已投递数)。
    """
    async with AsyncSessionLocal() as db:
        documents = await document_crud.list_all(db)
        dispatched = 0
        for document in documents:
            # 重置为 pending 再投递：process_document 的幂等分支会跳过 ready 的文档，
            # 所以必须先把状态改掉，否则重建索引对已完成的文档完全无效。
            await document_crud.update_status(
                db, document, STATUS_PENDING, error_message=None
            )
            dispatch(document.id)
            dispatched += 1
    logger.info("重建索引：共 %s 篇文档，已全部重新投递", dispatched)
    return len(documents), dispatched


async def recover_stale_documents() -> list[int]:
    """启动补偿：把上次没跑完的文档重置为 pending，返回需要重新投递的 id 列表。

    为什么需要（M3-Q3 选「进程内后台任务」的连带后果）：任务跑在应用进程里，
    进程重启或被 kill 时，任务会**无声消失**，文档永久停在 parsing / embedding。
    启动时扫一遍把它们救回来，是这个方案的必备补丁。
    """
    async with AsyncSessionLocal() as db:
        documents = await document_crud.list_in_progress(db)
        ids: list[int] = []
        for document in documents:
            await document_crud.update_status(
                db,
                document,
                STATUS_PENDING,
                error_message="上次入库未完成（服务重启），已自动重新排队",
            )
            ids.append(document.id)
        if ids:
            logger.warning("启动补偿：发现 %s 篇卡住的文档，已重新排队：%s", len(ids), ids)
        return ids
