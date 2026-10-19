"""多文件端到端脚本：多文件下单、逐文件换文件、老师确认、撤回与逐文件下载。

与 smoke_e2e.py（13 步）和 smoke_file_change.py（14 步）相互独立，不修改原脚本。
任一步不过退出码 1。

用法（后端已在 127.0.0.1:8000 起好）：

    python scripts/smoke_multi_file.py
    python scripts/smoke_multi_file.py --base-url http://127.0.0.1:8010

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


def build_pdf(pages: int, label: str) -> bytes:
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((72, 72), f"{label} page {index + 1}", fontsize=14)
    data = document.tobytes()
    document.close()
    return data


def upload(client: httpx.Client, headers: dict[str, str], name: str, pages: int) -> dict:
    uploaded = call(
        client,
        "POST",
        "/api/file/upload",
        headers=headers,
        files={"file": (name, build_pdf(pages, name), "application/pdf")},
    )
    expect(bool(uploaded.get("file_key")), f"上传 {name} 后没拿到 file_key")
    return uploaded


def queue_page(
    client: httpx.Client, headers: dict[str, str], status: str
) -> tuple[list[dict], int]:
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
    parser = argparse.ArgumentParser(description="智印助手多文件端到端")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    args = parser.parse_args()

    env = load_env(Path(args.env_file))
    username = env.get("ADMIN_USERNAME") or os.environ.get("ADMIN_USERNAME", "")
    password = env.get("ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD", "")
    if not username or not password:
        print("!! 没读到老师账号，检查 backend/.env 的 ADMIN_USERNAME 与 ADMIN_PASSWORD")
        return 1

    stamp = int(time.time())
    device = f"smoke-multi-file-{stamp}"
    print(f"多文件端到端目标 {args.base_url}，学生设备标识 {device}")

    try:
        with httpx.Client(base_url=args.base_url, timeout=120) as client:
            student = call(client, "POST", "/api/auth/login", json={"device_id": device})["token"]
            sh = {"Authorization": f"Bearer {student}"}
            step("学生登录")

            first = upload(client, sh, "smoke-正文-2p.pdf", 2)
            second = upload(client, sh, "smoke-附录-3p.pdf", 3)
            third = upload(client, sh, "smoke-图册-5p.pdf", 5)
            step("上传三份文件")

            order = call(
                client,
                "POST",
                "/api/order/create",
                headers=sh,
                json={
                    "files": [
                        {"file_key": first["file_key"], "file_name": first["file_name"],
                         "page_count": 2},
                        {"file_key": second["file_key"], "file_name": second["file_name"],
                         "page_count": 3},
                        {"file_key": third["file_key"], "file_name": third["file_name"],
                         "page_count": 5},
                    ],
                    "print_side": 2,
                    "color_mode": 0,
                    "copies": 1,
                    "paper_size": "A4",
                },
            )
            order_id = int(order["id"])
            order_no = order["order_no"]
            pickup_code = order["pickup_code"]
            total_pages = int(order["page_count"])
            total_amount = int(order["amount_cents"])
            unit_amount = total_amount // total_pages
            expect(total_pages == 10, f"三份文件合计应为 10 页，实际 {total_pages}")
            expect(
                total_amount == unit_amount * 10,
                f"金额应按总页数算，实际 {total_amount} 分",
            )
            expect(len(order["files"]) == 3, f"订单应返回 3 份文件，实际 {len(order['files'])}")
            expect(
                [item["file_index"] for item in order["files"]] == [1, 2, 3],
                "文件序号不是 1、2、3",
            )
            step("多文件下单：总页数计费、一单一码")

            paid = call(client, "POST", f"/api/order/{order_id}/pay", headers=sh)
            expect(paid["status"] == "queuing", f"支付后应为排队中，实际 {paid['status']}")
            step("模拟支付")

            replacement = upload(client, sh, "smoke-新附录-4p.pdf", 4)
            pending = call(
                client,
                "POST",
                f"/api/order/{order_id}/replace-file",
                headers=sh,
                json={
                    "file_key": replacement["file_key"],
                    "file_name": replacement["file_name"],
                    "page_count": 4,
                    "file_index": 2,
                    "reason": "附录传错版本",
                },
            )
            expect(pending["applied"] is False, "已付款换文件不应立即生效")
            expect(pending["page_count"] == 10, "待确认期间订单总页数不应变化")
            change = pending.get("file_change") or {}
            expect(int(change.get("file_index", 0)) == 2, "待确认请求没有记录第 2 份文件")
            expect(int(change.get("old_file_page_count", 0)) == 3, "旧文件页数不对")
            expect(int(change.get("new_file_page_count", 0)) == 4, "新文件页数不对")
            expect(int(change.get("new_page_count", 0)) == 11, "换完后的总页数应为 11")
            expect(
                int(change.get("new_amount_cents", 0)) == unit_amount * 11,
                "系统重算价没有按整单 11 页算",
            )
            step("逐文件换文件：待老师确认，整单总页数正确")

            duplicate = client.post(
                f"/api/order/{order_id}/replace-file",
                headers=sh,
                json={
                    "file_key": replacement["file_key"],
                    "file_name": replacement["file_name"],
                    "page_count": 4,
                    "file_index": 2,
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
            expect(len(row.get("files") or []) == 3, "老师端队列没有显示三份文件")
            expect(
                int((row.get("file_change") or {}).get("file_index", 0)) == 2,
                "队列没有显示是哪一份文件被换",
            )
            step("老师端队列显示文件清单与待确认文件")

            confirmed = call(
                client,
                "POST",
                f"/api/admin/order/{order_id}/confirm-file-change",
                headers=ah,
                json={"approve": True},
            )
            confirmed_order = confirmed["order"]
            expect(confirmed["approved"] is True, "老师确认没有通过")
            expect(confirmed_order["page_count"] == 11, "确认后整单页数应为 11")
            expect(
                int(confirmed_order["amount_cents"]) == unit_amount * 11,
                "确认后整单金额没有按 11 页重算",
            )
            expect(
                confirmed_order["files"][1]["file_name"] == replacement["file_name"],
                "确认后第 2 份文件没有替换",
            )
            expect(confirmed_order["files"][0]["file_name"] == first["file_name"], "第 1 份文件被误改")
            expect(confirmed_order["files"][2]["file_name"] == third["file_name"], "第 3 份文件被误改")
            expect(bool(confirmed_order.get("requeue_at")), "确认后没有写重新排队时间")
            expect(confirmed_order["files_unconfirmed"] is True, "替换后应显示文件待确认")
            expect(confirmed_order["pickup_code"] == pickup_code, "换文件改了取件码")
            expect(confirmed_order["order_no"] == order_no, "换文件改了订单号")
            step("老师确认：只替换第 2 份、整单重算、重排队、标记待确认")

            items, total = queue_page(client, ah, "queuing")
            expect(total >= 1, "队列为空")
            expect(
                int(items[-1]["id"]) == order_id,
                f"换完文件的单没有排到队尾（队尾是 {items[-1]['id']}）",
            )
            last_row = items[-1]
            expect(last_row["files_unconfirmed"] is True, "队列没有显示文件已替换待确认")
            step("队列显示文件已替换、单排到队尾")

            links = call(
                client, "GET", f"/api/admin/order/{order_id}/download", headers=ah
            )
            expect(len(links.get("files") or []) == 3, "下载接口没有返回三份文件链接")
            expect(
                all(item["signed_url"].startswith("http") for item in links["files"]),
                "逐文件链接不是签名 URL",
            )
            second_link = call(
                client,
                "GET",
                f"/api/admin/order/{order_id}/download",
                headers=ah,
                params={"file_index": 2},
            )
            expect(
                second_link["file_key"] == replacement["file_key"],
                "指定第 2 份下载时返回的不是新附录",
            )
            step("老师逐文件下载：一份一个限时链接")

            seen = call(
                client, "POST", f"/api/admin/order/{order_id}/confirm-files-seen", headers=ah
            )
            expect(seen["files_unconfirmed"] is False, "确认看到后标记没有清除")
            expect(bool(seen.get("files_seen_at")), "确认看到后没有记录时间")
            step("老师确认看到后清除待确认标记")

            # 撤回：自己的临传文件能删，别人的不能，已被订单引用的不能
            temp = upload(client, sh, "smoke-待撤回.pdf", 1)
            deleted = call(
                client, "POST", "/api/file/delete",
                headers=sh, json={"file_key": temp["file_key"]},
            )
            expect(deleted["deleted"] is True, "撤回自己的临传文件失败")
            again = client.post(
                "/api/file/delete", headers=sh, json={"file_key": temp["file_key"]}
            ).json()
            expect(again.get("code") != 0, "重复撤回没有返回失败")

            other = call(
                client, "POST", "/api/auth/login",
                json={"device_id": f"smoke-multi-other-{stamp}"},
            )["token"]
            other_headers = {"Authorization": f"Bearer {other}"}
            foreign = client.post(
                "/api/file/delete",
                headers=other_headers,
                json={"file_key": first["file_key"]},
            ).json()
            expect(foreign.get("code") == 40404, "别人能撤回我的文件")

            referenced = client.post(
                "/api/file/delete", headers=sh, json={"file_key": first["file_key"]}
            ).json()
            expect(referenced.get("code") == 40920, "已被订单引用的文件被删掉了")
            step("撤回：本人可删、他人不可删、已引用拒绝")

            started = call(client, "POST", f"/api/admin/order/{order_id}/start", headers=ah)
            expect(started["order"]["status"] == "printing", "开始打印失败")
            expect(len(started["print_job"].get("files") or []) == 3, "打印任务没有带三份文件")
            completed = call(client, "POST", f"/api/admin/order/{order_id}/complete", headers=ah)
            expect(completed["order"]["status"] == "ready", "完成打印失败")
            step("开始打印并完成打印（打印任务带三份文件链接）")

            verified = call(
                client,
                "POST",
                "/api/admin/order/verify-code",
                headers=ah,
                json={"pickup_code": pickup_code},
            )
            expect(verified["order"]["status"] == "completed", "核销后应为已完成")
            expect(verified["collected_onsite"] is True, "换文件差额没有现场补收")
            expect(
                int(verified["collected_cents"]) == unit_amount * 1,
                f"应补收 {unit_amount} 分，实际 {verified['collected_cents']}",
            )
            step("核销取件并现场补收差额")

            mine = call(client, "GET", f"/api/order/{order_id}", headers=sh)
            expect(mine["status"] == "completed", f"学生侧应为已完成，实际 {mine['status']}")
            expect(mine["page_count"] == 11, "学生侧总页数不是 11")
            expect(
                mine["files"][1]["file_name"] == replacement["file_name"],
                "学生侧第 2 份文件没有更新",
            )
            expect(mine["files"][1]["page_count"] == 4, "学生侧第 2 份文件页数不对")
            expect(
                int(mine["amount_cents"]) == unit_amount * 11,
                "学生侧金额不是确认后的价格",
            )
            expect(mine["pickup_code"] == pickup_code, "学生侧取件码变了")
            step("学生端看到三份文件、新文件与不变取件码")
    except StepFailed as error:
        print(f"\n多文件端到端失败：{error}")
        print(f"已通过 {len(PASSED)} 步，卡在下一步")
        return 1
    except httpx.HTTPError as error:
        print(f"\n多文件端到端失败：连不上后端（{error}）")
        return 1

    print(f"\n多文件端到端通过：{len(PASSED)} 步全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
