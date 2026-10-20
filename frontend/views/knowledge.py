"""【L6】知识库管理页（M5 落地，M5-Q4 选「完整集」）。

职责：上传 / 进度轮询 / 覆盖重传 / 删除 / 全量重建索引 / 失败原因展示。

## 为什么要有「进度轮询」这一环

后端入库是**后台异步**的：上传返回 `202`，文档停在 `pending`，真正的解析→切分→
向量化在后台任务里跑。若不轮询，前端只能看到「已上传」却看不到「切好了没」，
而且失败了也浑然不觉——那等于把「正在入库」伪装成「已入库」。

这里上传后**就地轮询** `GET /knowledge/documents/{id}`，直到 `ready` / `failed`，
用小屏实时刷新状态。小文档通常一两秒，大文档也能看到状态在走。
"""

from __future__ import annotations

import time

import streamlit as st
from api_client import BackendClient, BackendError

from views import get_client

_SUPPORTED_TYPES = ["txt", "md", "csv", "pdf", "docx"]


def render() -> None:
    client = get_client()
    token = st.session_state["token"]

    st.title("知识库")
    st.caption("上传公司文档，问答时就能基于它们回答，并标注来源。")

    _render_upload(client, token)
    _render_reindex(client, token)
    st.markdown("---")
    _render_document_list(client, token)


def _render_upload(client: BackendClient, token: str) -> None:
    uploaded = st.file_uploader(
        "上传文档（支持 txt / md / csv / pdf / docx，单文件上限见后端配置）",
        type=_SUPPORTED_TYPES,
        key="doc_upload",
    )
    if uploaded is not None and st.button("上传", type="primary"):
        try:
            _upload_and_wait(client, token, uploaded.name, uploaded.getvalue())
            st.rerun()
        except BackendError as exc:
            st.error(f"上传失败：{exc.message}")


def _render_reindex(client: BackendClient, token: str) -> None:
    with st.expander("🔁 全量重建索引"):
        st.write("换过向量模型、或怀疑索引损坏时使用。会重新投递所有已入库文档做向量化，不删除任何数据。")
        if st.button("开始重建"):
            try:
                result = client.reindex(token)
                st.info(
                    f"已开始重建：共 {result.get('total', 0)} 篇，本次投递 {result.get('dispatched', 0)} 篇。"
                )
                st.rerun()
            except BackendError as exc:
                st.error(f"重建失败：{exc.message}")


def _render_document_list(client: BackendClient, token: str) -> None:
    try:
        data = client.list_documents(token, page_size=100)
    except BackendError as exc:
        st.error(f"无法加载文档列表：{exc.message}")
        return

    items = data.get("items") or []
    st.subheader(f"已入库文档（{data.get('total', 0)}）")
    if not items:
        st.info("还没有文档，先上传一份试试。")
        return

    for document in items:
        _render_document(client, token, document)


def _render_document(client: BackendClient, token: str, document: dict) -> None:
    document_id = document["id"]
    filename = document.get("filename", "（无文件名）")
    status = document.get("status", "pending")
    chunk_count = document.get("chunk_count", 0)

    with st.container(border=True):
        head = f"**{filename}**　`{status}`　切分 {chunk_count} 段"
        if status == "failed":
            st.error(head)
            st.caption(f"失败原因：{document.get('error_message')}")
        elif status == "ready":
            st.markdown(head)
        else:
            st.info(head)

        col_overwrite, col_delete = st.columns(2)
        with col_overwrite:
            with st.expander("覆盖重传（重新解析并重建向量）"):
                replacement = st.file_uploader(
                    "选择新文件", type=_SUPPORTED_TYPES, key=f"replace_{document_id}"
                )
                if replacement is not None and st.button("覆盖", key=f"replace_btn_{document_id}"):
                    try:
                        _upload_and_wait(client, token, replacement.name, replacement.getvalue(), document_id=document_id)
                        st.rerun()
                    except BackendError as exc:
                        st.error(f"覆盖失败：{exc.message}")
        with col_delete:
            if st.button("🗑 删除", key=f"delete_doc_{document_id}"):
                try:
                    client.delete_document(token, document_id)
                    st.success(f"已删除「{filename}」。")
                    st.rerun()
                except BackendError as exc:
                    st.error(f"删除失败：{exc.message}")


def _upload_and_wait(
    client: BackendClient,
    token: str,
    filename: str,
    content: bytes,
    *,
    document_id: int | None = None,
) -> None:
    """上传（或覆盖）并就地轮询入库状态，直到 ready / failed。

    `document_id` 为 None 时是新上传，否则是「覆盖重传」（走 PUT）。
    """
    if document_id is None:
        result = client.upload_document(token, filename, content)
    else:
        result = client.update_document(token, document_id, filename, content)

    document = result["document"]
    if result.get("deduplicated"):
        st.info(f"「{filename}」已存在（内容完全相同），未重复处理。")
        return

    placeholder = st.empty()
    status = document.get("status", "pending")
    detail: dict = document
    deadline = time.time() + 90
    while time.time() < deadline:
        if status in ("ready", "failed"):
            break
        time.sleep(0.4)
        try:
            detail = client.get_document(token, document["id"])
        except BackendError:
            detail = {}
        status = detail.get("status", status)
        placeholder.info(f"「{filename}」正在入库（{status}）…")

    if status == "ready":
        placeholder.success(f"✅「{filename}」已入库，切分 {detail.get('chunk_count', '?')} 段。")
    elif status == "failed":
        placeholder.error(f"❌「{filename}」入库失败：{detail.get('error_message') or '（无详情）'}")
    else:
        placeholder.warning(f"「{filename}」仍在入库中，可稍后刷新页面查看结果。")
