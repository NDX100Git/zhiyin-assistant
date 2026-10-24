#!/usr/bin/env node
/**
 * 智印助手浏览器端到端回归。
 *
 * 一条命令跑完整闭环，两个视口各跑一遍：
 *   学生登录 → 上传 PDF → 跳过 AI 直接下单 → 模拟支付 → 我的订单 →
 *   订单详情换文件（待老师确认）→ 老师登录 → 队列看到待确认 →
 *   确认换文件 → 下载并确认看到 → 开始打印 → 打印完成 → 核销 →
 *   学生端看到已完成。
 *
 * 接着跑多文件闭环：一次选两份 PDF 下单 → 学生换第二份 → 老师确认 →
 * 逐份下载（每份都要拿到自己的序号与签名链接）→ 学生端看到第二份已更新。
 * 最后跑四文件闭环：桌面点「查看全部」逐份下载，手机看逐份列表与按钮尺寸。
 *
 * 用法：
 *   npm install
 *   node run.mjs                 # 用已经起好的后端、学生端、老师端
 *   node run.mjs --start         # 自动起一套隔离环境（库名以 _e2e 结尾）
 *
 * 失败会点名卡在第几步，并把截图、正文、接口错误写到 e2e/artifacts/<时间>/。
 * 默认地址可用参数覆盖：--student-url --teacher-url --backend-url。
 */

import { spawn, spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import puppeteer from 'puppeteer'

const E2E_DIR = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(E2E_DIR, '..')
const STAMP = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
const ARTIFACTS = path.join(E2E_DIR, 'artifacts', STAMP)
fs.mkdirSync(ARTIFACTS, { recursive: true })

const START = process.argv.includes('--start')
const valueOf = (name, fallback) => {
  const index = process.argv.indexOf(name)
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback
}

const STUDENT_URL = valueOf('--student-url', START ? 'http://127.0.0.1:3100' : 'http://127.0.0.1:5173')
const TEACHER_URL = valueOf('--teacher-url', START ? 'http://127.0.0.1:3101' : 'http://127.0.0.1:5174')
const BACKEND_URL = valueOf('--backend-url', START ? 'http://127.0.0.1:8100' : 'http://127.0.0.1:8000')

const FIXTURES = {
  v1: path.join(E2E_DIR, 'fixtures', 'thesis-v1.pdf'),
  v2: path.join(E2E_DIR, 'fixtures', 'thesis-v2.pdf'),
  v3: path.join(E2E_DIR, 'fixtures', 'thesis-v3.pdf'),
  v4: path.join(E2E_DIR, 'fixtures', 'thesis-v4.pdf'),
}

const VIEWPORTS = [
  { name: '390', width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, mobile: true },
  { name: '1440', width: 1440, height: 900, deviceScaleFactor: 1, isMobile: false, mobile: false },
]

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function readEnvFile(file) {
  if (!fs.existsSync(file)) return {}
  const result = {}
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const match = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/)
    if (match && !line.trim().startsWith('#')) result[match[1]] = match[2]
  }
  return result
}

const backendEnv = readEnvFile(path.join(REPO_ROOT, 'backend', '.env'))
const ADMIN = {
  user: process.env.E2E_ADMIN_USERNAME || backendEnv.ADMIN_USERNAME || 'admin',
  pass: process.env.E2E_ADMIN_PASSWORD || backendEnv.ADMIN_PASSWORD || '',
}

function detectPython() {
  if (process.env.E2E_PYTHON) return process.env.E2E_PYTHON
  const candidates = [
    path.join(REPO_ROOT, '..', '..', 'Temp', 'zhiyin-venv', 'bin', 'python'),
    path.join(REPO_ROOT, 'backend', '.venv', 'bin', 'python'),
    path.join(REPO_ROOT, 'backend', '.venv', 'Scripts', 'python.exe'),
  ]
  return candidates.find((candidate) => fs.existsSync(candidate)) || 'python3'
}

// ---------------------------------------------------------------- 服务编排
const services = []
let stopping = false

function startService(name, command, args, { cwd, env } = {}) {
  const logPath = path.join(ARTIFACTS, `${name}.log`)
  const logFd = fs.openSync(logPath, 'a')
  const child = spawn(command, args, {
    cwd,
    env: { ...process.env, ...env },
    detached: true,
    stdio: ['ignore', logFd, logFd],
  })
  child.on('exit', (code) => {
    if (!stopping) console.error(`[服务] ${name} 提前退出，code=${code}，日志：${logPath}`)
  })
  services.push({ name, child, logPath })
  return child
}

function stopStack() {
  stopping = true
  for (const { child } of services) {
    try {
      process.kill(-child.pid, 'SIGTERM')
    } catch {
      /* 已经退出 */
    }
  }
}

process.on('SIGINT', () => {
  stopStack()
  process.exit(130)
})
process.on('exit', stopStack)

async function waitHttp(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  let lastError = ''
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.ok) return
      lastError = `HTTP ${response.status}`
    } catch (error) {
      lastError = error.message
    }
    await sleep(600)
  }
  throw new Error(`${url} 在 ${Math.round(timeoutMs / 1000)} 秒内没有就绪（${lastError}）`)
}

async function startStack() {
  if (!ADMIN.pass) throw new Error('backend/.env 里没有 ADMIN_PASSWORD，无法登录老师端')
  const python = detectPython()
  const prep = spawnSync(
    python,
    [path.join(E2E_DIR, 'scripts', 'prepare_db.py'), '--backend', path.join(REPO_ROOT, 'backend')],
    { env: { ...process.env, DB_NAME: 'zhiyin_e2e' }, encoding: 'utf8' },
  )
  if (prep.status !== 0) throw new Error(`重建回归库失败：${prep.stderr || prep.stdout}`)
  console.log(`  ${prep.stdout.trim()}`)

  startService(
    'backend',
    python,
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', new URL(BACKEND_URL).port],
    {
      cwd: path.join(REPO_ROOT, 'backend'),
      env: {
        DB_NAME: 'zhiyin_e2e',
        MINIO_BUCKET: 'zhiyin-files-e2e',
        CLEANUP_ENABLED: 'false',
        AUTO_CREATE_TABLES: 'true',
        DEBUG: 'false',
        // 回归环境没有真实密钥，跳过弱凭据启动自检（与单测 conftest 一致）
        ALLOW_INSECURE_DEFAULTS: 'true',
      },
    },
  )
  startService(
    'student',
    path.join(REPO_ROOT, 'student-app', 'node_modules', '.bin', 'uni'),
    ['--host', '127.0.0.1', '--port', new URL(STUDENT_URL).port, '--strictPort'],
    { cwd: path.join(REPO_ROOT, 'student-app'), env: { VITE_API_BASE_URL: BACKEND_URL } },
  )
  startService(
    'teacher',
    path.join(REPO_ROOT, 'teacher-web', 'node_modules', '.bin', 'vite'),
    ['--host', '127.0.0.1', '--port', new URL(TEACHER_URL).port, '--strictPort'],
    { cwd: path.join(REPO_ROOT, 'teacher-web'), env: { VITE_API_BASE_URL: BACKEND_URL } },
  )

  await waitHttp(`${BACKEND_URL}/api/health`, 90000)
  await waitHttp(`${STUDENT_URL}/`, 150000)
  await waitHttp(`${TEACHER_URL}/`, 90000)
}

// ---------------------------------------------------------------- 页面操作
const INTERACTIVE =
  'button, uni-button, a, [role="button"], .el-button, .uni-tabbar__item, ' +
  '.uni-tabbar__label, .seg-item, .stepper, .action, .primary, .ghost'

async function findByText(page, selector, text) {
  const handle = await page.evaluateHandle(
    (selector, text) => {
      const nodes = [...document.querySelectorAll(selector)]
      return (
        nodes.find(
          (element) =>
            element.getClientRects().length > 0 &&
            (element.innerText || element.textContent || '').includes(text),
        ) || null
      )
    },
    selector,
    text,
  )
  return handle.asElement()
}

async function clickByText(page, text, selector = INTERACTIVE) {
  const handle = await page.evaluateHandle(
    (selector, text) => {
      const nodes = [...document.querySelectorAll(selector)].filter(
        (element) => element.getClientRects().length > 0,
      )
      const label = (element) => (element.innerText || element.textContent || '').trim()
      const exact = nodes.filter((element) => label(element) === text)
      const loose = nodes.filter((element) => label(element).includes(text))
      const pool = exact.length ? exact : loose
      pool.sort((a, b) => {
        const area = (element) => element.getBoundingClientRect().width * element.getBoundingClientRect().height
        return area(a) - area(b)
      })
      return pool[0] || null
    },
    selector,
    text,
  )
  const element = handle.asElement()
  if (!element) throw new Error(`页面上找不到可点击的「${text}」`)
  // 用 DOM click 而不是 Puppeteer 的鼠标事件：uni H5 的 chooseFile 会在
  // 点击回调里再 click 一个动态 input，真实鼠标事件会把渲染进程卡在原生选择框上。
  // 文件由 pickFile 直接塞给 input，不依赖这次原生弹窗。
  await element.evaluate((node) => node.click())
}

async function waitBody(page, text, timeout = 45000) {
  await page.waitForFunction(
    (text) => document.body && document.body.innerText.includes(text),
    { timeout, polling: 250 },
    text,
  )
}

/**
 * 点一次按钮，若目标文字还在就再点一次。
 *
 * uni H5 偶尔会把一次 DOM click 吃掉（页面已渲染但没触发处理函数），
 * 这里只在「文字仍在」时重试，断言本身不变：没消失就是失败。
 */
async function clickByTextRetry(page, text, { attempts = 3, settle = 3000 } = {}) {
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    await clickByText(page, text)
    await sleep(600)
    const still = await page.evaluate((text) => document.body.innerText.includes(text), text)
    if (!still) return attempt
    await sleep(settle)
  }
  return attempts
}

async function waitGone(page, text, timeout = 45000) {
  await page.waitForFunction(
    (text) => document.body && !document.body.innerText.includes(text),
    { timeout, polling: 250 },
    text,
  )
}

/**
 * 把文件塞给 uni H5 动态创建的 input[type=file]。
 *
 * 一次传多个路径时浏览器只派发一次 change，页面按自己的多选链路逐份上传；
 * 下单页的 chooseFile count 大于 1，input 带 multiple。
 */
async function pickFiles(page, triggerText, filePaths) {
  // uni H5 的 chooseFile 会动态追加一个 input[type=file] 再 click()。
  // 无头浏览器里这次 click 没有用户激活，原生选择框不会弹；直接给这个 input
  // 塞文件并触发 change，走的是页面同一套上传链路。input 多个时取最后追加的那个。
  await clickByText(page, triggerText)
  // uni 每次 choose 都会先移除旧 input 再追加新的，所以最后一个就是本次的。
  await page.waitForSelector('input[type=file]', { timeout: 20000 })
  await sleep(150)
  const inputs = await page.$$('input[type=file]')
  const input = inputs[inputs.length - 1]
  await input.uploadFile(...filePaths)
}

async function pickFile(page, triggerText, filePath) {
  await pickFiles(page, triggerText, [filePath])
}

async function clickRowButton(page, code, text) {
  // 队列每 5 秒刷新一次，先拿 handle 再点会点到已脱离文档的旧节点。
  // 这里在页面里一次完成「按取件码找行 → 找按钮 → 点击」。
  const result = await page.evaluate(
    ({ code, text }) => {
      const rows = [...document.querySelectorAll('.order-card, .el-table__row, tr')]
      const row = rows.find((element) => (element.innerText || element.textContent || '').includes(code))
      if (!row) return 'no-row'
      // 只找按钮，别误点到「文件已替换，待下载确认」这种标签
      const nodes = [...row.querySelectorAll('button, a, .el-button')]
      const labelOf = (node) => (node.innerText || node.textContent || '').trim()
      const target = nodes.find((node) => labelOf(node) === text) ||
        nodes.find((node) => labelOf(node).includes(text))
      window.__e2eTrace = {
        buttons: [...row.querySelectorAll('button')].map((node) => ({
          text: (node.innerText || '').trim(),
          disabled: node.disabled,
          cls: String(node.className).slice(0, 80),
        })),
        matched: target ? `${target.tagName}.${String(target.className).slice(0, 80)}` : null,
      }
      if (!target) return 'no-button'
      target.click()
      return 'ok'
    },
    { code, text },
  )
  if (process.env.E2E_TRACE) {
    const trace = await page.evaluate(() => window.__e2eTrace)
    console.log(`      TRACE ${orderTrace(trace)}`)
  }
  if (result !== 'ok') throw new Error(`队列里 ${code} 找不到「${text}」（${result}）`)
}

