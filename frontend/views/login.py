"""【L6】登录页（M5 落地）。

登录成功直接拿到 access_token；注册成功后再调一次 `login` 拿 token（后端注册只回传
用户信息、不直接发 token）。token 存进 `st.session_state`，然后 `st.rerun()`。
rerun 后 `app.py` 的 `logged_in()` 变真，导航自动从「只有登录页」切换成「对话 + 知识库」。
"""

from __future__ import annotations

import streamlit as st
from api_client import BackendError, default_client


def render() -> None:
    st.title("🌸 瓜葛天气助手")
    st.caption("✨ 可私有化部署的企业内部智能问答 Agent —— 先登录，再对话或管理知识库。")

    client = default_client()

    tab_login, tab_register = st.tabs(["登录", "注册"])

    with tab_login:
        with st.form("login_form"):
            username = st.text_input("用户名", key="login_username")
            password = st.text_input("密码", type="password", key="login_password")
            submitted = st.form_submit_button("登录", type="primary", use_container_width=True)
            if submitted:
                if not username or not password:
                    st.error("请输入用户名与密码。")
                else:
                    try:
                        token = client.login(username.strip(), password)
                        st.session_state["token"] = token
                        st.session_state["username"] = username.strip()
                        st.rerun()
                    except BackendError as exc:
                        st.error(f"登录失败：{exc.message}")

    with tab_register:
        with st.form("register_form"):
            r_username = st.text_input("用户名", key="reg_username")
            r_email = st.text_input("邮箱", key="reg_email")
            r_password = st.text_input("密码（至少 6 位）", type="password", key="reg_password")
            submitted = st.form_submit_button("注册并登录", use_container_width=True)
            if submitted:
                if not r_username or not r_password:
                    st.error("用户名与密码不能为空。")
                elif len(r_password) < 6:
                    st.error("密码至少 6 位。")
                else:
                    try:
                        # 后端注册只回传用户信息、不直接发 token，所以注册成功后
                        # 再调 login 拿 access_token——两个动作分开，语义更清楚。
                        client.register(r_username.strip(), r_email.strip(), r_password)
                        token = client.login(r_username.strip(), r_password)
                        st.session_state["token"] = token
                        st.session_state["username"] = r_username.strip()
                        st.rerun()
                    except BackendError as exc:
                        st.error(f"注册失败：{exc.message}")
