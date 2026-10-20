"""RAG 能力层（L3）的单元测试：文档解析 + 向量化器装配。

刻意与 `test_knowledge.py` 分开：那个文件测的是**入库全链路**（HTTP → 服务 → 库），
这个文件测的是**两个适配层自身的分支**——解析失败的几种形态、
模型名/维度/前缀这些「配置写错」的判定。

**全部用例都是离线的**（不联网、不下载模型）。真实模型是否真的「有语义」属于
需要外部资源的验证，放在 `scripts/check_embedding.py` 里，与 `check_weather_api.py`
同一套思路：能被离线测试覆盖的逻辑才写进 pytest，依赖外部资源的独立成脚本。

测试样本的构造方式值得一提（都不需要引入新依赖）：
- **PDF**：用 `pypdf.PdfWriter` 现造，往页面里塞一个内容流。
  只能写 ASCII 文本——Helvetica 字体不含中文字形，写中文需要嵌入字体，不值得为测试引入。
  中文场景由 DOCX 用例覆盖。
- **DOCX**：`.docx` 本质是一个 zip + 一段 OOXML，用 `zipfile` 手工拼最小结构即可。
"""

import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.core.config import Settings, settings
from app.rag import loader as loader_module
from app.rag.embedder import (
    FastembedEmbedder,
    PlaceholderEmbedder,
    UnsupportedEmbeddingProviderError,
    get_embedder,
    reset_embedder,
)
from app.services import knowledge_service

# --- 测试样本构造 ------------------------------------------------------


def _make_pdf(path: Path, text: str | None) -> Path:
    """造一个真实可解析的 PDF。text 为 None 时造一个没有文字层的空白页。"""
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    if text is not None:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 14 Tf 20 200 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(str(path))
    return path


def _make_encrypted_pdf(path: Path, password: str) -> Path:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt(password)
    writer.write(str(path))
    return path


_DOCX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/></Types>'
)
_DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
)


def _make_docx(path: Path, paragraphs: list[str]) -> Path:
    """用 zip + 最小 OOXML 造一个 docx2txt 能读的 Word 文件（无需 python-docx）。"""
    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        archive.writestr("_rels/.rels", _DOCX_RELS)
        archive.writestr("word/document.xml", document)
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"/>',
        )
    return path


async def _extract(path: Path) -> str:
    return await loader_module.extract_text(path, path.name)


# --- 文档解析：成功路径 -------------------------------------------------


async def test_pdf_text_is_extracted(tmp_path):
    """真实 PDF 的文字层必须能被提取出来（不是只把文件读成字节流）。"""
    pdf = _make_pdf(tmp_path / "制度.pdf", "Travel policy: hotel cap 600 RMB")
    text = await _extract(pdf)
    assert "Travel policy" in text
    assert "hotel cap 600" in text


async def test_docx_extracts_chinese_text(tmp_path):
    """DOCX 的中文正文提取——PDF 用例覆盖不了中文（字体限制），由这里补上。"""
    docx = _make_docx(
        tmp_path / "差旅制度.docx",
        ["一线城市住宿费每晚上限六百元。", "报销需在十五个工作日内提交。"],
    )
    text = await _extract(docx)
    assert "住宿费每晚上限六百元" in text
    assert "十五个工作日" in text


async def test_gbk_encoded_text_file_is_decoded(tmp_path):
    """中文 Windows 导出的 txt/csv 常见 GBK 编码，只试 UTF-8 会直接失败。"""
    path = tmp_path / "gbk.txt"
    path.write_bytes("住宿标准按城市分级。".encode("gbk"))
    text = await _extract(path)
    assert "住宿标准按城市分级" in text


# --- 文档解析：失败路径（每条都要给出可操作的原因）----------------------


