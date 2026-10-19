"""演示自检：一条命令走完 12 步演示链路，每步打印一句可直接当录屏旁白的中文。

用法（后端已起，默认 8000）：

    python scripts/demo_check.py
    python scripts/demo_check.py --base-url http://127.0.0.1:8000
    ZHIYIN_BASE_URL=http://127.0.0.1:8000 python scripts/demo_check.py

老师账号从 backend/.env 读 ADMIN_USERNAME 与 ADMIN_PASSWORD。
脚本自己生成 PDF，不依赖任何外部文件；任一步失败都会点名卡在哪一步，退出码非 0。
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
# 一句旁白最多 30 字：录屏时一句话讲完一步，多了会跟不上手速
NARRATION_LIMIT = 30


class StepFailed(Exception):
    """某一步的断言没过：消息直接面向录屏的人。"""


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def load_pricing(path: Path) -> dict[str, int]:
    """读后端价目表：脚本里的期望金额一律按它算，不写死数字。"""
    import json

    fallback = {"bw_single": 15, "bw_duplex": 15, "color_single": 80, "color_duplex": 80}
    if not path.is_file():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def build_pdf(pages: int, label: str) -> bytes:
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((72, 72), f"{label} page {index + 1}", fontsize=14)
    data = document.tobytes()
    document.close()
    return data


def call(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    ok_code: int = 0,
    **kwargs,
) -> dict:
    response = client.request(method, path, headers=headers, **kwargs)
    try:
        body = response.json()
    except ValueError:
        raise StepFailed(
            f"{method} {path} 返回的不是 JSON（HTTP {response.status_code}）："
            f"{response.text[:200]}"
        ) from None
    if body.get("code") != ok_code:
        raise StepFailed(
            f"{method} {path} 期望 code={ok_code}，实际 {body.get('code')}："
            f"{body.get('message')}"
        )
    return body.get("data") or {}


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise StepFailed(message)


def upload(client: httpx.Client, headers: dict[str, str], name: str, pages: int) -> dict:
    data = build_pdf(pages, name)
    uploaded = call(
        client,
        "POST",
        "/api/file/upload",
        headers=headers,
        files={"file": (name, data, "application/pdf")},
    )
    expect(bool(uploaded.get("file_key")), f"上传 {name} 后没拿到 file_key")
    return {"file_key": uploaded["file_key"], "file_name": uploaded["file_name"], "pages": pages}


@dataclass
class Demo:
    """一次演示自检里跨步骤共用的状态。"""

    client: httpx.Client
    pricing: dict[str, int]
    device_id: str
    admin_username: str
    admin_password: str
    student: dict[str, str] = field(default_factory=dict)
    admin: dict[str, str] = field(default_factory=dict)
    first: dict = field(default_factory=dict)
    second: dict = field(default_factory=dict)
    replacement: dict = field(default_factory=dict)
    replacement_bytes: bytes = b""
    quote: dict = field(default_factory=dict)
    order: dict = field(default_factory=dict)

    @property
    def order_id(self) -> int:
        return int(self.order["id"])

    def price(self, print_side: int, color_mode: int) -> int:
        color = "color" if color_mode == 1 else "bw"
        side = "duplex" if print_side == 2 else "single"
        return int(self.pricing[f"{color}_{side}"])

    def two_file_payload(self) -> dict:
        """两份参数不同的文件：第一份单面黑白 1 份，第二份双面彩色 2 份。"""
        return {
            "files": [
                {
                    "file_key": self.first["file_key"],
                    "file_name": self.first["file_name"],
                    "page_count": self.first["pages"],
                    "print_side": 1,
                    "color_mode": 0,
                    "paper_size": "A4",
                    "copies": 1,
                },
                {
                    "file_key": self.second["file_key"],
                    "file_name": self.second["file_name"],
                    "page_count": self.second["pages"],
                    "print_side": 2,
                    "color_mode": 1,
                    "paper_size": "A4",
                    "copies": 2,
                },
            ],
            "print_side": 2,
            "color_mode": 0,
            "copies": 1,
            "paper_size": "A4",
        }


def find_in_queue(
    client: httpx.Client, headers: dict[str, str], order_id: int, status: str = "queuing"
) -> dict:
    """在老师端队列里按订单 id 找这一单（队列可能有多页，从最后一页往前找）。"""
    first = call(
        client,
        "GET",
        "/api/admin/queue",
        headers=headers,
        params={"status": status, "page": 1, "page_size": 200},
    )
    total = int(first.get("total") or 0)
    pages = list(range(1, max(1, math.ceil(total / 200)) + 1))
    for page in reversed(pages):
        data = first
        if page != 1:
            data = call(
                client,
                "GET",
                "/api/admin/queue",
                headers=headers,
                params={"status": status, "page": page, "page_size": 200},
            )
        row = next(
            (item for item in data.get("items") or [] if int(item["id"]) == order_id), None
        )
        if row is not None:
            return row
    raise StepFailed(f"老师端队列（status={status}）里找不到订单 {order_id}")


def step_01_student_login(demo: Demo) -> None:
    token = call(
        demo.client, "POST", "/api/auth/login", json={"device_id": demo.device_id}
    )["token"]
    expect(bool(token), "学生登录没拿到 token")
    demo.student = {"Authorization": f"Bearer {token}"}
    print(f"  学生设备标识 {demo.device_id}，登录成功")


def step_02_upload_two_pdfs(demo: Demo) -> None:
    demo.first = upload(demo.client, demo.student, "演示-正文-2页.pdf", 2)
    demo.second = upload(demo.client, demo.student, "演示-附录-3页.pdf", 3)
    for item in (demo.first, demo.second):
        print(f"  已上传 {item['file_name']}（{item['pages']} 页）")


def step_03_quote_two_files(demo: Demo) -> None:
    payload = demo.two_file_payload()
    quote = call(demo.client, "POST", "/api/order/quote", headers=demo.student, json=payload)
    rows = quote.get("files") or []
    expect(len(rows) == 2, f"报价应返回 2 份文件，实际 {len(rows)} 份")

    # 第一份单面黑白 1 份、第二份双面彩色 2 份，逐份金额按价目表算
    layouts = [(1, 0, 1), (2, 1, 2)]
    expected: list[int] = []
    for item, (side, color, copies) in zip(rows, layouts, strict=True):
        print_pages = int(item["page_count"]) * copies
        amount = demo.price(side, color) * print_pages
        expected.append(amount)
        name = "双面彩色" if color == 1 else "单面黑白"
        print(
            f"  第 {item['file_index']} 份：{name} {copies} 份，"
            f"打印 {item['print_pages']} 页，金额 {item['amount_cents']} 分（报价算出 {amount} 分）"
        )
    expect(
        [int(item["amount_cents"]) for item in rows] == expected,
        f"逐份金额不对：报价 {[item['amount_cents'] for item in rows]} 分，应为 {expected} 分",
    )
    total = sum(expected)
    expect(
        int(quote["amount_cents"]) == total,
        f"整单金额应为 {total} 分，实际 {quote['amount_cents']} 分",
    )
    print(f"  整单 {quote['page_count']} 页文档、{quote['print_page_count']} 打印页，共 {total} 分")
    demo.quote = quote


def step_04_create_and_pay(demo: Demo) -> None:
    order = call(demo.client, "POST", "/api/order/create", headers=demo.student,
                 json=demo.two_file_payload())
    demo.order = order
    expect(int(order["amount_cents"]) == int(demo.quote["amount_cents"]),
           "下单金额与报价不一致（同一套计费才对）")
    print(f"  订单号 {order['order_no']}，取件码 {order['pickup_code']}，"
          f"金额 {order['amount_cents']} 分")
    paid = call(demo.client, "POST", f"/api/order/{demo.order_id}/pay", headers=demo.student)
    expect(paid["status"] == "queuing", f"支付后应为排队中，实际 {paid['status']}")
    position = int((paid.get("queue") or {}).get("queue_position", -1))
    print(f"  支付成功，当前排在第 {position} 位")


def step_05_student_order_list(demo: Demo) -> None:
    listing = call(
        demo.client, "GET", "/api/order/list", headers=demo.student,
        params={"page": 1, "page_size": 100},
    )
    mine = next(
        (item for item in listing.get("items") or [] if int(item["id"]) == demo.order_id), None
    )
    expect(mine is not None, "学生「我的订单」里找不到刚下的这一单")
    expect(mine["status"] == "queuing", f"学生侧状态应为排队中，实际 {mine['status']}")
    position = mine.get("queue_position")
    expect(isinstance(position, int), "学生侧订单没有返回排队位置")
    print(f"  订单 {mine['order_no']}：{mine['status_label']}，"
          f"排在前面 {position} 单，预计 {mine['estimated_minutes']} 分钟")


def step_06_admin_queue(demo: Demo) -> None:
    token = call(
        demo.client, "POST", "/api/admin/login",
        json={"username": demo.admin_username, "password": demo.admin_password},
    )["token"]
    expect(bool(token), "老师登录没拿到 token")
    demo.admin = {"Authorization": f"Bearer {token}"}
    print(f"  老师 {demo.admin_username} 登录成功")

    row = find_in_queue(demo.client, demo.admin, demo.order_id)
    files = row.get("files") or []
    expect(len(files) == 2, f"队列里这一单应有 2 份文件，实际 {len(files)} 份")
    names = "、".join(f"{item['file_index']}.{item['file_name']}" for item in files)
    print(f"  队列里看到订单 {row['order_no']}，两份文件：{names}")
    expect(int(row["print_page_count"]) == int(demo.quote["print_page_count"]),
           "队列里的整单打印页数与报价不一致")


def step_07_student_replace_second(demo: Demo) -> None:
    demo.replacement_bytes = build_pdf(4, "演示-附录-换稿-4页.pdf")
    uploaded = call(
        demo.client, "POST", "/api/file/upload", headers=demo.student,
        files={"file": ("演示-附录-换稿-4页.pdf", demo.replacement_bytes, "application/pdf")},
    )
    expect(bool(uploaded.get("file_key")), "换文件的上传没拿到 file_key")
    demo.replacement = {
        "file_key": uploaded["file_key"],
        "file_name": uploaded["file_name"],
        "pages": 4,
    }
    pending = call(
        demo.client, "POST", f"/api/order/{demo.order_id}/replace-file", headers=demo.student,
        json={
            "file_key": demo.replacement["file_key"],
            "file_name": demo.replacement["file_name"],
            "page_count": 4,
            "file_index": 2,
            "reason": "附录传错版本",
        },
    )
    expect(pending["applied"] is False, "已付款订单换文件不应立即生效")
    change = pending.get("file_change") or {}
    expect(int(change.get("file_index", 0)) == 2, "待确认请求没有记下是第 2 份文件")
    expect(int(change.get("new_file_page_count", 0)) == 4, "待确认请求里的新文件页数不对")
    expect(int(change.get("new_amount_cents", 0)) > 0, "系统没有给出换文件后的重算价")
    print(f"  已提交换稿，订单仍在排队中，等待老师确认；"
          f"换后系统重算 {change['new_amount_cents']} 分")


def step_08_admin_confirm_change(demo: Demo) -> None:
    row = find_in_queue(demo.client, demo.admin, demo.order_id)
    change = row.get("file_change") or {}
    expect(change, "队列里没有这一单的换文件待确认请求")
    expect(int(change.get("file_index", 0)) == 2, "队列里的待确认请求不是第 2 份文件")
    print(f"  老师端看到第 2 份待确认：{change['old_file_name']} → {change['new_file_name']}")

    confirmed = call(
        demo.client, "POST", f"/api/admin/order/{demo.order_id}/confirm-file-change",
        headers=demo.admin, json={"approve": True},
    )
    expect(confirmed["approved"] is True, "老师确认换文件没有通过")
    order = confirmed["order"]
    expect(len(order["files"]) == 2, "确认后文件数变了")
    expect(order["files"][1]["file_name"] == demo.replacement["file_name"],
           "确认后第 2 份文件没有换成本次上传的新文件")
    expect(order["files"][0]["file_name"] == demo.first["file_name"], "第 1 份文件被误改")
    expect(int(order["amount_cents"]) == int(change["new_amount_cents"]),
           "确认后的整单金额与系统重算价不一致")
    expect(order.get("files_unconfirmed") is True, "确认换文件后没有标出「已替换未确认」")
    demo.order = order
    print(f"  确认换文件：第 2 份换成 {order['files'][1]['file_name']}，"
          f"整单 {order['amount_cents']} 分；队列标出「已替换未确认」")


def step_09_admin_download_second(demo: Demo) -> None:
    link = call(
        demo.client, "GET", f"/api/admin/order/{demo.order_id}/download",
        headers=demo.admin, params={"file_index": 2},
    )
    expect(link["file_key"] == demo.replacement["file_key"],
           "逐份下载第 2 份返回的不是本次换上的文件")
    expect(link["file_name"] == demo.replacement["file_name"], "逐份下载第 2 份的文件名不对")
    signed = link.get("signed_url") or ""
    expect(signed.startswith("http"), "逐份下载没有返回限时签名链接")
    fetched = demo.client.get(signed)
    expect(fetched.status_code == 200, f"签名链接下载失败（HTTP {fetched.status_code}）")
    expect(fetched.content == demo.replacement_bytes,
           f"下载到的内容不是第 2 份（{len(fetched.content)} 字节 vs 应为 "
           f"{len(demo.replacement_bytes)} 字节）")
    print(f"  第 2 份 {link['file_name']}：{len(fetched.content)} 字节，内容与本地上传逐字节一致")


def step_10_admin_print(demo: Demo) -> None:
    started = call(
        demo.client, "POST", f"/api/admin/order/{demo.order_id}/start", headers=demo.admin
    )
    expect(started["order"]["status"] == "printing",
           f"开始打印后状态应为打印中，实际 {started['order']['status']}")
    job_files = started["print_job"].get("files") or []
    expect(len(job_files) == 2, f"打印任务应带 2 份文件，实际 {len(job_files)} 份")
    print(f"  开始打印，打印任务带 {len(job_files)} 份文件，"
          f"打印页数 {[item['print_pages'] for item in job_files]}")

    completed = call(
        demo.client, "POST", f"/api/admin/order/{demo.order_id}/complete", headers=demo.admin
    )
    expect(completed["order"]["status"] == "ready",
           f"打印完成后状态应为待取件，实际 {completed['order']['status']}")
    print(f"  打印完成，订单转为待取件，实收 {completed['amount_cents']} 分")


def step_11_admin_verify_code(demo: Demo) -> None:
    pickup_code = demo.order["pickup_code"]
    verified = call(
        demo.client, "POST", "/api/admin/order/verify-code", headers=demo.admin,
        json={"pickup_code": pickup_code},
    )
    expect(verified["order"]["status"] == "completed",
           f"核销后状态应为已完成，实际 {verified['order']['status']}")
    expect(verified["order"]["pickup_code"] == pickup_code, "核销的取件码对不上本单")
    print(f"  按取件码 {pickup_code} 核销成功，订单号 {verified['order']['order_no']}")


def step_12_student_sees_completed(demo: Demo) -> None:
    detail = call(
        demo.client, "GET", f"/api/order/{demo.order_id}", headers=demo.student
    )
    expect(detail["status"] == "completed", f"学生侧状态应为已完成，实际 {detail['status']}")
    expect(detail["status_label"] == "已完成", "学生侧状态文案不是「已完成」")
    expect(bool(detail.get("picked_at")), "已完成订单没有取件时间")
    print(f"  学生端刷新：{detail['status_label']}，取件时间 {detail['picked_at']}")


STEPS: list[tuple[str, Callable[[Demo], None]]] = [
    ("学生打开小程序，用自己的设备登录", step_01_student_login),
    ("选两份 PDF 上传，等页数算出来", step_02_upload_two_pdfs),
    ("第二份改成双面彩色两份，看逐份报价", step_03_quote_two_files),
    ("下单并支付，记住订单号和取件码", step_04_create_and_pay),
    ("学生看我的订单，能看到排在第几单", step_05_student_order_list),
    ("老师登录老师端，队列里看到这一单两份", step_06_admin_queue),
    ("学生换掉第二份，订单进入待确认", step_07_student_replace_second),
    ("老师看到第二份待确认，点确认换文件", step_08_admin_confirm_change),
    ("老师逐份下载第二份，核对拿到的是新文件", step_09_admin_download_second),
    ("老师开始打印，再点打印完成", step_10_admin_print),
    ("学生来取件，老师按取件码核销", step_11_admin_verify_code),
    ("学生端刷新，订单显示已完成", step_12_student_sees_completed),
]


def check_narrations() -> None:
    """旁白超过 30 字就念不完一步，宁可启动就报错，也别录到一半卡住。"""
    for index, (narration, _) in enumerate(STEPS, start=1):
        if len(narration) > NARRATION_LIMIT:
            raise SystemExit(
                f"第 {index} 步旁白 {len(narration)} 字，超过 {NARRATION_LIMIT} 字：{narration}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="智印助手演示自检（12 步）")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("ZHIYIN_BASE_URL", DEFAULT_BASE_URL),
        help=f"后端地址，默认 {DEFAULT_BASE_URL}，也可用环境变量 ZHIYIN_BASE_URL",
    )
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    args = parser.parse_args()

    check_narrations()
    host = urlparse(args.base_url).hostname or ""
    env = load_env(Path(args.env_file))
    admin_username = env.get("ADMIN_USERNAME") or os.environ.get("ADMIN_USERNAME", "")
    admin_password = env.get("ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD", "")
    if not admin_username or not admin_password:
        print("没读到老师账号，检查 backend/.env 的 ADMIN_USERNAME 与 ADMIN_PASSWORD")
        return 1
    stamp = int(time.time())
    device_id = f"demo-check-{stamp}"
    total = len(STEPS)
    print(f"演示自检目标 {args.base_url}，学生设备标识 {device_id}，共 {total} 步\n")

    started = time.perf_counter()
    passed = 0
    demo = Demo(
        client=httpx.Client(base_url=args.base_url, timeout=120),
        pricing=load_pricing(ROOT / "config" / "pricing.json"),
        device_id=device_id,
        admin_username=admin_username,
        admin_password=admin_password,
    )
    try:
        try:
            health = call(demo.client, "GET", "/api/health")
        except httpx.HTTPError as error:
            print(f"连不上后端 {args.base_url}：{error}")
            print("先把后端起起来：bash backend/scripts/dev_up.sh（默认 8000）")
            return 1
        if health.get("status") != "up":
            print(f"后端没就绪：{args.base_url}/api/health 返回 {health}")
            return 1
        for index, (narration, action) in enumerate(STEPS, start=1):
            print(f"[{index:02d}/{total}] {narration}")
            try:
                action(demo)
            except StepFailed as error:
                print(f"\n卡在第 {index} 步：{narration}")
                print(f"原因：{error}")
                print(f"已通过 {passed}/{total} 步，耗时 {time.perf_counter() - started:.1f} 秒")
                return 1
            except httpx.HTTPError as error:
                print(f"\n卡在第 {index} 步：{narration}")
                print(f"原因：请求 {args.base_url} 失败（{error}）")
                print(f"已通过 {passed}/{total} 步，耗时 {time.perf_counter() - started:.1f} 秒")
                return 1
            passed += 1
            print()
    finally:
        demo.client.close()

    elapsed = time.perf_counter() - started
    print(f"演示自检通过：{passed} 步全过，总耗时 {elapsed:.1f} 秒")
    print(f"（本次演示订单取件码 {demo.order.get('pickup_code')}，"
          f"订单号 {demo.order.get('order_no')}，host {host}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
