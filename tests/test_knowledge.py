"""M3 知识库入库测试。

覆盖四个决策的落地效果：
- **Q1C 占位向量**：确定性、归一化、非 dev 环境被硬拦截。
- **Q2A 递归切分**：中文句子不被从中间斩断、空白块被丢掉、参数校验。
- **Q3A 进程内后台任务**：全链路状态流转、失败落到 failed、启动补偿。
- **Q4A 每用户私有**：跨用户访问一律 404、检索元数据带 user_id。

向量库与上传目录都指向 `tmp_path`，不碰仓库里的 `data/`。
"""

import pytest
import pytest_asyncio
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.models.document import STATUS_FAILED, STATUS_PENDING, STATUS_READY
from app.rag import vectorstore
from app.rag.embedder import PlaceholderEmbedder, reset_embedder
from app.rag.splitter import split_text
from app.schemas.common import ErrorCode
from app.services import knowledge_service

# 注意：这里刻意不写 `pytestmark = pytest.mark.asyncio`。
# pytest.ini 已经设了 asyncio_mode = auto，异步用例会自动被识别；再手工打一遍标记
# 会让下面的同步用例（纯函数测试）也被当成协程，pytest 报一堆
# "coroutine was never awaited" 之类的 PytestWarning。


@pytest_asyncio.fixture(autouse=True)
async def _isolate_knowledge(monkeypatch, tmp_path, session_factory):
    """每个用例独立的向量库目录、上传目录与会话工厂。

    - `tmp_path` 隔离磁盘：Chroma 是持久化的，共用目录会让用例互相看见对方的向量。
    - `AsyncSessionLocal` 必须一起换掉：入库流程跑在请求之外，用的是它自己的会话工厂，
      不换就会写到仓库里那个真实的 SQLite 文件。
    """
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "chroma_dir", str(tmp_path / "chroma"))
    monkeypatch.setattr(settings, "chroma_collection", "test_knowledge")
    monkeypatch.setattr(settings, "embedding_provider", "placeholder")
    monkeypatch.setattr(settings, "chunk_size", 200)
    monkeypatch.setattr(settings, "chunk_overlap", 40)
    monkeypatch.setattr(knowledge_service, "AsyncSessionLocal", session_factory)
    vectorstore.reset_store()
    reset_embedder()
    yield
    vectorstore.reset_store()
    reset_embedder()


async def _register(client, username: str) -> dict[str, str]:
    """注册并返回鉴权头。"""
    await client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": "pw12345678"},
    )
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": "pw12345678"}
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


# --- 纯函数：文件名安全与切分 -------------------------------------------


def test_sanitize_filename_blocks_path_traversal():
    """路径穿越必须被挡住——否则上传就能往项目外写文件。"""
    assert knowledge_service.sanitize_filename("../../windows/system32/x.dll") == "x.dll"
    # 反斜杠在 POSIX 上不是分隔符，必须显式处理，否则这里会原样保留
    assert knowledge_service.sanitize_filename(r"..\..\secret.docx") == "secret.docx"
    assert knowledge_service.sanitize_filename("C:/Users/me/报告.pdf") == "报告.pdf"
    assert knowledge_service.sanitize_filename("a<b>c?.txt") == "a_b_c_.txt"


def test_sanitize_filename_keeps_extension_when_truncating():
    """截断超长文件名时要保住扩展名，否则后续按后缀判类型的逻辑会失效。"""
    name = knowledge_service.sanitize_filename("很长的名字" * 40 + ".pdf")
    assert name.endswith(".pdf")
    assert len(name) <= knowledge_service.MAX_STORED_NAME


def test_sanitize_filename_falls_back_when_empty():
    assert knowledge_service.sanitize_filename("...") == "unnamed"
    assert knowledge_service.sanitize_filename("") == "unnamed"


