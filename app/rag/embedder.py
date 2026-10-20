"""【L3】知识库能力：向量化（把一段文字变成一串「语义坐标」）。

一个向量化器需要同时满足两件事：

1. **同一段文本永远得到同一个向量**——否则重建索引后检索结果会变，问题无法复现。
2. **意思相近的文本得到相近的向量**——否则检索没有意义。

## 两种实现（`EMBEDDING_PROVIDER` 切换）

| provider | 用途 | 是否可上线 |
| --- | --- | --- |
| `fastembed` | 本地 ONNX 真实向量模型（默认，D-M4-1 用户选定） | ✅ |
| `placeholder` | 开发期占位替身：结构真、语义假 | ❌ 非 dev 环境被配置校验拦下 |

## 关于本地 ONNX 路线（D-M4-1 决策：方案 A）

选它而不是 PyTorch，是因为环境里**本来就有 `onnxruntime`**（chromadb 的传递依赖），
所以只需多装一个 `fastembed` 包加一个 90MB 的模型，不必引入 torch 那 2.5GB。
推理走 ONNX 有图优化，比 PyTorch CPU 更快。

**模型只在首次运行时下载**，之后完全离线。缓存目录由 `FASTEMBED_CACHE_PATH` 指定
（默认在项目内 `data/models/fastembed`），这样「私有化部署」时把整个目录拷到目标机器即可。

## ⚠️ 查询前缀：一个不报错、只降质的地方

bge-zh 系列模型的**官方推荐用法**是给「检索查询」加一句指令前缀，而**入库的文档不加**。
这源于它的训练方式：查询与文档用了不对称的编码方式。

**本项目的实测结果（2026-10-12，如实记录，不是复述官方说法）**：

- 3 组样本、共 17 条中文查询，**加与不加前缀都没有改变排序结果**（Recall@1 全对）。
- 在贴近真实业务的场景（209 字的长 chunk vs 9 字的短查询）下，加前缀让
  「真答案与最强干扰」的平均分差从 +0.0796 扩大到 +0.0827（约 +4%）。
- 在短文档场景下，加前缀只是让相似度的**绝对值整体下降**，排序不变。
- 结论：**样本量小、统计效力不足，既不能证明收益也不能证明有害。**

因此默认**按模型名自动加前缀**（跟随模型作者的推荐用法，且实测方向为正），
但把它做成可配置项：`EMBEDDING_QUERY_INSTRUCTION`
- 留空/不设 → 按模型名自动判断
- 显式设为空字符串 → 强制不加（想在自己的真实语料上做 A/B 时用）

## 换模型的注意事项

换了模型（或改了 `EMBEDDING_MODEL`）之后**必须重建索引**（`POST /api/v1/knowledge/reindex`）：
不同模型的向量不在同一坐标系，混在一起检索结果毫无意义。
`vectorstore.py` 里有维度护栏会在写入时报错，但**同维度的不同模型**它测不出来，
所以这条只能靠人记住——这也是把它写进本节的原因。
"""

import asyncio
import hashlib
import logging
import math
import os
from typing import Any, Protocol, runtime_checkable

from app.core.config import settings
from app.core.exceptions import AppError
from app.schemas.common import ErrorCode

logger = logging.getLogger(__name__)

# bge-zh 系列官方推荐的中文检索指令前缀。只加在**查询**上，文档不加。
_ZH_RETRIEVAL_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

# 按模型名自动选用的前缀。表里没有的模型一律不加（多数模型不需要）。
_QUERY_INSTRUCTIONS: dict[str, str] = {
    "BAAI/bge-small-zh-v1.5": _ZH_RETRIEVAL_INSTRUCTION,
    "BAAI/bge-large-zh-v1.5": _ZH_RETRIEVAL_INSTRUCTION,
    "BAAI/bge-base-zh-v1.5": _ZH_RETRIEVAL_INSTRUCTION,
}


