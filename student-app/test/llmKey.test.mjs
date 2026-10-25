/**
 * 学生「AI 密钥」单测：`npm test` 即可，不依赖浏览器与 uni 运行时。
 *
 * 锁住产品三条硬要求里能自动验的部分：
 * 1. 密钥只进本地存储 —— 保存、读取、清除、脱敏展示；
 * 2. 密钥只挂请求头 —— 只在 AI 接口上出现，且不出现在 URL 与请求体里；
 * 3. 失效不静默回退 —— 密钥失败要能认出来并给出「清除后改用学校共享」的原话。
 *
 * 这里的 `uni` 是一个假的全局对象：api.js / llmKey.js 都在**调用时**才读 `uni`，
 * 所以测试里先装桩、再调函数就够了，不需要真的跑起小程序。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import { STORAGE_KEYS } from '../src/config/index.js'
import {
  LLM_KEY_HEADER,
  LLM_KEY_INVALID_TEXT,
  LLM_KEY_NOTICE,
  clearLlmKey,
  getLlmKey,
  hasLlmKey,
  isLlmKeyFailure,
  llmKeyFailureText,
  llmKeyHeader,
  llmKeyStatus,
  llmKeyUsageLabel,
  maskLlmKey,
  saveLlmKey,
} from '../src/services/llmKey.js'
import { checkLlmKey, isAiPath, request, uploadFile } from '../src/services/api.js'

/** 一把假的密钥：形状像真的，值本身无所谓。 */
const KEY = 'sk-111122223333444455556666cd34'

/** 装一个假的 uni：内存存储 + 记录所有请求（含请求头、URL、请求体）。 */
function installUniStub(reply) {
  const store = new Map()
  const calls = []
  const respond = reply || (() => ({ statusCode: 200, data: { code: 0, message: 'ok', data: {} } }))
  globalThis.uni = {
    getStorageSync: (key) => (store.has(key) ? store.get(key) : ''),
    setStorageSync: (key, value) => {
      store.set(key, value)
    },
    removeStorageSync: (key) => {
      store.delete(key)
    },
    $emit: () => {},
    request: (options) => {
      calls.push(options)
      options.success(respond(options))
    },
    uploadFile: (options) => {
      calls.push(options)
      options.success({ statusCode: 200, data: JSON.stringify({ code: 0, message: 'ok', data: {} }) })
    },
  }
  return { store, calls }
}

test('保存后能读回，键名走 config 里的 STORAGE_KEYS.llmKey', () => {
  const { store } = installUniStub()
  assert.equal(hasLlmKey(), false, '空存储时不该有密钥')

  const result = saveLlmKey(`  ${KEY}\n`)
  assert.equal(result.ok, true)
  assert.equal(result.key, KEY, '保存时应去掉首尾换行与空格')
  assert.equal(store.get(STORAGE_KEYS.llmKey), KEY, '存的键名不是 config 里的常量')
  assert.equal(store.get('zhiyin_llm_key'), KEY, '键名要和项目既有 zhiyin_* 风格一致')
  assert.equal(getLlmKey(), KEY)
  assert.equal(hasLlmKey(), true)
})

test('明显不对的输入不写存储，并给出可读原因', () => {
  const { store } = installUniStub()
  const cases = [
    ['', /请先粘贴/],
    ['   ', /请先粘贴/],
    ['sk-1', /太短/],
    ['sk-中文密钥不该被接受', /不能用的字符/],
    ['x'.repeat(201), /太长/],
  ]
  for (const [input, pattern] of cases) {
    const result = saveLlmKey(input)
    assert.equal(result.ok, false, `「${input.slice(0, 12)}…」不该被保存`)
    assert.match(result.error, pattern)
    assert.equal(store.has(STORAGE_KEYS.llmKey), false, '不合法的输入写进了存储')
  }
})

