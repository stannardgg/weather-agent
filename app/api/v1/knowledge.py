"""【L5】知识库路由（CLAUDE.md §7）。

本层只做三件事：参数校验（Pydantic 完成）、调用 service、包装统一响应。
不含业务规则，也不碰数据库。

**关于后台任务的投递**：重活交给 `services.knowledge_service.process_document`，
这里用 FastAPI 的 `BackgroundTasks` 投递。按 §4 的依赖方向，L5 → L3 是允许的，
所以「谁来调度」这件事放在路由层是合规的（让 L1 的 worker 去 import L3 才是反向依赖）。
"""

from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Query, UploadFile, status

from app.api.deps import CurrentUser, DBSession
from app.schemas.common import Envelope, envelope, paginated
from app.schemas.knowledge import (
    DocumentDetail,
    DocumentOut,
    ReindexResult,
    UploadResult,
)
from app.services import knowledge_service
from app.worker.tasks import run_guarded

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _dispatcher(background: BackgroundTasks):
    """把「投递一个入库任务」这件事包成一个单参数函数传给 service。

    这样 service 不依赖 Web 框架，测试里可以直接传一个同步执行的假函数。
    `run_guarded` 保证后台任务的异常一定被记进日志（见 worker/tasks.py 的说明）。
    """

    def dispatch(document_id: int) -> None:
        background.add_task(
            run_guarded,
            f"process_document:{document_id}",
            knowledge_service.process_document(document_id),
        )

    return dispatch


@router.post(
    "/documents",
    response_model=Envelope[UploadResult],
    status_code=status.HTTP_202_ACCEPTED,
    summary="上传文档（异步入库）",
)
async def upload_document(
    background: BackgroundTasks,
    db: DBSession,
    current_user: CurrentUser,
    file: UploadFile = File(..., description="支持 pdf / docx / txt / md / csv"),
) -> Envelope[Any]:
    """返回 202 而不是 201：文档只是被**接收**了，此刻还在 pending。

    真正的入库在后台跑，前端应轮询 `GET /knowledge/documents/{id}` 看 status 变化。
    """
    content = await knowledge_service.read_upload(file)
    document, deduplicated = await knowledge_service.upload_document(
        db,
        current_user,
        filename=file.filename or "",
        content=content,
        content_type=file.content_type,
        dispatch=_dispatcher(background),
    )
    message = "该文件此前已上传过，直接返回已有记录" if deduplicated else "已接收，正在入库"
    return envelope(UploadResult(document=document, deduplicated=deduplicated), message=message)


@router.get(
    "/documents",
    response_model=Envelope[dict],
    summary="文档列表（分页）",
)
async def list_documents(
    db: DBSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> Envelope[Any]:
    items, total = await knowledge_service.list_documents(
        db, current_user, page=page, page_size=page_size
    )
    return envelope(paginated(items, total, page, page_size))


@router.get(
    "/documents/{document_id}",
    response_model=Envelope[DocumentDetail],
    summary="文档详情（含切分预览）",
)
async def get_document(
    document_id: int, db: DBSession, current_user: CurrentUser
) -> Envelope[Any]:
    detail = await knowledge_service.get_document_detail(db, current_user, document_id)
    return envelope(detail)


@router.put(
    "/documents/{document_id}",
    response_model=Envelope[DocumentOut],
    status_code=status.HTTP_202_ACCEPTED,
    summary="更新文档（覆盖重解析）",
)
async def update_document(
    document_id: int,
    background: BackgroundTasks,
    db: DBSession,
    current_user: CurrentUser,
    file: UploadFile = File(...),
) -> Envelope[Any]:
    content = await knowledge_service.read_upload(file)
    document = await knowledge_service.update_document(
        db,
        current_user,
        document_id,
        filename=file.filename or "",
        content=content,
        content_type=file.content_type,
        dispatch=_dispatcher(background),
    )
    return envelope(document, message="已替换内容，正在重新入库")


@router.delete(
    "/documents/{document_id}",
    response_model=Envelope[None],
    summary="删除文档及其向量",
)
async def delete_document(
    document_id: int, db: DBSession, current_user: CurrentUser
) -> Envelope[Any]:
    await knowledge_service.delete_document(db, current_user, document_id)
    return envelope(None, message="已删除")


@router.post(
    "/reindex",
    response_model=Envelope[ReindexResult],
    status_code=status.HTTP_202_ACCEPTED,
    summary="全量重建索引",
)
async def reindex(
    background: BackgroundTasks, current_user: CurrentUser
) -> Envelope[Any]:
    """换了向量模型或改了切分参数后使用。**不接受任何参数**，全库重算。

    注意这是全库操作（不属于某一个用户），因此只要求登录、不按 user_id 过滤——
    相应地它被设计成「不做任何破坏性动作」：只重新投递，不删别人的数据。
    """
    total, dispatched = await knowledge_service.reindex(
        _dispatcher(background)
    )
    return envelope(
        ReindexResult(total=total, dispatched=dispatched, message="已全部重新投递"),
        message=f"共 {dispatched} 篇文档已重新排队",
    )