async function waitRowText(page, code, text, timeout = 45000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    // 桌面表格固定操作列会复制一份行；两份都要看，状态文字只在主行里
    const texts = await page.evaluate((code) => {
      const rows = [...document.querySelectorAll('.order-card, .el-table__row, tr')]
      return rows
        .filter((element) => (element.innerText || element.textContent || '').includes(code))
        .map((element) => element.innerText || element.textContent || '')
    }, code)
    if (texts.some((content) => content.includes(text))) return
    await sleep(400)
  }
  throw new Error(`队列里 ${code} 没有变成「${text}」`)
}

async function clickDialog(page, label) {
  const clicked = await page.evaluate((label) => {
    const dialogs = [...document.querySelectorAll('.el-dialog, .el-message-box')].filter(
      (element) => element.getClientRects().length > 0,
    )
    const dialog = dialogs[dialogs.length - 1]
    if (!dialog) return false
    const button = [...dialog.querySelectorAll('button')].find((item) =>
      (item.innerText || item.textContent || '').includes(label),
    )
    if (!button) return false
    button.click()
    return true
  }, label)
  if (!clicked) throw new Error(`弹窗里找不到「${label}」按钮`)
}

// ---------------------------------------------------------------- 多文件场景
/**
 * 切页并强制重建页面实例。
 *
 * uni H5 会把同一路由的旧页面留在页面栈里，改查询参数后可能拿旧参数继续用；
 * 补一次整页刷新，保证 onLoad 拿到新参数（登录态在 localStorage 里，不受影响）。
 */
async function gotoFresh(page, url) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.reload({ waitUntil: 'domcontentloaded', timeout: 60000 })
}

/**
 * 队列按取件码过滤。
 *
 * 直接给 input 赋最新值并派发 input 事件让 Vue 的 v-model 跟上；
 * 上一单的关键词必须先清掉，否则会拼成两个码（三连击不一定能全选）。
 */
async function searchQueue(page, code) {
  const search = await page.$('input[placeholder="订单号 / 取件码 / 文件名"]')
  if (!search) throw new Error('老师端队列里找不到搜索框')
  await search.evaluate((element, code) => {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    ).set
    setter.call(element, code)
    element.dispatchEvent(new Event('input', { bubbles: true }))
  }, code)
  await clickByText(page, '查询')
}

/** 从「下单成功」卡片上读订单号与取件码。 */
async function readOrderResult(page) {
  const result = await page.evaluate(() => {
    const text = document.body.innerText
    return {
      orderNo: (text.match(/\b\d{6}-\d{3}\b/) || [])[0] || '',
      pickup: (text.match(/\b\d{6}-[0-9A-Z]-[0-9A-Z]{2}\b/) || [])[0] || '',
    }
  })
  if (!result.orderNo || !result.pickup) {
    throw new Error(`没有从下单成功卡片读到订单号/取件码：${JSON.stringify(result)}`)
  }
  return result
}

/** 详情页与换文件页都要订单 id；从列表接口取，不依赖卡片 DOM 结构。 */
async function orderIdFromApi(page, orderNo) {
  const row = await page.evaluate(
    async ({ backendUrl, orderNo }) => {
      const key = Object.keys(localStorage).find((item) => item.includes('zhiyin_token'))
      const token = key ? localStorage.getItem(key) : ''
      if (!token) {
        return { error: `localStorage 里没有 token：${Object.keys(localStorage).join(',')}` }
      }
      const response = await fetch(`${backendUrl}/api/order/list`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      const body = await response.json()
      if (body.code !== 0) return { error: `订单列表返回 ${body.code} ${body.message}` }
      return (body.data.items || []).find((item) => item.order_no === orderNo) || {}
    },
    { backendUrl: BACKEND_URL, orderNo },
  )
  if (!row || row.error || !row.id) throw new Error(`没有取到订单 id：${JSON.stringify(row)}`)
  return row.id
}

/** 换文件页先选「换哪一份」：点中带该文件名的整行。 */
async function clickTargetRow(page, fileName) {
  const clicked = await page.evaluate((fileName) => {
    const rows = [...document.querySelectorAll('.target-row')].filter(
      (element) => element.getClientRects().length > 0,
    )
    const row = rows.find((element) => (element.innerText || '').includes(fileName))
    if (!row) return false
    row.click()
    return true
  }, fileName)
  if (!clicked) throw new Error(`换文件页找不到「${fileName}」这一份`)
}

/** 点某一份文件自己的下载按钮：桌面行内文件行与手机卡片逐份行都认。 */
async function clickFileDownload(page, code, fileName) {
  const result = await page.evaluate(
    ({ code, fileName }) => {
      const rows = [...document.querySelectorAll('.order-card, .el-table__row, tr')].filter(
        (element) => (element.innerText || '').includes(code),
      )
      for (const row of rows) {
        const line = [...row.querySelectorAll('.file-line, .oc-file')].find((element) =>
          (element.innerText || '').includes(fileName),
        )
        if (!line) continue
        const button = [...line.querySelectorAll('button, a, .el-button')].find((element) =>
          (element.innerText || '').trim().includes('下载'),
        )
        if (!button) continue
        button.click()
        return 'ok'
      }
      return 'no-target'
    },
    { code, fileName },
  )
  if (result !== 'ok') {
    throw new Error(`队列里 ${code} 找不到「${fileName}」的下载按钮（${result}）`)
  }
}

/** 点「订单文件」弹窗里第几份的下载按钮。 */
async function clickDialogFileDownload(page, seq) {
  const clicked = await page.evaluate((seq) => {
    const dialogs = [...document.querySelectorAll('.el-dialog')].filter(
      (element) => element.getClientRects().length > 0,
    )
    const dialog = dialogs[dialogs.length - 1]
    if (!dialog) return false
    const rows = [...dialog.querySelectorAll('.files-dialog-row')]
    const row = rows.find((element) =>
      ((element.querySelector('.files-dialog-seq') || {}).textContent || '').trim() === `#${seq}`,
    )
    if (!row) return false
    const button = [...row.querySelectorAll('button')].find((element) =>
      (element.innerText || '').includes(`下载第 ${seq} 份`),
    )
    if (!button) return false
    button.click()
    return true
  }, seq)
  if (!clicked) throw new Error(`「订单文件」弹窗里找不到第 ${seq} 份的下载按钮`)
}

/** 记录老师端下载接口的请求参数与响应体。 */
function recordDownloads(page) {
  const items = []
  const handler = async (response) => {
    let url
    try {
      url = new URL(response.url())
    } catch {
      return
    }
    if (!/^\/api\/admin\/order\/\d+\/download$/.test(url.pathname)) return
    const item = { fileIndex: Number(url.searchParams.get('file_index')) || null, body: null }
    items.push(item)
    try {
      item.body = await response.json()
    } catch {
      /* 非 JSON 响应，调用方按失败处理 */
    }
  }
  page.on('response', handler)
  return { items, stop: () => page.off('response', handler) }
}

async function waitDownload(items, fileIndex, timeout = 20000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const hit = items.find((item) => item.fileIndex === fileIndex && item.body)
    if (hit) return hit
    await sleep(200)
  }
  throw new Error(`没有等到第 ${fileIndex} 份文件的下载响应`)
}

/** 把 window.open 换成记录器：签名链接落到数组里，不真开新标签页。 */
async function captureOpened(page) {
  await page.evaluate(() => {
    window.__e2eOpened = []
    window.open = (url) => {
      window.__e2eOpened.push(String(url))
      return null
    }
  })
}

async function openedUrls(page, count, timeout = 20000) {
  await page.waitForFunction(
    (count) => (window.__e2eOpened || []).length >= count,
    { timeout },
    count,
  )
  return page.evaluate(() => window.__e2eOpened.slice())
}

/** 下载参数必须是这一份自己的序号，打开的链接必须与接口返回的签名链接一致。 */
function checkDownload(download, openedUrl, expected) {
  const data = download.body && download.body.data
  if (!data) throw new Error(`第 ${download.fileIndex} 份下载没有返回数据`)
  if (!data.signed_url) throw new Error(`第 ${download.fileIndex} 份下载没有返回签名链接`)
  if (expected.fileIndex !== download.fileIndex) {
    throw new Error(`下载参数是第 ${download.fileIndex} 份，预期第 ${expected.fileIndex} 份`)
  }
  if (data.file_name !== expected.fileName) {
    throw new Error(`第 ${download.fileIndex} 份下载返回了「${data.file_name}」，预期「${expected.fileName}」`)
  }
  if (openedUrl !== data.signed_url) {
    throw new Error(`第 ${download.fileIndex} 份打开的链接与接口返回的签名链接不一致`)
  }
}

/** 链接要求的金额文本：页面与接口用同一种写法，比对前统一。 */
function money(cents) {
  return `¥${((Number(cents) || 0) / 100).toFixed(2)}`
}

/** 记录学生端的报价请求体：页面自己调报价用的就是下单参数。 */
function recordQuoteRequests(page) {
  const items = []
  const handler = (request) => {
    try {
      const url = new URL(request.url())
      if (request.method() !== 'POST' || url.pathname !== '/api/order/quote') return
      items.push({ payload: JSON.parse(request.postData() || 'null') })
    } catch {
      /* 非 JSON 请求忽略 */
    }
  }
  page.on('request', handler)
  return { items, stop: () => page.off('request', handler) }
}

/**
 * 脚本自己调一次报价接口（用页面登录态与同一份参数），不写死任何数字。
 * 页面上的行金额与总额必须等于这次服务端返回值。
 */
async function quoteFromPage(page, payload) {
  return page.evaluate(
    async ({ backendUrl, payload }) => {
      const key = Object.keys(localStorage).find((item) => item.includes('zhiyin_token'))
      const token = key ? localStorage.getItem(key) : ''
      if (!token) return { code: -1, message: 'localStorage 里没有 token' }
      const response = await fetch(`${backendUrl}/api/order/quote`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify(payload),
      })
      return response.json()
    },
    { backendUrl: BACKEND_URL, payload },
  )
}

/** 学生端下单页：清单里每份文件自己的参数、打印页数与行金额。 */
async function readFileParams(page) {
  return page.evaluate(() =>
    [...document.querySelectorAll('.file-row')].map((row) => ({
      index: Number(row.getAttribute('data-file-index')),
      name: ((row.querySelector('.file-name') || {}).textContent || '').trim(),
      segments: [...row.querySelectorAll('.seg-item.on')].map((element) =>
        (element.textContent || '').trim(),
      ),
      copies: ((row.querySelector('.copy-num') || {}).textContent || '').trim(),
      printPages: ((row.querySelector('.file-print-pages') || {}).textContent || '').trim(),
      amount: ((row.querySelector('.file-amount') || {}).textContent || '').trim(),
    })),
  )
}

/** 学生端下单页：打印方案卡上的三行服务端数字。 */
async function readQuoteSummary(page) {
  return page.evaluate(() => {
    const result = {}
    for (const row of document.querySelectorAll('.quote-summary .quote-row')) {
      const key = ((row.querySelector('.quote-k') || {}).textContent || '').trim()
      const value = ((row.querySelector('.quote-v') || {}).textContent || '').trim()
      if (key) result[key] = value
    }
    return result
  })
}

/** 点清单里第 index 份文件自己的参数按钮（单面 / 彩色 / A4 / ＋ / －）。 */
async function clickFileOption(page, fileIndex, text) {
  const result = await page.evaluate(
    ({ fileIndex, text }) => {
      const row = document.querySelector(`.file-row[data-file-index="${fileIndex}"]`)
      if (!row) return 'no-row'
      const nodes = [...row.querySelectorAll('.seg-item, .stepper')].filter(
        (element) => element.getClientRects().length > 0,
      )
      const target = nodes.find((element) => (element.textContent || '').trim() === text)
      if (!target) return 'no-option'
      target.click()
      return 'ok'
    },
    { fileIndex, text },
  )
  if (result !== 'ok') {
    throw new Error(`第 ${fileIndex} 份文件里找不到可点的「${text}」（${result}）`)
  }
}

/** 学生端详情/换文件页：按份列出的文件名、参数、页数与行金额。 */
async function readDetailFiles(page) {
  return page.evaluate(() =>
    [...document.querySelectorAll('.file-line')].map((row) => ({
      name: ((row.querySelector('.file-name') || {}).textContent || '').trim(),
      params: ((row.querySelector('.file-params') || {}).textContent || '').trim(),
      pages: ((row.querySelector('.file-pages') || {}).textContent || '').trim(),
      amount: ((row.querySelector('.file-amount') || {}).textContent || '').trim(),
    })),
  )
}

