# 智印助手 · AI 论文格式体检与自助打印平台

面向高校文印室的智能化改造：学生上传文档 → AI 做格式与版式体检 → 生成修复建议和打印方案 →
下单排队 → 文印室打印 → 学生凭取件码取件并结清。

当前进度：**阶段 1（项目初始化）已完成**，后端骨架、数据模型、统一响应、配置文件、
容器编排与开发文档就绪，业务接口从阶段 2 开始逐个实现。

---

## 目录结构

```
zhiyin-assistant/
├─ docs/
│  └─ HANDOFF.md            # 项目交接文档（规格全文，唯一需求来源）
├─ backend/                 # Python 3.11+ / FastAPI
│  ├─ app/
│  │  ├─ main.py            # FastAPI 入口
│  │  ├─ config.py          # 配置读取（环境变量 + config/*.json）
│  │  ├─ database.py        # SQLAlchemy 引擎 / 会话 / 建表
│  │  ├─ models/            # 数据表模型（8 张表）
│  │  ├─ schemas/           # Pydantic 出入参模型
│  │  ├─ routers/           # auth / file / ai / order / admin
│  │  ├─ services/
│  │  │  ├─ auth/           # 登录抽象：base / device(V1) / wechat(V2 预留) / admin(阶段 4)
│  │  │  ├─ storage/        # 存储抽象：base / keys / minio_store
│  │  │  ├─ user_service.py # 取用户或首次自动建用户
│  │  │  ├─ file_service.py # 上传校验与入库
│  │  │  ├─ doc_parser.py   # 文档解析（5-A）
│  │  │  ├─ format_rules_engine.py / format_checker.py  # 规则引擎 + 大模型体检（5-B）
│  │  │  ├─ proofreader.py / print_planner.py            # 错别字检查、打印方案（5-C）
│  │  │  ├─ ocr.py / converter.py                        # 图片转文字、PDF 转 Word（5-D）
│  │  │  └─ llm.py / task_registry.py                    # 模型调用与任务进度
│  │  └─ utils/             # 统一响应、异常处理、JWT 鉴权、文件类型识别
│  ├─ config/
│  │  ├─ format_rules.json  # 格式校验规则（外置，禁止写死）
│  │  ├─ pricing.json       # 计费规则，单位：分/页
│  │  ├─ features.json      # 功能开关
│  │  ├─ upload_rules.json  # 上传大小上限与扩展名白名单
│  │  ├─ order_rules.json   # 收单时段、容量、等待时间参数、下单限制
│  │  └─ ai_rules.json      # AI 任务参数（分块大小、并发数、方案可选值）
│  ├─ docker/
│  │  └─ libreoffice/       # 备用方案：Word 转 PDF 的 headless 容器
│  ├─ scripts/
│  │  ├─ dev_mysql.ps1      # 免安装版 MySQL 8 管理脚本
│  │  ├─ dev_minio.ps1      # 免安装版 MinIO 管理脚本
│  │  └─ make_demo_docx.py  # 生成含格式错误的演示论文（答辩道具）
│  ├─ tests/                # pytest：存储抽象与上传接口测试
│  ├─ schema.sql            # 建表脚本（与模型等价）
│  ├─ requirements.txt
│  ├─ requirements-dev.txt  # pytest / ruff 等开发依赖
│  ├─ ruff.toml             # 静态检查配置
│  ├─ docker-compose.yml    # MySQL + MinIO + LibreOffice
│  └─ .env.example
└─ README.md
```

---

## 环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | 3.11+ | 本机开发环境为 `E:\python\python.exe`（3.14.7） |
| MySQL | 8.x | 本机使用免安装版，装在 `G:\deepseek\.toolchain\mysql8` |
| MinIO | 最新版 | 阶段 2 使用，可本机安装或用 docker-compose |
| LibreOffice | 7.x+ | 阶段 5-A 使用，可本机安装或用 docker-compose |
| Docker | 可选 | 装了就用 compose 起 MySQL + MinIO + LibreOffice |

---

## 快速开始（本机免安装 MySQL 路线）

以下命令在 `G:\deepseek\zhiyin-assistant` 目录下执行。

**1. 初始化并启动 MySQL（首次执行 init，之后只需 start）**

```powershell
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_mysql.ps1 init
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_mysql.ps1 start
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_mysql.ps1 create-db
```

