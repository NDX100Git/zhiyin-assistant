# 智印助手 · 架构说明

> 面向完全不了解项目的人。先读 [PROJECT_STATE.md](PROJECT_STATE.md) 了解进度，
> 本文讲"系统由哪些部分组成、数据怎么流动、改代码该去哪里"。

---

## 1. 技术栈与总体结构

| 层 | 技术 | 目录 |
|---|---|---|
| 学生端 | uni-app（Vue3 + Vite） | `student-app/` |
| 老师端 | Vue3 + Element Plus（阶段 8，未开始） | 预留 `teacher-web/` |
| 后端 | Python + FastAPI | `backend/app/` |
| 数据库 | MySQL 8 + SQLAlchemy 2.x | `backend/app/models/`、`schema.sql` |
| 对象存储 | MinIO（抽象层可换云存储） | `backend/app/services/storage/` |
| AI | DeepSeek（OpenAI 兼容 SDK，model=`deepseek-flash`） | `backend/app/services/llm.py` |
| 文档 | LibreOffice headless、PyMuPDF、pdf2docx、python-docx | `backend/app/services/` |

请求链路（V1）：

```
uni-app / H5 ──HTTP(JSON/multipart)──> FastAPI ──> MySQL
                                        │
                                        ├──> MinIO（私有桶 + 30 分钟签名 URL）
                                        └──> DeepSeek（文本 / 多模态）
```

---

## 2. 目录结构（带职责）

```
zhiyin-assistant/
├─ PROJECT_STATE.md              项目进度、决策、已知问题（每阶段更新）
├─ ARCHITECTURE.md               本文件
├─ README.md                     启动与工程约定
├─ docs/HANDOFF.md               原始需求规格（唯一需求来源）
├─ backend/                      FastAPI 后端
│  ├─ app/
│  │  ├─ main.py                 入口：lifespan 建表、CORS、异常处理、路由挂载
│  │  ├─ config.py               环境变量 + config/*.json 的唯一读取入口
│  │  ├─ database.py             SQLAlchemy engine / Session / init_db
│  │  ├─ models/                 8 张表的 ORM 模型（见第 4 节）
│  │  ├─ schemas/                Pydantic 入参/响应模型
│  │  ├─ routers/                按 URL 分组的接口（见第 5 节）
│  │  ├─ services/               业务逻辑（见第 6 节）
│  │  └─ utils/                  统一响应、异常、JWT、文件头识别
│  ├─ config/                    运行期规则（价格、格式、上传、订单、AI 参数）
│  ├─ scripts/                   免安装 MySQL/MinIO、演示论文生成
│  ├─ tests/                     pytest（连真实 MySQL + MinIO）
│  ├─ schema.sql                 与 ORM 等价的建表脚本
│  ├─ test_llm.py                DeepSeek 连通性自检（独立运行）
│  └─ requirements*.txt          运行 / 开发依赖
└─ student-app/                  学生端 uni-app
   ├─ src/pages/                 首页、下单、我的、订单详情
   ├─ src/services/              api / auth / filePicker / notify 适配层
   ├─ src/config/index.js        API 地址、轮询间隔（来自 .env）
   ├─ src/pages.json             页面与 tabBar 配置
   ├─ src/manifest.json          App 名称、权限、Android minSdk=26
   ├─ .env.development           H5 调试 → http://127.0.0.1:8000
   └─ .env.production            真机/APK → 电脑局域网 IP
```

---

## 3. 核心业务流

### 3.1 学生主链路

```
登录（设备标识）→ 上传文档（服务端生成 key）→ AI 体检（解析 + 规则 + 模型）
   → 勾选问题生成修复副本（可选）→ 打印方案（推荐参数 + 省纸省钱）
   → 创建订单（pending_pay，服务端生成订单号/取件码）
   → 模拟支付（paid=1，写 payment_logs）→ queuing
   → 轮询订单状态（3-5 秒，页面隐藏即停）
```

### 3.2 文印室链路

```
老师登录（环境变量账号）→ 队列 → 下载签名 URL → 开始打印（printing）
   → 打印完成（自动计价预填，可改价并留痕）→ ready
   → 按订单/取件码核销 → completed（未付款则现场收款写 payment_logs）
```

### 3.3 AI 体检内部流程（5-B）

```
上传文件 → doc_parser.parse_document（缓存 kind=parse）
      → 规则引擎 collect_facts（空白页/图片越界/页边距/结构/排版属性）
      → 大模型任务一：结构完整性 + 参考文献著录（JSON 模式 + 思考模式）
      → 大模型任务二：版式诊断（把规则事实作为客观依据）
      → merge_issues（规则优先、同位置同类型去重、按严重度排序）
      → 存 ai_reports（kind=check）→ 返回问题清单
```

