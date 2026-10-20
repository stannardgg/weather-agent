"""【L2】会话与消息的数据访问 —— 数据库的唯一出口（CLAUDE.md §11）。

表模型 M1 就建好了（`models/conversation.py`），M4 的 Agent 开始真正往里写。

## 与 `crud/document.py` 一致的三条约定

1. **所有按 id 的查询都必须带 `user_id`**。会话属于用户（需求 C5「按用户隔离」），
   用 `db.get(Conversation, id)` 会绕过这条过滤，所以一律显式 `select(...).where(...)`。
2. **「不存在」与「属于别人」返回同一个 None**，由上层统一转成 404，
   不给出可区分的错误——否则就成了一个「枚举别人会话 ID」的探测接口。
3. **写操作在函数内部 commit**（事务边界在 crud 内），与 `crud/user.py`、`crud/document.py` 一致。

## 一个容易漏的点：加消息要顺手更新会话的 `updated_at`

`conversations.updated_at` 靠 `onupdate=func.now()` 自动维护，但那**只在会话这一行被
UPDATE 时才触发**。新增一条消息并不会碰会话行，于是「会话列表按最近活跃排序」会失效——
列表顺序永远停在各会话的创建时刻。所以 `add_message` 里显式把父会话的 `updated_at`
推一次。这是那种「不报错、只是顺序不对」的静默问题。

⚠️ 顺带记一个精度限制：SQLite 的 `CURRENT_TIMESTAMP` 只精确到**秒**。同一秒内创建的两个
会话时间戳完全相同，排序会退化成按 id 兜底。日常使用（每次提问间隔远超一秒）不受影响，
但写测试时必须把参照对象的时间戳挪到过去才能观察到差异。
"""

# 同 crud/document.py：自定义函数名不能与 SQLAlchemy 的同名符号撞车（§11 硬约定第 13 条）
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message

# 消息角色取值（§6 定稿）：user / assistant / tool
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"

DEFAULT_TITLE = "新会话"
# 首条消息落库后，用它的前若干字当标题（§7 约定：截取前 20 字）
TITLE_MAX_CHARS = 20


