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

/**
 * 登录失效的业务码。后端统一返回 HTTP 200 + 业务 code，只能按 code 判断：
 * 40100=没有带 token，40101=token 无效或账号不存在（例如演示前重建了数据库）。
 */
const AUTH_EXPIRED_CODES = [40100, 40101]

/** 登录失效时广播的全局事件，由 App.vue 监听并静默重新登录。 */
export const AUTH_EXPIRED_EVENT = 'zhiyin:auth-expired'

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
        if (body && AUTH_EXPIRED_CODES.includes(body.code)) {
          // 本地 token 已经不被服务端认可（过期、换密钥、演示前重建了数据库）。
          // 必须清掉：login() 见到本地有 token 就直接返回，留着它会让之后每个
          // 请求都 401，页面永远自愈不了。清掉后由 App.vue 静默重新登录。
          clearToken()
          uni.$emit(AUTH_EXPIRED_EVENT)
          reject(new Error(body.message || '登录已失效，正在重新登录'))
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

/**
 * 撤回一个还没下单的上传文件。
 *
 * 服务端会校验归属（只能删自己传的）与引用（已被订单、文件版本或待确认请求
 * 引用的文件拒绝删除），因此这里不做任何本地猜测，失败就把原因原样交给页面。
 */
export function deleteUploadedFile(fileKey) {
  return request('/api/file/delete', { method: 'POST', data: { file_key: fileKey } })
}

/**
 * 服务端报价：只算不落库。
 *
 * 请求体与下单完全一致（文件清单 + 订单级四个参数），返回
 * `{files: [{file_index, file_key, file_name, page_count, print_pages,
 * print_side, color_mode, paper_size, copies, amount_cents}],
 * page_count, print_page_count, amount_cents}`。
 * 页面上的每份行金额与整单金额只能来自这里，不许前端自己乘单价。
 */
export function quoteOrder(payload) {
  return request('/api/order/quote', { method: 'POST', data: payload })
}
