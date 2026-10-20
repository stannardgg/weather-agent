"""横切层：知识库的请求 / 响应契约（CLAUDE.md §7）。

`documents` 的字段形状与 §6 定稿一致。**刻意不暴露 `stored_path` 与 `file_hash`**：
前者是服务器内部路径，泄露出去等于告诉别人本机目录结构；后者是内容指纹，
对用户没有意义，且会让前端有机会做「探测某个文件是否已存在」这种事。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChunkOut(BaseModel):
    """切分片段（文档详情里的预览用）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    chunk_index: int
    vector_id: str
    char_count: int
    content: str


class DocumentOut(BaseModel):
    """文档概要：列表接口返回的就是这个形状。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    content_type: str | None = None
    size: int
    status: str = Field(description="pending / parsing / embedding / ready / failed")
    chunk_count: int
    error_message: str | None = Field(
        default=None, description="仅 status=failed 时有值，说明失败原因"
    )
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentOut):
    """文档详情 = 概要 + 切分预览。

    `chunks` 只返回前 N 条（N = 配置项 `DOCUMENT_PREVIEW_CHUNKS`），
    `total_chunks` 是完整数量——一篇 20 页 PDF 可能切出上百块，
    全量返回会让响应体变得很大，而用户真正想看的只是「切得对不对」。
    """

    chunks: list[ChunkOut] = Field(default_factory=list)
    total_chunks: int = 0


class UploadResult(BaseModel):
    """上传结果。`deduplicated=true` 表示命中了 §8.1 的 file_hash 查重，没有重复处理。"""

    document: DocumentOut
    deduplicated: bool = False


class ReindexResult(BaseModel):
    """全量重建索引的结果。"""

    total: int
    dispatched: int
    message: str