def _escape_like(value: str) -> str:
    """转义 LIKE 的通配符。

    不转义的话，用户搜一个 `%` 就等于「匹配任意字符串」，搜索会退化成「返回全部会话」；
    搜 `_` 则变成「匹配任一单字」。反斜杠本身也要先转义，否则 `\\%` 这类输入会乱。
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def create_conversation(
    db: AsyncSession, user_id: int, title: str = DEFAULT_TITLE
) -> Conversation:
    conversation = Conversation(user_id=user_id, title=title)
    db.add(conversation)
    await db.commit()
    # 刷新以拿到数据库生成的时间戳与自增 id（expire_on_commit=False，所以必须显式 refresh）
    await db.refresh(conversation)
    return conversation


async def get_conversation(
    db: AsyncSession, conversation_id: int, user_id: int
) -> Conversation | None:
    """按 id 取会话，且必须属于该用户。取不到区分不了「不存在」还是「别人的」。"""
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    )
    return result.scalar_one_or_none()


async def get_conversation_with_messages(
    db: AsyncSession, conversation_id: int, user_id: int
) -> Conversation | None:
    """连消息一起取回（`Conversation.messages` 声明了 `lazy="selectin"`，会自动加载）。

    保留这个独立函数是为了让「我要不要消息」在调用点上看得很清楚——
    列表接口只需要会话本身，带上全部消息会让响应体随对话长度无限膨胀。
    """
    return await get_conversation(db, conversation_id, user_id)


async def list_conversations(
    db: AsyncSession, user_id: int, *, offset: int = 0, limit: int = 20
) -> list[Conversation]:
    """按最近活跃倒序列出会话（`updated_at` 由 `add_message` 维护，见模块说明）。"""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_conversations(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Conversation)
        .where(Conversation.user_id == user_id)
    )
    return int(result.scalar_one())


async def search_conversations(
    db: AsyncSession, user_id: int, query: str, *, offset: int = 0, limit: int = 20
) -> list[Conversation]:
    """按关键词搜会话：**标题命中，或该会话下任一消息内容命中**即算匹配。

    命中规则做成「标题 OR 内容」而不是只搜标题：用户记得的往往是「聊过什么」，
    而不是被自动截断的标题。子查询先圈出「有消息命中」的会话 id，再用它去筛会话，
    避免把每条消息都 JOIN 出来造成重复行。

    为什么用 LIKE 而不是全文索引：SQLite 没有内置全文索引（FTS5 需要额外建虚表并
    保持同步），而本项目是「单用户几十到几百条会话」的量级，LIKE 子串匹配足够，
    也省掉一套索引维护。通配符由 `_escape_like` 转义。

    用户隔离与 `list_conversations` 一致：强制 `Conversation.user_id == user_id`，
    子查询只在已属于该用户的会话范围内生效（消息表本身没有 user_id 列）。
    """
    pattern = f"%{_escape_like(query)}%"
    matched_conversations = select(Message.conversation_id).where(
        Message.content.like(pattern, escape="\\")
    )
    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.user_id == user_id,
            or_(
                Conversation.title.like(pattern, escape="\\"),
                Conversation.id.in_(matched_conversations),
            ),
        )
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_search_conversations(db: AsyncSession, user_id: int, query: str) -> int:
    """搜索命中的会话总数（分页用，规则与 `search_conversations` 完全一致）。"""
    pattern = f"%{_escape_like(query)}%"
    matched_conversations = select(Message.conversation_id).where(
        Message.content.like(pattern, escape="\\")
    )
    result = await db.execute(
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.user_id == user_id,
            or_(
                Conversation.title.like(pattern, escape="\\"),
                Conversation.id.in_(matched_conversations),
            ),
        )
    )
    return int(result.scalar_one())


async def update_title(
    db: AsyncSession, conversation: Conversation, title: str
) -> Conversation:
    conversation.title = title
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def delete_conversation(db: AsyncSession, conversation: Conversation) -> None:
    """物理删除会话及其全部消息。

    ## 为什么这里用两条批量 DELETE，而不是 `db.delete(conversation)`

    两个原因，都不是风格偏好：

    1. **ORM 的级联删除只作用于「已经加载进会话的那批子对象」**。本项目的消息是通过
       `add_message(conversation_id=...)` 用显式外键写进去的，SQLAlchemy 不会因此把它
       追加到 `conversation.messages` 集合里——那个集合可能还停在「刚创建时的空集合」。
       此时 `db.delete(conversation)` 会认为没有子对象，把消息原样留在库里成为孤儿行。
    2. 反过来，如果集合**已经**加载了消息，ORM 又会对这些行再发一次 DELETE；而它们
       已经被第 1 条批量语句删掉了，于是产生
       `SAWarning: DELETE ... expected to delete N row(s); 0 were matched` 这类纯噪音警告。

    批量 DELETE 绕开 ORM 的级联机制，行为对「集合有没有加载」不敏感，也与数据库类型
    和外键 PRAGMA 无关（数据库层的 `ondelete="CASCADE"` 只在 SQLite 且
    `PRAGMA foreign_keys=ON` 时才生效，测试环境并没有打开它）。

    最后 `expunge` 一下：对象已被删除，但 `expire_on_commit=False` 意味着它仍留在
    会话里、属性也还读得到，不清理会让后续代码以为「这个会话还在」。
    """
    await db.execute(sa_delete(Message).where(Message.conversation_id == conversation.id))
    await db.execute(sa_delete(Conversation).where(Conversation.id == conversation.id))
    db.expunge(conversation)
    await db.commit()


async def add_message(
    db: AsyncSession,
    conversation_id: int,
    *,
    role: str,
    content: str,
    tool_calls: dict | None = None,
    tokens: int | None = None,
) -> Message:
    """追加一条消息，并顺手把父会话的 `updated_at` 推到当前时刻（原因见模块说明）。"""
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        tool_calls=tool_calls,
        tokens=tokens,
    )
    db.add(message)
    await db.execute(
        sa_update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(updated_at=func.now())
    )
    await db.commit()
    await db.refresh(message)
    return message


async def list_messages(
    db: AsyncSession, conversation_id: int, *, limit: int | None = None
) -> list[Message]:
    """按时间**正序**返回消息。

    `limit` 取的是**最近** N 条而不是最早 N 条：多轮问答里真正影响下一句的是最近的
    上下文，截掉开头比截掉结尾合理。实现上是先按 id 倒序取 N 条再翻转回来。
    """
    if limit is None:
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id)
        )
        return list(result.scalars().all())

    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(limit)
    )
    return list(reversed(list(result.scalars().all())))


async def count_messages(db: AsyncSession, conversation_id: int) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Message)
        .where(Message.conversation_id == conversation_id)
    )
    return int(result.scalar_one())


async def list_messages_page(
    db: AsyncSession, conversation_id: int, *, page: int, page_size: int
) -> list[Message]:
    """分页取历史消息，**page=1 是最新的一页**。

    分页方向刻意与普通列表接口相反（那边 page=1 是最早的一批）。理由是这个接口服务的是
    聊天界面：用户打开一个聊了很久的会话，第一眼要看到的是最近说了什么，
    而不是三个月前的开场白。翻页是往回翻（page=2 是更早的一页）。

    返回的一页内部按时间**正序**，前端可以直接逐条渲染。
    """
    skip = max(page - 1, 0) * page_size
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .offset(skip)
        .limit(page_size)
    )
    return list(reversed(list(result.scalars().all())))


async def delete_messages(db: AsyncSession, conversation_id: int) -> None:
    """删除某会话的全部消息（会话本身保留）。"""
    await db.execute(sa_delete(Message).where(Message.conversation_id == conversation_id))
    await db.commit()
