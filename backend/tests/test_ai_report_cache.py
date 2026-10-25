"""体检报告缓存按账号隔离（产品口径：各账号各存一份，互不共用）。

报告里有整篇正文、问题清单与修改建议，只要两个账号能碰到同一个 file_key，
只看 file_key 的缓存就会把 A 的报告发给 B。所以缓存键是「账号 + 文件」，
这里从三个角度钉住它：

1. 接口层：两个账号各体检一次，ai_reports 里两行、各带自己的账号；
2. 服务层：同一个 file_key 先按账号 11 体检、再按账号 22 体检，缓存互不串，
   各自再体检一次都命中自己那一份，不再重新解析；
3. 迁移：`ai_reports.user_id` 列幂等补齐，迁移语句拼接前会拦下非法标识符。
"""

from __future__ import annotations

import io
import itertools
import time

import fitz
import pytest
from sqlalchemy import select, text

from app.migrations import (
    MIGRATION_COLUMNS,
    UnsafeIdentifierError,
    quote_identifier,
    run_migrations,
)
from app.models.ai_report import AiReport
from app.models.uploaded_file import UploadedFile
from app.services import proofreader, task_registry
from app.services.doc_parser import bind_report_owner, get_cached_report, save_report
from app.services.format_checker import check_format
from app.services.storage import build_object_key

PDF_MIME = "application/pdf"


# ---------------------------------------------------------------- 工具
def _login(client, device_id: str) -> tuple[dict[str, str], int]:
    body = client.post("/api/auth/login", json={"device_id": device_id}).json()
    assert body["code"] == 0, body
    data = body["data"]
    return {"Authorization": f"Bearer {data['token']}"}, int(data["user"]["id"])


def _upload(client, headers: dict[str, str], name: str, data: bytes) -> str:
    body = client.post(
        "/api/file/upload", files={"file": (name, data, PDF_MIME)}, headers=headers
    ).json()
    assert body["code"] == 0, body
    return body["data"]["file_key"]


def _make_pdf(path) -> bytes:
    """一页最小 PDF，够解析出页数与正文。"""
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((90, 100), "Abstract")
        page.insert_text((90, 130), "This paper studies format checking.")
        document.save(str(path))
    return path.read_bytes()


def _report_rows(db_session, file_key: str, kind: str) -> list[AiReport]:
    return list(
        db_session.execute(
            select(AiReport)
            .where(AiReport.file_key == file_key)
            .where(AiReport.report_json["kind"].as_string() == kind)
            .order_by(AiReport.id)
        ).scalars()
    )


def _install_model(monkeypatch, counter: itertools.count | None = None) -> list[int]:
    """替身模型：每次调用产出一个带序号的独立问题，用序号区分是谁的报告。"""
    seen: list[int] = []
    counter = counter or itertools.count(1)

    def fake_chat(messages, thinking: bool = False):
        number = next(counter)
        seen.append(number)
        return {
            "issues": [
                {
                    "type": "ai_note",
                    "severity": "medium",
                    "location": f"标记{number}",
                    "original": f"第{number}次模型调用",
                    "suggestion": "只需人工确认",
                    "reason": "区分不同账号的报告内容",
                }
            ]
        }

    monkeypatch.setattr("app.services.format_checker.chat_json", fake_chat)
    return seen


# ---------------------------------------------------------------- 接口层
def test_两个账号上传同一份文件各自体检_库里各存一行(client, db_session, test_storage, tmp_path, monkeypatch):
    """同一份文件被两个账号体检：ai_reports 里两行，各带自己的账号，内容互不串。"""
    alice, alice_id = _login(client, "cache-alice")
    bob, bob_id = _login(client, "cache-bob")
    pdf = _make_pdf(tmp_path / "论文.pdf")
    alice_key = _upload(client, alice, "论文.pdf", pdf)
    bob_key = _upload(client, bob, "论文.pdf", pdf)
    assert alice_key != bob_key, "对象 key 由服务端生成，两次上传必然不同"

    seen = _install_model(monkeypatch)

    first = client.post("/api/ai/check", json={"file_key": alice_key}, headers=alice).json()
    assert first["code"] == 0, first
    second = client.post("/api/ai/check", json={"file_key": bob_key}, headers=bob).json()
    assert second["code"] == 0, second

    alice_rows = _report_rows(db_session, alice_key, "check")
    bob_rows = _report_rows(db_session, bob_key, "check")
    assert len(alice_rows) == 1 and len(bob_rows) == 1
    assert alice_rows[0].user_id == alice_id
    assert bob_rows[0].user_id == bob_id
    assert alice_rows[0].report_json["issues"] != bob_rows[0].report_json["issues"]

    # 论文口径要跑「结构校验 + 版式诊断」两个模型任务，两个账号共四次
    assert len(seen) == 4

    # 各自再体检一次：命中自己的缓存，不再调用模型
    third = client.post("/api/ai/check", json={"file_key": alice_key}, headers=alice).json()
    assert third["code"] == 0
    assert third["data"]["generated_at"] == first["data"]["generated_at"]
    assert third["data"]["issues"] == first["data"]["issues"]
    assert len(seen) == 4, "命中缓存不该再调用模型"
    assert len(_report_rows(db_session, alice_key, "check")) == 1


