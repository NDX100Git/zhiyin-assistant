import fs from 'node:fs'
import path from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import uni from '@dcloudio/vite-plugin-uni'

/** 工程根目录（vite.config.js 所在目录），与 uni 的 UNI_INPUT_DIR（`<root>/src`）对应。 */
const ROOT = process.cwd()
const MANIFEST_PATH = path.join(ROOT, 'src', 'manifest.json')
/** uni 把 `src/manifest.json` 包成的虚拟模块名（@dcloudio/uni-cli-shared 的 JSON_JS_MAP）。 */
const MANIFEST_JSON_JS = 'manifest-json-js'

/**
 * 微信小程序身份码（appid）从构建参数注入，不写进版本库。
 *
 * 为什么不在 `src/manifest.json` 里写死：
 * - 身份码是**部署参数**，不是源码。写进仓库等于谁拉代码谁拿到了业务方的小程序身份，
 *   而正式身份码一旦泄露，别人可以拿它传代码、发体验版；
 * - 一台机器可能同时出好几个包（测试号 / 正式号），写死就得改文件，改完还容易误提交。
 *
 * 读取顺序（前者优先）：
 * 1. 命令行构建参数：`UNI_MP_WEIXIN_APPID=wx... npm run build:mp-weixin`；
 * 2. `.env.local` / `.env.<mode>.local` 里的 `VITE_MP_WEIXIN_APPID`（已被 .gitignore 忽略，
 *    本机开发就放这里，退出重进也还在）；
 * 3. 都没有 = 空字符串：产物是游客身份包，构建时点名告警（不抛错，H5 / App 构建不受影响）。
 *
 * 本地开发默认走第 2 条：`student-app/.env.local` 写一行 `VITE_MP_WEIXIN_APPID=wx...` 即可。
 */
function resolveWeixinAppid(mode) {
  const env = loadEnv(mode, ROOT, 'VITE_')
  return String(process.env.UNI_MP_WEIXIN_APPID || env.VITE_MP_WEIXIN_APPID || '').trim()
}

function readManifest() {
  return JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf-8'))
}

/**
 * 把身份码注入到 manifest 的 `mp-weixin.appid`。
 *
 * uni 读 manifest 的方式是：把一个虚拟模块（`<inputDir>/manifest-json-js`）的**内容**
 * 当成 manifest.json 的文本交给上层解析，小程序产物里的 `project.config.json`
 * 就是从这里拿 appid 的。所以注入点选在这个虚拟模块的 `load`：本插件排在 `uni()`
 * 之前且 `enforce: 'pre'`，先拿到这次 load，返回改写后的 JSON，uni 自己的 load 就不再执行。
 * 磁盘上的 `src/manifest.json` 一个字节都不动——不然构建会把工作区改脏。
 */
function injectWeixinAppid(appid) {
  return {
    name: 'zhiyin-inject-mp-weixin-appid',
    enforce: 'pre',
    load(id) {
      if (process.env.UNI_PLATFORM !== 'mp-weixin') return
      if (!appid) return
      const clean = id.split('?')[0].replace(/\\/g, '/')
      if (!clean.endsWith(MANIFEST_JSON_JS)) return
      const manifest = readManifest()
      manifest['mp-weixin'] = { ...(manifest['mp-weixin'] || {}), appid }
      return JSON.stringify(manifest, null, 2)
    },
  }
}

/**
 * 微信小程序产物必须有 appid，否则只能生成游客身份包（只能在本机开发者工具里跑）。
 * 这里在构建时点名提示，避免交付一个打不开的包。
 */
function warnMissingWeixinAppid(appid) {
  if (process.env.UNI_PLATFORM !== 'mp-weixin') return
  if (appid) return
  console.warn(
    '\n[配置] 微信小程序 appid 为空，产物会以游客身份生成，无法真机预览与上传。\n' +
      '       注入方式（二选一，不要写进 src/manifest.json）：\n' +
      '         UNI_MP_WEIXIN_APPID=wx... npm run build:mp-weixin\n' +
      '         student-app/.env.local 里写：VITE_MP_WEIXIN_APPID=wx...\n'
  )
}

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const appid = resolveWeixinAppid(mode)
  warnMissingWeixinAppid(appid)

  return {
    plugins: [
      // 必须排在 uni() 之前：抢在 uni 之前接管 manifest 虚拟模块的 load
      injectWeixinAppid(appid),
      uni(),
    ],
  }
})
