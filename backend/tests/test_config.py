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
