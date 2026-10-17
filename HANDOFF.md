# 智印助手 · 项目交接文档（新手版）

> 目标读者：从未参与过本项目的同学。
> 按照本文从第 4 节开始操作，可以在一台全新 Windows 电脑上把项目跑起来。
> 项目状态、已知问题、待办以本文为准；`PROJECT_STATE.md` 是更详细的开发日志。

---

## 1. 项目是什么

**一句话**：面向高校文印室的 AI 论文格式体检与自助打印平台。

核心流程：

```
学生上传文档 → AI 体检（格式/参考文献/版式，可选）→ 生成修复建议（可选）
→ 选择打印方案 → 下单 → 模拟支付 → 老师接单打印 → 学生凭取件码取件并结清
```

三个端：

| 端 | 技术 | 入口 |
|---|---|---|
| 学生端 | uni-app（Vue3），可编译 Android APK / H5 / 微信小程序 | `student-app/` |
| 老师端 | Vue3 + Element Plus Web 后台 | `teacher-web/` |
| 后端 | Python FastAPI + MySQL + MinIO + DeepSeek | `backend/` |

---

## 2. 技术栈与架构

| 部分 | 技术 | 说明 |
|---|---|---|
| 后端框架 | Python 3.11+ / FastAPI | 同步 SQLAlchemy，接口统一返回 `{code,message,data}` |
| 数据库 | MySQL 8 + SQLAlchemy 2.x | 8 张表；启动时可自动建表 |
| 对象存储 | MinIO（本地私有桶） | 业务只依赖 `FileStorage` 抽象，可换云存储 |
| AI | DeepSeek（OpenAI 兼容 SDK，model=`deepseek-flash`） | 文本 + 多模态 OCR；统一封装在 `services/llm.py` |
| 文档处理 | LibreOffice headless、PyMuPDF、pdf2docx、python-docx | Word→PDF、PDF 解析、PDF→Word、Word 修复 |
| 学生端 | uni-app + Vue3 + Vite | H5 / Android / 微信小程序 |
| 老师端 | Vue3 + Element Plus + Vite | 浏览器后台 |
| 实时刷新 | 前端定时轮询（3-5 秒） | 不使用 WebSocket |

架构要点：

```
学生端/老师端 ──HTTP──> FastAPI ──> MySQL
                          ├──> MinIO（私有桶 + 限时签名 URL）
                          └──> DeepSeek（体检/校对/方案/OCR）
```

---

## 3. 目录结构

```
zhiyin-handoff/
├─ START_HERE.md              接手先看这里（三句话）
├─ HANDOFF.md                 本文件（12 个部分）
├─ PROJECT_STATE.md           进度、决策、已知问题、下一步
├─ ARCHITECTURE.md            目录/数据表/接口/文件职责
├─ README.md                  开发启动速查
├─ .env.example               环境变量模板（复制为 backend/.env）
├─ start_all.cmd              一键启动（环境装好后）
├─ database/
│  └─ schema.sql              完整建表 + 初始数据
├─ backend/
│  ├─ app/
│  │  ├─ main.py              FastAPI 入口
│  │  ├─ config.py            环境变量 + JSON 配置读取
│  │  ├─ database.py          MySQL 连接/会话/建表
│  │  ├─ models/              8 张表模型
│  │  ├─ routers/             接口路由
│  │  ├─ services/            业务逻辑（AI/订单/存储/Provider）
│  │  └─ utils/               统一响应、异常、JWT、文件类型
│  ├─ config/                 价格/格式/订单/AI/上传规则（JSON）
│  ├─ scripts/                MySQL/MinIO 管理、演示论文生成
│  ├─ tests/                  pytest（89 条）
│  ├─ schema.sql              后端用建表脚本（compose 挂载）
│  ├─ requirements.txt        运行依赖（已锁定版本）
│  ├─ requirements-dev.txt    开发依赖
│  └─ docker-compose.yml      MySQL + MinIO + LibreOffice
├─ student-app/               学生端 uni-app（CLI 项目）
│  ├─ src/pages/              首页/下单/我的/订单详情
│  ├─ src/services/           auth/filePicker/notify/api 适配层
│  ├─ src/manifest.json       App 名称/包名/图标/权限
│  ├─ .env.development        H5 调试接口地址
│  └─ .env.production         APK/真机接口地址
├─ teacher-web/               老师端 Vue3
│  ├─ src/views/              登录/工作台/流水/收单公告
│  ├─ src/api/                请求封装
│  └─ dev.cmd / build.cmd     双击启动/构建
└─ docs/
   ├─ PACKAGING.md            APK/小程序/H5 打包
   ├─ DEPLOYMENT.md           部署、安全、排障
   ├─ INSTALL.md              评委安装说明
   └─ ORIGINAL_SPEC.md        最初的需求规格（参考用）
```

