# 上一轮 AI 给下一位接力者的说明

> 本文整理自项目收尾时的交接记录。项目源码、完整需求和排障细节以
> [HANDOFF.md](HANDOFF.md) 为准；本文用于让新接手者先理解“拿到项目后应该做什么”。

## 同学拿到后第一步做什么

1. 将项目解压到一个纯英文路径，例如 `D:\zhiyin-handoff`。
2. 先读根目录的 [START_HERE.md](START_HERE.md)，再读 [HANDOFF.md](HANDOFF.md) 第 4、5 节。
3. 按交接文档完成环境准备：
   - 安装 Python、Node.js、MySQL、MinIO 和 LibreOffice；
   - 将 `.env.example` 复制为 `backend\.env`，填写本机数据库和存储密码；
   - 创建 Python 虚拟环境并安装后端依赖；
   - 初始化 MySQL、启动 MinIO，然后启动后端；
   - 安装学生端和老师端的前端依赖。
4. 环境安装完成后，每次开机双击根目录的 `start_all.cmd` 即可启动服务。

默认本地入口：

- 学生端 H5：<http://127.0.0.1:5173>
- 老师端 Web：<http://127.0.0.1:5174>
- 接口文档：<http://127.0.0.1:8000/docs>

## 新接手者必须自行安装的组件

- Python 3.11+（推荐 3.11-3.14）
- Node.js 18+
- MySQL 8
- MinIO
- LibreOffice
- 如果需要生成 APK，还需要 HBuilderX 和 DCloud 账号

仓库是源码交付，不包含以下与本机环境相关的目录或大数据文件：

- `backend\.venv`
- `backend\.tools\mysql8`
- `backend\.tools\minio`
- `node_modules`
- MySQL 和 MinIO 的数据目录
- 真实 `.env` 文件
- 真实 API Key

MinIO 的 Windows 二进制约 108 MB，官方已停发该二进制。需要从可信来源获取
`minio.exe`，放到 `backend\.tools\minio\minio.exe`，具体版本和下载方式见
[HANDOFF.md](HANDOFF.md)。

## 交付时仍未解决的问题

1. APK 尚未实际生成，需要 DCloud 账号在 HBuilderX 中云打包。
2. App 端选择文档依赖 `plus.io.chooseFile`，部分 Android 版本可能不可用；项目已提供
   「载入示例论文」兜底。
3. 上传文件 24 小时后清理、AI 报告 7 天后清理尚未接入定时任务。
4. 扫描版 PDF 转 Word 的效果有限，需要先做 OCR。
5. AI 任务进度存放在内存中，后端重启后会丢失；V1 暂可接受。
6. 管理端目前是单账号，没有多管理员和 RBAC。
7. MinIO 二进制来源不稳定属于运行环境问题，不是业务代码问题。

## 从旧压缩包接手时的额外提醒

旧交接压缩包只包含源码和文档，不包含可直接运行的依赖。拿到后如果页面能打开但
登录或上传失败，先确认 MySQL、MinIO 和后端是否都在运行，而不是先怀疑 API Key。
API Key 只影响 AI 体检、修复建议和 OCR；基础打印方案与下单流程仍可运行。