### 3.4 文档修复（阶段 6）

```
check 报告 + 用户勾选的 issue_ids
   → 大模型输出 actions（只允许 4 类）
   → sanitize_actions 校验取值
   → python-docx 执行（set_line_spacing / set_font / remove_blank_page / fix_reference）
   → 新 docx 存 MinIO（kind=fix 记录）→ 返回新 file_key
```

**硬约束**：模型不生成文档；未勾选不修复；原文件不动；模型遗漏的 `[1]` 序号由代码补回；
无法推断的信息用 `⟨待补⟩` 占位，禁止编造。

---

## 4. 数据表（MySQL 8，共 8 张）

| 表 | 用途 | 关键字段 |
|---|---|---|
| `users` | 学生/设备身份 | `openid`(设备标识)、`nickname`、`noshow_count`、`auth_provider`、`student_no_hash`（预留，V1 不采集） |
| `orders` | 订单主表 | `order_no`(MMDD-序号，唯一)、`pickup_code`、`status`、`file_key`、页数/单双面/颜色/份数、`amount_cents`(分)、`paid`、`paid_at`/`printed_at`/`picked_at`、预留 `site_id`/`pay_channel`/`external_pay_id`/`printer_id` |
| `payment_logs` | 收款流水 | `order_id`、`amount_cents`、`method`(mock/onsite)、`operator` |
| `queue_sessions` | 上/下午收单设置 | `date + session` 唯一、`is_open`、`capacity`、`used`、`closed_at` |
| `notices` | 公告 | `content`、`created_by`、`active` |
| `print_logs` | 订单操作日志 | `order_id`、`action`(state_change/note/abnormal)、`from_status`/`to_status`、`note`、`operator` |
| `ai_reports` | AI 结果缓存 | `file_key`、`report_json`（JSON，含 `kind`：parse/check/proofread/print_plan/ocr/fix） |
| `notifications` | 待发消息事件 | `user_id`、`order_id`、`event`、`payload`、`status`、`sent_at` |

约定：

- 金额永远是「分」的整数，禁止浮点。
- 时间由服务端生成与判断（`CURRENT_TIMESTAMP` / `datetime.now()`），不信任客户端时间。
- `schema.sql` 与 ORM 模型等价；启动时 `AUTO_CREATE_TABLES=true` 会自动建表。

---

## 5. 接口清单

统一响应：`{"code":0,"message":"ok","data":{...}}`。业务/校验错误返回 HTTP 200 + 非 0 code；
未预期异常 HTTP 500 + 同一结构。除登录、健康检查、`/docs` 外均需 `Authorization: Bearer <token>`。

### 5.1 公共 / 学生端

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查（免 token） |
| POST | `/api/auth/login` | 设备登录，返回 JWT（免 token） |
| POST | `/api/auth/profile` | 更新昵称 |
| POST | `/api/file/upload` | 上传（≤50MB；白名单；文件头校验） |
| POST | `/api/file/demo` | 载入服务端示例论文（App 选文件失败兜底） |
| POST | `/api/ai/check` | AI 体检（规则 + 大模型） |
| POST | `/api/ai/proofread` | 错别字检查，返回 `task_id` |
| GET | `/api/ai/proofread/{task_id}` | 轮询进度与结果 |
| POST | `/api/ai/print-plan` | 打印方案（含省纸省钱） |
| POST | `/api/ai/ocr` | 图片转文字（多模态） |
| POST | `/api/ai/convert` | PDF 转 Word（pdf2docx） |
| POST | `/api/ai/fix` | 勾选问题生成修复副本 |
| POST | `/api/order/create` | 下单，返回订单号/取件码/时段提示 |
| GET | `/api/order/list` | 我的订单（可按状态筛选） |
| GET | `/api/order/{id}` | 详情（含排队位置与预计时间） |
| POST | `/api/order/{id}/pay` | 模拟支付 |
| POST | `/api/order/{id}/cancel` | 取消（仅待支付/排队中） |
| GET | `/api/home/summary` | 首页汇总（队列、待取件、收单状态） |
| GET | `/api/notice/list` | 公告列表 |

