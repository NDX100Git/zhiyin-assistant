"""登录与上传接口测试。"""

from __future__ import annotations

import re

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< >>\n%%EOF\n"
FAKE_PDF_BYTES = b"this is not a real pdf"


def login(client, device_id: str = "test-device-0001") -> str:
    resp = client.post("/api/auth/login", json={"device_id": device_id})
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["token"]


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_登录缺少设备标识返回业务错误(client):
    resp = client.post("/api/auth/login", json={})
    body = resp.json()
    assert resp.status_code == 200
    assert body["code"] == 40001
    assert "设备标识" in body["message"]


def test_首次登录自动建用户并返回token(client):
    resp = client.post("/api/auth/login", json={"device_id": "dev-abc", "nickname": "同学"})
    data = resp.json()["data"]
    assert data["token"]
    assert data["expires_in"] > 0
    assert data["user"]["auth_provider"] == "device"
    assert data["user"]["nickname"] == "同学"

    # 同一设备再次登录复用同一用户
    again = client.post("/api/auth/login", json={"device_id": "dev-abc"}).json()
    assert again["data"]["user"]["id"] == data["user"]["id"]


def test_上传必须携带token(client):
    resp = client.post("/api/file/upload", files={"file": ("a.pdf", PDF_BYTES, "application/pdf")})
    body = resp.json()
    assert resp.status_code == 200
    assert body["code"] == 40100


def test_拒绝白名单外的扩展名(client):
    token = login(client)
    resp = client.post(
        "/api/file/upload",
        files={"file": ("note.txt", b"hello", "text/plain")},
        headers=auth_header(token),
    )
    body = resp.json()
    assert body["code"] == 40004
    assert "不支持的文件类型" in body["message"]


def test_文件头与扩展名不符被拒绝(client):
    token = login(client)
    resp = client.post(
        "/api/file/upload",
        files={"file": ("fake.pdf", FAKE_PDF_BYTES, "application/pdf")},
        headers=auth_header(token),
    )
    body = resp.json()
    assert body["code"] == 40007


def test_超出大小上限被拒绝(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.file_service.get_upload_rules",
        lambda: {"max_size_mb": 0, "allowed_extensions": ["pdf"], "max_filename_length": 120},
    )
    token = login(client)
    resp = client.post(
        "/api/file/upload",
        files={"file": ("big.pdf", PDF_BYTES, "application/pdf")},
        headers=auth_header(token),
    )
    assert resp.json()["code"] == 40005


def test_上传成功返回服务端生成的key与签名url(client, test_storage):
    token = login(client)
    resp = client.post(
        "/api/file/upload",
        files={"file": ("毕业论文.pdf", PDF_BYTES, "application/pdf")},
        headers=auth_header(token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert re.fullmatch(r"files/\d{4}/\d{2}/[0-9a-f]{32}\.pdf", data["file_key"])
    assert "毕业论文" not in data["file_key"]
    assert data["file_name"] == "毕业论文.pdf"
    assert data["size"] == len(PDF_BYTES)
    assert data["content_type"] == "application/pdf"
    assert data["signed_url"].startswith("http")
    assert data["expires_minutes"] == 30

    # 文件确实进了桶
    assert test_storage.read_bytes(data["file_key"]) == PDF_BYTES


def test_载入示例论文兜底接口(client, test_storage):
    token = login(client)
    body = client.post("/api/file/demo", headers=auth_header(token)).json()

    assert body["code"] == 0, body
    data = body["data"]
    assert data["is_demo"] is True
    assert data["file_key"].endswith(".docx")
    assert data["size"] > 1000
    assert data["signed_url"].startswith("http")
    assert test_storage.read_bytes(data["file_key"])[:2] == b"PK"


def test_未登录不能载入示例论文(client):
    assert client.post("/api/file/demo").json()["code"] == 40100


def test_读取页数不调用AI(client, monkeypatch):
    token = login(client)
    monkeypatch.setattr(
        "app.routers.file.parse_document", lambda db, key: {"page_count": 12}
    )
    body = client.post(
        "/api/file/page-count",
        json={"file_key": "files/2026/10/demo.pdf"},
        headers=auth_header(token),
    ).json()

    assert body["code"] == 0
    assert body["data"]["page_count"] == 12