---

## 4. 环境要求与安装（Windows 优先）

> **重要**：项目路径尽量使用纯英文，例如 `D:\zhiyin-handoff`。
> 中文路径会导致 LibreOffice / esbuild 出现难以排查的临时文件错误。

| 组件 | 版本 | 安装方式 | 验证命令 |
|---|---|---|---|
| Python | 3.11+（本机验证 3.14.7） | 官网安装包，勾选 Add to PATH：https://www.python.org/downloads/windows/ | `python --version` |
| Node.js | 18+（本机用 24） | 官网 LTS：https://nodejs.org/ | `node -v`、`npm.cmd -v` |
| MySQL | 8.0/8.x | 二选一：① 官方 ZIP 免安装；② 官方 Installer；ZIP 解压到 `backend\.tools\mysql8` | 目录下存在 `bin\mysqld.exe` |
| MinIO | 任意可用版本 | 官方已停发二进制，可用 GitHub 最后版本：`https://github.com/minio/minio/releases/download/RELEASE.2025-09-07T16-13-09Z/minio.windows-amd64.RELEASE.2025-09-07T16-13-09Z.exe`，下载后重命名为 `minio.exe` 放到 `backend\.tools\minio\minio.exe` | 运行 `minio.exe --version` |
| LibreOffice | 7.x+ | 官网安装：https://www.libreoffice.org/download/download-libreoffice/ ，记下 `soffice.exe` 路径 | 文件存在即可 |

> MinIO 官方下载页（dl.min.io）现在返回 410，上面的 GitHub 链接是最后带 Windows 二进制的版本。
> 如果下载失败，可以让原开发同学直接拷贝 `minio.exe`（约 108MB）。

> PowerShell 若提示"禁止运行脚本"，所有 npm 命令请用 `npm.cmd`，不要用 `npm.ps1`。

---

## 5. 从零启动（一步一步照做）

### 第 0 步：解压并进入项目

把交接包解压到例如 `D:\zhiyin-handoff`，在 PowerShell 中执行：

```powershell
cd D:\zhiyin-handoff
```

应该看到：提示符切换到该目录。

### 第 1 步：配置环境变量

```powershell
Copy-Item .env.example backend\.env
notepad backend\.env
```

在打开的记事本里，至少修改：

- `DB_PASSWORD`：你准备给 MySQL root 用的密码
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`：自己设的 MinIO 账号密码（至少 8 位）
- `ADMIN_PASSWORD`：老师端登录密码
- `JWT_SECRET`：随便一段 ≥32 位的随机字符串
- `LLM_API_KEY`：你的 DeepSeek Key（没有就先留空，体检/方案会不可用，但基础下单可用）
- `SOFFICE_PATH`：本机 LibreOffice 的 `soffice.exe` 路径

保存并关闭记事本。

### 第 2 步：创建 Python 虚拟环境并安装依赖

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

应该看到：最后一行 `Successfully installed ...`。

> 如果 `venv` 创建失败（ensurepip 报错），先跳过，改用：
> `python -m pip install --user -r backend\requirements.txt`，并在后续命令里用 `python` 替代 `backend\.venv\Scripts\python.exe`。

### 第 3 步：初始化并启动 MySQL

确保 MySQL 免安装包解压在 `backend\.tools\mysql8`（里面能看到 `bin\mysqld.exe`），然后：

```powershell
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_mysql.ps1 init
```

应该看到：`初始化完成。下一步执行 start...`

```powershell
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_mysql.ps1 start
```

应该看到：`MySQL 已启动，端口 3306`

```powershell
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_mysql.ps1 create-db
```

应该看到：`数据库已就绪：zhiyin`

> 如果你用 MySQL Installer 安装并已经运行了服务，可以跳过 init/start，只需在 `backend\.env` 填好 `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD`，
> 然后执行 `mysql -uroot -p < database\schema.sql` 手工建表。

### 第 4 步：启动 MinIO 并创建私有桶

```powershell
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_minio.ps1 start
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_minio.ps1 bucket
```

应该看到：`MinIO 已启动`、`私有桶已就绪：zhiyin-files`。

### 第 5 步：启动后端

```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

应该看到：

