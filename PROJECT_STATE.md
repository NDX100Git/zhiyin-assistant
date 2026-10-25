# 智印助手 · 项目状态（给未来的接手者）

> 最后更新：2026-10-25
> 本轮（2026-10-25）的 22 项缺陷修复与完整实测记录见 [HANDOFF-2026-10-25.md](HANDOFF-2026-10-25.md)，
> 本文件只保留"现在到哪了"的状态摘要。
> 阅读顺序建议：先看本文件了解"现在到哪了"，再看 [ARCHITECTURE.md](ARCHITECTURE.md) 了解"代码怎么组织"，
> 最后按需查阅 [docs/ORIGINAL_SPEC.md](docs/ORIGINAL_SPEC.md)（原始需求规格，唯一需求来源）。

---

## 0. 5 分钟上手（接手者 checklist）

1. 读本文件第 1-4 节，知道现在做到哪、哪些决策不能随便改。
2. 按第 2 节启动 MySQL、MinIO、后端、学生端 H5。
3. 打开 <http://127.0.0.1:8000/docs> 看接口，打开 <http://127.0.0.1:5173> 看学生端。
4. 跑 `cd backend && python -m pytest -q`，确认 **451 passed / 1 skipped**（跳过的是需要
   LibreOffice 的那一条 Office 转换用例；其余全绿）。
5. 要改哪个模块，先查 [ARCHITECTURE.md](ARCHITECTURE.md) 第 6 节的"关键文件职责"。
6. 改完一个阶段：更新本文件 + 跑测试 + `git commit`（message 写清楚阶段和内容）。

---

## 1. 一句话与当前状态

智印助手：学生上传论文 → AI 做格式与版式体检 → 给修复建议与打印方案 → 下单排队 →
文印室老师打印 → 学生凭取件码取件并结清。

**当前阶段：V1 核心闭环已跑通（后端全部业务 + 学生端 H5 五个页面），阶段 7 收尾中，阶段 8/9 未开始。**

最近一次验证（2026-10-25，本轮重跑了后端）：

- 后端 `pytest -q`：**451 passed / 1 skipped**（跳过的是 `tests/test_doc_parser.py` 里需要
  LibreOffice 的 Office 转换用例）
- 同一套用例在三套时钟下数字一致：真实墙钟、`CLOCK_SHIFT_TIME=18:43`、`CLOCK_SHIFT_TIME=06:00`
  （后两者用 `tests/clock_shift_plugin.py` 把应用侧时钟拨早，验证排队与过期判定不靠应用时钟）
- 后端 `ruff check .`：All checks passed
- 后端接口：`/openapi.json` 里 **44 条** `/api` 操作（`backend/app/routers/` 七个路由模块）
- 学生端 `npm run build:h5`：构建成功（23 秒，本轮未重跑）
- 老师端 `npm run build`：构建成功（12 秒，主 chunk 1.06 MB，有 chunk 体积警告，本轮未重跑）
- 浏览器端到端（`cd e2e && node run.mjs --start`）：脚本现为 **56 步 × 2 视口（390/1440）**
  （`e2e/run.mjs` 里 56 个 `step()` 调用）；本轮未重跑浏览器回归，上一次全通记录是 41 步时
  的 [HANDOFF-2026-10-25.md](HANDOFF-2026-10-25.md)，
  覆盖单文件/多文件/四文件闭环、换文件确认、打印前拦截、两套皮肤、深色模式对比度抽查
- 唯一未覆盖：Office 文档 → PDF 转换（本机无 LibreOffice）

---

## 2. 本机开发环境（重要：换机时先核对）

| 组件 | 位置 / 版本 | 说明 |
|---|---|---|
| Python | Python 3.12+（本机验证 3.14.7） | 用系统 Python 创建 `backend/.venv`，路径因机器而异 |
| 后端 venv | `backend/.venv` | 依赖版本见 `backend/requirements.txt`（已锁定） |
| MySQL | `backend/.tools/mysql8`（可用 `ZHIYIN_MYSQL_ROOT` 覆盖） | 免安装版；数据目录在 `mysql8\data`；密码见 `backend/.env` |
| MinIO | `backend/.tools/minio/minio.exe`（可用 `ZHIYIN_MINIO_EXE` 覆盖） | 数据目录 `backend/.data/minio`；9000 API / 9001 控制台 |
| LibreOffice | 路径由 `SOFFICE_PATH` 配置 | Windows 默认 `C:\Program Files\LibreOffice\program\soffice.exe` |
| H5 开发服务器 | <http://127.0.0.1:5173> | `npm run dev:h5`，当前在运行 |
| 老师端 Web | <http://127.0.0.1:5174> | `npm.cmd run dev` 或双击 `teacher-web/dev.cmd`，当前在运行 |
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

