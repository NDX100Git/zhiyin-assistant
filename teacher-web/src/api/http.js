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

const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000',
  timeout: 120000,
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => {
    const body = response.data
    if (body && body.code === 0) {
      return body.data
    }
    const message = (body && body.message) || '请求失败'
    if (body && [40100, 40101, 40102, 40300].includes(body.code)) {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
      ElMessage.error(message)
      window.location.hash = '#/login'
    } else {
      ElMessage.error(message)
    }
    return Promise.reject(new Error(message))
  },
  (error) => {
    const message =
      (error.response && error.response.data && error.response.data.message) ||
      error.message ||
      '网络错误'
    ElMessage.error(message)
    return Promise.reject(new Error(message))
  },
)

export default http
