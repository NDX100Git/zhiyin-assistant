"""端到端冒烟：一条完整业务闭环，逐步断言，任一步不过就退出码 1。

覆盖顺序就是学生和老师的真实操作顺序：
学生登录、上传、取页数、跳过 AI 出方案、下单、支付；
老师登录、看队列、开始打印、完成打印、用当天短码核销、重复核销被拒；
学生再查订单看到已完成。

用法（后端已在 127.0.0.1:8000 起好）：

    python scripts/smoke_e2e.py
    python scripts/smoke_e2e.py --base-url http://127.0.0.1:8001

老师账号从 backend/.env 读 ADMIN_USERNAME 与 ADMIN_PASSWORD。
"""

from __future__ import annotations

import argparse
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


def build_pdf() -> bytes:
    document = pymupdf.open()
    for index in range(2):
        page = document.new_page()
        page.insert_text((72, 72), f"ZhiYin smoke page {index + 1}", fontsize=14)
    data = document.tobytes()
    document.close()
    return data


def short_code(pickup_code: str) -> str:
    """完整取件码是日期前缀加三位短码，老师手输的是后三位。"""
    return pickup_code.split("-", 1)[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="智印助手端到端冒烟")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    args = parser.parse_args()

    env = load_env(Path(args.env_file))
    username = env.get("ADMIN_USERNAME") or os.environ.get("ADMIN_USERNAME", "")
    password = env.get("ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD", "")
    if not username or not password:
        print("!! 没读到老师账号，检查 backend/.env 的 ADMIN_USERNAME 与 ADMIN_PASSWORD")
        return 1

    device = f"smoke-{int(time.time())}"
    print(f"冒烟目标 {args.base_url}，学生设备标识 {device}")

    try:
        with httpx.Client(base_url=args.base_url, timeout=120) as client:
            student = call(client, "POST", "/api/auth/login", json={"device_id": device})["token"]
            sh = {"Authorization": f"Bearer {student}"}
            step("学生登录")

            upload = call(
                client,
                "POST",
                "/api/file/upload",
                headers=sh,
                files={"file": ("smoke.pdf", build_pdf(), "application/pdf")},
            )
            file_key = upload["file_key"]
            expect(bool(file_key), "上传后没拿到 file_key")
            step("上传 PDF")

            counted = call(
                client, "POST", "/api/file/page-count", headers=sh, json={"file_key": file_key}
            )
            page_count = int(counted.get("page_count") or 0)
            expect(page_count == 2, f"页数应为 2，实际 {page_count}")
            step("取页数")

            call(
                client,
                "POST",
                "/api/ai/print-plan",
                headers=sh,
                json={"file_key": file_key, "page_count": page_count, "options": {"use_ai": False}},
            )
            step("跳过 AI 出基础方案")

            order = call(
                client,
                "POST",
                "/api/order/create",
                headers=sh,
                json={
                    "file_key": file_key,
                    "page_count": page_count,
                    "print_side": 2,
                    "color_mode": 0,
                    "copies": 1,
                    "paper_size": "A4",
                },
            )
            order_id = order["id"]
            pickup_code = order["pickup_code"]
            expect(order["status"] == "pending_pay", f"下单后状态应为待支付，实际 {order['status']}")
            expect(len(short_code(pickup_code)) >= 3, f"取件码形状不对：{pickup_code}")
            step("下单")

            paid = call(
                client, "POST", f"/api/order/{order_id}/pay", headers=sh, json={"provider": "mock"}
            )
            expect(paid["status"] == "queuing", f"支付后应为排队中，实际 {paid['status']}")
            step("模拟支付")

            admin = call(
                client, "POST", "/api/admin/login", json={"username": username, "password": password}
            )["token"]
            ah = {"Authorization": f"Bearer {admin}"}
            step("老师登录")

            queue = call(client, "GET", "/api/admin/queue", headers=ah)
            expect(int(queue.get("total") or 0) >= 1, "队列为空，刚支付的单不在队列里")
            step("老师端队列可见")

            started = call(client, "POST", f"/api/admin/order/{order_id}/start", headers=ah)
            status = started["order"]["status"]
            expect(status == "printing", f"开始打印后应为打印中，实际 {status}")
            step("开始打印")

            completed = call(client, "POST", f"/api/admin/order/{order_id}/complete", headers=ah)
            status = completed["order"]["status"]
            expect(status == "ready", f"完成打印后应为待取件，实际 {status}")
            step("完成打印")

            verified = call(
                client,
                "POST",
                "/api/admin/order/verify-code",
                headers=ah,
                json={"pickup_code": short_code(pickup_code)},
            )
            expect(
                verified["order"]["status"] == "completed",
                f"核销后应为已完成，实际 {verified['order']['status']}",
            )
            step("用当天短码核销取件")

            again = client.post(
                "/api/admin/order/verify-code", headers=ah, json={"pickup_code": pickup_code}
            ).json()
            expect(again.get("code") != 0, "同一张单被核销了两次，重复核销没有被拒")
            step("重复核销被拒")

            mine = call(client, "GET", f"/api/order/{order_id}", headers=sh)
            expect(mine["status"] == "completed", f"学生侧状态应为已完成，实际 {mine['status']}")
            step("学生查订单")
    except StepFailed as error:
        print(f"\n冒烟失败：{error}")
        print(f"已通过 {len(PASSED)} 步，卡在下一步")
        return 1
    except httpx.HTTPError as error:
        print(f"\n冒烟失败：连不上后端（{error}）")
        return 1

    print(f"\n冒烟通过：{len(PASSED)} 步全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