免安装版 MySQL 位于 `G:\deepseek\.toolchain\mysql8`，数据目录为其中的 `data\`，
开发用 root 密码为 `zhiyin_dev_2026`（见 `backend/.env`，仅本机开发使用）。

**2. 创建虚拟环境并安装依赖**

```powershell
E:\python\python.exe -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt      # 运行依赖
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt  # 开发依赖（部署时不需要）
```

若 `venv` 创建时 `ensurepip` 报错、或 `pip` 命令在受限环境里不可用，改用基础解释器的 pip 直接装进 venv：

```powershell
E:\python\python.exe -m pip --python backend\.venv\Scripts\python.exe install -r backend\requirements.txt
```

注意：本机 Python 装在 E 盘、venv 在 G 盘，个别受限终端里 venv 可能加载不了 `_ssl`。
若出现该报错，请在普通（非受限）终端中启动服务。

**3. 准备环境变量**

`backend/.env` 已按本机开发环境填好；换机器时复制 `backend/.env.example` 再改。
真实密钥（`LLM_API_KEY` 等）只写在服务端 `.env`，绝不进入前端代码。

**4. 启动服务**

```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

启动时会按 `AUTO_CREATE_TABLES=true` 自动建表；也可手工建表：

```powershell
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_mysql.ps1 apply-schema
```

**5. 验证**

- 接口文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>
- 表结构（应看到 8 张表）：

```powershell
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_mysql.ps1 shell
# 进入后执行： SHOW TABLES;
```

**6. 启动 MinIO（阶段 2 起需要）**

```powershell
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_minio.ps1 start    # 启动
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_minio.ps1 bucket   # 创建私有桶 zhiyin-files
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_minio.ps1 status   # 查看状态
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_minio.ps1 stop     # 停止
```

MinIO 控制台：<http://127.0.0.1:9001>；数据目录 `G:\deepseek\.minio-data`（已加入 .gitignore）。

**7. 跑测试**

```powershell
cd backend
.venv\Scripts\python.exe -m pytest -q
```

测试会使用独立的 `zhiyin_test` 库与 `zhiyin-files-test` 桶，不污染开发数据。

**8. AI 连通性测试（阶段 5）**

先在 `backend/.env` 填好 `LLM_API_KEY`，然后：

```powershell
cd backend
.venv\Scripts\python.exe test_llm.py
```

脚本会依次验证普通对话、JSON 模式、思考模式三条调用链。

**9. LibreOffice（阶段 5-A 起需要）**

本机当前没有管理员权限，因此用 MSI 的「管理安装」把 LibreOffice 解包到
`G:\deepseek\.toolchain\libreoffice`，并在 `backend/.env` 配置：

```ini
SOFFICE_PATH=G:\deepseek\.toolchain\libreoffice\program\soffice.exe
SOFFICE_TIMEOUT_SECONDS=180
```

若换机器且能正常安装，路径通常是 `C:\Program Files\LibreOffice\program\soffice.exe`。

> ⚠️ 踩坑记录：本机用户目录含中文（`C:\Users\纳指GO\...`），当子进程的 `TMP`/`TEMP`
> 指向该中文路径时，LibreOffice 会报 `Error: source file could not be loaded` 而无法转换。
> `doc_parser.convert_to_pdf` 已显式把子进程的 `TMP`/`TEMP` 指向纯 ASCII 目录来规避；
> 另外不要用 `subprocess.PIPE` 捕获 soffice 输出——它会派生后台进程占住管道导致永久阻塞，
> 代码里改为重定向到日志文件。

---

## Docker 路线（装好 Docker 后可用）

```powershell
cd backend
docker compose up -d
```

会启动 MySQL 8（自动执行 `schema.sql`）、MinIO（<http://127.0.0.1:9001>）与
LibreOffice 转换容器。compose 自动读取 `backend/.env`。

---

## 已确立的工程约定（后续阶段必须遵守）

1. **统一响应**：所有接口返回 `{"code": 0, "message": "ok", "data": {...}}`；
   业务错误与参数校验错误返回 HTTP 200 + 非 0 `code`，未预期异常返回 HTTP 500 + 同一结构。
   路由内使用 `from app.utils.response import ok`，业务失败抛 `BusinessError`。
2. **金额**：一律「分」为单位的整数，禁止浮点数。
3. **订单号 / 取件码 / 状态 / 时间**：全部由服务端生成与推进，客户端只读。
4. **配置外置**：价格读取 `config/pricing.json`，格式规则读取 `config/format_rules.json`，
   功能开关读取 `config/features.json`，运行参数读环境变量，业务代码不写死数值。
5. **Provider 抽象**：支付、登录、通知、打印四个模块按「抽象接口 + 可切换实现」编写，
   业务代码只调用工厂函数，配置项为 `PAYMENT_PROVIDER` / `AUTH_PROVIDER` /
   `NOTIFIER_PROVIDER` / `PRINTER_PROVIDER`。
6. **AI 只给建议**：AI 输出结构化问题清单与修改指令，文档修改由代码执行，需用户勾选确认。
7. **鉴权**：除登录 `/api/auth/login`、健康检查 `/api/health`、`/docs` 外，所有接口都要带
   `Authorization: Bearer <token>`；token 由服务端签发，学生与管理员用 `role` 区分。
