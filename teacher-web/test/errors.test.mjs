/**
 * 老师端错误码映射单测：`npm test` 即可，不依赖浏览器与 Element Plus 的真实渲染。
 *
 * 锁四件事：
 * 1. 本轮新增的码都译成可读中文，且各码说法互不相同（40061 的文案要与后端
 *    AMOUNT_NOT_POSITIVE_MESSAGE 同口径：负数与 0 元都要拒，免单走作废）；
 * 2. 未收录的码保留后端原文（后端文案本来就是中文）；
 * 3. **错误对象真的带着业务码**：驱动 `src/api/http.js` 里真实的响应拦截器逻辑，
 *    确认它抛出的 Error 上 `error.code` 就是后端业务码，且 `explainError` 直接按码译；
 * 4. 按后端原文反查码的兜底已经删掉（上一轮 http.js 拿不到 code 时的临时手段）：
 *    只有原文、没有码时不再硬猜一个码出来。
 *
 * 拦截器的提示器可以注入（`createResponseHandlers({ notify })`），所以这里不需要
 * 拉起 Vue 渲染，只补 localStorage / window.location 两个最小全局对象。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import * as errorsModule from '../src/utils/errors.js'
import {
  ERROR_MESSAGES,
  explainApiCode,
  explainError,
  isKnownErrorCode,
} from '../src/utils/errors.js'

const REQUIRED_CODES = [40406, 40061, 40062, 40905, 40906, 50041, 50043, 50046]

// ---------------------------------------------------------------- 最小浏览器环境
const TOKEN_KEY = 'zhiyin_admin_token'
const USER_KEY = 'zhiyin_admin_user'

const storage = new Map()
globalThis.localStorage = {
  getItem: (key) => (storage.has(key) ? storage.get(key) : null),
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
  clear: () => storage.clear(),
}
const locationStub = { hash: '#/dashboard', href: 'http://localhost/' }
globalThis.window = {
  location: locationStub,
  navigator: { userAgent: 'node' },
  addEventListener() {},
  removeEventListener() {},
}

const { createResponseHandlers } = await import('../src/api/http.js')

/** 一份拦截器 + 一个收集提示文案的假提示器。 */
function makeHandlers() {
  const notices = []
  const handlers = createResponseHandlers({ notify: (message) => notices.push(message) })
  return { handlers, notices }
}

/** 断言一次 reject，并把错误对象交回来。 */
async function rejectedWith(promise, label) {
  try {
    await promise
  } catch (error) {
    return error
  }
  assert.fail(`${label} 必须 reject`)
}

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
  // 40061 现在与后端 pricing.py 的 AMOUNT_NOT_POSITIVE_MESSAGE 对齐：负数与 0 元共用
  // 一个码，只说「不能为负数」会让老师把 300 敲成 0 时看不懂为什么被拒。
  const notPositive = explainApiCode(40061)
  assert.match(notPositive, /大于 0/)
  assert.match(notPositive, /不能填负数/)
  assert.match(notPositive, /也不能填 0/)
  assert.match(notPositive, /作废/, '免单要说清只能走作废流程')
  assert.match(explainApiCode(40062), /超过|上限/)
  assert.notEqual(explainApiCode(40061), explainApiCode(40062))
})

test('未收录的码与空消息：原文优先，兜底不返回空串', () => {
  assert.equal(explainApiCode(49999, '代理返回了看不懂的话'), '代理返回了看不懂的话')
  assert.equal(explainApiCode('40406', '后端原文'), ERROR_MESSAGES[40406])
  assert.equal(explainApiCode(undefined, undefined), '请求失败，请稍后重试')
  assert.ok(explainApiCode(49999, '').length > 0)
})

