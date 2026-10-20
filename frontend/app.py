"""【L6】前端入口：登录态 + 多页导航。

M5-Q1 选「多页应用」：登录 / 对话 / 知识库三页，用 `st.navigation` 组织。

登录态放在 `st.session_state`：Streamlit 的一次会话内，所有页面共享同一个
`session_state`，所以「登录一次、各页通用」是天然成立的。退出登录就是清掉
`token` 再 `st.rerun()`——rerun 后 `logged_in()` 变假，导航自动退回登录页。

运行方式（先起后端，再起前端）：
    .venv/Scripts/python.exe -m streamlit run frontend/app.py
后端地址默认 `http://127.0.0.1:8000/api/v1`，可用环境变量 `API_BASE_URL` 覆盖。
"""

from __future__ import annotations

import streamlit as st

import theme

# 页面先 import（此时只是拿到 callable，不执行其渲染逻辑）
from views import chat, knowledge, login

st.set_page_config(
    page_title="瓜葛天气助手",
    page_icon="🌸",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 注入少女粉主题（CSS + 漂浮装饰）。必须放在 nav.run() 之前，且晚于 set_page_config。
theme.inject()


def logged_in() -> bool:
    return bool(st.session_state.get("token"))


# 用 callable 构造 Page：st.Page 支持传入函数，渲染时再调用（见 chat/knowledge/login 的 render）
login_page = st.Page(login.render, title="登录", url_path="login")
chat_page = st.Page(chat.render, title="对话", url_path="chat")
knowledge_page = st.Page(knowledge.render, title="知识库", url_path="knowledge")

if logged_in():
    with st.sidebar:
        st.markdown(f"已登录：**{st.session_state.get('username', '')}**")
        if st.button("退出登录", use_container_width=True):
            st.session_state.pop("token", None)
            st.session_state.pop("username", None)
            st.session_state.pop("conversation_id", None)
            st.rerun()
    nav = st.navigation([chat_page, knowledge_page])
else:
    nav = st.navigation([login_page])

nav.run()
