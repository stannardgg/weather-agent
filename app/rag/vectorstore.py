"""【L3】知识库能力：向量库封装（Chroma）。

D4 已定用 Chroma。本文件是它在本项目的适配层，负责三件事：

1. **把同步调用放进线程池**。Chroma 的客户端 API 全是同步阻塞的，直接在异步函数里
   调用会卡住事件循环（CLAUDE.md §11），因此每个方法都用 `asyncio.to_thread` 包一层。
2. **只存向量与元数据，不存正文**。按 §6 的设计，正文留在关系库 `document_chunks`，
   这样按 ID 精确回查与删除都不必依赖向量库。元数据里带 `document_id / user_id /
   chunk_index / source`，其中 `user_id` 是 M4「每用户私有」（M3-Q4 决策）过滤的依据。
3. **在写入前做维度与数量校验**。Chroma 自己的 `DimensionError` 只给两串数字，
   很难看出是哪一批对不上，前置校验能让报错直接指向问题。

## ⚠️ 单进程约束（M3-Q3 决策的连带后果）

`PersistentClient` 是**单进程设计**：两个进程同时读写同一目录可能报锁错误。
M3-Q3 选了「进程内后台任务」，所以只有 uvicorn 一个进程碰它，是安全的。
**若将来改成独立 worker 进程，必须同时把这里改成 `HttpClient` 模式**
（即额外跑一个 chroma 服务），否则会出现偶发的锁冲突——详见 CLAUDE.md §13 的 M3-Q3 记录。
"""

import asyncio
import logging
from typing import Any

import chromadb

from app.core.config import settings
from app.core.exceptions import AppError
from app.schemas.common import ErrorCode

logger = logging.getLogger(__name__)


class VectorStoreError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(
            f"向量库操作失败：{message}",
            code=ErrorCode.INTERNAL_ERROR,
            http_status=500,
        )


