"""【L4】意图识别（M4 落地，Q4C 选「规则优先 + LLM 兜底」）。

判定顺序（命中即返回，不再往下走）：

    1. 纯问候/道谢（短句且不含任何天气/知识库词）→ chat，来源=rule
    2. 天气词与知识库词同时命中            → mixed，来源=rule
    3. 只命中天气词                        → weather，来源=rule
    4. 只命中知识库词                      → knowledge，来源=rule
    5. 都没命中                            → 交给 LLM 分类，来源=llm
                                             LLM 不可用/失败 → chat，来源=fallback

## 为什么把规则放在前面（而不是让 LLM 每次判断）

三个具体收益，不是「规则更优雅」这种空话：
- **零延迟零成本**：绝大多数问题（「明天上海要带伞吗」）根本不需要为分类多跑一次模型，
  省掉一次往返（约 1 秒）与一份 token。
- **结果是确定的**：同一句话永远得到同一个分类，排查「为什么这次没检索」时不会
  出现「上次还好的啊」这种不可复现的情况。
- **失败面小**：LLM 不可用（密钥没配、限流、超时）时，规则路径仍能覆盖高频问法，
  功能不会整体瘫掉——只有兜底那一档退化成 chat。

## 代价（必须诚实说明）

规则是「按词表匹配」，对没写进词表的表达无能为力（如「出门需要穿外套吗」不含任何
表内词，会落到 LLM 兜底）。所以词表需要随真实提问持续补充，这份词表本身就当配置看待。
更根本的办法是上意图分类模型或让 LLM 全量判断——那属于新的技术决策，须按 §2.4 走流程。
"""

from app.agent.state import Intent, IntentSource
from app.core.logging import get_logger
from app.llm import gateway

logger = get_logger(__name__)

# 天气侧词表。刻意**不用单字**（如「风」「雨」）：单字太容易误伤，
# 「风险」「风口」「雨露均沾」都会被当成天气问题。全部用两字以上的词。
WEATHER_KEYWORDS: tuple[str, ...] = (
    "天气", "气温", "温度", "体感", "多少度", "几度",
    "下雨", "降雨", "降水", "雨量", "暴雨", "大雨", "中雨", "小雨", "阵雨", "雷阵雨",
    "雷暴", "打雷", "下雪", "降雪", "积雪", "冰雹", "大雾", "雾霾", "沙尘", "能见度",
    "风力", "风速", "风级", "大风", "刮风", "台风", "飓风",
    "湿度", "干燥", "潮湿", "紫外线", "气压", "潮汐",
    "带伞", "雨伞", "穿什么", "穿多少", "冷不冷", "热不热",
    "降温", "升温", "温差", "天气预报", "预报", "阴天", "多云", "晴天", "日出", "日落", "穿衣",
)

# 知识库侧词表。「预警」刻意**不放进任何一侧**：它在「橙色预警对应什么处置流程」
# 里属于制度语境，在「暴雨橙色预警」里属于天气语境，放进任一侧都会把另一侧判错；
# 留给两侧各自的其它词去决定，判不出来时由 LLM 兜底——这才是兜底该干的活。
KNOWLEDGE_KEYWORDS: tuple[str, ...] = (
    "预案", "应急预案", "制度", "规定", "流程", "规范", "标准", "办法", "政策",
    "手册", "指引", "文档", "知识库", "条款", "条例", "细则", "通知",
    "报销", "差旅", "住宿", "补贴", "审批", "权限", "合规", "审计", "保密",
    "应急", "响应", "分级", "级别", "部室", "岗位", "职责",
    "培训", "考核", "绩效", "招聘", "入职", "离职", "薪资", "工资", "福利",
    "年假", "考勤", "采购", "合同", "工单", "运维", "客服", "话术", "报价", "资质",
)

# 纯问候/道谢：短句 + 含问候词 + 不含任何业务词 → 直接判 chat，不必花一次 LLM 调用。
# 「谢谢」这类收尾语很常见，每次都交给模型分类是纯浪费。
GREETING_KEYWORDS: tuple[str, ...] = (
    "你好", "您好", "hi", "hello", "嗨", "哈喽", "早上好", "中午好", "下午好", "晚上好",
    "在吗", "在么", "谢谢", "多谢", "感谢", "再见", "拜拜", "辛苦了",
)
_GREETING_MAX_CHARS = 12  # 超过这个长度就不当纯问候（如「你好，帮我看看明天上海天气」）