```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

浏览器打开 <http://127.0.0.1:8000/docs>，应该看到接口文档页面。

### 第 6 步：启动学生端 H5

另开一个 PowerShell：

```powershell
cd D:\zhiyin-handoff\student-app
npm.cmd install
npm.cmd run dev:h5
```

应该看到：`Local: http://localhost:5173/`，浏览器打开即可。

### 第 7 步：启动老师端

再开一个 PowerShell：

```powershell
cd D:\zhiyin-handoff\teacher-web
npm.cmd install
npm.cmd run dev
```

打开 <http://127.0.0.1:5174>，用 `backend\.env` 里的管理员账号登录。

### 第 8 步：验证

```powershell
cd D:\zhiyin-handoff\backend
.venv\Scripts\python.exe -m pytest -q
```

应该看到：`89 passed`（测试会使用独立的 `zhiyin_test` 库与测试桶）。

### 一键启动（完成上面安装后）

双击项目根目录的 `start_all.cmd`，它会尝试启动 MySQL、MinIO、后端、两个前端，并打开浏览器。
首次安装依赖仍需按上面步骤手动完成。

---

## 6. 当前进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 | 项目骨架、8 张表、统一响应、异常处理 | ✅ |
| 2 | 设备登录、上传（文件头校验、私有桶、签名 URL） | ✅ |
| 3 | 订单核心（订单号/取件码/状态机/模拟支付/排队/首页） | ✅ |
| 4 | 老师端接口（队列/打印/改价/核销/异常/收单/统计/公告） | ✅ |
| 5 | AI（解析/体检/校对/打印方案/OCR/PDF→Word） | ✅ |
| 6 | 文档修复（AI 指令 + python-docx 执行 + 副本） | ✅ |
| 7 | 学生端 uni-app（4 页面 + 适配层） | ✅ H5 已验证；真机待验证 |
| 8 | 老师端 Vue3 + Element Plus | ✅ |
| 9 | 打包与交付 | 🟡 文档/配置完成；APK 待 HBuilderX 云打包 |

**半成品/未做**：

- APK 尚未实际生成（需要 DCloud 账号云打包）。
- App 端 `plus.io.chooseFile` 未真机验证；已提供「载入示例论文」兜底。
- 真实支付、微信登录、推送、云打印、多文印点均为 V2，仅有抽象接口与占位文件。
- 文件 24 小时清理、AI 报告 7 天清理尚未接定时任务。

---

## 7. 已知问题清单（诚实版）

1. **APK 未生成**：`student-app/src/manifest.json` 的 appid 还是占位值，需要换成真实 DCloud appid。
2. **App 选择文档未真机验证**：`plus.io.chooseFile` 在部分 Android 版本可能不可用；失败时会提示，可以改用「载入示例论文」。
3. **MinIO 二进制来源**：官方已停发，换机可能下载不到；需要保留现有 exe 或使用代理下载 GitHub release。
4. **LibreOffice 路径依赖**：必须正确设置 `SOFFICE_PATH`；路径含中文时可能转换失败（代码已规避 TMP 中文问题，但安装路径本身也建议英文）。
5. **页边距推断误差**：通过 PDF 文本块坐标推算，标题段前距会使上边距偏大；已放宽上边距容差并标注为"通行参考值"。
6. **PPT/Excel 不套用论文规则**：只做版式检查，不检查摘要/参考文献（这是刻意设计，不是 bug）。
7. **扫描版 PDF 转 Word 效果差**：pdf2docx 需要文本层；扫描件应先 OCR。
8. **AI 任务进度存内存**：后端重启会丢失进度；V1 单机可接受，V2 换 Redis。
9. **AI 有成本**：体检/校对/方案会消耗 DeepSeek 额度；有缓存，`force` 才重算。
10. **老师端改价补差**：学生已支付但改价上调时会标记需补款，核销只收差额；这是当前业务规则，测试时注意。
11. **未做并发压测**：低并发校园演示可用，未做多实例部署验证。
12. **管理员单账号**：账号密码来自环境变量，没有 RBAC/多管理员。
13. **前端构建警告**：老师端 Element Plus 全量引入，包体约 1MB；不影响演示。
14. **隐私清理任务缺失**：上传文件不会自动删除（规则要求 24 小时后删除）。

---

## 8. 待办事项（按优先级）