| 阶段 | 内容 | 属于 | 状态 | 关键产出 |
|---|---|---|---|---|
| 1 | 项目骨架、13 张表、统一返回格式、全局异常 | 后端 | ✅ | FastAPI 骨架、SQLAlchemy 模型、`schema.sql`、配置文件、compose、README |
| 2 | 登录 + 上传 | 后端 | ✅ | `AuthProvider`（device）、`FileStorage`（MinIO）、文件头校验、签名 URL、上传需 token |
| 3 | 订单核心 | 后端 | ✅ | 订单号/取件码、状态机、Mock 支付、排队估算、首页汇总、公告列表 |
| 4 | 老师端接口 | 后端 | ✅ | 队列、下载、开始/完成（可改价留痕）、批量完成、核销（含按码）、异常/恢复/关闭、收单开关、统计、公告 |
| 5-A | 文档解析 | 后端 | ✅ | Word→PDF（LibreOffice）、PDF 解析（页数/文本/图片/页边距/空白页/章节/参考文献）、缓存 |
| 5-B | 格式体检 | 后端 | ✅ | 规则引擎 + 大模型混合、`/api/ai/check`、问题合并去重排序、按 paper/slide/sheet 分口径 |
| 5-C | 错别字 + 打印方案 | 后端 | ✅ | 分块并发校对（进度轮询）、打印方案（服务端算钱、省纸计算、可选 AI/规则方案） |
| 5-D | OCR + PDF 转 Word | 后端 | ✅ | 多模态 OCR、pdf2docx 转换（不调大模型） |
| 扩展 | 排版属性检查 | 后端 | ✅ | 从 PDF 提取正文常见字体/字号/行距，按 `common` 口径只出建议 |
| 6 | 文档修复 | 后端 | ✅ | AI 出 actions、代码执行、序号保留、`⟨待补⟩` 占位、生成副本 |
| 7 | 学生端 uni-app | 学生端 | 🔄 | 5 个页面 + auth/filePicker/notify 适配层；H5 冒烟通过；**体检/修复可选**，基础方案可直接下单；打印方案可手动改单双面/颜色/版式/份数；真机 plus.io 待验证 |
| 8 | 老师端 Vue3 后台 | 老师端 | ✅ | 登录、工作台（今日数据/队列/下载/开始/完成改价/核销/批量完成/异常处理）、收款流水、收单开关、公告发布；Edge E2E 通过 |
| 9 | 打包与交付 | 文档/工程 | 🟡 | 已配置 appid 占位、包名、图标、权限、minSdk 26；已写打包/部署/安装说明；H5/小程序/App 资源构建通过；**APK 待用 HBuilderX 云打包** |

---

## 4. 关键决策（都有原因，不要轻易推翻）

1. **Python 用 3.14.7**：用户明确指定用本机版本，不额外装 3.11。代码按 3.12+ 写（下界不许退回 3.11）：
   `app/schemas/common.py` 的 `class ApiResponse[T](BaseModel)` 是 3.12 才支持的 PEP 695 泛型语法，
   3.11 下 import 就 SyntaxError；`backend/requirements.txt` 注释与 `backend/Dockerfile` 同样是 3.12 下界。
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
16. **按文档类型选择体检口径**：`format_checker.detect_profile()` 区分 paper（pdf/doc/docx）、slide（ppt/pptx）、sheet（xls/xlsx）。论文套用结构与参考文献规则；PPT/表格只做版式检查，避免报"缺摘要/缺参考文献"这类无意义问题。报告里带 `profile` 与 `profile_note`。
17. **AI 全程可选**：上传后先用 `POST /api/file/page-count` 取页数（不调模型），再用 `use_ai=false` 生成基础打印方案，学生可直接下单；想体检再点「开始 AI 体检」，体检完成后才出现报告与"AI 修复"开关（默认关闭、默认不勾选任何问题）。
18. **老师改价后补差**：学生已支付但老师上调金额时，订单 `paid` 置 0 表示需补款；核销时按"最终金额 − 已支付流水合计"只收差额，并写一条 `onsite` 流水。不会重复收全款，也不会漏收差额。

---

## 4.1 近期修复记录（防止回归）

