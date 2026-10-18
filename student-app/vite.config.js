import fs from 'node:fs'
import { defineConfig } from 'vite'
import uni from '@dcloudio/vite-plugin-uni'

/**
 * 微信小程序产物必须有 appid，否则只能生成游客身份包（只能在本机开发者工具里跑）。
 * 这里在构建时点名提示，避免交付一个打不开的包。
 */
function warnMissingWeixinAppid() {
  if (process.env.UNI_PLATFORM !== 'mp-weixin') return
  try {
    const manifest = JSON.parse(fs.readFileSync('src/manifest.json', 'utf-8'))
    const appid = (manifest['mp-weixin'] || {}).appid
    if (!appid) {
      console.warn(
        '\n[配置] 微信小程序 appid 为空，产物会以游客身份生成，无法真机预览与上传。\n' +
          '       请在 src/manifest.json 的 mp-weixin.appid 填入小程序 appid。\n'
      )
    }
  } catch (error) {
    console.warn('[配置] 读取 src/manifest.json 失败', error && error.message)
  }
}

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    uni(),
    {
      name: 'zhiyin-config-check',
      buildStart() {
        warnMissingWeixinAppid()
      },
    },
  ],
})
