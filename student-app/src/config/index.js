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

export const POLL_INTERVAL = Number(
  (import.meta.env && import.meta.env.VITE_POLL_INTERVAL) || 4000
)

export const STORAGE_KEYS = {
  token: 'zhiyin_token',
  user: 'zhiyin_user',
  deviceId: 'zhiyin_device_id',
}
