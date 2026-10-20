"""【L5】健康检查（CLAUDE.md §7）。

/health       存活检查：进程还活着就返回 200，用于容器探针与快速自检。
/health/ready 就绪检查：逐个报告下游组件的可用性。
"""

from typing import Any

from fastapi import APIRouter, Response, status

from app.api.deps import DBSession
from app.core.config import settings
from app.crud import health as health_crud
from app.rag.embedder import get_embedder
from app.rag.vectorstore import get_store
from app.schemas.common import Envelope, envelope

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=Envelope[dict[str, Any]], summary="存活检查")
async def health() -> Envelope[dict[str, Any]]:
    return envelope(
        {
            "status": "ok",
            "app": settings.app_name,
            "env": settings.app_env,
            "milestone": "M6",
        }
    )


@router.get("/ready", response_model=Envelope[dict[str, Any]], summary="就绪检查（含下游组件连通性）")
async def ready(response: Response, db: DBSession) -> Envelope[dict[str, Any]]:
    """必需组件全部就绪返回 200，否则返回 503。

    哪些组件算「必需」随里程碑变化：M3 仍只有 database 是必需的。
    vector_store 与 weather 的可用性取决于本地配置，且「知识库/天气不可用」
    不应阻止鉴权等基础功能，因此标记 required=false 且不计入判定。

    注意 vector_store 的探测**只确认能否打开向量库目录，不做任何写入**：
    健康检查被探针高频调用，写入会让它变成一个"有副作用"的接口。
    """
    db_ready = await health_crud.ping(db)
    weather_configured = bool(settings.weather_api_host and settings.weather_api_key)

    vector_ready = False
    vector_note = "未接入"
    try:
        vector_ready = await get_store().heartbeat()
        vector_note = f"已接入（{settings.vector_store_type}），集合 {settings.chroma_collection}"
    except Exception as exc:  # noqa: BLE001 —— 健康检查不能因为某个组件挂了就整体抛错
        vector_note = f"不可用：{type(exc).__name__}: {exc}"

    embedder_ready = False
    embedder_note = "未接入"
    try:
        embedder = get_embedder()
        # 占位向量「能跑」但语义无效，因此 ready 记为 False：让它一眼看出不是可用能力，
        # 而不是让健康检查报一片绿、实际检索结果全是随机的。
        embedder_ready = not embedder.is_placeholder
        embedder_note = (
            f"⚠️ 占位向量（{embedder.name}，{embedder.dimension} 维）：链路可跑通，"
            "但检索无语义，需接真实向量模型"
            if embedder.is_placeholder
            else f"已接入（{embedder.name}，{embedder.dimension} 维）"
        )
    except Exception as exc:  # noqa: BLE001
        embedder_note = f"不可用：{type(exc).__name__}: {exc}"

    components: dict[str, dict[str, Any]] = {
        "database": {
            "configured": True,
            "ready": db_ready,
            "required": True,
            "note": "已接入" if db_ready else "连接失败",
        },
        "vector_store": {
            "configured": bool(settings.vector_store_type),
            "ready": vector_ready,
            "required": False,
            "note": vector_note,
        },
        "embedder": {
            "configured": True,
            "ready": embedder_ready,
            "required": False,
            "note": embedder_note,
        },
        "llm": {
            # 与 core/config.py 的 llm_configured 用同一个判定，避免两处标准不一致
            # （曾经只看 base_url + api_key，漏掉了模型名——而模型名写错同样调不通）
            "configured": settings.llm_configured,
            # 只做「配置是否齐备」的静态判断，**不发起真实调用**：
            # 健康检查会被轮询，每次真调一次 LLM 既费钱又慢，而且把「检查」变成了「消耗」。
            "ready": settings.llm_configured,
            "required": False,
            "note": (
                f"已接入（{settings.llm_model}）"
                if settings.llm_configured
                else "M4 已实现，但未配置 LLM_API_KEY（或 LLM_BASE_URL / LLM_MODEL 有缺）"
            ),
        },
        "weather": {
            "configured": weather_configured,
            "ready": weather_configured,
            "required": False,
            "note": (
                f"已接入（{settings.weather_provider}）"
                if weather_configured
                else "M2 已实现，但未配置 WEATHER_API_HOST / WEATHER_API_KEY"
            ),
        },
    }
    all_required_ready = all(c["ready"] for c in components.values() if c["required"])
    if not all_required_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return envelope(
        {
            "ready": all_required_ready,
            "milestone": "M6",
            "provider": {
                "vector_store": settings.vector_store_type,
                "embedding": settings.embedding_provider,
                "weather": settings.weather_provider,
            },
            "components": components,
        }
    )
