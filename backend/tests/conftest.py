"""pytest 公共夹具：测试库 zhiyin_test + 测试桶 + TestClient。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.models  # noqa: E402  确保所有表注册到 metadata
from app.config import settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.migrations import run_migrations  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.services import storage as storage_module  # noqa: E402

TEST_DB_NAME = f"{settings.db_name}_test"
TEST_BUCKET = f"{settings.minio_bucket}-test"

# 单测不启动定时清理：TestClient 会触发 lifespan，后台任务会去扫真实数据库与对象存储
settings.cleanup_enabled = False


@pytest.fixture(scope="session")
def engine():
    """在同一个 MySQL 实例上建独立测试库，避免污染开发库。"""
    url = make_url(settings.database_url)

    root_engine = create_engine(url.set(database=None), pool_pre_ping=True)
    with root_engine.connect() as conn:
        conn.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{TEST_DB_NAME}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
        conn.commit()
    root_engine.dispose()

    test_engine = create_engine(url.set(database=TEST_DB_NAME), pool_pre_ping=True)
    Base.metadata.create_all(test_engine)
    # 测试库可能由旧版本建过，create_all 不会给已有表加列；跑一遍幂等迁移
    run_migrations(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture(scope="session")
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def clean_tables(engine):
    """每个用例结束后清空测试数据，保证用例互不影响。"""
    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f"TRUNCATE TABLE `{table.name}`"))


@pytest.fixture
def db_session(session_factory):
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """把应用的数据库依赖替换成测试会话。"""

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _fresh_upload_time(monkeypatch):
    """下单会校验文件新旧；单测里的 file_key 是假的，统一按「刚上传」处理。

    要验证过期与文件缺失的用例在用例内部再 monkeypatch 覆盖即可。
    """
    from datetime import datetime

    from app.services.storage.minio_store import MinioStorage

    monkeypatch.setattr(MinioStorage, "stat_modified", lambda self, key: datetime.now())


@pytest.fixture
def test_storage(monkeypatch):
    """把存储切到独立的测试桶，避免和开发数据混在一起。"""
    monkeypatch.setattr(settings, "minio_bucket", TEST_BUCKET)
    storage_module.get_storage.cache_clear()
    store = storage_module.get_storage()
    store.ensure_bucket()
    yield store
    storage_module.get_storage.cache_clear()