async def test_encrypted_pdf_gives_actionable_error(tmp_path):
    """加密 PDF 不能只说「解析失败」——用户得知道要去掉密码保护。"""
    pdf = _make_encrypted_pdf(tmp_path / "加密.pdf", "secret")
    with pytest.raises(loader_module.DocumentParseError) as exc:
        await _extract(pdf)
    assert "加密" in str(exc.value)
    assert "密码" in str(exc.value)


async def test_pdf_without_text_layer_mentions_ocr(tmp_path):
    """扫描件 PDF 只有图片没有文字层，必须提示 OCR，而不是返回空字符串让上层以为文档是空的。"""
    pdf = _make_pdf(tmp_path / "扫描件.pdf", text=None)
    with pytest.raises(loader_module.DocumentParseError) as exc:
        await _extract(pdf)
    message = str(exc.value)
    assert "OCR" in message or "文字层" in message


async def test_broken_pdf_is_reported_as_parse_error(tmp_path):
    """伪装成 PDF 的垃圾文件：不能抛出未包装的第三方异常。"""
    broken = tmp_path / "假的.pdf"
    broken.write_bytes(b"this is definitely not a pdf")
    with pytest.raises(loader_module.DocumentParseError):
        await _extract(broken)


async def test_broken_docx_is_reported_as_parse_error(tmp_path):
    broken = tmp_path / "假的.docx"
    broken.write_bytes(b"not a zip archive at all")
    with pytest.raises(loader_module.DocumentParseError):
        await _extract(broken)


async def test_unsupported_suffix_lists_what_is_supported(tmp_path):
    """报错要告诉用户「那支持什么」，否则只能去翻文档。"""
    path = tmp_path / "x.exe"
    path.write_bytes(b"MZ")
    with pytest.raises(loader_module.UnsupportedFileTypeError) as exc:
        await _extract(path)
    message = str(exc.value)
    assert ".pdf" in message and ".docx" in message and ".txt" in message


def test_supported_suffixes_are_stable():
    """后缀集合是入库层的准入判据（knowledge_service 也读它），改动必须被测试拦一下。"""
    assert loader_module.SUPPORTED_SUFFIXES == {".txt", ".md", ".markdown", ".csv", ".pdf", ".docx"}


# --- 向量化器装配（离线部分）-------------------------------------------


def test_dimension_is_read_from_model_metadata_not_hardcoded():
    """维度必须来自 fastembed 自带的模型元数据——硬编码 512 在换模型时会悄悄出错。"""
    embedder = FastembedEmbedder(model_name="BAAI/bge-small-zh-v1.5", dimension=None)
    assert embedder.dimension == 512
    assert embedder.is_placeholder is False
    assert embedder.name == "fastembed:BAAI/bge-small-zh-v1.5"


def test_unknown_model_error_lists_chinese_candidates():
    """模型名写错时给出可用候选，而不是抛 KeyError 让人去翻文档。"""
    from app.rag.embedder import EmbeddingModelError

    with pytest.raises(EmbeddingModelError) as exc:
        FastembedEmbedder(model_name="BAAI/不存在的模型", dimension=512)
    assert "bge-small-zh-v1.5" in str(exc.value)


def test_query_instruction_is_auto_enabled_for_bge_zh(monkeypatch):
    """bge-zh 系列按模型名自动加检索指令前缀（仅作用于查询）。"""
    monkeypatch.setattr(settings, "embedding_query_instruction", None)
    embedder = FastembedEmbedder(model_name="BAAI/bge-small-zh-v1.5", dimension=512)
    assert embedder.query_instruction == "为这个句子生成表示以用于检索相关文章："


def test_query_instruction_can_be_forced_off(monkeypatch):
    """显式设为空字符串 = 强制不加，用于在自己的真实语料上做 A/B。"""
    monkeypatch.setattr(settings, "embedding_query_instruction", "")
    embedder = FastembedEmbedder(model_name="BAAI/bge-small-zh-v1.5", dimension=512)
    assert embedder.query_instruction == ""


