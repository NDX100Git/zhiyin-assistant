/**
 * 学生端最小单测：跑 `node --test test/` 即可，不依赖浏览器与 uni 运行时。
 *
 * 覆盖两件本轮改动过的事：
 * 1. 本地存储键名统一由 config 的 STORAGE_KEYS 给出（「我的」页不再手写 'zhiyin_user'）；
 * 2. 运行配置的默认值：没配 .env 时接口地址回落到本机后端、轮询间隔是 30 秒。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  API_BASE_URL,
  IS_MINI_PROGRAM,
  POLL_INTERVAL,
  STORAGE_KEYS,
  warnOnPlatformConfig,
} from '../src/config/index.js'

test('本地存储键名只有一个来源，键值保持历史值不变', () => {
  assert.deepEqual(STORAGE_KEYS, {
    token: 'zhiyin_token',
    user: 'zhiyin_user',
    deviceId: 'zhiyin_device_id',
  })
})

test('没配 .env 时接口地址回落到本机后端，且结尾不留斜杠', () => {
  assert.equal(API_BASE_URL, 'http://127.0.0.1:8000')
  assert.ok(!API_BASE_URL.endsWith('/'))
})

test('轮询间隔默认 30 秒，且是数字', () => {
  assert.equal(typeof POLL_INTERVAL, 'number')
  assert.equal(POLL_INTERVAL, 30000)
})

test('小程序自检只告警、不抛错、不阻断开发', () => {
  // 源码里的 `// #ifdef MP-WEIXIN` 是构建期条件编译块，在 node 里不生效，
  // 所以 IS_MINI_PROGRAM 在测试环境会是真的；这里只锁「按平台告警但不抛错」。
  assert.equal(typeof IS_MINI_PROGRAM, 'boolean')
  assert.doesNotThrow(() => warnOnPlatformConfig())
})
