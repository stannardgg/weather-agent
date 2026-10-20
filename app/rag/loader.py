"""【L3】知识库能力：文档解析（把 PDF / Word / 文本统一变成纯文本）。

## 为什么是自己写，而不是用 LangChain 的 loader

D7 原本选的是「LangChain Document Loaders」，其载体是 `langchain-community`。
**该包已于 2026-05-22 官宣 sunset、仓库在 2026-06-19 归档为只读**，官方原文明确
「这个包里的任何东西都不会再被修复，包括安全漏洞」。

官方给出的三条替代路径里，适用于本项目的是第二条——**直接在应用代码里实现简单工具**。
原因是：本项目只支持三类格式，而 `PyPDFLoader` / `Docx2txtLoader` 的底层后端
（`pypdf`、`docx2txt`）我们**本来就装着**，langchain 那层包装大约只有 30 行。
另外官方**并没有**把 loader 拆成 `langchain-pypdf` 这类独立包（拆出去的是
chat model / vector store 这类 provider 集成），所以「迁到专用包」这条路走不通。

这项决定（D-M4-2 方案 A）的收益是**依赖净减少**：`langchain-community` 没有任何包
依赖它，而它独占牵着 `langchain-classic`，移除它可以让依赖树更干净。

本文件保留这一层适配的意义：
1. **编码回退**：中文 Windows 下的 `.txt` 常见 GBK 编码，直接按 UTF-8 读会抛异常或乱码。
2. **线程池**：解析全是同步阻塞调用，PDF 还是 CPU 密集活，必须在
   `asyncio.to_thread` 里跑，否则会卡住整个事件循环（CLAUDE.md §11）。
3. **统一出口**：三类格式的差异、以及各自的失败模式，都收敛在这一个文件里。

⚠️ 已知边界：**扫描件 PDF（只有图片、没有文字层）解析出来为空**。本层会把这种情况
翻译成一条说明原因的错误，而不是返回空字符串让上层以为「文档本来就是空的」。
"""

import asyncio
import logging
from pathlib import Path

from app.core.exceptions import AppError
from app.schemas.common import ErrorCode

logger = logging.getLogger(__name__)

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv"}
PDF_SUFFIXES = {".pdf"}
DOCX_SUFFIXES = {".docx"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | PDF_SUFFIXES | DOCX_SUFFIXES

# 按「可能性从高到低」尝试。gb18030 是 GBK 的超集，放最后兜底。
TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "gbk", "gb18030")


class UnsupportedFileTypeError(AppError):
    def __init__(self, suffix: str) -> None:
        supported = "、".join(sorted(SUPPORTED_SUFFIXES))
        super().__init__(
            f"不支持的文件类型「{suffix or '无后缀'}」，目前支持：{supported}",
            code=ErrorCode.PARAM_ERROR,
            http_status=415,
        )


class DocumentParseError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(
            f"文档解析失败：{message}",
            code=ErrorCode.BIZ_ERROR,
            http_status=422,
        )


def _load_text_file(path: Path) -> str:
    """纯文本读取，带编码回退。

    逐个候选编码尝试，全都失败时按 UTF-8 忽略非法字节——即少数乱码胜于整个文件读不出来。
    """
    for encoding in TEXT_ENCODINGS:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    logger.warning("文本文件按 %s 均解码失败，改为 UTF-8 并忽略非法字节", "/".join(TEXT_ENCODINGS))
    return path.read_text(encoding="utf-8", errors="replace")


def _load_pdf(path: Path) -> str:
    """用 pypdf 逐页提取文本。

    三个刻意处理的点：

    1. **加密 PDF**：很多企业 PDF 只是「限制编辑」，用空密码就能解开，这类不该报错。
       只有真的需要密码时才给出明确提示——否则用户只会看到一句莫名的解析失败。
    2. **单页提取失败不整体失败**：多页文档里偶有一页结构异常很常见，
       放弃整篇文档的代价远大于丢掉一页。
    3. **页间用空行分隔**：给切分器制造天然的断点，避免跨页的句子被粘在一起。
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))

    if reader.is_encrypted:
        # decrypt 返回 0 表示密码不对。先试空密码——能解开就继续，解不开才是真加密。
        if reader.decrypt("") == 0:
            raise DocumentParseError(
                f"{path.name}：这份 PDF 已加密，需要密码才能读取。"
                "请先去掉密码保护再上传。"
            )

    try:
        page_count = len(reader.pages)
    except Exception as exc:  # noqa: BLE001 —— 损坏文件的报错类型不稳定
        raise DocumentParseError(
            f"{path.name}：PDF 结构损坏，无法读取页面列表（{type(exc).__name__}: {exc}）"
        ) from exc

    if page_count == 0:
        raise DocumentParseError(f"{path.name}：这份 PDF 里没有任何页面。")

    parts: list[str] = []
    failed_pages = 0
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001
            failed_pages += 1
            logger.warning("PDF %s 有一页提取失败，已跳过：%s: %s", path.name, type(exc).__name__, exc)

    if failed_pages:
        logger.warning("PDF %s 共 %s 页，其中 %s 页提取失败", path.name, page_count, failed_pages)

    return "\n\n".join(parts)


def _load_docx(path: Path) -> str:
    """用 docx2txt 提取 Word 正文（含表格文字，不含图片）。

    docx2txt 对损坏/伪装文件的异常类型不固定（BadZipFile、KeyError、Exception 都有），
    因此不逐个 catch，交给上层的统一兜底转成 DocumentParseError。
    """
    import docx2txt

    text = docx2txt.process(str(path))
    return text or ""


def _extract_sync(path: Path, suffix: str) -> str:
    if suffix in TEXT_SUFFIXES:
        return _load_text_file(path)
    if suffix in PDF_SUFFIXES:
        return _load_pdf(path)
    if suffix in DOCX_SUFFIXES:
        return _load_docx(path)
    raise UnsupportedFileTypeError(suffix)


async def extract_text(path: Path, filename: str) -> str:
    """把磁盘上的文件解析成纯文本。失败时抛 AppError 的子类（带明确原因）。"""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedFileTypeError(suffix)

    try:
        # §11：解析是同步阻塞调用，PDF 更是 CPU 密集，必须丢线程池
        text = await asyncio.to_thread(_extract_sync, path, suffix)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 第三方解析库的异常类型不稳定，统一转成解析错误
        raise DocumentParseError(f"{filename}：{type(exc).__name__}: {exc}") from exc

    if not text.strip():
        raise DocumentParseError(
            f"{filename}：未提取到任何文字。"
            "最常见的两个原因：① 扫描件 PDF 只有图片、没有文字层，需要先做 OCR；"
            "② 文件本身是空的。"
        )
    return text