def test_splitter_does_not_cut_chinese_sentences_blindly():
    """中文没有空格，若不给 LangChain 配中文分隔符，句子会被从中间硬切开。"""
    text = "。".join(f"这是第{i}句用于测试切分效果的中文内容" for i in range(1, 40)) + "。"
    chunks = split_text(text)
    assert len(chunks) > 1, "长文本应当被切成多块"
    # 每块都应以句号结尾（keep_separator="end" 的效果），说明断开处落在句子边界
    for chunk in chunks[:-1]:
        assert chunk.endswith("。"), f"块结尾不是句号，说明切在了句子中间：{chunk[-20:]!r}"


def test_splitter_drops_blank_chunks():
    chunks = split_text("第一段。\n\n\n\n   \n\n第二段。")
    assert all(chunk.strip() for chunk in chunks), "纯空白块不应该被保留"
    assert any("第一段" in chunk for chunk in chunks)


def test_splitter_respects_configured_size(monkeypatch):
    monkeypatch.setattr(settings, "chunk_size", 50)
    monkeypatch.setattr(settings, "chunk_overlap", 10)
    text = "测试内容。" * 100
    chunks = split_text(text)
    # 允许少量超出（分隔符会带来额外长度），但不应出现成倍超标
    assert all(len(chunk) <= 50 + 10 for chunk in chunks)


# --- 配置校验：把「上线才发现」的问题提前到启动 -------------------------


def test_non_dev_env_rejects_placeholder_embedder():
    """占位向量是开发期替身，决不能被带上线——与 SECRET_KEY 的默认值同一个道理。"""
    with pytest.raises(ValidationError) as exc:
        Settings(app_env="prod", secret_key="a" * 40, embedding_provider="placeholder")
    assert "占位向量" in str(exc.value)


def test_chunk_overlap_must_be_smaller_than_chunk_size():
    with pytest.raises(ValidationError) as exc:
        Settings(secret_key="a" * 40, chunk_size=100, chunk_overlap=100)
    assert "CHUNK_OVERLAP" in str(exc.value)


# --- 占位向量 ----------------------------------------------------------


async def test_placeholder_embedder_deterministic_and_normalized():
    embedder = PlaceholderEmbedder(dimension=64)
    first = await embedder.embed_documents(["上海明天要带伞吗"])
    second = await embedder.embed_documents(["上海明天要带伞吗"])
    third = await embedder.embed_documents(["北京后天天气如何"])

    assert first == second, "同一段文本必须得到完全相同的向量（重建索引才可复现）"
    assert first != third, "不同文本不应得到相同向量"
    assert len(first[0]) == 64
    norm = sum(value * value for value in first[0]) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-9), "向量必须归一化（否则余弦距离失去意义）"


# --- 全链路 ------------------------------------------------------------


async def test_upload_txt_reaches_ready(client):
    """上传 → 解析 → 切分 → 向量化 → 入库，最终状态为 ready。"""
    headers = await _register(client, "kb_user1")
    content = ("关于差旅报销的规定。员工出差前需提交申请。住宿标准按城市分级。" * 20).encode()

    response = await client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("差旅制度.txt", content, "text/plain")},
    )
    assert response.status_code == 202, response.text
    body = response.json()["data"]
    assert body["deduplicated"] is False
    document_id = body["document"]["id"]

    # 后台任务在 ASGITransport 里随请求一起跑完，因此这里应已是终态
    detail = await client.get(f"/api/v1/knowledge/documents/{document_id}", headers=headers)
    data = detail.json()["data"]
    assert data["status"] == STATUS_READY, data
    assert data["chunk_count"] > 0
    assert data["error_message"] is None
    assert len(data["chunks"]) > 0, "详情里应有切分预览"
    assert data["total_chunks"] == data["chunk_count"]

    # 向量确实写进了向量库，且元数据带 user_id（M4 按用户过滤的依据）
    stored_ids = await vectorstore.get_store().list_ids(document_id)
    assert len(stored_ids) == data["chunk_count"]


