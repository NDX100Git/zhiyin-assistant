"""手工执行结构迁移：只补新表、新列与新索引，不删不改，可重复执行。

用法（在 backend/ 下）：

    python scripts/migrate_schema.py

已有开发库补一次即可；新库直接建全。后端启动时也会自动跑同一份迁移。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.models  # noqa: E402,F401  注册全部表
from app.database import engine  # noqa: E402
from app.migrations import run_migrations  # noqa: E402
from app.models.base import Base  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    Base.metadata.create_all(bind=engine)
    actions = run_migrations(engine)
    if actions:
        print("迁移完成：" + "、".join(actions))
    else:
        print("迁移完成：结构已是最新，没有需要补的变更")
    print(f"当前共 {len(Base.metadata.tables)} 张表")
    return 0


if __name__ == "__main__":
    sys.exit(main())