# ---------------------------------------------------------------- 服务层（同一 file_key）
def test_同一个文件按两个账号各存一份报告互不串(db_session, test_storage, tmp_path, monkeypatch):
    """缓存的键必须是「账号 + 文件」：同一 file_key 下两个账号各一份，各自命中自己。"""
    pdf_path = tmp_path / "shared.pdf"
    pdf = _make_pdf(pdf_path)
    key = build_object_key("shared.pdf")
    test_storage.upload(key, io.BytesIO(pdf), len(pdf), PDF_MIME)

    seen = _install_model(monkeypatch)

    bind_report_owner(db_session, 11)
    first = check_format(db_session, key)
    bind_report_owner(db_session, 22)
    second = check_format(db_session, key)

    rows = _report_rows(db_session, key, "check")
    assert len(rows) == 2, "两个账号各存一行"
    assert [row.user_id for row in rows] == [11, 22]
    ai_first = {issue["original"] for issue in first["issues"] if issue["source"] == "ai"}
    ai_second = {issue["original"] for issue in second["issues"] if issue["source"] == "ai"}
    assert ai_first and ai_second
    assert not (ai_first & ai_second), "模型给出的结论各不相同，两份报告不能串内容"

    calls = len(seen)
    bind_report_owner(db_session, 11)
    again = check_format(db_session, key)
    assert again["generated_at"] == first["generated_at"]
    assert len(seen) == calls, "命中自己的缓存不该再调用模型"

    # 解析缓存同样按账号各存一份
    parse_rows = _report_rows(db_session, key, "parse")
    assert [row.user_id for row in parse_rows] == [11, 22]


def test_迁移前的老缓存行被当前账号认领后不再共享(db_session):
    """老库里 user_id 为空的报告行没有归属信息，只认领一次，之后各看各的。"""
    key = "files/2026/10/legacy.pdf"
    save_report(db_session, key, {"file_key": key, "summary": "迁移前的老报告"}, "check")

    legacy = _report_rows(db_session, key, "check")
    assert len(legacy) == 1 and legacy[0].user_id is None

    bind_report_owner(db_session, 11)
    adopted = get_cached_report(db_session, key, "check")
    assert adopted["summary"] == "迁移前的老报告"
    db_session.expire_all()
    assert _report_rows(db_session, key, "check")[0].user_id == 11

    # 另一个账号读不到已经被认领的那一行
    bind_report_owner(db_session, 22)
    assert get_cached_report(db_session, key, "check") is None


def test_没绑定账号时会话行为与改造前一致(db_session):
    """下单链路、后台校对用独立会话且不带账号上下文，缓存读写仍然可用。"""
    key = "files/2026/10/no-owner.pdf"
    save_report(db_session, key, {"file_key": key, "summary": "无账号上下文"}, "parse")

    cached = get_cached_report(db_session, key, "parse")
    assert cached["summary"] == "无账号上下文"
    assert _report_rows(db_session, key, "parse")[0].user_id is None


