"""【L4】Agent 状态：LangGraph 各节点之间传递的那一个字典（M4 落地，Q1B）。

## 为什么用 TypedDict 而不是 dataclass

LangGraph 的状态要求在节点之间「部分更新」——每个节点只返回它改动的键，
框架负责合并。TypedDict + `total=False` 正好表达这个语义：键都可缺省，
节点返回什么就覆盖什么。用 dataclass 的话每个节点都得自己 copy 一份完整对象，
既啰嗦又容易漏字段。

## 分层归属说明（重要）

`AgentState` 里的 `db` 字段看起来像是「L4 碰了数据库」，实际不是：
它只是一个**句柄**，由 service 层放进来，再由检索节点原样传给 L3 的
`rag/retriever.py`。L4 自己不写任何 SQL、也不 import `crud/`——
数据库访问仍然只发生在 L2（§4 第 3 条、§11）。
之所以放在 state 里而不是靠全局单例，是为了让「这次问答用哪个数据库会话」
显式可见：测试与后台任务各自有各自的会话，全局变量会让它们互相串。
"""

from enum import Enum
from typing import Any, TypedDict

from app.rag.retriever import RetrievedChunk
from app.schemas.weather import WeatherResult


class Intent(str, Enum):
    """用户意图。Q1B 的分支节点就是按这个值分流的。"""

    WEATHER = "weather"  # 只问天气
    KNOWLEDGE = "knowledge"  # 只问公司文档
    MIXED = "mixed"  # 同时需要天气与文档（需求 C3）
    CHAT = "chat"  # 打招呼、闲聊、与两者都无关


class IntentSource(str, Enum):
    """意图是谁判的。

    单独记下来不是为了好看：规则命中的准确率与 LLM 兜底的准确率差很多，
    出问题时要能一眼看出「这次是规则判错了还是模型判错了」，否则调优无从下手。
    """

    RULE = "rule"  # 关键词规则命中（Q4C 的主路径）
    LLM = "llm"  # 规则未命中，交给模型分类（Q4C 的兜底路径）
    FALLBACK = "fallback"  # 模型也没法用（未配置/失败），退回闲聊处理


INTENT_LABELS: dict[str, str] = {
    Intent.WEATHER.value: "天气",
    Intent.KNOWLEDGE.value: "知识库",
    Intent.MIXED.value: "天气+知识库",
    Intent.CHAT.value: "闲聊",
}

# 需要走向量检索的意图（§8.2 的「条件触发检索」）
RETRIEVAL_INTENTS = (Intent.KNOWLEDGE.value, Intent.MIXED.value)
# 需要调天气工具的意图
WEATHER_INTENTS = (Intent.WEATHER.value, Intent.MIXED.value)


class AgentState(TypedDict, total=False):
    """节点间传递的状态。字段按「输入 → 中间 → 输出」三段排列。

    ## 为什么消息字段标成 `list[Any]` 而不是 `list[BaseMessage]`

    LangGraph 在 `StateGraph(AgentState)` 构造时会对整个 schema 调
    `typing.get_type_hints()`，**强制在运行时求值每一个注解**。于是
    `history: "list[BaseMessage]"` 这种写法会直接 `NameError`——`BaseMessage`
    只在 `TYPE_CHECKING` 下导入，运行时并不存在。（这一点与普通函数注解不同：
    函数注解里的字符串不会被求值，所以 `prompts.build_messages` 那样写没问题。）

    两个选择：
    - 把 `langchain_core.messages` 提到模块顶层 → 实测首次导入 **371ms**，
      而这个模块会被 API 层一路 import 到，等于给**每次冷启动**都加上这份开销，
      为了一个类型标注不值得；
    - 标成 `Any` 并在此说明实际类型 → 零开销，代价是编辑器不再提示字段类型。

    选了后者。字段里放的确实是 `BaseMessage` 对象（`SystemMessage` / `HumanMessage` /
    `AIMessage`），由 `services/chat_service.py` 与 `llm/prompts.build_messages()` 生产。
    """

    # --- 输入（由 services/chat_service.py 填）---
    question: str
    history: list[Any] | None  # 实际的 list[BaseMessage]，见类说明
    user_id: int
    db: Any  # AsyncSession，仅用于传递给 L3 检索器
    city: str | None  # 调用方显式指定时优先于从问题里抽
    date: str | None  # 同上，格式 YYYY-MM-DD

    # --- 中间产物 ---
    intent: str
    intent_source: str
    weather: WeatherResult | None
    chunks: list[RetrievedChunk]
    messages: list[Any]  # 实际的 list[BaseMessage]，见类说明

    # --- 输出 ---
    answer: str
    usage: dict[str, int]
    short_circuit: bool  # True = 答案已定，不再调模型（防幻觉的硬保证）
    notes: list[str]  # 给日志/前端看的过程信息（如「天气取数失败」）
