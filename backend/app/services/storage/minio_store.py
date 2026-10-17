"""MinIO 实现（V1 开发期使用的本地对象存储）。"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO

from minio import Minio

from app.config import settings
from app.services.storage.base import FileStorage

logger = logging.getLogger(__name__)


class MinioStorage(FileStorage):
    """基于 MinIO 私有桶的实现。桶不存在时按需创建。"""

    def __init__(self) -> None:
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self._bucket = settings.minio_bucket

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)
            logger.info("已创建私有桶: %s", self._bucket)

    def upload(self, key: str, data: BinaryIO, size: int,
               content_type: str | None = None) -> str:
        self.ensure_bucket()
        self._client.put_object(
            self._bucket,
            key,
            data,
            length=size,
            content_type=content_type or "application/octet-stream",
        )
        return key

    def signed_url(self, key: str, expires_minutes: int | None = None) -> str:
        minutes = expires_minutes or settings.signed_url_expire_minutes
        return self._client.presigned_get_object(
            self._bucket, key, expires=timedelta(minutes=minutes)
        )

    def delete(self, key: str) -> None:
        self._client.remove_object(self._bucket, key)

    def download_to_file(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._client.fget_object(self._bucket, key, str(dest))
        return dest

    def read_bytes(self, key: str) -> bytes:
        response = None
        try:
            response = self._client.get_object(self._bucket, key)
            return response.read()
        finally:
            if response is not None:
                response.close()
                response.release_conn()