| 优先级 | 任务 | 说明 |
|---|---|---|
| P0 | 云打包 APK 并真机安装 | 按 `docs/PACKAGING.md`；替换 appid，选择图标与证书 |
| P0 | 真机验证选文档 | 验证 `plus.io.chooseFile`；不可用则确认兜底按钮 |
| P1 | 上传/报告定时清理 | 订单完成 N 小时删文件；报告 7 天清理 |
| P1 | 后端容器内 LibreOffice | 服务器部署时让后端容器内调用 soffice（见 DEPLOYMENT 2.4） |
| P1 | 部署到校园服务器 | HTTPS、域名、防火墙、备份 |
| P2 | 老师端登录账号管理 | 多管理员/RBAC（V2） |
| P2 | 真实支付/微信登录/推送 | 仅需新增 Provider 实现 + 改配置 |
| P2 | 并发与稳定性测试 | 队列、文件转换、AI 限流 |

---

## 9. 接口清单

统一响应：`{"code":0,"message":"ok","data":{...}}`。
除登录/健康检查/docs 外都需要 `Authorization: Bearer <token>`。

### 公共与学生端

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 健康检查（免 token） |
| POST | `/api/auth/login` | 设备登录（免 token） |
| POST | `/api/auth/profile` | 更新昵称 |
| POST | `/api/file/upload` | 上传（≤50MB，白名单，文件头校验） |
| POST | `/api/file/demo` | 载入服务端示例论文 |
| POST | `/api/file/page-count` | 只取页数（不调 AI） |
| POST | `/api/ai/check` | AI 体检（规则+大模型） |
| POST | `/api/ai/proofread` | 错别字检查（返回 task_id） |
| GET | `/api/ai/proofread/{task_id}` | 轮询校对进度 |
| POST | `/api/ai/print-plan` | 打印方案（可 `use_ai=false`） |
| POST | `/api/ai/ocr` | 图片转文字 |
| POST | `/api/ai/convert` | PDF 转 Word |
| POST | `/api/ai/fix` | 按勾选问题生成修复副本 |
| POST | `/api/order/create` | 创建订单 |
| GET | `/api/order/list` | 我的订单 |
| GET | `/api/order/{id}` | 订单详情 |
| POST | `/api/order/{id}/pay` | 模拟支付 |
| POST | `/api/order/{id}/cancel` | 取消订单 |
| GET | `/api/home/summary` | 首页汇总 |
| GET | `/api/notice/list` | 公告 |

