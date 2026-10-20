"""【L6】少女粉主题（樱花粉 / 蜜桃粉马卡龙配色）。

通过 `st.markdown` 注入 `<style>` 与漂浮装饰 HTML，纯前端装饰，不触碰任何业务
逻辑与后端交互（不违反 L6 层边界）。

## 为什么装饰用纯 CSS 动画而不是 JS 全屏粒子

Streamlit 会剥离 `st.markdown` 里的 `<script>` 标签；而 `st.components.v1.html`
是 iframe，`pointer-events` 无法穿透、会挡住点击。纯 CSS 的 `position: fixed` +
`pointer-events: none` 既能全屏漂浮、又完全不挡交互，且零额外依赖。
"""

from __future__ import annotations

import streamlit as st

CSS = """
<style>
/* ============ 少女粉主题变量 ============ */
:root {
  --pb-pink: #ff6b9d;
  --pb-pink-deep: #f0527f;
  --pb-pink-soft: #ffb3c6;
  --pb-pink-mist: #ffd6e0;
  --pb-text: #6d4a5c;
  --pb-text-light: #a3708a;
}

/* ============ 全局背景：樱花粉渐变 ============ */
.stApp {
  background: linear-gradient(160deg, #fff0f5 0%, #ffe4ec 42%, #ffd6e7 100%);
  font-size: 17px;
}

[data-testid="stHeader"] {
  background: transparent;
}

/* ============ 主内容：居中 + 放大 ============ */
.block-container {
  padding: 2.6rem 1.5rem 3.5rem;
  max-width: 840px;
  margin: 0 auto;
}

/* ============ 标题 ============ */
h1, h2, h3, h4 {
  color: var(--pb-pink-deep) !important;
  font-weight: 700 !important;
  letter-spacing: 0.2px;
}

h1 {
  font-size: 2.6rem !important;
  background: linear-gradient(135deg, #f0527f 0%, #ff8fab 60%, #ff9eb5 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

h2 { font-size: 1.9rem !important; }
h3 { font-size: 1.45rem !important; }

/* ============ 侧边栏：蜜桃粉渐变 ============ */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #ffe4ec 0%, #ffd6e7 100%);
  border-right: 2px solid #ffc2d1;
}

[data-testid="stSidebar"] * {
  color: var(--pb-text);
}

/* ============ 按钮：粉色胶囊 ============ */
.stButton button,
[data-testid="stButton"] button {
  background: linear-gradient(135deg, #ff8fab 0%, #ff6b9d 100%) !important;
  color: #ffffff !important;
  border: none !important;
  border-radius: 999px !important;
  font-weight: 600 !important;
  font-size: 16px !important;
  padding: 0.65rem 1.5rem !important;
  box-shadow: 0 4px 14px rgba(255, 107, 157, 0.35);
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}

.stButton button:hover,
[data-testid="stButton"] button:hover {
  transform: translateY(-2px);
  box-shadow: 0 7px 22px rgba(255, 107, 157, 0.55);
  color: #ffffff !important;
}

[data-testid="stBaseButton-primary"] {
  background: linear-gradient(135deg, #f0527f 0%, #ff6b9d 100%) !important;
}

[data-testid="stBaseButton-secondary"] {
  background: #fff0f5 !important;
  color: var(--pb-pink-deep) !important;
  border: 1.5px solid #ffb3c6 !important;
}

/* ============ 登录页按钮：渐变半透明玻璃按键 ============ */
[data-testid="stFormSubmitButton"] button {
  border-radius: 999px !important;
  font-weight: 600 !important;
  font-size: 16px !important;
  padding: 0.65rem 1.5rem !important;
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}

/* 登录按钮（primary）：半透明粉渐变 + 白色玻璃描边 */
[data-testid="stFormSubmitButton"] [data-testid="stBaseButton-primary"] {
  background: linear-gradient(135deg, rgba(255, 143, 171, 0.62), rgba(240, 82, 127, 0.62)) !important;
  color: #ffffff !important;
  border: 1.5px solid rgba(255, 255, 255, 0.72) !important;
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  box-shadow: 0 4px 18px rgba(255, 107, 157, 0.32);
}

[data-testid="stFormSubmitButton"] [data-testid="stBaseButton-primary"]:hover {
  background: linear-gradient(135deg, rgba(255, 143, 171, 0.78), rgba(240, 82, 127, 0.78)) !important;
  transform: translateY(-2px);
  box-shadow: 0 7px 22px rgba(255, 107, 157, 0.45);
  color: #ffffff !important;
}

/* 注册按钮（secondary）：白色半透明 + 粉描边 */
[data-testid="stFormSubmitButton"] [data-testid="stBaseButton-secondary"] {
  background: rgba(255, 255, 255, 0.55) !important;
  color: var(--pb-pink-deep) !important;
  border: 1.5px solid #ffb3c6 !important;
}

/* ============ 侧边栏按钮：紧凑内边距 ============ */
/* 删除按钮在 [5,1] 的窄列里，若沿用主按钮的大内边距会把图标挤出可视区（表现为空白圆点）。 */
[data-testid="stSidebar"] [data-testid="stButton"] button {
  padding: 0.4rem 0.7rem !important;
  min-height: 38px;
  font-size: 15px !important;
}

/* ============ 输入框：单层边框（边框在容器层，input 透明） ============ */
[data-testid="stTextInput"] [data-baseweb="input"],
[data-testid="stNumberInput"] [data-baseweb="input"] {
  border: 2px solid #ffc2d1 !important;
  border-radius: 14px !important;
  background: #fffafc !important;
  box-shadow: none !important;
  min-height: 46px;
}

/* input 元素自身透明、去边框，避免「框套框」 */
[data-testid="stTextInput"] input[data-baseweb="input"],
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input {
  border: none !important;
  background: transparent !important;
  box-shadow: none !important;
  color: var(--pb-text) !important;
  font-size: 16px !important;
}

/* 聚焦：容器层光圈，而不是给 input 再加一圈 */
[data-testid="stTextInput"] [data-baseweb="input"]:focus-within,
[data-testid="stNumberInput"] [data-baseweb="input"]:focus-within {
  border-color: var(--pb-pink) !important;
  box-shadow: 0 0 0 4px rgba(255, 107, 157, 0.16) !important;
}

/* 聊天输入框：同样只在容器层加边框 */
[data-testid="stChatInputTextArea"] {
  border: 2px solid #ffc2d1 !important;
  border-radius: 18px !important;
  background: #fffafc !important;
}

[data-testid="stChatInput"] textarea {
  border: none !important;
  background: transparent !important;
  box-shadow: none !important;
  color: var(--pb-text) !important;
  font-size: 16px !important;
}

[data-testid="stChatInput"] {
  border: none !important;
}

/* ============ 聊天气泡 ============ */
[data-testid="stChatMessage"] {
  border-radius: 20px !important;
  padding: 0.7rem 1rem !important;
  margin: 0.4rem 0 !important;
  background: rgba(255, 255, 255, 0.72);
  border: 1.5px solid #ffd6e0;
  box-shadow: 0 2px 10px rgba(255, 143, 171, 0.12);
}

/* ============ 「思考中」占位（等待模型第一个字时显示） ============ */
/* 从提交问题到第一个 delta 之间，后端在跑意图识别（一次 LLM 调用）与知识库检索，
   通常要几秒。这段时间视觉上必须「在动」，静止的文字会被当成卡死。
   结构由 views/chat.py 的 _THINKING_HTML 生成，这里只管样式。 */
.pb-thinking {
  display: inline-flex;
  align-items: center;
  color: var(--pb-pink-deep);
  font-size: 16px;
  font-style: italic;
  letter-spacing: 0.4px;
  /* 整体轻微呼吸，配合后面三个跳动的小点 */
  animation: pb-breathe 1.6s ease-in-out infinite;
}

.pb-thinking-dot {
  font-style: normal;
  font-weight: 700;
  line-height: 1;
  animation: pb-think-dot 1.2s ease-in-out infinite;
}

@keyframes pb-breathe {
  0%, 100% { opacity: 0.7; }
  50%      { opacity: 1; }
}

@keyframes pb-think-dot {
  0%, 100% { opacity: 0.15; transform: translateY(0); }
  40%      { opacity: 1;    transform: translateY(-3px); }
}

/* ============ 标签页：半透明渐变玻璃按键 ============ */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
  gap: 10px;
  background: transparent;
}

/* 登录 / 注册两个 tab 都做成半透明渐变 */
[data-testid="stTabs"] [data-baseweb="tab"] {
  background: linear-gradient(135deg, rgba(255, 143, 171, 0.5), rgba(240, 82, 127, 0.5)) !important;
  color: #ffffff !important;
  border: 1.5px solid rgba(255, 255, 255, 0.7) !important;
  border-radius: 14px !important;
  padding: 0.55rem 1.5rem;
  font-weight: 600;
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  box-shadow: 0 3px 12px rgba(255, 107, 157, 0.2);
}

/* 选中的 tab：渐变更饱和一点，与未选中拉开层次 */
[data-testid="stTabs"] [aria-selected="true"] {
  background: linear-gradient(135deg, rgba(255, 143, 171, 0.75), rgba(240, 82, 127, 0.75)) !important;
  color: #ffffff !important;
  box-shadow: 0 4px 16px rgba(255, 107, 157, 0.34);
}

[data-testid="stTabs"] [data-baseweb="tab-highlight"] {
  background-color: transparent;
}

/* ============ 折叠面板 ============ */
[data-testid="stExpander"] {
  background: rgba(255, 255, 255, 0.6);
  border: 1.5px solid #ffd6e0 !important;
  border-radius: 16px !important;
}

/* ============ 表单：去重边框，只留柔和背景 ============ */
[data-testid="stForm"] {
  background: rgba(255, 255, 255, 0.72);
  border: 1.5px solid #ffe0e9;
  border-radius: 24px;
  padding: 1.5rem 1.7rem;
  box-shadow: 0 8px 28px rgba(255, 143, 171, 0.16);
}

/* ============ 文件上传 ============ */
[data-testid="stFileUploader"] section {
  background: #fff5f8;
  border: 2px dashed #ffb3c6 !important;
  border-radius: 18px !important;
}

/* ============ 提示框 ============ */
[data-testid="stAlert"] {
  border-radius: 14px;
}

.st-caption, .st-caption p {
  color: var(--pb-text-light) !important;
}

/* ============ 滚动条 ============ */
::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}
::-webkit-scrollbar-track {
  background: #ffe4ec;
}
::-webkit-scrollbar-thumb {
  background: linear-gradient(180deg, #ffb3c6, #ff8fab);
  border-radius: 999px;
}
::-webkit-scrollbar-thumb:hover {
  background: #ff6b9d;
}

/* ============ 漂浮装饰（爱心 / 花瓣 / 蝴蝶结） ============ */
.pb-decor {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  overflow: hidden;
}
.pb-decor span {
  position: absolute;
  bottom: -48px;
  font-size: 22px;
  opacity: 0;
  animation: pb-float linear infinite;
  filter: drop-shadow(0 2px 6px rgba(255, 107, 157, 0.35));
}
@keyframes pb-float {
  0% {
    transform: translateY(0) translateX(0) rotate(0deg) scale(0.7);
    opacity: 0;
  }
  10% {
    opacity: 0.9;
  }
  50% {
    transform: translateY(-55vh) translateX(34px) rotate(180deg) scale(1.08);
  }
  90% {
    opacity: 0.55;
  }
  100% {
    transform: translateY(-112vh) translateX(-26px) rotate(360deg) scale(0.8);
    opacity: 0;
  }
}
</style>
"""

