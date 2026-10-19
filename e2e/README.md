# 浏览器端到端回归

一条命令跑学生端与老师端的完整闭环，两个视口各跑一遍：

- 学生：登录 → 上传 PDF → 跳过 AI 直接下单 → 模拟支付 → 我的订单 → 订单详情 → 换文件（待老师确认）
- 老师：登录 → 队列看到待确认 → 确认换文件 → 下载并确认看到 → 开始打印 → 打印完成 → 核销
- 学生：订单详情看到已完成

视口固定为 **390×844**（手机，双倍像素密度）与 **1440×900**（桌面）。390 下额外检查老师端队列与确认弹窗无横向溢出、按钮不小于 44 像素。

## 跑法

```bash
# 首次运行：三处依赖都要装
cd HumanProject/zhiyin-assistant/student-app && npm install
cd ../teacher-web && npm install
cd ../e2e && npm install

node run.mjs --start
```

`--start` 会做四件事：

1. 重建独立库 `zhiyin_e2e`（库名不以 `_e2e` 结尾会拒绝），对象存储桶用 `zhiyin-files-e2e`；
2. 起后端（默认 `http://127.0.0.1:8100`）；
3. 起学生端 H5（默认 `http://127.0.0.1:3100`）与老师端（默认 `http://127.0.0.1:3101`）；
4. 跑完自动停掉三个服务。

也可以对着已经起好的开发环境跑：

```bash
node run.mjs
```

默认连 `5173`（学生端）、`5174`（老师端）、`8000`（后端）；用 `--student-url`、`--teacher-url`、`--backend-url` 覆盖。

老师账号默认从 `backend/.env` 的 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 读取，也可以用 `E2E_ADMIN_USERNAME` / `E2E_ADMIN_PASSWORD` 覆盖。Python 解释器按 `E2E_PYTHON` → `Temp/zhiyin-venv` → `backend/.venv` → `python3` 的顺序探测。

调试时可以加 `E2E_TRACE=1`，会额外打印老师端队列按钮的匹配结果与 `/api/admin/*` 请求。

## 失败怎么读

每一步都会打印序号、名称与耗时。失败时终端给出：

```
失败：视口 390 卡在第 15 步「老师下载新文件并确认看到」
原因：...
现场：e2e/artifacts/<时间>/
```

现场目录里有失败页截图、页面正文、三个服务的日志与 `result.json`。接口 4xx/5xx 与页面未捕获异常也会在最后的「页面错误汇总」里点名。

## 常见问题

- 启动报找不到 Chrome：执行 `npx puppeteer browsers install chrome`。
- 端口被占用：`--start` 固定用 3100、3101、8100；先停掉占用这些端口的进程，或用 `--student-url` / `--teacher-url` / `--backend-url` 对着已有服务跑。
- 老师端账号读不到：确认 `backend/.env` 里有 `ADMIN_PASSWORD`，或显式给 `E2E_ADMIN_USERNAME` / `E2E_ADMIN_PASSWORD`。

## 覆盖边界

- 只跑浏览器版 H5；微信小程序与安卓真机不在本脚本内。
- 文件替换走真实的老师确认接口，不是 mock；因此后端契约不一致会在这里直接暴露。
- 依赖 LibreOffice 的 docx 转换不在链路里，夹具是两份 PDF（1 页与 2 页），避免把转换服务的状态混进前端回归。
- 上传文件由脚本直接塞给 uni H5 动态创建的 `input[type=file]`：无头浏览器里脚本触发的原生选择框会被判为用户取消，这条替代路径仍然走页面真实的 `/api/file/upload` 与页数读取。
