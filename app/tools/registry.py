"""【L3】工具注册表：Agent 能自主调用的能力清单。

## 为什么需要这一层

在它出现之前，「Agent 有哪些工具」是写在 `agent/graph.py` 里的三条硬编码边：
天气走 A 节点、知识库走 B 节点、闲聊直接回答。模型没有选择权——它只是被路由到的
那一支里的文本生成器。加第三个工具要同时改图、改节点、改条件边三处。

注册表把「能力」和「流程」拆开：

- 能力 = 一份 `ToolSpec`（名字 + 给模型看的描述 + JSON Schema + 执行器）
- 流程 = 模型自己决定调哪个、调几次（见 `agent/tool_loop.py`）

这样加工具只需 `register()` 一行，图不用动。

## 为什么工具契约不依赖 LangChain

`ToolSpec.parameters` 用的是**OpenAI function calling 的 JSON Schema 形状**，
而不是 LangChain 的 `@tool` 装饰器。理由有两条：

1. 注册表是纯数据，可以离线单测——不需要构造模型、不需要联网；
2. 换编排框架（LangGraph → 别家）时这一层完全不动。

`to_openai_schema()` 产出的是 `bind_tools()` 能直接吃的结构，适配只发生在边界上。

## 错误处理约定

**执行失败不抛异常，返回 `ok=False`**。工具失败是运行时常态（天气接口超时、
知识库为空、模型编了个不存在的工具名），它应该变成一个「模型能看见并据此调整」
的事实，而不是把整轮对话炸掉。唯一的例外是注册重名——那是编程错误，
越早炸越好。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 只为类型标注，避免本层反向依赖 db 层
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolContext:
    """一次工具调用所需的运行时上下文。

    **刻意不放进 JSON Schema**：`db` 与 `user_id` 是服务端事实，不是模型可以填的参数。
    如果把它们写进工具签名，模型就有机会传一个别人的 `user_id`——那是最典型的越权入口。
    这里的做法是：模型只能填业务参数（城市、日期、检索词），身份由服务端注入。
    """

    db: AsyncSession
    user_id: int


@dataclass(frozen=True)
class ToolResult:
    """一次工具调用的结果。

    - `content`：回填给模型的文本（模型看到的就是它，所以必须自包含、可读）；
    - `data`：给程序用的结构化结果（如 `WeatherResult` / `list[RetrievedChunk]`），
      供 `chat_service` 组装「依据」列表，不参与 prompt；
    - `ok=False` 表示这次调用没成功，`content` 里写的是给模型看的失败原因。
    """

    name: str
    ok: bool
    content: str
    data: Any = None
    error: str | None = None


ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的完整定义。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def to_openai_schema(self) -> dict[str, Any]:
        """转成 OpenAI function calling 的 tools 条目。

        `strict` 不开：它要求模型严格按 schema 输出，部分兼容 OpenAI 协议的服务
        （含本项目默认的 DeepSeek）支持程度不一，开了反而可能整轮失败。
        参数合法性由执行器自己校验——模型不守规矩时返回 ok=False 让它重试。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具集合。进程内单例由 `builtin.build_default_registry()` 提供。"""

    def __init__(self, specs: tuple[ToolSpec, ...] = ()) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        """注册一个工具。重名直接报错——两个工具同名时，模型选哪个是不确定的。"""
        if spec.name in self._specs:
            raise ValueError(f"工具名重复：{spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs.values())

    def openai_schemas(self) -> list[dict[str, Any]]:
        """给 `bind_tools()` 用的工具清单。"""
        return [spec.to_openai_schema() for spec in self._specs.values()]

    async def execute(
        self, name: str, arguments: Any, context: ToolContext
    ) -> ToolResult:
        """按名字执行工具。**任何失败都收敛成 ok=False 的结果**。

        三种失败都要被模型看见并有机会纠正：
        - 工具名不存在（模型幻觉，或提示词里的清单过期）；
        - 参数不是对象（模型把 `{"city": "北京"}` 写成了 `"北京"`）；
        - 执行器内部异常（上游超时、数据库抖动）。
        """
        spec = self._specs.get(name)
        if spec is None:
            available = "、".join(self._specs) or "（无）"
            return ToolResult(
                name=name,
                ok=False,
                content=f"工具「{name}」不存在。当前可用工具：{available}。",
                error="unknown_tool",
            )

        if not isinstance(arguments, dict):
            return ToolResult(
                name=name,
                ok=False,
                content=f"工具「{name}」的参数必须是 JSON 对象，收到的是 {type(arguments).__name__}。",
                error="bad_arguments",
            )

        try:
            return await spec.handler(arguments, context)
        except Exception as exc:  # noqa: BLE001 —— 工具失败必须降级而不是中断对话
            logger.exception("工具「%s」执行失败", name)
            return ToolResult(
                name=name,
                ok=False,
                content=f"工具「{name}」执行时出错：{type(exc).__name__}。可以换一种问法或改用其它信息。",
                error=type(exc).__name__,
            )
