"""造演示数据：走真实接口下单并推进状态，库里立刻有待支付到已完成的全谱。

用途是给开发看队列、给老师端练手、给演示铺底，不动数据库结构也不直接写表。
所有单共用一份上传好的示例 PDF，只占一个对象存储文件。

前置：后端已起好，且老师账号写在 backend/.env。

    python scripts/seed_demo.py                 # 造 12 单 + 四类演示场景单
    python scripts/seed_demo.py --count 30      # 造 30 单 + 四类演示场景单
    python scripts/seed_demo.py --no-scenarios  # 只造 --count 那批，不看场景单

四类演示场景各造一单，录屏时用来讲不同情况：

- 单文件、已完成：讲取件码是「一单一码」、核销即完成；
- 多文件（两份）逐份参数不同、排队中：讲一单多文件与逐份计价；
- 已替换未确认：讲老师端看到「已替换未确认」要先重新下载；
- 异常单：讲卡纸缺墨这类情况老师标记异常、学生看不到已完成。

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


def build_pdf(pages: int = 1, label: str = "ZhiYin demo paper") -> bytes:
    document = pymupdf.open()
    for index in range(max(1, pages)):
        page = document.new_page()
        page.insert_text((72, 72), f"{label} page {index + 1}", fontsize=16)
    data = document.tobytes()
    document.close()
    return data


def data_of(client: httpx.Client, method: str, path: str, **kwargs) -> dict:
    body = client.request(method, path, **kwargs).json()
    if body.get("code") != 0:
        raise RuntimeError(f"{method} {path} 失败：{body.get('code')} {body.get('message')}")
    return body.get("data") or {}


# ===== 四类演示场景 =====
# 每类各造一单，录屏时按场景讲；每类用一个独立学生设备标识，老师端搜文件名词就能找到。
SCENE_LABELS = {
    "completed_single": "单文件已完成（讲取件码）",
    "queuing_multi": "多文件排队中（逐份参数不同）",
    "replaced_unconfirmed": "已替换未确认（讲老师端拦截）",
    "abnormal": "异常单（讲异常处理）",
}


def student_login(client: httpx.Client, device_id: str) -> dict[str, str]:
    token = data_of(client, "POST", "/api/auth/login", json={"device_id": device_id})["token"]
    return {"Authorization": f"Bearer {token}"}


def upload_pdf(client: httpx.Client, headers: dict[str, str], name: str, pages: int) -> dict:
    uploaded = data_of(
        client,
        "POST",
        "/api/file/upload",
        headers=headers,
        files={"file": (name, build_pdf(pages, name), "application/pdf")},
    )
    return {"file_key": uploaded["file_key"], "file_name": uploaded["file_name"]}


def create_single_order(client: httpx.Client, headers: dict[str, str], upload: dict,
                        pages: int) -> dict:
    return data_of(
        client,
        "POST",
        "/api/order/create",
        headers=headers,
        json={
            "file_key": upload["file_key"],
            "file_name": upload["file_name"],
            "page_count": pages,
            "print_side": 2,
            "color_mode": 0,
            "copies": 1,
            "paper_size": "A4",
        },
    )


def seed_scenarios(client: httpx.Client, admin_headers: dict[str, str]) -> dict[str, int]:
    """造四类演示场景单，返回每类造了几单。"""
    created = dict.fromkeys(SCENE_LABELS, 0)

    # 1）单文件、已完成：讲「一单一码」，取件码核销后学生端就是已完成
    sh = student_login(client, "demo-scene-1")
    body = upload_pdf(client, sh, "场景-单文件-6页.pdf", 6)
    order = create_single_order(client, sh, body, 6)
    data_of(client, "POST", f"/api/order/{order['id']}/pay", headers=sh)
    data_of(client, "POST", f"/api/admin/order/{order['id']}/start", headers=admin_headers)
    data_of(client, "POST", f"/api/admin/order/{order['id']}/complete", headers=admin_headers)
    data_of(
        client,
        "POST",
        "/api/admin/order/verify-code",
        headers=admin_headers,
        json={"pickup_code": order["pickup_code"]},
    )
    created["completed_single"] += 1
    print(f"  {order['order_no']}  {order['pickup_code']}  -> {SCENE_LABELS['completed_single']}")

    # 2）多文件（两份）逐份参数不同、排队中：讲一单多文件与逐份计价
    sh = student_login(client, "demo-scene-2")
    first = upload_pdf(client, sh, "场景-多文件-正文-4页.pdf", 4)
    second = upload_pdf(client, sh, "场景-多文件-附录-3页.pdf", 3)
    order = data_of(
        client,
        "POST",
        "/api/order/create",
        headers=sh,
        json={
            "files": [
                {**first, "page_count": 4, "print_side": 1, "color_mode": 0,
                 "paper_size": "A4", "copies": 1},
                {**second, "page_count": 3, "print_side": 2, "color_mode": 1,
                 "paper_size": "A4", "copies": 2},
            ],
            "print_side": 2,
            "color_mode": 0,
            "copies": 1,
            "paper_size": "A4",
        },
    )
    data_of(client, "POST", f"/api/order/{order['id']}/pay", headers=sh)
    created["queuing_multi"] += 1
    print(
        f"  {order['order_no']}  {order['pickup_code']}  -> {SCENE_LABELS['queuing_multi']}"
        f"（逐份 {[item['amount_cents'] for item in order['files']]} 分，"
        f"整单 {order['amount_cents']} 分）"
    )

    # 3）已替换未确认：学生换第 2 份、老师确认后，队列标出「已替换未确认」
    sh = student_login(client, "demo-scene-3")
    first = upload_pdf(client, sh, "场景-换文件-正文-2页.pdf", 2)
    second = upload_pdf(client, sh, "场景-换文件-附录-2页.pdf", 2)
    order = data_of(
        client,
        "POST",
        "/api/order/create",
        headers=sh,
        json={
            "files": [
                {**first, "page_count": 2, "print_side": 1, "color_mode": 0,
                 "paper_size": "A4", "copies": 1},
                {**second, "page_count": 2, "print_side": 1, "color_mode": 0,
                 "paper_size": "A4", "copies": 1},
            ],
        },
    )
    data_of(client, "POST", f"/api/order/{order['id']}/pay", headers=sh)
    replacement = upload_pdf(client, sh, "场景-换文件-附录-换稿-3页.pdf", 3)
    data_of(
        client,
        "POST",
        f"/api/order/{order['id']}/replace-file",
        headers=sh,
        json={**replacement, "page_count": 3, "file_index": 2, "reason": "附录传错版本"},
    )
    data_of(
        client,
        "POST",
        f"/api/admin/order/{order['id']}/confirm-file-change",
        headers=admin_headers,
        json={"approve": True},
    )
    created["replaced_unconfirmed"] += 1
    print(
        f"  {order['order_no']}  {order['pickup_code']}  -> "
        f"{SCENE_LABELS['replaced_unconfirmed']}（第 2 份换成 {replacement['file_name']}）"
    )

    # 4）异常单：老师标记异常，学生端看到的是异常而不是已完成
    sh = student_login(client, "demo-scene-4")
    body = upload_pdf(client, sh, "场景-异常单-5页.pdf", 5)
    order = create_single_order(client, sh, body, 5)
    data_of(client, "POST", f"/api/order/{order['id']}/pay", headers=sh)
    data_of(
        client,
        "POST",
        f"/api/admin/order/{order['id']}/abnormal",
        headers=admin_headers,
        json={"reason": "打印机卡纸，缺 A4 纸，等处理"},
    )
    created["abnormal"] += 1
    print(f"  {order['order_no']}  {order['pickup_code']}  -> {SCENE_LABELS['abnormal']}")

    return created


def main() -> int:
    parser = argparse.ArgumentParser(description="造智印助手演示数据")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--count", type=int, default=12, help="造多少单")
    parser.add_argument("--students", type=int, default=4, help="分摊到几个学生账号")
    parser.add_argument(
        "--no-scenarios",
        action="store_true",
        help="不造四类演示场景单，只要 --count 那一批（保留旧的纯批量行为）",
    )
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

        scene_counts: dict[str, int] = {}
        if not args.no_scenarios:
            print("\n四类演示场景：")
            scene_counts = seed_scenarios(client, ah)

    total = sum(created.values())
    summary = "、".join(f"{LABELS.get(k, k)} {v} 单" for k, v in created.items())
    print(f"\n共造 {total} 单：{summary}；学生账号 {len(students)} 个（设备标识 demo-1 起）")
    if scene_counts:
        scene_total = sum(scene_counts.values())
        detail = "、".join(
            f"{SCENE_LABELS[key]} {scene_counts[key]} 单" for key in SCENE_LABELS
        )
        print(f"四类演示场景 {scene_total} 单：{detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
