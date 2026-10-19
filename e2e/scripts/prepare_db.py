"""重置浏览器回归专用数据库。

只清理名字以 `_e2e` 结尾的库，避免误伤开发库与测试库。
由 run.mjs --start 在启动后端前调用。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", type=Path, required=True)
    args = parser.parse_args()

    sys.path.insert(0, str(args.backend.resolve()))
    from app.config import settings

    name = settings.db_name
    if not name.endswith("_e2e"):
        print(f"库名 {name} 不以 _e2e 结尾，拒绝重建", file=sys.stderr)
        return 2

    root = create_engine(make_url(settings.database_url).set(database=""), pool_pre_ping=True)
    with root.begin() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS `{name}`"))
        conn.execute(
            text(
                f"CREATE DATABASE `{name}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
    root.dispose()
    print(f"回归库已重建：{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
