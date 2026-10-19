"""定时清理服务测试：过期文件删除、订单保护、保留期、演练模式与报告缓存。

全部使用内存替身存储，不连接真实 MinIO。
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.ai_report import AiReport
from app.models.order import Order
from app.services.cleanup import run_cleanup
from app.services.order_state import (
    STATUS_ABNORMAL,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_PENDING_PAY,
    STATUS_PRINTING,
    STATUS_QUEUING,
    STATUS_READY,
)
from app.services.storage.base import FileStorage, StoredObject

# 固定参考时间，用例不依赖真实时钟
NOW = datetime(2026, 3, 1, 12, 0, 0)
EXPIRED_AT = NOW - timedelta(hours=settings.file_expire_hours + 1)
FRESH_AT = NOW - timedelta(hours=1)

_ORDER_SEQ = itertools.count(1)


class FakeStorage(FileStorage):
    """内存替身：只维护对象清单与删除记录，不连 MinIO。"""

    def __init__(self) -> None:
        self.objects: dict[str, StoredObject] = {}
        self.deleted: list[str] = []
        self.fail_keys: set[str] = set()
        self.list_error: Exception | None = None

    def put(self, key: str, last_modified: datetime, size: int = 1024) -> None:
        self.objects[key] = StoredObject(key=key, size=size, last_modified=last_modified)

    def ensure_bucket(self) -> None:
        return None

    def upload(self, key: str, data: BinaryIO, size: int,
               content_type: str | None = None) -> str:
        raise NotImplementedError("单测替身不支持上传")

    def signed_url(self, key: str, expires_minutes: int | None = None) -> str:
        raise NotImplementedError("单测替身不支持签名链接")

    def download_to_file(self, key: str, dest: Path) -> Path:
        raise NotImplementedError("单测替身不支持下载")

    def read_bytes(self, key: str) -> bytes:
        raise NotImplementedError("单测替身不支持读取")

    def stat_modified(self, key: str) -> datetime | None:
        obj = self.objects.get(key)
        return obj.last_modified if obj else None

    def list_objects(self, prefix: str = "") -> list[StoredObject]:
        if self.list_error is not None:
            raise self.list_error
        return [obj for key, obj in sorted(self.objects.items()) if key.startswith(prefix)]

    def delete(self, key: str) -> None:
        if key in self.fail_keys:
            raise RuntimeError("模拟对象存储删除失败")
        self.objects.pop(key, None)
        self.deleted.append(key)


@pytest.fixture
def fake_storage() -> FakeStorage:
    return FakeStorage()


def _add_order(db_session, file_key: str, status: str) -> Order:
    """直接插入订单行，只关心 file_key 与状态。"""
    seq = next(_ORDER_SEQ)
    order = Order(
        order_no=f"260301-{seq:03d}",
        pickup_code=f"260301-A-{seq:02d}",
        user_id=1,
        status=status,
        file_key=file_key,
    )
    db_session.add(order)
    db_session.flush()
    return order


def test_超过保留期且无订单引用的文件会被删除(db_session, fake_storage):
    key = "files/2026/02/orphan.pdf"
    fake_storage.put(key, EXPIRED_AT)

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.scanned == 1
    assert stats.deleted == 1
    assert stats.skipped == 0
    assert stats.failed == 0
    assert fake_storage.deleted == [key]
    assert key not in fake_storage.objects


def test_未完结订单引用的文件跳过(db_session, fake_storage):
    statuses = [
        STATUS_PENDING_PAY,
        STATUS_QUEUING,
        STATUS_PRINTING,
        STATUS_READY,
        STATUS_ABNORMAL,
    ]
    keys = []
    for index, status in enumerate(statuses):
        key = f"files/2026/02/unfinished-{index}.pdf"
        keys.append(key)
        fake_storage.put(key, EXPIRED_AT)
        _add_order(db_session, key, status)

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.scanned == len(keys)
    assert stats.skipped == len(keys)
    assert stats.deleted == 0
    assert stats.failed == 0
    assert fake_storage.deleted == []
    assert all(key in fake_storage.objects for key in keys)


def test_已完结订单引用且过期的文件会删除(db_session, fake_storage):
    completed_key = "files/2026/02/completed.pdf"
    cancelled_key = "files/2026/02/cancelled.pdf"
    fake_storage.put(completed_key, EXPIRED_AT)
    fake_storage.put(cancelled_key, EXPIRED_AT)
    _add_order(db_session, completed_key, STATUS_COMPLETED)
    _add_order(db_session, cancelled_key, STATUS_CANCELLED)

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.deleted == 2
    assert stats.skipped == 0
    assert set(fake_storage.deleted) == {completed_key, cancelled_key}


def test_保留期内的文件不处理(db_session, fake_storage):
    key = "files/2026/03/fresh.pdf"
    fake_storage.put(key, FRESH_AT)

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.scanned == 1
    assert stats.not_expired == 1
    assert stats.deleted == 0
    assert fake_storage.deleted == []
    assert key in fake_storage.objects


def test_演练模式不真正删除文件(db_session, fake_storage):
    key = "files/2026/02/dry-run.pdf"
    fake_storage.put(key, EXPIRED_AT)
    db_session.add(
        AiReport(
            file_key=key,
            report_json={"kind": "parse"},
            created_at=NOW - timedelta(days=settings.report_retention_days + 1),
        )
    )
    db_session.flush()

    stats = run_cleanup(db_session, fake_storage, now=NOW, dry_run=True)

    assert stats.dry_run is True
    assert stats.deleted == 1
    assert stats.reports_deleted == 1
    assert fake_storage.deleted == []
    assert key in fake_storage.objects
    # 报告缓存也不能在演练模式下被删
    remaining = db_session.execute(select(AiReport)).scalars().all()
    assert len(remaining) == 1


def test_删除失败计入失败且下一轮仍能扫到(db_session, fake_storage):
    key = "files/2026/02/retry.pdf"
    fake_storage.put(key, EXPIRED_AT)
    fake_storage.fail_keys.add(key)

    first = run_cleanup(db_session, fake_storage, now=NOW)

    assert first.failed == 1
    assert first.deleted == 0
    assert key in fake_storage.objects
    assert fake_storage.deleted == []

    # 存储恢复后下一轮重新扫到并成功删除
    fake_storage.fail_keys.clear()
    second = run_cleanup(db_session, fake_storage, now=NOW)

    assert second.failed == 0
    assert second.deleted == 1
    assert key not in fake_storage.objects


def test_超过保留天数的体检报告缓存被清理(db_session, fake_storage):
    old_key = "files/2026/02/report-old.pdf"
    fresh_key = "files/2026/03/report-fresh.pdf"
    db_session.add(
        AiReport(
            file_key=old_key,
            report_json={"kind": "parse"},
            created_at=NOW - timedelta(days=settings.report_retention_days + 1),
        )
    )
    db_session.add(
        AiReport(
            file_key=fresh_key,
            report_json={"kind": "parse"},
            created_at=NOW - timedelta(days=1),
        )
    )
    db_session.flush()

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.reports_deleted == 1
    remaining = db_session.execute(select(AiReport)).scalars().all()
    assert [row.file_key for row in remaining] == [fresh_key]


def test_对象存储列举失败只记失败不崩溃(db_session, fake_storage):
    fake_storage.list_error = RuntimeError("模拟对象存储不可用")

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.failed == 1
    assert stats.deleted == 0


def test_异常单引用的文件在额外宽限期内保留(db_session, fake_storage) -> None:
    """异常单没有自动超时，所以给三天额外宽限，而不是无限期保留。"""
    now = datetime(2026, 3, 1, 12, 0, 0)
    # 三天宽限 + 二十四小时保留 = 四天，这里取三天多，仍在宽限内
    old = now - timedelta(hours=settings.file_expire_hours + 30)
    fake_storage.put("files/abnormal-recent.pdf", old)
    _add_order(db_session, "files/abnormal-recent.pdf", STATUS_ABNORMAL)

    stats = run_cleanup(db_session, fake_storage, now=now)

    assert stats.deleted == 0
    assert stats.skipped == 1
    assert "files/abnormal-recent.pdf" in fake_storage.objects


def test_异常单引用的文件超过额外宽限后回收(db_session, fake_storage) -> None:
    now = datetime(2026, 3, 1, 12, 0, 0)
    # 保留期加三天宽限再多一天，已经超出上限
    old = now - timedelta(hours=settings.file_expire_hours + 24 * 3 + 24)
    fake_storage.put("files/abnormal-old.pdf", old)
    _add_order(db_session, "files/abnormal-old.pdf", STATUS_ABNORMAL)

    stats = run_cleanup(db_session, fake_storage, now=now)

    assert stats.deleted == 1
    assert "files/abnormal-old.pdf" in fake_storage.deleted
