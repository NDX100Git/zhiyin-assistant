# 智印助手 · 老师端（Vue3 + Element Plus）

## 运行

```powershell
cd teacher-web
npm.cmd install
npm.cmd run dev
```

打开 <http://127.0.0.1:5174>。

管理员账号在 `backend/.env` 的 `ADMIN_USERNAME` / `ADMIN_PASSWORD`。

接口地址通过 `.env.development` 的 `VITE_API_BASE_URL` 配置，默认 `http://127.0.0.1:8000`。

## 页面

- `/login`：老师登录
- `/dashboard`：今日数据、接单队列、开始打印、完成改价、核销（含按取件码）、批量完成、异常处理
- `/transactions`：按日期查看收款流水
- `/settings`：上午/下午收单开关、发布公告

## 说明

- 队列每 5 秒自动刷新，页面隐藏时不刷新。
- 金额在界面上按"元"展示，提交给后端时转换为整数"分"。
- 完成打印时金额由系统按 `pricing.json` 预填，老师可修改，改价会写入操作日志。
