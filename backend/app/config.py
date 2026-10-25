"""应用配置。

铁律第 10 条：所有可变参数（价格、格式规则、功能开关、Provider 选择）必须外置到
配置文件或环境变量，禁止硬编码在业务逻辑里。本模块是唯一的读取入口。
"""

from __future__ import annotations

import json
import logging
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录与 config/ 目录的绝对路径
BACKEND_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = BACKEND_DIR / "config"

# 弱凭据黑名单。这些值出现在公开仓库的示例配置里，继续用就等于没设：
# JWT 是对称密钥，拿到就能自己签发任意 role 的 token；管理员口令是文印室后台的唯一入口。
WEAK_CREDENTIALS = frozenset(
    {"", "change_me", "changeme", "admin", "123456", "password", "secret"}
)


logger = logging.getLogger(__name__)


def is_weak_credential(value: str) -> bool:
    """判断是否为公开示例值：命中黑名单，或仍带着 change_me / change-me 字样。

    示例文件里的 `please_change_me_to_...` 这类提示串同样要拦住，否则照抄示例
    就能绕过自检。
    """
    normalized = (value or "").strip().lower()
    return (
        normalized in WEAK_CREDENTIALS
        or "change_me" in normalized
        or "change-me" in normalized
    )


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
    # AI 调用的隔离参数。模型接口慢，一次请求能占住线程几十秒，
    # 不设上限时几十个人同时点体检会把登录与下单一起拖死，所以并发要卡住、
    # 排队要短、超时要早失败。
    llm_timeout_seconds: int = 30
    llm_max_attempts: int = 2
    llm_max_concurrency: int = 4
    llm_queue_wait_seconds: float = 3.0
    # AI 任务专用线程池大小与排队上限。公共线程池只留给交易链路，
    # AI 再慢也只占这几个名额；排队等不到就早失败，不占线程。
    ai_max_workers: int = 4
    ai_queue_wait_seconds: float = 30.0

    # 管理员（阶段 4 使用）
    admin_username: str = "admin"
    admin_password: str = "change_me"

    # 业务参数
    jwt_secret: str = "change_me"
    jwt_expire_minutes: int = 720
    # 上传文件的物理保留小时数。必须大于下单宽限期（下方 order_file_grace_hours），
    # 否则学生拿到的是一份已被删掉的引用。启动时会自检并提示。
    file_expire_hours: int = 72
    # 上传后允许多久之内下单；超过就提示重新上传（宽限期口径，产品定）
    order_file_grace_hours: int = 48
    # 异常单引用的文件额外保留天数。异常单没有自动超时，不设上限会无限期占空间
    abnormal_file_grace_days: int = 3
    signed_url_expire_minutes: int = 30

    # 定时清理。默认开启；测试环境通过 CLEANUP_ENABLED=false 关掉，避免后台任务干扰用例
    cleanup_enabled: bool = True
    cleanup_interval_minutes: int = 60

    # LibreOffice headless 可执行文件路径（阶段 5-A 的 Word -> PDF 使用）。
    # 空字符串表示本机未配置，此时 Office 文档一律报 50040，不尝试启动进程。
    # 不提供盘符默认值：Windows 与 Linux 的安装位置不同，猜错了比没配更难查。
    soffice_path: str = ""
    soffice_timeout_seconds: int = 180
    # PDF 转 Word（pdf2docx）在进程内跑，没有自己的超时。上限设在这里：
    # 扫描件要跑几分钟，卡死时必须能失败返回，否则会一直占着 AI 并发槽位。
    pdf2docx_timeout_seconds: int = 180
    # 同时最多几个转换进程。每次转换都新起一个 soffice，几十人同时上传时
    # 进程数与内存会把机器打满，所以超出上限的请求排队，排不到就返回 50046。
    soffice_max_concurrency: int = 2
    # 排队等空位的最长秒数。等不到就早失败，不占着请求线程干等。
    soffice_queue_wait_seconds: float = 20.0
    # 也可以不装本机 LibreOffice，改用常驻转换容器（编排文件里的 libreoffice 服务）。
    # 填容器名即走 docker exec，容器内把后端的 runtime 目录挂成 soffice_container_dir。
    soffice_container: str = ""
    soffice_container_dir: str = "/workspace/runtime"
    # 表格渲染（归一版式 + LibreOffice 转换）一条请求的总耗时预算，单位秒。
    # 字太小会逐页放宽重试，每次重试都要重新转换一次，单次最长 soffice_timeout_seconds。
    # 不设预算时最差四次串行＝十几分钟一直占住一个 AI 并发名额，后面排队的人只会超时。
    # 超预算就按当前结果出纸，并在体检报告里提示列太多、字号偏小。
    spreadsheet_render_budget_seconds: float = 240.0

    # 可扩展点：V1 用简化实现，V2 换实现只改环境变量
    auth_provider: str = "device"  # device | wechat | cas
    payment_provider: str = "mock"  # mock | wechat | card
    notifier_provider: str = "poll"  # poll | wechat | push
    printer_provider: str = "manual"  # manual | cloud

    # 支付相关（V2 启用真实支付时填写，V1 留空）
    wechat_mch_id: str = ""
    wechat_api_key: str = ""
    wechat_notify_url: str = ""

    # 安全自检开关。仅本机演示与单测使用：true 时允许弱密钥/弱口令启动，
    # 生产环境绝不能开。开了会在启动日志里留一条醒目告警。
    allow_insecure_defaults: bool = False

    @model_validator(mode="after")
    def _check_file_retention(self) -> Settings:
        """文件物理保留期必须长于下单宽限期，否则学生会引用到已删除的文件。"""
        if self.file_expire_hours <= self.order_file_grace_hours:
            raise ValueError(
                "FILE_EXPIRE_HOURS 必须大于 ORDER_FILE_GRACE_HOURS："
                f"当前为 {self.file_expire_hours} 与 {self.order_file_grace_hours}"
            )
        return self

    @model_validator(mode="after")
    def _check_weak_credentials(self) -> Settings:
        """默认密钥与管理员口令必须换掉，否则拒绝启动。

        JWT_SECRET 是 HS256 的对称密钥，泄露即等于任何人都能给自己签发
        role=admin 的 token；ADMIN_PASSWORD 是老师端后台的唯一入口。这两项留在
        公开示例值上运行，等于把文印室后台公开给整个网络。

        本机演示与跑单测时可用 ALLOW_INSECURE_DEFAULTS=true 豁免。
        """
        if self.allow_insecure_defaults:
            logger.warning(
                "安全警告：ALLOW_INSECURE_DEFAULTS=true，弱密钥与弱口令自检已跳过。"
                "当前 JWT_SECRET / ADMIN_PASSWORD 若是公开示例值，任何人都能伪造管理员"
                " token 接管老师端后台。生产环境禁止开启本开关。"
            )
            return self

        weak = [
            name
            for name, value in (
                ("JWT_SECRET", self.jwt_secret),
                ("ADMIN_PASSWORD", self.admin_password),
            )
            if is_weak_credential(value)
        ]
        if weak:
            raise ValueError(
                "以下配置仍是公开示例值，拒绝启动：" + "、".join(weak)
                + "。请在 backend/.env 中改成随机值（JWT_SECRET 建议 32 字节以上）；"
                "本机演示或跑单测可临时设 ALLOW_INSECURE_DEFAULTS=true。"
            )
        return self

    @property
    def report_expire_hours(self) -> int:
        """体检报告缓存（ai_reports）的保留小时数。

        产品口径：报告跟它对应的上传文件同寿命、同步删除，所以这里不另设独立
        常量，直接取 file_expire_hours。两处各写一个数字迟早会打架。
        """
        return self.file_expire_hours

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