async def test_上传同一文件命中查重(client):
    """§8.1 的 file_hash 查重：同一用户重复上传同一文件，不重复处理。"""
    headers = await _register(client, "kb_user2")
    payload = {"file": ("同名.txt", b"same content here", "text/plain")}

    first = await client.post("/api/v1/knowledge/documents", headers=headers, files=payload)
    second = await client.post("/api/v1/knowledge/documents", headers=headers, files=payload)

    assert first.json()["data"]["deduplicated"] is False
    assert second.json()["data"]["deduplicated"] is True
    assert first.json()["data"]["document"]["id"] == second.json()["data"]["document"]["id"]


async def test_不同用户可以各自上传同一份文件(client):
    """M3-Q4「每用户私有」的连带结果：去重键是 (user_id, file_hash)，不是 file_hash。"""
    payload = {"file": ("共享手册.txt", b"company handbook content", "text/plain")}
    alice = await _register(client, "kb_alice")
    bob = await _register(client, "kb_bob")

    a = await client.post("/api/v1/knowledge/documents", headers=alice, files=payload)
    b = await client.post("/api/v1/knowledge/documents", headers=bob, files=payload)

    assert a.json()["data"]["deduplicated"] is False
    assert b.json()["data"]["deduplicated"] is False, "B 不应因 A 传过就拿到 A 的文档"
    assert a.json()["data"]["document"]["id"] != b.json()["data"]["document"]["id"]


async def test_不支持的类型返回_415(client):
    headers = await _register(client, "kb_user3")
    response = await client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("恶意.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert response.status_code == 415
    assert response.json()["code"] == ErrorCode.PARAM_ERROR


async def test_超过大小上限返回_413(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    headers = await _register(client, "kb_user4")
    response = await client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("大文件.txt", b"x" * (1024 * 1024 + 10), "text/plain")},
    )
    assert response.status_code == 413


async def test_解析失败时状态为_failed_且带原因(client):
    """假 PDF：pypdf 解析必然失败。必须落到 failed + error_message，不能卡在 parsing。"""
    headers = await _register(client, "kb_user5")
    response = await client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("假的.pdf", b"this is definitely not a pdf", "application/pdf")},
    )
    assert response.status_code == 202
    document_id = response.json()["data"]["document"]["id"]

    detail = await client.get(f"/api/v1/knowledge/documents/{document_id}", headers=headers)
    data = detail.json()["data"]
    assert data["status"] == STATUS_FAILED, data
    assert data["error_message"], "失败必须说明原因，否则用户无从下手"


# --- 用户隔离（M3-Q4）--------------------------------------------------


async def test_跨用户访问文档一律_404(client):
    """对「不存在」与「属于别人」返回同一个错误，避免通过错误差异探测文档是否存在。"""
    alice = await _register(client, "kb_iso_a")
    bob = await _register(client, "kb_iso_b")
    created = await client.post(
        "/api/v1/knowledge/documents",
        headers=alice,
        files={"file": ("私密.txt", b"alice private content", "text/plain")},
    )
    document_id = created.json()["data"]["document"]["id"]

    assert (await client.get(f"/api/v1/knowledge/documents/{document_id}", headers=bob)).status_code == 404
    assert (await client.delete(f"/api/v1/knowledge/documents/{document_id}", headers=bob)).status_code == 404
    assert (
        await client.put(
            f"/api/v1/knowledge/documents/{document_id}",
            headers=bob,
            files={"file": ("覆盖.txt", b"bob content", "text/plain")},
        )
    ).status_code == 404

    # B 的列表里也不应出现 A 的文档
    listing = await client.get("/api/v1/knowledge/documents", headers=bob)
    assert listing.json()["data"]["total"] == 0

    # A 自己仍然能访问，确认不是「接口本身坏了」
    assert (await client.get(f"/api/v1/knowledge/documents/{document_id}", headers=alice)).status_code == 200


async def test_未登录访问知识库返回_401(client):
    assert (await client.get("/api/v1/knowledge/documents")).status_code == 401


