import http from './http'
import { explainError } from '../utils/errors'

/**
 * 包一层：把请求层的错误统一换成可读中文（码表见 utils/errors.js）。
 *
 * 拦截器已经在弹后端原文的轻提示了，这里再换一遍是为了让**调用方**（弹窗内联
 * 报错、关闭按钮的 loading 收尾）拿到同一句话，不至于一个错误两套说法。
 * 未知码原样透传后端原文，不做二次创作。
 */
function wrap(promise) {
  return promise.catch((error) => {
    throw explainError(error)
  })
}

const get = (url, config) => wrap(http.get(url, config))
const post = (url, data, config) => wrap(http.post(url, data, config))

export const login = (username, password) => post('/api/admin/login', { username, password })

export const fetchQueue = (params) => get('/api/admin/queue', { params })
export const fetchSummary = () => get('/api/admin/summary')
export const fetchQuote = (orderId) => get(`/api/admin/order/${orderId}/quote`)
export const downloadUrl = (orderId) => get(`/api/admin/order/${orderId}/download`)

/**
 * 逐文件下载：fileIndex 是清单里的「第几份」（从 1 开始）。
 * 不传时后端返回全部文件链接，行为与旧接口一致。
 */
export const downloadFileUrl = (orderId, fileIndex) =>
  get(`/api/admin/order/${orderId}/download`, {
    params:
      fileIndex === undefined || fileIndex === null || fileIndex === ''
        ? {}
        : { file_index: fileIndex },
  })

/** 老师重新下载后确认看到当前文件，清掉队列里的「文件已替换」标记。 */
export const confirmFileSeen = (orderId) =>
  post(`/api/admin/order/${orderId}/confirm-files-seen`)
export const startPrint = (orderId) => post(`/api/admin/order/${orderId}/start`)

export const completePrint = (orderId, amountCents) =>
  post(`/api/admin/order/${orderId}/complete`, { amount_cents: amountCents })

export const batchComplete = (orderIds) =>
  post('/api/admin/order/batch-complete', { order_ids: orderIds })

export const verifyOrder = (orderId) => post(`/api/admin/order/${orderId}/verify`)
export const verifyByCode = (pickupCode) =>
  post('/api/admin/order/verify-code', { pickup_code: pickupCode })
export const markAbnormal = (orderId, reason) =>
  post(`/api/admin/order/${orderId}/abnormal`, { reason })
export const recoverOrder = (orderId) => post(`/api/admin/order/${orderId}/recover`)

/**
 * 关闭异常单（异常单唯一的关单入口），可同时登记退款。
 *
 * refundCents 单位是分：不传由后端按成交价全额退（不超过实际已收），
 * 传 0 表示不退。后端 40061/40062 会挡住负数与超过已收的金额，
 * 前端在弹窗里也先拦一道，别让老师填完才被拒。
 */
export const cancelAbnormal = (orderId, reason, refundCents) =>
  post(`/api/admin/order/${orderId}/cancel`, {
    reason,
    ...(refundCents === undefined || refundCents === null ? {} : { refund_cents: refundCents }),
  })

/**
 * 确认或驳回学生提交的换文件申请。
 * amountCents 是老师确认后的最终价（分）；不传则按系统重算价落库。
 */
export const confirmFileChange = (orderId, approve, amountCents) =>
  post(`/api/admin/order/${orderId}/confirm-file-change`, {
    approve,
    ...(amountCents === undefined || amountCents === null
      ? {}
      : { amount_cents: amountCents }),
  })

export const fetchSessions = (date) => get('/api/admin/sessions', { params: { date } })
export const closeSession = (date, session) =>
  post('/api/admin/session/close', { date, session })
export const openSession = (date, session) =>
  post('/api/admin/session/open', { date, session })
export const publishNotice = (content) => post('/api/admin/notice', { content })
export const fetchTransactions = (date) => get('/api/admin/transactions', { params: { date } })
