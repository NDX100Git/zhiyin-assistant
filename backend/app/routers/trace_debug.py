"""测试用的轨迹日志接收端。

只在一件事上有用：真机上拿不到应用控制台输出时，把页面、点击、请求这些事件
从手机发回后端，用 `docker logs` 就能看到。

开关：设置项 `debug_trace_enabled`（环境变量 `DEBUG_TRACE_ENABLED=true`）为真时才注册路由，
默认关闭，生产环境不会有这个接口。

请求体是一批事件，每行一条 JSON。这里只做记录，不落库、不回查。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings

router = APIRouter(prefix="/api/debug", tags=["debug"])

logger = logging.getLogger(__name__)

# 单批最多收多少条，防止误用把这个接口当成上传通道
MAX_BATCH = 500


class TraceBatch(BaseModel):
    """一批轨迹事件。

    `lines` 里每条是前端自己拼好的 JSON 字符串，后端原样记录，
    不解析字段，免得两边结构一改就要跟着改后端。
    """

    device: str = ""
    lines: list[str] = []


@router.post("/trace")
async def collect_trace(batch: TraceBatch) -> dict:
    for line in batch.lines[:MAX_BATCH]:
        logger.info("TRACE %s %s", batch.device, line[:800])
    return {"code": 0, "message": "ok", "data": {"received": len(batch.lines[:MAX_BATCH])}}


@router.get("/trace/ping")
async def trace_ping() -> dict:
    return {"code": 0, "message": "ok", "data": {"enabled": bool(settings.debug_trace_enabled)}}