test('脱敏展示只露前 4 后 4，短密钥一个字符都不露', () => {
  assert.equal(maskLlmKey(KEY), 'sk-1…cd34')
  assert.equal(maskLlmKey(`  ${KEY}\n`), 'sk-1…cd34', '带空白的粘贴也要正确脱敏')
  assert.equal(maskLlmKey(''), '')
  // 9 位及以下前 4 后 4 会把整把印出来，只给位数
  const short = 'sk-12345'
  const maskedShort = maskLlmKey(short)
  assert.equal(maskedShort.includes('12345'), false, `短密钥被露出来了：${maskedShort}`)
  assert.match(maskedShort, /8 位/)
  // 脱敏结果里不许出现密钥中段
  assert.equal(maskLlmKey(KEY).includes(KEY.slice(4, -4)), false)
})

test('状态行：没填是学校共享，填了是我的密钥，清除后立刻回落', () => {
  installUniStub()
  assert.deepEqual(llmKeyStatus(), {
    mode: 'school',
    own: false,
    masked: '',
    label: '使用学校共享密钥',
  })
  assert.equal(llmKeyUsageLabel(), '学校共享')

  saveLlmKey(KEY)
  const own = llmKeyStatus()
  assert.equal(own.mode, 'own')
  assert.equal(own.own, true)
  assert.equal(own.label, '使用我自己的密钥（sk-1…cd34）')
  assert.equal(llmKeyUsageLabel(), '我的密钥')

  assert.equal(clearLlmKey(), true)
  assert.equal(getLlmKey(), '')
  assert.equal(llmKeyStatus().mode, 'school', '清除后没有回到学校共享')
  assert.equal(llmKeyUsageLabel(), '学校共享')
})

test('请求头只在本地有密钥时出现，且只挂在 AI 接口上', () => {
  const { calls } = installUniStub()
  assert.deepEqual(llmKeyHeader(), {}, '没有密钥时不该组出请求头')

  // 没有密钥：AI 请求也不带你根本没有的钥匙
  request('/api/ai/check', { method: 'POST', data: { file_key: 'fk1' } })
  assert.equal(calls[0].header[LLM_KEY_HEADER], undefined)

  saveLlmKey(KEY)
  request('/api/ai/check', { method: 'POST', data: { file_key: 'fk1' } })
  assert.equal(calls[1].header[LLM_KEY_HEADER], KEY, 'AI 请求没带学生的密钥')

  // 非 AI 接口不带：下单、报价、文件都不需要模型
  request('/api/order/quote', { method: 'POST', data: { files: [] } })
  assert.equal(calls[2].header[LLM_KEY_HEADER], undefined, '报价接口不该看到学生密钥')

  // 路径判定覆盖全部 AI 入口（含还没接页面的校对轮询与 PDF 转 Word）
  for (const path of [
    '/api/ai/check',
    '/api/ai/proofread',
    '/api/ai/proofread/t-1',
    '/api/ai/print-plan',
    '/api/ai/fix',
    '/api/ai/ocr',
    '/api/ai/convert',
    '/api/ai/key-check',
  ]) {
    assert.equal(isAiPath(path), true, `${path} 应当带密钥`)
  }
  for (const path of ['/api/file/upload', '/api/file/page-count', '/api/order/create']) {
    assert.equal(isAiPath(path), false, `${path} 不该带密钥`)
  }
})

test('密钥不出现在 URL 与请求体里，也不会走文件上传通道', async () => {
  const { calls } = installUniStub()
  saveLlmKey(KEY)

  const payload = { file_key: 'fk1', issue_ids: ['a'], file_name: '修复版-x.pdf' }
  await request('/api/ai/fix', { method: 'POST', data: payload })
  await checkLlmKey(KEY)
  await uploadFile('/tmp/a.pdf', 'a.pdf')

  const aiCall = calls[0]
  assert.equal(aiCall.url.includes(KEY), false, 'URL 里出现了密钥')
  assert.equal(JSON.stringify(aiCall.data).includes(KEY), false, '请求体里出现了密钥')
  assert.equal(aiCall.header[LLM_KEY_HEADER], KEY)

  const checkCall = calls[1]
  assert.equal(checkCall.url, '/api/ai/key-check')
  assert.equal(JSON.stringify(checkCall.data ?? '').includes(KEY), false, '检查接口的请求体里出现了密钥')
  assert.equal(checkCall.header[LLM_KEY_HEADER], KEY)

  // 上传通道只做文件落盘，不带密钥（少一个把密钥发出去的口子）
  const uploadCall = calls[2]
  assert.equal(uploadCall.url, '/api/file/upload')
  assert.equal(uploadCall.header[LLM_KEY_HEADER], undefined, '上传通道不该带密钥')
  assert.equal(JSON.stringify(uploadCall.formData || {}).includes(KEY), false)
})

