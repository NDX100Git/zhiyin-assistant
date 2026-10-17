import axios from 'axios'
import { ElMessage } from 'element-plus'

const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000',
  timeout: 120000,
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem('zhiyin_admin_token')
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
      localStorage.removeItem('zhiyin_admin_token')
      localStorage.removeItem('zhiyin_admin_user')
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