class ChromaStore:
    """Chroma 的薄封装。所有公开方法都是 async，内部统一 to_thread。"""

    def __init__(self, path: str | None = None, collection: str | None = None) -> None:
        self._path = path or settings.chroma_dir
        self._collection_name = collection or settings.chroma_collection
        self._client: Any = None
        self._collection: Any = None
        # 首次写入时记下维度，后续写入不一致就直接报错（见 add 的说明）
        self._dimension: int | None = None

    # -- 连接与健康 ------------------------------------------------------

    def _get_collection(self):
        """惰性建连接：只有真正用到向量库时才去开目录，避免拖慢应用启动。"""
        if self._collection is None:
            try:
                self._client = chromadb.PersistentClient(path=self._path)
                self._collection = self._client.get_or_create_collection(
                    name=self._collection_name,
                    # 余弦距离：本项目向量都会归一化，余弦是最合适的度量
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception as exc:  # noqa: BLE001 —— chroma 的异常类型不稳定，统一转成自家错误
                raise VectorStoreError(
                    f"无法打开向量库目录「{self._path}」：{type(exc).__name__}: {exc}"
                ) from exc
        return self._collection

    async def heartbeat(self) -> bool:
        """健康探测：只确认向量库能打开，不做任何写入。"""
        try:
            await asyncio.to_thread(self._get_collection)
        except AppError:
            raise
        return True

    async def count(self) -> int:
        return await asyncio.to_thread(lambda: self._get_collection().count())

    # -- 写入 ------------------------------------------------------------

    async def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """批量写入向量。

        刻意**不传 `documents=`**：正文在关系库，这里只存向量与元数据（§6 设计要点）。
        """
        if not ids:
            return
        if not (len(ids) == len(embeddings) == len(metadatas)):
            raise VectorStoreError(
                f"写入向量库时 id/向量/元数据数量不一致："
                f"{len(ids)} / {len(embeddings)} / {len(metadatas)}"
            )
        dimensions = {len(vector) for vector in embeddings}
        if len(dimensions) != 1:
            raise VectorStoreError(f"同一批向量的维度不一致：{sorted(dimensions)}")
        dimension = dimensions.pop()
        if self._dimension is not None and dimension != self._dimension:
            raise VectorStoreError(
                f"向量维度与已有数据不一致：现存 {self._dimension} 维，本次 {dimension} 维。"
                "换了向量模型就必须重建整个索引（POST /api/v1/knowledge/reindex），"
                "否则新老向量不在同一坐标系，检索结果会毫无意义。"
            )
        await asyncio.to_thread(self._add_sync, ids, embeddings, metadatas, dimension)

    def _add_sync(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
        dimension: int,
    ) -> None:
        collection = self._get_collection()
        try:
            collection.add(ids=ids, embeddings=embeddings, metadatas=metadatas)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"写入 {len(ids)} 条向量失败：{type(exc).__name__}: {exc}") from exc
        self._dimension = dimension

    # -- 删除 ------------------------------------------------------------

    async def delete_document(self, document_id: int) -> int:
        """删除某文档的全部向量，返回删除条数。

        用 `where` 按元数据过滤而不是按 id 列表删除：调用方不需要知道有哪些 vector_id，
        即使关系库里的 chunk 记录已经不一致，也能靠元数据把向量清干净。
        """
        return await asyncio.to_thread(self._delete_document_sync, document_id)

    def _delete_document_sync(self, document_id: int) -> int:
        collection = self._get_collection()
        where = {"document_id": document_id}
        try:
            found = collection.get(where=where, include=[])
            ids = list(found.get("ids") or [])
            if ids:
                collection.delete(ids=ids)
            return len(ids)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"删除文档 {document_id} 的向量失败：{exc}") from exc

    async def list_ids(self, document_id: int) -> list[str]:
        """列出某文档的向量 id（自检脚本与测试用来确认写入是否真的发生）。"""
        return await asyncio.to_thread(self._list_ids_sync, document_id)

    def _list_ids_sync(self, document_id: int) -> list[str]:
        collection = self._get_collection()
        found = collection.get(where={"document_id": document_id}, include=[])
        return list(found.get("ids") or [])

    # -- 检索 ------------------------------------------------------------

    async def query(
        self,
        embedding: list[float],
        *,
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """按向量检索最近邻，返回 `[{id, metadata, distance}]`（按距离升序）。

        `where` 的作用不是「优化」而是「隔离」：M4 的检索**必须**传
        `{"user_id": 当前用户}`，否则一次检索就能命中别人的文档片段。
        这里刻意把它做成**必填的关键字参数**而不是可选参数——忘了传会直接 TypeError，
        而不是安静地搜全库（§8.2 / §11）。

        **不传 `documents=`**：正文在关系库 `document_chunks`，向量库只存向量与元数据
        （§6 设计要点）。检索到的片段要由调用方按 `chunk_id` 回关系库取正文。
        """
        if where is None:
            raise VectorStoreError(
                "检索必须显式指定 where 过滤条件（如 {'user_id': 当前用户}），"
                "否则会跨用户命中他人的文档片段"
            )
        if top_k < 1:
            raise VectorStoreError(f"top_k 必须至少为 1，收到 {top_k}")
        return await asyncio.to_thread(self._query_sync, embedding, top_k, where)

    def _query_sync(
        self,
        embedding: list[float],
        top_k: int,
        where: dict[str, Any],
    ) -> list[dict[str, Any]]:
        collection = self._get_collection()
        # 空集合时直接返回：Chroma 对空集合调 query 的行为在不同版本间不完全一致，
        # 与其依赖它的实现细节，不如在这里给一个确定的「没有结果」。
        if collection.count() == 0:
            return []
        try:
            found = collection.query(
                query_embeddings=[embedding],
                n_results=top_k,
                where=where,
                # 只要元数据与距离——正文不在向量库里（见方法说明）
                include=["metadatas", "distances"],
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"向量检索失败：{type(exc).__name__}: {exc}") from exc

        ids = (found.get("ids") or [[]])[0]
        metadatas = (found.get("metadatas") or [[]])[0]
        distances = (found.get("distances") or [[]])[0]
        return [
            {
                "id": vector_id,
                "metadata": dict(meta or {}),
                "distance": float(distance),
            }
            for vector_id, meta, distance in zip(ids, metadatas, distances, strict=False)
        ]


_store: ChromaStore | None = None


def get_store() -> ChromaStore:
    global _store
    if _store is None:
        if settings.vector_store_type != "chroma":
            raise VectorStoreError(f"不支持的向量库类型「{settings.vector_store_type}」")
        _store = ChromaStore()
    return _store


def reset_store() -> None:
    """清空单例（测试用：改完 chroma_dir 后需要重新指向临时目录）。"""
    global _store
    _store = None
