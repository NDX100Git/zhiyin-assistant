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
      await clickByText(student, '模拟支付')
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

    if (problems.length) {
      throw Object.assign(new Error(`页面/接口有 ${problems.length} 条异常记录`), {
        step: '页面错误汇总',
        stepIndex,
      })
    }
    return { viewport: label, orderNo: order.orderNo, pickup: order.pickup, multiFile, fourFile }
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

  await step('多文件：下单并支付', async () => {
    await clickByText(student, '确认下单')
    await waitBody(student, '下单成功', 60000)
    Object.assign(order, await readOrderResult(student))
    order.id = await orderIdFromApi(student, order.orderNo)
    await clickByText(student, '模拟支付')
    await waitGone(student, '模拟支付', 30000)
  })

  await step('多文件：老师端队列列出两份文件', async () => {
    await searchQueue(teacher, order.pickup)
    await waitRowText(teacher, order.pickup, 'thesis-v1.pdf')
    await waitRowText(teacher, order.pickup, 'thesis-v3.pdf')
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
    await clickByText(student, '模拟支付')
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
