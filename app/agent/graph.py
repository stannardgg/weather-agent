"""【L4】LangGraph 状态机定义（M4 落地，Q1B 选「意图路由 + 分支节点」）。

## 图长什么样

    START
      │
      ▼
    classify_intent ──┬── weather ──► fetch_weather ──┬── mixed ──► retrieve_knowledge ─┐
                      │                               └── 其它 ─────────────────────────┐│
                      ├── knowledge ────────────────► retrieve_knowledge ──────────────┤│
                      ├── chat ───────────────────────────────────────────────────────►┤│
                      │                                                                ││
                      └── tool_loop（AGENT_MODE=tool_loop 且非闲聊）──────────────────►┤│
                                                                                       ▼▼
                                                                                     compose
                                                                                       │
                                            ┌──────────────────────────────────────────┤
                                            │ short_circuit=True                       │ False
                                            ▼                                          ▼
                                           END                                     generate
                                                                                       │
                                                                                       ▼
                                                                                      END

`fetch_weather` / `retrieve_knowledge`（router 模式）与 `tool_loop`（模型自主选工具）
是**并列的取数方式**，两条路最终都汇合到 compose。切哪个由 `AGENT_MODE` 决定，
下游的短路、prompt 组装、流式生成完全共用——这也是新增一条路径的风险被压到最低的原因。

（`compose` 之后的分叉是「防幻觉短路」：需要知识库却检索为空 / 只问天气却取数失败时，
答案已经定好，直接结束，**模型根本没有机会编**。理由见 nodes.compose 的注释。）

## 为什么不用 checkpointer 存图状态

LangGraph 自带的 Checkpointer 能把每一步状态存进数据库、支持断点续跑。
本项目**刻意不用**：多轮上下文的权威来源是 `messages` 表（需求 C5「会话历史持久化」），
用户要能在 `GET /conversations/{id}/messages` 里看到完整历史。
再引入一套图状态存储，就变成两份「对话历史」需要同步——两边不一致时以谁为准是个
无解的扯皮问题。图的每次调用都是无状态的：历史由服务层从库里读出来、当作输入塞进去。

## 为什么编译后缓存

`compile()` 会做一遍图结构校验（可达性、条件边映射完整性）。放在模块级只做一次，
既省掉每次请求的开销，也让「图接错了」在**第一次问答**就暴露，而不是等到某条冷门分支。
"""

from typing import Any

from app.agent import nodes, tool_loop
from app.agent.nodes import (
    NODE_CLASSIFY,
    NODE_COMPOSE,
    NODE_GENERATE,
    NODE_GUARD,
    NODE_RETRIEVE,
    NODE_TOOL_LOOP,
    NODE_WEATHER,
    ROUTE_BLOCKED,
    ROUTE_CONTINUE,
    ROUTE_SHORT_CIRCUIT,
)
from app.agent.state import AgentState

_graph: Any = None


def build_graph():
    """搭出并编译状态机。测试要断言图结构（节点名/边）时调它。"""
    # 延迟导入：langgraph 会拉进 langchain-core 的一大串子模块，
    # 放模块顶层会让应用启动慢数百毫秒（§11 第 11 条）。
    from langgraph.graph import END, START, StateGraph
    builder = StateGraph(AgentState)

    builder.add_node(NODE_GUARD, nodes.guard_input)
    builder.add_node(NODE_CLASSIFY, nodes.classify_intent)
    builder.add_node(NODE_WEATHER, nodes.fetch_weather)
    builder.add_node(NODE_RETRIEVE, nodes.retrieve_knowledge)
    builder.add_node(NODE_TOOL_LOOP, tool_loop.run_tool_loop)
    builder.add_node(NODE_COMPOSE, nodes.compose)
    builder.add_node(NODE_GENERATE, nodes.generate)

    # 入口先过护栏：命中劫持句式直接 END，请求不进模型（C 阶段）
    builder.add_edge(START, NODE_GUARD)
    builder.add_conditional_edges(
        NODE_GUARD,
        nodes.route_after_guard,
        {ROUTE_BLOCKED: END, ROUTE_CONTINUE: NODE_CLASSIFY},
    )

    # 入口分流：三种意图各走各的（chat 直接去组装，不查任何东西）。
    # AGENT_MODE=tool_loop 时，需要外部数据的三类意图统一走 NODE_TOOL_LOOP。
    builder.add_conditional_edges(
        NODE_CLASSIFY,
        nodes.route_after_classify,
        [NODE_WEATHER, NODE_RETRIEVE, NODE_COMPOSE, NODE_TOOL_LOOP],
    )
    # 查完天气后的第二段分流：只有混合意图还要继续去检索
    builder.add_conditional_edges(
        NODE_WEATHER,
        nodes.route_after_weather,
        [NODE_RETRIEVE, NODE_COMPOSE],
    )
    builder.add_edge(NODE_RETRIEVE, NODE_COMPOSE)
    # 工具循环只负责取数，取完同样汇合到 compose —— 短路与流式都复用同一条路
    builder.add_edge(NODE_TOOL_LOOP, NODE_COMPOSE)

    # 短路判断：答案已定就结束，否则去生成
    builder.add_conditional_edges(
        NODE_COMPOSE,
        nodes.route_after_compose,
        {ROUTE_SHORT_CIRCUIT: END, NODE_GENERATE: NODE_GENERATE},
    )
    builder.add_edge(NODE_GENERATE, END)

    return builder.compile()


def get_graph():
    """取编译好的图（单例）。"""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def reset_graph() -> None:
    """清空缓存的图（测试改过节点实现后需要重新编译）。"""
    global _graph
    _graph = None