def test_query_instruction_can_be_overridden(monkeypatch):
    monkeypatch.setattr(settings, "embedding_query_instruction", "检索：")
    embedder = FastembedEmbedder(model_name="BAAI/bge-small-zh-v1.5", dimension=512)
    assert embedder.query_instruction == "检索："


def test_model_without_known_instruction_gets_none(monkeypatch):
    """没登记过的模型不加前缀——乱加前缀比不加更糟。"""
    monkeypatch.setattr(settings, "embedding_query_instruction", None)
    embedder = FastembedEmbedder(model_name="BAAI/bge-small-en-v1.5", dimension=384)
    assert embedder.query_instruction == ""


def test_dimension_mismatch_with_config_fails_loudly(monkeypatch):
    """配置里的维度与模型对不上时必须报错。

    放过它的后果是「配置写着 256、实际存的是 512」，或者更糟——换了模型却忘了
    重建索引，新老向量混在同一个集合里，检索结果毫无意义却看不出问题。
    """
    from app.rag.embedder import EmbeddingModelError

    monkeypatch.setattr(settings, "embedding_provider", "fastembed")
    monkeypatch.setattr(settings, "embedding_dim", 256)
    reset_embedder()
    try:
        with pytest.raises(EmbeddingModelError) as exc:
            get_embedder()
        message = str(exc.value)
        assert "512" in message and "EMBEDDING_DIM" in message
        assert "reindex" in message
    finally:
        reset_embedder()


def test_unsupported_provider_rejected(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "openai")
    reset_embedder()
    try:
        with pytest.raises(UnsupportedEmbeddingProviderError):
            get_embedder()
    finally:
        reset_embedder()


def test_unsupported_provider_is_rejected_by_config_validation_too():
    """白名单同时存在于配置校验里——写错的值应该在**应用启动时**就被拦下，而不是等第一次上传。"""
    with pytest.raises(ValidationError) as exc:
        Settings(secret_key="a" * 40, embedding_provider="openai")
    assert "EMBEDDING_PROVIDER" in str(exc.value)


def test_placeholder_still_available_for_offline_use(monkeypatch):
    """占位实现要保留：测试与离线冒烟需要一个不依赖网络与模型文件的替身。"""
    monkeypatch.setattr(settings, "embedding_provider", "placeholder")
    monkeypatch.setattr(settings, "embedding_dim", 128)
    reset_embedder()
    try:
        embedder = get_embedder()
        assert isinstance(embedder, PlaceholderEmbedder)
        assert embedder.is_placeholder is True
        assert embedder.dimension == 128
    finally:
        reset_embedder()


# --- 解析层与入库层的接口约定 -------------------------------------------


def test_knowledge_service_reuses_loader_suffix_set():
    """入库层判断后缀时读的是 loader 的集合，不是自己抄一份——避免两处判定不一致。"""
    assert knowledge_service.loader_module.SUPPORTED_SUFFIXES is loader_module.SUPPORTED_SUFFIXES


def test_loader_no_longer_depends_on_langchain():
    """D-M4-2 决策的护栏：解析层不得再引入 langchain-community。

    langchain-community 已 sunset 且不再接受任何修复（含安全漏洞），
    若有人改回去，这条测试会立刻失败并指向决策记录。范围只看 rag 层：
    切分器仍在用 langchain-text-splitters，那是**单独拆出、未被 sunset** 的包。
    """
    for module in (loader_module,):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "langchain_community" not in source, f"{module.__name__} 又引入了已 sunset 的包"
        assert "from langchain" not in source, f"{module.__name__} 又引入了 langchain"


def test_rag_layer_imports_only_expected_langchain_package():
    """整个 rag 包里允许出现的 langchain 依赖只有拆分出来的切分器包。"""
    rag_dir = Path(loader_module.__file__).parent
    offenders = []
    for path in rag_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "langchain_community" in source or "langchain_classic" in source:
            offenders.append(path.name)
    assert not offenders, f"以下文件引入了已 sunset 的 langchain 包：{offenders}"
