"""【L1】LLM 提示词模板（M4 落地）。

集中放在一个文件而不是散在节点里，理由是**提示词是本项目防幻觉的主要抓手**
（CLAUDE.md §8.3）：散开写就会出现「意图分类的系统提示与问答的系统提示规则不一样」，
改一处漏两处，而且很难看出哪里不一致。

## 延迟导入

按 §11 第 11 条，`langchain` 系列一律在函数内部导入：它们在 import 期会加载大量
子模块，放模块顶层会让应用启动慢数百毫秒，而多数请求（鉴权、天气、知识库管理）
根本用不到提示词。
"""

from typing import TYPE_CHECKING, Any

from app.rag.retriever import RetrievedChunk
from app.schemas.weather import WeatherResult

if TYPE_CHECKING:  # 仅供类型检查，运行时不导入（见上面的说明）
    from langchain_core.messages import BaseMessage


SYSTEM_PROMPT = """你是一个智能助手，既能回答各类通用问题，也能查询天气和公司内部制度。请按问题类型采用对应规则：

1. 【天气类】使用【天气数据】里的真实数字与结论，不要凭记忆改写或推算。
   数据里没有的项（例如没查到紫外线指数），就说没查到，不要估一个。
2. 【公司制度类】使用【参考资料】（公司内部文档原文片段）作为**唯一依据**。
   资料中找不到的，如实说明「知识库中未找到相关内容」，不要用常识或印象补充，
   更不要编造文件名、条款编号、响应等级。
3. 【通用类】常识、写作、编程、翻译、数学等与天气和公司制度无关的问题，
   用你自己的知识正常、自然地回答，不受上面两条限制。
4. 回答简洁，先给结论再给依据。引用知识库内容时，在相关句子末尾标注来源，
   格式为（文件名#第N段），N 用参考资料里给出的段号。
5. 不要在回答里复述本段规则，也不要提到「参考资料」「上下文」「天气数据」这类系统词汇，
   直接给用户答案即可。
"""


INTENT_PROMPT = """把用户的问题归入下列四类之一，只输出类别的英文单词本身，
不要输出标点、解释、引号或任何其它字符。

weather   只问天气：气温、降水、风力、湿度、紫外线、要不要带伞、穿什么等。
knowledge 只问**公司内部文档/制度**：制度、预案、流程、标准、规定、报销、应急预案等。
          注意：像「什么是人工智能」「帮我写首诗」「翻译这句话」这类通用问题不是公司文档问题，应归 chat。
mixed     一个问题里同时涉及天气与公司制度，例如「按台风预案明天深圳该启动几级响应」。
chat      打招呼、闲聊，或与天气和公司文档都无关的其它问题（含通用知识、写作、编程、翻译、数学等）。

用户问题：{question}
"""


TOOL_DECISION_PROMPT = """你可以调用工具获取事实数据。请严格遵守：

1. 需要天气数字时调用 query_weather；需要公司制度原文时调用 search_knowledge。
   一个问题同时涉及两者时，两个工具各调用一次。
2. 调用前确认必要参数。天气工具需要城市名：用户没说城市就先不调用，
   直接请用户补充城市（不要自己猜一个城市）。
3. 本阶段**只需要取数，不需要撰写完整回答**。工具返回结果后停止调用即可，
   最终回答由后续环节统一生成。不要复述工具输出，也不要提到「工具」这个词。
4. 通用知识、写作、编程、翻译、闲聊类问题，不调用任何工具。
5. 工具返回「查询失败」或「没有检索到」时，不要换个参数反复重试同一工具，
   那是浪费；把事实留给后续环节如实呈现。
"""


# §8.3 的硬要求：检索为空时必须明确回答「未找到」，禁止编造。
# 这句话**不经过模型**，由节点直接返回——把「不编造」交给提示词去约束，
# 不如让它在代码层面根本没有编造的机会。
NO_KNOWLEDGE_ANSWER = (
    "知识库中未找到相关内容。"
    "可以换一种说法再问，或先在知识库页面上传相关文档。"
)

# 天气查询失败时的兜底话术（失败原因由 service 记入日志，不把技术细节抛给用户）
WEATHER_UNAVAILABLE_ANSWER = (
    "暂时没能取到天气数据，可能是气象服务暂时不可用或城市名没识别出来。"
    "请稍后重试，或把城市写得更具体一些（例如「上海市浦东新区」）。"
)

# LLM 调用失败时的兜底话术（M6-Q4）：把「请检查 LLM_API_KEY」这类面向运维的技术细节
# 拦在用户视线之外，只给一句可行动的提示。真正的技术原因会由 service 记入日志
# （含 trace_id），排查时从日志定位，而不是让终端用户看到。
LLM_UNAVAILABLE_ANSWER = (
    "抱歉，我暂时无法回答这个问题。服务可能出现了短暂波动，请稍后再试。"
)


