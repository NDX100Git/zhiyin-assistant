# 先看这里

1. 先读 [HANDOFF-2026-10-25.md](HANDOFF-2026-10-25.md)，这是最近一轮（22 项缺陷修复 + 完整实测）
   的交接报告，含改动清单、行为变化、还没做的事。
2. 再读 [PREVIOUS_AI_HANDOFF.md](PREVIOUS_AI_HANDOFF.md)，了解源码交付范围和上一轮交接结论。
3. 再读 [HANDOFF.md](HANDOFF.md) 的第 4、5 节，安装环境并启动。
4. 环境装好后，双击项目根目录的 `start_all.cmd` 即可启动全部服务。
5. 遇到问题先查 `HANDOFF.md` 第 12 节「故障排查」。

注意：本仓库是源码项目，不包含 MySQL、MinIO、LibreOffice、Python/Node 依赖、
真实 `.env` 或 API Key。只有安装好这些运行环境后，登录、上传和 AI 功能才能工作。
