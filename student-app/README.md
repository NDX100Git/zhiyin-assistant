# 智印助手 · 学生端（uni-app / Vue3）

## 运行

```powershell
cd student-app
npm.cmd install
npm.cmd run dev:h5        # 浏览器预览
npm.cmd run build:h5      # H5 产物：dist/build/h5
npm.cmd run build:app     # App 资源，供 HBuilderX 云打包
```

> PowerShell 执行策略可能禁止 `npm.ps1`，本项目统一用 `npm.cmd` / `npx.cmd`。

## API 地址（真机调试必看）

API 地址通过 `.env.development` / `.env.production` 的 `VITE_API_BASE_URL` 注入，
代码里没有硬编码。**真机不能填 127.0.0.1**（那指向手机自己），要填电脑的局域网 IP：

```ini
VITE_API_BASE_URL=http://127.0.0.1:8000      # H5 在电脑浏览器预览
VITE_API_BASE_URL=http://192.168.1.100:8000  # 真机调试：改成电脑局域网 IP（ipconfig 查看）
```

换网络后记得同步修改，并让后端监听 `0.0.0.0`：

```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Windows 防火墙放行 8000（需要管理员 PowerShell，本项目开发机当前不是管理员）：

```powershell
netsh advfirewall firewall add rule name="ZhiYin API 8000" dir=in action=allow protocol=TCP localport=8000
```

手机与电脑需在同一局域网，手机浏览器先访问 `http://电脑IP:8000/api/health` 确认可达。

## 选文件的三个平台分支（重点）

页面里**不直接调用** `uni.chooseFile` / `uni.chooseImage`，统一走 `src/services/filePicker.js`：

| 场景 | 实现 |
|---|---|
| H5 / 小程序 | `uni.chooseFile`（支持 pdf/doc/docx/ppt/pptx/xls/xlsx） |
| App | 优先 `plus.io.chooseFile`（5+ API），先做能力检测，不可用时给出明确提示 |
| 图片 / 拍照 | `uni.chooseImage`（全端支持） |
| 演示兜底 | 「载入示例论文」调用后端 `POST /api/file/demo`，服务端直接返回已上传的示例文档 |

`plus.io.chooseFile` 属于设备侧 API，只能在真机/模拟器里验证；当前开发环境无法运行 Android，
因此仍以「示例论文」作为答辩兜底路径，保证上传→体检链路不会因选文件失败而卡住。

## 适配层

- `src/services/auth.js`：设备标识登录、token 存取；页面不直接调用 `uni.login`
- `src/services/filePicker.js`：选文档 / 拍照 / 示例论文，全端条件编译
- `src/services/notify.js`：Toast 通知 + 轮询器（`start` / `stop` / `refresh`）
- `src/services/api.js`：统一请求与上传封装，自动带 token、统一解析 `{code,message,data}`

## 轮询与页面生命周期

首页、我的订单、订单详情都用 `createPoller` 在 `onShow` 启动、`onHide` 停止，
`onUnmounted` 再兜底清理，避免页面隐藏后继续空跑。

## 用 HBuilderX 打开与云打包

1. HBuilderX → 文件 → 导入 → 从本地目录导入，选择 `student-app`。
2. 打开 `src/manifest.json`，把 `appid` 换成 DCloud 申请的真实 uni-app 应用 appid。
3. 运行 → 运行到手机或模拟器（需开启 USB 调试）。
4. 发行 → 原生 App-云打包 → 选择 Android，使用自己的签名证书。

## 目录

```
src/
  pages/index/index.vue      首页（排队、可排单状态、公告）
  pages/order/order.vue      AI 下单（选文件 → 体检 → 修复 → 打印方案 → 下单支付）
  pages/mine/mine.vue        我的订单（状态筛选、昵称）
  pages/mine/detail.vue      订单详情（取件码、排队进度、取消/支付）
  services/                  auth / filePicker / notify / api 适配层
  config/index.js            API 地址与轮询间隔
```