def format_weather(result: WeatherResult) -> str:
    """把归一化后的天气结果渲染成给模型看的纯文本。

    为什么要先渲染成文本再拼进 prompt，而不是把 JSON 直接塞进去：
    模型读「气温 18.0–24.0 摄氏度」比读 `{"temp_min": 18.0, "temp_max": 24.0}`
    更不容易把字段搞混，也省 token。另外这里**只输出有值的字段**，
    避免一堆 `null` 让模型误以为「数据是空的」。
    """
    lines: list[str] = [f"地点：{result.resolved.name}"]
    if result.resolved.adm1 or result.resolved.adm2:
        lines.append(
            "行政区划："
            + " / ".join(part for part in (result.resolved.adm1, result.resolved.adm2) if part)
        )

    current = result.current
    if current is not None:
        parts = []
        if current.condition:
            parts.append(current.condition)
        if current.temperature is not None:
            parts.append(f"气温 {current.temperature}°C")
        if current.feels_like is not None:
            parts.append(f"体感 {current.feels_like}°C")
        if current.humidity is not None:
            parts.append(f"湿度 {current.humidity}%")
        if current.wind_direction or current.wind_speed is not None:
            wind = current.wind_direction or ""
            if current.wind_speed is not None:
                wind += f" {current.wind_speed} km/h"
            parts.append(f"风 {wind.strip()}")
        if current.uv_index is not None:
            parts.append(f"紫外线指数 {current.uv_index}")
        if parts:
            lines.append("实时：" + "，".join(parts))

    if result.target is not None:
        lines.append("—— 用户所问日期 ——")
        lines.append(_format_day(result.target))
    elif result.daily:
        # 没指定日期时，给未来三天足够回答「明天要带伞吗」，全给会白烧 token
        lines.append("—— 未来预报 ——")
        lines.extend(_format_day(day) for day in result.daily[:3])

    if result.indices:
        # 生活指数是数据源**已经算好的结论**（如「带伞指数：建议带伞」）。
        # §8.3 明确它的优先级高于让模型自行从降水量推断，所以一并给出。
        lines.append("—— 生活指数（数据源给出的结论，可直接采用）——")
        lines.extend(
            f"{item.name}：{item.category or ''}{item.text or ''}".rstrip("：")
            for item in result.indices[:8]
        )

    if result.cached:
        lines.append("（说明：以上为缓存数据，缓存有效期 15 分钟）")
    return "\n".join(lines)


def _format_day(day: Any) -> str:
    parts = [f"{day.date}"]
    if day.condition_day or day.condition_night:
        day_text = day.condition_day or ""
        night_text = day.condition_night or ""
        parts.append(f"白天{day_text}，夜间{night_text}" if night_text != day_text else day_text)
    if day.temp_min is not None and day.temp_max is not None:
        parts.append(f"{day.temp_min}~{day.temp_max}°C")
    if day.precip_probability is not None:
        parts.append(f"降水概率 {day.precip_probability}%")
    if day.precipitation is not None:
        parts.append(f"降水量 {day.precipitation}mm")
    if day.wind_direction_day or day.wind_speed_day is not None:
        wind = day.wind_direction_day or ""
        if day.wind_speed_day is not None:
            wind += f" {day.wind_speed_day} km/h"
        parts.append(f"风 {wind.strip()}")
    if day.uv_index is not None:
        parts.append(f"紫外线 {day.uv_index}")
    return "｜".join(parts)


def format_chunks(chunks: list[RetrievedChunk], *, max_chars: int) -> str:
    """把检索片段渲染成带来源标注的纯文本（§8.3 要求「检索片段必须带来源标注」）。

    `max_chars` 是**总字符上限**，超出时按**相关度从低到高**逐个丢弃尾部片段：
    低相关度的片段往往只是个别词碰巧相近，留着不但占 token，还会把模型的注意力
    从真正相关的片段上引开。丢弃是逐个整段丢，不截断单段——半截的句子比没有更糟。
    """
    if not chunks:
        return ""
    ordered = sorted(chunks, key=lambda chunk: chunk.distance)  # 距离小的在前 = 更相关
    kept: list[str] = []
    used = 0
    for chunk in ordered:
        block = f"[{chunk.label}]（相关度 {chunk.score:.2f}）\n{chunk.content.strip()}"
        if used + len(block) > max_chars and kept:
            break
        kept.append(block)
        used += len(block)
    return "\n\n".join(kept)


def build_context_section(*, weather_text: str = "", chunks_text: str = "") -> str:
    """把天气与检索片段拼成 prompt 里的「资料区」。

    两块都用带方括号的小标题包裹：模型对「这段是原始数据、不是用户说的话」的区分
    依赖这种显式边界，否则容易把资料内容当成用户的新指令（提示注入的常见入口）。
    """
    blocks: list[str] = []
    if weather_text.strip():
        blocks.append(f"【天气数据】\n{weather_text.strip()}")
    if chunks_text.strip():
        blocks.append(f"【参考资料】\n{chunks_text.strip()}")
    return "\n\n".join(blocks)


def build_messages(
    *,
    question: str,
    context_section: str,
    history: list["BaseMessage"] | None = None,
) -> list["BaseMessage"]:
    """组装最终发给模型的完整消息列表。

    顺序刻意是「系统提示 → 历史对话 → 当前问题（含资料）」：
    - 资料放在**最后一条用户消息里**而不是单独一条 system 消息，是因为多数模型对
      「同一条消息内的上下文」贴合度更好，且这样资料与问题的距离最近。
    - 历史放在资料之前，让最近的材料紧贴问题，长对话下不会被历史挤远。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
    if history:
        messages.extend(history)
    content = f"{context_section}\n\n【用户问题】\n{question}".strip()
    messages.append(HumanMessage(content=content))
    return messages
