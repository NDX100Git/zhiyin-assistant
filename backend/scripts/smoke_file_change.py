"""换文件端到端脚本：下单、待支付换文件、支付、申请、老师确认、核销补收。

与 smoke_e2e.py 相互独立，不替代也不修改原来的 13 步冒烟。任一步不过退出码 1。

用法（后端已在 127.0.0.1:8000 起好）：

    python scripts/smoke_file_change.py
    python scripts/smoke_file_change.py --base-url http://127.0.0.1:8001

老师账号从 backend/.env 读 ADMIN_USERNAME 与 ADMIN_PASSWORD。
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path

import httpx
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
PASSED: list[str] = []


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


class StepFailed(Exception):
    pass


def step(name: str) -> None:
    PASSED.append(name)
    print(f"  通过  {name}")


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


def build_pdf(pages: int) -> bytes:
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((72, 72), f"ZhiYin file-change page {index + 1}", fontsize=14)
    data = document.tobytes()
    document.close()
    return data


def upload(client: httpx.Client, headers: dict[str, str], name: str, pages: int) -> dict:
    uploaded = call(
        client,
        "POST",
        "/api/file/upload",
        headers=headers,
        files={"file": (name, build_pdf(pages), "application/pdf")},
    )
    expect(bool(uploaded.get("file_key")), f"上传 {name} 后没拿到 file_key")
    return uploaded


def replace(
    client: httpx.Client, headers: dict[str, str], order_id: int,
    uploaded: dict, page_count: int, reason: str,
) -> dict:
    return call(
        client,
        "POST",
        f"/api/order/{order_id}/replace-file",
        headers=headers,
        json={
            "file_key": uploaded["file_key"],
            "file_name": uploaded["file_name"],
            "page_count": page_count,
            "reason": reason,
        },
    )


def queue_page(
    client: httpx.Client, headers: dict[str, str], status: str
) -> tuple[list[dict], int]:
    """取队列最后一页：换完文件的单排在队尾，最后一页才看得到它。"""
    first = call(
        client,
        "GET",
        "/api/admin/queue",
        headers=headers,
        params={"status": status, "page": 1, "page_size": 200},
    )
    total = int(first.get("total") or 0)
    last_page = max(1, math.ceil(total / 200))
    if last_page == 1:
        return list(first.get("items") or []), total
    last = call(
        client,
        "GET",
        "/api/admin/queue",
        headers=headers,
        params={"status": status, "page": last_page, "page_size": 200},
    )
    return list(last.get("items") or []), total


def main() -> int:
    parser = argparse.ArgumentParser(description="智印助手换文件端到端")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    args = parser.parse_args()

    env = load_env(Path(args.env_file))
    username = env.get("ADMIN_USERNAME") or os.environ.get("ADMIN_USERNAME", "")
    password = env.get("ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD", "")
    if not username or not password:
        print("!! 没读到老师账号，检查 backend/.env 的 ADMIN_USERNAME 与 ADMIN_PASSWORD")
        return 1

    device = f"smoke-file-change-{int(time.time())}"
    print(f"换文件端到端目标 {args.base_url}，学生设备标识 {device}")

    try:
        with httpx.Client(base_url=args.base_url, timeout=120) as client:
            student = call(client, "POST", "/api/auth/login", json={"device_id": device})["token"]
            sh = {"Authorization": f"Bearer {student}"}
            step("学生登录")

            original = upload(client, sh, "smoke-original-2p.pdf", 2)
            counted = call(
                client, "POST", "/api/file/page-count", headers=sh,
                json={"file_key": original["file_key"]},
            )
            expect(int(counted["page_count"]) == 2, f"原文件页数应为 2，实际 {counted['page_count']}")
            step("上传原文件并取页数")

            order = call(
                client,
                "POST",
                "/api/order/create",
                headers=sh,
                json={
                    "file_key": original["file_key"],
                    "file_name": original["file_name"],
                    "page_count": 2,
                    "print_side": 2,
                    "color_mode": 0,
                    "copies": 1,
                    "paper_size": "A4",
                },
            )
            order_id = int(order["id"])
            order_no = order["order_no"]
            pickup_code = order["pickup_code"]
            unit_amount = int(order["amount_cents"]) // 2
            expect(order["status"] == "pending_pay", f"下单后应为待支付，实际 {order['status']}")
            step("下单")

            immediate = upload(client, sh, "smoke-replaced-4p.pdf", 4)
            replaced = replace(client, sh, order_id, immediate, 4, "传错版本，待支付时换")
            expect(replaced["applied"] is True, "待支付换文件没有立即生效")
            expect(replaced["status"] == "pending_pay", "待支付换文件后状态变了")
            expect(replaced["page_count"] == 4, f"换文件后页数应为 4，实际 {replaced['page_count']}")
            expect(
                int(replaced["amount_cents"]) == unit_amount * 4,
                f"换文件后金额应为 {unit_amount * 4} 分，实际 {replaced['amount_cents']}",
            )
            expect(replaced["order_no"] == order_no, "换文件改了订单号")
            expect(replaced["pickup_code"] == pickup_code, "换文件改了取件码")
            expect(replaced["file_change"] is None, "待支付换文件不应出现待确认请求")
            step("待支付换文件立即生效（订单号与取件码不变）")

            paid = call(client, "POST", f"/api/order/{order_id}/pay", headers=sh)
            expect(paid["status"] == "queuing", f"支付后应为排队中，实际 {paid['status']}")
            step("模拟支付")

            pending_file = upload(client, sh, "smoke-pending-6p.pdf", 6)
            pending = replace(client, sh, order_id, pending_file, 6, "付款后发现版本不对")
            expect(pending["applied"] is False, "已付款换文件不应立即生效")
            expect(pending["page_count"] == 4, "待确认期间订单页数不应变化")
            expect(
                int(pending["amount_cents"]) == unit_amount * 4,
                "待确认期间订单金额不应变化",
            )
            change = pending.get("file_change") or {}
            expect(bool(change.get("change_id")), "待确认时没有返回 file_change")
            expect(int(change["new_page_count"]) == 6, "待确认请求的新页数不对")
            expect(
                int(change["new_amount_cents"]) == unit_amount * 6,
                "待确认请求的系统重算价不对",
            )
            step("已付款申请换文件，进入等待老师确认")

            duplicate = client.post(
                f"/api/order/{order_id}/replace-file",
                headers=sh,
                json={
                    "file_key": pending_file["file_key"],
                    "file_name": pending_file["file_name"],
                    "page_count": 6,
                },
            ).json()
            expect(duplicate.get("code") != 0, "重复换文件请求没有被拒绝")
            step("重复申请被拒")

            admin = call(
                client, "POST", "/api/admin/login",
                json={"username": username, "password": password},
            )["token"]
            ah = {"Authorization": f"Bearer {admin}"}
            step("老师登录")

            items, _ = queue_page(client, ah, "queuing")
            row = next((item for item in items if int(item["id"]) == order_id), None)
            expect(row is not None, "老师端队列里找不到这张单")
            expect(
                bool((row.get("file_change") or {}).get("change_id")),
                "老师端队列没有显示文件待确认标记",
            )
            step("老师端队列显示文件待确认")

            confirmed = call(
                client,
                "POST",
                f"/api/admin/order/{order_id}/confirm-file-change",
                headers=ah,
                json={"approve": True},
            )
            confirmed_order = confirmed["order"]
            expect(confirmed["approved"] is True, "老师确认没有通过")
            expect(confirmed_order["status"] == "queuing", "确认后订单应仍在排队")
            expect(confirmed_order["page_count"] == 6, "确认后页数没有换成新文件")
            expect(
                int(confirmed_order["amount_cents"]) == unit_amount * 6,
                "确认后金额没有按新页数重算",
            )
            expect(confirmed_order["file_change"] is None, "确认后仍显示待确认标记")
            expect(bool(confirmed_order.get("requeue_at")), "确认后没有写重新排队时间")
            expect(confirmed_order["pickup_code"] == pickup_code, "换文件改了取件码")
            expect(confirmed_order["order_no"] == order_no, "换文件改了订单号")
            step("老师确认：替换文件、按系统重算价落库、重新排队")

            items, total = queue_page(client, ah, "queuing")
            expect(total >= 1, "队列为空")
            expect(
                int(items[-1]["id"]) == order_id,
                f"换完文件的单没有排到队尾（队尾是 {items[-1]['id']}）",
            )
            step("换完文件的单排在队尾")

            started = call(client, "POST", f"/api/admin/order/{order_id}/start", headers=ah)
            expect(started["order"]["status"] == "printing", "开始打印失败")
            completed = call(client, "POST", f"/api/admin/order/{order_id}/complete", headers=ah)
            expect(completed["order"]["status"] == "ready", "完成打印失败")
            step("开始打印并完成打印")

            verified = call(
                client,
                "POST",
                "/api/admin/order/verify-code",
                headers=ah,
                json={"pickup_code": pickup_code},
            )
            expect(verified["order"]["status"] == "completed", "核销后应为已完成")
            expect(verified["collected_onsite"] is True, "新价更高的差额没有现场补收")
            expect(
                int(verified["collected_cents"]) == unit_amount * 2,
                f"应补收 {unit_amount * 2} 分，实际 {verified['collected_cents']}",
            )
            step("核销取件并现场补收差额")

            mine = call(client, "GET", f"/api/order/{order_id}", headers=sh)
            expect(mine["status"] == "completed", f"学生侧应为已完成，实际 {mine['status']}")
            expect(mine["page_count"] == 6, "学生侧页数不是新文件")
            expect(mine["file_name"] == pending_file["file_name"], "学生侧文件名不是新文件")
            expect(
                int(mine["amount_cents"]) == unit_amount * 6,
                "学生侧金额不是确认后的价格",
            )
            expect(mine["pickup_code"] == pickup_code, "学生侧取件码变了")
            step("学生端看到新文件、新金额与不变取件码")
    except StepFailed as error:
        print(f"\n换文件端到端失败：{error}")
        print(f"已通过 {len(PASSED)} 步，卡在下一步")
        return 1
    except httpx.HTTPError as error:
        print(f"\n换文件端到端失败：连不上后端（{error}）")
        return 1

    print(f"\n换文件端到端通过：{len(PASSED)} 步全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