/** 学生端详情页的整单行（文档页数 / 打印页数 / 金额）。 */
async function readDetailRows(page) {
  return page.evaluate(() => {
    const result = {}
    for (const row of document.querySelectorAll('.row')) {
      const key = ((row.querySelector('.k') || {}).textContent || '').trim()
      const value = ((row.querySelector('.v') || {}).textContent || '').trim()
      if (key) result[key] = value
    }
    return result
  })
}

/** 从页面带 token 读订单详情，拿到服务端落库的逐份参数与金额。 */
async function orderDetailFromApi(page, orderId) {
  const data = await page.evaluate(
    async ({ backendUrl, orderId }) => {
      const key = Object.keys(localStorage).find((item) => item.includes('zhiyin_token'))
      const token = key ? localStorage.getItem(key) : ''
      if (!token) return { error: 'localStorage 里没有 token' }
      const response = await fetch(`${backendUrl}/api/order/${orderId}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      const body = await response.json()
      if (body.code !== 0) return { error: `订单详情返回 ${body.code} ${body.message}` }
      return body.data
    },
    { backendUrl: BACKEND_URL, orderId },
  )
  if (!data || data.error) throw new Error(`没有取到订单详情：${JSON.stringify(data)}`)
  return data
}

async function assertNoOverflow(page, label) {
  const metrics = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }))
  const widest = Math.max(metrics.document, metrics.body)
  if (widest > metrics.viewport + 1) {
    throw new Error(`${label} 横向溢出：内容宽 ${widest} > 视口 ${metrics.viewport}`)
  }
}

async function assertTouchButtons(page, label, rootSelector) {
  // 容器还没渲染时不要偷偷检查整页：页头按钮会把报错带到无关地方
  const found = await page.evaluate(
    (rootSelector) => Boolean(document.querySelector(rootSelector)),
    rootSelector,
  )
  if (!found) throw new Error(`${label} 找不到容器 ${rootSelector}，无法检查按钮尺寸`)
  const small = await page.evaluate((rootSelector) => {
    const root = document.querySelector(rootSelector)
    return [...root.querySelectorAll('button')]
      .filter((element) => element.getClientRects().length > 0)
      .map((element) => ({
        text: (element.innerText || element.textContent || '').trim().slice(0, 16),
        height: Math.round(element.getBoundingClientRect().height),
      }))
      .filter((item) => item.height > 0 && item.height < 44)
  }, rootSelector)
  if (small.length) {
    throw new Error(
      `${label} 有按钮低于 44 像素：${small.map((item) => `${item.text}(${item.height}px)`).join('、')}`,
    )
  }
}

function orderTrace(trace) {
  if (!trace) return '无'
  return `匹配 ${trace.matched || '无'}；按钮 ${trace.buttons.map((item) => `${item.text}${item.disabled ? '(禁用)' : ''}`).join(' / ')}`
}

/** 点一个可见元素：按 CSS 选择器找，可选在某个容器内找。 */
async function clickSelector(page, selector, rootSelector = '') {
  const result = await page.evaluate(
    ({ selector, rootSelector }) => {
      const scope = rootSelector ? document.querySelector(rootSelector) : document
      if (!scope) return 'no-root'
      const node = [...scope.querySelectorAll(selector)].find(
        (element) => element.getClientRects().length > 0,
      )
      if (!node) return 'no-node'
      node.click()
      return 'ok'
    },
    { selector, rootSelector },
  )
  if (result !== 'ok') {
    throw new Error(`找不到可点击的 ${rootSelector || '页面'} 里的 ${selector}（${result}）`)
  }
}

/** 等某个可见元素出现（元素存在且已布局）。 */
async function waitVisible(page, selector, timeout = 20000) {
  await page.waitForFunction(
    (selector) =>
      [...document.querySelectorAll(selector)].some(
        (element) => element.getClientRects().length > 0,
      ),
    { timeout, polling: 200 },
    selector,
  )
}

/** 等页面里某个节点的 class 变成预期的主题类。 */
async function waitThemeClass(page, selector, themeClass, timeout = 20000) {
  await page.waitForFunction(
    (selector, themeClass) => {
      const node = document.querySelector(selector)
      return Boolean(node && node.classList.contains(themeClass))
    },
    { timeout, polling: 200 },
    selector,
    themeClass,
  )
}

/** 等 body 上的主题类变成预期值（老师端主题变量挂在 body 上）。 */
async function waitBodyTheme(page, themeClass, timeout = 20000) {
  await page.waitForFunction(
    (themeClass) => document.body.classList.contains(themeClass),
    { timeout, polling: 200 },
    themeClass,
  )
}

// ---------------------------------------------------------------- 深色模式
/**
 * 把系统深浅设置改成 light 或 dark。
 *
 * 页面里没有任何手动开关，深浅只认 prefers-color-scheme；
 * 这里改的就是浏览器模拟的系统设置，等同于用户改系统外观。
 */
async function setColorScheme(page, scheme) {
  // 后台标签页会被浏览器节流：媒体查询的 change 事件不派发，页面也就不会跟着换色。
  // 真机上用户在看的那个窗口永远不会被节流，这里先切到前台，再改系统深浅。
  await page.bringToFront()
  await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: scheme }])
}

/** 读一次页面外观：根节点类名 + 根节点与关键区块的实际颜色。 */
async function readAppearance(page, rootSelector, blockSelectors = []) {
  return page.evaluate(
    ({ rootSelector, blockSelectors }) => {
      // uni H5 的页面栈会把旧页面留在文档里，量和截都只认当前可见的那个
      const visible = (selector) =>
        [...document.querySelectorAll(selector)].filter((node) => node.getClientRects().length > 0).pop() || null
      const root = visible(rootSelector)
      const read = (node) => {
        if (!node) return null
        const style = getComputedStyle(node)
        return {
          background: style.backgroundColor,
          color: style.color,
          image: style.backgroundImage === 'none' ? '' : 'image',
        }
      }
      return {
        classes: root ? [...root.classList] : [],
        root: read(root),
        blocks: blockSelectors.map((selector) => ({ selector, style: read(visible(selector)) })),
      }
    },
    { rootSelector, blockSelectors },
  )
}

/**
 * 量一段文字与它身下背景的对比度（WCAG 相对亮度公式）。
 *
 * 背景按祖先链逐层叠加真实色值算，不看虚拟背景图；
 * 半透明文字先与背景混合再算，眯掉视觉上被刻意调淡的装饰文字（opacity < 0.9）。
 */
async function measureTextContrast(page, rootSelector, label) {
  return page.evaluate(
    ({ rootSelector, label }) => {
      const parse = (value) => {
        const match = String(value).match(/rgba?\(([^)]+)\)/)
        if (!match) return null
        const parts = match[1].split(/[,/\s]+/).filter(Boolean).map(Number)
        if (parts.length < 3 || parts.some((item) => Number.isNaN(item))) return null
        return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 }
      }
      const channel = (value) => {
        const c = value / 255
        return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
      }
      const luminance = (color) =>
        0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b)
      const ratio = (a, b) => {
        const la = luminance(a)
        const lb = luminance(b)
        return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
      }
      const blend = (front, back) => ({
        r: front.r * front.a + back.r * (1 - front.a),
        g: front.g * front.a + back.g * (1 - front.a),
        b: front.b * front.a + back.b * (1 - front.a),
        a: 1,
      })
      const backgroundOf = (node) => {
        const chain = []
        let current = node
        while (current) {
          const parsed = parse(getComputedStyle(current).backgroundColor)
          if (parsed && parsed.a > 0.01) {
            chain.push(parsed)
            if (parsed.a >= 1) break
          }
          current = current.parentElement
        }
        let background = { r: 255, g: 255, b: 255, a: 1 }
        for (let index = chain.length - 1; index >= 0; index -= 1) {
          background = blend(chain[index], background)
        }
        return background
      }
      const dimmed = (node, root) => {
        let current = node
        while (current && current !== root.parentElement) {
          if (Number(getComputedStyle(current).opacity) < 0.9) return true
          current = current.parentElement
        }
        return false
      }

      const root =
        [...document.querySelectorAll(rootSelector)]
          .filter((node) => node.getClientRects().length > 0)
          .pop() || null
      if (!root) return { label, error: `找不到可见的 ${rootSelector}` }
      const items = []
      for (const node of root.querySelectorAll('*')) {
        const tag = node.tagName.toLowerCase()
        if (['input', 'textarea', 'select', 'option', 'script', 'style'].includes(tag)) continue
        const text = [...node.childNodes]
          .filter((child) => child.nodeType === 3)
          .map((child) => child.textContent.trim())
          .join('')
        if (text.length < 2) continue
        if (!node.getClientRects().length) continue
        if (dimmed(node, root)) continue
        const style = getComputedStyle(node)
        const front = parse(style.color)
        if (!front || front.a < 0.99) continue
        const background = backgroundOf(node)
        items.push({
          text: text.slice(0, 18),
          className: String(node.className || tag).slice(0, 40),
          ratio: Number(ratio(front, background).toFixed(2)),
          color: style.color,
          background: `rgb(${Math.round(background.r)}, ${Math.round(background.g)}, ${Math.round(background.b)})`,
        })
      }
      items.sort((a, b) => a.ratio - b.ratio)
      return {
        label,
        checked: items.length,
        worst: items.slice(0, 3),
        failures: items.filter((item) => item.ratio < 4.5).slice(0, 5),
      }
    },
    { rootSelector, label },
  )
}

/**
 * 等某个「可见」元素的 class 变成预期值。
 *
 * uni H5 的页面栈里旧页面还留在文档中（拿不到布局），waitThemeClass 只看第一个，
 * 深色阶段会量到旧页面的旧类名，所以这里只认有布局的那个。
 */
async function waitVisibleClass(page, selector, className, timeout = 20000) {
  await page.waitForFunction(
    (selector, className) =>
      [...document.querySelectorAll(selector)].some(
        (node) => node.getClientRects().length > 0 && node.classList.contains(className),
      ),
    { timeout, polling: 150 },
    selector,
    className,
  )
}

/**
 * 截一张图到本次回归产物目录。
 *
 * 同时算一份内容摘要：深浅两张图必须真的不一样，
 * 只比字节数会在两张图恰好压缩后同长时误判。
 */
async function shoot(page, name) {
  const file = path.join(ARTIFACTS, `${name}.png`)
  await page.screenshot({ path: file })
  const buffer = fs.readFileSync(file)
  if (!buffer.length) throw new Error(`截图 ${name}.png 是空文件`)
  return { file, size: buffer.length, digest: createHash('sha256').update(buffer).digest('hex') }
}

/** 页面上目前有几个可见的确认弹框（ElMessageBox）。 */
function visibleMessageBoxes(page) {
  return page.evaluate(
    () =>
      [...document.querySelectorAll('.el-message-box')].filter(
        (element) => element.getClientRects().length > 0,
      ).length,
  )
}

/** 等一条带指定文字的轻提示（ElMessage）出现。 */
async function waitMessageText(page, text, timeout = 20000) {
  await page.waitForFunction(
    (text) =>
      [...document.querySelectorAll('.el-message')].some(
        (element) =>
          element.getClientRects().length > 0 &&
          (element.innerText || element.textContent || '').includes(text),
      ),
    { timeout, polling: 200 },
    text,
  )
}

/** 记录老师端「开始打印」接口调用，用来断言第一次点击真的没有调接口。 */
function recordStartCalls(page) {
  const calls = []
  const handler = (request) => {
    try {
      const url = new URL(request.url())
      if (request.method() === 'POST' && /^\/api\/admin\/order\/\d+\/start$/.test(url.pathname)) {
        calls.push(url.pathname)
      }
    } catch {
      /* 非 http 请求忽略 */
    }
  }
  page.on('request', handler)
  return { calls, stop: () => page.off('request', handler) }
}

/** 等「开始打印」接口被调到的次数不少于 count。 */
async function waitStartCalls(record, count, timeout = 20000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (record.calls.length >= count) return record.calls
    await sleep(200)
  }
  throw new Error(`没有等到第 ${count} 次开始打印接口调用（实际 ${record.calls.length} 次）`)
}

function watchPage(page, label, bucket) {
  page.on('pageerror', (error) => bucket.push(`${label} 页面异常：${error.message}`))
  page.on('console', (message) => {
    if (message.type() === 'error') bucket.push(`${label} console：${message.text().slice(0, 240)}`)
  })
  page.on('request', (request) => {
    if (process.env.E2E_TRACE && request.url().includes('/api/admin')) {
      console.log(`      TRACE 请求 ${request.method()} ${new URL(request.url()).pathname}`)
    }
  })
  page.on('response', (response) => {
    if (response.url().includes('/api/') && response.status() >= 400) {
      const pathname = new URL(response.url()).pathname
      bucket.push(`${label} 接口 ${response.status()} ${response.request().method()} ${pathname}`)
    }
  })
}

async function saveFailure(page, stepLabel) {
  try {
    await page.screenshot({ path: path.join(ARTIFACTS, `${stepLabel}.png`), fullPage: true })
    const text = await page.evaluate(() => document.body.innerText.slice(0, 4000))
    fs.writeFileSync(path.join(ARTIFACTS, `${stepLabel}.txt`), text)
  } catch {
    /* 页面可能已经关闭 */
  }
}

// ---------------------------------------------------------------- 业务闭环
async function runViewport(browser, viewport) {
  const label = viewport.name
  const problems = []
  const context = await browser.createBrowserContext()
  const student = await context.newPage()
  const teacher = await context.newPage()
  for (const page of [student, teacher]) {
    await page.setViewport({
      width: viewport.width,
      height: viewport.height,
      deviceScaleFactor: viewport.deviceScaleFactor,
      isMobile: viewport.isMobile,
      hasTouch: viewport.isMobile,
    })
  }
  watchPage(student, '学生端', problems)
  watchPage(teacher, '老师端', problems)

  // uni H5 的 chooseFile 会在没有用户激活时 click 动态 input，浏览器随即派发
  // cancel 让页面以为用户取消了选择。这里只拦文件 input 的 click：不弹原生框，
  // 由 pickFile 直接塞文件触发 change，后续仍走页面真实的 upload 接口。
  await student.evaluateOnNewDocument(() => {
    const original = HTMLInputElement.prototype.click
    HTMLInputElement.prototype.click = function () {
      if (this.type === 'file') {
        window.__e2eFileInput = this
        return
      }
      return original.call(this)
    }
  })

  let stepIndex = 0
  const step = async (name, action, pageForShot = student) => {
    stepIndex += 1
    const prefix = `[${label} ${String(stepIndex).padStart(2, '0')}]`
    process.stdout.write(`  ${prefix} ${name} ... `)
    const started = Date.now()
    try {
      const value = await action()
      console.log(`OK (${Date.now() - started} ms)`)
      return value
    } catch (error) {
      console.log('FAIL')
      error.step = name
      error.stepIndex = stepIndex
      await saveFailure(pageForShot, `${label}-step-${String(stepIndex).padStart(2, '0')}`)
      throw error
    }
  }

  let order = null
  try {
    await step('学生端打开首页并登录', async () => {
      await student.goto(`${STUDENT_URL}/`, { waitUntil: 'domcontentloaded', timeout: 60000 })
      await waitBody(student, '智印助手', 60000)
    })

    await step('进入下单页', async () => {
      // 直接走路由，避免首次点 tab 时 Vite 现场编译把点击事件拖住
      await student.goto(`${STUDENT_URL}/#/pages/order/order`, {
        waitUntil: 'domcontentloaded',
        timeout: 60000,
      })
      await waitBody(student, '选择要打印的文件', 90000)
    })

    await step('上传第一份 PDF 并等待基础方案', async () => {
      await pickFile(student, '选择文档', FIXTURES.v1)
      await waitBody(student, '3. 打印方案', 90000)
      await waitBody(student, '跳过体检，直接下单', 30000)
      await waitBody(student, '确认下单')
    })

    await step('跳过 AI 直接下单，拿到订单号与取件码', async () => {
      await clickByText(student, '确认下单')
      await waitBody(student, '下单成功', 60000)
      order = await student.evaluate(() => {
        const text = document.body.innerText
        return {
          orderNo: (text.match(/\b\d{6}-\d{3}\b/) || [])[0] || '',
          pickup: (text.match(/\b\d{6}-[0-9A-Z]-[0-9A-Z]{2}\b/) || [])[0] || '',
        }
      })
      if (!order.orderNo || !order.pickup) {
        throw new Error(`没有从下单成功卡片读到订单号/取件码：${JSON.stringify(order)}`)
      }
    })

    await step('模拟支付', async () => {
      await clickByTextRetry(student, '模拟支付')
      await waitGone(student, '模拟支付', 30000)
    })

    await step('我的订单里能看到这一单', async () => {
      await student.goto(`${STUDENT_URL}/#/pages/mine/mine`, {
        waitUntil: 'domcontentloaded',
        timeout: 60000,
      })
      await waitBody(student, '全部', 60000)
      await waitBody(student, order.orderNo)
      await waitBody(student, order.pickup)

      // 详情页要订单 id；从页面里带 token 调列表接口取，避免依赖卡片 DOM 结构
      const row = await student.evaluate(
        async ({ backendUrl, orderNo }) => {
          const key = Object.keys(localStorage).find((item) => item.includes('zhiyin_token'))
          const token = key ? localStorage.getItem(key) : ''
          if (!token) return { error: `localStorage 里没有 token：${Object.keys(localStorage).join(',')}` }
          const response = await fetch(`${backendUrl}/api/order/list`, {
            headers: { Authorization: `Bearer ${token}` },
          })
          const body = await response.json()
          if (body.code !== 0) return { error: `订单列表返回 ${body.code} ${body.message}` }
          return (body.data.items || []).find((item) => item.order_no === orderNo) || {}
        },
        { backendUrl: BACKEND_URL, orderNo: order.orderNo },
      )
      if (!row || row.error || !row.id) {
        throw new Error(`没有取到订单 id：${JSON.stringify(row)}`)
      }
      order.id = row.id
    })

    await step('订单详情打开', async () => {
      await student.goto(`${STUDENT_URL}/#/pages/mine/detail?id=${order.id}`, {
        waitUntil: 'domcontentloaded',
        timeout: 60000,
      })
      await waitBody(student, '取件码 · 凭此码到文印室取件', 60000)
      if (viewport.mobile) await assertNoOverflow(student, '学生端订单详情')
    })

    await step('换文件：上传第二份 PDF 并提交', async () => {
      await student.goto(`${STUDENT_URL}/#/pages/mine/replace?id=${order.id}`, {
        waitUntil: 'domcontentloaded',
        timeout: 60000,
      })
      await waitBody(student, '当前订单', 60000)
      await pickFile(student, '选择文档', FIXTURES.v2)
      await waitBody(student, '2. 确认变更', 90000)
      await clickByText(student, '提交换文件申请')
      await waitBody(student, '换文件待老师确认', 45000)
    })

    await step('学生详情仍显示原取件码不变', async () => {
      await waitBody(student, order.pickup)
    })

    await step('老师端登录并打开队列', async () => {
      await teacher.goto(`${TEACHER_URL}/#/login`, { waitUntil: 'domcontentloaded', timeout: 60000 })
      await teacher.waitForSelector('input[placeholder="请输入账号"]', { timeout: 30000 })
      await teacher.type('input[placeholder="请输入账号"]', ADMIN.user)
      await teacher.type('input[placeholder="请输入密码"]', ADMIN.pass)
      await teacher.keyboard.press('Enter')
      await waitBody(teacher, '接单队列', 60000)
      if (viewport.mobile) {
        // 队列数据是异步拉的：等第一张卡出来再量按钮，否则会量到页头
        await teacher.waitForSelector('.order-card', { timeout: 30000 })
        await assertNoOverflow(teacher, '老师端队列')
        await assertTouchButtons(teacher, '老师端队列', '.order-card')
      }
    })

    await step('队列里能看到待确认标记', async () => {
      // 开发库可能有很多历史单，先按取件码过滤，避免目标单不在第一页
      await searchQueue(teacher, order.pickup)
      await waitRowText(teacher, order.pickup, '文件待确认', 45000)
    }, teacher)

    await step('确认换文件弹窗对照新旧文件与重算价', async () => {
      await clickRowButton(teacher, order.pickup, '确认换文件')
      await waitBody(teacher, '系统重算价', 30000)
      await waitBody(teacher, 'thesis-v2.pdf', 30000)
      if (viewport.mobile) {
        await assertNoOverflow(teacher, '老师端确认弹窗')
        await assertTouchButtons(teacher, '老师端确认弹窗', '.el-dialog')
      }
    }, teacher)

    await step('老师通过换文件，队列出现已替换标记', async () => {
      await clickDialog(teacher, '通过')
      await waitRowText(teacher, order.pickup, '已替换', 45000)
    }, teacher)

    await step('老师下载新文件并确认看到', async () => {
      await teacher.evaluate(() => {
        window.open = () => null
      })
      await clickRowButton(teacher, order.pickup, '下载')
      await waitBody(teacher, '已确认看到新文件', 30000)
      await teacher.waitForFunction(
        (code) => {
          const nodes = [...document.querySelectorAll('.order-card, .el-table__row')]
          const row = nodes.find((element) => (element.innerText || '').includes(code))
          const text = (row && row.innerText) || ''
          return row && !text.includes('已替换') && !text.includes('待下载确认')
        },
        { timeout: 30000, polling: 300 },
        order.pickup,
      )
    }, teacher)

    await step('老师开始打印', async () => {
      await clickRowButton(teacher, order.pickup, '开始打印')
      await clickDialog(teacher, '开始')
      await waitRowText(teacher, order.pickup, '打印中', 45000)
    }, teacher)

    await step('老师打印完成', async () => {
      await clickRowButton(teacher, order.pickup, '打印完成')
      await waitBody(teacher, '确认完成', 30000)
      await clickDialog(teacher, '确认完成')
      await waitRowText(teacher, order.pickup, '待取件', 45000)
    }, teacher)

    await step('老师按取件码核销', async () => {
      await clickRowButton(teacher, order.pickup, '核销')
      await waitBody(teacher, '确认核销', 30000)
      await clickDialog(teacher, '确认核销')
      await waitRowText(teacher, order.pickup, '已完成', 45000)
    }, teacher)

    await step('学生端订单详情看到已完成', async () => {
      await student.reload({ waitUntil: 'domcontentloaded' })
      await student.waitForFunction(
        () => {
          const status = document.querySelector('.status-text')
          return status && (status.textContent || '').includes('已完成')
        },
        { timeout: 45000, polling: 300 },
      )
      if (viewport.mobile) await assertNoOverflow(student, '学生端完成后详情')
    })

    const multiFile = await runMultiFilePhase({ student, teacher, viewport, step })
    const fourFile = await runFourFilePhase({ student, teacher, viewport, step })
    const theme = await runThemePhase({ student, teacher, viewport, step })
    const startGuard = await runStartGuardPhase({ student, teacher, viewport, step })
    const darkMode = await runDarkModePhase({
      student,
      teacher,
      viewport,
      step,
      orderId: startGuard.id,
    })

    if (problems.length) {
      throw Object.assign(new Error(`页面/接口有 ${problems.length} 条异常记录`), {
        step: '页面错误汇总',
        stepIndex,
      })
    }
    return {
      viewport: label,
      orderNo: order.orderNo,
      pickup: order.pickup,
      multiFile,
      fourFile,
      theme,
      startGuard,
      darkMode,
    }
  } finally {
    await context.close()
  }
}

