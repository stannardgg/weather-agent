"""【L3】知识库能力：向量检索（M4 落地，对应 CLAUDE.md §8.2 的检索链路）。

M3 只做「入库」，本文件补齐「检索」。链路是：

    问题 → 向量化（embed_query）→ 向量库 top_k 检索（按 user_id 过滤）
         → 距离阈值过滤 → 按 vector_id 回关系库取正文 → 交给上层组装 prompt

## 三个关键设计点

1. **`where={"user_id": ...}` 是硬约束，不是优化项**（§8.2）。
   M3 把 `user_id` 写进了每个向量的元数据，就是为了让这里能过滤。仅靠「入库时隔离」
   是不够的——少了这个条件，一次向量检索就能命中别人的文档片段。
   本函数把它做成**必填的关键字参数**，漏传直接 TypeError（`ChromaStore.query`
   还会再拦一道）。

2. **关系库这边再校验一次归属**（`list_chunks_by_vector_ids` 里的 JOIN）。
   正文不在向量库里（§6 设计要点），所以取正文时是一次新的数据库访问。
   两次过滤独立成立，等于把「越权拿到别人文档」这件事压在两个条件上而不是一个。

3. **阈值过滤之后才去查正文**（M4-Q3 选 Q3A：纯向量 top_k + 距离阈值）。
   顺序反过来的话，会把注定要被丢弃的片段也查一遍正文，白白多刷一次数据库。

## 本实现刻意不做的事

- **不做重排（rerank）**。§8.2 把它标为「可选」，Q3A 也没选它。做重排需要额外一个
  模型（如 bge-reranker）或一次额外的 LLM 调用，收益要靠评测集才能确认，
  在没有评测集的情况下属于「加了但不知道有没有用」。
- **不做 top_k 扩大后再截断**。Q3A 的定义就是「top_k + 阈值」，先按 top_k 取再过滤，
  不会出现「阈值过滤掉一些、还能替补上来」的行为。这是有意的：结果数量随问题变化，
  比「凑满 5 条」更诚实——凑满就得塞进不相关的片段，正是幻觉的来源。
"""

import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics as metrics_module
from app.core.config import settings
from app.core.logging import get_logger
from app.crud import document as document_crud
from app.rag.embedder import get_embedder
from app.rag.vectorstore import get_store

logger = get_logger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    """一条检索结果。

    与向量库里的元数据字段一一对应，另外补上从关系库取回的正文 `content`。
    `distance` 是**余弦距离**（越小越相关）；对外展示用 `score` 更直观。
    """

    chunk_id: str  # 即 vector_id，形如 doc12-chunk3
    document_id: int
    chunk_index: int
    source: str  # 文件名，用于回答里的来源标注（§8.3 要求）
    content: str
    distance: float

    @property
    def score(self) -> float:
        """余弦相似度 = 1 − 余弦距离。范围约 [0, 1]，越大越相关。

        加它的理由：余弦**距离**是「越小越好」，而人在看日志与调阈值时习惯「越大越好」，
        两个方向混用很容易把阈值调反。对外（日志、来源标注、测试断言）统一用 score。
        """
        return 1.0 - self.distance

    @property
    def label(self) -> str:
        """来源标注文本，形如 `气象应急预案.pdf#第3段`。

        §8.3 要求检索片段必须带来源标注，且要在回答中注明依据。这里统一生成，
        避免各调用点自己拼、拼出几种不同格式。
        """
        return f"{self.source}#第{self.chunk_index + 1}段"


async def retrieve(
    db: AsyncSession,
    query: str,
    *,
    user_id: int,
    top_k: int | None = None,
    score_threshold: float | None = None,
) -> list[RetrievedChunk]:
    """按语义检索当前用户的知识库片段。

    - `top_k` 默认取 `RETRIEVE_TOP_K`；`score_threshold` 默认取 `RETRIEVE_SCORE_THRESHOLD`。
    - 与阈值比较的是 `score`（相似度，越大越相关），不是 distance。见 `RetrievedChunk.score`。
    - **检索不到任何内容时返回空列表**，不抛错：这不是异常，而是一个需要被如实传达的
      事实（§8.3：此时必须回答「知识库中未找到相关内容」，禁止编造）。是否降级由上层决定。
    """
    text = (query or "").strip()
    if not text:
        # 空问题没有必要去做一次向量检索（还得把查询向量算出来，纯浪费）
        return []

    effective_top_k = top_k if top_k is not None else settings.retrieve_top_k
    threshold = (
        score_threshold if score_threshold is not None else settings.retrieve_score_threshold
    )

    started = time.perf_counter()
    embedder = get_embedder()
    embedding = await embedder.embed_query(text)
    hits = await get_store().query(
        embedding,
        top_k=effective_top_k,
        # ⚠️ 这一行是用户隔离的兑现点，去掉它就会跨用户命中（§8.2）
        where={"user_id": user_id},
    )
    # 阈值过滤放在查正文之前：被丢弃的片段没必要再去数据库读一遍正文
    kept = [hit for hit in hits if (1.0 - hit["distance"]) >= threshold]

    rows = await document_crud.list_chunks_by_vector_ids(
        db, [hit["id"] for hit in kept], user_id
    )
    by_vector_id = {row.vector_id: row for row in rows}

    results: list[RetrievedChunk] = []
    for hit in kept:
        metadata = hit["metadata"]
        row = by_vector_id.get(hit["id"])
        if row is None:
            # 向量还在但关系库记录没了。正常成因：写入分两步（先关系库再向量库），
            # 中断在中间；或覆盖重解析刚清完旧片段。跳过并告警，不影响其余结果。
            logger.warning(
                "检索命中向量 %s 但关系库无对应片段（document_id=%s），已跳过",
                hit["id"],
                metadata.get("document_id"),
            )
            continue
        results.append(
            RetrievedChunk(
                chunk_id=hit["id"],
                document_id=int(metadata.get("document_id") or row.document_id),
                chunk_index=int(metadata.get("chunk_index", row.chunk_index)),
                source=str(metadata.get("source") or ""),
                content=row.content,
                distance=hit["distance"],
            )
        )

    elapsed_ms = (time.perf_counter() - started) * 1000
    metrics_module.observe_summary(
        "retrieval_duration_seconds",
        "向量检索耗时（秒）",
        value=elapsed_ms / 1000,
    )
    logger.info(
        "检索完成 user_id=%s 命中=%s 阈值内=%s 可用=%s 耗时=%.1fms 向量化方式=%s",
        user_id,
        len(hits),
        len(kept),
        len(results),
        elapsed_ms,
        embedder.name,
    )
    if not results:
        # 单独记一条：检索为空是「答不出来」的直接原因，排查时必须一眼看到，
        # 而不是从「命中=3 阈值内=0」里自己推。
        logger.info(
            "检索结果为空 user_id=%s 阈值=%.4f 最高相似度=%s",
            user_id,
            threshold,
            f"{max((1.0 - h['distance'] for h in hits), default=0.0):.4f}" if hits else "无命中",
        )
    return results