### 5.2 老师端

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/admin/login` | 账号密码登录（免 token） |
| GET | `/api/admin/queue` | 队列（状态筛选 + 关键字搜索） |
| GET | `/api/admin/order/{id}/download` | 30 分钟签名下载链接 |
| POST | `/api/admin/order/{id}/start` | 开始打印 → printing |
| POST | `/api/admin/order/{id}/complete` | 打印完成 → ready（可改价，留痕） |
| POST | `/api/admin/order/batch-complete` | 批量完成（逐单返回成功/失败） |
| POST | `/api/admin/order/{id}/verify` | 核销 → completed（未付款现场收款） |
| POST | `/api/admin/order/verify-code` | 按取件码核销 |
| POST | `/api/admin/order/{id}/abnormal` | 标记异常 |
| POST | `/api/admin/order/{id}/recover` | 异常恢复排队 |
| POST | `/api/admin/order/{id}/cancel` | 关闭异常单 |
| POST | `/api/admin/session/close` / `open` | 关闭/恢复时段收单 |
| GET | `/api/admin/summary` | 今日接单数/收款额/待取件/异常 |
| POST | `/api/admin/notice` | 发布公告 |

---

## 6. 关键文件职责

### 6.1 后端服务

| 文件 | 职责 |
|---|---|
| `services/doc_parser.py` | 下载文件 → Office 转 PDF → PyMuPDF 解析（页数/文本/图片/页边距/空白页/章节/参考文献/排版属性）→ `ai_reports` 缓存 |
| `services/format_rules_engine.py` | 规则引擎：空白页、图片越界、页边距偏差、结构缺失/乱序、字体字号行距 |
| `services/format_checker.py` | 组装提示词、调模型、合并规则与 AI 结果、排序去重、缓存；按 paper/slide/sheet 选择检查口径 |
| `services/proofreader.py` | 分块（默认 2500 字）、`asyncio.gather` 限流 3 路、进度回调、合并去重 |
| `services/print_planner.py` | 文档特征、模型推荐、方案校验、服务端算纸张与金额 |
| `services/doc_fixer.py` | actions 校验 + python-docx 执行 + 生成副本 + 序号保留 |
| `services/ocr.py` | 图片 base64 内联 → 多模态模型 → 纯文本 |
| `services/converter.py` | pdf2docx 转换（本地函数与上传接口共用） |
| `services/order_state.py` | 状态机 `TRANSITIONS`、`change_status`（写 print_logs + notifications） |
| `services/order_service.py` | 下单/列表/详情/取消/支付/首页汇总、日序号与取件码 |
| `services/admin_service.py` | 老师端全部操作与今日统计 |
| `services/queue_service.py` | 队列位置、近 20 单页数加权等待时间 |
| `services/session_service.py` | 上午/下午时段、容量、截单提示 |
| `services/pricing.py` | 读 `pricing.json`，分单位计价 |
| `services/llm.py` | 唯一模型入口：同步/异步、JSON 模式、思考模式、3 次重试、JSON 容错 |
| `services/task_registry.py` | 长任务进度表（V1 内存，V2 可换 Redis） |
| `services/storage/` | `FileStorage` 抽象 + MinIO 实现 + 服务端 key 生成 |
| `services/auth/` | `AuthProvider` 抽象 + device/admin 实现 + 工厂 |
| `services/payment/` | `PaymentProvider` 抽象 + mock 实现 + 工厂 |
| `services/notifier/` | `Notifier` 抽象 + poll 实现（只记录事件）+ 工厂 |
| `services/printer/` | `PrinterProvider` 抽象 + manual 实现 + 工厂 |
| `utils/exceptions.py` | `BusinessError` + 全局异常处理（中文提示） |
| `utils/filetype.py` | 文件头 magic number、OOXML 类型判定、字体名归一化（doc_parser 内） |
| `utils/security.py` | JWT 签发/解析、`get_current_user`、`require_admin` |

### 6.2 学生端

| 文件 | 职责 |
|---|---|
| `services/api.js` | 统一请求/上传、自动带 token、统一解析 code |
| `services/auth.js` | 设备标识登录、token 存取（页面不直接调 `uni.login`） |
| `services/filePicker.js` | H5/小程序 `uni.chooseFile`、App `plus.io.chooseFile`、`uni.chooseImage`、示例论文 |
| `services/notify.js` | Toast + `createPoller`（start/stop/refresh） |
| `pages/index/index.vue` | 首页：排队数、预计等待、待取件、收单状态、公告 |
| `pages/order/order.vue` | 选文件 → 上传 → 体检 → 勾选修复 → 打印方案 → 下单 → 支付 |
| `pages/mine/mine.vue` | 我的订单：状态筛选、昵称、支付/取消 |
| `pages/mine/detail.vue` | 订单详情：取件码大字、排队进度、时间线、取消/支付 |

---

## 7. 配置项

### 7.1 JSON 配置（改行为只改文件，不改代码）

| 文件 | 内容 |
|---|---|
| `config/pricing.json` | 单/双面、黑白/彩色单价（分/页） |
| `config/format_rules.json` | 结构要求、参考文献规则、页边距、字体字号行距（含 `_source` 强制/通行参考） |
| `config/upload_rules.json` | 50MB 上限、扩展名白名单（含 PPT/Excel）、文件名长度 |
| `config/order_rules.json` | 时段窗口、容量、截单提示、等待算法参数、下单限制、取件码字母表 |
| `config/ai_rules.json` | 校对分块大小/并发数/最大块数、打印方案可选版式与缩放 |
| `config/features.json` | V2 功能开关（支付/微信登录/推送/多点/自动打印） |

### 7.2 环境变量（`backend/.env`，已 gitignore）

数据库、MinIO、`LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL`、管理员账号、
`JWT_SECRET`（≥32 字节）、文件保留时长、签名有效期、`SOFFICE_PATH`/`SOFFICE_TIMEOUT_SECONDS`、
四个 Provider 选择（`AUTH_PROVIDER`/`PAYMENT_PROVIDER`/`NOTIFIER_PROVIDER`/`PRINTER_PROVIDER`）。

模板见 `backend/.env.example`。

---

## 8. 订单状态机

```
pending_pay --支付--> queuing --开始打印--> printing --打印完成--> ready --核销--> completed
pending_pay --取消--> cancelled
queuing     --取消--> cancelled
queuing / printing / ready --标记异常--> abnormal
abnormal    --恢复--> queuing
abnormal    --关闭--> cancelled（仅老师）
其余流转一律拒绝（40900，中文提示）
```

每次变更写 `print_logs`（from/to/operator/note），并写一条 `notifications` 事件（V1 不发）。

---

## 9. 工程约定（改代码前必读）

1. API Key 只在 `backend/.env`，前端任何位置不出现。
2. 订单号/取件码/状态/时间/金额全部服务端生成或推进。
3. 所有金额用 `int` 表示分。
4. 所有接口返回统一 envelope；失败用 `BusinessError(code, message)`。
5. 所有可变参数放 `config/*.json` 或环境变量。
6. V2 扩展点：新增 Provider 实现类 + 改配置；业务代码只调用工厂函数。
7. 前端页面不直接调用 `uni.login` / `uni.chooseFile`，只走 `services/*` 适配层。
8. 轮询必须在 `onShow` 启动、`onHide` 停止、`onUnmounted` 兜底清理。
9. 长文档必须分块处理；AI 输出必须 JSON 容错解析。
10. AI 结果只是建议；修复需勾选，AI 不直接生成文档。

---

## 10. 踩过的坑（避免重复浪费时间）

| 现象 | 原因 | 解决 |
|---|---|---|
| H5 上传报 `file 字段要求` | `uni.uploadFile` 被设置了 `Content-Type: application/json`，缺 multipart boundary | 上传请求头不要手动设置 Content-Type |
| `soffice --version` / 转换卡死 | `subprocess.PIPE` 被后台子进程占住 | 输出重定向到日志文件 |
| 转换报 `source file could not be loaded` | `TMP/TEMP` 指向含中文的用户目录 | 子进程显式使用纯 ASCII 临时目录 |
| 转换一夜之间"坏了" | 多个 soffice 实例/残留 profile | 每次转换独立 `-env:UserInstallation` |
| PowerShell 中文脚本报错 | UTF-8 无 BOM 被按 GBK 读 | `*.ps1` 存为 UTF-8 BOM |
| `npm` 无法运行 | 执行策略禁止 `npm.ps1` | 统一用 `npm.cmd` / `npx.cmd` |
| MinIO 下载 410 | 官方停发二进制 | 用 GitHub 最后带二进制的 release（2025-09-07） |
| venv 里 `_ssl` 失败 | 受限终端 + E: 盘 Python | 在普通终端运行；或用 base pip `--python` 安装 |

---

## 11. V2 演进检查表

| 需求 | 只需改哪里 |
|---|---|
| 真实支付 | 新增 `payment/wechat.py` + 配置 `PAYMENT_PROVIDER` + 回调路由 |
| 微信/学校统一认证 | 新增 `auth/wechat.py` / `auth/cas.py` + 配置 |
| 微信订阅消息 / APP 推送 | 新增 `notifier/wechat.py` / `push.py` + 配置 |
| 云打印 | 新增 `printer/cloud.py` + 配置 |
| 云对象存储 | 新增 `storage/xxx.py` + 工厂分支 |
| 多文印点 | 启用 `orders.site_id` + 路由层过滤（新增逻辑，不改现有服务） |
| 学校格式规范 | 替换 `config/format_rules.json` |
