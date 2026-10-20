"""【L3】知识库能力：文本切分。

M3-Q2 用户选定：**递归字符切分（RecursiveCharacterTextSplitter）**，
块大小 800 字符、重叠 100 字符（`CHUNK_SIZE` / `CHUNK_OVERLAP`，可配置）。

「递归」的含义（白话）：先尝试用最强的语义边界去切（空行＝段落），如果切出来的
块还是太大，就在这些块内部换用更弱的边界（换行 → 句号 → 逗号 → 空格），
一直退到「硬按字符数切」为止。这样能尽量让断开处落在自然边界上。

**为什么必须显式配置中文分隔符**：LangChain 的默认分隔符是英文那套
`["\n\n", "\n", " ", ""]`。中文句子之间没有空格，默认规则只能在字符级硬切，
经常把一个句子从中间斩断——检索命中后喂给模型的就成了半句话。
"""

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import settings

# 语义边界从强到弱。中文标点必须列出，理由见文件头。
SEPARATORS = [
    "\n\n",  # 段落
    "\n",  # 换行
    "。",  # 句号
    "！",
    "？",
    "；",
    "…",
    "，",  # 逗号：退到这一级说明句子确实太长了
    "、",
    " ",
    "",  # 最后手段：按字符硬切
]


def build_splitter() -> RecursiveCharacterTextSplitter:
    """按配置构造切分器。

    `length_function=len` 表示按**字符数**而非 token 数计量。这样做的原因是：
    精确按 token 切需要引入一个 tokenizer（额外依赖 + 与目标 embedding 模型强绑定），
    而本项目的目标是「先让链路跑通、参数可调」，字符数已经够用。
    """
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=SEPARATORS,
        keep_separator="end",  # 把句号留在前一块末尾，读起来才是完整的句子
        length_function=len,
    )


def split_text(text: str) -> list[str]:
    """切分成块，并丢掉纯空白块。

    切分器在连续空行的边界处可能产出纯空白块（如 `"\\n\\n"`）。这种块入库只会
    白占一条记录、还会在检索时被命中后塞给模型一堆空白，所以直接过滤。
    同时统一做 strip，避免块首尾带着换行导致向量里混入无关噪声。
    """
    chunks = build_splitter().split_text(text)
    cleaned = [chunk.strip() for chunk in chunks]
    return [chunk for chunk in cleaned if chunk]