test('「测试连通」测的是输入框里那把，而不是本机存着的那把', async () => {
  const { calls } = installUniStub()
  saveLlmKey(KEY)
  const typed = 'sk-999988887777666655554444ab12'
  await checkLlmKey(typed)
  assert.equal(calls[0].header[LLM_KEY_HEADER], typed, '测试用的是本地存的那把，不是学生刚粘贴的')
  assert.equal(getLlmKey(), KEY, '测试不该改动本地存着的密钥')

  // 不传参数时才退回本机那把
  await checkLlmKey()
  assert.equal(calls[1].header[LLM_KEY_HEADER], KEY)
})

test('密钥失效能认出来，并给出「清除后改用学校共享」的原话', async () => {
  const { calls } = installUniStub(() => ({
    statusCode: 200,
    data: { code: 50051, message: '你提供的密钥无效或已过期', data: null },
  }))
  saveLlmKey(KEY)

  const error = await request('/api/ai/check', { method: 'POST', data: { file_key: 'fk1' } }).then(
    () => null,
    (err) => err
  )
  assert.ok(error, '密钥失效时请求应当失败，不能静默成功')
  assert.equal(calls[0].header[LLM_KEY_HEADER], KEY)
  assert.equal(error.llmKeySent, true, '失败对象没标上「带过密钥」')
  assert.equal(isLlmKeyFailure(error), true, '密钥失效没被认出来')
  const text = llmKeyFailureText(error)
  assert.match(text, /你的密钥不可用/)
  assert.match(text, /清除后改用学校共享/)
  assert.match(text, /密钥无效或已过期/, '没把服务端给的原因带上')
  assert.equal(text.includes(KEY), false, '提示文案里出现了密钥原文')
  // 不静默回退：本地密钥还在，等学生自己决定清不清
  assert.equal(getLlmKey(), KEY)
})

test('HTTP 401/403 直接算密钥被拒；没带密钥的失败不算密钥问题', async () => {
  let stub = installUniStub(() => ({ statusCode: 401, data: { detail: 'Unauthorized' } }))
  saveLlmKey(KEY)
  const rejected = await request('/api/ai/check', { method: 'POST' }).then(
    () => null,
    (err) => err
  )
  assert.equal(rejected.llmKeyRejected, true)
  assert.equal(isLlmKeyFailure(rejected), true)

  // AI 接口失败但服务端说的是文件的事：不许引导学生去清理密钥
  stub = installUniStub(() => ({
    statusCode: 200,
    data: { code: 50043, message: '文档转换失败，请确认文件未加密、未损坏', data: null },
  }))
  saveLlmKey(KEY)
  const fileError = await request('/api/ai/check', { method: 'POST' }).then(
    () => null,
    (err) => err
  )
  assert.equal(isLlmKeyFailure(fileError), false, '文件类失败被误判成密钥失效')

  // 没带密钥的请求失败，更不该提密钥
  clearLlmKey()
  const noKey = await request('/api/ai/check', { method: 'POST' }).then(
    () => null,
    (err) => err
  )
  assert.equal(noKey.llmKeySent, undefined)
  assert.equal(isLlmKeyFailure(noKey), false)
  void stub
})

test('面板说明提到「本地存储」「不上传服务器」「换设备或清缓存会丢」', () => {
  assert.match(LLM_KEY_NOTICE, /本地存储/)
  assert.match(LLM_KEY_NOTICE, /不会上传服务器/)
  assert.match(LLM_KEY_NOTICE, /换设备或清理缓存/)
  assert.match(LLM_KEY_INVALID_TEXT, /清除后改用学校共享/)
})
