"""【L6】前端视图包。

提供 `get_client()`：跨 rerun 复用一个 `BackendClient`。Streamlit 的脚本模型是
「每次交互整个脚本重跑一遍」，若不缓存，每次 rerun 都会新建一个 `httpx.Client`
（底层是连接池），频繁交互会不断开新连接。`st.cache_resource` 让它只建一次。
"""

from __future__ import annotations

import streamlit as st
from api_client import BackendClient, default_client


@st.cache_resource
def get_client() -> BackendClient:
    return default_client()
