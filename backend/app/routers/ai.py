"""AI 体检相关路由。

阶段 5 实现 check / print-plan / fix / ocr / convert 五个接口，当前已完成 check。

铁律：所有接口先校验 file_key 归属（ensure_files_owned_by）。只要求「已登录」不够 ——
任何人都能登录，拿到别人的 file_key 就能读论文内容；而 /convert 与 /fix 还会把
生成的文件挂到自己名下，等于把别人的论文洗成自己的。

两件容易踩的事：
1. 报告缓存按账号隔离（产品口径：各账号各存一份，互不共用）。所以每个入口在跑任务前
   都把当前用户绑到会话上（bind_report_owner），缓存读写据此带上账号维度。
2. 慢活跑在 ai_pool 的线程里，用的仍是这个请求的数据库会话。客户端断开时请求会先
   结束、会话随之关闭，线程却还在用 —— 见 ai_pool.run_ai_job 里屏蔽取消的做法。

另一条铁律：学生自带的 AI 密钥（请求头 X-LLM-Api-Key）只在请求头里出现一次，
后端用完即弃 —— 不写数据库、不写日志、不写任务结果、不进 URL 与响应体。所以它在这里
被读出来之后，只以**函数参数**的形式往下传，不存到任何模块级变量上。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.config import settings
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
from app.services import ai_pool
from app.services.ai_pool import LANE_CONVERSION, ai_slot, run_ai_job
from app.services.converter import convert_pdf_to_docx
from app.services.doc_fixer import fix_document
from app.services.doc_parser import bind_report_owner
from app.services.file_service import ensure_files_owned_by
from app.services.format_checker import check_format
from app.services.llm import USER_KEY_HEADER, user_key_from_header, verify_user_key
from app.services.ocr import ocr_image
from app.services.print_planner import plan_print
from app.services.proofreader import get_progress, start_proofread
from app.utils.exceptions import BusinessError
from app.utils.response import ok
from app.utils.security import get_current_user

router = APIRouter()


def user_llm_key(request: Request) -> str | None:
    """学生自带的 AI 密钥：只在这次请求里出现一次。

    没带这个头（或运维关掉了开关）时返回 None，后面一律用学校共享密钥。
    """
    return user_key_from_header(request.headers.get(USER_KEY_HEADER))


# FastAPI 依赖里可以声明和端点同一个请求体模型：两边拿到的是同一个已解析的 body，
# 所以这里能按 file_key 的扩展名选车道，而不用在端点里事后补一道名额。
async def check_lane(payload: AiCheckRequest) -> AsyncIterator[str]:
    """体检的名额：表格走转换车道，其余格式走模型车道。产出车道名给端点复用。"""
    lane = ai_pool.lane_for_file(payload.file_key)
    async with ai_pool.job_lane(lane):
        yield lane


async def print_plan_lane(payload: PrintPlanRequest) -> AsyncIterator[str]:
    lane = ai_pool.lane_for_file(payload.file_key)
    async with ai_pool.job_lane(lane):
        yield lane


async def conversion_lane() -> AsyncIterator[str]:
    """PDF 转 Word / 文档修复本身就要跑 LibreOffice 与 pdf2docx，固定走转换车道。"""
    async with ai_pool.job_lane(LANE_CONVERSION):
        yield LANE_CONVERSION


@router.post("/check", summary="AI 格式体检（需登录）", response_model=None)
async def ai_check(
    payload: AiCheckRequest,
    lane: str = Depends(check_lane),
    api_key: str | None = Depends(user_llm_key),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """规则引擎 + 大模型混合检查。结果只作建议，不自动修改文档。

    异步端点 + 专用线程池：模型慢的时候只占 AI 名额，不会挤掉登录与下单。
    表格文件走转换车道（预算 240 秒），不占模型名额。
    """
    ensure_files_owned_by(db, user, [payload.file_key])
    bind_report_owner(db, user.id)
    return ok(
        await run_ai_job(
            check_format,
            db,
            payload.file_key,
            force=payload.force,
            api_key=api_key,
            lane=lane,
        )
    )


@router.post("/key-check", summary="检查学生自带的 AI 密钥是否可用（需登录）", response_model=None)
async def ai_key_check(
    _slot: None = Depends(ai_slot),
    api_key: str | None = Depends(user_llm_key),
    user: User = Depends(get_current_user),
) -> dict:
    """用请求头里的密钥做一次最小调用，确认它真能用。

    响应里不回显密钥（连脱敏形式也不回）：学生只需要知道「通 / 不通 + 为什么不通」。
    失败时抛的是可直接展示的中文原因，见 llm.verify_user_key。
    """
    if not settings.llm_user_key_enabled:
        raise BusinessError(
            "学校已关闭学生自带密钥，当前一律使用学校共享密钥", code=50037
        )
    if not api_key:
        raise BusinessError(
            f"请求头 {USER_KEY_HEADER} 里没有密钥，请先在设置里填写", code=50036
        )
    # 这是同步网络调用，可能在模型超时与重试上等待几十秒；放进 AI 专用线程池，
    # 不能直接在 async 端点里执行，否则一个密钥检查就会阻塞整个事件循环。
    await run_ai_job(verify_user_key, api_key)
    return ok(
        {
            "ok": True,
            "message": "密钥可用，本次调用已用完即弃",
            "model": settings.llm_model,
        }
    )


@router.post("/proofread", summary="错别字与标点检查（异步任务）", response_model=None)
def ai_proofread(
    payload: ProofreadRequest,
    api_key: str | None = Depends(user_llm_key),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """立即返回 task_id；用 GET /api/ai/proofread/{task_id} 轮询进度与结果（不用 WebSocket）。

    校对在后台线程里分块调模型，所以密钥要跟着任务一起交给那个线程（用完即弃），
    不能只留在请求上下文里 —— 请求早就返回了，模型调用才刚开始。
    """
    ensure_files_owned_by(db, user, [payload.file_key])
    bind_report_owner(db, user.id)
    return ok(start_proofread(payload.file_key, owner=str(user.id), api_key=api_key))


@router.get("/proofread/{task_id}", summary="查看错别字检查进度", response_model=None)
def ai_proofread_progress(task_id: str, user: User = Depends(get_current_user)) -> dict:
    """只返回本人发起的任务：结果里是论文原文片段，不能让别人拿到 task_id 就读。"""
    return ok(get_progress(task_id, owner=str(user.id)))


@router.post("/print-plan", summary="生成打印方案（含省纸省钱计算）", response_model=None)
async def ai_print_plan(
    payload: PrintPlanRequest,
    lane: str = Depends(print_plan_lane),
    api_key: str | None = Depends(user_llm_key),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """模型给推荐方案与理由，纸张数与金额由服务端按 pricing.json 计算。"""
    ensure_files_owned_by(db, user, [payload.file_key])
    bind_report_owner(db, user.id)
    options = payload.options.model_dump(exclude_none=True) if payload.options else {}
    return ok(
        await run_ai_job(
            plan_print,
            db,
            payload.file_key,
            options,
            force=payload.force,
            api_key=api_key,
            lane=lane,
        )
    )


@router.post("/ocr", summary="图片转文字（需登录）", response_model=None)
async def ai_ocr(
    payload: OcrRequest,
    _slot: None = Depends(ai_slot),
    api_key: str | None = Depends(user_llm_key),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """多模态模型识别图片文字，返回纯文本。"""
    ensure_files_owned_by(db, user, [payload.file_key])
    bind_report_owner(db, user.id)
    return ok(
        await run_ai_job(
            ocr_image, db, payload.file_key, force=payload.force, api_key=api_key
        )
    )


@router.post("/convert", summary="PDF 转 Word（需登录）", response_model=None)
async def ai_convert(
    payload: ConvertRequest,
    lane: str = Depends(conversion_lane),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """用 pdf2docx 转换（不调用大模型），返回新文件的 file_key 与签名链接。

    转换是纯 CPU 活，同样走专用池：它慢起来也会顶满公共线程池。
    生成的文件记在当前用户名下，学生才能在「移除」时把对象一起删掉。
    """
    ensure_files_owned_by(db, user, [payload.file_key])
    bind_report_owner(db, user.id)
    if payload.target.lower() != "docx":
        raise BusinessError("V1 只支持转换为 docx", code=40079)
    return ok(
        await run_ai_job(
            convert_pdf_to_docx,
            db,
            payload.file_key,
            payload.file_name,
            user=user,
            lane=lane,
        )
    )


@router.post("/fix", summary="修复文档（需用户勾选问题）", response_model=None)
async def ai_fix(
    payload: FixRequest,
    lane: str = Depends(conversion_lane),
    api_key: str | None = Depends(user_llm_key),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """AI 只输出结构化修改指令，真正的修改由 python-docx 执行，生成修复副本。

    修复副本同样记在当前用户名下，学生才能把它撤回。
    """
    ensure_files_owned_by(db, user, [payload.file_key])
    bind_report_owner(db, user.id)
    return ok(
        await run_ai_job(
            fix_document,
            db,
            payload.file_key,
            payload.issue_ids,
            payload.file_name,
            user=user,
            api_key=api_key,
            lane=lane,
        )
    )
