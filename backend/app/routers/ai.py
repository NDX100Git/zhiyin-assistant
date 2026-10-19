"""AI 体检相关路由。

阶段 5 实现 check / print-plan / fix / ocr / convert 五个接口，当前已完成 check。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.schemas.ai import (
    AiCheckRequest,
    ConvertRequest,
    FixRequest,
    OcrRequest,
    PrintPlanRequest,
    ProofreadRequest,
)
from app.services.ai_pool import ai_slot, run_ai_job
from app.services.converter import convert_pdf_to_docx
from app.services.doc_fixer import fix_document
from app.services.format_checker import check_format
from app.services.ocr import ocr_image
from app.services.print_planner import plan_print
from app.services.proofreader import get_progress, start_proofread
from app.utils.exceptions import BusinessError
from app.utils.response import ok
from app.utils.security import get_current_user

router = APIRouter()


@router.post("/check", summary="AI 格式体检（需登录）", response_model=None)
async def ai_check(
    payload: AiCheckRequest,
    _slot: None = Depends(ai_slot),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """规则引擎 + 大模型混合检查。结果只作建议，不自动修改文档。

    异步端点 + 专用线程池：模型慢的时候只占 AI 名额，不会挤掉登录与下单。
    """
    return ok(await run_ai_job(check_format, db, payload.file_key, force=payload.force))


@router.post("/proofread", summary="错别字与标点检查（异步任务）", response_model=None)
def ai_proofread(payload: ProofreadRequest, _: User = Depends(get_current_user)) -> dict:
    """立即返回 task_id；用 GET /api/ai/proofread/{task_id} 轮询进度与结果（不用 WebSocket）。"""
    return ok(start_proofread(payload.file_key))


@router.get("/proofread/{task_id}", summary="查看错别字检查进度", response_model=None)
def ai_proofread_progress(task_id: str, _: User = Depends(get_current_user)) -> dict:
    return ok(get_progress(task_id))


@router.post("/print-plan", summary="生成打印方案（含省纸省钱计算）", response_model=None)
async def ai_print_plan(
    payload: PrintPlanRequest,
    _slot: None = Depends(ai_slot),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """模型给推荐方案与理由，纸张数与金额由服务端按 pricing.json 计算。"""
    options = payload.options.model_dump(exclude_none=True) if payload.options else {}
    return ok(await run_ai_job(plan_print, db, payload.file_key, options, force=payload.force))


@router.post("/ocr", summary="图片转文字（需登录）", response_model=None)
async def ai_ocr(
    payload: OcrRequest,
    _slot: None = Depends(ai_slot),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """多模态模型识别图片文字，返回纯文本。"""
    return ok(await run_ai_job(ocr_image, db, payload.file_key, force=payload.force))


@router.post("/convert", summary="PDF 转 Word（需登录）", response_model=None)
async def ai_convert(
    payload: ConvertRequest,
    _slot: None = Depends(ai_slot),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """用 pdf2docx 转换（不调用大模型），返回新文件的 file_key 与签名链接。

    转换是纯 CPU 活，同样走 AI 专用池：它慢起来也会顶满公共线程池。
    """
    if payload.target.lower() != "docx":
        raise BusinessError("V1 只支持转换为 docx", code=40079)
    return ok(
        await run_ai_job(convert_pdf_to_docx, db, payload.file_key, payload.file_name)
    )


@router.post("/fix", summary="修复文档（需用户勾选问题）", response_model=None)
async def ai_fix(
    payload: FixRequest,
    _slot: None = Depends(ai_slot),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """AI 只输出结构化修改指令，真正的修改由 python-docx 执行，生成修复副本。"""
    return ok(
        await run_ai_job(fix_document, db, payload.file_key, payload.issue_ids, payload.file_name)
    )
