"""向量化自检：验证「向量真的表示语义」以及各项配置是否自洽。

与 `tests/test_rag.py` 的分工：
- 那里的用例全部离线（不联网、不下载模型），测的是解析失败分支、模型名/维度/前缀的判定逻辑；
- 这个脚本要**真的把模型跑起来**，只有它能回答「检索到底有没有用」这个问题。

需要外部资源（首次运行会下载模型，约 90MB；之后走本地缓存），因此单独成脚本，
与 `check_weather_api.py` 同一套思路。

用法:
    .venv/Scripts/python.exe scripts/check_embedding.py

脚本只读不写：不会碰你的数据库与向量库，也不会改动任何配置。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.rag.embedder import (  # noqa: E402
    FastembedEmbedder,
    _resolve_model_dimension,
    get_embedder,
    reset_embedder,
)

PASS = "  [OK]"
FAIL = "  [!!]"
INFO = "  [--]"
WARN = "  [~~]"


class Checks:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            print(f"{PASS} {name}")
        else:
            self.failed += 1
            print(f"{FAIL} {name}" + (f" —— {detail}" if detail else ""))
        return condition


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


# 用于语义验证的样本：前半是「问题的答案」，后半是同领域干扰项。
# 干扰项刻意与答案共享大量词汇，否则测不出真正的语义区分能力。
DOCS = [
    ("一线城市住宿费每晚上限六百元，二线城市为四百元，超出部分自理。", "住宿"),
    ("餐费补贴标准为每人每天一百元，不区分城市等级，按实际出差天数计发。", "餐补"),
    ("交通费按实际发生金额据实报销，需提供正规票据，市内交通不予报销。", "交通"),
    ("系统故障分级中，一级故障须在十五分钟内响应，二级故障为一小时。", "故障"),
    ("故障等级共分四级，等级越高代表影响范围越大，需上报的层级也越高。", "故障"),
    ("客户投诉须在二十四小时内首次响应，并在三个工作日内给出解决方案。", "投诉"),
    ("员工内部投诉渠道为人力资源部邮箱，受理后五个工作日内答复。", "投诉"),
]
QUERIES = [
    ("住宿费上限是多少？", 0),
    ("餐费补贴怎么算？", 1),
    ("一级故障多久要响应？", 3),
    ("客户投诉的处理时限？", 5),
]


def report_config() -> None:
    print("=" * 76)
    print("向量化自检（D-M4-1 决策：本地 ONNX / fastembed）")
    print("=" * 76)
    print(f"\n[1] 当前配置")
    print(f"{INFO} EMBEDDING_PROVIDER = {settings.embedding_provider}")
    print(f"{INFO} EMBEDDING_MODEL    = {settings.embedding_model}")
    print(f"{INFO} EMBEDDING_DIM      = {settings.embedding_dim}")
    print(f"{INFO} 查询指令前缀设置   = {settings.embedding_query_instruction!r}（None = 按模型名自动）")
    print(f"{INFO} HF_ENDPOINT        = {settings.hf_endpoint}")
    print(f"{INFO} 模型缓存目录       = {settings.fastembed_cache_path}")
    cache = PROJECT_ROOT / settings.fastembed_cache_path.lstrip("./")
    if cache.exists():
        size_mb = sum(f.stat().st_size for f in cache.rglob("*") if f.is_file()) / 1024 / 1024
        print(f"{INFO} 缓存已存在         = 是（{size_mb:.1f} MB）")
    else:
        print(f"{WARN} 缓存目录还不存在，首次运行会下载模型（约 90MB）")


def main() -> int:
    checks = Checks()
    report_config()

    print(f"\n[2] 解析模型元数据（离线，不需要下载）")
    try:
        model_dim = _resolve_model_dimension(settings.embedding_model)
        print(f"{INFO} 模型 {settings.embedding_model} 的真实维度 = {model_dim}")
        checks.check(
            "模型名有效且能被 fastembed 识别",
            True,
        )
        checks.check(
            f"EMBEDDING_DIM({settings.embedding_dim}) 与模型维度一致",
            model_dim == settings.embedding_dim,
            f"模型为 {model_dim}，配置为 {settings.embedding_dim}",
        )
    except Exception as exc:  # noqa: BLE001
        checks.check("模型名有效", False, f"{type(exc).__name__}: {exc}")
        print(f"\n结果：{checks.passed} 项通过，{checks.failed} 项失败")
        return 1

    print(f"\n[3] 装配向量化器（provider={settings.embedding_provider}）")
    reset_embedder()
    try:
        embedder = get_embedder()
    except Exception as exc:  # noqa: BLE001
        checks.check("get_embedder() 成功", False, f"{type(exc).__name__}: {exc}")
        print(f"\n结果：{checks.passed} 项通过，{checks.failed} 项失败")
        return 1

    checks.check("装配成功", True)
    checks.check(
        "不是占位向量（占位向量检索无意义，只用于离线测试）",
        embedder.is_placeholder is False,
        f"当前是 {embedder.name}",
    )
    print(f"{INFO} name = {embedder.name}, dimension = {embedder.dimension}")
    print(f"{INFO} 查询前缀 = {getattr(embedder, 'query_instruction', '（不适用）')!r}")

    if embedder.is_placeholder:
        print(f"\n{WARN} 当前是占位向量，后续「语义有效性」检查没有意义，已跳过。")
        print(f"\n结果：{checks.passed} 项通过，{checks.failed} 项失败")
        return 1 if checks.failed else 0

    print(f"\n[4] 加载模型并做首次推理（首次会下载，可能要等十几秒）")
    started = time.time()
    try:
        vectors = asyncio.run(embedder.embed_documents([d[0] for d in DOCS]))
    except Exception as exc:  # noqa: BLE001
        checks.check("模型加载并完成推理", False, f"{type(exc).__name__}: {exc}")
        print(f"\n{WARN} 如果报的是网络错误，多半是模型下载不通：")
        print(f"{WARN}   当前 HF_ENDPOINT = {settings.hf_endpoint}")
        print(f"{WARN}   可在 .env 里换成可用的镜像，或把已下好的模型目录拷到 {settings.fastembed_cache_path}")
        print(f"\n结果：{checks.passed} 项通过，{checks.failed} 项失败")
        return 1
    elapsed = time.time() - started
    print(f"{INFO} 耗时 {elapsed:.1f}s（{len(vectors)} 条）")

    doc_matrix = np.array(vectors)
    checks.check("向量维度与模型维度一致", doc_matrix.shape[1] == model_dim, str(doc_matrix.shape))
    # 归一化不是必须的（Chroma 用余弦距离会自行处理），但归一化后余弦与内积等价，
    # 排查问题时更好算。这里只作记录，不作为失败项。
    norms = np.linalg.norm(doc_matrix, axis=1)
    print(f"{INFO} 向量模长范围 {norms.min():.4f} ~ {norms.max():.4f}（归一化与否都能用余弦距离）")

    print(f"\n[5] 确定性：同一段文本必须得到完全相同的向量")
    again = asyncio.run(embedder.embed_documents([DOCS[0][0]]))
    checks.check(
        "同一文本两次结果完全一致",
        np.allclose(np.array(again[0]), doc_matrix[0]),
        "不一致会导致「重建索引后检索结果变了」这种无法复现的问题",
    )

    print(f"\n[6] 语义有效性：意思相近的文本应当比无关文本更接近")
    related = cosine(
        np.array(asyncio.run(embedder.embed_query("住宿费上限是多少？"))),
        doc_matrix[0],
    )
    unrelated = cosine(
        np.array(asyncio.run(embedder.embed_query("住宿费上限是多少？"))),
        doc_matrix[3],
    )
    print(f"{INFO} 「住宿费上限是多少？」 vs 住宿条款 = {related:.4f}")
    print(f"{INFO} 「住宿费上限是多少？」 vs 故障分级 = {unrelated:.4f}")
    checks.check(
        "相关文本的相似度明显高于无关文本",
        related > unrelated + 0.15,
        f"相关 {related:.4f} 未明显高于无关 {unrelated:.4f}",
    )

    print(f"\n[7] 检索排序：用小规模语料验证真实召回效果")
    query_vectors = np.array(
        [asyncio.run(embedder.embed_query(q)) for q, _ in QUERIES]
    )
    scores = query_vectors @ doc_matrix.T
    ranks: list[int] = []
    for i, (query, gold) in enumerate(QUERIES):
        order = np.argsort(-scores[i])
        rank = int(np.where(order == gold)[0][0]) + 1
        ranks.append(rank)
        top3 = " > ".join(f"文档{j}({scores[i][j]:.3f})" for j in order[:3])
        mark = "✓" if rank == 1 else "✗"
        print(f"{INFO} {mark} 「{query}」 真答案位次={rank}  {top3}")
    recall1 = sum(1 for r in ranks if r == 1)
    mrr = sum(1 / r for r in ranks) / len(ranks)
    checks.check(
        f"Recall@1 达到 {len(QUERIES)}/{len(QUERIES)}",
        recall1 == len(QUERIES),
        f"实际 {recall1}/{len(QUERIES)}，MRR={mrr:.4f}",
    )
    print(f"{INFO} 合计：Recall@1={recall1}/{len(QUERIES)}  MRR={mrr:.4f}")

    print(f"\n[8] 查询前缀的影响（供你判断是否要在自己的语料上关掉它）")
    plain = FastembedEmbedder(instruction="")
    with_prefix = get_embedder()
    plain_docs = np.array(asyncio.run(plain.embed_documents([d[0] for d in DOCS])))
    rows = []
    for tag, emb in (("不加前缀", plain), ("加前缀  ", with_prefix)):
        qv = np.array([asyncio.run(emb.embed_query(q)) for q, _ in QUERIES])
        s = qv @ plain_docs.T
        r = [int(np.where(np.argsort(-s[i]) == gold)[0][0]) + 1 for i, (_, gold) in enumerate(QUERIES)]
        hit = sum(1 for x in r if x == 1)
        rows.append((tag, hit, r))
        print(f"{INFO} {tag}: Recall@1={hit}/{len(QUERIES)}  位次={r}")
    # 只作对比展示，不判定通过与否：本项目用 17 条中文查询实测过「有差异但不改变排序」，
    # 样本量不足以定论。详见 app/rag/embedder.py 顶部记录。
    print(f"{WARN} 结论：小样本上两者排序往往一致，前缀的收益需要在你自己的真实语料上评估。")
    print(f"{WARN} 要关掉它：在 .env 里设 EMBEDDING_QUERY_INSTRUCTION=（留空即强制不加）")

    print("\n" + "=" * 76)
    print(f"结果：{checks.passed} 项通过，{checks.failed} 项失败")
    print("=" * 76)
    return 1 if checks.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