| 日期 | 问题 | 原因 | 修复 |
|---|---|---|---|
| 2026-10-25 | 换过文件的单在应用机慢于数据库时排到队首，而不是队尾 | 订单创建时间 `created_at` 取数据库 `CURRENT_TIMESTAMP`，重排队时间 `requeue_at` 取应用进程的 `datetime.now()`，两套时钟不同源 | `file_change_service.db_now()`：本模块写时间列一律读数据库时钟，队列排序只依赖一个时钟；补 `tests/test_file_change.py::test_应用时钟慢于数据库_换文件后仍排到队尾` 锁住语义 |
| 2026-10-25 | 老师改价只挡负数，30 元误输成 300 元照样落库 | 只判断 `< 0` | `pricing.ensure_amount_allowed()`：单笔上限 1000 元 + 相对系统价 10 倍，且至少给 20 元浮动空间（小额单不至于被拦）；`complete_print` 与换文件确认价都接上，错误码 `40062` |
| 2026-10-25 | 核销覆盖线上支付时间，对账口径错位 | `verify_pickup` 无条件写 `paid_at` | 只在 `paid_at` 为空时写入；补款金额照旧记 `PaymentLog` |
| 2026-10-25 | 已打印的异常单恢复后被重新打印一遍 | `ready→abnormal→queuing` 回路，`recover_order` 无业务校验 | 已打印过的异常单恢复到 `ready`（状态机补 `ABNORMAL→READY`）；`printed_at` 改为只在首次进 `ready` 时写 |
| 2026-10-25 | 报价不校验真实页数，改小 `page_count` 可拿低价估价 | 校验只在 `create_order` 里 | `validate_declared_page_counts` 移入下单/报价共用的 `_prepare_items` |
| 2026-10-25 | 清理任务中途崩溃留下「对象已删、记录还在」的僵尸行 | 整轮只在末尾 `commit()` | 保持「先删对象、后删记录」，但改为**逐条提交**，窗口压到单条记录 |
| 2026-10-25 | soffice 超时只杀父进程，孙进程锁住 profile 目录 | `proc.kill()` 不杀进程组 | Windows `taskkill /F /T`、POSIX `killpg`（`Popen` 加 `start_new_session=True`），kill 后 `wait` 确认 |
| 2026-10-25 | 模型返回 `"双面"` 这类脏值直接 500 | `int()` 在异常捕获之外 | `_safe_int()` 统一按默认方案降级 |
| 2026-10-25 | 老师端手工写个 localStorage 就能进后台 | 守卫只判断 token 是否存在 | 校验三段式 JWT 且 `exp` 未过期；真正的鉴权仍由后端 401 兜底 |
| 2026-10-17 | H5 上传报"参数校验失败：file 字段要求" | `uni.uploadFile` 被强行带上 `Content-Type: application/json`，浏览器无法生成 multipart boundary | 上传专用请求头只带 Authorization，交给运行时设置 Content-Type；`api.js` 已加注释警示 |
| 2026-10-17 | PPT 体检误报"缺摘要/缺参考文献" | 所有文档都套用了论文结构规则 | 新增 paper/slide/sheet 口径；PPT 只检查版式（图片越界、空白页） |
| 2026-10-17 | 用户不想体检也要等 AI | 上传后自动触发体检 | 新增页数接口 + 基础方案；体检与修复都改为用户主动开启 |

完整清单（22 项）见 [HANDOFF-2026-10-25.md](HANDOFF-2026-10-25.md) 第三节。

---

## 5. 已知问题与风险

