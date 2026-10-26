/**
 * 学生端错误码映射单测：跑 `npm test` 即可，不依赖浏览器与 uni 运行时。
 *
 * 这里锁住的是「后端新增的错误码，学生看到的到底是什么话」：
 * - 表里有的码必须给出固定的中文（不能退化成后端原文，两端口径要一致）；
 * - 表里没有的码必须保留后端原文（后端文案本来就是中文，编新词只会误导）；
 * - 连原文都没有时才给兜底，且兜底不能是空串（空消息在页面等于没提示）。
 *
 * 都是**真跑**：把码喂给 explainApiCode() 拿输出，不与源码文本比对。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  ERROR_MESSAGES,
  explainApiCode,
  formatAiErrorList,
  isKnownErrorCode,
} from '../src/utils/errors.js'

/**
 * 本轮验收点名要接的码：
 * 40406 / 40061 / 40062 / 40905 / 40906 / 50041 / 50043 / 50046（原有）
 * + 50035 / 50036 / 50037（学生自带 AI 密钥三兄弟）
 */
const REQUIRED_CODES = [40406, 40061, 40062, 40905, 40906, 50041, 50043, 50046, 50035, 50036, 50037]

test('本轮要求的十一个错误码都有中文映射，且文案非空、互不相同', () => {
  for (const code of REQUIRED_CODES) {
    assert.ok(isKnownErrorCode(code), `${code} 没有映射`)
    // 输入码 → 输出文案：真调函数，不看源码
    const text = explainApiCode(code, '后端原文')
    assert.equal(typeof text, 'string')
    assert.ok(text.length >= 8, `${code} 的文案太短：${text}`)
    assert.ok(text.includes('请') || text.includes('不能') || text.includes('失败'), `${code} 的文案不像可执行的提示：${text}`)
    // 有映射时不应该把后端原文直接透传出去
    assert.notEqual(text, '后端原文', `${code} 走了透传而不是映射`)
  }
  const texts = REQUIRED_CODES.map((code) => ERROR_MESSAGES[code])
  assert.equal(new Set(texts).size, REQUIRED_CODES.length, '有两条码用了同一句话，学生分不清')
})

test('已支付不能自助取消与重复退款是两句不同的话（40905 / 40906）', () => {
  assert.match(explainApiCode(40905), /已支付/)
  assert.match(explainApiCode(40906), /重复|已经登记/)
  assert.notEqual(explainApiCode(40905), explainApiCode(40906))
})

test('金额不合法与金额超限分开说（40061 / 40062）', () => {
  const notPositive = explainApiCode(40061)
  // 40061 一句话要说清两种情形：负数与 0 元。只写「不能为负数」时，老师填 0 会被翻译错。
  assert.match(notPositive, /负数/)
  assert.match(notPositive, /0/)
  assert.match(notPositive, /作废/, '没有告诉学生「确需免单走作废流程」')
  // 与后端 services/pricing.py 的 AMOUNT_NOT_POSITIVE_MESSAGE 逐字一致，改一处就必须改另一处
  assert.equal(notPositive, '金额必须大于 0 分：不能填负数，也不能填 0；确需免单请改走作废流程')

  const tooLarge = explainApiCode(40062)
  assert.match(tooLarge, /超过|上限/)
  assert.notEqual(notPositive, tooLarge)
})

test('学生自带密钥三个码各自说清怎么办（50035 / 50036 / 50037）', () => {
  // 50035：密钥不可用 —— 必须给出「清除后改用学校共享」这个出口
  assert.match(explainApiCode(50035), /密钥不可用/)
  assert.match(explainApiCode(50035), /清除后改用学校共享/)
  // 50036：没填或格式不对
  assert.match(explainApiCode(50036), /密钥/)
  assert.match(explainApiCode(50036), /粘贴|清除/)
  // 50037：学校关闭了自带密钥，填了也不生效
  assert.match(explainApiCode(50037), /关闭|学校共享/)
  // 三句互不相同，学生能分辨自己是哪一种
  const keyTexts = [50035, 50036, 50037].map((code) => explainApiCode(code))
  assert.equal(new Set(keyTexts).size, 3)
})

test('转换类三个码都能指出下一步怎么办（50041 / 50043 / 50046）', () => {
  assert.match(explainApiCode(50041), /超时/)
  assert.match(explainApiCode(50043), /加密|损坏|格式/)
  assert.match(explainApiCode(50046), /排队|稍/)
})

test('未收录的码保留后端原文，不编新词', () => {
  const raw = '库存不足，请联系店长'
  assert.equal(explainApiCode(49999, raw), raw)
  assert.equal(isKnownErrorCode(49999), false)
  // 字符串形式的码（部分代理会把 code 变成字符串）也要认
  assert.equal(explainApiCode('40406', '后端原文'), ERROR_MESSAGES[40406])
})

test('后端原文为空时给兜底，不返回空消息', () => {
  const text = explainApiCode(49999, '')
  assert.ok(text.length > 0)
  assert.equal(explainApiCode(undefined, undefined), '请求失败，请稍后重试')
})

test('formatAiErrorList 把降级错误收成一行：有错才非空，无错必须空串', () => {
  // 打印方案：后端 errors[]，句子里已经带了「密钥不可用怎么办」
  assert.equal(
    formatAiErrorList(['打印方案：你填写的 AI 密钥不可用，请在设置里清除它后改用学校共享密钥再试']),
    '打印方案：你填写的 AI 密钥不可用，请在设置里清除它后改用学校共享密钥再试'
  )
  // 体检：后端 ai_errors[] 有多条时用中文分号连起来（页面一行读完）
  assert.equal(formatAiErrorList(['格式合规校验：超时', '版式问题诊断：超时']), '格式合规校验：超时；版式问题诊断：超时')
  // 正常出方案 / 体检没报错时必须是空串：页面靠它决定显不显示提示区
  assert.equal(formatAiErrorList([]), '')
  assert.equal(formatAiErrorList(undefined), '')
  assert.equal(formatAiErrorList(null), '')
  // 后端降级时可能给空串或非字符串，不能让页面显示「; ;」
  assert.equal(formatAiErrorList(['', '  ', null, 42]), '')
  assert.equal(formatAiErrorList([' 超时 ', '', undefined]), '超时')
  // 传进来不是数组（老缓存里的字段被写成字符串）也不许抛
  assert.equal(formatAiErrorList('超时'), '')
})
