# 智印助手 · 项目状态（给未来的接手者）

> 最后更新：2026-10-17
> 阅读顺序建议：先看本文件了解"现在到哪了"，再看 [ARCHITECTURE.md](ARCHITECTURE.md) 了解"代码怎么组织"，
> 最后按需查阅 [docs/HANDOFF.md](docs/HANDOFF.md)（原始需求规格，唯一需求来源）。

---

## 1. 一句话与当前状态

智印助手：学生上传论文 → AI 做格式与版式体检 → 给修复建议与打印方案 → 下单排队 →
文印室老师打印 → 学生凭取件码取件并结清。

**当前阶段：V1 核心闭环已跑通（后端全部业务 + 学生端 H5 四个页面），阶段 7 收尾中，阶段 8/9 未开始。**

最近一次验证（2026-10-17）：

- 后端 `pytest -q`：**78+ passed**（阶段 6 结束时 74 条，补充排版属性检查后新增 4 条）
- 后端 `ruff check`：全绿
- 学生端 `npm run build:h5`：构建成功
- 学生端 Edge 真浏览器冒烟：首页 / 下单 / AI 体检报告 / 打印方案 / 我的，**0 控制台错误**
- DeepSeek 连通性（`backend/test_llm.py`）：普通对话 / JSON 模式 / 思考模式全部通过
- 多模态 OCR：真实识别中文图片成功（确认 `deepseek-flash` 支持图片输入）

---

## 2. 本机开发环境（重要：换机时先核对）

| 组件 | 位置 / 版本 | 说明 |
|---|---|---|
| Python | `E:\python\python.exe`（3.14.7） | 用户指定用本机 3.14；需求文档写 3.11+，代码保持 3.11 兼容写法 |
| 后端 venv | `backend/.venv` | 依赖已装齐（含 PyMuPDF、pdf2docx、minio、openai、pytest、ruff） |
| MySQL | `G:\deepseek\.toolchain\mysql8`（8.0.29 免安装版） | 数据目录在 `mysql8\data`，root 密码见 `backend/.env` |
| MinIO | `G:\deepseek\.toolchain\minio\minio.exe`（RELEASE.2025-09-07） | 数据目录 `G:\deepseek\.minio-data`（已 gitignore）；9000 API / 9001 控制台 |
| LibreOffice | `G:\deepseek\.toolchain\libreoffice`（26.2.6，MSI 管理安装解包） | 路径由 `SOFFICE_PATH` 配置，非硬编码 |
| H5 开发服务器 | <http://127.0.0.1:5173> | `npm run dev:h5`，当前在运行 |
| 后端 API | <http://127.0.0.1:8000>（监听 0.0.0.0:8000） | `/docs` 可交互调试 |

启动命令：

```powershell
# MySQL / MinIO（已初始化过的机器只需 start）
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_mysql.ps1 start
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_minio.ps1 start

# 后端
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 学生端 H5
cd student-app
npm.cmd run dev:h5
```

> PowerShell 若报"禁止运行脚本"，统一用 `npm.cmd` / `npx.cmd`，不要用 `npm.ps1`。

---

## 3. 已完成模块

| 阶段 | 内容 | 状态 | 关键产出 |
|---|---|---|---|
| 1 | 项目初始化 | ✅ | FastAPI 骨架、8 张表、统一响应、配置文件、compose、README |
| 2 | 登录 + 上传 | ✅ | `AuthProvider`（device）、`FileStorage`（MinIO）、文件头校验、签名 URL、上传需 token |
| 3 | 订单核心 | ✅ | 订单号/取件码、状态机、Mock 支付、排队估算、首页汇总、公告列表 |
| 4 | 老师端接口 | ✅ | 队列、下载、开始/完成（可改价留痕）、批量完成、核销（含按码）、异常/恢复/关闭、收单开关、统计、公告 |
| 5-A | 文档解析 | ✅ | Word→PDF（LibreOffice）、PDF 解析（页数/文本/图片/页边距/空白页/章节/参考文献）、缓存 |
| 5-B | 格式体检 | ✅ | 规则引擎 + 大模型混合、`/api/ai/check`、问题合并去重排序 |
| 5-C | 错别字 + 打印方案 | ✅ | 分块并发校对（进度轮询）、打印方案（服务端算钱、省纸计算） |
| 5-D | OCR + PDF 转 Word | ✅ | 多模态 OCR、pdf2docx 转换（不调大模型） |
| 扩展 | 排版属性检查 | ✅ | 从 PDF 提取正文常见字体/字号/行距，按 `common` 口径只出建议 |
| 6 | 文档修复 | ✅ | AI 出 actions、代码执行、序号保留、`⟨待补⟩` 占位、生成副本 |
| 7 | 学生端 uni-app | 🔄 | 4 个页面 + auth/filePicker/notify 适配层；H5 冒烟通过；真机 plus.io 待验证 |
| 8 | 老师端 Vue3 后台 | ⬜ | 未开始 |
| 9 | 打包与交付 | ⬜ | 未开始（HBuilderX 云打包可用） |

---

## 4. 关键决策（都有原因，不要轻易推翻）

