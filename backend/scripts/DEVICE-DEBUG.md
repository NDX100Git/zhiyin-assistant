# 微信小程序真机调试步骤（单机演示用）

适用工作树：`/home/twilightrain/WorkZone/Temp/zhiyin-r4-c`
端口口径：后端 **8208**、学生端 H5 **3207**、老师端 **3208**。
前提：手机与这台电脑连同一个局域网；`bash backend/scripts/dev_up.sh` 已经把后端、学生端、老师端起好。

正式备案与正式身份码这次不做：演示用**测试身份码 + 开发者工具真机调试**，真机不通时用录屏兜底（见第 8 步）。

---

## 1. 怎么拿到测试身份码

测试身份码由业务方（项目负责人 / 小程序管理员）提供，来源二选一：

- 微信公众平台的小程序**测试号** AppID（登录 `mp.weixin.qq.com` 后在测试号页面可查）；
- 已注册小程序的 AppID（用开发版 / 体验版调试）。

**身份码不进版本库**：`student-app/src/manifest.json` 里 `mp-weixin.appid` 现在是空字符串，
身份码在构建时由构建参数注入（见下一步）。拿到之后只在本机配一处，不要提交。
空着构建不会报错，但产物是**游客身份包**，不能真机预览、不能上传代码，控制台会点名告警。

## 2. 填在哪一处

- 文件：`student-app/src/manifest.json` 里 `mp-weixin.appid` **保持空字符串，谁也不要改它**。
  以前这里硬编码过一个测试身份码，等于谁拉代码谁拿到了业务方的小程序身份，已经清掉。
- 身份码改成构建时注入，二选一：

```bash
# 方式一：命令行给构建参数（推荐，一次一用，不落盘）
cd student-app
UNI_MP_WEIXIN_APPID=<测试身份码> npm run dev:mp-weixin
UNI_MP_WEIXIN_APPID=<测试身份码> npm run build:mp-weixin
```

```ini
# 方式二：本机未入库配置（长期用某台机器调试时最省事）
# 文件：student-app/.env.local（已被 .gitignore 忽略，不会提交）
VITE_MP_WEIXIN_APPID=<测试身份码>
```

- 注入效果：小程序的 `project.config.json` 里 `appid` 变成注入的值；
  缺省（两处都没配）时是 `touristappid`，构建日志里会有「appid 为空，产物会以游客身份生成」一条告警。
- 自查：`cd student-app && npm run build:mp-weixin`，再打开 `dist/build/mp-weixin/project.config.json`
  看 `appid` 是不是你注入的那个值（不是 `touristappid` 就对了）。

## 3. 后端怎么允许局域网访问

- `backend/scripts/dev_up.sh` 起后端时已经是 `uvicorn --host 0.0.0.0 --port 8208`（监听所有网卡），不用改代码、不用改配置。
- 查这台机器的局域网地址：`hostname -I | awk '{print $1}'`（本次实测 `10.127.73.93`，换环境以你查到的为准，下文用 `<局域网IP>` 代替）。
- 防火墙放行 8208（按发行版选一条，本机没开防火墙就跳过）：

```bash
sudo ufw allow 8208/tcp                                    # Ubuntu / Debian
sudo firewall-cmd --add-port=8208/tcp --permanent && sudo firewall-cmd --reload   # firewalld
```

- 先用**另一台设备**的浏览器验证：打开 `http://<局域网IP>:8208/api/health`，返回 `{"code":0,...,"status":"up"}` 才算通。
- 文件相关还要 9000 通（上传与老师端下载走 MinIO）：同法打开 `http://<局域网IP>:9000`，出现 AccessDenied 说明端口通了。
- 注意：老师端下载的签名链接按 `backend/.env` 的 `MINIO_ENDPOINT` 生成（当前 `127.0.0.1:9000`），在手机上会指向手机自己。演示时下载放在电脑上做；非要在手机上下载，把 `MINIO_ENDPOINT` 改成 `<局域网IP>:9000` 再重启后端。

