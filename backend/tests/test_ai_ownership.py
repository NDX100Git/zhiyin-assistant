"""AI 与文件接口的归属校验（回归保护）。

这几个接口历史上只要求「已登录」，不校验 file_key 属于谁；而学生登录本身不需要任何
凭据（任意 device_id 即是新用户），所以只要拿到别人的 file_key 就能读论文内容，
`/convert` 与 `/fix` 还会把生成的文件挂到自己名下，等于把别人的论文洗成自己的。

这里只断言「他人被拒」：成功路径由 test_upload_api / test_generated_files 覆盖，
且两个端点都是同步的，不进 AI 线程池，用例不依赖模型与 LibreOffice。
"""

from __future__ import annotations

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< >>\n%%EOF\n"


def _login(client, device_id: str) -> dict[str, str]:
    """登录并返回请求头；不同 device_id 就是不同用户。"""
    body = client.post("/api/auth/login", json={"device_id": device_id}).json()
    assert body["code"] == 0, body
    return {"Authorization": f"Bearer {body['data']['token']}"}


def _upload_pdf(client, headers: dict[str, str]) -> str:
    """上传一份最小 PDF，返回服务端生成的 file_key。"""
    body = client.post(
        "/api/file/upload",
        files={"file": ("论文.pdf", PDF_BYTES, "application/pdf")},
        headers=headers,
    ).json()
    assert body["code"] == 0, body
    return body["data"]["file_key"]


def test_读取页数拒绝他人的文件(client, test_storage):
    owner = _login(client, "ownership-owner-1")
    other = _login(client, "ownership-other-1")
    file_key = _upload_pdf(client, owner)

    body = client.post(
        "/api/file/page-count", json={"file_key": file_key}, headers=other
    ).json()

    assert body["code"] == 40404


def test_AI校对拒绝他人的文件(client, test_storage):
    owner = _login(client, "ownership-owner-2")
    other = _login(client, "ownership-other-2")
    file_key = _upload_pdf(client, owner)

    body = client.post(
        "/api/ai/proofread", json={"file_key": file_key}, headers=other
    ).json()

    assert body["code"] == 40404