8. **上传校验**：大小与扩展名白名单读 `config/upload_rules.json`；文件真实类型按文件头
   （magic number）判定，与扩展名不符直接拒绝；对象 key 由服务端生成，不含原始文件名。
9. **截单不拒绝下单**：老师关闭当前时段、当前时段容量已满、或不在收单时段内时，
   学生仍可下单，订单排到下一个打印时段；首页与下单响应通过 `order_session.hint` /
   `session_notice.hint` 提示学生预计取件时间。已在队列中的订单不受任何影响。

## 数据表（阶段 1 已建）

`users`、`orders`、`payment_logs`、`queue_sessions`、`notices`、`print_logs`、
`ai_reports`、`notifications`，并已在 `users` / `orders` 中预留
`auth_provider`、`student_no_hash`、`site_id`、`pay_channel`、`external_pay_id`、`printer_id`。

## 已实现接口（阶段 1-3）

| 方法 | 路径 | 说明 | 需 token |
|---|---|---|---|
| GET | `/api/health` | 健康检查 | 否 |
| POST | `/api/auth/login` | 设备登录，返回 JWT | 否 |
| POST | `/api/file/upload` | 上传文档（白名单 + 文件头校验） | 是 |
| POST | `/api/order/create` | 下单，返回订单号与取件码 | 是 |
| GET | `/api/order/list` | 我的订单列表（可按状态筛选） | 是 |
| GET | `/api/order/{id}` | 订单详情（含排队位置与预计时间） | 是 |
| POST | `/api/order/{id}/pay` | 模拟支付，成功后转排队中 | 是 |
| POST | `/api/order/{id}/cancel` | 取消订单（仅待支付/排队中） | 是 |
| GET | `/api/home/summary` | 首页汇总（队列、待取件、是否可排单） | 是 |
| GET | `/api/notice/list` | 公告列表 | 是 |
| POST | `/api/ai/check` | AI 格式体检（规则引擎 + 大模型混合，只作建议） | 是 |
| POST | `/api/ai/proofread` | 错别字与标点检查（异步任务，返回 task_id） | 是 |
| GET | `/api/ai/proofread/{task_id}` | 轮询检查进度与结果（3-5 秒一次） | 是 |
| POST | `/api/ai/print-plan` | 打印方案（推荐参数 + 省纸省钱计算） | 是 |
| POST | `/api/ai/ocr` | 图片转文字（多模态模型，返回纯文本） | 是 |
| POST | `/api/ai/convert` | PDF 转 Word（pdf2docx，不调用大模型） | 是 |
| POST | `/api/ai/fix` | 修复文档（AI 出指令、代码执行，生成副本） | 是 |
| POST | `/api/admin/login` | 老师端登录（环境变量账号） | 否 |
| GET | `/api/admin/queue` | 接单队列（状态筛选 + 关键字搜索） | 管理员 |
| GET | `/api/admin/order/{id}/download` | 限时签名下载链接 | 管理员 |
| POST | `/api/admin/order/{id}/start` | 开始打印（queuing→printing） | 管理员 |
| POST | `/api/admin/order/{id}/complete` | 打印完成，可改价（printing→ready） | 管理员 |
| POST | `/api/admin/order/batch-complete` | 批量完成 | 管理员 |
| POST | `/api/admin/order/{id}/verify` | 核销取件（ready→completed） | 管理员 |
| POST | `/api/admin/order/verify-code` | 按取件码核销 | 管理员 |
| POST | `/api/admin/order/{id}/abnormal` | 标记异常 | 管理员 |
| POST | `/api/admin/order/{id}/recover` | 异常恢复排队 | 管理员 |
| POST | `/api/admin/order/{id}/cancel` | 关闭异常单 | 管理员 |
| POST | `/api/admin/session/close` / `open` | 关闭 / 恢复某时段收单 | 管理员 |
| GET | `/api/admin/summary` | 今日接单数、收款额、待取件、异常数 | 管理员 |
| POST | `/api/admin/notice` | 发布公告 | 管理员 |

## 阶段路线图

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 | 项目初始化（骨架 + 数据表 + 统一响应 + 配置 + compose + README） | 已完成 |
| 2 | 登录（AuthProvider）+ 文件上传（存储抽象 + MinIO） | 已完成 |
| 3 | 订单核心（订单号 / 取件码 / 状态机 / 模拟支付 / 排队算法） | 已完成 |
| 4 | 老师端后台接口（队列 / 完成 / 核销 / 收单开关 / 统计） | 已完成 |
| 5 | AI 模块（文档解析 / 格式校验 / 错别字 / 打印方案 / OCR / 转换） | 已完成（5-A~5-D） |
| 6 | 文档修复（AI 出指令、代码执行） | 已完成 |
| 7 | 学生端 uni-app | 待开始 |
| 8 | 老师端 Vue3 + Element Plus | 待开始 |
| 9 | 打包与交付（APK / 部署文档 / 安装说明） | 待开始 |
