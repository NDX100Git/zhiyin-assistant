/**
 * 学生端错误码映射单测：跑 `npm test` 即可，不依赖浏览器与 uni 运行时。
 *
 * 这里锁住的是「后端新增的错误码，学生看到的到底是什么话」：
 * - 表里有的码必须给出固定的中文（不能退化成后端原文，两端口径要一致）；
 * - 表里没有的码必须保留后端原文（后端文案本来就是中文，编新词只会误导）；
 * - 连原文都没有时才给兜底，且兜底不能是空串（空消息在页面等于没提示）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import { ERROR_MESSAGES, explainApiCode, isKnownErrorCode } from '../src/utils/errors.js'

/** 本轮验收点名要接的码：40406 / 40061 / 40062 / 40905 / 40906 / 50041 / 50043 / 50046 */
const REQUIRED_CODES = [40406, 40061, 40062, 40905, 40906, 50041, 50043, 50046]

test('本轮要求的八个错误码都有中文映射，且文案非空、互不相同', () => {
  for (const code of REQUIRED_CODES) {
    assert.ok(isKnownErrorCode(code), `${code} 没有映射`)
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
  assert.match(explainApiCode(40061), /负数|不合法/)
  assert.match(explainApiCode(40062), /超过|上限/)
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
