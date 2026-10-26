"""配置校验：文件保留期必须长于下单宽限期。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_文件保留期必须大于下单宽限期() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, file_expire_hours=24, order_file_grace_hours=48)


def test_合法保留期可以创建配置() -> None:
    settings = Settings(_env_file=None, file_expire_hours=72, order_file_grace_hours=48)
    assert settings.file_expire_hours > settings.order_file_grace_hours


# ---------------------------------------------------------------- 弱密钥自检
STRONG_JWT = "b7f1c0d2e9a84517b0e3f6a1c8d24e57"  # 32 位随机串
STRONG_PASSWORD = "YinShua-2026-9f2c"


def test_弱JWT密钥拒绝启动() -> None:
    """JWT_SECRET 是 HS256 的对称密钥，留在示例值上等于谁都能伪造管理员 token。"""
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            _env_file=None,
            allow_insecure_defaults=False,
            jwt_secret="change_me",
            admin_password=STRONG_PASSWORD,
        )
    assert "JWT_SECRET" in str(excinfo.value)


def test_过短的JWT密钥拒绝启动() -> None:
    """只拦固定示例值不够：一个字符的 HS256 密钥同样能被立即猜中。"""
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            _env_file=None,
            allow_insecure_defaults=False,
            jwt_secret="a",
            admin_password=STRONG_PASSWORD,
        )
    assert "JWT_SECRET" in str(excinfo.value)


def test_弱管理员口令拒绝启动() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            _env_file=None,
            allow_insecure_defaults=False,
            jwt_secret=STRONG_JWT,
            admin_password="admin",
        )
    assert "ADMIN_PASSWORD" in str(excinfo.value)


def test_过短的管理员口令拒绝启动() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            _env_file=None,
            allow_insecure_defaults=False,
            jwt_secret=STRONG_JWT,
            admin_password="1",
        )
    assert "ADMIN_PASSWORD" in str(excinfo.value)


def test_照抄示例里的提示串同样被拦住() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            allow_insecure_defaults=False,
            jwt_secret="please_change_me_to_a_random_value",
            admin_password=STRONG_PASSWORD,
        )


def test_两项都弱时全部列出来() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            _env_file=None,
            allow_insecure_defaults=False,
            jwt_secret="change_me",
            admin_password="123456",
        )
    message = str(excinfo.value)
    assert "JWT_SECRET" in message and "ADMIN_PASSWORD" in message


def test_强凭据可以正常启动() -> None:
    settings = Settings(
        _env_file=None,
        allow_insecure_defaults=False,
        jwt_secret=STRONG_JWT,
        admin_password=STRONG_PASSWORD,
    )
    assert settings.jwt_secret == STRONG_JWT


def test_豁免开关打开时放行弱凭据() -> None:
    """仅本机演示与跑单测使用；开了要放行，但要留下告警。"""
    settings = Settings(
        _env_file=None,
        allow_insecure_defaults=True,
        jwt_secret="change_me",
        admin_password="change_me",
    )
    assert settings.allow_insecure_defaults is True
