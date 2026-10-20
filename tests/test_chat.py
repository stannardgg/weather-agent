"""M4 检索问答测试。

覆盖本轮四个决策的落地效果：

- **Q1B 意图路由 + 分支节点**：四条分支各走对；短路时**模型一次都不被调用**。
- **Q2A langchain-openai 网关**：模型可被替身替换，token 事件能逐段捕获，
  异常翻译把「模型名写错」与「密钥失效」区分开。
- **Q3A 纯向量 top_k + 距离阈值**：阈值内外、`user_id` 隔离、query 必须带 where。
- **Q4C 规则优先 + LLM 兜底**：规则命中/未命中、LLM 失败退回 chat。

## 离线性与它的边界

全程用 `GenericFakeChatModel` 替身 + 占位向量，**不联网、不消耗任何额度**。
这正是把模型做成可替换接缝的意义：让「结构对不对」与「真实效果好不好」分开验证。

⚠️ 必须说清楚它**证明不了**什么：假模型按脚本输出，不产生任何语义。所以这些用例
证明的是「prompt 组装正确、路由走对、短路生效、token 逐段流出、落库完整」，
**不证明「模型答得准」**。后者必须有真实密钥才能验证（见 `llm/gateway.py` 的说明）。
"""

from datetime import datetime

import pytest
import pytest_asyncio
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import PrivateAttr
from sqlalchemy import update as sa_update

from app.agent import extract, graph as graph_module, nodes, router
from app.agent.state import Intent, IntentSource
from app.core.config import settings
from app.crud import conversation as conversation_crud
from app.crud import document as document_crud
from app.crud import user as user_crud
from app.llm import gateway
from app.llm.prompts import NO_KNOWLEDGE_ANSWER, WEATHER_UNAVAILABLE_ANSWER
from app.models.conversation import Conversation
from app.rag import retriever as retriever_module
from app.rag import vectorstore
from app.rag.embedder import get_embedder, reset_embedder
from app.rag.retriever import RetrievedChunk
from app.rag.vectorstore import ChromaStore, VectorStoreError
from app.services import chat_service
from app.tools.weather import CityNotFoundError


class ScriptedModel(GenericFakeChatModel):
    """按脚本吐字的假模型，并**记录被调用的次数**。

    记录次数是这个类存在的唯一理由：M4 最重要的保证是「检索为空时绝不调用模型」，
    而这条保证只能通过「模型确实没被调用」来证明，不能靠「答案看着像固定文案」——
    后者在模型刚好输出了同样内容时会误判。

    `_inside_stream` 这个标志是必需的：`GenericFakeChatModel._stream()` 内部会**回调
    `self._generate()`**（它先取整条消息再切成 token）。不区分的话，一次流式调用会被
    记成一次「invoke + stream」，看起来像是多调了一次模型，从而把断言写歪。
    这个嵌套只是假模型的实现细节，真实 `ChatOpenAI` 不会这样。
    """

    calls: list[str] = []
    _inside_stream: bool = PrivateAttr(default=False)

    def _generate(self, *args, **kwargs):
        if not self._inside_stream:
            self.calls.append("invoke")
        return super()._generate(*args, **kwargs)

    def _stream(self, *args, **kwargs):
        self._inside_stream = True
        self.calls.append("stream")
        try:
            yield from super()._stream(*args, **kwargs)
        finally:
            self._inside_stream = False


def install_model(*contents: str) -> ScriptedModel:
    """装上一个按顺序吐 `contents` 的假模型。每次调用消费一条。"""
    model = ScriptedModel(messages=iter([AIMessage(content=text) for text in contents]))
    gateway.set_chat_model_override(model)
    return model


@pytest_asyncio.fixture(autouse=True)
async def _isolate_chat(monkeypatch, tmp_path, session_factory):
    """每个用例独立的向量库目录与会话工厂，并清掉模型替身。

    `AsyncSessionLocal` 必须换掉：流式问答的生成器跑在请求之外，用的是它自己的会话工厂，
    不换就会写到仓库里那个真实的 SQLite 文件（与 `test_knowledge.py` 同一个理由）。
    """
    monkeypatch.setattr(settings, "chroma_dir", str(tmp_path / "chroma"))
    monkeypatch.setattr(settings, "chroma_collection", "test_chat")
    monkeypatch.setattr(settings, "embedding_provider", "placeholder")
    monkeypatch.setattr(settings, "embedding_dim", 64)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(chat_service, "AsyncSessionLocal", session_factory)

    # ⚠️ 天气必须默认离线。`.env` 里配的是**真实凭据**，不拦住的话凡是命中天气意图的
    # 用例都会真的去打和风的接口——测试会变慢、依赖网络，还会消耗免费额度。
    # 需要真实天气数据的用例自己覆盖它。
    async def _offline_weather(city, date=None):
        raise CityNotFoundError(f"测试环境不联网：{city}")

    monkeypatch.setattr(nodes, "get_weather", _offline_weather)

    vectorstore.reset_store()
    reset_embedder()
    gateway.set_chat_model_override(None)
    yield
    vectorstore.reset_store()
    reset_embedder()
    gateway.set_chat_model_override(None)


