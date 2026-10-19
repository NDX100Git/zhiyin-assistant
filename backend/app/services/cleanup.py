"""定时清理：过期上传文件与体检报告缓存。

规则：
1. 上传文件：对象存储中超过 FILE_EXPIRE_HOURS 的文件会被删除，但只要还有
   未完结订单引用该文件就跳过。未完结指状态不是「已完成」也不是「已取消」，
   包含待支付 / 排队中 / 打印中 / 待取件 / 异常，宁可少删也不能删掉在途文件。
2. 体检报告（ai_reports）：与文件同寿命，跟着文件走，不单独按天数清。
   - 文件被删掉的那一轮，同一 file_key 的报告缓存一起删，不等下一轮；
   - 文件还在（含在途保护而留下的）就跟着留，不提前删；
   - 文件已不存在但报告残留（历史数据、人工删了文件）时，按
     report_expire_hours（即 file_expire_hours）兜底清掉，不留垃圾。

对外入口是 run_cleanup(db, storage, now=None, dry_run=False)，纯函数式、便于单测；
后台调度由 cleanup_loop() 负责，单轮异常只记日志，不让任务静默死掉。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.ai_report import AiReport
from app.models.order import Order
from app.models.order_file import OrderFile
from app.models.order_file_change import CHANGE_PENDING, OrderFileChange
from app.models.order_file_version import OrderFileVersion
from app.models.uploaded_file import UploadedFile
from app.services.order_state import STATUS_CANCELLED, STATUS_COMPLETED
from app.services.storage import FileStorage, get_storage

logger = logging.getLogger(__name__)

# 上传文件统一落在 files/ 前缀下（见 storage.keys.build_object_key）
OBJECT_PREFIX = "files/"
# 已完结状态：只有这些状态引用的过期文件可以删
FINISHED_STATUSES = frozenset({STATUS_COMPLETED, STATUS_CANCELLED})
# 异常单不算完结，但也没有自动超时，所以给它一个额外的宽限上限而不是无限期保留
ABNORMAL_STATUS = "abnormal"
# 按 file_key 批量查订单时每批的数量，避免 IN 列表过长
ORDER_QUERY_CHUNK = 500
# 启动后先等一小会儿再跑第一轮，给数据库与对象存储留出就绪时间
FIRST_RUN_DELAY_SECONDS = 60


@dataclass
class CleanupStats:
    """一轮清理的统计结果。dry_run 时 deleted 表示「将会删除」的数量。"""

    scanned: int = 0           # 列出并检查的上传文件数
    deleted: int = 0           # 成功删除（或演练时将会删除）的文件数
    skipped: int = 0           # 因未完结订单引用而跳过的文件数
    failed: int = 0            # 删除失败的文件数，下一轮会重新扫到
    not_expired: int = 0       # 仍在保留期内、本轮不处理的文件数
    reports_deleted: int = 0   # 删除（或将会删除）的体检报告缓存行数，含随文件同删与残留兜底两块
    pending_protected: int = 0 # 因待老师确认的换文件请求而跳过的文件数
    dry_run: bool = False

    def as_dict(self) -> dict[str, int | bool]:
        """转成可直接写日志或返回的字典。"""
        return {
            "scanned": self.scanned,
            "deleted": self.deleted,
            "skipped": self.skipped,
            "failed": self.failed,
            "not_expired": self.not_expired,
            "reports_deleted": self.reports_deleted,
            "pending_protected": self.pending_protected,
            "dry_run": self.dry_run,
        }


def _as_local_naive(value: datetime) -> datetime:
    """把时间统一成本地时间且不带时区，与数据库 DATETIME 列的口径一致。

    MinIO 返回带时区的 UTC 时间，直接拿它和 datetime.now() 比较会差出整个时区。
    """
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _load_order_statuses(db: Session, keys: list[str]) -> dict[str, set[str]]:
    """按 file_key 汇总引用该文件的订单状态。

    三张表都看：订单表上的第一份文件（也覆盖没有清单行的老数据）、订单文件
    清单里的全部当前文件，以及版本表里标记为当前生效的版本。历史版本故意不计入
    引用，换下来的旧文件过了保留期就可以回收，不额外续命。
    """
    statuses: dict[str, set[str]] = {}
    for start in range(0, len(keys), ORDER_QUERY_CHUNK):
        chunk = keys[start:start + ORDER_QUERY_CHUNK]
        rows = db.execute(
            select(Order.file_key, Order.status).where(Order.file_key.in_(chunk))
        ).all()
        for file_key, status in rows:
            if file_key:
                statuses.setdefault(file_key, set()).add(status)

        version_rows = db.execute(
            select(OrderFileVersion.file_key, Order.status)
            .join(Order, Order.id == OrderFileVersion.order_id)
            .where(
                OrderFileVersion.file_key.in_(chunk),
                OrderFileVersion.is_current == 1,
            )
        ).all()
        for file_key, status in version_rows:
            if file_key:
                statuses.setdefault(file_key, set()).add(status)

        # 多文件订单的第二份及以后只出现在清单表里，必须单独查
        file_rows = db.execute(
            select(OrderFile.file_key, Order.status)
            .join(Order, Order.id == OrderFile.order_id)
            .where(OrderFile.file_key.in_(chunk))
        ).all()
        for file_key, status in file_rows:
            if file_key:
                statuses.setdefault(file_key, set()).add(status)
    return statuses


def _load_pending_file_keys(db: Session, keys: list[str]) -> set[str]:
    """待老师确认的换文件请求引用的新文件。"""
    pending: set[str] = set()
    for start in range(0, len(keys), ORDER_QUERY_CHUNK):
        chunk = keys[start:start + ORDER_QUERY_CHUNK]
        rows = db.execute(
            select(OrderFileChange.new_file_key).where(
                OrderFileChange.new_file_key.in_(chunk),
                OrderFileChange.status == CHANGE_PENDING,
            )
        ).scalars()
        pending.update(key for key in rows if key)
    return pending


def _delete_reports_of_file(
    db: Session,
    file_key: str,
    dry_run: bool,
    stats: CleanupStats,
) -> int:
    """删除某一份文件的体检报告缓存，返回删除（或演练时将会删除）的行数。

    文件与报告必须同轮一起走：只删文件会把报告留成孤儿，下一轮虽然能兜底，
    但中间这段时间学生若还在看报告页就会拿到一份已经取不到文件的报告。
    """
    if dry_run:
        count = db.execute(
            select(func.count())
            .select_from(AiReport)
            .where(AiReport.file_key == file_key)
        ).scalar_one()
        stats.reports_deleted += int(count)
        return int(count)

    result = db.execute(
        delete(AiReport)
        .where(AiReport.file_key == file_key)
        .execution_options(synchronize_session=False)
    )
    removed = int(result.rowcount or 0)
    stats.reports_deleted += removed
    return removed


def _cleanup_files(
    db: Session,
    storage: FileStorage,
    now: datetime,
    dry_run: bool,
    stats: CleanupStats,
) -> set[str] | None:
    """删除超过保留期且没有未完结订单引用的上传文件。

    返回本轮对象存储里列出的 file_key 集合：报告清理靠它区分「跟文件走」与
    「文件早已不在的残留」。本轮被删掉的文件也在里面，免得报告兜底再算一遍。
    列举对象失败时返回 None，调用方据此跳过残留报告清理，否则会把文件其实
    还在的报告误删掉。
    """
    cutoff = now - timedelta(hours=settings.file_expire_hours)
    abnormal_cutoff = cutoff - timedelta(days=settings.abnormal_file_grace_days)
    try:
        objects = storage.list_objects(OBJECT_PREFIX)
    except Exception:
        # 列不出来就无法判断谁过期，计入失败并跳过本轮文件清理，下一轮重试
        stats.failed += 1
        logger.exception("列出对象存储文件失败，本轮跳过文件清理")
        return None

    listed_keys = {obj.key for obj in objects}
    stats.scanned = len(objects)
    expired = [obj for obj in objects if _as_local_naive(obj.last_modified) <= cutoff]
    stats.not_expired = stats.scanned - len(expired)
    if not expired:
        return listed_keys

    references = _load_order_statuses(db, [obj.key for obj in expired])
    # 还没确认的新文件不能被清掉：学生可能刚传完，老师还没来得及点确认
    pending_keys = _load_pending_file_keys(db, [obj.key for obj in expired])

    for obj in expired:
        if obj.key in pending_keys:
            stats.skipped += 1
            stats.pending_protected += 1
            logger.info("跳过文件 %s：仍是待老师确认的换文件请求引用的新文件", obj.key)
            continue

        statuses = references.get(obj.key, set())
        # 异常单享受额外宽限：没超过宽限上限就继续留着
        in_flight = statuses - FINISHED_STATUSES - {ABNORMAL_STATUS}
        if in_flight:
            stats.skipped += 1
            logger.info(
                "跳过文件 %s：仍被未完结订单引用（状态 %s）", obj.key, "、".join(sorted(in_flight))
            )
            continue
        if ABNORMAL_STATUS in statuses and _as_local_naive(obj.last_modified) > abnormal_cutoff:
            stats.skipped += 1
            logger.info(
                "跳过文件 %s：仍被异常单引用，且未超过额外宽限的 %d 天",
                obj.key,
                settings.abnormal_file_grace_days,
            )
            continue

        if dry_run:
            stats.deleted += 1
            reports = _delete_reports_of_file(db, obj.key, dry_run=True, stats=stats)
            logger.info("[演练] 将删除过期文件 %s 及其 %d 条体检报告缓存", obj.key, reports)
            continue

        try:
            storage.delete(obj.key)
        except Exception:
            # 不吞异常：计入失败并保留日志，对象没删掉，下一轮还能扫到
            stats.failed += 1
            logger.exception("删除文件失败，下一轮重试：%s", obj.key)
            continue

        # 对象已经删掉，上传记录一并清掉，避免表里留下不可撤回的僵尸行
        db.execute(
            delete(UploadedFile)
            .where(UploadedFile.file_key == obj.key)
            .execution_options(synchronize_session=False)
        )
        # 报告跟文件同寿命：文件这一轮走的，报告同一轮走，不等下一轮
        reports = _delete_reports_of_file(db, obj.key, dry_run=False, stats=stats)
        stats.deleted += 1
        logger.info("已删除过期文件 %s，同时删除体检报告缓存 %d 条", obj.key, reports)

    return listed_keys


def _cleanup_reports(
    db: Session,
    now: datetime,
    dry_run: bool,
    stats: CleanupStats,
    listed_keys: set[str] | None,
) -> None:
    """兜底清理「文件已不在、报告还在」的残留报告缓存。

    文件还在的报告一律不动：报告跟文件同寿命，文件因为保留期或异常单宽限还留着时，
    报告也不能先走。判断依据是本轮对象存储里列出的 key（含本轮刚删掉的文件，
    它们的报告已经同轮删过），所以列对象失败时必须跳过。
    """
    if listed_keys is None:
        logger.info("对象存储未列出，本轮跳过残留体检报告清理")
        return

    cutoff = now - timedelta(hours=settings.report_expire_hours)
    rows = db.execute(
        select(AiReport.id, AiReport.file_key).where(AiReport.created_at < cutoff)
    ).all()
    orphan_ids = [row.id for row in rows if row.file_key not in listed_keys]
    if not orphan_ids:
        return

    if dry_run:
        stats.reports_deleted += len(orphan_ids)
        logger.info("[演练] 将删除 %d 条残留体检报告缓存（对应文件已不在）", len(orphan_ids))
        return

    removed = 0
    for start in range(0, len(orphan_ids), ORDER_QUERY_CHUNK):
        chunk = orphan_ids[start:start + ORDER_QUERY_CHUNK]
        result = db.execute(
            delete(AiReport)
            .where(AiReport.id.in_(chunk))
            .execution_options(synchronize_session=False)
        )
        removed += int(result.rowcount or 0)
    stats.reports_deleted += removed
    logger.info("已删除 %d 条残留体检报告缓存（对应文件已不在）", removed)


def run_cleanup(
    db: Session,
    storage: FileStorage,
    now: datetime | None = None,
    dry_run: bool = False,
) -> CleanupStats:
    """执行一轮清理，返回统计。

    now 可注入便于测试；不传时取服务端本地时间。dry_run 只统计不真正删除。
    不提交事务，由调用方决定事务边界。
    """
    reference = _as_local_naive(now or datetime.now())
    stats = CleanupStats(dry_run=dry_run)
    logger.info(
        "清理开始（%s）：文件保留 %d 小时，体检报告与文件同寿命（%d 小时）",
        "演练" if dry_run else "实跑",
        settings.file_expire_hours,
        settings.report_expire_hours,
    )

    listed_keys = _cleanup_files(db, storage, reference, dry_run, stats)
    _cleanup_reports(db, reference, dry_run, stats, listed_keys)

    logger.info(
        "清理完成：删除文件 %d 个，删除体检报告 %d 条，%s",
        stats.deleted,
        stats.reports_deleted,
        stats.as_dict(),
    )
    return stats


def run_cleanup_once(dry_run: bool = False) -> CleanupStats | None:
    """使用独立会话跑一轮清理并提交。异常记日志后返回 None，交给下一轮重试。"""
    db = SessionLocal()
    try:
        stats = run_cleanup(db, get_storage(), dry_run=dry_run)
        db.commit()
        return stats
    except Exception:
        db.rollback()
        logger.exception("定时清理本轮失败，下一轮会自动重试")
        return None
    finally:
        db.close()


async def cleanup_loop(
    interval_minutes: int | None = None,
    first_delay_seconds: float = FIRST_RUN_DELAY_SECONDS,
) -> None:
    """后台清理循环：定期跑一轮，任何单轮异常都只记日志，任务本身不退出。"""
    interval = max(1, interval_minutes or settings.cleanup_interval_minutes)
    await asyncio.sleep(first_delay_seconds)

    while True:
        try:
            # 清理是阻塞 IO（数据库 + 对象存储），放线程里跑，别卡住事件循环
            await asyncio.to_thread(run_cleanup_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("定时清理出现未预期异常，后台任务继续运行")
        await asyncio.sleep(interval * 60)
