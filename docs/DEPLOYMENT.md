# 部署说明（阶段 9）

本文覆盖三种场景：本机开发（当前状态）、Linux 服务器容器化部署、常见故障排查。

---

## 1. 本机开发（Windows，当前使用）

| 组件 | 位置 |
|---|---|
| MySQL 8.0.29 | `backend/.tools/mysql8`（或环境变量 `ZHIYIN_MYSQL_ROOT`） |
| MinIO | `backend/.tools/minio/minio.exe`（或 `ZHIYIN_MINIO_EXE`），数据 `backend/.data/minio` |
| LibreOffice | 由 `SOFFICE_PATH` 配置（本机可为解压目录） |
| 后端 venv | `backend/.venv` |

启动：

```powershell
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_mysql.ps1 start
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_minio.ps1 start
powershell -ExecutionPolicy Bypass -File backend\scripts\dev_minio.ps1 bucket

cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

老师端：`cd teacher-web && npm.cmd run dev`（或双击 `dev.cmd`）→ <http://127.0.0.1:5174>。

学生端 H5：`cd student-app && npm.cmd run dev:h5` → <http://127.0.0.1:5173>。

---

## 2. Linux 服务器（Docker Compose）

`backend/docker-compose.yml` 已包含 MySQL 8、MinIO、LibreOffice 转换容器。

### 2.1 准备

```bash
git clone <仓库地址> zhiyin-assistant
cd zhiyin-assistant/backend
cp .env.example .env
```

编辑 `.env`，至少修改：

| 变量 | 说明 |
|---|---|
| `DB_PASSWORD` | 强密码（compose 与后端共用） |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | 强密码 |
| `JWT_SECRET` | ≥32 字节随机字符串 |
| `ADMIN_PASSWORD` | 老师端登录密码 |
| `LLM_API_KEY` | DeepSeek Key，仅服务端 |
| `SOFFICE_PATH` | 容器部署时后端不直接调用本机 soffice，转换走 `libreoffice` 容器（见 2.4） |
| `FILE_EXPIRE_HOURS` / `SIGNED_URL_EXPIRE_MINUTES` | 按需 |

### 2.2 启动

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8000/api/health
```

MySQL 首次启动会执行 `schema.sql` 建库建表；后端启动时也会 `create_all`（幂等）。
MinIO 私有桶在后端首次上传时自动创建。

### 2.3 构建前端并托管

```bash
cd student-app
npm ci
npm run build:h5
# 将 dist/build/h5 交给 Nginx

cd ../teacher-web
npm ci
npm run build
# 将 dist 交给 Nginx
```

> 前端 API 地址在构建时写入（`.env.production` / `.env.development`），改地址后需重新构建。

两端的生产包**默认都不带域名**：`student-app/.env.production` 与 `teacher-web/.env.production`
里的 `VITE_API_BASE_URL` 都留空，意思是“同源相对路径”——页面请求 `/api/...`，由下面的 Nginx
把 `/api/` 反代到后端。这样换域名、换机器都不用重新出包。

代码里没有任何回环地址兜底（以前老师端兜底写死 `http://127.0.0.1:8000`，生产包真带着它，
上线就打到柜台机自己）。两端构建产物里都不许出现 `127.0.0.1` / `localhost:端口` / 内网网段，
回归里的 `e2e/run.mjs` 会各打一次生产包扫一遍；老师端 `npm run build` 时也会自检
`VITE_API_BASE_URL`，发现本机或内网地址会告警。

只有当老师端与学生端**不同源**（比如前端挂 CDN）时才需要桥地址写进 `teacher-web/.env.production`：

```ini
VITE_API_BASE_URL=https://your-domain.com
```

### 2.4 LibreOffice 转换

当前 `doc_parser.convert_to_pdf` 通过 `SOFFICE_PATH` 调用本机可执行文件。
容器部署有两种做法：

1. 后端也放进容器，安装 LibreOffice，`SOFFICE_PATH=/usr/bin/soffice`；
2. 后端在宿主机，挂载共享目录，调用 `docker exec zhiyin-libreoffice soffice ...`
   （需要在 `convert_to_pdf` 增加一种“容器调用”实现，属 V1.1 小改动）。

演示阶段建议用方案 1（后端镜像内置 LibreOffice），最省心。

### 2.5 Nginx 反代示例

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        client_max_body_size 60m;
    }

    location /teacher/ {
        root /var/www/zhiyin;
        try_files $uri $uri/ /teacher/index.html;
    }

    location / {
        root /var/www/zhiyin/h5;
        try_files $uri $uri/ /index.html;
    }
}
```

生产环境请配置 HTTPS（证书可由学校提供）。

---

## 3. 安全清单（上线前逐条确认）

1. `backend/.env` 不在版本库中，权限仅服务账号可读。
2. `JWT_SECRET` 已换成随机值（≥32 字节），`ADMIN_PASSWORD` 已改。
3. `LLM_API_KEY` 只存在服务端，前端构建产物中搜不到 `sk-`。
4. MinIO 桶保持私有，只通过 30 分钟签名 URL 访问。
5. 防火墙只开放必要端口（80/443；8000/9000 不对公网开放）。
6. 不采集学号、年级、身份证；用户表只存设备标识/昵称。
7. 文件过期清理任务（订单完成 N 小时后删原文件）与 AI 报告 7 天清理任务尚未实现，交付前需补定时任务。
8. 定期备份 MySQL 与 MinIO 数据：

```bash
mysqldump -uroot -p zhiyin > backup_$(date +%F).sql
tar czf minio_$(date +%F).tar.gz /path/to/minio-data
```

---

## 4. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 前端上传报 `file 字段要求` | 上传请求被设置了 `Content-Type: application/json` | 已在 `student-app/src/services/api.js` 修复；不要给 uploadFile 手动设 Content-Type |
| LibreOffice 报 `source file could not be loaded` | TEMP/TMP 指向含中文路径 | 代码已用 ASCII 临时目录；检查 `SOFFICE_PATH` 是否有效 |
| 老师端 `npm run dev` 报 esbuild `Access is denied` | 同上（中文 TEMP） | 用 `teacher-web/dev.cmd` 或手动把 TMP/TEMP 指到英文目录 |
| 手机打不开 API | 用了 127.0.0.1 或防火墙未放行 | 改局域网 IP + 管理员 PowerShell 放行 8000 |
| Docker 中 MySQL 起不来 | 端口 3306 被本机 MySQL 占用 | 改 compose/docker 端口映射，或停掉本机 MySQL |
| 文件上传成功但转换失败 | 文档损坏或加密 | 用 Word 打开另存后重试；扫描件先 OCR |
