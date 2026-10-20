"""【L1】异步任务运行器（CLAUDE.md §5：worker/ 属基建层）。

## 为什么这里只有「运行器」，没有业务逻辑

§4 规定依赖只能自上而下（L6→L5→L4→L3→L2→L1），L1 可以被动地被任何层引用，
但**不能反向 import L2/L3**。因此「解析→切分→向量化」这条业务链路放在
`services/knowledge_service.py`（L3），本文件只负责「怎么把一段协程安全地放到后台跑」。
谁把两者接起来？`api/v1/knowledge.py`（L5→L3，方向正确）与 `main.py` 的启动钩子。

## 这个运行器解决什么问题

后台任务有个隐蔽的坑：**它抛出的异常没人接**。放在 `BackgroundTasks` 里，
异常会冒到 ASGI 层被记成一条看不懂的日志；用 `asyncio.create_task` 更糟——
任务异常只在 GC 时打印「Task exception was never retrieved」，而且可能永远不打印。
结果是「上传了文档，一直卡在 pending」，日志里什么线索都没有。

所以这里的约定是：**任何后台任务都必须经 `run_guarded` / `schedule_guarded` 启动**，
由它们统一捕获异常、带上任务名打完整堆栈。
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)


async def run_guarded(name: str, coro: Coroutine[Any, Any, Any]) -> None:
    """等待协程完成；失败时记录完整堆栈，**不向外抛**。

    不向外抛是刻意的：FastAPI 的 BackgroundTasks 里抛异常不会影响已经返回给用户的
    响应，只会污染日志；而入库失败这件事应当通过 `documents.status=failed` 呈现给用户，
    业务层自己已经处理过了。运行器只需保证「异常一定被看见」。
    """
    try:
        await coro
    except asyncio.CancelledError:
        # 应用关闭时被取消属于正常流程，不要当成错误
        logger.info("后台任务「%s」被取消（通常是应用正在关闭）", name)
        raise
    except Exception:  # noqa: BLE001 —— 后台任务的兜底必须捕获所有异常
        logger.exception("后台任务「%s」异常退出", name)


def schedule_guarded(name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """在事件循环里调度后台协程，返回 Task（供测试 await 或取消）。

    用于**没有 HTTP 请求上下文**的场景，目前只有一处：应用启动时把上次没跑完的
    入库任务重新投递（M3-Q3 选的进程内后台任务，进程重启会让任务丢失）。
    """
    task = asyncio.create_task(run_guarded(name, coro), name=name)
    logger.info("已调度后台任务「%s」", name)
    return task
