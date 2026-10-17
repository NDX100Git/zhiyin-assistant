/**
 * 登录适配层。
 *
 * V1：设备标识登录（不采集学号等敏感信息），首次自动注册。
 * V2：可在此文件内替换为微信登录 / 学校统一认证，页面无需改动。
 */

import { STORAGE_KEYS } from '@/config'
import { clearToken, getToken, request, setToken } from './api'

function getDeviceId() {
  let deviceId = uni.getStorageSync(STORAGE_KEYS.deviceId)
  if (!deviceId) {
    deviceId = `dev-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
    uni.setStorageSync(STORAGE_KEYS.deviceId, deviceId)
  }
  return deviceId
}

export async function login() {
  const token = getToken()
  if (token) {
    return token
  }

  const data = await request('/api/auth/login', {
    method: 'POST',
    data: { device_id: getDeviceId() },
    auth: false,
  })
  setToken(data.token)
  uni.setStorageSync(STORAGE_KEYS.user, data.user || {})
  return data.token
}

export function getCurrentUser() {
  return uni.getStorageSync(STORAGE_KEYS.user) || null
}

export function logout() {
  clearToken()
  uni.removeStorageSync(STORAGE_KEYS.user)
}

export async function ensureLogin() {
  return login()
}
