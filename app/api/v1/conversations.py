"""【L5】会话路由（CLAUDE.md §7 的补充约定：会话系列放在新增的 conversations.py）。

三个端点：会话列表、历史消息、删除会话。与 `chat.py` 分开成两个文件，
是为了让「问答」与「会话管理」各自独立——它们的鉴权要求相同，但改动节奏完全不同
（问答会随 Agent 编排频繁调整，会话管理基本定型）。

用户隔离由 `crud/conversation.py` 强制保证：所有按 id 的查询都带 `user_id`，
且「不存在」与「属于别人」返回同一个 404，不给出可区分的错误（否则就成了
枚举别人会话 ID 的探测接口）。
"""

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DBSession
from app.crud import conversation as conversation_crud
from app.core.exceptions import NotFoundError
from app.schemas.chat import (
    ConversationMessagesOut,
    ConversationOut,
    MessageOut,
)
from app.schemas.common import Envelope, envelope, paginated
from app.services import audit_service

router = APIRouter(prefix="/conversations", tags=["conversations"])

MESSAGE_PAGE_SIZE_MAX = 200


@router.get("", response_model=Envelope[dict], summary="会话列表（分页）")
async def list_conversations(
    db: DBSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> Envelope[dict]:
    """按最近活跃倒序返回当前用户的会话。"""
    total = await conversation_crud.count_conversations(db, current_user.id)
    rows = await conversation_crud.list_conversations(
        db,
        current_user.id,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    items = [ConversationOut.model_validate(row) for row in rows]
    return envelope(paginated(items, total, page, page_size))


@router.get("/search", response_model=Envelope[dict], summary="搜索会话（标题或聊天内容）")
async def search_conversations(
    db: DBSession,
    current_user: CurrentUser,
    q: str = Query(..., min_length=1, max_length=100, description="搜索关键词"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> Envelope[dict]:
    """按关键词搜索当前用户的会话。

    命中规则：**会话标题包含关键词，或该会话下任一消息内容包含关键词**。

    ⚠️ 这个端点必须声明在 `/{conversation_id}` 之前：FastAPI 按声明顺序匹配，
    路径里的 `search` 若排在后面会被当成 `conversation_id`（int 转换失败 → 422）。
    """
    keyword = q.strip()
    if not keyword:
        # 纯空白关键词返回空结果，而不是让 LIKE '%%' 匹配全部——「搜空串」语义上是空
        return envelope(paginated([], 0, page, page_size))
    total = await conversation_crud.count_search_conversations(db, current_user.id, keyword)
    rows = await conversation_crud.search_conversations(
        db,
        current_user.id,
        keyword,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    items = [ConversationOut.model_validate(row) for row in rows]
    return envelope(paginated(items, total, page, page_size))


@router.get(
    "/{conversation_id}/messages",
    response_model=Envelope[ConversationMessagesOut],
    summary="历史消息（分页，page=1 为最新一页）",
)
async def list_messages(
    conversation_id: int,
    db: DBSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=MESSAGE_PAGE_SIZE_MAX),
) -> Envelope[ConversationMessagesOut]:
    """取某会话的历史消息。

    **page=1 是最新的一页**，与普通列表接口相反——聊天界面的惯例是先看到最近说了什么
    （`crud.list_messages_page` 里有完整说明）。
    """
    conversation = await conversation_crud.get_conversation(db, conversation_id, current_user.id)
    if conversation is None:
        raise NotFoundError(f"会话不存在：{conversation_id}")

    total = await conversation_crud.count_messages(db, conversation_id)
    rows = await conversation_crud.list_messages_page(
        db, conversation_id, page=page, page_size=page_size
    )
    return envelope(
        ConversationMessagesOut(
            conversation=ConversationOut.model_validate(conversation),
            items=[MessageOut.model_validate(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )
    )


@router.delete(
    "/{conversation_id}",
    response_model=Envelope[dict],
    status_code=status.HTTP_200_OK,
    summary="删除会话（连同其消息）",
)
async def delete_conversation(
    conversation_id: int, db: DBSession, current_user: CurrentUser
) -> Envelope[dict]:
    """物理删除会话。

    消息随会话一起删除：`Conversation.messages` 声明了 `cascade="all, delete-orphan"`，
    数据库外键也有 `ondelete="CASCADE"` 作双保险（§6）。
    """
    conversation = await conversation_crud.get_conversation(db, conversation_id, current_user.id)
    if conversation is None:
        raise NotFoundError(f"会话不存在：{conversation_id}")
    await conversation_crud.delete_conversation(db, conversation)
    await audit_service.record(
        db,
        action="delete_conversation",
        user_id=current_user.id,
        target_type="conversation",
        target_id=conversation_id,
        detail={"title": conversation.title},
    )
    return envelope(None, message="会话已删除")