test('http.js 把后端业务码带到错误对象上，按码翻译直接生效', async () => {
  const { handlers, notices } = makeHandlers()
  const backendMessage = '该订单已登记过退款，请勿重复操作'
  const rejected = await rejectedWith(
    handlers.onFulfilled({ data: { code: 40906, message: backendMessage } }),
    '业务错误',
  )

  assert.equal(rejected.code, 40906, '业务码没有带出来')
  assert.equal(rejected.message, backendMessage, '后端原文应保留')
  assert.ok(rejected.response, '应保留原始响应，便于排查')
  assert.deepEqual(notices, [backendMessage], '仍然要给学生/老师弹一次提示')

  // 调用方无需再按文案反查：拿到错误就能直接按码译
  const explained = explainError(rejected)
  assert.equal(explained.message, ERROR_MESSAGES[40906])
  assert.equal(explained.code, 40906)
})

test('登录失效码：清掉本地凭证并跳登录页，错误同样带码', async () => {
  for (const code of [40100, 40101, 40102, 40300]) {
    const { handlers } = makeHandlers()
    storage.clear()
    storage.set(TOKEN_KEY, 'stale-token')
    storage.set(USER_KEY, '{"username":"admin"}')
    locationStub.hash = '#/dashboard'

    const rejected = await rejectedWith(
      handlers.onFulfilled({ data: { code, message: '登录已失效' } }),
      `${code} 登录失效`,
    )
    assert.equal(rejected.code, code)
    assert.equal(storage.has(TOKEN_KEY), false, `${code} 时没清 token`)
    assert.equal(storage.has(USER_KEY), false, `${code} 时没清用户信息`)
    assert.equal(locationStub.hash, '#/login')
  }
})

test('传输层失败时也带上业务码；没有业务码就不硬塞', async () => {
  const { handlers } = makeHandlers()

  const withCode = await rejectedWith(
    handlers.onRejected({
      message: 'Request failed with status code 502',
      response: { status: 502, data: { code: 50046, message: '文档转换排队已满' } },
    }),
    '网关带了业务码',
  )
  assert.equal(withCode.code, 50046)
  assert.equal(withCode.message, '文档转换排队已满')

  const networkOnly = await rejectedWith(
    handlers.onRejected({ message: 'Network Error' }),
    '纯网络错误',
  )
  assert.equal(networkOnly.code, undefined, '没有业务码时不能编一个出来')
  assert.equal(networkOnly.message, 'Network Error')
})

test('成功响应照旧只把 data 交出去', () => {
  const { handlers, notices } = makeHandlers()
  assert.deepEqual(handlers.onFulfilled({ data: { code: 0, data: { ok: 1 } } }), { ok: 1 })
  assert.deepEqual(notices, [], '成功不该弹提示')
})

test('按后端原文反查码的兜底已删除，只有原文时不猜码', () => {
  assert.equal(
    'codeFromMessage' in errorsModule,
    false,
    'codeFromMessage 应随本轮 http.js 带码一起删掉',
  )
  const onlyMessage = explainError(new Error('该订单已登记过退款，请勿重复操作'))
  assert.equal(onlyMessage.code, undefined, '只有原文时不该反查出一个码')
  assert.equal(onlyMessage.message, '该订单已登记过退款，请勿重复操作')
})

test('explainError 带上 code 时按码译，完全没有信息时给兜底', () => {
  const withCode = Object.assign(new Error('任意后端原文'), { code: 40906 })
  const explained = explainError(withCode)
  assert.equal(explained.message, ERROR_MESSAGES[40906])
  assert.equal(explained.code, 40906)
  assert.notEqual(explained, withCode, '应返回新错误，不能就地改掉调用方的错误对象')

  const unknown = explainError(Object.assign(new Error('库存不足，请联系店长'), { code: 49999 }))
  assert.equal(unknown.message, '库存不足，请联系店长')
  assert.equal(unknown.code, 49999)

  assert.equal(explainError(new Error('')).message, '请求失败，请稍后重试')
  assert.equal(explainError(undefined).message, '请求失败，请稍后重试')
})