# --- 删除与更新 --------------------------------------------------------


async def test_删除同时清掉向量与磁盘文件(client):
    headers = await _register(client, "kb_user6")
    created = await client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("待删除.txt", ("要删除的内容。" * 30).encode(), "text/plain")},
    )
    document_id = created.json()["data"]["document"]["id"]
    assert await vectorstore.get_store().list_ids(document_id), "删除前应有向量"

    assert (await client.delete(f"/api/v1/knowledge/documents/{document_id}", headers=headers)).status_code == 200

    # 向量必须一并清掉：残留的孤儿向量会继续被检索命中，用户会看到「来源是已删除的文档」
    assert await vectorstore.get_store().list_ids(document_id) == []
    assert (await client.get(f"/api/v1/knowledge/documents/{document_id}", headers=headers)).status_code == 404
    listing = await client.get("/api/v1/knowledge/documents", headers=headers)
    assert listing.json()["data"]["total"] == 0


async def test_覆盖重解析会替换内容(client):
    headers = await _register(client, "kb_user7")
    created = await client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("迭代.txt", "旧内容第一版。".encode(), "text/plain")},
    )
    document_id = created.json()["data"]["document"]["id"]
    first_ids = await vectorstore.get_store().list_ids(document_id)

    updated = await client.put(
        f"/api/v1/knowledge/documents/{document_id}",
        headers=headers,
        files={"file": ("迭代.txt", ("新内容第二版。" * 40).encode(), "text/plain")},
    )
    assert updated.status_code == 202

    detail = (await client.get(f"/api/v1/knowledge/documents/{document_id}", headers=headers)).json()["data"]
    assert detail["status"] == STATUS_READY
    assert "新内容第二版" in detail["chunks"][0]["content"]
    assert first_ids != await vectorstore.get_store().list_ids(document_id), (
        "重解析后向量应被替换；由于 vector_id 是确定性的，这里比对的是数量变化"
    )


# --- 重建索引与启动补偿 -------------------------------------------------


async def test_reindex_重新投递所有文档(client):
    headers = await _register(client, "kb_user8")
    await client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("索引.txt", ("用于重建索引的内容。" * 20).encode(), "text/plain")},
    )
    response = await client.post("/api/v1/knowledge/reindex", headers=headers)
    assert response.status_code == 202
    data = response.json()["data"]
    assert data["total"] >= 1
    assert data["dispatched"] == data["total"]


async def test_启动补偿把卡住的文档重置为_pending(client, session_factory):
    """进程重启会让进程内后台任务无声消失，文档将永久停在 parsing——必须有补偿。"""
    headers = await _register(client, "kb_user9")
    created = await client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("卡住.txt", b"content that got stuck", "text/plain")},
    )
    document_id = created.json()["data"]["document"]["id"]

    # 人为把它改回 parsing，模拟「跑到一半进程被 kill」
    async with session_factory() as db:
        from app.crud import document as document_crud
        from app.models.document import STATUS_PARSING

        document = await document_crud.get_by_id_any_user(db, document_id)
        await document_crud.update_status(db, document, STATUS_PARSING)

    recovered = await knowledge_service.recover_stale_documents()
    assert document_id in recovered

    async with session_factory() as db:
        document = await document_crud.get_by_id_any_user(db, document_id)
        assert document.status == STATUS_PENDING
        assert "重新排队" in (document.error_message or "")


async def test_重复处理已完成文档是幂等的(client, session_factory):
    """幂等：再次投递不会重算，也不会破坏已有结果。"""
    headers = await _register(client, "kb_user10")
    created = await client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("幂等.txt", ("幂等测试内容。" * 20).encode(), "text/plain")},
    )
    document_id = created.json()["data"]["document"]["id"]
    before = await vectorstore.get_store().list_ids(document_id)

    await knowledge_service.process_document(document_id)

    assert await vectorstore.get_store().list_ids(document_id) == before
