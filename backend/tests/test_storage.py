"""存储抽象层测试：上传、限时签名 URL、取回、删除。"""

from __future__ import annotations

import io
import urllib.request

from app.services.storage import build_object_key

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< >>\n%%EOF\n"


def test_object_key_不使用原始文件名():
    key = build_object_key("我的 论文 v3.docx")
    assert key.startswith("files/")
    assert key.endswith(".docx")
    assert "论文" not in key


def test_上传后可取回且签名URL可访问(test_storage):
    key = build_object_key("demo.pdf")
    test_storage.upload(key, io.BytesIO(PDF_BYTES), len(PDF_BYTES), "application/pdf")

    assert test_storage.read_bytes(key) == PDF_BYTES

    url = test_storage.signed_url(key)
    assert "X-Amz-Signature" in url or "Signature" in url
    with urllib.request.urlopen(url, timeout=15) as resp:
        assert resp.status == 200
        assert resp.read() == PDF_BYTES

    test_storage.delete(key)
