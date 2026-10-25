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
from app.models.order_file import OrderFile
from app.models.order_file_change import CHANGE_PENDING, CHANGE_REJECTED, OrderFileChange
from app.models.order_file_version import OrderFileVersion
from app.models.uploaded_file import UploadedFile
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
# 报告与文件同寿命：超过这个小时数才算「过了保留期」
REPORT_EXPIRED_AT = NOW - timedelta(hours=settings.report_expire_hours + 1)
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
            created_at=REPORT_EXPIRED_AT,
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


def test_超过保留小时的体检报告缓存被清理(db_session, fake_storage):
    """文件早就不在了（历史残留、人工删文件），过保留期就兜底清掉。"""
    old_key = "files/2026/02/report-old.pdf"
    fresh_key = "files/2026/03/report-fresh.pdf"
    db_session.add(
        AiReport(
            file_key=old_key,
            report_json={"kind": "parse"},
            created_at=REPORT_EXPIRED_AT,
        )
    )
    db_session.add(
        AiReport(
            file_key=fresh_key,
            report_json={"kind": "parse"},
            created_at=NOW - timedelta(hours=1),
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


def test_待确认换文件引用的新文件不被清理(db_session, fake_storage):
    """学生刚传的新文件还没被老师确认，即使过了保留期也不能删。"""
    old_key = "files/2026/02/change-old.pdf"
    new_key = "files/2026/02/change-new.pdf"
    fake_storage.put(old_key, EXPIRED_AT)
    fake_storage.put(new_key, EXPIRED_AT)

    order = _add_order(db_session, old_key, STATUS_QUEUING)
    db_session.add(
        OrderFileChange(
            order_id=order.id,
            status=CHANGE_PENDING,
            pending_flag=1,
            old_file_key=old_key,
            new_file_key=new_key,
            new_page_count=20,
            new_amount_cents=300,
        )
    )
    db_session.flush()

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.deleted == 0
    assert stats.pending_protected == 1
    assert old_key in fake_storage.objects
    assert new_key in fake_storage.objects
    assert fake_storage.deleted == []


def test_待确认请求被驳回后新文件按保留期回收(db_session, fake_storage):
    old_key = "files/2026/02/rejected-old.pdf"
    new_key = "files/2026/02/rejected-new.pdf"
    fake_storage.put(old_key, EXPIRED_AT)
    fake_storage.put(new_key, EXPIRED_AT)

    order = _add_order(db_session, old_key, STATUS_QUEUING)
    db_session.add(
        OrderFileChange(
            order_id=order.id,
            status=CHANGE_REJECTED,
            pending_flag=None,
            old_file_key=old_key,
            new_file_key=new_key,
            new_page_count=20,
            new_amount_cents=300,
        )
    )
    db_session.flush()

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    # 订单当前文件仍受在途保护；被驳回的新文件没有引用，过了保留期就回收
    assert new_key in fake_storage.deleted
    assert old_key in fake_storage.objects
    assert stats.pending_protected == 0


def test_历史版本不额外续命_当前版本仍受在途保护(db_session, fake_storage):
    """换下来的旧文件按原保留期回收；当前生效文件仍按未完结订单保护。"""
    old_key = "files/2026/02/version-old.pdf"
    current_key = "files/2026/02/version-current.pdf"
    fake_storage.put(old_key, EXPIRED_AT)
    fake_storage.put(current_key, EXPIRED_AT)

    order = _add_order(db_session, current_key, STATUS_QUEUING)
    db_session.add_all(
        [
            OrderFileVersion(
                order_id=order.id,
                version_no=1,
                file_key=old_key,
                page_count=10,
                amount_cents=150,
                is_current=0,
            ),
            OrderFileVersion(
                order_id=order.id,
                version_no=2,
                file_key=current_key,
                page_count=20,
                amount_cents=300,
                is_current=1,
            ),
        ]
    )
    db_session.flush()

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert old_key in fake_storage.deleted
    assert current_key in fake_storage.objects
    assert stats.deleted == 1
    assert stats.skipped == 1


def test_历史版本在保留期内不提前删除(db_session, fake_storage):
    old_key = "files/2026/03/version-fresh.pdf"
    current_key = "files/2026/03/version-current-fresh.pdf"
    fake_storage.put(old_key, FRESH_AT)
    fake_storage.put(current_key, FRESH_AT)

    order = _add_order(db_session, current_key, STATUS_QUEUING)
    db_session.add(
        OrderFileVersion(
            order_id=order.id,
            version_no=1,
            file_key=old_key,
            page_count=10,
            amount_cents=150,
            is_current=0,
        )
    )
    db_session.flush()

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.deleted == 0
    assert old_key in fake_storage.objects

def test_多文件订单的非首份文件仍受在途保护(db_session, fake_storage):
    """订单表只挂第一份文件，第二份及以后只在清单表里，清理不能误删。"""
    first_key = "files/2026/02/multi-first.pdf"
    second_key = "files/2026/02/multi-second.pdf"
    fake_storage.put(first_key, EXPIRED_AT)
    fake_storage.put(second_key, EXPIRED_AT)

    order = _add_order(db_session, first_key, STATUS_QUEUING)
    db_session.add(
        OrderFile(
            order_id=order.id,
            file_index=2,
            file_key=second_key,
            page_count=5,
            amount_cents=75,
        )
    )
    db_session.flush()

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.deleted == 0
    assert stats.skipped == 2
    assert first_key in fake_storage.objects
    assert second_key in fake_storage.objects


def test_清理过期文件时同步删除上传记录(db_session, fake_storage):
    key = "files/2026/02/with-record.pdf"
    fake_storage.put(key, EXPIRED_AT)
    db_session.add(
        UploadedFile(
            user_id=1,
            file_key=key,
            file_name="有记录.pdf",
            size=1024,
            content_type="application/pdf",
        )
    )
    db_session.flush()

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.deleted == 1
    assert db_session.execute(
        select(UploadedFile).where(UploadedFile.file_key == key)
    ).scalar_one_or_none() is None


# ---------------------------------------------------------------- 报告与文件同寿命


def _add_report(db_session, file_key: str, created_at: datetime) -> AiReport:
    """插入一条体检报告缓存行，只关心 file_key 与创建时间。"""
    report = AiReport(file_key=file_key, report_json={"kind": "parse"}, created_at=created_at)
    db_session.add(report)
    db_session.flush()
    return report


def _report_keys(db_session) -> list[str]:
    return [row.file_key for row in db_session.execute(select(AiReport)).scalars().all()]


def test_文件过期清理时文件_上传记录_体检报告三者同轮一起没了(db_session, fake_storage):
    """文件过了 72 小时又没人引用：文件、上传记录、对应报告一轮里全清掉。

    报告本身还在自己的保留期内（一小时前生成），它消失的唯一原因只能是跟着文件走，
    所以这条同时验证「同轮一起删」，而不是等下一轮报告清理。
    """
    key = "files/2026/02/triple.pdf"
    fake_storage.put(key, EXPIRED_AT)
    db_session.add(
        UploadedFile(
            user_id=1,
            file_key=key,
            file_name="三件套.pdf",
            size=1024,
            content_type="application/pdf",
        )
    )
    _add_report(db_session, key, NOW - timedelta(hours=1))

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.deleted == 1
    assert stats.reports_deleted == 1
    assert fake_storage.deleted == [key]
    assert key not in fake_storage.objects
    assert db_session.execute(
        select(UploadedFile).where(UploadedFile.file_key == key)
    ).scalar_one_or_none() is None
    assert _report_keys(db_session) == []


def test_文件还在保留期内时报告不提前清(db_session, fake_storage):
    """报告自己的创建时间早就过了 72 小时，但文件还在，报告就不能先走。"""
    key = "files/2026/03/kept.pdf"
    fake_storage.put(key, FRESH_AT)
    _add_report(db_session, key, REPORT_EXPIRED_AT)

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.deleted == 0
    assert stats.reports_deleted == 0
    assert _report_keys(db_session) == [key]
    assert key in fake_storage.objects


def test_文件已不存在但报告残留时过保留小时后单独清掉(db_session, fake_storage):
    """历史数据或人工删掉文件留下的报告，没有文件可跟，按同一保留期兜底清。"""
    key = "files/2026/02/orphan-report.pdf"
    _add_report(db_session, key, REPORT_EXPIRED_AT)

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.deleted == 0
    assert stats.reports_deleted == 1
    assert _report_keys(db_session) == []


def test_文件已不存在但报告未过保留小时时留着(db_session, fake_storage):
    """文件刚被人工删掉、报告还很新，等它过保留期再由兜底清理收走。"""
    key = "files/2026/03/orphan-report-fresh.pdf"
    _add_report(db_session, key, NOW - timedelta(hours=1))

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.reports_deleted == 0
    assert _report_keys(db_session) == [key]


def test_在途订单引用时文件与报告都不清(db_session, fake_storage):
    """文件受在途保护，报告跟着文件留，不能因为自己老了就提前删。"""
    key = "files/2026/02/in-flight-report.pdf"
    fake_storage.put(key, EXPIRED_AT)
    _add_order(db_session, key, STATUS_QUEUING)
    _add_report(db_session, key, REPORT_EXPIRED_AT)

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.skipped == 1
    assert stats.deleted == 0
    assert stats.reports_deleted == 0
    assert key in fake_storage.objects
    assert _report_keys(db_session) == [key]


def test_待确认换文件的新文件及其报告都不清(db_session, fake_storage):
    """学生刚传、老师还没确认的新文件同样受保护，报告一起留着。"""
    old_key = "files/2026/02/change-report-old.pdf"
    new_key = "files/2026/02/change-report-new.pdf"
    fake_storage.put(old_key, EXPIRED_AT)
    fake_storage.put(new_key, EXPIRED_AT)
    _add_report(db_session, new_key, REPORT_EXPIRED_AT)

    order = _add_order(db_session, old_key, STATUS_QUEUING)
    db_session.add(
        OrderFileChange(
            order_id=order.id,
            status=CHANGE_PENDING,
            pending_flag=1,
            old_file_key=old_key,
            new_file_key=new_key,
            new_page_count=20,
            new_amount_cents=300,
        )
    )
    db_session.flush()

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.deleted == 0
    assert stats.pending_protected == 1
    assert stats.reports_deleted == 0
    assert new_key in fake_storage.objects
    assert _report_keys(db_session) == [new_key]


def test_文件删除失败时报告跟着留着(db_session, fake_storage):
    """对象没删掉，报告也不能单独删，否则又会变成「文件在、报告没了」。"""
    key = "files/2026/02/delete-failed-report.pdf"
    fake_storage.put(key, EXPIRED_AT)
    fake_storage.fail_keys.add(key)
    _add_report(db_session, key, REPORT_EXPIRED_AT)

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.failed == 1
    assert stats.deleted == 0
    assert stats.reports_deleted == 0
    assert key in fake_storage.objects
    assert _report_keys(db_session) == [key]


# ---------------------------------------------------------------- 上传记录的僵尸行
def _add_upload_record(db_session, file_key: str, user_id: int = 1):
    record = UploadedFile(
        user_id=user_id,
        file_key=file_key,
        file_name="僵尸.pdf",
        size=1024,
        content_type="application/pdf",
    )
    db_session.add(record)
    db_session.flush()
    return record


def _record_exists(db_session, file_key: str) -> bool:
    return (
        db_session.execute(
            select(UploadedFile).where(UploadedFile.file_key == file_key)
        ).scalar_one_or_none()
        is not None
    )


def test_对象已不在的上传记录被清掉(db_session, fake_storage):
    """对象删掉了、上传记录还留着：下一轮永远扫不到它（对象已不在 list 结果里）。

    学生点预览、下单会一直拿到 40053，而这一行会永久留在表里。所以每轮按上传记录
    反查对象是否存在，缺失就把记录连同它的报告一起删。
    """
    key = "files/2026/02/zombie.pdf"
    _add_upload_record(db_session, key)
    _add_report(db_session, key, FRESH_AT)

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.records_deleted == 1
    assert stats.reports_deleted == 1
    assert not _record_exists(db_session, key)
    assert _report_keys(db_session) == []


def test_演练模式只统计不清僵尸记录(db_session, fake_storage):
    key = "files/2026/02/zombie-dry.pdf"
    _add_upload_record(db_session, key)

    stats = run_cleanup(db_session, fake_storage, now=NOW, dry_run=True)

    assert stats.records_deleted == 1
    assert stats.dry_run is True
    assert _record_exists(db_session, key)


def test_列不出对象时不碰上传记录(db_session, fake_storage):
    """列举对象失败时无法判断谁不在，删记录会把文件还在的上传记录删掉。"""
    key = "files/2026/02/alive.pdf"
    fake_storage.put(key, FRESH_AT)
    _add_upload_record(db_session, key)
    fake_storage.list_error = RuntimeError("对象存储不可用")

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.records_deleted == 0
    assert _record_exists(db_session, key)


def test_列举结果滞后但对象其实存在时不动记录(db_session, fake_storage, monkeypatch):
    """列目录可能有延迟或前缀差异，删除前必须再问一次对象存储。"""
    key = "files/2026/02/lagging.pdf"
    fake_storage.put(key, FRESH_AT)
    _add_upload_record(db_session, key)
    monkeypatch.setattr(fake_storage, "list_objects", lambda prefix="": [])

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.records_deleted == 0
    assert _record_exists(db_session, key)


def test_对象还在的普通上传记录不受影响(db_session, fake_storage):
    alive = "files/2026/02/alive-normal.pdf"
    fake_storage.put(alive, FRESH_AT)
    _add_upload_record(db_session, alive)

    stats = run_cleanup(db_session, fake_storage, now=NOW)

    assert stats.records_deleted == 0
    assert _record_exists(db_session, alive)