// ---------------------------------------------------------------- 多文件闭环
/**
 * 多文件场景：一次选两份 PDF 下一单，学生换第二份，老师确认后逐份下载。
 *
 * 桌面文件列前三份直接展开，两份都在行内；四份场景的「查看全部」单独跑。
 */
async function runMultiFilePhase({ student, teacher, viewport, step }) {
  const order = { orderNo: '', pickup: '', id: 0 }
  // 页面自己调的报价入参与返回：后续下单、详情、换文件都用它当基准数
  const pricing = { payload: null, quote: null }

  await step('多文件：一次选两份 PDF', async () => {
    await gotoFresh(student, `${STUDENT_URL}/#/pages/order/order`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '选择要打印的文件', 90000)
    await pickFiles(student, '选择文档', [FIXTURES.v1, FIXTURES.v3])
    await waitBody(student, '2 个文件', 120000)
    await waitBody(student, 'thesis-v1.pdf', 30000)
    await waitBody(student, 'thesis-v3.pdf', 30000)
    await waitBody(student, '确认下单', 60000)
  })

  await step('多文件：逐份设参数，行金额与总额等于服务端报价', async () => {
    const quotes = recordQuoteRequests(student)
    try {
      // 第一份保持单面 + 黑白 + 1 份；第二份改成双面 + 彩色 + 2 份
      await clickFileOption(student, 1, '单面')
      await clickFileOption(student, 1, '黑白')
      await clickFileOption(student, 2, '双面')
      await clickFileOption(student, 2, '彩色')
      await clickFileOption(student, 2, '＋')
      // 300 ms 防抖 + 一次往返：等最后那次报价回来再读页面
      await sleep(1500)
      await waitVisible(student, '.file-row[data-file-index="2"] .file-amount', 20000)
      if (!quotes.items.length) throw new Error('页面没有调用 /api/order/quote')
      pricing.payload = quotes.items[quotes.items.length - 1].payload
    } finally {
      quotes.stop()
    }

    // 防抖后的入参就是页面当前的选择，先把它钉住，避免拿到旧一次的报价
    const sent = pricing.payload
    const rowsSent = sent && Array.isArray(sent.files) ? sent.files : []
    if (rowsSent.length !== 2) {
      throw new Error(`报价入参应该有两份文件：${JSON.stringify(sent)}`)
    }
    const expectSent = [
      { print_side: 1, color_mode: 0, copies: 1 },
      { print_side: 2, color_mode: 1, copies: 2 },
    ]
    expectSent.forEach((expect, index) => {
      const file = rowsSent[index]
      if (
        file.print_side !== expect.print_side ||
        file.color_mode !== expect.color_mode ||
        file.copies !== expect.copies
      ) {
        throw new Error(
          `第 ${index + 1} 份的报价参数不对：${JSON.stringify(file)}，预期 ${JSON.stringify(expect)}`,
        )
      }
    })

    // 脚本自己再调一次接口：同一份参数必须得到同一组数字
    const body = await quoteFromPage(student, sent)
    if (!body || body.code !== 0) throw new Error(`报价接口返回 ${JSON.stringify(body)}`)
    pricing.quote = body.data

    const rows = await readFileParams(student)
    if (rows.length !== 2) throw new Error(`清单里应有两份文件，实际 ${rows.length}`)
    if (!rows[0].segments.includes('单面') || !rows[0].segments.includes('黑白')) {
      throw new Error(`第 1 份的参数不是单面黑白：${JSON.stringify(rows[0])}`)
    }
    if (rows[0].copies !== '1') throw new Error(`第 1 份份数应为 1，实际「${rows[0].copies}」`)
    if (!rows[1].segments.includes('双面') || !rows[1].segments.includes('彩色')) {
      throw new Error(`第 2 份的参数不是双面彩色：${JSON.stringify(rows[1])}`)
    }
    if (rows[1].copies !== '2') throw new Error(`第 2 份份数应为 2，实际「${rows[1].copies}」`)

    // 页面上的行金额、逐份打印页数、总额三样全部等于服务端报价
    pricing.quote.files.forEach((file, index) => {
      const row = rows[index]
      if (row.amount !== money(file.amount_cents)) {
        throw new Error(
          `第 ${index + 1} 份行金额显示「${row.amount}」，报价是 ${money(file.amount_cents)}`,
        )
      }
      if (!row.printPages.includes(`${file.print_pages} 页`)) {
        throw new Error(
          `第 ${index + 1} 份打印页数显示「${row.printPages}」，报价是 ${file.print_pages} 页`,
        )
      }
    })
    const sum = pricing.quote.files.reduce((acc, file) => acc + Number(file.amount_cents), 0)
    if (sum !== pricing.quote.amount_cents) {
      throw new Error(
        `报价自己就不自洽：逐份之和 ${sum} 分，整单 ${pricing.quote.amount_cents} 分`,
      )
    }
    const summary = await readQuoteSummary(student)
    const expectedSummary = {
      文档页数: `${pricing.quote.page_count} 页`,
      打印页数: `${pricing.quote.print_page_count} 页`,
      应付金额: money(pricing.quote.amount_cents),
    }
    for (const [key, value] of Object.entries(expectedSummary)) {
      if (summary[key] !== value) {
        throw new Error(`页面「${key}」显示「${summary[key] || '无'}」，报价是「${value}」`)
      }
    }
  })

  await step('多文件：下单并支付', async () => {
    await clickByText(student, '确认下单')
    await waitBody(student, '下单成功', 60000)
    Object.assign(order, await readOrderResult(student))
    order.id = await orderIdFromApi(student, order.orderNo)
    await clickByTextRetry(student, '模拟支付')
    await waitGone(student, '模拟支付', 30000)
  })

  await step('多文件：学生详情按份显示参数与行金额', async () => {
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/detail?id=${order.id}`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '文件（2 份', 60000)

    // 订单落库的逐份金额必须就是刚才报价那组数（同一套计费，不是两套算法）
    const detail = await orderDetailFromApi(student, order.id)
    if (detail.files.length !== 2) {
      throw new Error(`订单详情应有 2 份文件，实际 ${detail.files.length}`)
    }
    detail.files.forEach((file, index) => {
      const quoted = pricing.quote.files[index]
      if (Number(file.amount_cents) !== Number(quoted.amount_cents)) {
        throw new Error(
          `第 ${index + 1} 份落库行金额 ${file.amount_cents} 分，报价 ${quoted.amount_cents} 分`,
        )
      }
      if (Number(file.print_pages) !== Number(quoted.print_pages)) {
        throw new Error(
          `第 ${index + 1} 份落库打印页数 ${file.print_pages}，报价 ${quoted.print_pages}`,
        )
      }
    })

    const rows = await readDetailFiles(student)
    if (rows.length !== 2) throw new Error(`详情页应逐份列出 2 份，实际 ${rows.length}`)
    const expectedParams = ['单面 · 黑白', '双面 · 彩色']
    rows.forEach((row, index) => {
      const quoted = pricing.quote.files[index]
      if (!row.name.includes(index === 0 ? 'thesis-v1.pdf' : 'thesis-v3.pdf')) {
        throw new Error(`第 ${index + 1} 份文件名不对：「${row.name}」`)
      }
      if (!row.params.includes(expectedParams[index])) {
        throw new Error(`第 ${index + 1} 份参数显示「${row.params}」，预期含「${expectedParams[index]}」`)
      }
      if (!row.params.includes(`${quoted.copies} 份`)) {
        throw new Error(`第 ${index + 1} 份份数显示「${row.params}」，预期 ${quoted.copies} 份`)
      }
      if (!row.pages.includes(`文档 ${quoted.page_count} 页`)) {
        throw new Error(`第 ${index + 1} 份文档页数显示「${row.pages}」`)
      }
      if (!row.pages.includes(`打印 ${quoted.print_pages} 页`)) {
        throw new Error(`第 ${index + 1} 份打印页数显示「${row.pages}」`)
      }
      if (row.amount !== money(quoted.amount_cents)) {
        throw new Error(
          `第 ${index + 1} 份行金额显示「${row.amount}」，报价是 ${money(quoted.amount_cents)}`,
        )
      }
    })

    const totals = await readDetailRows(student)
    const expectTotals = {
      文档页数: `${pricing.quote.page_count} 页`,
      打印页数: `${pricing.quote.print_page_count} 页`,
    }
    for (const [key, value] of Object.entries(expectTotals)) {
      if (totals[key] !== value) {
        throw new Error(`详情页「${key}」显示「${totals[key] || '无'}」，报价是「${value}」`)
      }
    }
    if (!(totals['金额'] || '').includes(money(pricing.quote.amount_cents))) {
      throw new Error(`详情页总额显示「${totals['金额'] || '无'}」与报价不符`)
    }
    if (viewport.mobile) await assertNoOverflow(student, '学生端多文件详情（逐份参数）')
  })

  await step('多文件：老师端队列列出两份文件', async () => {
    await searchQueue(teacher, order.pickup)
    await waitRowText(teacher, order.pickup, 'thesis-v1.pdf')
    await waitRowText(teacher, order.pickup, 'thesis-v3.pdf')
    // 每一份自己的单双面、颜色、纸张、份数、打印页数与行金额
    const lines = await teacher.evaluate((code) => {
      const row = [...document.querySelectorAll('.order-card, .el-table__row, tr')].find((element) =>
        (element.innerText || '').includes(code),
      )
      if (!row) return null
      return [...row.querySelectorAll('.file-line, .oc-file')].map((element) =>
        (element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim(),
      )
    }, order.pickup)
    if (!lines || lines.length !== 2) {
      throw new Error(`老师端没有逐份列出两份：${JSON.stringify(lines)}`)
    }
    const expectedLines = ['单面 · 黑白', '双面 · 彩色']
    lines.forEach((line, index) => {
      const quoted = pricing.quote.files[index]
      if (!line.includes(expectedLines[index])) {
        throw new Error(`老师端第 ${index + 1} 份参数显示「${line}」`)
      }
      if (!line.includes(`${quoted.copies} 份`)) {
        throw new Error(`老师端第 ${index + 1} 份没显示份数 ${quoted.copies}`)
      }
      if (!line.includes(`打印 ${quoted.print_pages} 页`)) {
        throw new Error(`老师端第 ${index + 1} 份没显示打印页数 ${quoted.print_pages}`)
      }
      if (!line.includes(money(quoted.amount_cents))) {
        throw new Error(
          `老师端第 ${index + 1} 份没显示行金额 ${money(quoted.amount_cents)}：「${line}」`,
        )
      }
    })
    if (viewport.mobile) {
      await assertNoOverflow(teacher, '老师端多文件队列')
      await assertTouchButtons(teacher, '老师端多文件队列', '.order-card')
    }
  }, teacher)

  await step('多文件：学生换第二份文件', async () => {
    // 先进订单详情再去换文件页：换完提交后页面会退回上一页，
    // 不先经过详情的话会退回到下单页，看不到待确认状态。
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/detail?id=${order.id}`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '文件（2 份', 60000)
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/replace?id=${order.id}`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '1. 选要换哪一份', 60000)
    await clickTargetRow(student, 'thesis-v3.pdf')
    await pickFile(student, '选择文档', FIXTURES.v2)
    await waitBody(student, '3. 确认变更', 90000)
    await clickByText(student, '提交换文件申请')
    await waitBody(student, '已提交一份换文件申请', 45000)
    // 整页刷新后页面栈里没有上一页，navigateBack 退不回去；直接去详情页看待确认状态
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/detail?id=${order.id}`)
    await waitBody(student, '换文件待老师确认', 45000)
  })

  await step('多文件：老师确认换的是第 2 份', async () => {
    await searchQueue(teacher, order.pickup)
    await waitRowText(teacher, order.pickup, '文件待确认')
    await clickRowButton(teacher, order.pickup, '确认换文件')
    await waitBody(teacher, '本次换的是第 2 个文件', 30000)
    await waitBody(teacher, 'thesis-v2.pdf', 30000)
    if (viewport.mobile) {
      await assertNoOverflow(teacher, '老师端多文件确认弹窗')
      await assertTouchButtons(teacher, '老师端多文件确认弹窗', '.el-dialog')
    }
    await clickDialog(teacher, '通过')
    await waitRowText(teacher, order.pickup, '已替换', 45000)
    await waitRowText(teacher, order.pickup, 'thesis-v2.pdf', 45000)
  }, teacher)

  await step('多文件：逐份下载，第二份拿到自己的链接', async () => {
    const downloads = recordDownloads(teacher)
    try {
      await captureOpened(teacher)
      await clickFileDownload(teacher, order.pickup, 'thesis-v2.pdf')
      const second = await waitDownload(downloads.items, 2)
      await waitBody(teacher, '已确认看到新文件', 30000)
      const afterSecond = await openedUrls(teacher, 1)
      checkDownload(second, afterSecond[0], { fileIndex: 2, fileName: 'thesis-v2.pdf' })

      await clickFileDownload(teacher, order.pickup, 'thesis-v1.pdf')
      const first = await waitDownload(downloads.items, 1)
      const afterFirst = await openedUrls(teacher, 2)
      checkDownload(first, afterFirst[1], { fileIndex: 1, fileName: 'thesis-v1.pdf' })
    } finally {
      downloads.stop()
    }
  }, teacher)

  await step('多文件：学生详情看到第二份已更新', async () => {
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/detail?id=${order.id}`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, 'thesis-v1.pdf', 60000)
    await waitBody(student, 'thesis-v2.pdf', 60000)
    await waitGone(student, 'thesis-v3.pdf', 30000)
    const names = await student.$$eval('.file-line .file-name', (elements) =>
      elements.map((element) => (element.textContent || '').trim()),
    )
    if (
      names.length !== 2 ||
      !names[0].includes('thesis-v1.pdf') ||
      !names[1].includes('thesis-v2.pdf')
    ) {
      throw new Error(`学生端多文件顺序不对：${JSON.stringify(names)}`)
    }

    // 换完第二份后：第一份的参数与行金额不动，第二份按原参数重算，
    // 整单总额等于服务端报价（脚本用当前清单再报一次价来对）。
    const detail = await orderDetailFromApi(student, order.id)
    const body = await quoteFromPage(student, {
      files: detail.files.map((file) => ({
        file_key: file.file_key,
        file_name: file.file_name,
        page_count: file.page_count,
        print_side: file.print_side,
        color_mode: file.color_mode,
        paper_size: file.paper_size,
        copies: file.copies,
      })),
      print_side: detail.print_side,
      color_mode: detail.color_mode,
      copies: detail.copies,
      paper_size: detail.paper_size,
    })
    if (!body || body.code !== 0) {
      throw new Error(`换文件后报价接口返回 ${JSON.stringify(body)}`)
    }
    const quoted = body.data
    if (Number(quoted.amount_cents) !== Number(detail.amount_cents)) {
      throw new Error(
        `整单总额与服务端报价不一致：订单 ${detail.amount_cents} 分，报价 ${quoted.amount_cents} 分`,
      )
    }
    if (Number(quoted.files[0].amount_cents) !== Number(pricing.quote.files[0].amount_cents)) {
      throw new Error(
        `第一份行金额被改了：换前 ${pricing.quote.files[0].amount_cents} 分，` +
          `换后 ${quoted.files[0].amount_cents} 分`,
      )
    }
    if (Number(quoted.files[1].print_side) !== 2 || Number(quoted.files[1].color_mode) !== 1) {
      throw new Error(`第二份换文件后参数被改了：${JSON.stringify(quoted.files[1])}`)
    }
    if (Number(quoted.files[1].copies) !== 2) {
      throw new Error(`第二份换文件后份数被改成 ${quoted.files[1].copies}`)
    }

    const rows = await readDetailFiles(student)
    if (rows.length !== 2) throw new Error(`详情页应逐份列出 2 份，实际 ${rows.length}`)
    if (!rows[0].params.includes('单面 · 黑白') || !rows[0].params.includes('1 份')) {
      throw new Error(`第一份的参数变了：「${rows[0].params}」`)
    }
    if (rows[0].amount !== money(quoted.files[0].amount_cents)) {
      throw new Error(`第一份行金额显示「${rows[0].amount}」，服务端是 ${money(quoted.files[0].amount_cents)}`)
    }
    if (!rows[1].params.includes('双面 · 彩色') || !rows[1].params.includes('2 份')) {
      throw new Error(`第二份的参数没沿用原值：「${rows[1].params}」`)
    }
    if (rows[1].amount !== money(quoted.files[1].amount_cents)) {
      throw new Error(`第二份行金额显示「${rows[1].amount}」，服务端是 ${money(quoted.files[1].amount_cents)}`)
    }
    if (!rows[1].pages.includes(`打印 ${quoted.files[1].print_pages} 页`)) {
      throw new Error(`第二份打印页数显示「${rows[1].pages}」`)
    }
    const totals = await readDetailRows(student)
    if (!(totals['金额'] || '').includes(money(quoted.amount_cents))) {
      throw new Error(`详情页总额显示「${totals['金额'] || '无'}」，服务端报价 ${money(quoted.amount_cents)}`)
    }
    if (totals['打印页数'] !== `${quoted.print_page_count} 页`) {
      throw new Error(`详情页打印页数显示「${totals['打印页数'] || '无'}」`)
    }
    if (viewport.mobile) await assertNoOverflow(student, '学生端多文件详情')
  })

  return { orderNo: order.orderNo, pickup: order.pickup }
}

/**
 * 四文件场景：验证桌面文件列前三份之外的那份。
 *
 * 桌面点「查看全部 4 个文件」开弹窗，四份都有自己的下载按钮；
 * 手机直接逐份列出，按钮不能小于 44 像素、390 宽不能横向溢出。
 */
async function runFourFilePhase({ student, teacher, viewport, step }) {
  const order = { orderNo: '', pickup: '', id: 0 }
  const expected = ['thesis-v1.pdf', 'thesis-v2.pdf', 'thesis-v3.pdf', 'thesis-v4.pdf']

  await step('四文件：一次选四份 PDF 下单', async () => {
    await gotoFresh(student, `${STUDENT_URL}/#/pages/order/order`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '选择要打印的文件', 90000)
    await pickFiles(student, '选择文档', [FIXTURES.v1, FIXTURES.v2, FIXTURES.v3, FIXTURES.v4])
    await waitBody(student, '4 个文件', 180000)
    for (const name of expected) await waitBody(student, name, 30000)
    await waitBody(student, '确认下单', 60000)
    await clickByText(student, '确认下单')
    await waitBody(student, '下单成功', 60000)
    Object.assign(order, await readOrderResult(student))
    order.id = await orderIdFromApi(student, order.orderNo)
    await clickByTextRetry(student, '模拟支付')
    await waitGone(student, '模拟支付', 30000)
  })

  await step('四文件：老师端每份都有独立下载入口', async () => {
    await searchQueue(teacher, order.pickup)
    await waitRowText(teacher, order.pickup, 'thesis-v1.pdf')
    if (viewport.mobile) {
      const rows = await teacher.evaluate((code) => {
        const card = [...document.querySelectorAll('.order-card')].find((element) =>
          (element.innerText || '').includes(code),
        )
        if (!card) return null
        return [...card.querySelectorAll('.oc-file')].map((line) => ({
          name: ((line.querySelector('.oc-file-name') || {}).textContent || '').trim(),
          seq: ((line.querySelector('.oc-file-seq') || {}).textContent || '').trim(),
          buttons: [...line.querySelectorAll('button')].map((button) => ({
            text: (button.innerText || '').trim(),
            height: Math.round(button.getBoundingClientRect().height),
          })),
        }))
      }, order.pickup)
      if (!rows || rows.length !== 4) {
        throw new Error(`手机端没有逐份列出 4 份文件：${JSON.stringify(rows)}`)
      }
      rows.forEach((row, index) => {
        if (!row.name.includes(expected[index])) {
          throw new Error(`手机端第 ${index + 1} 份显示为「${row.name}」`)
        }
        if (row.seq !== `第 ${index + 1} 个文件`) {
          throw new Error(`手机端第 ${index + 1} 份序号不对：「${row.seq}」`)
        }
        if (row.buttons.length !== 1 || !row.buttons[0].text.includes('下载')) {
          throw new Error(`手机端第 ${index + 1} 份没有独立下载按钮`)
        }
        if (row.buttons[0].height < 44) {
          throw new Error(`手机端第 ${index + 1} 份下载按钮只有 ${row.buttons[0].height} 像素`)
        }
      })
      await assertNoOverflow(teacher, '老师端四文件卡片')
    } else {
      await waitRowText(teacher, order.pickup, '查看全部 4 个文件')
      await clickRowButton(teacher, order.pickup, '查看全部 4 个文件')
      await waitBody(teacher, '订单文件', 20000)
      const rows = await teacher.evaluate(() => {
        const dialog = [...document.querySelectorAll('.el-dialog')]
          .filter((element) => element.getClientRects().length > 0)
          .pop()
        if (!dialog) return null
        return [...dialog.querySelectorAll('.files-dialog-row')].map((row) => ({
          seq: ((row.querySelector('.files-dialog-seq') || {}).textContent || '').trim(),
          name: ((row.querySelector('.files-dialog-name') || {}).textContent || '').trim(),
          buttons: [...row.querySelectorAll('button')].map((button) => ({
            text: (button.innerText || '').trim(),
            height: Math.round(button.getBoundingClientRect().height),
          })),
        }))
      })
      if (!rows || rows.length !== 4) {
        throw new Error(`桌面端「查看全部」没有列出 4 份文件：${JSON.stringify(rows)}`)
      }
      rows.forEach((row, index) => {
        if (row.seq !== `#${index + 1}`) {
          throw new Error(`桌面端第 ${index + 1} 份序号不对：「${row.seq}」`)
        }
        if (!row.name.includes(expected[index])) {
          throw new Error(`桌面端第 ${index + 1} 份显示为「${row.name}」`)
        }
        if (row.buttons.length !== 1 || !row.buttons[0].text.includes(`下载第 ${index + 1} 份`)) {
          throw new Error(`桌面端第 ${index + 1} 份没有独立下载按钮`)
        }
      })
    }
  }, teacher)

  await step('四文件：逐份下载拿到的都是那一份', async () => {
    const downloads = recordDownloads(teacher)
    try {
      await captureOpened(teacher)
      for (let seq = 1; seq <= 4; seq += 1) {
        if (viewport.mobile) {
          await clickFileDownload(teacher, order.pickup, expected[seq - 1])
        } else {
          await clickDialogFileDownload(teacher, seq)
        }
        const hit = await waitDownload(downloads.items, seq)
        const urls = await openedUrls(teacher, seq)
        checkDownload(hit, urls[seq - 1], {
          fileIndex: seq,
          fileName: expected[seq - 1],
        })
      }
      if (!viewport.mobile) await clickDialog(teacher, '关闭')
    } finally {
      downloads.stop()
    }
  }, teacher)

  return { orderNo: order.orderNo, pickup: order.pickup }
}