1. **Python 用 3.14.7**：用户明确指定用本机版本，不额外装 3.11。代码保持 3.11 兼容写法（`from __future__ import annotations`、标准库类型）。
2. **数据库建表双轨**：启动时 `create_all`（`AUTO_CREATE_TABLES=true`）+ 可执行 `schema.sql`，不引入 Alembic（V1 轻量）。
3. **统一响应约定**：业务错误与参数校验错误返回 **HTTP 200 + 非 0 code**；未预期异常返回 HTTP 500 但响应体仍是 `{code,message,data}`。前端只解析 code。
4. **金额一律「分」整数**：单价读 `config/pricing.json`；打印方案里模型只给推荐与理由，**纸张数/金额由服务端算**，不让模型算钱。
5. **截单语义（用户确认）**：老师关闭时段 / 容量满 / 不在时段内，**都不拒绝下单**，新订单排到下一个打印时段并在首页与下单响应里提示；已在队列的订单不受影响。
6. **四个抽象模块**：auth / payment / notifier / printer 全部「抽象接口 + 配置切换 + 工厂函数」，业务代码不 import 具体实现；V2 新增实现只改一行配置。
7. **AI 只给建议**：体检与修复都只出结构化建议/指令；修复必须勾选问题后执行，且改的是副本，原文件保留。
8. **规则引擎 + 大模型混合**：字体/字号/行距/页边距/空白页/图片越界用规则引擎（可复现），参考文献著录、结构语义用大模型；合并时同一位置同一类型以规则为准。
9. **对象 key 由服务端生成**：`files/YYYY/MM/<uuid>.<ext>`，不含原文件名（防枚举 + 避开中文编码）。
10. **上传校验读文件头**：magic number 与扩展名不符直接拒绝；白名单含 ppt/pptx/xls/xlsx（讲义模式省纸需要）。
11. **缓存用 `ai_reports`**：用 `report_json.kind` 区分 `parse / check / proofread / print_plan / ocr / fix`，同 `file_key` 不重复计算。
12. **长任务进度用内存表 + 轮询**：`task_registry` 单进程内存实现；V2 多实例换成 Redis 时接口不变。前端不使用 WebSocket。
13. **LibreOffice 子进程环境**：显式把 `TMP/TEMP` 指向纯 ASCII 目录，且**不能用 `subprocess.PIPE`**（会因后台子进程继承管道而永久阻塞）。详见 `doc_parser.convert_to_pdf`。
14. **学生端先 H5**：用户是前端新手，开发期用 HBuilderX「运行到浏览器」看效果；CLI 项目（`student-app`）已本地构建通过。
15. **接口地址可配置**：`.env.development` 用 `http://127.0.0.1:8000`（浏览器预览），`.env.production` 用局域网 IP（真机/APK）。

---

## 5. 已知问题与风险

| 问题 | 影响 | 现状 / 建议 |
|---|---|---|
| App 端 `plus.io.chooseFile` 未真机验证 | 手机上可能选不了文档 | 代码已按能力检测实现，失败有明确提示；**「载入示例论文」是兜底**（调 `POST /api/file/demo`）。有真机后需实测 |
| Windows 防火墙没有 8000 入站规则 | 手机访问电脑 API 可能被拦 | 当前账号不是管理员，无法自动添加；需用户用管理员 PowerShell 执行 README 中的 `netsh` 命令 |
| MinIO 官方已停发二进制 | 换机可能下不到 minio.exe | 当前 exe 是 GitHub 最后一个带二进制的版本（2025-09-07），**请备份该 exe** |
| LibreOffice 用 MSI 管理安装解包 | 非标准安装方式，个别功能可能受限 | headless 转换已实测可用；若换机能正常安装，改 `SOFFICE_PATH` 即可 |
| 用户目录含中文导致 soffice 失败 | 转换报 `source file could not be loaded` | 已在代码中覆盖子进程 TMP/TEMP 为 ASCII 目录规避 |
| 页边距由文本块坐标推算 | 上边距会受标题段前距影响偏大 | 上边距容差放宽到 1cm，且标记为 `common` 非强制参考值 |
| PDF 中图片颜色判断依赖 colorspace | 灰度扫描件可能被当成彩色/灰度误判 | 仅作打印方案特征，学生可手动改颜色 |
| PDF 转 Word 对扫描件效果差 | 扫描版 PDF 转出的文字可能为空 | 接口 note 已提示"扫描件请先 OCR"；后续可自动检测文本层 |
| 长任务进度是单进程内存 | 多进程/重启后任务进度丢失 | V1 单机演示可接受；V2 换 Redis 实现（接口不变） |
| LLM 调用有成本 | 演示时反复体检会消耗额度 | 解析/体检/方案均有缓存；`force=true` 才会重算 |
| 学生端只验证了 H5 | App/小程序端未实测 | H5 已用 Edge 冒烟（0 错误）；App 需 HBuilderX 真机运行 |

---

## 6. 下一步计划

1. **阶段 7 收尾**：用户用 HBuilderX 打开 `student-app` 预览四个页面；根据反馈调整文案/交互；有真机后验证 `plus.io.chooseFile`。
2. **阶段 8 老师端**：Vue3 + Element Plus 后台（登录、工作台队列、完成改价弹窗、核销、收单开关、公告、流水）。
3. **阶段 9 打包交付**：HBuilderX 云打包 APK（需替换 manifest 的 appid 与签名证书）、部署文档、安装说明。
4. 可选优化：扫描件自动检测、报告 7 天清理定时任务、MinIO 对象生命周期。

**更新规则**：每完成一个阶段，更新本文件的「已完成模块 / 已知问题 / 下一步」，并做一次 git commit。

---

## 7. 常用验证命令

```powershell
# 后端测试 + 静态检查
cd backend
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .

# 模型连通性
.venv\Scripts\python.exe test_llm.py

# 生成答辩演示论文（含格式错误）
.venv\Scripts\python.exe scripts\make_demo_docx.py

# 前端构建
cd ..\student-app
npm.cmd run build:h5
```

阶段验收用的真实链路脚本（一次性，未入库）在 `G:\deepseek\.tmp\acceptance_*.py`。
