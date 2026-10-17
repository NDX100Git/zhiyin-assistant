# 打包说明（阶段 9）

本项目学生端是 CLI 版 uni-app 项目（`student-app/`），HBuilderX 可直接导入，
也可用命令行构建 H5 / 微信小程序 / App 资源。

---

## 0. 前置检查

1. HBuilderX 已安装并登录（云打包需要登录 DCloud 账号）。
2. `student-app/node_modules` 已安装（本机已装好）。
3. 后端已启动且手机能访问（见 `docs/INSTALL.md` 的网络说明）。

```powershell
# 只改接口地址，不改代码
# 编辑 student-app/.env.production
VITE_API_BASE_URL=http://电脑局域网IP:8000
```

改完地址后需要重新构建/打包，旧包里的地址不会变。

---

## 1. 导入 HBuilderX

1. 打开 HBuilderX。
2. 菜单「文件」→「导入」→「从本地目录导入」。
3. 选择目录：`G:\deepseek\zhiyin-assistant\student-app`。
4. 导入后打开 `src/manifest.json`。

需要确认的配置：

| 配置 | 当前值 | 说明 |
|---|---|---|
| 应用名称 | 智印助手 | `manifest.json` 的 `name` |
| 包名 | `com.zhiyin.assistant` | `app-plus.distribute.android.packagename` |
| 版本 | 1.0.0 / 100 | `versionName` / `versionCode` |
| 最低系统 | Android 8.0（API 26） | `minSdkVersion: 26` |
| 权限 | 网络、相机、读写存储、相册 | 已按最小必要配置 |
| 图标 | `src/static/app-icon.png` | 1024×1024；HBuilderX 图标配置里可直接选它 |

**appid 必须换掉**：当前是占位值 `__UNI__ZHIYIN01`。
在 HBuilderX 里打开 `manifest.json` →「基础配置」→ 点击「重新获取」/ 选择你账号下的 uni-app 应用，
把 appid 换成 DCloud 分配的真实值，否则云打包会失败。

---

## 2. 运行到浏览器（开发预览）

1. HBuilderX 菜单「运行」→「运行到浏览器」→ 选择 Edge/Chrome。
2. 或在 `student-app` 目录执行 `npm.cmd run dev:h5`，打开 <http://127.0.0.1:5173>。

---

## 3. Android 签名证书

云打包可以先用「公共测试证书」，但正式交付建议用自己的证书（升级安装需要同一证书）。

生成命令（JDK 的 `keytool`，密码自己设定并妥善保存）：

```powershell
keytool -genkeypair -v ^
  -keystore zhiyin-release.jks ^
  -alias zhiyin ^
  -keyalg RSA -keysize 2048 -validity 36500 ^
  -storepass 你的证书密码 ^
  -keypass 你的证书密码 ^
  -dname "CN=ZhiYin, OU=Student, O=ZhiYin Assistant, L=Changsha, ST=Hunan, C=CN"
```

注意：

- `.jks` 证书和密码**不要提交到 Git**。
- 记下 alias、storepass、keypass，云打包时要填。
- 证书丢失后无法覆盖安装升级，只能卸载重装。

---

## 4. 云打包 APK

1. HBuilderX 中打开 `student-app`。
2. 菜单「发行」→「原生 App-云打包」。
3. 平台勾选 **Android（apk）**。
4. 证书：
   - 演示可用「使用公共测试证书」；
   - 正式交付选「使用自有证书」，选择上面的 `.jks`，填写 alias 与密码。
5. 点击「打包」，等待云端完成，下载 APK。
6. APK 默认输出在项目的 `unpackage/release/` 下（以 HBuilderX 提示为准）。

打包前最后检查：

- `.env.production` 的 `VITE_API_BASE_URL` 是手机能访问的地址（同一局域网用电脑 IP）。
- 后端监听 `0.0.0.0:8000`，Windows 防火墙已放行 8000。
- `manifest.json` 的 appid 已换成真实值。

---

## 5. 微信小程序（后续学院改造用）

```powershell
cd student-app
npm.cmd run build:mp-weixin
```

产物在 `student-app/dist/build/mp-weixin/`。

1. 打开微信开发者工具 → 导入项目 → 选择该目录。
2. 在 `src/manifest.json` 的 `mp-weixin.appid` 填入小程序 appid（当前为空）。
3. 开发者工具中勾选「不校验合法域名」（开发期），生产环境需在微信后台配置 request/uploadFile 合法域名。

小程序端 `uni.chooseFile` 可用；App 端走 `plus.io.chooseFile`，失败时使用「载入示例论文」。

---

## 6. 构建产物一览

| 目标 | 命令 | 产物 |
|---|---|---|
| H5 | `npm.cmd run build:h5` | `dist/build/h5` |
| 微信小程序 | `npm.cmd run build:mp-weixin` | `dist/build/mp-weixin` |
| App 资源 | `npm.cmd run build:app` | `dist/build/app` |
| Android APK | HBuilderX 云打包 | `unpackage/release/*.apk` |
