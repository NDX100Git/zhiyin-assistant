/**
 * 前端运行配置。
 *
 * API 地址通过 .env 注入（VITE_API_BASE_URL），不在代码里硬编码。
 *
 * 没配时的兜底是**空字符串**，也就是走同源相对路径（请求 /api/...）：
 * - H5 与后端同源部署（Nginx 把 /api 反代到后端）是生产的主流做法，这时候
 *   留空就是对的，换域名、换机器都不用重新出包。
 * - 兜底不写回环地址：写死 http://127.0.0.1:8000 的话，线上构建一漏配就会
 *   全站打到用户自己的电脑上，控制台一片网络错误，排查的人还以为是后端挂了。
 * - 开发与真机调试由 .env.development 给绝对地址（本机 8000 / 局域网 IP /
 *   HTTPS 域名），见 student-app/.env.development 与 README。
 * - 微信小程序不支持相对路径，上线必须显式配绝对 HTTPS 地址，
 *   warnOnPlatformConfig() 会在构建/启动时点名提醒。
 */

const fallbackBase = ''

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
 * 启动自检：接口地址配错只会在真机、线上看到「网络异常」，
 * 不如启动时在控制台点名说清楚。不抛错——开发者工具里勾了「不校验合法域名」
 * 时局域网地址照样能用，开发不该被拦死。
 */
export function warnOnPlatformConfig() {
  if (!IS_MINI_PROGRAM) return
  const problems = []
  if (!API_BASE_URL) {
    problems.push(
      '接口地址为空（同源相对路径只对 H5 成立），小程序必须配绝对地址：' +
        'VITE_API_BASE_URL=https://你的域名'
    )
  } else if (!API_BASE_URL.startsWith('https://')) {
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

/**
 * 本地存储键名：全端唯一来源。
 *
 * 页面与服务层一律用这里的常量，不许再手写字符串。
 * 历史上「我的」页的昵称保存曾手写 'zhiyin_user'，与登录处的常量各存一份，
 * 两边一旦改得不同步，重启后就读不到用户信息。
 */
export const STORAGE_KEYS = {
  token: 'zhiyin_token',
  user: 'zhiyin_user',
  deviceId: 'zhiyin_device_id',
  // 学生自己的大模型密钥：只存本机、只走请求头，服务端不落库
  // （读写一律走 services/llmKey.js，页面不许直接碰这个键）
  llmKey: 'zhiyin_llm_key',
}
