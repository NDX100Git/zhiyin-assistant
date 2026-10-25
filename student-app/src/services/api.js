/**
 * 统一请求封装：自动带 token、统一解析 {code,message,data}。
 * 页面只通过本文件和适配层访问后端，不直接拼 URL。
 */

// 相对路径导入：单测在 Node 里直接 import 本文件，'@/' 别名只有 uni/vite 构建期能解析
import { API_BASE_URL, STORAGE_KEYS } from '../config/index.js'
import { explainApiCode } from '../utils/errors.js'
import { LLM_KEY_CHECK_PATH, LLM_KEY_HEADER, llmKeyHeader } from './llmKey.js'

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
  // 这里**故意**不带 X-LLM-Api-Key：/api/file/upload、/api/file/page-count 只做文件落盘
  // 与页数解析，一次模型都不调，没理由让学生密钥经过这条通道。
  return header
}

/** 所有 AI 接口都在 /api/ai/ 下（体检、校对与任务轮询、打印方案、修复、看图识字、PDF 转 Word）。 */
const AI_PATH_RE = /^\/api\/ai(\/|$|\?)/

/** 这个路径要不要带学生自己的密钥。按路径判，新加的 AI 接口自动就有，不会漏一个。 */
export function isAiPath(path) {
  return AI_PATH_RE.test(String(path || ''))
}

/**
 * 给失败对象挂上后端业务码，并打上「这次请求带过学生密钥」的标记。
 *
 * 业务码必须挂到错误对象上，不能只留一条译好的中文：文案是给人看的，码才是契约。
 * 上层（llmKey.js 的 isLlmKeyFailure）判断「学生的密钥是不是坏了」只认码；
 * 码丢了就只能退回拿后端措辞去比对，后端改一次错别字判定就失灵。
 * 两条通道（uni.request 与 uni.uploadFile）都走这里，别只补一条。
 *
 * `llmKeySent` / `llmKeyRejected` 标记的用途：isLlmKeyFailure() 靠它区分
 * 「密钥不行」与「文件解析失败」——没带密钥的请求失败，绝不能提示学生去清理密钥。
 */
function decorateApiError(error, { code, sentLlmKey = false, rejected = false } = {}) {
  if (typeof code !== 'undefined') {
    // 有的代理会把码变成字符串：能转成数字就统一成数字，免得上层比 50035 与 '50035' 不等
    const numeric = Number(code)
    error.code = Number.isFinite(numeric) ? numeric : code
  }
  if (sentLlmKey) {
    error.llmKeySent = true
    if (rejected) error.llmKeyRejected = true
  }
  return error
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
  const { method = 'GET', data, auth = true, timeout = 90000, llmKey } = options

  // 密钥只进请求头，且只在 AI 接口上带；data 与 URL 都不碰它。
  // llmKey 显式给了就用给的（「测试连通」要测还没保存的那把），否则用本机存的那把。
  const keyHeader = isAiPath(path) ? llmKeyHeader(llmKey) : {}
  const sentLlmKey = Boolean(keyHeader[LLM_KEY_HEADER])

  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE_URL}${path}`,
      method,
      data,
      header: { ...buildHeader(auth), ...keyHeader },
      timeout,
      success(response) {
        const body = parseBody(response.data)
        if (body && body.code === 0) {
          resolve(body.data)
          return
        }
        // HTTP 401/403：服务端直接拦在鉴权层，只可能是密钥这件事
        const httpRejected = response.statusCode === 401 || response.statusCode === 403
        if (body && AUTH_EXPIRED_CODES.includes(body.code)) {
          // 本地 token 已经不被服务端认可（过期、换密钥、演示前重建了数据库）。
          // 必须清掉：login() 见到本地有 token 就直接返回，留着它会让之后每个
          // 请求都 401，页面永远自愈不了。清掉后由 App.vue 静默重新登录。
          clearToken()
          uni.$emit(AUTH_EXPIRED_EVENT)
          reject(
            decorateApiError(new Error(body.message || '登录已失效，正在重新登录'), {
              code: body.code,
            })
          )
          return
        }
        if (body && typeof body.code !== 'undefined') {
          // 业务码统一在这里译成中文：页面上不需要再认码，也不会漏掉某一屏。
          // 码本身也挂到错误对象上——页面与 isLlmKeyFailure() 要的是码，不是句子。
          reject(
            decorateApiError(new Error(explainApiCode(body.code, body.message)), {
              code: body.code,
              sentLlmKey,
              rejected: httpRejected,
            })
          )
          return
        }
        reject(
          decorateApiError(new Error(`服务返回异常（HTTP ${response.statusCode}）`), {
            sentLlmKey,
            rejected: httpRejected,
          })
        )
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
        // 转换类错误（50041 / 50043 / 50046）多数在解析上传文件时就发生，
        // 走的是这条上传通道而不是 request()，翻译与挂码必须两边都做。
        if (body && typeof body.code !== 'undefined') {
          reject(
            decorateApiError(new Error(explainApiCode(body.code, body.message)), {
              code: body.code,
            })
          )
          return
        }
        reject(new Error('上传失败'))
      },
      fail(error) {
        reject(new Error(error.errMsg || '上传失败'))
      },
    })
  })
}

/**
 * 测试学生自己的密钥：让服务端拿请求头里那把做一次最小调用。
 *
 * 成功返回服务端给的 `data`；失败抛错（中文原因由服务端给）。
 * 密钥仍然只在请求头里，不进请求体。
 *
 * @param {string} [key] 要测的密钥；不传则测本机存着的那把
 */
export function checkLlmKey(key) {
  return request(LLM_KEY_CHECK_PATH, { method: 'POST', llmKey: key })
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