# ---------------------------------------------------------------- 迁移
def test_ai_reports的user_id列能幂等补齐(engine):
    """老库没有 user_id 列时补上；再跑一遍不报错、不重复补。"""
    with engine.begin() as conn:
        if not conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ai_reports' "
                "AND INDEX_NAME = 'idx_report_owner'"
            )
        ).scalar_one():
            conn.execute(
                text("ALTER TABLE `ai_reports` ADD KEY `idx_report_owner` (`user_id`, `file_key`)")
            )
        conn.execute(text("ALTER TABLE `ai_reports` DROP INDEX `idx_report_owner`"))
        conn.execute(text("ALTER TABLE `ai_reports` DROP COLUMN `user_id`"))

    first = run_migrations(engine)
    assert "ai_reports.user_id" in first
    assert "ai_reports.idx_report_owner" in first

    second = run_migrations(engine)
    assert "ai_reports.user_id" not in second
    assert "ai_reports.idx_report_owner" not in second

    with engine.begin() as conn:
        columns = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ai_reports'"
                )
            )
        }
    assert {"user_id", "file_key", "report_json"} <= columns


def test_迁移只接受合法的表名列名():
    """表名列名要拼进 ALTER 语句，非法字符一律拒绝。"""
    for bad in ("orders; DROP TABLE users", "orders`", "", "1orders", "orders-1", "订单"):
        with pytest.raises(UnsafeIdentifierError):
            quote_identifier(bad)

    assert quote_identifier("ai_reports") == "`ai_reports`"


def test_迁移配置里的标识符全部合法():
    for table, columns in MIGRATION_COLUMNS.items():
        quote_identifier(table)
        for column in columns:
            quote_identifier(column)


def test_非法列名在拼语句之前就被拦下(engine, monkeypatch):
    """白名单必须挡在真正执行 ALTER 的路上，而不是只在一个没人调用的函数里。"""
    monkeypatch.setitem(
        MIGRATION_COLUMNS, "orders", {"bad` INT; DROP TABLE users; --": "INT NULL"}
    )
    with pytest.raises(UnsafeIdentifierError):
        run_migrations(engine)


def test_上传记录与报告各自记着账号(db_session):
    """报告行的 user_id 与上传记录的 user_id 是同一口径（都是 User.id）。"""
    user_id = 7
    key = build_object_key("owner.pdf")
    db_session.add(
        UploadedFile(user_id=user_id, file_key=key, file_name="owner.pdf", size=1)
    )
    db_session.flush()

    bind_report_owner(db_session, user_id)
    save_report(db_session, key, {"file_key": key}, "parse")

    assert _report_rows(db_session, key, "parse")[0].user_id == user_id


