/**
 * 后端业务错误码 → 可读中文（学生端）。
 *
 * 后端所有接口都返回 HTTP 200 + `{code, message, data}`：网络层看不出对错，
 * 只能按业务 code 判断。这里只做一件事——把**本轮新增/新暴露的码**统一译成
 * 学生能照做的中文；表里没有的码一律保留后端原文（后端文案本来就是中文，
 * 翻译只会引入第二种说法）。
 *
 * 老师端有一份同样的表（teacher-web/src/utils/errors.js）。两个应用分开构建，
 * 没有共享模块的通道，改动时两边一起改，`npm test` 里各有一条用例锁住码表。
 */

/** 后端业务码 → 提示文案。键是数字码，值是给学生看的一句话。 */
export const ERROR_MESSAGES = {
  // 文件已被定时清理：文件对象没了，订单行还在
  40406: '这个订单的文件已被服务器过期清理，请重新上传文件后再打印',
  // 金额不合法 / 超出允许范围（改价超过系统价上限、退款超过已收）
  40061: '金额不合法：不能为负数，请重新填写',
  40062: '金额超出允许范围：退款不能超过已收金额，改价不能高于系统价的合理上限',
  // 已支付订单不可自助取消 / 已登记过收款、退款
  40905: '订单已支付，不能自助取消；如需退款请联系文印室老师',
  40906: '这笔已经登记过收款或退款了，请勿重复操作，刷新后再看',
  // 文档转换：超时、失败、排队满
  50041: '文档转换超时，请稍后重试；文件太大请先拆分或压缩后再传',
  50043: '文档转换失败，请确认文件未加密、未损坏、格式受支持',
  50046: '服务器正在集中转换文档，排队已满，请稍等一会儿再试或改传 PDF',
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
  // 未知码保留后端原文：它比「请求失败」有用得多
  return raw || '请求失败，请稍后重试'
}

/** 后端返回体是不是「已知业务错误码」，页面可据此决定要不要给重试入口。 */
export function isKnownErrorCode(code) {
  return Object.prototype.hasOwnProperty.call(ERROR_MESSAGES, Number(code))
}
