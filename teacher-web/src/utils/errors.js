/**
 * 后端业务错误码 → 可读中文（老师端）。
 *
 * 后端所有接口都返回 HTTP 200 + `{code, message, data}`，对错只能按业务 code 判断。
 * 这里把**本轮新增/新暴露的码**统一译成柜台能照做的中文；表里没有的码保留后端原文
 * （后端文案本来就是中文，硬翻成第二种说法只会让人以为出了别的问题）。
 *
 * 错误对象上的 `code` 由 `src/api/http.js` 的响应拦截器负责带上（`error.code =
 * body.code`）。上一轮 http.js 不在文件边界内，业务码到不了调用方，只能留一条
 * 「按后端原文反查」的 `codeFromMessage()` 兜底；本轮 http.js 已经能带码，那条兜底
 * 连同它的 `MESSAGE_CODES` 表一起删掉了——按文案反查只要后端改一个字就失效，
 * 而按码翻译是确定的。**调用方只需要解释 `error.code`，不要再对着 message 猜。**
 *
 * 学生端有一份同样的表（student-app/src/utils/errors.js），两个应用分开构建，
 * 没有共享模块的通道，改动时两边一起改，`npm test` 各有一条用例锁住码表。
 */

/** 后端业务码 → 提示文案。 */
export const ERROR_MESSAGES = {
  // 文件已被定时清理：对象没了，订单行还在，下载/打印都会失败
  40406: '这单的文件已被服务器过期清理，请让学生重新上传文件后再打印',
  // 金额不合法 / 超出允许范围
  40061: '金额不合法：不能为负数，请重新填写',
  40062: '金额超出允许范围：退款不能超过已收金额，改价不能高于系统价的合理上限',
  // 已支付不可自助取消 / 已登记过收款、退款
  40905: '订单已支付，不能自助取消；请在后台关单并按已收金额登记退款',
  40906: '这笔已经登记过收款或退款了，请勿重复操作，刷新后再看',
  // 文档转换：超时、失败、排队满
  50041: '文档转换超时，请稍后重试；文件太大让学生拆分或压缩后再传',
  50043: '文档转换失败：文件可能已损坏、加密或格式不受支持，让学生重传',
  50046: '服务器文档转换排队已满，请稍等一会儿再试，或让学生改传 PDF',
}

/**
 * 把业务码译成中文。
 *
 * @param {number|string} code 后端业务码
 * @param {string} [backendMessage] 后端原文，未收录的码原样返回它
 * @returns {string} 一定非空
 */
export function explainApiCode(code, backendMessage) {
  const numeric = Number(code)
  const known = ERROR_MESSAGES[numeric]
  if (known) return known
  const raw = typeof backendMessage === 'string' ? backendMessage.trim() : ''
  return raw || '请求失败，请稍后重试'
}

/**
 * 把请求层抛出来的错误换成「可读中文」的新错误。
 *
 * - 错误对象上有业务码（http.js 已经带上）：按码译；
 * - 没有码：后端原文原样保留（它本来就是中文），不猜码；
 * - 完全没有信息：给一句兜底，不留空 message（界面上会变成空的红色提示）。
 *
 * @param {Error & {code?: number|string}} error
 * @returns {Error & {code?: number}}
 */
export function explainError(error) {
  const message = (error && error.message) || ''
  const fromCode = Number(error && error.code)
  const code = Number.isFinite(fromCode) && fromCode ? fromCode : 0
  const explained = new Error(explainApiCode(code, message))
  if (code) explained.code = code
  return explained
}

/** 后端返回体是不是「已知业务错误码」，页面可据此决定要不要给重试入口。 */
export function isKnownErrorCode(code) {
  return Object.prototype.hasOwnProperty.call(ERROR_MESSAGES, Number(code))
}
