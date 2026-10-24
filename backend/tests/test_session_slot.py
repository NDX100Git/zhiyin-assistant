"""时段容量归还（取消 / 关闭异常单不占名额）。

用假会话验证分支，不连数据库：容量只增不减会让时段在取消几单后永久显示「已截单」，
老师手动重开也救不回来。
"""

from __future__ import annotations

from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.session_service import release_session_slot

_TODAY = date.today()


def _fake_db(row):
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = row
    return db


def _at(hour: int) -> datetime:
    return datetime.combine(_TODAY, time(hour, 0))


def test_取消订单归还一个名额():
    row = SimpleNamespace(date=_TODAY, session="pm", used=5)
    assert release_session_slot(_fake_db(row), _at(14)) is True
    assert row.used == 4


def test_已归零不再减():
    row = SimpleNamespace(date=_TODAY, session="pm", used=0)
    assert release_session_slot(_fake_db(row), _at(14)) is False
    assert row.used == 0


def test_时段行不存在时安全跳过():
    assert release_session_slot(_fake_db(None), _at(14)) is False


def test_非收单时段不动容量():
    db = _fake_db(SimpleNamespace(date=_TODAY, session="pm", used=5))
    assert release_session_slot(db, _at(23)) is False
    assert db.execute.called is False


def test_上午订单归还到上午时段():
    row = SimpleNamespace(date=_TODAY, session="am", used=2)
    assert release_session_slot(_fake_db(row), _at(10)) is True
    assert row.used == 1
