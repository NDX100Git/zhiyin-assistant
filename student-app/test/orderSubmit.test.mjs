/**
 * 学生端下单页两条关键行为的单测：`npm test` 即可，不依赖浏览器与 uni 运行时。
 *
 * 为什么从源码取表达式：
 * 页面是 `order.vue`（script setup + 模板），Node 里没法直接 import 一个 .vue，
 * 而这两条守卫**就写在页面里**——照抄一份到测试里等于测副本。所以这里从页面源码
 * 里取出真实的 `:disabled` 表达式与 `retryQuote` 函数体，在 Node 里求值/断言，
 * 页面一改守卫条件，这里立刻红。
 *
 * 锁住三件事：
 * 1. 已下单（created 非空）后「确认下单」不能再点（防连点出第二张单）；
 * 2. 报价失败（quoteError 非空）时按钮锁住，不许拿旧金额下单；
 * 3. 报价失败后有「重新报价」出口，重试清掉 quoteError 后按钮解锁。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const APP_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const ORDER_PAGE = path.join(APP_ROOT, 'src', 'pages', 'order', 'order.vue')
const source = fs.readFileSync(ORDER_PAGE, 'utf8')

/** 页面上「确认下单」按钮里真正生效的守卫表达式。 */
function orderGuardExpression() {
  const button = source.match(/<button\b[^>]*@click="createOrder"[^>]*>/)
  assert.ok(button, 'order.vue 里找不到「确认下单」按钮')
  const disabled = button[0].match(/:disabled="([^"]+)"/)
  assert.ok(disabled, '「确认下单」按钮没有 :disabled 守卫')
  return disabled[1]
}

/** 把表达式编译成「给定状态 → 按钮是否禁用」的判定函数。 */
function compileGuard(expression) {
  const names = [...new Set([...expression.matchAll(/[A-Za-z_$][\w$]*/g)].map((item) => item[0]))]
  // 用解构把状态摊进作用域，等价于模板里读 setup 顶层变量
  return new Function('state', `const { ${names.join(', ')} } = state; return Boolean(${expression});`)
}

/** 一个「报价成功、参数齐、还没下单」的基准状态。 */
function readyState(overrides = {}) {
  return {
    busy: false,
    plan: { sheets_needed: 12 },
    quoteError: '',
    quoting: false,
    created: null,
    ...overrides,
  }
}

const guard = compileGuard(orderGuardExpression())

test('一切就绪时「确认下单」可以点', () => {
  assert.equal(guard(readyState()), false)
})

test('已下单后「确认下单」不能再点（防连点出第二张订单）', () => {
  assert.equal(guard(readyState({ created: { id: 1, order_no: '20251025-001' } })), true)
  // 即使报价与参数都正常，已下单也不能再点
  assert.equal(guard(readyState({ created: { id: 1 } })), true)
})

test('报价失败时按钮锁住，不许拿上一次的旧金额下单', () => {
  assert.equal(guard(readyState({ quoteError: '服务端报价失败：超时' })), true)
  assert.equal(guard(readyState({ quoting: true })), true)
  assert.equal(guard(readyState({ plan: null })), true)
  assert.equal(guard(readyState({ busy: true })), true)
})

test('报价失败后有「重新报价」入口，重试成功后按钮解锁', () => {
  // 1) 模板里必须有一个只在 quoteError 时出现、点它调 retryQuote 的按钮
  const retryButton = source.match(/<button\b[^>]*@click="retryQuote"[^>]*>/)
  assert.ok(retryButton, 'order.vue 里没有「重新报价」按钮')
  assert.match(retryButton[0], /v-if="quoteError"/, '「重新报价」按钮不是靠 quoteError 显示')

  // 2) retryQuote 先清错误再重新报价：成功一次就解锁
  const retryStart = source.indexOf('async function retryQuote')
  assert.ok(retryStart > 0, 'order.vue 里找不到 retryQuote')
  const retryBody = source.slice(retryStart, source.indexOf('\n}', retryStart))
  assert.match(retryBody, /quoteError\.value = ''/, 'retryQuote 没有先清掉错误，按钮不会解锁')
  assert.match(retryBody, /refreshQuote\(/, 'retryQuote 没有重新请求报价')

  // 3) 重试成功后的状态：错误清空、不再请求中、仍未下单 → 按钮可点
  assert.equal(guard(readyState({ quoteError: '', quoting: false })), false)
})

test('createOrder 自己也拦一道：已下单时直接返回，不重复请求', () => {
  const start = source.indexOf('async function createOrder')
  assert.ok(start > 0, 'order.vue 里找不到 createOrder')
  const body = source.slice(start, source.indexOf('\n}', start))
  const guardLine = body.split(/\r?\n/).find((line) => line.includes('return')) || ''
  assert.match(guardLine, /created\.value/, 'createOrder 的早退条件里没有 created，连点会出第二张单')
  assert.match(guardLine, /busy\.value/, 'createOrder 的早退条件里没有 busy')
})
