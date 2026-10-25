/**
 * 学生端最小单测：跑 `node --test test/` 即可，不依赖浏览器与 uni 运行时。
 *
 * 覆盖几件与配置相关的事：
 * 1. 本地存储键名统一由 config 的 STORAGE_KEYS 给出（「我的」页不再手写 'zhiyin_user'）；
 * 2. 运行配置的兜底：没配 .env 时接口地址是**空串**（同源相对路径），
 *    不许退回本机回环地址——线上漏配不能把请求打到用户自己电脑上；
 * 3. 轮询间隔默认 30 秒。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  API_BASE_URL,
  IS_MINI_PROGRAM,
  POLL_INTERVAL,
  STORAGE_KEYS,
  warnOnPlatformConfig,
} from '../src/config/index.js'

const APP_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

test('本地存储键名只有一个来源，键值保持历史值不变', () => {
  assert.deepEqual(STORAGE_KEYS, {
    token: 'zhiyin_token',
    user: 'zhiyin_user',
    deviceId: 'zhiyin_device_id',
  })
})

test('没配 .env 时接口地址是空串，走同源相对路径且不退回回环地址', () => {
  assert.equal(API_BASE_URL, '')
  assert.ok(!API_BASE_URL.endsWith('/'))
  // 第三种错误的写法也不许出现：写死本机地址会让线上构建全站打到自己电脑
  assert.ok(!/127\.0\.0\.1|localhost/i.test(API_BASE_URL))
})

test('.env.production 留空、注释、代码兜底三者说法一致', () => {
  const envFile = fs.readFileSync(path.join(APP_ROOT, '.env.production'), 'utf8')
  const baseLine = envFile
    .split(/\r?\n/)
    .find((line) => /^\s*VITE_API_BASE_URL\s*=/.test(line))
  assert.ok(baseLine, '.env.production 里没有 VITE_API_BASE_URL')
  assert.equal(baseLine.split('=')[1].trim(), '', '.env.production 不该写死接口地址')
  // 注释里承诺“留空走相对路径”，并且明说不会退回回环地址
  assert.match(envFile, /相对路径/)
  assert.match(envFile, /不会.{0,4}退回|不退回/)
  assert.ok(!/192\.168\.\d+\.\d+/.test(envFile), '.env.production 不该出现具体内网 IP')

  const source = fs.readFileSync(path.join(APP_ROOT, 'src', 'config', 'index.js'), 'utf8')
  assert.match(source, /const fallbackBase = ''/, '代码兜底不是空串，注释又对不上')
  assert.ok(!/fallbackBase\s*=\s*'http/.test(source), '代码兜底还写着绝对地址')
})

test('README 的部署说明不再教人写死 192.168.1.100', () => {
  const readme = fs.readFileSync(path.join(APP_ROOT, 'README.md'), 'utf8')
  assert.ok(!readme.includes('192.168.1.100'), 'README 里还留着写死的内网地址示例')
  assert.match(readme, /留空就是同源相对路径/)
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