async def register(client, username: str) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": "pw12345678"},
    )
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": "pw12345678"}
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def make_user(db, username: str):
    return await user_crud.create(
        db, username=username, email=f"{username}@example.com", hashed_password="x" * 20
    )


async def index_chunk(db, *, user_id: int, filename: str, content: str, hash_seed: str) -> None:
    """往关系库与向量库各写一条片段，供检索用例使用。"""
    document = await document_crud.create(
        db,
        user_id=user_id,
        filename=filename,
        file_hash=hash_seed * 8,
        stored_path=f"/tmp/{filename}",
        content_type="text/plain",
        size=len(content),
    )
    rows = await document_crud.add_chunks(db, document_id=document.id, texts=[content])
    vector = (await get_embedder().embed_documents([content]))[0]
    await vectorstore.get_store().add(
        ids=[row.vector_id for row in rows],
        embeddings=[vector],
        metadatas=[
            {
                "document_id": document.id,
                "user_id": user_id,
                "chunk_index": 0,
                "source": filename,
            }
            for row in rows
        ],
    )


# ---------------------------------------------------------------------------
# Q4C：意图规则（纯函数）
# ---------------------------------------------------------------------------


def test_weather_question_routes_to_weather_by_rule():
    intent, weather_hits, knowledge_hits = router.classify_by_rules("明天上海要带伞吗")
    assert intent == Intent.WEATHER
    assert "带伞" in weather_hits
    assert knowledge_hits == []


def test_document_question_routes_to_knowledge_by_rule():
    intent, _, knowledge_hits = router.classify_by_rules("我们的应急预案里橙色预警对应什么处置流程")
    assert intent == Intent.KNOWLEDGE
    assert "预案" in knowledge_hits


def test_both_sides_hit_means_mixed():
    """需求 C3 的典型问法：台风（天气）+ 预案/响应（制度）。"""
    intent, weather_hits, knowledge_hits = router.classify_by_rules(
        "按公司台风预案，明天深圳该启动几级响应？"
    )
    assert intent == Intent.MIXED
    assert weather_hits and knowledge_hits


def test_warning_word_alone_does_not_bias_either_side():
    """「预警」刻意不在任一侧词表里：它在两种语境下都常见，放哪都会把另一侧判错。"""
    assert "预警" not in router.WEATHER_KEYWORDS
    assert "预警" not in router.KNOWLEDGE_KEYWORDS


async def test_greeting_is_chat_without_touching_the_model():
    intent, source = await router.classify("你好")
    assert intent == Intent.CHAT
    assert source == IntentSource.RULE


def test_greeting_with_business_word_is_not_treated_as_greeting():
    """「你好，明天上海要带伞吗」是天气问题，不能因为以「你好」开头就判成闲聊。"""
    result = router.classify_by_rules("你好，明天上海要带伞吗")
    assert result is not None
    assert result[0] == Intent.WEATHER


def test_unmatched_question_defers_to_llm():
    """规则判不出来时返回 None，表示「该交给 LLM 了」（Q4C 的兜底路径）。"""
    assert router.classify_by_rules("帮我看看这个怎么弄") is None


def test_no_single_char_keywords_in_tables():
    """回归护栏：单字词（如「风」「雨」）会让「风险」「风口」被判成天气问题。"""
    for table in (router.WEATHER_KEYWORDS, router.KNOWLEDGE_KEYWORDS):
        for word in table:
            assert len(word) >= 2, f"关键词「{word}」太短，容易误伤"


async def test_llm_fallback_parses_loose_output():
    """模型常会带上「分类：」或句号，解析要能容错，否则兜底路径形同虚设。"""
    install_model("分类：weather。")
    intent, source = await router.classify("帮我看看这个怎么弄")
    assert intent == Intent.WEATHER
    assert source == IntentSource.LLM


