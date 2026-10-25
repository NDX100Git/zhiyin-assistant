import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * 本机回环 / 内网地址：出现在**生产构建**的接口地址里就是配置事故。
 *
 * 老师端装在打印店柜台机上，地址一漏配，上线当天全站请求会打回柜台机自己，
 * 现场表现是「后端挂了」，实际是前端地址配错。这里不抛错（学校内部署确实可能
 * 用内网域名/IP 直连），只在构建时把话说清楚，别让人上线后才发现。
 */
const PRIVATE_HOST_RE =
  /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\]|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/i

/**
 * 构建时自检接口地址（只看生产构建：开发环境本来就用本机回环，一天提醒十次没人看）。
 *
 * 读取口径与 `src/api/http.js` 完全一致：都只看 `VITE_API_BASE_URL`，
 * 留空即同源相对路径（`docs/DEPLOYMENT.md` 的 Nginx 反代做法）。
 */
function warnOnApiBase(mode, apiBase) {
  if (mode !== 'production') return
  const value = String(apiBase || '').trim()
  if (!value) {
    console.log(
      '[配置] VITE_API_BASE_URL 为空：生产包走同源相对路径（请求 /api/...），按 Nginx 反代部署。'
    )
    return
  }
  if (PRIVATE_HOST_RE.test(value)) {
    console.warn(
      `[配置] 生产构建的接口地址是本机或内网地址（${value}）：` +
        '换一台机器部署就会全部请求失败。请改用域名，或留空走同源相对路径。'
    )
    return
  }
  if (!value.startsWith('https://')) {
    console.warn(`[配置] 生产构建的接口地址不是 HTTPS（${value}），浏览器会拦混合内容。`)
  }
}

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  // 第三个参数限定只读 VITE_ 前缀，和 http.js 里 import.meta.env 的可见范围一致
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  warnOnApiBase(mode, env.VITE_API_BASE_URL)

  return {
    plugins: [vue()],
    server: {
      host: '127.0.0.1',
      port: 5174,
    },
  }
})