@runtime_checkable
class Embedder(Protocol):
    """向量化器的统一契约。

    `is_placeholder` 是刻意放进协议里的：入库流程会把「当前用的是假向量」写进日志，
    避免以后有人看到检索结果不对却不知道原因。

    `embed_documents` 与 `embed_query` **必须分开**——不是为了对称好看，
    而是因为多数检索模型对「查询」和「文档」的编码方式本就不对称
    （见上面关于查询前缀的说明）。把两者合成一个方法，这个差异就没有地方落。
    """

    name: str
    dimension: int
    is_placeholder: bool

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量向量化（入库用）。**不加查询前缀**。"""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """单条向量化（检索用）。与 embed_documents 必须同源，否则两边不在同一坐标系。"""
        ...


class UnsupportedEmbeddingProviderError(AppError):
    def __init__(self, provider: str) -> None:
        super().__init__(
            f"不支持的向量化方式「{provider}」，当前实现了：fastembed、placeholder",
            code=ErrorCode.INTERNAL_ERROR,
            http_status=500,
        )


class EmbeddingModelError(AppError):
    """模型名写错、维度对不上这类「配置错」——启动或首次使用时就要响亮报错。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, code=ErrorCode.INTERNAL_ERROR, http_status=500)


class PlaceholderEmbedder:
    """占位向量：确定性哈希 → 固定维度 → 归一化。**无语义**。

    保留它的唯一理由是：测试与离线冒烟需要一个不依赖网络、不依赖模型文件的替身。
    生产路径必须走 `FastembedEmbedder`（非 dev 环境已被配置校验拦下）。
    """

    name = "placeholder"
    is_placeholder = True

    def __init__(self, dimension: int | None = None) -> None:
        self.dimension = dimension or settings.embedding_dim

    def _vector(self, text: str) -> list[float]:
        """把文本映射成固定维度的确定性向量。

        两个必须遵守的点：

        1. **用 hashlib 而不是内置 `hash()`**。内置 hash 对字符串加了随机盐
           （PYTHONHASHSEED），换个进程结果就变，会出现「同一份文档重建索引后
           检索结果完全不同」这种极难排查的现象。
        2. **归一化到单位长度**。Chroma 用余弦距离，未归一化的向量在同一条文本上
           也会因为长度差异产生偏差（虽然这里影响小，但真实模型必须归一化，
           接口保持一致免得将来接真模型时才发现问题）。
        """
        values: list[float] = []
        counter = 0
        while len(values) < self.dimension:
            digest = hashlib.sha256(f"{counter}\x00{text}".encode("utf-8")).digest()
            # 每个字节 0-255 映射到 [-1, 1)
            values.extend(byte / 127.5 - 1.0 for byte in digest)
            counter += 1
        values = values[: self.dimension]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]

    def _embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # 占位实现是纯内存计算（微秒级），但仍走线程池：这样接口行为与真实模型一致，
        # 将来换成真模型时不用改调用方。
        return await asyncio.to_thread(self._embed_many, texts)

    async def embed_query(self, text: str) -> list[float]:
        vectors = await asyncio.to_thread(self._embed_many, [text])
        return vectors[0]


class FastembedEmbedder:
    """本地 ONNX 真实向量模型（默认实现）。

    推理是同步阻塞的，全部经 `asyncio.to_thread` 调用（CLAUDE.md §11）。
    模型对象**惰性创建**：只有第一次真正要算向量时才下载/加载模型。
    这样「应用启动」和「健康检查」不会被一个 90MB 的下载拖住。
    """

    is_placeholder = False

    def __init__(
        self,
        model_name: str | None = None,
        dimension: int | None = None,
        instruction: str | None = None,
    ) -> None:
        self._model_name = model_name or settings.embedding_model
        self.name = f"fastembed:{self._model_name}"
        # 模型名**必须**每次都解析一遍，不能因为调用方传了 dimension 就跳过：
        # 否则模型名写错时不会在构造阶段报错，而要等到第一次真正向量化才炸，
        # 而且那时的报错来自 fastembed 内部，没有我们现在给的候选清单有用。
        model_dimension = _resolve_model_dimension(self._model_name)
        self.dimension = dimension or model_dimension
        self._instruction = (
            _resolve_query_instruction(self._model_name) if instruction is None else instruction
        )
        self._model: Any = None

    @property
    def query_instruction(self) -> str:
        """当前生效的查询前缀（空字符串 = 不加）。暴露出来便于自检脚本打印。"""
        return self._instruction

    def _get_model(self) -> Any:
        if self._model is None:
            # 延迟导入：fastembed 会拉进 onnxruntime / huggingface_hub / tokenizers 一大串，
            # 放在模块顶层会让应用启动慢数百毫秒，而多数请求用不到向量化。
            from fastembed import TextEmbedding

            self._model = TextEmbedding(
                self._model_name,
                cache_dir=settings.fastembed_cache_path,
            )
        return self._model

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        vectors = self._get_model().embed(
            texts, batch_size=settings.embedding_batch_size
        )
        # fastembed 返回 numpy 数组的生成器，转成纯 Python list 才能交给 Chroma 序列化
        return [vector.tolist() for vector in vectors]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_sync, list(texts))

    async def embed_query(self, text: str) -> list[float]:
        # 查询前缀只加在这里。加错了（比如加到文档上）不会报错，只会让检索质量下降，
        # 所以这两条路径必须是两个方法，不能合并。
        vectors = await asyncio.to_thread(self._embed_sync, [self._instruction + text])
        return vectors[0]