def _hits(text: str, keywords: tuple[str, ...]) -> list[str]:
    """命中的关键词（返回命中项而非计数，便于日志里说清「凭哪些词判的」）。"""
    return [word for word in keywords if word in text]


def _is_greeting(text: str, lowered: str) -> bool:
    if len(text) > _GREETING_MAX_CHARS:
        return False
    if not any(word in lowered for word in GREETING_KEYWORDS):
        return False
    # 含业务词就不算纯问候——「你好，明天上海要带伞吗」显然是天气问题
    return not _hits(lowered, WEATHER_KEYWORDS) and not _hits(lowered, KNOWLEDGE_KEYWORDS)


def classify_by_rules(question: str) -> tuple[Intent, list[str], list[str]] | None:
    """规则分类。判不出来时返回 None（表示「该交给 LLM 了」）。

    第二个返回值是命中的天气词，第三个是命中的知识库词——返回它们而不是只返回
    一个枚举，是为了让日志能记下「凭什么这么判」。调词表时，没有这两个列表就只能靠猜。
    """
    text = (question or "").strip()
    if not text:
        return None

    # 英文问候（hi / hello）不区分大小写，中文无所谓，统一转小写更省心
    lowered = text.lower()

    weather_hits = _hits(lowered, WEATHER_KEYWORDS)
    knowledge_hits = _hits(lowered, KNOWLEDGE_KEYWORDS)

    if not weather_hits and not knowledge_hits:
        if _is_greeting(text, lowered):
            return Intent.CHAT, [], []
        return None

    if weather_hits and knowledge_hits:
        return Intent.MIXED, weather_hits, knowledge_hits
    if weather_hits:
        return Intent.WEATHER, weather_hits, knowledge_hits
    return Intent.KNOWLEDGE, weather_hits, knowledge_hits


def _parse_intent(raw: str) -> Intent | None:
    """把模型返回的文本解析成枚举。

    先看开头（正常情况下模型只回一个词），再全文兜底找一次——模型常会加上
    「分类：」「答案：」这类前缀或句号，严格只认整串相等会让兜底路径形同虚设。
    """
    text = (raw or "").strip().lower().strip("。.!！,，:：\"'“”")
    if not text:
        return None
    for intent in Intent:
        if text.startswith(intent.value):
            return intent
    for intent in Intent:
        if intent.value in text:
            return intent
    return None


async def classify(question: str) -> tuple[Intent, IntentSource]:
    """意图识别总入口：规则优先，未命中走 LLM，LLM 不可用则退回 chat。

    **刻意不抛异常**：意图识别失败不该让整次问答失败。判不出来时退回 chat
    仍然能正常回答（只是不检索、不查天气），比直接 500 好得多。
    """
    rule_result = classify_by_rules(question)
    if rule_result is not None:
        intent, weather_hits, knowledge_hits = rule_result
        logger.info(
            "意图识别[规则] %s 天气词=%s 知识库词=%s",
            intent.value,
            weather_hits,
            knowledge_hits,
        )
        return intent, IntentSource.RULE

    from langchain_core.messages import HumanMessage

    from app.llm.prompts import INTENT_PROMPT

    try:
        raw, _usage = await gateway.ainvoke_text(
            [HumanMessage(content=INTENT_PROMPT.format(question=question))]
        )
    except Exception as exc:  # noqa: BLE001 —— 任何失败都退回兜底，不让分类拖垮问答
        # LLMGatewayError 是「配置/上游」问题，这里只记 warning 不抛出：
        # 用户问的是一句闲聊，不该因为没配密钥就收到 503。
        logger.warning("意图识别[LLM] 调用失败，退回 chat：%s", exc)
        return Intent.CHAT, IntentSource.FALLBACK

    parsed = _parse_intent(raw)
    if parsed is None:
        logger.warning("意图识别[LLM] 返回无法解析的内容 %r，退回 chat", raw[:80])
        return Intent.CHAT, IntentSource.FALLBACK
    logger.info("意图识别[LLM] %s（原始返回 %r）", parsed.value, raw[:40])
    return parsed, IntentSource.LLM