// ---------------------------------------------------------------- 主题入口
/**
 * 两端主题入口：学生端「我的」页齿轮、老师端侧边栏（手机是抽屉）。
 *
 * 切到第二套 signal 后当前页面类名立刻变，刷新后仍是这一套（存进了各自存储）。
 * 两个视口都要断言按钮尺寸与横向溢出。
 */
async function runThemePhase({ student, teacher, viewport, step }) {
  const studentTheme = { current: '', persisted: '', options: 0 }
  const teacherTheme = { current: '', persisted: '', options: 0 }

  await step('主题：学生端齿轮切皮肤并刷新后保持', async () => {
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/mine`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '全部', 60000)
    await waitVisible(student, '.theme-entry', 20000)
    // 齿轮按钮在 390 宽下也不能被压到 44 像素以下
    await assertTouchButtons(student, '学生端我的页页头', '.page-head')
    await clickSelector(student, '.theme-entry')
    await waitVisible(student, '.theme-sheet', 20000)
    studentTheme.options = await student.$$eval('.theme-sheet .theme-option', (nodes) => nodes.length)
    if (studentTheme.options !== 3) {
      throw new Error(`学生端皮肤面板应该三选一，实际 ${studentTheme.options} 项`)
    }
    // 面板里的选项与关闭按钮同样不得低于 44 像素
    await assertTouchButtons(student, '学生端皮肤面板', '.theme-sheet')
    if (viewport.mobile) await assertNoOverflow(student, '学生端皮肤面板')
    // 第二套：signal
    await clickSelector(student, '.theme-option[data-theme="signal"]', '.theme-sheet')
    await waitThemeClass(student, '.page', 'zi-theme-signal', 20000)
    studentTheme.current = await student.$eval('.page', (node) =>
      [...node.classList].find((name) => name.startsWith('zi-theme-')),
    )
    if (studentTheme.current !== 'zi-theme-signal') {
      throw new Error(`学生端切肤后页面类是「${studentTheme.current}」`)
    }
    // 刷新（退出重进）后应仍然是这一套
    await student.reload({ waitUntil: 'domcontentloaded', timeout: 60000 })
    await waitBody(student, '全部', 60000)
    await waitThemeClass(student, '.page', 'zi-theme-signal', 30000)
    studentTheme.persisted = await student.$eval('.page', (node) =>
      [...node.classList].find((name) => name.startsWith('zi-theme-')),
    )
    if (viewport.mobile) await assertNoOverflow(student, '学生端我的页（signal）')
  })

  await step('主题：老师端侧边栏切皮肤并刷新后保持', async () => {
    const root = viewport.mobile ? '.drawer-body' : '.aside'
    if (viewport.mobile) {
      // 手机端入口在抽屉里：先开抽屉
      await clickSelector(teacher, '.menu-trigger')
      await waitVisible(teacher, '.drawer-body', 20000)
    }
    await waitVisible(teacher, `${root} .theme-option`, 20000)
    teacherTheme.options = await teacher.$$eval(
      `${root} .theme-option`,
      (nodes) => nodes.length,
    )
    if (teacherTheme.options !== 3) {
      throw new Error(`老师端皮肤入口应该三选一，实际 ${teacherTheme.options} 项`)
    }
    // 抽屉里的入口也要够得着：按钮不得低于 44 像素
    await assertTouchButtons(teacher, '老师端皮肤入口', root)
    if (viewport.mobile) await assertNoOverflow(teacher, '老师端皮肤抽屉')
    await clickSelector(teacher, '.theme-option[data-theme="signal"]', root)
    await waitBodyTheme(teacher, 'zi-theme-signal', 20000)
    teacherTheme.current = await teacher.evaluate(() =>
      [...document.body.classList].find((name) => name.startsWith('zi-theme-')),
    )
    if (teacherTheme.current !== 'zi-theme-signal') {
      throw new Error(`老师端切肤后 body 类是「${teacherTheme.current}」`)
    }
    await teacher.reload({ waitUntil: 'domcontentloaded', timeout: 60000 })
    await waitBody(teacher, '接单队列', 60000)
    await waitBodyTheme(teacher, 'zi-theme-signal', 30000)
    teacherTheme.persisted = await teacher.evaluate(() =>
      [...document.body.classList].find((name) => name.startsWith('zi-theme-')),
    )
    if (teacherTheme.persisted !== 'zi-theme-signal') {
      throw new Error(`老师端刷新后 body 类是「${teacherTheme.persisted}」`)
    }
    // 刷新后入口仍然在那里（桌面侧边栏直接看得到）
    if (!viewport.mobile) await waitVisible(teacher, '.aside .theme-option', 20000)
  }, teacher)

  return { student: studentTheme, teacher: teacherTheme }
}

// ---------------------------------------------------------------- 开始打印拦截
/**
 * 「已替换未确认」订单的两段式拦截。
 *
 * 第一下只提示先下载，不弹框、不调 /start，队列不动；
 * 第二下才弹确认框，确认后照常进入打印中。
 */
async function runStartGuardPhase({ student, teacher, viewport, step }) {
  const order = { orderNo: '', pickup: '', id: 0 }
  const result = { firstClickStartCalls: 0, secondClickStartCalls: 0, messageBoxes: 0 }

  await step('拦截：下一单并换文件，老师确认后进入已替换未确认', async () => {
    await gotoFresh(student, `${STUDENT_URL}/#/pages/order/order`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '选择要打印的文件', 90000)
    await pickFile(student, '选择文档', FIXTURES.v1)
    await waitBody(student, '3. 打印方案', 90000)
    await waitBody(student, '确认下单', 60000)
    await clickByText(student, '确认下单')
    await waitBody(student, '下单成功', 60000)
    Object.assign(order, await readOrderResult(student))
    order.id = await orderIdFromApi(student, order.orderNo)
    await clickByTextRetry(student, '模拟支付')
    await waitGone(student, '模拟支付', 30000)

    // 换文件：先进详情再进取换页，完成后回到详情看待确认状态
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/detail?id=${order.id}`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '取件码 · 凭此码到文印室取件', 60000)
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/replace?id=${order.id}`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '当前订单', 60000)
    await pickFile(student, '选择文档', FIXTURES.v2)
    await waitBody(student, '2. 确认变更', 90000)
    await clickByText(student, '提交换文件申请')
    // 整页刷新进来时页面栈里没有上一页，提交后 navigateBack 退不回去，
    // 先等本页的提交成功标记，再自己走详情页看待确认状态。
    await waitBody(student, '已提交一份换文件申请', 45000)
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/detail?id=${order.id}`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '换文件待老师确认', 45000)
  })

  await step('拦截：老师确认换文件，订单变成已替换未确认', async () => {
    await searchQueue(teacher, order.pickup)
    await waitRowText(teacher, order.pickup, '文件待确认', 45000)
    await clickRowButton(teacher, order.pickup, '确认换文件')
    await waitBody(teacher, '系统重算价', 30000)
    await clickDialog(teacher, '通过')
    await waitRowText(teacher, order.pickup, '已替换', 45000)
    await waitRowText(teacher, order.pickup, '排队中', 45000)
  }, teacher)

  await step('拦截：第一次点开始打印只提示、不弹框、不调接口', async () => {
    const starts = recordStartCalls(teacher)
    try {
      await clickRowButton(teacher, order.pickup, '开始打印')
      await waitMessageText(teacher, '先下载新文件', 20000)
      // 轻提示已经出来了，再等一拍确认没有后续弹框/接口调用
      await sleep(800)
      result.messageBoxes = await visibleMessageBoxes(teacher)
      result.firstClickStartCalls = starts.calls.length
      if (result.messageBoxes !== 0) {
        throw new Error(`第一次点击弹出了 ${result.messageBoxes} 个确认框（不应该弹）`)
      }
      if (result.firstClickStartCalls !== 0) {
        throw new Error(`第一次点击就调了 ${result.firstClickStartCalls} 次开始打印接口`)
      }
      // 队列不变：仍在排队中，仍然带着已替换标记
      await waitRowText(teacher, order.pickup, '排队中', 20000)
      await waitRowText(teacher, order.pickup, '已替换', 20000)
    } finally {
      starts.stop()
    }
  }, teacher)

  await step('拦截：第二次点开始打印弹确认，确认后进入打印中', async () => {
    const starts = recordStartCalls(teacher)
    try {
      await clickRowButton(teacher, order.pickup, '开始打印')
      await waitVisible(teacher, '.el-message-box', 20000)
      await waitBody(teacher, '仍要现在开始打印吗', 20000)
      await clickDialog(teacher, '仍然开始')
      await waitRowText(teacher, order.pickup, '打印中', 45000)
      const calls = await waitStartCalls(starts, 1, 20000)
      result.secondClickStartCalls = calls.length
      if (result.secondClickStartCalls !== 1) {
        throw new Error(`第二次点击应该调 1 次开始打印接口，实际 ${result.secondClickStartCalls} 次`)
      }
    } finally {
      starts.stop()
    }
  }, teacher)

  return { orderNo: order.orderNo, pickup: order.pickup, id: order.id, ...result }
}

// ---------------------------------------------------------------- 深色模式
/**
 * 深色模式验收。
 *
 * 不新增皮肤、不加手动开关：深浅只看系统设置（prefers-color-scheme）。
 * 浅色 → 深色 → 浅色 回切，断言根节点状态、关键区块背景色确实变了；
 * 深色下 390 宽无横向溢出、老师端按钮不小于 44 像素；
 * 深色与纸面工单 / 信号牌两套皮肤交叉看一遍；最后量一遍正文对比度。
 */
async function runDarkModePhase({ student, teacher, viewport, step, orderId }) {
  const result = {
    student: {},
    teacher: {},
    skins: {},
    pages: {},
    contrast: [],
    screenshots: [],
  }

  await step('深色：学生端跟随系统切深色，根节点与卡片背景跟着变', async () => {
    await setColorScheme(student, 'light')
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/mine`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '全部', 60000)
    await waitVisibleClass(student, '.page', 'zi-scheme-light')
    const light = await readAppearance(student, '.page', ['.card', '.page-head'])
    result.student.light = light

    // 只改系统设置，不刷新页面：页面自己就要跟着换
    await setColorScheme(student, 'dark')
    await waitVisibleClass(student, '.page', 'zi-scheme-dark')
    const dark = await readAppearance(student, '.page', ['.card', '.page-head'])
    result.student.dark = dark

    const skinOf = (classes) => classes.filter((name) => name.startsWith('zi-theme-'))
    const lightSkin = skinOf(light.classes)
    const darkSkin = skinOf(dark.classes)
    if (lightSkin.length !== 1 || darkSkin.length !== 1) {
      throw new Error(`根节点应该正好一套皮肤：浅色 ${lightSkin.join(',')} / 深色 ${darkSkin.join(',')}`)
    }
    if (lightSkin[0] !== darkSkin[0]) {
      throw new Error(`切深浅不该换皮肤：浅色 ${lightSkin[0]} / 深色 ${darkSkin[0]}`)
    }
    if (!dark.classes.includes('zi-scheme-dark') || !light.classes.includes('zi-scheme-light')) {
      throw new Error(`根节点深浅状态不对：浅色 ${light.classes.join(' ')} / 深色 ${dark.classes.join(' ')}`)
    }
    const darkBackground = dark.root && dark.root.background
    if (!darkBackground || darkBackground === (light.root && light.root.background)) {
      throw new Error(`切深色后页面底色没变：浅色 ${light.root && light.root.background} / 深色 ${darkBackground}`)
    }
    const lightBlock = light.blocks[0].style && light.blocks[0].style.background
    const darkBlock = dark.blocks[0].style && dark.blocks[0].style.background
    if (!darkBlock || darkBlock === lightBlock) {
      throw new Error(`切深色后卡片底色没变：浅色 ${lightBlock} / 深色 ${darkBlock}`)
    }
  })

  await step('深色：学生端五个页面都是深色，390 无横向溢出', async () => {
    const pages = [
      { name: '首页', url: `${STUDENT_URL}/#/pages/index/index`, text: '智印助手' },
      { name: '下单', url: `${STUDENT_URL}/#/pages/order/order`, text: '选择要打印的文件' },
      { name: '我的', url: `${STUDENT_URL}/#/pages/mine/mine`, text: '全部' },
      {
        name: '订单详情',
        url: `${STUDENT_URL}/#/pages/mine/detail?id=${orderId}`,
        text: '取件码 · 凭此码到文印室取件',
      },
      { name: '换文件', url: `${STUDENT_URL}/#/pages/mine/replace?id=${orderId}`, text: '当前订单' },
    ]
    for (const item of pages) {
      await gotoFresh(student, item.url, { waitUntil: 'domcontentloaded', timeout: 60000 })
      await waitBody(student, item.text, 60000)
      await waitVisibleClass(student, '.page', 'zi-scheme-dark')
      const appearance = await readAppearance(student, '.page', ['.page'])
      const background = appearance.root && appearance.root.background
      const match = background && background.match(/rgba?\(([^)]+)\)/)
      const channels = match ? match[1].split(/[,/\s]+/).filter(Boolean).map(Number) : []
      const brightness = channels.length >= 3 ? (channels[0] + channels[1] + channels[2]) / 3 : 255
      if (brightness > 90) {
        throw new Error(`学生端${item.name}页深色下底色仍然偏亮：${background}`)
      }
      if (viewport.mobile) await assertNoOverflow(student, `学生端${item.name}页（深色）`)
      result.pages[`student-${item.name}`] = background
    }
    if (viewport.mobile) {
      await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/detail?id=${orderId}`, {
        waitUntil: 'domcontentloaded',
        timeout: 60000,
      })
      await waitBody(student, '取件码 · 凭此码到文印室取件', 60000)
      result.contrast.push(await measureTextContrast(student, '.page', '学生端订单详情（深色）'))
    }
  })

  await step('深色：老师端四屏深色，390 无横向溢出且按钮不小于 44 像素', async () => {
    // 登录屏：带着 token 会被路由直接送去工作台，先摘下来，看完再装回去
    const adminToken = await teacher.evaluate(() => localStorage.getItem('zhiyin_admin_token'))
    await teacher.evaluate(() => localStorage.removeItem('zhiyin_admin_token'))
    await setColorScheme(teacher, 'light')
    await teacher.goto(`${TEACHER_URL}/#/login`, { waitUntil: 'domcontentloaded', timeout: 60000 })
    await waitBodyTheme(teacher, 'zi-scheme-light', 30000)
    await waitVisible(teacher, '.card', 30000)
    // 卡片底色有过渡动画，等它停稳再量，否则会读到过渡帧
    await sleep(600)
    const loginLight = await readAppearance(teacher, 'body', ['.card'])
    await setColorScheme(teacher, 'dark')
    await waitBodyTheme(teacher, 'zi-scheme-dark', 30000)
    await sleep(600)
    const loginDark = await readAppearance(teacher, 'body', ['.card'])
    if (!loginDark.blocks[0].style || loginDark.blocks[0].style.background === loginLight.blocks[0].style.background) {
      throw new Error('老师端登录页深色下卡片底色没变')
    }
    result.teacher.login = loginDark.blocks[0].style.background
    await teacher.evaluate((token) => localStorage.setItem('zhiyin_admin_token', token), adminToken || '')

    // 深色下一路看完工作台、流水、设置
    await gotoFresh(teacher, `${TEACHER_URL}/#/dashboard`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(teacher, '接单队列', 60000)
    await waitBodyTheme(teacher, 'zi-scheme-dark', 30000)
    const dashboard = await readAppearance(teacher, 'body', ['.page-card', '.stats'])
    result.teacher.dashboard = dashboard
    await waitVisible(teacher, '.page-card', 20000)
    if (viewport.mobile) {
      await teacher.waitForSelector('.order-card', { timeout: 30000 })
      await assertNoOverflow(teacher, '老师端工作台（深色）')
      await assertTouchButtons(teacher, '老师端工作台（深色）', '.order-card')
      await assertTouchButtons(teacher, '老师端工作台工具栏（深色）', '.main')
    }
    result.contrast.push(await measureTextContrast(teacher, '.main', '老师端工作台（深色）'))

    for (const item of [
      { name: '收款流水', path: '/transactions', text: '共' },
      { name: '收单与公告', path: '/settings', text: '收单管理' },
    ]) {
      await gotoFresh(teacher, `${TEACHER_URL}/#${item.path}`, {
        waitUntil: 'domcontentloaded',
        timeout: 60000,
      })
      await waitBody(teacher, item.text, 60000)
      await waitBodyTheme(teacher, 'zi-scheme-dark', 30000)
      const appearance = await readAppearance(teacher, 'body', ['.page-card'])
      const background = appearance.blocks[0].style && appearance.blocks[0].style.background
      const match = background && background.match(/rgba?\(([^)]+)\)/)
      const channels = match ? match[1].split(/[,/\s]+/).filter(Boolean).map(Number) : []
      const brightness = channels.length >= 3 ? (channels[0] + channels[1] + channels[2]) / 3 : 255
      if (brightness > 90) throw new Error(`老师端${item.name}屏深色下卡片底色仍然偏亮：${background}`)
      if (viewport.mobile) await assertNoOverflow(teacher, `老师端${item.name}屏（深色）`)
      result.pages[`teacher-${item.name}`] = background
    }
  }, teacher)

  await step('深色：与纸面工单、信号牌两套皮肤交叉显示', async () => {
    // 学生端：面板仍是三选一，没有深色开关，选中项变了底色也跟着变
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/mine`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '全部', 60000)
    await waitVisibleClass(student, '.page', 'zi-scheme-dark')
    await clickSelector(student, '.theme-entry')
    await waitVisible(student, '.theme-sheet', 20000)
    const sheet = await student.evaluate(() => ({
      options: [...document.querySelectorAll('.theme-sheet .theme-option')].map((node) =>
        node.getAttribute('data-theme'),
      ),
      schemeSwitches: [...document.querySelectorAll('.theme-sheet button')].filter((node) =>
        (node.innerText || '').includes('深色'),
      ).length,
    }))
    if (sheet.options.length !== 3) {
      throw new Error(`皮肤面板被改成了 ${sheet.options.length} 项：${sheet.options.join('、')}`)
    }
    if (sheet.schemeSwitches !== 0) {
      throw new Error('皮肤面板里多出了手动深色开关')
    }
    // 纸面工单（面板选完会自己收起，量之前按需重新打开）
    await clickSelector(student, '.theme-option[data-theme="press"]', '.theme-sheet')
    await waitVisibleClass(student, '.page', 'zi-theme-press')
    await sleep(400)
    const pressAppearance = await readAppearance(student, '.page', ['.card'])
    const pressBackground = pressAppearance.blocks[0].style.background
    result.skins.studentPress = pressBackground
    if (!pressAppearance.classes.includes('zi-scheme-dark')) {
      throw new Error(`学生端纸面工单深色下丢了深浅状态：${pressAppearance.classes.join(' ')}`)
    }
    // 订单里的纸面工单：深色下这张单子的纸面颜色不变（打印观感不受深浅影响）
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/detail?id=${orderId}`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '取件码 · 凭此码到文印室取件', 60000)
    await waitVisibleClass(student, '.page', 'zi-theme-press')
    await waitVisibleClass(student, '.page', 'zi-scheme-dark')
    const ticket = await student.evaluate(() => {
      const visible = (selector) =>
        [...document.querySelectorAll(selector)].filter((node) => node.getClientRects().length > 0).pop() || null
      const block = visible('.code-block')
      const page = visible('.page')
      return {
        paper: block ? getComputedStyle(block).backgroundColor : '',
        ink: block ? getComputedStyle(block.querySelector('.code')).color : '',
        page: page ? getComputedStyle(page).backgroundColor : '',
      }
    })
    result.skins.printPaper = ticket
    if (ticket.paper !== 'rgb(248, 244, 233)') {
      throw new Error(`深色下纸面工单的纸面颜色被改成了 ${ticket.paper}（应为浅色的 rgb(248, 244, 233)）`)
    }
    if (ticket.page === ticket.paper) {
      throw new Error('深色下纸面工单与页面底色一样，深浅没生效')
    }
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/mine`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '全部', 60000)
    await waitVisibleClass(student, '.page', 'zi-theme-press')
    await waitVisibleClass(student, '.page', 'zi-scheme-dark')
    await clickSelector(student, '.theme-entry')
    await waitVisible(student, '.theme-sheet', 20000)
    await clickSelector(student, '.theme-option[data-theme="signal"]', '.theme-sheet')
    await waitVisibleClass(student, '.page', 'zi-theme-signal')
    await waitVisibleClass(student, '.page', 'zi-scheme-dark')
    await sleep(400)
    const signalAppearance = await readAppearance(student, '.page', ['.card'])
    const signalBackground = signalAppearance.blocks[0].style.background
    result.skins.studentSignal = signalBackground
    if (!signalAppearance.classes.includes('zi-scheme-dark')) {
      throw new Error(`学生端信号牌深色下丢了深浅状态：${signalAppearance.classes.join(' ')}`)
    }
    if (signalBackground === pressBackground) {
      throw new Error(`学生端两套皮肤在深色下卡片底色一样：${signalBackground}`)
    }
    if (viewport.mobile) await assertNoOverflow(student, '学生端我的页（信号牌 · 深色）')

    // 老师端：侧边栏（手机在抽屉里）切到信号牌，深色状态保持
    const root = viewport.mobile ? '.drawer-body' : '.aside'
    if (viewport.mobile) {
      await clickSelector(teacher, '.menu-trigger')
      await waitVisible(teacher, '.drawer-body', 20000)
    }
    await waitVisible(teacher, `${root} .theme-option`, 20000)
    await clickSelector(teacher, '.theme-option[data-theme="signal"]', root)
    await waitBodyTheme(teacher, 'zi-theme-signal', 20000)
    await waitBodyTheme(teacher, 'zi-scheme-dark', 20000)
    if (viewport.mobile) {
      await teacher.keyboard.press('Escape')
      await sleep(600)
    }
    const teacherSignal = await readAppearance(teacher, 'body', ['.page-card'])
    result.skins.teacherSignal = teacherSignal.blocks[0].style.background
    if (viewport.mobile) await assertNoOverflow(teacher, '老师端工作台（信号牌 · 深色）')
  })

  await step('深色：浅色与深色各截一份学生端我的与老师端工作台', async () => {
    // 回到默认皮肤（纸面工单），先截深色
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/mine`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '全部', 60000)
    await clickSelector(student, '.theme-entry')
    await waitVisible(student, '.theme-sheet', 20000)
    await clickSelector(student, '.theme-option[data-theme="press"]', '.theme-sheet')
    await waitVisibleClass(student, '.page', 'zi-theme-press')
    await waitVisibleClass(student, '.page', 'zi-scheme-dark')
    await student.bringToFront()
    // 换肤轻提示会盖在页面中间，等它自己消失再截
    await waitGone(student, '界面皮肤已切换', 8000).catch(() => {})
    await sleep(400)
    const darkStudent = await shoot(student, `dark-student-mine-${viewport.name}`)

    await gotoFresh(teacher, `${TEACHER_URL}/#/dashboard`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(teacher, '接单队列', 60000)
    await waitBodyTheme(teacher, 'zi-scheme-dark', 30000)
    if (viewport.mobile) {
      const root = '.drawer-body'
      await clickSelector(teacher, '.menu-trigger')
      await waitVisible(teacher, root, 20000)
      await clickSelector(teacher, '.theme-option[data-theme="press"]', root)
      await waitBodyTheme(teacher, 'zi-theme-press', 20000)
      await teacher.keyboard.press('Escape')
      await sleep(600)
    } else {
      await clickSelector(teacher, '.theme-option[data-theme="press"]', '.aside')
      await waitBodyTheme(teacher, 'zi-theme-press', 20000)
    }
    await waitVisible(teacher, '.page-card', 20000)
    await teacher.bringToFront()
    await sleep(400)
    const darkTeacher = await shoot(teacher, `dark-teacher-dashboard-${viewport.name}`)

    result.contrast.push(await measureTextContrast(student, '.page', '学生端我的（深色）'))

    // 系统回浅色：界面自己回浅色，不用刷新
    await setColorScheme(student, 'light')
    await waitVisibleClass(student, '.page', 'zi-scheme-light')
    await sleep(400)
    const lightStudent = await shoot(student, `light-student-mine-${viewport.name}`)
    await setColorScheme(teacher, 'light')
    await waitBodyTheme(teacher, 'zi-scheme-light', 20000)
    await sleep(400)
    const lightTeacher = await shoot(teacher, `light-teacher-dashboard-${viewport.name}`)

    if (darkStudent.digest === lightStudent.digest) {
      throw new Error('学生端深色与浅色截图一模一样，深浅没生效')
    }
    if (darkTeacher.digest === lightTeacher.digest) {
      throw new Error('老师端深色与浅色截图一模一样，深浅没生效')
    }
    result.screenshots = [
      darkStudent.file,
      lightStudent.file,
      darkTeacher.file,
      lightTeacher.file,
    ]

    // 对比度：正文与背景，最差的一组判不过就失败
    const failures = result.contrast.flatMap((item) => item.failures || [])
    const worst = result.contrast
      .flatMap((item) => item.worst || [])
      .sort((a, b) => a.ratio - b.ratio)[0]
    result.worstContrast = worst
    for (const item of result.contrast) {
      console.log(
        `      对比度 ${item.label}：抽查 ${item.checked} 处，最差 ${(item.worst[0] || {}).ratio}:1`,
      )
    }
    if (worst) {
      console.log(
        `      最差一组：${worst.ratio}:1「${worst.text}」${worst.color} 于 ${worst.background}`,
      )
    }
    if (failures.length) {
      throw new Error(
        `深色下正文对比度低于 4.5:1：${failures
          .map((item) => `${item.ratio}:1「${item.text}」`)
          .join('、')}`,
      )
    }
  })

  return result
}

