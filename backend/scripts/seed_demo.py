"""造演示数据：走真实接口下单并推进状态，库里立刻有待支付到已完成的全谱。

用途是给开发看队列、给老师端练手、给演示铺底，不动数据库结构也不直接写表。
所有单共用一份上传好的示例 PDF，只占一个对象存储文件。

前置：后端已起好，且老师账号写在 backend/.env。

    python scripts/seed_demo.py                 # 造 12 单
    python scripts/seed_demo.py --count 30      # 造 30 单

安全阀：只允许对本机后端执行，避免把演示数据灌到别人在用的环境。
真要对外做，加 --force。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pymupdf

ROOT = Path(__file__).resolve().parents[1]

# 状态推进顺序，脚本按这个顺序把每张单推到目标状态
FLOW = ["pending_pay", "queuing", "printing", "ready", "completed"]
LABELS = {
    "pending_pay": "待支付",
    "queuing": "排队中",
    "printing": "打印中",
    "ready": "待取件",
    "completed": "已完成",
    "canceled": "已取消",
}


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


def build_pdf() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "ZhiYin demo paper", fontsize=16)
    data = document.tobytes()
    document.close()
    return data


def data_of(client: httpx.Client, method: str, path: str, **kwargs) -> dict:
    body = client.request(method, path, **kwargs).json()
    if body.get("code") != 0:
        raise RuntimeError(f"{method} {path} 失败：{body.get('code')} {body.get('message')}")
    return body.get("data") or {}


def main() -> int:
    parser = argparse.ArgumentParser(description="造智印助手演示数据")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--count", type=int, default=12, help="造多少单")
    parser.add_argument("--students", type=int, default=4, help="分摊到几个学生账号")
    parser.add_argument("--force", action="store_true", help="允许对非本机后端执行")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    args = parser.parse_args()

    host = urlparse(args.base_url).hostname or ""
    if not args.force and host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        print(f"拒绝对 {args.base_url} 造数据：只允许本机，确需如此请加 --force")
        return 1

    env = load_env(Path(args.env_file))
    username = env.get("ADMIN_USERNAME", "")
    password = env.get("ADMIN_PASSWORD", "")
    if not username or not password:
        print("没读到老师账号，检查 backend/.env 的 ADMIN_USERNAME 与 ADMIN_PASSWORD")
        return 1

    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        admin = data_of(
            client, "POST", "/api/admin/login", json={"username": username, "password": password}
        )["token"]
        ah = {"Authorization": f"Bearer {admin}"}

        pdf = build_pdf()
        students: list[dict[str, str]] = []
        for index in range(max(1, args.students)):
            token = data_of(
                client, "POST", "/api/auth/login", json={"device_id": f"demo-{index + 1}"}
            )["token"]
            sh = {"Authorization": f"Bearer {token}"}
            upload = data_of(
                client,
                "POST",
                "/api/file/upload",
                headers=sh,
                files={"file": (f"demo-{index + 1}.pdf", pdf, "application/pdf")},
            )
            students.append({"token": token, "file_key": upload["file_key"]})

        created: dict[str, int] = {}
        for index in range(args.count):
            student = students[index % len(students)]
            # 前几张按流程铺满，后面循环
            target = FLOW[index % len(FLOW)]
            if index >= len(FLOW) and index % 7 == 6:
                target = "canceled"

            order = data_of(
                client,
                "POST",
                "/api/order/create",
                headers={"Authorization": f"Bearer {student['token']}"},
                json={
                    "file_key": student["file_key"],
                    "page_count": 8 + (index % 5) * 4,
                    "print_side": 2 if index % 3 else 1,
                    "color_mode": 1 if index % 4 == 0 else 0,
                    "copies": 1,
                    "paper_size": "A4",
                },
            )
            order_id = order["id"]
            sh = {"Authorization": f"Bearer {student['token']}"}

            if target == "canceled":
                data_of(client, "POST", f"/api/order/{order_id}/cancel", headers=sh)
            elif target != "pending_pay":
                data_of(client, "POST", f"/api/order/{order_id}/pay", headers=sh, json={"provider": "mock"})
                reached = "queuing"
                if target in {"printing", "ready", "completed"}:
                    data_of(client, "POST", f"/api/admin/order/{order_id}/start", headers=ah)
                    reached = "printing"
                if target in {"ready", "completed"}:
                    data_of(client, "POST", f"/api/admin/order/{order_id}/complete", headers=ah)
                    reached = "ready"
                if target == "completed":
                    data_of(
                        client,
                        "POST",
                        "/api/admin/order/verify-code",
                        headers=ah,
                        json={"pickup_code": order["pickup_code"]},
                    )
                    reached = "completed"
                target = reached

            created[target] = created.get(target, 0) + 1
            print(f"  {order['order_no']}  {order['pickup_code']}  -> {LABELS.get(target, target)}")

    total = sum(created.values())
    summary = "、".join(f"{LABELS.get(k, k)} {v} 单" for k, v in created.items())
    print(f"\n共造 {total} 单：{summary}；学生账号 {len(students)} 个（设备标识 demo-1 起）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