# 漂浮装饰元素：26 个 emoji 粒子，位置 / 时长 / 延迟各不相同，错落飘浮。
_DECOR_SPANS = "".join(
    f'<span style="left:{left}%; animation-duration:{dur}s; animation-delay:{delay}s;">{icon}</span>'
    for left, dur, delay, icon in [
        (2, 13, 0, "🌸"),
        (6, 17, 4, "💗"),
        (11, 12, 7, "🎀"),
        (15, 16, 2, "🌷"),
        (20, 14, 9, "💕"),
        (25, 18, 1, "🩷"),
        (30, 12, 6, "✨"),
        (35, 15, 3, "🌸"),
        (40, 17, 10, "🦋"),
        (45, 13, 5, "💗"),
        (50, 16, 0, "🌷"),
        (55, 12, 8, "🎀"),
        (60, 18, 2, "💕"),
        (65, 14, 7, "🍑"),
        (70, 15, 4, "✨"),
        (75, 13, 11, "🌸"),
        (80, 17, 1, "💗"),
        (85, 12, 6, "🩷"),
        (90, 16, 3, "🌷"),
        (94, 14, 9, "🦋"),
        (97, 13, 5, "💕"),
        (8, 19, 12, "✨"),
        (28, 20, 8, "🎀"),
        (48, 18, 13, "🍑"),
        (68, 19, 2, "💗"),
        (88, 20, 10, "🌸"),
    ]
)

DECOR = f'<div class="pb-decor" aria-hidden="true">{_DECOR_SPANS}</div>'


def inject() -> None:
    """在页面渲染前调用，注入全局粉色主题与漂浮装饰。"""
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(DECOR, unsafe_allow_html=True)
