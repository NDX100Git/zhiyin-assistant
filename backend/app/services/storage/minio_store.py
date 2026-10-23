"""MinIO 实现（V1 开发期使用的本地对象存储）。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO

from minio import Minio
from minio.error import S3Error

from app.config import settings
from app.services.storage.base import FileStorage, StoredObject

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
        self._bucket_ready = False

    def ensure_bucket(self) -> None:
        """确保桶存在。首次调用真的去查一次，之后直接用标记，避免每次请求多一次 HEAD。"""
        if self._bucket_ready:
            return
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)
            logger.info("已创建私有桶: %s", self._bucket)
        self._bucket_ready = True

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
        # 桶被重置而库里有历史订单时，签名 URL 也要能自己拿桶回来，否则老师端直接 500
        self.ensure_bucket()
        minutes = expires_minutes or settings.signed_url_expire_minutes
        return self._client.presigned_get_object(
            self._bucket, key, expires=timedelta(minutes=minutes)
        )

    def delete(self, key: str) -> None:
        self.ensure_bucket()
        self._client.remove_object(self._bucket, key)

    def download_to_file(self, key: str, dest: Path) -> Path:
        self.ensure_bucket()
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._client.fget_object(self._bucket, key, str(dest))
        return dest

    def read_bytes(self, key: str) -> bytes:
        self.ensure_bucket()
        response = None
        try:
            response = self._client.get_object(self._bucket, key)
            return response.read()
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def stat_modified(self, key: str) -> datetime | None:
        """查对象修改时间；不存在返回 None，由调用方给出人话提示。"""
        self.ensure_bucket()
        try:
            stat = self._client.stat_object(self._bucket, key)
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                return None
            raise
        return stat.last_modified

    def list_objects(self, prefix: str = "") -> list[StoredObject]:
        """递归列出对象。MinIO 返回的 last_modified 是带时区的 UTC 时间。"""
        self.ensure_bucket()
        return [
            StoredObject(
                key=obj.object_name,
                size=int(obj.size or 0),
                last_modified=obj.last_modified,
            )
            for obj in self._client.list_objects(
                self._bucket, prefix=prefix, recursive=True
            )
        ]
