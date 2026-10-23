"""文件上传相关的出入参模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FileDeleteRequest(BaseModel):
    """撤回一个还没下单的上传文件。"""

    file_key: str = Field(description="上传接口返回的对象 key")


class UploadData(BaseModel):
    file_key: str = Field(description="服务端生成的对象 key，如 files/2026/10/xxx.pdf")
    file_name: str = Field(description="用户原始文件名，仅作展示")
    size: int = Field(description="文件字节数")
    content_type: str = Field(description="服务端按文件头判定的 MIME 类型")
    signed_url: str = Field(description="限时签名 URL，便于前端上传后立即预览")
    expires_minutes: int = Field(description="签名 URL 有效期，单位分钟")
    uploaded_at: str | None = Field(
        default=None, description="上传记录时间，便于前端判断是否超过下单窗口"
    )
