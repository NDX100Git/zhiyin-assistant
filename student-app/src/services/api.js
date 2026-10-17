/**
 * 统一请求封装：自动带 token、统一解析 {code,message,data}。
 * 页面只通过本文件和适配层访问后端，不直接拼 URL。
 */

import { API_BASE_URL, STORAGE_KEYS } from '@/config'

export function getToken() {
  return uni.getStorageSync(STORAGE_KEYS.token) || ''
}

export function setToken(token) {
  uni.setStorageSync(STORAGE_KEYS.token, token || '')
}

export function clearToken() {
  uni.removeStorageSync(STORAGE_KEYS.token)
}

function buildHeader(auth = true) {
  const header = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (auth && token) {
    header.Authorization = `Bearer ${token}`
  }
  return header
}

/**
 * 上传专用的请求头。
 *
 * 注意：不能带 Content-Type: application/json。文件上传必须由浏览器/运行时
 * 自动生成 multipart/form-data 及 boundary，否则后端收不到 file 字段
 * （会报「参数校验失败：file 字段要求」）。
 */
function buildUploadHeader() {
  const header = {}
  const token = getToken()
  if (token) {
    header.Authorization = `Bearer ${token}`
  }
  return header
}

function parseBody(raw) {
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw)
    } catch (error) {
      return null
    }
  }
  return raw
}

export function request(path, options = {}) {
  const { method = 'GET', data, auth = true, timeout = 90000 } = options

  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE_URL}${path}`,
      method,
      data,
      header: buildHeader(auth),
      timeout,
      success(response) {
        const body = parseBody(response.data)
        if (body && body.code === 0) {
          resolve(body.data)
          return
        }
        if (body && typeof body.code !== 'undefined') {
          reject(new Error(body.message || '请求失败'))
          return
        }
        reject(new Error(`服务返回异常（HTTP ${response.statusCode}）`))
      },
      fail(error) {
        reject(new Error(error.errMsg || '网络连接失败，请检查 API 地址与网络'))
      },
    })
  })
}

/** 上传本地文件（路径由 filePicker 适配层提供）。 */
export function uploadFile(filePath, fileName) {
  return new Promise((resolve, reject) => {
    uni.uploadFile({
      url: `${API_BASE_URL}/api/file/upload`,
      filePath,
      name: 'file',
      header: buildUploadHeader(),
      formData: { file_name: fileName || '' },
      timeout: 120000,
      success(response) {
        const body = parseBody(response.data)
        if (body && body.code === 0) {
          resolve(body.data)
          return
        }
        reject(new Error((body && body.message) || '上传失败'))
      },
      fail(error) {
        reject(new Error(error.errMsg || '上传失败'))
      },
    })
  })
}
