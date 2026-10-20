"""【L6】对话页（M5 落地）。

三块职责：
1. **侧边栏会话管理**：新建 / 切换 / 删除会话；
2. **历史消息渲染**：按时间正序逐条显示；
3. **流式问答**（M5-Q2：delta 实时拼接 + done 覆盖兜底）+ **混合输入**（M5-Q3）。

## 流式渲染的三个关键点

1. **提交后先亮「（思考中）」**：到第一个 `delta` 之间后端要跑意图识别与检索（数秒），
   这段空白必须给出「在动」的反馈，否则会被当成卡死（见 `_THINKING_HTML`）；
2. `st.empty()` 占位 + 循环里 `placeholder.markdown(...)` 更新，实现「打字机」效果；
3. **delta 拼接是临时态，done 的 `answer` 才是权威态**。所以流结束时**再用完整
   `answer` 覆盖一次**——这一步是 M5-Q2 决策的兑现：即便中间丢了一个 delta、或
   后端走了短路只补发一个 delta，最终屏幕上也一定是完整的答案。

## 会话 id 从哪里来

首次提问时 `conversation_id` 是 `None`（后端新建会话），后端在 SSE 的 `meta` 事件里
带回真正的 `conversation_id`，这里取它存进 session_state——下次追问就接续同一会话。
"""

from __future__ import annotations

import re
from typing import Any

import streamlit as st

from api_client import BackendClient, BackendError
from views import get_client

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 等待模型首个字时显示的占位。
#
# 为什么需要它：从提交问题到第一个 `delta` 之间，后端要先跑一次 LLM 意图识别、再做知识库
# 检索，实测有几秒。这段时间助手气泡里**什么都没有**，用户很容易以为卡死了。
#
# 为什么用 HTML + CSS 动效而不是一句纯文本：静止的「思考中」看两秒同样会被当成卡住，
# 必须让它是「在动」的。三个小点依次跳动，语义上就是通用的「正在输入」。
# 样式定义在 frontend/theme.py 的 `.pb-thinking`。
_THINKING_HTML = (
    '<div class="pb-thinking">（思考中）'
    '<span class="pb-thinking-dot">.</span>'
    '<span class="pb-thinking-dot">.</span>'
    '<span class="pb-thinking-dot">.</span>'
    "</div>"
)


def render() -> None:
    client = get_client()
    token = st.session_state["token"]

    _render_sidebar(client, token)

    st.title("对话")
    st.caption("问天气、问公司制度都可以；引用知识库的回答会标注来源。")

    # 历史消息（按时间正序）
    for message in st.session_state.get("messages", []):
        role = "user" if message.get("role") == "user" else "assistant"
        with st.chat_message(role):
            st.write(message.get("content", ""))

    # 混合输入：可折叠的城市 / 日期显式控件（M5-Q3）
    with st.expander("☀️ 指定城市 / 日期（可选，不填则从问题里猜）"):
        col_city, col_date = st.columns(2)
        city = col_city.text_input("城市", placeholder="如：上海市浦东新区", key="city_input")
        date_raw = col_date.text_input("日期", placeholder="YYYY-MM-DD", key="date_input")

    question = st.chat_input("输入问题，例如：明天上海要带伞吗？")
    if not question:
        return

    date = date_raw.strip() if date_raw and date_raw.strip() else None
    if date and not _DATE_RE.match(date):
        st.warning("日期格式应为 YYYY-MM-DD，本次将忽略该日期。")
        date = None

    # 用户消息立即上屏（历史循环已跑过，这条要在这里手动渲染一次）
    messages = st.session_state.setdefault("messages", [])
    messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)

    with st.chat_message("assistant"):
        _run_stream(client, token, question, city=city or None, date=date)


