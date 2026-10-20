"""【L5】API v1 路由汇总。

新增模块时在这里挂载 router（CLAUDE.md §5「新增模块的固定点位」第 6 步）：
M3 已挂 knowledge，M4 挂 chat 与 conversations。
"""

from fastapi import APIRouter

from app.api.v1 import auth, chat, conversations, health, knowledge, metrics

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(knowledge.router)
# M4 新增：问答（POST /chat，含 SSE）与会话管理（/conversations 系列）
api_router.include_router(chat.router)
api_router.include_router(conversations.router)
# M6 新增：指标端点（/metrics，Prometheus 文本格式）
api_router.include_router(metrics.router)