def _apply_fastembed_env() -> None:
    """把配置里的下载源与缓存目录同步给 fastembed 底层。

    必须在 `import fastembed`（进而 import huggingface_hub）**之前**调用：
    huggingface_hub 在模块导入时就把 `HF_ENDPOINT` 读进常量，之后再改环境变量不生效。

    用 `setdefault` 而不是直接赋值是刻意的——外部显式导出的环境变量优先级更高，
    这样「临时用 HF_ENDPOINT=... 跑一次」这种调试方式仍然可用。
    """
    if settings.hf_endpoint:
        os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)
    if settings.fastembed_cache_path:
        os.environ.setdefault("FASTEMBED_CACHE_PATH", settings.fastembed_cache_path)


def _resolve_model_dimension(model_name: str) -> int:
    """从 fastembed 自带的模型元数据里读出维度。

    刻意**不硬编码** 512：换模型时维度会变，硬编码就会悄悄错。
    `list_supported_models()` 读的是包内自带清单，**不需要联网也不需要模型文件**，
    所以可以在构造阶段安全调用。
    """
    _apply_fastembed_env()
    from fastembed import TextEmbedding

    entries = TextEmbedding.list_supported_models()
    for entry in entries:
        if entry.get("model") == model_name:
            return int(entry["dim"])

    # 模型名写错时给出可用的候选，而不是抛一个 KeyError 让人去翻文档
    chinese = sorted(
        e["model"] for e in entries if any(tag in e["model"].lower() for tag in ("zh", "multilingual"))
    )
    raise EmbeddingModelError(
        f"fastembed 不支持模型「{model_name}」。含中文能力的候选："
        f"{'、'.join(chinese) or '（无）'}；"
        f"完整清单见 https://qdrant.github.io/fastembed/examples/Supported_Models/"
    )


def _resolve_query_instruction(model_name: str) -> str:
    """查询前缀的取值优先级：显式配置 > 按模型名自动判断 > 不加。"""
    configured = settings.embedding_query_instruction
    if configured is not None:
        return configured
    instruction = _QUERY_INSTRUCTIONS.get(model_name, "")
    if instruction:
        logger.info("向量模型 %s 启用检索指令前缀（仅作用于查询）", model_name)
    return instruction


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """按 EMBEDDING_PROVIDER 返回向量化器（单例）。"""
    global _embedder
    if _embedder is None:
        provider = settings.embedding_provider

        if provider == "placeholder":
            logger.warning(
                "当前使用【占位向量】PlaceholderEmbedder："
                "入库链路可完整跑通，但产出的向量无语义，检索结果仅供验证管道，"
                "不能用于评估问答质量。改用真实模型请设 EMBEDDING_PROVIDER=fastembed。"
            )
            _embedder = PlaceholderEmbedder()
        elif provider == "fastembed":
            embedder = FastembedEmbedder()
            # 配置里的维度与模型真实维度对不上时直接报错。
            # 放过它的话，后果是「配置写着 256、实际存的是 512」，
            # 或者更糟：换了模型但忘了重建索引，新老向量混在一个集合里。
            if settings.embedding_dim != embedder.dimension:
                raise EmbeddingModelError(
                    f"EMBEDDING_DIM={settings.embedding_dim} 与模型 "
                    f"「{embedder._model_name}」的实际维度 {embedder.dimension} 不一致。"
                    f"请把 EMBEDDING_DIM 改成 {embedder.dimension}，"
                    "然后执行 POST /api/v1/knowledge/reindex 重建索引"
                    "（不同模型的向量不在同一坐标系，混用会让检索结果毫无意义）。"
                )
            logger.info(
                "向量化使用 %s（%s 维，模型缓存 %s）",
                embedder.name,
                embedder.dimension,
                settings.fastembed_cache_path,
            )
            _embedder = embedder
        else:
            raise UnsupportedEmbeddingProviderError(provider)

    return _embedder


def reset_embedder() -> None:
    """清空单例（测试用：改完 settings 后需要重新构造）。"""
    global _embedder
    _embedder = None