def _run_stream(
    client: BackendClient,
    token: str,
    question: str,
    *,
    city: str | None,
    date: str | None,
) -> None:
    """跑一次流式问答，把结果渲染并写回 session_state。"""
    conversation_id = st.session_state.get("conversation_id")
    placeholder = st.empty()

    # 先亮「思考中」，避免提交后到首个 delta 之间出现一段空白气泡。
    # 它不需要单独的「清除」逻辑：第一个 delta 会用流式文本把它覆盖掉。
    placeholder.markdown(_THINKING_HTML, unsafe_allow_html=True)

    streamed = ""
    final_answer = ""
    final_conversation_id = conversation_id
    sources: list[dict[str, Any]] = []
    notes: list[str] = []
    failed = False

    try:
        for name, data in client.chat_stream(
            token,
            question,
            conversation_id=conversation_id,
            city=city,
            date=date,
        ):
            if name == "meta":
                final_conversation_id = data.get("conversation_id", final_conversation_id)
                sources = data.get("sources") or []
            elif name == "delta":
                streamed += data.get("text", "")
                # 只渲染已流出的文本本身，**不在末尾拼任何光标/占位符**。
                # 早先这里拼了一个「▌」，用户反馈每条回答末尾都会拖一个黑色方块，
                # 很碍眼。而且它并非必要：文字本身在变长就是「还在生成」的反馈，
                # 首字之前的空窗期另有「（思考中）」占位兜着（见上方 _THINKING_HTML）。
                placeholder.markdown(streamed)
            elif name == "done":
                final_answer = data.get("answer") or streamed
                final_conversation_id = data.get("conversation_id", final_conversation_id)
                notes = data.get("notes") or []
            elif name == "error":
                failed = True
                placeholder.error(f"这轮没有答完：{data.get('message', '未知错误')}")
    except BackendError as exc:
        failed = True
        placeholder.error(f"调用失败：{exc.message}")

    if final_answer:
        # done 兜底：用完整 answer 覆盖掉流式拼接的中间态（M5-Q2）
        placeholder.markdown(final_answer)
        st.session_state.setdefault("messages", []).append(
            {"role": "assistant", "content": final_answer}
        )
        if sources:
            _render_sources(sources)
        if st.session_state.get("show_notes") and notes:
            with st.expander("🔍 过程痕迹（调试用）"):
                st.write("\n".join(f"- {item}" for item in notes))
    elif streamed:
        # 收到过 delta 却没等到 done（例如连接中途断开）：保留已经流出来的内容。
        # **不写进历史**——半截回答留在会话里比不写更容易误导。
        placeholder.markdown(streamed)
    elif not failed:
        # 一个 delta 都没收到、也没有 error（正常契约下不该发生）：
        # 必须显式清空，否则「思考中」会一直挂在屏幕上，看起来就是卡死。
        placeholder.markdown("")

    if final_conversation_id is not None:
        st.session_state["conversation_id"] = final_conversation_id


def _render_sources(sources: list[dict[str, Any]]) -> None:
    """把依据列表渲染成一行行可读的 caption。"""
    lines = []
    for source in sources:
        label = source.get("label", "")
        score = source.get("score")
        suffix = f"（相似度 {score:.2f}）" if isinstance(score, (int, float)) else ""
        lines.append(f"· {label}{suffix}")
    if lines:
        st.caption("**依据**：" + "　".join(lines))


def _render_sidebar(client: BackendClient, token: str) -> None:
    """侧边栏：搜索 / 新建 / 切换 / 删除会话。

    搜索走 `GET /conversations/search`（标题或聊天内容命中），输入关键词后列表即切换为
    搜索结果；清空关键词回到全部会话列表。
    """
    with st.sidebar:
        st.markdown("### 会话")

        keyword = st.text_input(
            "🔍 搜索对话",
            placeholder="搜标题或聊天内容…",
            key="conversation_search",
        ).strip()

        # on_click 回调在脚本重跑前执行，所以能安全地清空搜索框（写在 if 分支里会因
        # text_input 已实例化而报「cannot be modified after the widget is instantiated」）。
        st.button("＋ 新建会话", use_container_width=True, on_click=_new_conversation)

        try:
            if keyword:
                conversations = client.search_conversations(token, keyword)
            else:
                conversations = client.list_conversations(token)
        except BackendError as exc:
            st.caption(f"⚠️ 无法加载会话列表：{exc.message}")
            conversations = {"items": []}

        items = conversations.get("items") or []
        if not items:
            st.caption("没有匹配的会话。" if keyword else "暂无历史会话，直接提问即可新建。")
            return

        for conversation in items:
            conversation_id = conversation["id"]
            is_current = st.session_state.get("conversation_id") == conversation_id
            col_title, col_delete = st.columns([5, 1])
            col_title.button(
                conversation.get("title") or "（无标题）",
                key=f"conv_{conversation_id}",
                use_container_width=True,
                disabled=is_current,
                on_click=_select_conversation,
                args=(client, token, conversation_id),
            )
            col_delete.button(
                "",
                icon=":material/delete:",
                key=f"del_{conversation_id}",
                help="删除该会话",
                width="content",
                on_click=_delete_conversation,
                args=(client, token, conversation_id),
            )


def _new_conversation() -> None:
    """新建会话：清空当前会话、消息列表与搜索框（回到全部会话视图）。"""
    st.session_state["conversation_id"] = None
    st.session_state["messages"] = []
    st.session_state["conversation_search"] = ""


def _select_conversation(client: BackendClient, token: str, conversation_id: int) -> None:
    """点会话标题：拉取该会话历史并设为当前（Streamlit 会在回调后自动 rerun）。"""
    try:
        data = client.list_messages(token, conversation_id)
    except BackendError as exc:
        st.session_state["_sidebar_error"] = f"加载会话失败：{exc.message}"
        return
    st.session_state["conversation_id"] = conversation_id
    st.session_state["messages"] = [
        {"role": message.get("role"), "content": message.get("content", "")}
        for message in data.get("items", [])
    ]


def _delete_conversation(client: BackendClient, token: str, conversation_id: int) -> None:
    """删除会话；若删的是当前会话，则回到「新建」状态。"""
    try:
        client.delete_conversation(token, conversation_id)
    except BackendError as exc:
        st.session_state["_sidebar_error"] = f"删除失败：{exc.message}"
        return
    if st.session_state.get("conversation_id") == conversation_id:
        st.session_state["conversation_id"] = None
        st.session_state["messages"] = []
