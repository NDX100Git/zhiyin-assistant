/**
 * 老师端错误码映射单测：`npm test` 即可，不依赖浏览器与 Element Plus。
 *
 * 锁三件事：
 * 1. 本轮新增的码都译成可读中文，且各码说法互不相同；
 * 2. 未收录的码保留后端原文（后端文案本来就是中文）；
 * 3. 兜底识别：本轮 http.js 不在文件边界内，业务 code 到不了调用方，
 *    所以错误带上来的只有后端原文——`codeFromMessage()` 要能从原文认出这几个码，
 *    认不出就原样透传。http.js 能带 code 之后删掉这一支，断言 3 一起删。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  ERROR_MESSAGES,
  codeFromMessage,
  explainApiCode,
  explainError,
  isKnownErrorCode,
} from '../src/utils/errors.js'

const REQUIRED_CODES = [40406, 40061, 40062, 40905, 40906, 50041, 50043, 50046]

test('本轮要求的八个错误码都有中文映射，文案非空且互不相同', () => {
  for (const code of REQUIRED_CODES) {
    assert.ok(isKnownErrorCode(code), `${code} 没有映射`)
    const text = explainApiCode(code, '后端原文')
    assert.equal(typeof text, 'string')
    assert.ok(text.length >= 8, `${code} 的文案太短：${text}`)
    assert.notEqual(text, '后端原文', `${code} 走了透传而不是映射`)
  }
  const texts = REQUIRED_CODES.map((code) => ERROR_MESSAGES[code])
  assert.equal(new Set(texts).size, REQUIRED_CODES.length, '有两条码用了同一句话')
})

test('金额类错误分开说：不合法 / 超出范围（40061 / 40062）', () => {
  assert.match(explainApiCode(40061), /负数|不合法/)
  assert.match(explainApiCode(40062), /超过|上限/)
  assert.notEqual(explainApiCode(40061), explainApiCode(40062))
})

test('未收录的码与空消息：原文优先，兜底不返回空串', () => {
  assert.equal(explainApiCode(49999, '代理返回了看不懂的话'), '代理返回了看不懂的话')
  assert.equal(explainApiCode('40406', '后端原文'), ERROR_MESSAGES[40406])
  assert.equal(explainApiCode(undefined, undefined), '请求失败，请稍后重试')
  assert.ok(explainApiCode(49999, '').length > 0)
})

test('后端原文能认出本轮这些码（http.js 暂时丢掉 code 时的兜底）', () => {
  const cases = [
    ['该订单的文件已过期清理，请让学生重新上传', 40406],
    ['退款金额不能为负数', 40061],
    ['金额不能为负数', 40061],
    ['退款金额不能超过已收的 300 分', 40062],
    ['金额 99999 分明显高于系统计价 630 分', 40062],
    ['订单已支付，不能自助取消；如需退款请联系老师处理', 40905],
    ['该订单已登记过退款，请勿重复操作', 40906],
    ['该订单已登记过现场收款，请刷新后重试', 40906],
    ['文档转换超时，请稍后重试', 50041],
    ['PDF 转 Word 超时（超过 60 秒），请先把 PDF 拆小或压缩后再试', 50043],
    ['表格文件打不开，可能已损坏或不是 Excel 格式', 50043],
    ['文档转换排队已满（同时最多 2 个），等了 20 秒仍没有空位，请稍后重试或改上传 PDF', 50046],
  ]
  for (const [message, expected] of cases) {
    assert.equal(codeFromMessage(message), expected, `认错码：${message}`)
  }
  assert.equal(codeFromMessage('随便一句话'), 0)
  assert.equal(codeFromMessage(''), 0)
})

test('explainError 带上 code 时按码译，只有原文时按原文认，都没有时给兜底', () => {
  const withCode = Object.assign(new Error('任意后端原文'), { code: 40906 })
  const explained = explainError(withCode)
  assert.equal(explained.message, ERROR_MESSAGES[40906])
  assert.equal(explained.code, 40906)
  assert.notEqual(explained, withCode, '应返回新错误，不能就地改掉调用方的错误对象')

  const onlyMessage = explainError(new Error('订单已支付，不能自助取消；如需退款请联系老师处理'))
  assert.equal(onlyMessage.message, ERROR_MESSAGES[40905])
  assert.equal(onlyMessage.code, 40905)

  const unknown = explainError(new Error('库存不足，请联系店长'))
  assert.equal(unknown.message, '库存不足，请联系店长')
  assert.equal(unknown.code, undefined, '认不出的原文不该硬塞一个码')

  assert.equal(explainError(new Error('')).message, '请求失败，请稍后重试')
  assert.equal(explainError(undefined).message, '请求失败，请稍后重试')
})
