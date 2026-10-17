"""FastAPI 入口。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.routers import admin, ai, auth, file, home, notice, order
from app.utils.exceptions import register_exception_handlers
from app.utils.response import ok

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """启动时按配置决定是否自动建表。"""
    if settings.auto_create_tables:
        try:
            init_db()
        except Exception:
            logger.exception("数据库初始化失败，请检查 MySQL 是否已启动以及 .env 中的连接配置")
            raise
    yield


app = FastAPI(
    title=f"{settings.app_name} API",
    version=settings.app_version,
    description="智印助手 —— AI 论文格式体检与自助打印平台后端接口",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)


@app.get("/api/health", tags=["公共"], summary="健康检查（免 token）")
def health() -> dict:
    """供前端与部署脚本探活，返回统一响应格式。"""
    return ok({"status": "up", "app": settings.app_name, "version": settings.app_version})


# 路由挂载：各模块的具体接口按阶段实现，客户端只能读状态
app.include_router(auth.router, prefix="/api/auth", tags=["学生端-登录"])
app.include_router(file.router, prefix="/api/file", tags=["学生端-文件"])
app.include_router(ai.router, prefix="/api/ai", tags=["学生端-AI体检"])
app.include_router(order.router, prefix="/api/order", tags=["学生端-订单"])
app.include_router(home.router, prefix="/api/home", tags=["学生端-首页"])
app.include_router(notice.router, prefix="/api/notice", tags=["学生端-公告"])
app.include_router(admin.router, prefix="/api/admin", tags=["老师端-后台"])
