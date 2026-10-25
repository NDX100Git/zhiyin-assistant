import axios from 'axios'
import { ElMessage } from 'element-plus'

/**
 * 后台登录凭证存放在浏览器本地存储（localStorage），这是有意的取舍，不是遗漏。
 *
 * - 使用前提：老师端只装在打印店柜台机上，内网环境、固定机器、物理可控；
 *   柜台员工的登录凭证本来就要能自动续命，每次刷新都得重新登录会直接影响接单。
 * - 已知风险：XSS 可以读走 token。后端已把 token 做短有效期（JWT_EXPIRE_MINUTES），
 *   且这里在收到登录失效码时立即清掉两个键，不让坏 token 一直粘着。
 * - 本轮的约定：产品与老板已确认接受现状，不改存储方式（不换成 httpOnly Cookie）。
 * 要改的那一轮请连带改登录页（写 token 的地方在 views/LoginView.vue）与后端下发方式，
 * 只改这里会把登录流程改断。
 */
const TOKEN_KEY = 'zhiyin_admin_token'
const USER_KEY = 'zhiyin_admin_user'

/** 登录已失效：清掉本地凭证并回登录页（四个码都是这一个动作）。 */
const AUTH_LOST_CODES = [40100, 40101, 40102, 40300]

/** 默认提示器：交给 Element Plus 弹出红条。 */
function defaultNotify(message) {
  ElMessage.error(message)
}

/**
 * 后端会把业务码告诉调用方：抛出去的 Error 上带 `code`（业务码，数字）。
 *
 * 以前这里只 `new Error(message)` 把后端文案抛出去，业务码在拦截器里就丢了，
 * 调用方只能按文案反查是哪个码（后端改一个字就认不出来）。现在按
 * `utils/errors.js` 约定的契约直接带上 `error.code = body.code`，那里的按码翻译表
 * 就能生效。
 *
 * 注意 axios 自己的 Error 上 `code` 是传输层错误码（`ECONNABORTED`、`ERR_NETWORK`
 * 这类字符串）。两者同名但含义不同：只有后端明确给了非 0 的业务码时才覆盖它。
 */
function apiError(message, code, response) {
  const error = new Error(message)
  const numeric = Number(code)
  if (Number.isFinite(numeric) && numeric !== 0) {
    error.code = numeric
  }
  if (response) {
    error.response = response
  }
  return error
}

/**
 * 造一对响应拦截器处理函数。
 *
 * 提示器做成参数（默认就是 ElMessage），不只是为了方便单测：传输层不该直接绑死
 * 某个 UI 组件的渲染方式，换成别的提示样式时只换一个参数。`npm test` 就是这样
 * 驱动真实判断逻辑（带码、登录失效清凭证）而不必拉起 Vue 渲染。
 *
 * @param {{notify?: (message: string) => void}} [options]
 */
export function createResponseHandlers({ notify = defaultNotify } = {}) {
  return {
    /** HTTP 200 + 业务码：0 成功交 data，非 0 一律 reject 一个带 code 的错误。 */
    onFulfilled(response) {
      const body = response.data
      if (body && body.code === 0) {
        return body.data
      }
      const message = (body && body.message) || '请求失败'
      const error = apiError(message, body && body.code, response)
      if (body && AUTH_LOST_CODES.includes(body.code)) {
        localStorage.removeItem(TOKEN_KEY)
        localStorage.removeItem(USER_KEY)
        notify(message)
        window.location.hash = '#/login'
      } else {
        notify(message)
      }
      return Promise.reject(error)
    },

    /**
     * 传输层失败（超时、断网、网关 5xx）。后端在非 2xx 响应体里也会带 code/message，
     * 这时把业务码接到同一个错误对象上，交给 `utils/errors.js` 按码翻译。
     * 不换掉 axios 那个错误对象：它的 config / request / 传输层 code 还在，
     * 以后要区分「超时」和「业务拒绝」还用得上。
     */
    onRejected(error) {
      const body = error && error.response && error.response.data
      const bizCode = Number(body && body.code)
      const message = (body && body.message) || error.message || '网络错误'
      if (Number.isFinite(bizCode) && bizCode !== 0) {
        error.code = bizCode
      }
      if (body && body.message) {
        error.message = message
      }
      notify(message)
      return Promise.reject(error)
    },
  }
}

/**
 * 接口地址只从构建环境来（`VITE_API_BASE_URL`），代码里**没有任何地址兜底**。
 *
 * 以前这里兜底写死 `http://127.0.0.1:8000`，问题不是“开发方便”，而是会跟着
 * 生产包一起发出去：`.env.production` 一漏配，老师端柜台机上线就全站打到本机
 * 回环，控制台一片网络错误，还让人以为后端挂了。
 *
 * 现在口径与 `vite.config.js`、`.env.production` 一致：
 * - 留空（生产同源部署的默认值）= 相对路径 `''`，请求 `/api/...`，由 Nginx 反代；
 * - 开发由 `.env.development` 给绝对地址，真机/独立域名用命令行环境变量覆盖。
 * 单测在 Node 里 import 本模块时没有 Vite 注入的 import.meta.env，用可选链兜住。
 */
const API_BASE_URL = String((import.meta.env && import.meta.env.VITE_API_BASE_URL) || '')

const http = axios.create({
  baseURL: API_BASE_URL,
  timeout: 120000,
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

const handlers = createResponseHandlers()
http.interceptors.response.use(handlers.onFulfilled, handlers.onRejected)

export default http
