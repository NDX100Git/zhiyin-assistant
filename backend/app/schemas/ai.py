"""AI 相关接口的出入参模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AiCheckRequest(BaseModel):
    file_key: str = Field(description="上传接口返回的对象 key")
    force: bool = Field(default=False, description="忽略缓存，重新体检")


class ProofreadRequest(BaseModel):
    file_key: str = Field(description="上传接口返回的对象 key")


class PrintPlanOptions(BaseModel):
    copies: int | None = Field(default=None, ge=1, description="份数")
    color_mode: int | None = Field(default=None, description="0=黑白 1=彩色")
    print_side: int | None = Field(default=None, description="1=单面 2=双面")
    layout: str | None = Field(
        default=None, description="single / handout_2in1 / handout_4in1"
    )
    paper_size: str | None = Field(default=None, description="纸张规格")
    use_ai: bool = Field(default=True, description="false 时用规则方案，不调用大模型")


class PrintPlanRequest(BaseModel):
    file_key: str = Field(description="上传接口返回的对象 key")
    options: PrintPlanOptions | None = Field(default=None, description="用户偏好")
    force: bool = Field(default=False, description="忽略缓存，重新生成")


class OcrRequest(BaseModel):
    file_key: str = Field(description="图片对象 key（jpg/jpeg/png）")
    force: bool = Field(default=False, description="忽略缓存，重新识别")


class ConvertRequest(BaseModel):
    file_key: str = Field(description="待转换的 PDF 对象 key")
    target: str = Field(default="docx", description="目标格式，V1 只支持 docx")
    file_name: str | None = Field(default=None, description="转换后展示的文件名")


class FixRequest(BaseModel):
    file_key: str = Field(description="待修复文件的对象 key（docx 或 pdf）")
    issue_ids: list[str] = Field(min_length=1, description="用户勾选要修复的问题 id")
    file_name: str | None = Field(default=None, description="修复后展示的文件名")


class PageCountRequest(BaseModel):
    file_key: str = Field(description="上传接口返回的对象 key")
