"""【L3】内置工具的具体实现（见 `registry.py` 的契约）。

## 为什么工具只包裹已有的能力，而不重写一遍

`query_weather` 调 `tools/weather.get_weather`，`search_knowledge` 调
`rag/retriever.retrieve`——两者都已有完整实现、重试策略与测试。
工具层只做三件事：**校验模型给的参数、把结果转成模型能读的文本、把结构化数据留下**。
在工具里重新实现一遍业务逻辑，就会出现两套行为不一致的天气解析。

## 描述写得好不好，直接决定工具选得对不对

`description` 是模型唯一的选型依据。写「查询天气」太模糊——模型不知道适用范围；
写清「能查实时与未来预报、需要城市名、日期可省略」之后，它才不会把「今天适合穿什么」
这种需要推理的问题拿去调工具。这一段是提示词，不是注释，改动要看效果而不是看心情。
"""

from __future__ import annotations

from typing import Any

from app.llm import prompts
from app.rag import retriever
from app.tools.registry import ToolContext, ToolRegistry, ToolResult, ToolSpec
from app.tools.weather import WeatherError, get_weather

# 工具名是**对外契约**：模型输出的 tool_calls 里带的就是它，日志与评测集也按它统计。
# 改名等于改契约，评测集里的历史记录会全部失配，所以要改就一次性改干净。
WEATHER_TOOL = "query_weather"
KNOWLEDGE_TOOL = "search_knowledge"

_WEATHER_DESCRIPTION = (
    "查询指定城市的天气：实时天气、未来几天预报、生活指数（穿衣、紫外线、洗车等）。"
    "当用户问气温、下雨、风力、湿度、紫外线、要不要带伞、穿什么衣服时使用。"
    "用户没有说城市时不要调用，先向用户确认城市。"
)

_KNOWLEDGE_DESCRIPTION = (
    "在当前用户上传的公司内部文档、制度、预案、流程中做语义检索，返回原文片段。"
    "当问题涉及公司制度、报销规定、应急预案、操作流程、内部标准时使用。"
    "通用常识、写作、编程、翻译类问题不要使用本工具。"
)


async def _query_weather(arguments: dict[str, Any], _: ToolContext) -> ToolResult:
    """天气工具。参数：`city`（必填）、`date`（可选，YYYY-MM-DD）。"""
    city = str(arguments.get("city") or "").strip()
    if not city:
        # 参数缺失时返回 ok=False 而不是抛异常：模型看到这句话会自己补一次调用，
        # 或者改为向用户询问城市——两条路都比 500 好。
        return ToolResult(
            name=WEATHER_TOOL,
            ok=False,
            content="缺少参数 city（城市名）。请补充城市后重试。",
            error="missing_city",
        )

    raw_date = arguments.get("date")
    # 空字符串统一成 None：模型经常把「不限日期」写成 ""，而 get_weather 把 ""
    # 当成一个非法日期去解析会报错，这种脏数据应当在这里就被吸收掉。
    date = str(raw_date).strip() if raw_date not in (None, "") else None

    try:
        result = await get_weather(city, date)
    except WeatherError as exc:
        # 天气失败是**可预期**的（城市名错、接口超时、配额用尽）。
        # 把原因交给模型，让它如实告诉用户「没查到」，而不是让整轮对话失败。
        return ToolResult(
            name=WEATHER_TOOL,
            ok=False,
            content=f"天气查询失败（{city}）：{exc.message}。请如实告知用户没查到，不要推测天气。",
            error="weather_unavailable",
        )

    return ToolResult(
        name=WEATHER_TOOL,
        ok=True,
        content=prompts.format_weather(result),
        data=result,
    )


async def _search_knowledge(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    """知识库工具。参数：`query`（必填，检索语句）。"""
    query = str(arguments.get("query") or "").strip()
    if not query:
        return ToolResult(
            name=KNOWLEDGE_TOOL,
            ok=False,
            content="缺少参数 query（检索语句）。请补充要检索的内容后重试。",
            error="missing_query",
        )

    chunks = await retriever.retrieve(context.db, query, user_id=context.user_id)
    if not chunks:
        # 检索为空**不是错误**，是一个事实。判断「要不要因此拒绝回答」是 compose
        # 节点的职责（短路逻辑），这里只如实汇报，不替上层做决定。
        return ToolResult(
            name=KNOWLEDGE_TOOL,
            ok=True,
            content="知识库中没有检索到相关内容。",
            data=[],
        )

    return ToolResult(
        name=KNOWLEDGE_TOOL,
        ok=True,
        # 这里用与 RAG 主链路相同的格式化函数，保证「工具模式」与「路由模式」
        # 喂给模型的片段形状完全一致——否则同一份文档在两种模式下答案会不一样。
        content=prompts.format_chunks(chunks, max_chars=10_000),
        data=chunks,
    )


def build_default_registry() -> ToolRegistry:
    """构造默认工具集。

    每次调用返回新实例（而不是模块级单例）：测试要能注册假工具而不污染全局状态，
    而构建成本只是两个 dataclass，可以忽略。
    """
    return ToolRegistry(
        (
            ToolSpec(
                name=WEATHER_TOOL,
                description=_WEATHER_DESCRIPTION,
                parameters={
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                            "description": "城市名，如「北京」「上海市浦东新区」。必须是用户明确提到的城市。",
                        },
                        "date": {
                            "type": "string",
                            "description": (
                                "目标日期，格式 YYYY-MM-DD。查实时天气或用户没提日期时省略；"
                                "「明天」「后天」这类相对说法要先换算成具体日期再传。"
                            ),
                        },
                    },
                    "required": ["city"],
                },
                handler=_query_weather,
            ),
            ToolSpec(
                name=KNOWLEDGE_TOOL,
                description=_KNOWLEDGE_DESCRIPTION,
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "检索语句。用用户问题里的关键术语，不要加「请检索」这类指令词。",
                        },
                    },
                    "required": ["query"],
                },
                handler=_search_knowledge,
            ),
        )
    )
