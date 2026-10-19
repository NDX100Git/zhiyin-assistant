/**
 * 前端运行配置。
 *
 * API 地址通过 .env 注入（VITE_API_BASE_URL），不在代码里硬编码。
 * 真机调试必须填电脑的局域网 IP，不能用 127.0.0.1（那指向手机自己）。
 */

const fallbackBase = 'http://127.0.0.1:8000'

export const API_BASE_URL = String(
  (import.meta.env && import.meta.env.VITE_API_BASE_URL) || fallbackBase
).replace(/\/+$/, '')

/**
 * 订单状态轮询间隔（毫秒）。
 *
 * 默认 30 秒：一万人同时在线时每秒请求约 333 次，正好落在单机容量（约 340 次/秒）内。
 * 需要更实时可改 .env 里的 VITE_POLL_INTERVAL，但不要低于 5000，那会把容量打满。
 */
export const POLL_INTERVAL = Number(
  (import.meta.env && import.meta.env.VITE_POLL_INTERVAL) || 30000
)

let miniProgram = false
// #ifdef MP-WEIXIN
miniProgram = true
// #endif

/** 当前是不是微信小程序产物，用于走平台专有的能力与限制提示。 */
export const IS_MINI_PROGRAM = miniProgram

const PRIVATE_HOST_RE =
  /^https?:\/\/(127\.0\.0\.1|localhost|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/i

/**
 * 启动自检：微信小程序有硬性网络限制，配错了只会在真机上看到「网络异常」，
 * 不如启动时在控制台点名说清楚。不抛错——开发者工具里勾了「不校验合法域名」
 * 时局域网地址照样能用，开发不该被拦死。
 */
export function warnOnPlatformConfig() {
  if (!IS_MINI_PROGRAM) return
  const problems = []
  if (!API_BASE_URL.startsWith('https://')) {
    problems.push(`接口地址不是 HTTPS（当前 ${API_BASE_URL}）`)
  }
  if (PRIVATE_HOST_RE.test(API_BASE_URL)) {
    problems.push('接口地址是本机或局域网地址，真机与线上都连不到')
  }
  if (!problems.length) return
  console.warn(
    '[配置] 微信小程序线上要求 HTTPS 域名且已在小程序后台配好 request 与 uploadFile 白名单；' +
      `开发阶段可在开发者工具里勾「不校验合法域名」。当前问题：${problems.join('；')}`
  )
}

export const STORAGE_KEYS = {
  token: 'zhiyin_token',
  user: 'zhiyin_user',
  deviceId: 'zhiyin_device_id',
}