async def test_llm_failure_falls_back_to_chat(monkeypatch):
    """密钥没配 / 限流 / 超时都不该让一次问答直接失败——退回 chat 仍能正常回答。

    显式把 key 置空而不是依赖 `.env` 里恰好没填：否则用户一填上真实密钥，
    这条用例就会去打真实网络（变成一条「偶尔很慢、偶尔烧额度」的测试）。
    """
    monkeypatch.setattr(settings, "llm_api_key", "")
    gateway.set_chat_model_override(None)
    gateway.reset_chat_model()

    intent, source = await router.classify("帮我看看这个怎么弄")
    assert intent == Intent.CHAT
    assert source == IntentSource.FALLBACK

    gateway.reset_chat_model()


# ---------------------------------------------------------------------------
# 城市与日期抽取（纯函数）
# ---------------------------------------------------------------------------


def test_extract_relative_dates():
    from datetime import date

    base = date(2026, 10, 12)  # 周一
    assert extract.extract_date("今天的天气", today=base) == "2026-10-12"
    assert extract.extract_date("明天会下雨吗", today=base) == "2026-10-13"
    assert extract.extract_date("后天呢", today=base) == "2026-10-14"
    # 「大后天」必须先于「后天」匹配，否则会被截成「后天」而差一天
    assert extract.extract_date("大后天呢", today=base) == "2026-10-15"


def test_extract_explicit_dates():
    from datetime import date

    base = date(2026, 10, 12)
    assert extract.extract_date("2026-10-20 上海", today=base) == "2026-10-20"
    assert extract.extract_date("2026年10月21日", today=base) == "2026-10-21"
    assert extract.extract_date("10月25日怎么样", today=base) == "2026-10-25"
    # 非法日期当没抽到，而不是抛错（用户可能只是顺手打了个数字）
    assert extract.extract_date("2026-02-30 的天气", today=base) is None


def test_extract_weekday_uses_next_occurrence():
    from datetime import date

    base = date(2026, 10, 12)  # 周一
    assert extract.extract_date("周三下雨吗", today=base) == "2026-10-14"
    assert extract.extract_date("下周三呢", today=base) == "2026-10-21"
    # 同一星期几：今天问「周一」按今天理解（差 0 天）
    assert extract.extract_date("周一呢", today=base) == "2026-10-12"


def test_extract_city_prefers_whitelist_over_suffix():
    assert extract.extract_city("明天上海要带伞吗") == "上海"
    assert extract.extract_city("深圳市南山区会下雨吗") == "深圳"
    assert extract.extract_city("今天没有任何地名") is None


def test_extract_city_rejects_non_place_like_words():
    """「问题在市里怎么解决」这类句子会命中后缀正则，但含「问」字，必须被拒掉。"""
    assert extract.extract_city("这个问题在市里怎么解决") is None


# ---------------------------------------------------------------------------
# Q3A：检索与阈值
# ---------------------------------------------------------------------------


async def test_vectorstore_query_refuses_to_run_without_where():
    """漏传过滤条件 = 跨用户检索。做成必填参数，漏传直接报错而不是安静搜全库。"""
    store = ChromaStore()
    with pytest.raises(VectorStoreError, match="where"):
        await store.query([0.1] * settings.embedding_dim, top_k=3)


async def test_retriever_isolates_users(session_factory):
    """同一个词在两个用户的知识库里都存在时，检索只能命中自己那份（M3-Q4 在检索侧的兑现）。"""
    async with session_factory() as db:
        alice = await make_user(db, "alice_ret")
        bob = await make_user(db, "bob_ret")
        await index_chunk(
            db, user_id=alice.id, filename="alice.txt", content="住宿标准按城市分级", hash_seed="a"
        )
        await index_chunk(
            db, user_id=bob.id, filename="bob.txt", content="住宿标准按城市分级", hash_seed="b"
        )

        alice_chunks = await retriever_module.retrieve(db, "住宿标准按城市分级", user_id=alice.id)
        assert len(alice_chunks) == 1
        assert alice_chunks[0].source == "alice.txt"
        assert alice_chunks[0].document_id != bob.id