# ---------------------------------------------------------------- 校对任务（后台线程）
def _wait_task(task_id: str, owner: str = "", timeout: float = 30.0) -> dict:
    """等后台校对任务结束，返回最后一次进度（owner 与任务归属一致才查得到）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        progress = proofreader.get_progress(task_id, owner)
        if progress["status"] in {task_registry.STATUS_DONE, task_registry.STATUS_FAILED}:
            return progress
        time.sleep(0.05)
    raise AssertionError(f"校对任务 {task_id} 未在 {timeout} 秒内结束")


def _install_proofread_stub(monkeypatch) -> list[int]:
    """把解析与模型都换掉：只验证缓存归属，不去跑真模型。"""
    parsed = {
        "page_count": 1,
        "pages": [{"index": 1, "text": "实验结杲表明，该方法有效。" * 30, "images": []}],
    }
    monkeypatch.setattr(
        "app.services.proofreader.parse_document", lambda db, key, force=False: parsed
    )
    seen: list[int] = []

    async def fake_achat_json(messages, thinking=False, temperature=None):
        seen.append(1)
        return {"issues": []}

    monkeypatch.setattr("app.services.proofreader.achat_json", fake_achat_json)
    return seen


def test_后台校对任务的缓存写到发起人名下(
    db_session, session_factory, test_storage, tmp_path, monkeypatch
):
    """校对在后台线程里用自己的会话跑，缓存行仍然必须带发起人。

    背景：路由把发起人给了 task_registry（owner），但后台 worker 拿到的是新建会话，
    没有账号上下文，缓存行 user_id 为空、缓存键退化成只按文件。
    """
    pdf = _make_pdf(tmp_path / "proof.pdf")
    key = build_object_key("proof.pdf")
    test_storage.upload(key, io.BytesIO(pdf), len(pdf), PDF_MIME)
    seen = _install_proofread_stub(monkeypatch)

    started = proofreader.start_proofread(key, session_factory=session_factory, owner="11")
    task = _wait_task(started["task_id"], "11")
    assert task["status"] == task_registry.STATUS_DONE, task
    assert len(seen) == 1

    db_session.rollback()  # 结束当前事务，读到后台会话写入的行
    rows = _report_rows(db_session, key, "proofread")
    assert len(rows) == 1
    assert rows[0].user_id == 11, "后台任务的缓存行必须记着发起人"

    # 换一个账号跑同一份文件：各存一行，互不命中
    other = proofreader.start_proofread(key, session_factory=session_factory, owner="22")
    assert _wait_task(other["task_id"], "22")["status"] == task_registry.STATUS_DONE
    assert len(seen) == 2, "另一个账号不能命中别人的校对缓存"

    db_session.rollback()
    rows = _report_rows(db_session, key, "proofread")
    assert [row.user_id for row in rows] == [11, 22]

    # 各自再跑一次：命中自己那一份，不再调用模型
    again = proofreader.start_proofread(key, session_factory=session_factory, owner="11")
    assert _wait_task(again["task_id"], "11")["status"] == task_registry.STATUS_DONE
    assert len(seen) == 2, "同一账号重复发起不该重新调用模型"


def test_没带发起人的校对任务缓存行为与改造前一致(
    db_session, session_factory, test_storage, tmp_path, monkeypatch
):
    """内部调用（不传 owner）没有账号上下文，缓存行留空但读写仍然可用。"""
    pdf = _make_pdf(tmp_path / "proof-anon.pdf")
    key = build_object_key("proof-anon.pdf")
    test_storage.upload(key, io.BytesIO(pdf), len(pdf), PDF_MIME)
    _install_proofread_stub(monkeypatch)

    started = proofreader.start_proofread(key, session_factory=session_factory)
    assert _wait_task(started["task_id"])["status"] == task_registry.STATUS_DONE

    db_session.rollback()
    rows = _report_rows(db_session, key, "proofread")
    assert len(rows) == 1
    assert rows[0].user_id is None


# ---------------------------------------------------------------- 解析缓存（无账号链路）
def _count_parses(monkeypatch) -> list[int]:
    """记录真解析次数：缓存命中时不该再进解析。"""
    from app.services import doc_parser as doc_parser_module

    real = doc_parser_module.parse_pdf
    calls: list[int] = []

    def wrapper(pdf_path):
        calls.append(1)
        return real(pdf_path)

    monkeypatch.setattr(doc_parser_module, "parse_pdf", wrapper)
    return calls


def test_页数读取与体检共用一条解析缓存(db_session, test_storage, tmp_path, monkeypatch):
    from app.services.doc_parser import parse_document

    pdf = _make_pdf(tmp_path / "page-first.pdf")
    key = build_object_key("page-first.pdf")
    test_storage.upload(key, io.BytesIO(pdf), len(pdf), PDF_MIME)
    _install_model(monkeypatch)
    parses = _count_parses(monkeypatch)

    # 页数读取：路由不绑账号（routers/file.py 的 /page-count、下单、打印方案）
    parse_document(db_session, key)
    assert len(_report_rows(db_session, key, "parse")) == 1

    # 体检：带账号上下文
    bind_report_owner(db_session, 11)
    check_format(db_session, key)

    assert len(_report_rows(db_session, key, "parse")) == 1, "同一文件只能留一行解析缓存"
    assert len(parses) == 1, "体检链路该复用页数链路已经解析好的结果"


def test_体检之后再走页数读取不再新写解析缓存行(db_session, test_storage, tmp_path, monkeypatch):
    from app.services.doc_parser import REPORT_OWNER_KEY, parse_document

    pdf = _make_pdf(tmp_path / "check-first.pdf")
    key = build_object_key("check-first.pdf")
    test_storage.upload(key, io.BytesIO(pdf), len(pdf), PDF_MIME)
    _install_model(monkeypatch)
    parses = _count_parses(monkeypatch)

    bind_report_owner(db_session, 11)
    check_format(db_session, key)
    assert len(_report_rows(db_session, key, "parse")) == 1

    # 下单/页数读取用的是没有账号上下文的会话
    db_session.info.pop(REPORT_OWNER_KEY, None)
    parse_document(db_session, key)

    assert len(_report_rows(db_session, key, "parse")) == 1, "无账号链路不该另写一行"
    assert len(parses) == 1, "无账号链路该复用已有解析结果"
