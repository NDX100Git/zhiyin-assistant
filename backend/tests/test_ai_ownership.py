"""AI 与文件接口的归属校验（回归保护）。

这几个接口历史上只要求「已登录」，不校验 file_key 属于谁；而学生登录本身不需要任何
凭据（任意 device_id 即是新用户），所以只要拿到别人的 file_key 就能读论文内容，
`/convert` 与 `/fix` 还会把生成的文件挂到自己名下，等于把别人的论文洗成自己的。

这里只断言「他人被拒」：成功路径由 test_upload_api / test_generated_files 覆盖。
归属校验发生在任何 AI 线程、模型调用与 LibreOffice 转换之前，所以这些用例既不用
替身模型，也不依赖本机装了 LibreOffice。

覆盖面：六个 AI 入口（体检、校对、打印方案、看图识字、PDF 转 Word、文档修复）
加页数接口，逐个都要有「拿别人的文件被拒」的用例 —— 漏掉一个，就等于那道门没锁。
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


def test_AI体检拒绝他人的文件(client, test_storage):
    owner = _login(client, "ownership-owner-3")
    other = _login(client, "ownership-other-3")
    file_key = _upload_pdf(client, owner)

    body = client.post(
        "/api/ai/check", json={"file_key": file_key}, headers=other
    ).json()

    assert body["code"] == 40404


def test_AI打印方案拒绝他人的文件(client, test_storage):
    owner = _login(client, "ownership-owner-4")
    other = _login(client, "ownership-other-4")
    file_key = _upload_pdf(client, owner)

    body = client.post(
        "/api/ai/print-plan", json={"file_key": file_key}, headers=other
    ).json()

    assert body["code"] == 40404


def test_AI看图识字拒绝他人的文件(client, test_storage):
    owner = _login(client, "ownership-owner-5")
    other = _login(client, "ownership-other-5")
    file_key = _upload_pdf(client, owner)

    body = client.post(
        "/api/ai/ocr", json={"file_key": file_key}, headers=other
    ).json()

    assert body["code"] == 40404


def test_PDF转Word拒绝他人的文件(client, test_storage):
    owner = _login(client, "ownership-owner-6")
    other = _login(client, "ownership-other-6")
    file_key = _upload_pdf(client, owner)

    body = client.post(
        "/api/ai/convert",
        json={"file_key": file_key, "target": "docx", "file_name": "转换结果.docx"},
        headers=other,
    ).json()

    assert body["code"] == 40404


def test_文档修复拒绝他人的文件(client, test_storage):
    """修复会把产物挂到调用者名下，等于把别人的论文洗成自己的，必须先拦。"""
    owner = _login(client, "ownership-owner-7")
    other = _login(client, "ownership-other-7")
    file_key = _upload_pdf(client, owner)

    body = client.post(
        "/api/ai/fix",
        json={"file_key": file_key, "issue_ids": ["i1"], "file_name": "修复结果.docx"},
        headers=other,
    ).json()

    assert body["code"] == 40404
