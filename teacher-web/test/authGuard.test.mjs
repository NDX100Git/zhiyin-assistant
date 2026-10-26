/**
 * 老师端登录凭证守卫单测：`npm test` 即可，不依赖浏览器。
 *
 * 被测对象是真实的 `src/router/index.js`（守卫就在里面）：它只做一件前端能做的事——
 * 本地 token 必须是三段式 JWT 且 exp 未过期，否则清掉 token + 用户信息并跳登录页。
 * 手工往 localStorage 写一个 "1" 就能进后台，是这条守卫要挡住的经典事故。
 *
 * 做法：先给 Node 补最小 DOM（vue-router 的 hash history 建实例时要摸 window/document），
 * 再动态 import 路由模块，然后真的 push 一次路由，用自己注册的 beforeEach **观察**
 * 守卫的跳转决定（守卫按注册顺序执行，所以看到的是它改过的目标），最后断言键被清掉。
 * 自己的守卫里返回 false，是为了别让 vue-router 真的去加载 .vue 组件。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const TOKEN_KEY = 'zhiyin_admin_token'
const USER_KEY = 'zhiyin_admin_user'

// ---------------------------------------------------------------- 最小 DOM
const storage = new Map()
globalThis.localStorage = {
  getItem: (key) => (storage.has(key) ? storage.get(key) : null),
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
  clear: () => storage.clear(),
  key: (index) => [...storage.keys()][index] ?? null,
  get length() {
    return storage.size
  },
}

const locationStub = {
  href: 'http://localhost/',
  pathname: '/',
  search: '',
  hash: '#/login',
  origin: 'http://localhost',
  protocol: 'http:',
  host: 'localhost',
  hostname: 'localhost',
  assign() {},
  replace() {},
}
const historyStub = {
  state: {},
  length: 1,
  pushState() {},
  replaceState() {},
  go() {},
  back() {},
  forward() {},
}
globalThis.window = {
  location: locationStub,
  history: historyStub,
  addEventListener() {},
  removeEventListener() {},
  navigator: { userAgent: 'node' },
  matchMedia: () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  }),
}
globalThis.location = locationStub
globalThis.history = historyStub
globalThis.document = {
  querySelector: () => null,
  addEventListener() {},
  removeEventListener() {},
  createElement: () => ({ style: {}, setAttribute() {}, appendChild() {} }),
  baseURI: 'http://localhost/',
  documentElement: { style: {} },
}

const { default: router } = await import('../src/router/index.js')

/** 造一个 JWT：header.payload.signature，payload 里放 exp（秒）。 */
function makeToken(expSeconds) {
  const encode = (value) =>
    Buffer.from(JSON.stringify(value)).toString('base64').replace(/=+$/, '')
  return `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({ sub: 'admin', exp: expSeconds })}.sig`
}

/**
 * 走一次真实路由，返回守卫最终把我们送到哪里。
 *
 * 观察用的守卫只注册一次（vue-router 没有 eject，注册多次会互相叠），
 * 它排在业务守卫之后：业务守卫返回重定向时，这里收到的 to 已经是目标路由
 * （例如 /login），这就是它的决定。返回 false 是为了阻止后续真实加载 .vue 组件。
 */
let landed = null
router.beforeEach((to) => {
  landed = to.fullPath
  return false
})

async function whereDoesGuardSend(path) {
  landed = null
  await router.push(path).catch(() => {})
  return landed
}

test('没有 token：进工作台被送回登录页，且不种任何凭证', async () => {
  storage.clear()
  const landed = await whereDoesGuardSend('/dashboard')
  assert.equal(landed, '/login')
  assert.equal(storage.has(TOKEN_KEY), false)
})

test('结构不对的 token（手工写的 "1"）：清掉并跳登录页', async () => {
  storage.clear()
  storage.set(TOKEN_KEY, '1')
  storage.set(USER_KEY, JSON.stringify({ username: 'admin' }))
  const landed = await whereDoesGuardSend('/dashboard')
  assert.equal(landed, '/login', '伪造 token 竟然放行了')
  assert.equal(storage.has(TOKEN_KEY), false, '结构不对的 token 没被清掉')
  assert.equal(storage.has(USER_KEY), false, '结构不对时用户信息没被清掉')
})

test('已经过期的 token：清掉并跳登录页', async () => {
  storage.clear()
  storage.set(TOKEN_KEY, makeToken(Math.floor(Date.now() / 1000) - 60))
  storage.set(USER_KEY, '{}')
  const landed = await whereDoesGuardSend('/dashboard')
  assert.equal(landed, '/login', '过期 token 竟然放行了')
  assert.equal(storage.has(TOKEN_KEY), false, '过期 token 没被清掉')
  assert.equal(storage.has(USER_KEY), false, '过期时用户信息没被清掉')
})

test('payload 不是合法 JSON 的 token：同样清掉并跳登录页', async () => {
  storage.clear()
  storage.set(TOKEN_KEY, 'aaa.bbb.ccc')
  const landed = await whereDoesGuardSend('/dashboard')
  assert.equal(landed, '/login')
  assert.equal(storage.has(TOKEN_KEY), false)
})

test('未过期且结构正确的 token：放行，凭证原样保留', async () => {
  storage.clear()
  const token = makeToken(Math.floor(Date.now() / 1000) + 3600)
  storage.set(TOKEN_KEY, token)
  storage.set(USER_KEY, JSON.stringify({ username: 'admin' }))
  const landed = await whereDoesGuardSend('/dashboard')
  assert.equal(landed, '/dashboard', '有效 token 被挡住了')
  assert.equal(storage.get(TOKEN_KEY), token, '有效 token 不该被动')
  assert.equal(storage.get(USER_KEY), JSON.stringify({ username: 'admin' }))
})