### 老师端

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/admin/login` | 管理员登录（免 token） |
| GET | `/api/admin/queue` | 队列（状态/关键字） |
| GET | `/api/admin/order/{id}/download` | 签名下载链接 |
| GET | `/api/admin/order/{id}/quote` | 系统报价 |
| POST | `/api/admin/order/{id}/start` | 开始打印 |
| POST | `/api/admin/order/{id}/complete` | 打印完成（可改价） |
| POST | `/api/admin/order/batch-complete` | 批量完成 |
| POST | `/api/admin/order/{id}/verify` | 核销 |
| POST | `/api/admin/order/verify-code` | 按取件码核销 |
| POST | `/api/admin/order/{id}/abnormal` | 标记异常 |
| POST | `/api/admin/order/{id}/recover` | 异常恢复 |
| POST | `/api/admin/order/{id}/cancel` | 关闭异常单 |
| POST | `/api/admin/session/close` / `open` | 关闭/恢复收单 |
| GET | `/api/admin/sessions` | 某天时段状态 |
| GET | `/api/admin/summary` | 今日统计 |
| POST | `/api/admin/notice` | 发布公告 |
| GET | `/api/admin/transactions` | 按日期收款流水 |

---

## 10. 关键设计决策（不要随手推翻）

1. **金额一律用「分」的整数**：避免浮点误差。前端展示时才除以 100。
2. **AI 只给建议，不自动改文档**：修复必须用户勾选，且由代码执行、生成副本。
3. **订单号/取件码/状态/时间全部服务端生成**：客户端无权指定。
4. **订单状态机在服务端校验**，每次变更写 `print_logs` 与通知事件。
5. **文件私有存储 + 30 分钟签名 URL**；对象 key 由服务端生成，不含原文件名。
6. **支付/登录/通知/打印四个 Provider 抽象**：V2 新增实现只改配置。
7. **AI 全程可选**：着急打印的学生可以直接用基础方案下单，避免被 AI 卡住。
8. **按文档类型选体检口径**：论文查结构/参考文献；PPT/Excel 只查版式。
9. **规则引擎 + 大模型混合**：确定性属性用规则，语义问题用模型，结果合并去重。
10. **长文档分块 + 并发 3 路 + 轮询进度**：不使用 WebSocket。
11. **老师改价上调后补差**：核销只收"最终金额 − 已支付流水"。
12. **可配置优先**：价格/规则/开关都在 `backend/config/*.json` 与 `.env`。

---

## 11. 常见修改指引

| 想改什么 | 改哪里 |
|---|---|
| 打印价格 | `backend/config/pricing.json`（单位：分/页） |
| 论文格式规则（页边距/字体/字号/结构/参考文献） | `backend/config/format_rules.json` |
| 上传大小上限/白名单 | `backend/config/upload_rules.json` |
| 收单时段/容量/等待时间/取件码 | `backend/config/order_rules.json` |
| AI 分块大小/并发/打印方案选项 | `backend/config/ai_rules.json` |
| 功能开关（V2 预留） | `backend/config/features.json` |
| 学生端界面文案 | `student-app/src/pages/**/*.vue` |
| 老师端界面文案/表格列 | `teacher-web/src/views/**/*.vue` |
| 学生端接口地址 | `student-app/.env.development` / `.env.production` |
| 老师端接口地址 | `teacher-web/.env.development` |
| 管理员账号密码 | `backend/.env` 的 `ADMIN_USERNAME` / `ADMIN_PASSWORD` |
| App 名称/包名/图标/权限 | `student-app/src/manifest.json` |
| 数据库表结构 | `backend/app/models/` + `database/schema.sql` |

改完价格/规则不需要改代码，重启后端即可生效（有缓存，必要时 `force`）。

---

## 12. 故障排查

### 12.1 启动失败

| 现象 | 排查 |
|---|---|
| `No module named fastapi` | 没装依赖：`.venv\Scripts\python.exe -m pip install -r backend\requirements.txt` |
| `No module named app` | 必须在 `backend` 目录下启动 uvicorn |
| 端口 8000 被占用 | 关掉旧后端或用 `--port 8001`（同时改前端 API 地址） |
| `npm` 不能运行 | 用 `npm.cmd`，不要用 `npm.ps1`（PowerShell 执行策略） |
| `esbuild Access is denied` | 用 `teacher-web\dev.cmd` / `build.cmd`，或把 TMP/TEMP 指到英文目录 |

### 12.2 连不上数据库

1. 确认 MySQL 在运行：`powershell -File backend\scripts\dev_mysql.ps1 status`
2. 核对 `backend/.env` 的 `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME`
3. 手工连接：`backend\.tools\mysql8\bin\mysql.exe -h127.0.0.1 -uroot -p`
4. 手工建表：`mysql -uroot -p < database\schema.sql`

### 12.3 AI 调用失败

1. 检查 `backend/.env` 是否有 `LLM_API_KEY`，且没有多余空格/引号。
2. 运行 `cd backend && .venv\Scripts\python.exe test_llm.py`，看哪一步失败。
3. 确认能访问 `https://api.deepseek.com`（公司/校园网可能拦截）。
4. 体检接口即使 AI 失败也会返回规则引擎结果，并带 `ai_errors`；先看这个字段。

### 12.4 文档转换失败

1. 检查 `SOFFICE_PATH` 指向真实存在的 `soffice.exe`。
2. 手动测试：`"C:\Program Files\LibreOffice\program\soffice.exe" --headless --convert-to pdf --outdir . 测试.docx`
3. 项目路径与用户目录尽量用英文；中文 TEMP 会导致 `source file could not be loaded`。
4. 加密文档先解除密码。

### 12.5 手机连不上后端

1. 后端必须监听 `0.0.0.0`，不能用 127.0.0.1。
2. `student-app/.env.production` 改成电脑局域网 IP，重新构建。
3. 防火墙放行 8000（管理员 PowerShell）：
   `netsh advfirewall firewall add rule name="ZhiYin API 8000" dir=in action=allow protocol=TCP localport=8000`
4. 手机浏览器访问 `http://电脑IP:8000/api/health` 验证。

---

## 附：最小验收清单（接手后 30 分钟）

1. `/api/health` 返回 code=0
2. 学生端首页能打开、显示排队数与公告
3. 「载入示例论文」→ 出现基础打印方案
4. 点「开始 AI 体检」→ 出现问题清单（需 LLM Key）
5. 下单 → 取件码出现
6. 老师端登录 → 队列可见该订单
7. 老师完成打印（可改价）→ 学生端状态变待取件
8. 老师核销 → 状态变已完成
9. `pytest -q` → 89 passed
