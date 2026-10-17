"""存储工厂：业务代码只调用 get_storage()，不 import 具体实现类。"""

from __future__ import annotations

from functools import lru_cache

from app.services.storage.base import FileStorage
from app.services.storage.keys import build_object_key


@lru_cache(maxsize=1)
def get_storage() -> FileStorage:
    """返回当前配置的存储实现。V2 接云对象存储时在此加分支即可。"""
    from app.services.storage.minio_store import MinioStorage

    return MinioStorage()


__all__ = ["FileStorage", "build_object_key", "get_storage"]