async def test_retriever_applies_score_threshold(session_factory):
    """阈值是「宁可不答」的那道闸：把阈值抬到相似度之上，结果必须为空。"""
    async with session_factory() as db:
        user = await make_user(db, "carol_ret")
        await index_chunk(
            db, user_id=user.id, filename="c.txt", content="台风预案的响应分级", hash_seed="c"
        )

        # 占位向量对完全相同的文本距离为 0（相似度 1），默认阈值 0.45 必然保留
        kept = await retriever_module.retrieve(db, "台风预案的响应分级", user_id=user.id)
        assert len(kept) == 1
        assert kept[0].score == pytest.approx(1.0, abs=1e-6)

        # 余弦相似度最大为 1，把阈值抬到 1.5 就一定会被过滤掉
        dropped = await retriever_module.retrieve(
            db, "台风预案的响应分级", user_id=user.id, score_threshold=1.5
        )
        assert dropped == []


async def test_retriever_returns_empty_for_blank_question(session_factory):
    """空问题没必要算一次查询向量。"""
    async with session_factory() as db:
        assert await retriever_module.retrieve(db, "   ", user_id=1) == []


def test_retrieved_chunk_label_and_score():
    chunk = RetrievedChunk(
        chunk_id="doc1-chunk2",
        document_id=1,
        chunk_index=2,
        source="预案.pdf",
        content="正文",
        distance=0.3,
    )
    assert chunk.score == pytest.approx(0.7)
    # 段号对用户是 1 起算的（第 3 段），不是数组下标
    assert chunk.label == "预案.pdf#第3段"


# ---------------------------------------------------------------------------
# Q1B：图的分支与短路
# ---------------------------------------------------------------------------


async def run_graph(db, question: str, **extra):
    graph = graph_module.build_graph()
    return await graph.ainvoke(
        {"question": question, "user_id": 1, "db": db, "history": [], "notes": [], **extra}
    )


async def test_graph_short_circuits_and_never_calls_model(session_factory, monkeypatch):
    """核心保证：需要知识库却检索为空时，模型**一次都不被调用**，答案直接定稿。

    这比「把『不要编造』写进提示词」强在哪：提示词是请求模型配合，这里是让模型
    根本没有机会——短路后 `generate` 节点不执行。
    """
    async def empty(db, query, *, user_id, **kwargs):
        return []

    monkeypatch.setattr(retriever_module, "retrieve", empty)
    model = install_model("这条不该被输出")
    async with session_factory() as db:
        result = await run_graph(db, "我们的应急预案里橙色预警对应什么处置流程")

    assert result["intent"] == Intent.KNOWLEDGE.value
    assert result["short_circuit"] is True
    assert result["answer"] == NO_KNOWLEDGE_ANSWER
    assert model.calls == [], f"短路路径调用了模型：{model.calls}"


async def test_graph_short_circuits_when_weather_unavailable(session_factory, monkeypatch):
    """只问天气却取数失败 → 同样短路。模型手里没数据，只能编。"""
    async def fail(city, date=None):
        raise CityNotFoundError("未找到城市")

    monkeypatch.setattr(nodes, "get_weather", fail)
    model = install_model("这条不该被输出")
    async with session_factory() as db:
        result = await run_graph(db, "明天上海要带伞吗")

    assert result["intent"] == Intent.WEATHER.value
    assert result["short_circuit"] is True
    assert result["answer"] == WEATHER_UNAVAILABLE_ANSWER
    assert model.calls == []


async def test_graph_mixed_survives_weather_failure(session_factory, monkeypatch):
    """混合意图下天气挂了，不该连带把知识库那半边的回答也弄没（需求 C3）。"""
    async def fail(city, date=None):
        raise CityNotFoundError("未找到城市")

    async def one_chunk(db, query, *, user_id, **kwargs):
        return [
            RetrievedChunk(
                chunk_id="doc1-chunk0",
                document_id=1,
                chunk_index=0,
                source="台风预案.pdf",
                content="橙色预警启动Ⅲ级响应。",
                distance=0.2,
            )
        ]

    monkeypatch.setattr(nodes, "get_weather", fail)
    monkeypatch.setattr(retriever_module, "retrieve", one_chunk)
    model = install_model("按《台风预案.pdf》第1段，应启动Ⅲ级响应。")

    async with session_factory() as db:
        result = await run_graph(db, "按公司台风预案，明天深圳该启动几级响应？")

    assert result["intent"] == Intent.MIXED.value
    assert result["short_circuit"] is False, "有知识库内容就不该短路"
    assert result["answer"] == "按《台风预案.pdf》第1段，应启动Ⅲ级响应。"
    # 只断言「几次调用」与「至少用了流式」，不断言具体方法名：
    # 图内调用时 LangGraph 的回调会让 ainvoke 内部也走流式，按 `_generate`/`_stream`
    # 的名字断言会写成一条「换个 langchain 版本就红」的脆弱用例。
    assert len(model.calls) == 1, f"规则已判出混合意图，不该再有分类调用：{model.calls}"
    assert "stream" in model.calls
    # 天气失败必须被记下来，而不是悄悄跳过
    assert any("天气" in note for note in result["notes"])


