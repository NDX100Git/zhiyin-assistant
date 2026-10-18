"""应用配置。

铁律第 10 条：所有可变参数（价格、格式规则、功能开关、Provider 选择）必须外置到
配置文件或环境变量，禁止硬编码在业务逻辑里。本模块是唯一的读取入口。
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录与 config/ 目录的绝对路径
BACKEND_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = BACKEND_DIR / "config"


class Settings(BaseSettings):
    """从环境变量与 backend/.env 读取的运行时配置。"""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 应用
    app_name: str = "智印助手"
    app_version: str = "0.1.0"
    debug: bool = True
    cors_origins: str = "*"  # 逗号分隔，* 表示开发期允许全部
    auto_create_tables: bool = True  # 启动时自动建表（V1 简化实现）
    db_echo: bool = False

    # 数据库
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_name: str = "zhiyin"
    db_user: str = "root"
    db_password: str = ""
    # 连接池。默认值 5 加 10 是 SQLAlchemy 的出厂设置，一万人在线轮询时会排队超时，
    # 实测见 REVIEW 第 11 条；这里显式给足，并让等连接快失败而不是拖 30 秒
    db_pool_size: int = 20
    db_max_overflow: int = 30
    db_pool_timeout: int = 10

    # 对象存储（阶段 2 使用）
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "zhiyin-files"
    minio_secure: bool = False

    # AI 模型（阶段 5 使用，API Key 只允许存在于服务端环境变量）
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"

    # 管理员（阶段 4 使用）
    admin_username: str = "admin"
    admin_password: str = "change_me"

    # 业务参数
    jwt_secret: str = "change_me"
    jwt_expire_minutes: int = 720
    file_expire_hours: int = 24
    signed_url_expire_minutes: int = 30

    # LibreOffice headless 可执行文件路径（阶段 5-A 的 Word -> PDF 使用）。
    # 空字符串表示本机未配置，此时 Office 文档一律报 50040，不尝试启动进程。
    # 不提供盘符默认值：Windows 与 Linux 的安装位置不同，猜错了比没配更难查。
    soffice_path: str = ""
    soffice_timeout_seconds: int = 180
    # 也可以不装本机 LibreOffice，改用常驻转换容器（编排文件里的 libreoffice 服务）。
    # 填容器名即走 docker exec，容器内把后端的 runtime 目录挂成 soffice_container_dir。
    soffice_container: str = ""
    soffice_container_dir: str = "/workspace/runtime"

    # 可扩展点：V1 用简化实现，V2 换实现只改环境变量
    auth_provider: str = "device"  # device | wechat | cas
    payment_provider: str = "mock"  # mock | wechat | card
    notifier_provider: str = "poll"  # poll | wechat | push
    printer_provider: str = "manual"  # manual | cloud

    # 支付相关（V2 启用真实支付时填写，V1 留空）
    wechat_mch_id: str = ""
    wechat_api_key: str = ""
    wechat_notify_url: str = ""

    @property
    def database_url(self) -> str:
        """SQLAlchemy 连接串。密码做 URL 转义，避免特殊字符导致解析失败。"""
        password = quote_plus(self.db_password)
        return (
            f"mysql+pymysql://{self.db_user}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def soffice_binary(self) -> Path | None:
        """LibreOffice 可执行文件；未配置或不是一个文件时返回 None。

        不能把空值交给 Path() 再加 exists() 判断：Path("") 就是当前目录，
        exists() 会认为它存在，后续去执行目录才报权限错。全仓只在这里做一次判断。
        """
        raw = (self.soffice_path or "").strip()
        if not raw:
            return None
        candidate = Path(raw).expanduser()
        return candidate if candidate.is_file() else None


settings = Settings()


@cache
def read_json_config(filename: str) -> dict[str, Any]:
    """读取 backend/config/ 下的 JSON 配置（价格、格式规则、功能开关等）。

    业务代码一律通过本函数读取配置，不得把数值写死在代码里。
    """
    path = CONFIG_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def get_pricing_config() -> dict[str, Any]:
    """计费配置，单位：分/页。"""
    return read_json_config("pricing.json")


def get_format_rules() -> dict[str, Any]:
    """论文格式校验规则集。"""
    return read_json_config("format_rules.json")


def get_feature_flags() -> dict[str, Any]:
    """功能开关，供前端启动时拉取，控制未完成功能的入口是否显示。"""
    return read_json_config("features.json")


def get_upload_rules() -> dict[str, Any]:
    """上传校验规则（大小上限、扩展名白名单）。"""
    return read_json_config("upload_rules.json")


def get_spreadsheet_rules() -> dict[str, Any]:
    """表格归一版式的口径（缩放、方向、边距）。改口径只改配置文件。"""
    return read_json_config("spreadsheet_rules.json")


def get_order_rules() -> dict[str, Any]:
    """订单相关规则（收单时段、容量、等待时间参数、下单限制）。"""
    return read_json_config("order_rules.json")


def get_ai_rules() -> dict[str, Any]:
    """AI 任务参数（分块大小、并发数、打印方案可选值）。"""
    return read_json_config("ai_rules.json")