## 4. 接口地址怎么指到这台机器

`student-app/.env.development` 默认 `VITE_API_BASE_URL=http://127.0.0.1:8208`。真机不能用 127.0.0.1（那是手机自己），用环境变量覆盖，不改文件：

```bash
cd student-app
VITE_API_BASE_URL=http://<局域网IP>:8208 npm run dev:mp-weixin
```

产物在 `student-app/dist/dev/mp-weixin`，watch 模式，改代码会自动重编；改了 `.env` 或 manifest 要重跑这条命令。

## 5. 开发者工具里导入与勾项

1. 打开微信开发者工具 → 导入项目。
2. 目录选 `student-app/dist/dev/mp-weixin`（**不是** `src`）。
3. AppID 填第 2 步注入的那个测试身份码（与 `project.config.json` 里的一致）。
4. 右上角「详情」→「本地设置」→ 勾上 **「不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书」**。
5. 工具栏点「预览」生成二维码，手机微信扫码（手机与电脑同一 Wi-Fi）。

## 6. 手机上看到什么算成功

按顺序看到这几步就算通了：

1. 首屏「智印助手」正常渲染，不是白屏、不是 404；
2. 点上传能选到 PDF（选不到就用页面里的示例论文兜底）；
3. 上传后能看到页数、逐份打印参数与金额；
4. 下单并支付后订单显示「排队中」，并且给出排队位置数字；
5. 老师端（电脑上 `http://127.0.0.1:3208`）开始打印 / 打印完成 / 核销后，手机下拉刷新能看到「打印中」→「待取件」→「已完成」。

同一条链路在电脑浏览器 H5（`http://127.0.0.1:3207`）上应先走通：H5 通、手机不通，基本都是第 4 / 第 5 步的网络与域名校验问题。

## 7. 常见失败怎么读

| 手机上/控制台看到的 | 原因 | 怎么改 |
| --- | --- | --- |
| `request:fail url not in domain list` / 「不在以下 request 合法域名列表中」 | 开发者工具没勾「不校验合法域名」 | 回第 5 步第 4 条勾上，重新点「预览」 |
| `request:fail timeout` / 界面「网络异常」 | 接口地址写成 127.0.0.1、手机与电脑不同网段、防火墙没放行 8208 | 用第 4 步的命令传 `<局域网IP>`；手机与电脑连同一个 Wi-Fi；按第 3 步放行端口 |
| 上传报 `uploadFile:fail` | 同上，或文件超限 | 先按第 3 步验证 9000 端口；再看 `backend/config/upload_rules.json` 的大小上限 |
| 老师端/手机打开下载链接 403 或一直转圈 | `MINIO_ENDPOINT` 是 127.0.0.1，签名链接指回手机自己 | 见第 3 步最后一条：改成 `<局域网IP>:9000` 并重启后端 |
| 开发者工具提示游客身份 / 无法预览 | 构建时没注入身份码，`project.config.json` 的 `appid` 是 `touristappid` | 回第 2 步：命令里带 `UNI_MP_WEIXIN_APPID=<测试身份码>`，或在 `.env.local` 写 `VITE_MP_WEIXIN_APPID`，重新构建 |
| 扫码后一直转圈打不开 | 手机没连 Wi-Fi、或扫码的微信号没有该小程序权限 | 换同一个局域网，改用管理员/开发者微信号扫码 |
| 页面白屏 | 编译报错或改完没编完 | 看 `npm run dev:mp-weixin` 控制台报错；等编译结束再点「预览」 |

## 8. 真机不通时的录屏兜底

不要在现场折腾真机：

1. 电脑浏览器打开 `http://127.0.0.1:3207`（学生端 H5）与 `http://127.0.0.1:3208`（老师端），按第 6 步的顺序演一遍并录屏；
2. 同时开一个终端跑 `python scripts/demo_check.py`（在 `backend` 目录），12 步全过即是链路自证；
3. 录屏 + 自检输出一起交差。