async def test_graph_chat_branch_skips_retrieval_and_weather(session_factory, monkeypatch):
    """闲聊分支既不查天气也不检索——这两处被调用的话直接让测试失败。"""

    async def explode(*args, **kwargs):
        raise AssertionError("闲聊分支不该调用天气或检索")

    monkeypatch.setattr(nodes, "get_weather", explode)
    monkeypatch.setattr(retriever_module, "retrieve", explode)
    install_model("你好，有什么可以帮你？")

    async with session_factory() as db:
        result = await run_graph(db, "你好")

    assert result["intent"] == Intent.CHAT.value
    assert result["answer"] == "你好，有什么可以帮你？"


async def test_graph_streams_tokens_from_generate_node_only(session_factory, monkeypatch):
    """意图分类的 LLM 兜底调用也会产生 token 事件，必须按节点名过滤掉。

    注意这条用例必须让检索**有结果**：本问句的意图由 LLM 判为 knowledge，
    若检索为空就会短路，也不会有任何 token 增量——那样测的就不是过滤逻辑了。
    """
    async def one_chunk(db, query, *, user_id, **kwargs):
        return [
            RetrievedChunk(
                chunk_id="doc9-chunk0",
                document_id=9,
                chunk_index=0,
                source="制度.pdf",
                content="相关内容。",
                distance=0.3,
            )
        ]

    monkeypatch.setattr(retriever_module, "retrieve", one_chunk)
    # 第一次调用给分类兜底（规则判不出来），第二次给生成
    model = install_model("knowledge", "知识库里的答复。")

    async with session_factory() as db:
        graph = graph_module.build_graph()
        deltas: list[str] = []
        final: dict = {}
        async for mode, payload in graph.astream(
            {"question": "帮我看看这个怎么弄", "user_id": 1, "db": db, "history": [], "notes": []},
            stream_mode=["updates", "messages"],
        ):
            if mode == "messages":
                chunk, meta = payload
                if meta.get("langgraph_node") == "generate":
                    deltas.append(gateway.text_of(chunk))
            else:
                for update in payload.values():
                    if isinstance(update, dict):
                        final.update(update)

    assert final["intent"] == Intent.KNOWLEDGE.value
    # 恰好两次逻辑调用：一次分类兜底、一次生成。计数而不是比对方法名（理由同上一条用例）。
    assert len(model.calls) == 2, f"应当恰好调用模型两次，实际 {model.calls}"
    joined = "".join(deltas)
    assert joined == "知识库里的答复。"
    assert "knowledge" not in joined, "分类结果被混进了回答流"


# ---------------------------------------------------------------------------
# L2：会话与消息的数据访问
# ---------------------------------------------------------------------------


async def test_conversation_lookup_is_scoped_to_owner(session_factory):
    async with session_factory() as db:
        alice = await make_user(db, "alice_conv")
        bob = await make_user(db, "bob_conv")
        conversation = await conversation_crud.create_conversation(db, alice.id)

        assert await conversation_crud.get_conversation(db, conversation.id, alice.id) is not None
        # 「不存在」与「属于别人」返回同一个 None，上层统一转 404，避免被用来枚举 id
        assert await conversation_crud.get_conversation(db, conversation.id, bob.id) is None


