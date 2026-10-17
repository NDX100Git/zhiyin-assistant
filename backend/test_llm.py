"""DeepSeek 连通性测试脚本（独立运行，不依赖业务数据）。

用法（先在 backend/.env 里填好 LLM_API_KEY）：

    cd backend
    .venv\\Scripts\\python.exe test_llm.py

只验证三件事：普通对话、JSON 模式、思考模式能否正常返回。
"""

from __future__ import annotations

import json
import sys
import time

from app.config import settings
from app.services import llm


def _step(index: int, total: int, title: str) -> float:
    print(f"\n[{index}/{total}] {title}")
    return time.time()


def main() -> int:
    print("智印助手 · DeepSeek 连通性测试")
    print(f"  base_url : {settings.llm_base_url}")
    print(f"  model    : {settings.llm_model}")
    key_state = f"已配置（长度 {len(settings.llm_api_key)}）" if llm.is_configured() else "未配置"
    print(f"  api_key  : {key_state}")

    if not llm.is_configured():
        print("\n未配置 LLM_API_KEY：请在 backend/.env 中填写后重新运行。")
        return 1

    try:
        started = _step(1, 3, "普通对话")
        text = llm.chat([{"role": "user", "content": "只回复两个字：连通"}], temperature=0)
        print(f"  OK（{time.time() - started:.1f}s）：{text.strip()[:60]}")

        started = _step(2, 3, "JSON 模式 + 解析容错")
        data = llm.chat_json(
            [
                {"role": "system", "content": "你只输出 JSON，不要任何解释。"},
                {"role": "user", "content": '返回 {"ok": true, "msg": "json 连通"}'},
            ],
            temperature=0,
        )
        print(f"  OK（{time.time() - started:.1f}s）：{json.dumps(data, ensure_ascii=False)[:80]}")

        started = _step(3, 3, "思考模式（复杂任务用）")
        text = llm.chat(
            [
                {"role": "system", "content": "你是论文格式校验助手。"},
                {"role": "user", "content": "一句话说明学位论文参考文献应当遵循哪个国家标准。"},
            ],
            thinking=True,
        )
        print(f"  OK（{time.time() - started:.1f}s）：{text.strip()[:80]}")

        print("\n全部通过：模型连通、JSON 模式可用、思考模式可用。")
        return 0
    except Exception as exc:  # 诊断脚本需要打印原始错误
        print(f"\n失败：{type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
