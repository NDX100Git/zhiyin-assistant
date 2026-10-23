import http from './http'

export const login = (username, password) =>
  http.post('/api/admin/login', { username, password })

export const fetchQueue = (params) => http.get('/api/admin/queue', { params })
export const fetchSummary = () => http.get('/api/admin/summary')
export const fetchQuote = (orderId) => http.get(`/api/admin/order/${orderId}/quote`)
export const downloadUrl = (orderId) => http.get(`/api/admin/order/${orderId}/download`)

/**
 * 逐文件下载：fileIndex 是清单里的「第几份」（从 1 开始）。
 * 不传时后端返回全部文件链接，行为与旧接口一致。
 */
export const downloadFileUrl = (orderId, fileIndex) =>
  http.get(`/api/admin/order/${orderId}/download`, {
    params:
      fileIndex === undefined || fileIndex === null || fileIndex === ''
        ? {}
        : { file_index: fileIndex },
  })

/** 老师重新下载后确认看到当前文件，清掉队列里的「文件已替换」标记。 */
export const confirmFileSeen = (orderId) =>
  http.post(`/api/admin/order/${orderId}/confirm-files-seen`)
export const startPrint = (orderId) => http.post(`/api/admin/order/${orderId}/start`)

export const completePrint = (orderId, amountCents) =>
  http.post(`/api/admin/order/${orderId}/complete`, { amount_cents: amountCents })

export const batchComplete = (orderIds) =>
  http.post('/api/admin/order/batch-complete', { order_ids: orderIds })

export const verifyOrder = (orderId) => http.post(`/api/admin/order/${orderId}/verify`)
export const verifyByCode = (pickupCode) =>
  http.post('/api/admin/order/verify-code', { pickup_code: pickupCode })
export const markAbnormal = (orderId, reason) =>
  http.post(`/api/admin/order/${orderId}/abnormal`, { reason })
export const recoverOrder = (orderId) => http.post(`/api/admin/order/${orderId}/recover`)
export const cancelAbnormal = (orderId, reason) =>
  http.post(`/api/admin/order/${orderId}/cancel`, { reason })

/**
 * 确认或驳回学生提交的换文件申请。
 * amountCents 是老师确认后的最终价（分）；不传则按系统重算价落库。
 */
export const confirmFileChange = (orderId, approve, amountCents) =>
  http.post(`/api/admin/order/${orderId}/confirm-file-change`, {
    approve,
    ...(amountCents === undefined || amountCents === null
      ? {}
      : { amount_cents: amountCents }),
  })

export const fetchSessions = (date) => http.get('/api/admin/sessions', { params: { date } })
export const closeSession = (date, session) =>
  http.post('/api/admin/session/close', { date, session })
export const openSession = (date, session) =>
  http.post('/api/admin/session/open', { date, session })
export const publishNotice = (content) => http.post('/api/admin/notice', { content })
export const fetchTransactions = (date) =>
  http.get('/api/admin/transactions', { params: { date } })