// ---------------------------------------------------------------- 入口
async function main() {
  console.log(`智印助手浏览器回归 · ${STAMP}`)
  console.log(`  学生端 ${STUDENT_URL}`)
  console.log(`  老师端 ${TEACHER_URL}`)
  console.log(`  后端   ${BACKEND_URL}`)

  if (START) {
    console.log('\n启动隔离环境 …')
    await startStack()
  } else {
    await waitHttp(`${BACKEND_URL}/api/health`, 5000).catch(() => {
      throw new Error(`后端不可用：${BACKEND_URL}/api/health；先起服务或加 --start`)
    })
    await waitHttp(`${STUDENT_URL}/`, 15000).catch(() => {
      throw new Error(`学生端不可用：${STUDENT_URL}；先起服务或加 --start`)
    })
    await waitHttp(`${TEACHER_URL}/`, 15000).catch(() => {
      throw new Error(`老师端不可用：${TEACHER_URL}；先起服务或加 --start`)
    })
  }

  const browser = await puppeteer.launch({
    headless: true,
    protocolTimeout: 300000,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  })
  const results = []
  let failed = null
  try {
    for (const viewport of VIEWPORTS) {
      console.log(`\n视口 ${viewport.name}×${viewport.height}`)
      try {
        results.push(await runViewport(browser, viewport))
      } catch (error) {
        failed = { viewport: viewport.name, error }
        break
      }
    }
  } finally {
    await browser.close()
  }

  const summary = {
    stamp: STAMP,
    studentUrl: STUDENT_URL,
    teacherUrl: TEACHER_URL,
    backendUrl: BACKEND_URL,
    viewports: results,
    failed: failed
      ? {
          viewport: failed.viewport,
          stepIndex: failed.error.stepIndex,
          step: failed.error.step,
          message: failed.error.message,
        }
      : null,
  }
  fs.writeFileSync(path.join(ARTIFACTS, 'result.json'), `${JSON.stringify(summary, null, 2)}\n`)

  if (failed) {
    console.error(
      `\n失败：视口 ${failed.viewport} 卡在第 ${failed.error.stepIndex} 步「${failed.error.step}」`,
    )
    console.error(`原因：${failed.error.message}`)
    console.error(`现场：${ARTIFACTS}`)
    return 1
  }
  console.log(`\n全部通过：${results.map((item) => item.viewport).join('、')} 两个视口`)
  console.log(`记录：${ARTIFACTS}/result.json`)
  return 0
}

main()
  .then((code) => {
    stopStack()
    process.exitCode = code
  })
  .catch((error) => {
    console.error(`\n启动失败：${error.stack || error.message}`)
    stopStack()
    process.exitCode = 2
  })
