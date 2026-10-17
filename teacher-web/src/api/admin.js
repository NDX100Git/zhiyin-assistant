import http from './http'

export const login = (username, password) =>
  http.post('/api/admin/login', { username, password })

export const fetchQueue = (params) => http.get('/api/admin/queue', { params })
export const fetchSummary = () => http.get('/api/admin/summary')
export const fetchQuote = (orderId) => http.get(`/api/admin/order/${orderId}/quote`)
export const downloadUrl = (orderId) => http.get(`/api/admin/order/${orderId}/download`)
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

export const fetchSessions = (date) => http.get('/api/admin/sessions', { params: { date } })
export const closeSession = (date, session) =>
  http.post('/api/admin/session/close', { date, session })
export const openSession = (date, session) =>
  http.post('/api/admin/session/open', { date, session })
export const publishNotice = (content) => http.post('/api/admin/notice', { content })
export const fetchTransactions = (date) =>
  http.get('/api/admin/transactions', { params: { date } })
