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
 *
 * 学生「AI 密钥」三个码（50035 / 50036 / 50037）也在这里定义：判定「密钥坏了」
 * 一律看码，不看后端措辞（见 services/llmKey.js 的 isLlmKeyFailure）。
 */

/** 后端业务码 → 提示文案。键是数字码，值是给学生看的一句话。 */
export const ERROR_MESSAGES = {
  // 文件已被定时清理：文件对象没了，订单行还在
  40406: '这个订单的文件已被服务器过期清理，请重新上传文件后再打印',
  // 金额不合法 / 超出允许范围（改价超过系统价上限、退款超过已收）。
  // 负数与 0 元共用这个码：文案必须把两种情形都说清，与后端
  // services/pricing.py 的 AMOUNT_NOT_POSITIVE_MESSAGE 逐字一致。
  40061: '金额必须大于 0 分：不能填负数，也不能填 0；确需免单请改走作废流程',
  40062: '金额超出允许范围：退款不能超过已收金额，改价不能高于系统价的合理上限',
  // 已支付订单不可自助取消 / 已登记过收款、退款
  40905: '订单已支付，不能自助取消；如需退款请联系文印室老师',
  40906: '这笔已经登记过收款或退款了，请勿重复操作，刷新后再看',
  // 文档转换：超时、失败、排队满
  50041: '文档转换超时，请稍后重试；文件太大请先拆分或压缩后再传',
  50043: '文档转换失败，请确认文件未加密、未损坏、格式受支持',
  50046: '服务器正在集中转换文档，排队已满，请稍等一会儿再试或改传 PDF',
  // 学生自带的 AI 密钥不可用（失效、额度用完、被模型端拒）。
  // 「不静默回退学校共享密钥」这条产品要求就是从这句提示落地的：
  // 学生得知道现在烧的是自己的钥匙，并且知道去哪儿清掉它。
  50035: '你的密钥不可用，请在「我的」→「AI 密钥」里清除后改用学校共享密钥',
  // 请求头里没带密钥，或密钥格式明显不对（中间有空格、长度不够）
  50036: '没读到可用的密钥：请重新复制粘贴，或清除后改用学校共享密钥',
  // 运维关掉了「学生自带密钥」这个开关，填了也不生效
  50037: '学校已关闭学生自带密钥，当前一律用学校共享密钥，请清除自己填的那把',
}

/**
 * 「你的密钥不可用」这一句的标准文案。
 *
 * 只有这一份：错误码 50035 的翻译与页面横幅都从这里取，别处不许另编一套说法。
 */
export const LLM_KEY_INVALID_TEXT = ERROR_MESSAGES[50035]

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

/**
 * 把后端降级返回的错误文案列表收成一行给学生看的提示。
 *
 * 两个接口都**故意**不把 AI 出错当失败：
 * - `/api/ai/check` 成功返回体检报告，模型挂了的信息放在 `report.ai_errors[]`；
 * - `/api/ai/print-plan` 成功返回基础方案，模型挂了的信息放在 `plan.errors[]`。
 * 这是有意的降级（学生照样能下单），但文案必须有个出口，否则「你的密钥不可用」
 * 只会在体检那一屏出现，点打印方案时又变成哑巴。
 *
 * @param {unknown} list 后端给的 `errors` / `ai_errors` 字段
 * @returns {string} 没有错误时返回空串（页面据此不显示提示区）
 */
export function formatAiErrorList(list) {
  if (!Array.isArray(list)) return ''
  const parts = list
    .map((item) => (typeof item === 'string' ? item.trim() : ''))
    .filter((item) => item.length > 0)
  return parts.join('；')
}