async def test_adding_message_bumps_conversation_updated_at(session_factory):
    """会话列表按最近活跃排序，靠的就是这一步——漏了会让顺序停在创建时刻。

    ⚠️ 必须先把「较新的那个会话」的时间戳挪到过去，否则观察不到差异：
    SQLite 的 `CURRENT_TIMESTAMP` 只精确到**秒**，同一秒内创建的两个会话时间戳完全相同，
    排序会退化成按 id 兜底（真实使用中提问间隔远超一秒，不受影响，但测试跑得太快）。
    """
    async with session_factory() as db:
        user = await make_user(db, "dave_conv")
        older = await conversation_crud.create_conversation(db, user.id, title="旧的")
        newer = await conversation_crud.create_conversation(db, user.id, title="新的")

        # 两个都挪到过去，且让 newer 比 older 更新——这样「顺序变化」的唯一原因
        # 就只可能是 add_message 的 bump，而不是创建时刻的先后或同一秒的时间戳。
        await db.execute(
            sa_update(Conversation)
            .where(Conversation.id == older.id)
            .values(updated_at=datetime(2020, 1, 1))
        )
        await db.execute(
            sa_update(Conversation)
            .where(Conversation.id == newer.id)
            .values(updated_at=datetime(2020, 6, 1))
        )
        await db.commit()

        before = await conversation_crud.list_conversations(db, user.id)
        assert [c.id for c in before] == [newer.id, older.id]

        await conversation_crud.add_message(
            db, older.id, role=conversation_crud.ROLE_USER, content="刚说的话"
        )

        after = await conversation_crud.list_conversations(db, user.id)
        assert after[0].id == older.id, "有消息往来的会话应当排到最前"


async def test_list_messages_limit_keeps_the_newest(session_factory):
    """截断要截掉最早的：影响下一句的是最近的上下文。"""
    async with session_factory() as db:
        user = await make_user(db, "erin_conv")
        conversation = await conversation_crud.create_conversation(db, user.id)
        for index in range(6):
            await conversation_crud.add_message(
                db, conversation.id, role=conversation_crud.ROLE_USER, content=f"第{index}条"
            )

        kept = await conversation_crud.list_messages(db, conversation.id, limit=3)
        assert [row.content for row in kept] == ["第3条", "第4条", "第5条"]


async def test_message_page_one_is_the_newest_page(session_factory):
    async with session_factory() as db:
        user = await make_user(db, "frank_conv")
        conversation = await conversation_crud.create_conversation(db, user.id)
        for index in range(5):
            await conversation_crud.add_message(
                db, conversation.id, role=conversation_crud.ROLE_USER, content=f"m{index}"
            )

        page1 = await conversation_crud.list_messages_page(db, conversation.id, page=1, page_size=2)
        page3 = await conversation_crud.list_messages_page(db, conversation.id, page=3, page_size=2)
        assert [row.content for row in page1] == ["m3", "m4"]
        assert [row.content for row in page3] == ["m0"]
        # 页内按时间正序，前端可以逐条渲染
        assert [row.id for row in page1] == sorted(row.id for row in page1)


async def test_conversation_delete_cascades_to_messages(session_factory):
    async with session_factory() as db:
        user = await make_user(db, "grace_conv")
        conversation = await conversation_crud.create_conversation(db, user.id)
        conversation_id = conversation.id
        await conversation_crud.add_message(
            db, conversation_id, role=conversation_crud.ROLE_USER, content="x"
        )
        assert await conversation_crud.count_messages(db, conversation_id) == 1

        await conversation_crud.delete_conversation(db, conversation)
        assert await conversation_crud.count_messages(db, conversation_id) == 0


async def test_search_matches_title_and_message_and_isolates_users(session_factory):
    """搜索要同时覆盖「标题命中」与「消息内容命中」，且严格隔离用户。"""
    async with session_factory() as db:
        alice = await make_user(db, "search_alice")
        bob = await make_user(db, "search_bob")

        by_title = await conversation_crud.create_conversation(db, alice.id, title="上海天气咨询")
        by_content = await conversation_crud.create_conversation(db, alice.id, title="出行安排")
        await conversation_crud.add_message(
            db, by_content.id, role=conversation_crud.ROLE_USER, content="差旅报销标准是多少"
        )
        # 别人的会话：即使标题一模一样也不能被搜到（隔离做在 crud 层）
        await conversation_crud.create_conversation(db, bob.id, title="上海天气咨询")

        title_hits = await conversation_crud.search_conversations(db, alice.id, "上海")
        assert [c.id for c in title_hits] == [by_title.id]

        content_hits = await conversation_crud.search_conversations(db, alice.id, "报销")
        assert [c.id for c in content_hits] == [by_content.id]

        assert await conversation_crud.count_search_conversations(db, alice.id, "上海") == 1
        assert await conversation_crud.count_search_conversations(db, alice.id, "报销") == 1
        assert await conversation_crud.search_conversations(db, alice.id, "不存在的词") == []