| 问题 | 影响 | 现状 / 建议 |
|---|---|---|
| App 端 `plus.io.chooseFile` 未真机验证 | 手机上可能选不了文档 | 代码已按能力检测实现，失败有明确提示；**「载入示例论文」是兜底**（调 `POST /api/file/demo`）。有真机后需实测 |
| Windows 防火墙没有 8000 入站规则 | 手机访问电脑 API 可能被拦 | 当前账号不是管理员，无法自动添加；需用户用管理员 PowerShell 执行 README 中的 `netsh` 命令 |
| MinIO 官方已停发二进制 | 换机可能下不到 minio.exe | 当前 exe 是 GitHub 最后一个带二进制的版本（2025-09-07），**请备份该 exe** |
| APK 尚未生成 | 云打包需要 DCloud 账号登录并手动点击；appid 仍是占位值 | 按 `docs/PACKAGING.md` 操作：换 appid → 选图标 → 云打包；安装说明见 `docs/INSTALL.md` |
| LibreOffice 用 MSI 管理安装解包 | 非标准安装方式，个别功能可能受限 | headless 转换已实测可用；若换机能正常安装，改 `SOFFICE_PATH` 即可 |
| 用户目录含中文导致 soffice 失败 | 转换报 `source file could not be loaded` | 已在代码中覆盖子进程 TMP/TEMP 为 ASCII 目录规避 |
| 老师端 `npm run build/dev` 报 esbuild Access denied | esbuild 在中文 TEMP 目录清理临时文件失败 | `teacher-web/dev.cmd` / `build.cmd` 已把 TMP/TEMP 指向项目内 `.runtime\tmp`；也可手动设置环境变量 |
| 页边距由文本块坐标推算 | 上边距会受标题段前距影响偏大 | 上边距容差放宽到 1cm，且标记为 `common` 非强制参考值 |
| PDF 中图片颜色判断依赖 colorspace | 灰度扫描件可能被当成彩色/灰度误判 | 仅作打印方案特征，学生可手动改颜色 |
| PDF 转 Word 对扫描件效果差 | 扫描版 PDF 转出的文字可能为空 | 接口 note 已提示"扫描件请先 OCR"；后续可自动检测文本层 |
| 长任务进度是单进程内存 | 多进程/重启后任务进度丢失 | V1 单机演示可接受；V2 换 Redis 实现（接口不变） |
| LLM 调用有成本 | 演示时反复体检会消耗额度 | 解析/体检/方案均有缓存；`force=true` 才会重算 |
| 学生端只验证了 H5 | App/小程序端未实测 | H5 已用 Edge 冒烟（0 错误）；App 需 HBuilderX 真机运行 |
| 后端容器已降权到 uid 10001，运行时目录改为命名卷 | 只做了配置层验证（YAML 可解析、卷名与挂载点一致） | **需在有 Docker 的机器上 `docker compose up -d --build` 实测一次**，确认容器能写 `/app/runtime/tmp` |
| Office 文档 → PDF 转换未实测 | 本机无 LibreOffice，1 条用例被 skip | 上传 PDF 的链路是通测的；有 LibreOffice 的机器上跑 `pytest -k 转换` 补齐 |
| 已支付订单学生仍可一键取消（无退款、无反向流水） | `STUDENT_CANCELLABLE` 含 `queuing` | **待产品口径**：收紧为「付了不能自取消」，或允许取消但走退款 + 反向流水 |
| 并发支付时真实扣款在状态保护之外 | mock 支付看不出 | **待产品口径**：`SELECT ... FOR UPDATE` 行锁 或 客户端幂等键 |
| `ai_reports` 缓存没有 `user_id` 维度 | 路由层 `ensure_files_owned_by` 已拦住跨用户路径 | 属纵深防御；改动面约 8 个文件（加列 + 幂等迁移 + 5 个 service 签名） |
| `layout` 版式参数不进 `Order` 也不参与计费 | 前端会展示「省 21 张纸」，但付款按页数算 | **答辩易被追问**；改动要动计费模型，属口径问题 |

---

## 6. 下一步计划

**先定口径（不定就别动手）**

1. **已支付订单能否取消**：A 收紧为「付了就不能自己取消」／B 允许取消但走退款 + 反向流水。
2. **支付并发**：A `SELECT ... FOR UPDATE` 行锁／B 客户端幂等键。
3. **`layout` 要不要参与计费**：现在展示「省纸」但按页数收款，两者口径不一致。

**可以直接做**

4. **补跑 Office 转换的 1 条用例**（本机缺 LibreOffice 被 skip）。
5. **把 `e2e/` 接进 CI**：`e2e/run.mjs` 4428 行 / 56 步全链路回归，已作为独立作业接入流水线（`.github/workflows/ci.yml` 的 browser-e2e）。
6. **CI 补 lint**：两个前端都已具备 `lint` 与 `test` 脚本并接入流水线，后端 ruff 也在流水线里跑。
7. **Docker 实测**：容器降权 + 命名卷需真机验证一次。
8. **阶段 9 打包交付**：按 `docs/PACKAGING.md` 在 HBuilderX 云打包 APK（需替换 appid、选择 `src/static/app-icon.png`、配置签名证书）；部署按 `docs/DEPLOYMENT.md`，评委安装看 `docs/INSTALL.md`。
9. **真机验证**：拿到 Android 手机后验证 `plus.io.chooseFile`，失败则继续用"载入示例论文"兜底。
10. 收集用户试用反馈，调整文案/交互/演示数据。

**更新规则**：每完成一个阶段，更新本文件的「已完成模块 / 已知问题 / 下一步」，并做一次 git commit。

---

## 7. 常用验证命令

```powershell
# 后端测试 + 静态检查（用虚拟环境里的 python；直接跑系统 python 也一样）
cd backend
.venv\Scripts\python.exe -m pytest -q      # 期望 451 passed, 1 skipped
.venv\Scripts\python.exe -m ruff check .   # 期望 All checks passed

# 模型连通性
.venv\Scripts\python.exe test_llm.py

# 生成答辩演示论文（含格式错误）
.venv\Scripts\python.exe scripts\make_demo_docx.py

# 前端构建
cd ..\student-app
npm.cmd run build:h5
```

阶段验收用的一次性脚本不入库，交付包中不包含。