async def test_search_escapes_like_wildcards(session_factory):
    """用户输入的 % / _ 不能被当成 LIKE 通配符，否则搜索退化成「返回全部会话」。"""
    async with session_factory() as db:
        user = await make_user(db, "search_escape")
        await conversation_crud.create_conversation(db, user.id, title="普通会话")

        assert await conversation_crud.search_conversations(db, user.id, "%") == []
        assert await conversation_crud.search_conversations(db, user.id, "_") == []


def test_derive_title_truncates_to_20_chars():
    assert chat_service.derive_title("短标题") == "短标题"
    long_question = "一" * 30
    derived = chat_service.derive_title(long_question)
    assert derived == "一" * 20 + "…"
    # 换行与连续空格会被压平，避免标题在列表里折行
    assert chat_service.derive_title("第一行\n第二行") == "第一行 第二行"
    assert chat_service.derive_title("   ") == conversation_crud.DEFAULT_TITLE


# ---------------------------------------------------------------------------
# L5：HTTP 端到端
# ---------------------------------------------------------------------------


async def test_chat_non_stream_returns_envelope(client):
    headers = await register(client, "chat_user1")
    install_model("明天上海多云，18~24°C，降水概率 20%，不用带伞。")

    response = await client.post(
        "/api/v1/chat", json={"question": "你好", "stream": False}, headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["intent"] == Intent.CHAT.value
    assert "不用带伞" in data["answer"]
    assert data["message_id"] > 0
    assert body["trace_id"]


async def test_chat_creates_conversation_and_derives_title(client):
    headers = await register(client, "chat_user2")
    install_model("好的。")

    response = await client.post(
        "/api/v1/chat", json={"question": "明天上海要带伞吗这个问题的标题应该会被截断"}, headers=headers
    )
    conversation_id = response.json()["data"]["conversation_id"]

    listing = await client.get("/api/v1/conversations", headers=headers)
    item = listing.json()["data"]["items"][0]
    assert item["id"] == conversation_id
    assert item["title"].endswith("…")
    assert len(item["title"]) == conversation_crud.TITLE_MAX_CHARS + 1


async def test_chat_continues_existing_conversation(client):
    headers = await register(client, "chat_user3")
    install_model("第一答。", "第二答。")

    first = await client.post("/api/v1/chat", json={"question": "你好"}, headers=headers)
    conversation_id = first.json()["data"]["conversation_id"]

    second = await client.post(
        "/api/v1/chat",
        json={"question": "谢谢", "conversation_id": conversation_id},
        headers=headers,
    )
    assert second.json()["data"]["conversation_id"] == conversation_id

    messages = await client.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=headers
    )
    data = messages.json()["data"]
    assert data["total"] == 4
    assert [row["role"] for row in data["items"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


async def test_chat_rejects_other_users_conversation(client):
    """「不存在」与「属于别人」都是 404，不给出可区分的错误。"""
    alice = await register(client, "chat_alice")
    bob = await register(client, "chat_bob")
    install_model("好的。")

    created = await client.post("/api/v1/chat", json={"question": "你好"}, headers=alice)
    conversation_id = created.json()["data"]["conversation_id"]

    response = await client.post(
        "/api/v1/chat",
        json={"question": "你好", "conversation_id": conversation_id},
        headers=bob,
    )
    assert response.status_code == 404

    assert (
        await client.get(f"/api/v1/conversations/{conversation_id}/messages", headers=bob)
    ).status_code == 404
    assert (
        await client.delete(f"/api/v1/conversations/{conversation_id}", headers=bob)
    ).status_code == 404


async def test_chat_stream_emits_sse_events_in_order(client):
    """SSE 契约：事件名、顺序（meta → delta → done）、以及 SSE 必需的空行分隔。"""
    headers = await register(client, "chat_stream")
    install_model("多云，不用带伞。")

    response = await client.post(
        "/api/v1/chat", json={"question": "你好", "stream": True}, headers=headers
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"

    text = response.text
    # 少一个空行就会让浏览器一直挂起后续事件，所以格式本身也要断言
    assert text.endswith("\n\n")
    assert "event: meta" in text and "event: delta" in text and "event: done" in text
    assert text.index("event: meta") < text.index("event: delta") < text.index("event: done")
    assert "不用带伞" in text


async def test_chat_stream_short_circuit_still_sends_the_answer_as_a_delta(client):
    """短路路径不经过生成节点，但答案仍要作为 delta 发出去，否则前端的流式渲染区是空的。"""
    headers = await register(client, "chat_stream_short")
    install_model("这条不该被输出")

    response = await client.post(
        "/api/v1/chat",
        json={"question": "我们的应急预案里橙色预警对应什么处置流程", "stream": True},
        headers=headers,
    )
    text = response.text
    assert "event: delta" in text
    assert "知识库中未找到相关内容" in text
    assert "event: done" in text


async def test_chat_validates_question_and_date(client, monkeypatch):
    headers = await register(client, "chat_validate")
    monkeypatch.setattr(settings, "chat_max_question_chars", 10)

    too_long = await client.post("/api/v1/chat", json={"question": "一" * 11}, headers=headers)
    assert too_long.status_code == 422
    assert too_long.json()["code"] == 1000

    bad_date = await client.post(
        "/api/v1/chat", json={"question": "上海天气", "city": "上海", "date": "2026/10/20"}, headers=headers
    )
    assert bad_date.status_code == 422

    blank = await client.post("/api/v1/chat", json={"question": "   "}, headers=headers)
    assert blank.status_code == 422


async def test_chat_requires_authentication(client):
    response = await client.post("/api/v1/chat", json={"question": "你好"})
    assert response.status_code == 401


async def test_conversation_list_paginates_and_deletes(client):
    headers = await register(client, "chat_list")
    install_model(*["好的。"] * 3)

    for _ in range(3):
        await client.post("/api/v1/chat", json={"question": "你好"}, headers=headers)

    listing = await client.get("/api/v1/conversations?page=1&page_size=2", headers=headers)
    data = listing.json()["data"]
    assert data["total"] == 3
    assert data["page_size"] == 2
    assert len(data["items"]) == 2

    target = data["items"][0]["id"]
    deleted = await client.delete(f"/api/v1/conversations/{target}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["message"] == "会话已删除"

    after = await client.get("/api/v1/conversations", headers=headers)
    assert after.json()["data"]["total"] == 2


async def test_missing_conversation_returns_404(client):
    headers = await register(client, "chat_404")
    assert (await client.get("/api/v1/conversations/999/messages", headers=headers)).status_code == 404
    assert (await client.delete("/api/v1/conversations/999", headers=headers)).status_code == 404


async def test_conversation_search_endpoint(client):
    """GET /conversations/search：标题或聊天内容命中，且搜不到别的会话。"""
    headers = await register(client, "chat_search")
    # 第一次问天气：测试环境离线 → 短路为固定文案（不调模型）；第二次闲聊需模型出字。
    # 假模型按顺序消费：第一条给分类（必须回 "chat"），第二条给生成。
    install_model("chat", "春天来了，花开满枝。")

    first = await client.post(
        "/api/v1/chat", json={"question": "上海明天天气怎么样"}, headers=headers
    )
    assert first.status_code == 200
    await client.post(
        "/api/v1/chat", json={"question": "帮我写一首关于春天的诗"}, headers=headers
    )

    # 标题命中（标题取自问题前 20 字）
    hit = await client.get("/api/v1/conversations/search?q=上海", headers=headers)
    assert hit.status_code == 200
    data = hit.json()["data"]
    assert data["total"] == 1
    assert "上海" in data["items"][0]["title"]

    # 聊天内容命中（问题本身作为 user 消息落库）
    content_hit = await client.get("/api/v1/conversations/search?q=春天", headers=headers)
    assert content_hit.json()["data"]["total"] == 1

    miss = await client.get("/api/v1/conversations/search?q=不存在的关键词", headers=headers)
    assert miss.json()["data"]["total"] == 0


async def test_conversation_search_requires_authentication(client):
    assert (await client.get("/api/v1/conversations/search?q=x")).status_code == 401


async def test_gateway_reports_missing_credentials_clearly(monkeypatch):
    """没配密钥时要给出「去哪填」的具体指引，而不是一句 502。"""
    monkeypatch.setattr(settings, "llm_api_key", "")
    gateway.set_chat_model_override(None)
    gateway.reset_chat_model()
    from langchain_core.messages import HumanMessage

    with pytest.raises(Exception) as excinfo:
        await gateway.ainvoke_text([HumanMessage(content="x")])
    message = str(excinfo.value)
    assert "LLM_API_KEY" in message
    assert "platform.deepseek.com" in message
    # 必须点明与和风天气的凭据无关——这两套凭据最容易混
    assert "和风" in message
