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
      if (process.platform === 'win32') {
        // Windows 不支持负数 PID 的进程组语义，process.kill(-pid) 会直接抛错，
        // 结果就是 dev server 与后端一直挂着、node 不退出。用 taskkill /T 连子进程一起收。
        spawnSync('taskkill', ['/F', '/T', '/PID', String(child.pid)], { stdio: 'ignore' })
      } else {
        process.kill(-child.pid, 'SIGTERM')
      }
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
  // 直接 spawn node_modules/.bin/uni 在 Windows 上会 ENOENT：那边只有 uni.cmd / uni.ps1，
  // 无扩展名的那个是 Unix shell 脚本，Windows 不认。统一用 node 执行 CLI 的 js 入口，
  // 两个平台同一条路径，也不必绕 shell（Node 22 起 spawn .cmd 需要 shell:true，容易出幺蛾子）。
  const studentCli = path.join(
    REPO_ROOT, 'student-app', 'node_modules', '@dcloudio', 'vite-plugin-uni', 'bin', 'uni.js',
  )
  const teacherCli = path.join(
    REPO_ROOT, 'teacher-web', 'node_modules', 'vite', 'bin', 'vite.js',
  )
  for (const [label, cli] of [['学生端', studentCli], ['老师端', teacherCli]]) {
    if (!fs.existsSync(cli)) {
      throw new Error(`${label} CLI 不存在：${cli}，请先在对应目录执行 npm install`)
    }
  }

  startService(
    'student',
    process.execPath,
    [studentCli, '--host', '127.0.0.1', '--port', new URL(STUDENT_URL).port, '--strictPort'],
    { cwd: path.join(REPO_ROOT, 'student-app'), env: { VITE_API_BASE_URL: BACKEND_URL } },
  )
  startService(
    'teacher',
    process.execPath,
    [teacherCli, '--host', '127.0.0.1', '--port', new URL(TEACHER_URL).port, '--strictPort'],
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
 * 提交换文件申请。
 *
 * 上传完之后页面还要取页数、重新报价，这期间按钮文案是「提交中…」且处于 disabled，
 * 只等「确认变更」这个静态标题出现就去点，会点在禁用状态的按钮上（dev 模式按需编译
 * 慢的时候必现）。这里等中间态消失、按钮文案恢复成「提交换文件申请」再点。
 */
async function submitChangeRequest(page, confirmText, timeout = 90000) {
  await waitBody(page, confirmText, timeout)
  await waitGone(page, '正在读取页数…', 60000)
  await waitBody(page, '提交换文件申请', 60000)
  await clickByText(page, '提交换文件申请')
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

/**
 * 点掉 uni.showModal 的“确定”（学生端点「取消订单」会用到）。
 *
 * 两个坑：
 * 1. uni H5 的确认框是 <uni-modal> + div.uni-modal__btn，不是 <button>；
 * 2. 项目没配 uni 语言包，默认按钮文案是 OK / Cancel，不是中文。
 * 所以不按文字找，直接点主按钮，再校验它确实是确认键。
 */
async function confirmUniModal(page, timeout = 20000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const text = await page.evaluate(() => {
      const modal = [...document.querySelectorAll('uni-modal, .uni-modal')]
        .filter((element) => element.getClientRects().length > 0)
        .pop()
      if (!modal) return ''
      const button = modal.querySelector('.uni-modal__btn_primary')
      if (!button) return ''
      button.click()
      return (button.innerText || button.textContent || '').trim()
    })
    if (text) {
      if (!/^(OK|确定|确认)$/.test(text)) {
        throw new Error(`确认框主按钮文案异常：「${text}」，可能点错了按钮`)
      }
      return text
    }
    await sleep(300)
  }
  throw new Error('学生端取消订单的确认框没有出现')
}

/** 给 el-input-number 里的原生 input 赋值（v-model 跟随 input 事件）。 */
async function setNumberInput(page, selector, value) {
  const ok = await page.evaluate(
    ({ selector, value }) => {
      const input =
        document.querySelector(`${selector} input`) || document.querySelector(selector)
      if (!input) return false
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      setter.call(input, String(value))
      input.dispatchEvent(new Event('input', { bubbles: true }))
      input.dispatchEvent(new Event('change', { bubbles: true }))
      input.blur()
      return true
    },
    { selector, value },
  )
  if (!ok) throw new Error(`找不到金额输入框：${selector}`)
}

/** 读 el-input-number 里原生 input 当前显示的值。 */
async function readNumberInput(page, selector) {
  return page.evaluate((selector) => {
    const input = document.querySelector(`${selector} input`) || document.querySelector(selector)
    return input ? String(input.value).trim() : ''
  }, selector)
}

/** 最近一个可见弹窗的正文（弹窗里没渲染出来就算空）。 */
async function visibleDialogText(page) {
  return page.evaluate(() => {
    const dialogs = [...document.querySelectorAll('.el-dialog')].filter(
      (element) => element.getClientRects().length > 0,
    )
    const dialog = dialogs[dialogs.length - 1]
    return dialog ? dialog.innerText || dialog.textContent || '' : ''
  })
}

/** 老师端队列接口的原始返回（带管理员 token），用来算“还有多少单没显示”。 */
async function fetchQueueFromPage(page, pageSize = 60) {
  const body = await page.evaluate(
    async ({ backendUrl, pageSize }) => {
      const token = localStorage.getItem('zhiyin_admin_token')
      if (!token) return { error: 'localStorage 里没有管理员 token' }
      const response = await fetch(`${backendUrl}/api/admin/queue?page=1&page_size=${pageSize}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      return response.json()
    },
    { backendUrl: BACKEND_URL, pageSize },
  )
  if (!body || body.code !== 0) throw new Error(`队列接口返回 ${JSON.stringify(body)}`)
  return body.data
}

/** 队列末尾的「还有 N 单未显示」提示文案，没有就是空串。 */
async function readQueueNote(page) {
  return page.evaluate(() => {
    const note = [...document.querySelectorAll('.queue-more-note')].filter(
      (element) => element.getClientRects().length > 0,
    )[0]
    return note ? (note.innerText || note.textContent || '').trim() : ''
  })
}

/**
 * 页面上真的列出来多少单：桌面是表格行，手机是一单一张卡。
 */
const QUEUE_ROW_SELECTOR = '.order-card, .el-table__row'
/** 队列卡片里的表格行（勾选只在桌面表格里有，手机卡片没有复选框）。 */
const QUEUE_TABLE_ROW_SELECTOR = '.table-card .el-table__body-wrapper .el-table__row'
/** 后端 /api/admin/queue 的 page_size 上限（DashboardView.vue 的 QUEUE_PAGE_SIZE_MAX）。 */
const QUEUE_PAGE_SIZE_MAX = 200
/** 轮询一屏的条数（DashboardView.vue 的 QUEUE_PAGE_SIZE_POLL）。 */
const QUEUE_PAGE_SIZE_POLL = 60
/** 「加载更多」每次多要的条数（DashboardView.vue 的 QUEUE_LOAD_MORE_STEP）。 */
const QUEUE_LOAD_MORE_STEP = 60

/**
 * 同一帧里读全「页面列出来多少单」与「页面对隐藏了多少单的说法」。
 *
 * 必须一次 evaluate 读完：队列每 5 秒轮询一次，条数会在手点查询的 200 条与轮询的
 * 60 条之间来回切，分两次读会读到两个不同状态，算出一笔对不上的差额。
 */
async function readQueueSnapshot(page) {
  return page.evaluate((rowSelector) => {
    const visible = (element) => element.getClientRects().length > 0
    const rows = [...document.querySelectorAll(rowSelector)].filter(visible)
    const note = [...document.querySelectorAll('.queue-more-note')].filter(visible)[0]
    const loadMore = [...document.querySelectorAll('.queue-load-more')].filter(visible)[0]
    return {
      rows: rows.length,
      note: note ? (note.innerText || note.textContent || '').trim() : '',
      hasLoadMore: Boolean(loadMore),
    }
  }, QUEUE_ROW_SELECTOR)
}

/**
 * 比对「接口 total − 页面列出来的条数」与「页面自己写的隐藏数」，返回 null 表示自洽。
 *
 * 提示里的隐藏数是页面拿 total 减去当前列表条数算出来的，而列表条数取决于页面这次
 * 请求的 page_size（轮询 60、手点查询/刷新 200、「加载更多」+60）。回归写死 60 去算
 * 预期，会算出一个页面根本不会显示的差额：库里只剩 163 单时，页面拉满 200 条已经
 * 全部显示，60 条口径却报「还有 103 单没显示」，把正确的空提示判成失败。
 * 所以只核对页面自己的账：它说隐藏了多少单，就得和它实际列出来的条数对得上。
 *
 * 产品文案见 DashboardView.vue 的 queue-more-note 与 queueAtCap：
 * - 没隐藏：不出提示，也不给「加载更多」；
 * - 隐藏了但没顶到后端单页上限：写「共 N 单，还有 M 单未显示…」并给「加载更多」；
 * - 已经拉满 200 单却还有单：只能写「已显示前 200 单…还有 M 单未显示」，不再给按钮。
 */
function queueNoteProblem(snapshot, total) {
  const hidden = Math.max(total - snapshot.rows, 0)
  if (hidden === 0) {
    if (snapshot.note) {
      return `接口 total ${total}、页面列出 ${snapshot.rows} 单，已经全部显示，却出现提示：「${snapshot.note}」`
    }
    if (snapshot.hasLoadMore) {
      return `接口 total ${total}、页面列出 ${snapshot.rows} 单，已经全部显示，却给了「加载更多」`
    }
    return null
  }
  if (!snapshot.note.includes(`还有 ${hidden} 单未显示`)) {
    return `接口 total ${total}、页面列出 ${snapshot.rows} 单，还差 ${hidden} 单没显示，提示却是：「${snapshot.note}」`
  }
  if (snapshot.rows >= QUEUE_PAGE_SIZE_MAX) {
    if (!snapshot.note.includes(`已显示前 ${QUEUE_PAGE_SIZE_MAX} 单`)) {
      return (
        `页面已拉满 ${QUEUE_PAGE_SIZE_MAX} 单、还有 ${hidden} 单没显示，` +
        `提示该说「已显示前 ${QUEUE_PAGE_SIZE_MAX} 单」，实际：「${snapshot.note}」`
      )
    }
    if (snapshot.hasLoadMore) {
      return (
        `页面已拉满 ${QUEUE_PAGE_SIZE_MAX} 单、还有 ${hidden} 单没显示，` +
        `不该再给「加载更多」：「${snapshot.note}」`
      )
    }
    return null
  }
  if (!snapshot.note.includes(`共 ${total} 单`)) {
    return `提示里没写总条数（应写「共 ${total} 单」）：「${snapshot.note}」`
  }
  if (!snapshot.hasLoadMore) {
    return `还有 ${hidden} 单没显示，却没给「加载更多」：「${snapshot.note}」`
  }
  return null
}

/**
 * 等到页面真的列出至少 min 单。
 *
 * 请求发出去不等于表格已经渲染：el-table 拿到数据后还要一帧才出 `<tr>`，
 * 「加载更多之后条数没缩水」必须在行真的在 DOM 里之后读，否则读到的是上一帧。
 */
async function waitQueueRows(page, min, timeout = 15000) {
  const deadline = Date.now() + timeout
  let snapshot = await readQueueSnapshot(page)
  while (snapshot.rows < min && Date.now() < deadline) {
    await sleep(200)
    snapshot = await readQueueSnapshot(page)
  }
  return snapshot
}

/** 桌面表格里当前勾选着哪些行（按订单号读，跨轮询比对用）。 */
async function readQueueCheckedRows(page) {
  return page.evaluate((rowSelector) => {
    return [...document.querySelectorAll(rowSelector)]
      .filter((row) => row.querySelector('.el-checkbox.is-checked'))
      .map((row) => {
        const cells = row.querySelectorAll('td')
        return ((cells[1] && cells[1].innerText) || '').trim()
      })
      .filter(Boolean)
  }, QUEUE_TABLE_ROW_SELECTOR)
}

/**
 * 勾上队列第一行（只有桌面表格有复选框），返回这一行的订单号。
 *
 * 用 DOM 直接 click 而不是点坐标：轮询随时会盖上 v-loading 遮罩，
 * 坐标点击会被遮罩拦掉，DOM click 不会。
 */
async function checkFirstQueueRow(page) {
  const picked = await page.evaluate((rowSelector) => {
    const row = document.querySelector(rowSelector)
    if (!row) return { ok: false, reason: '队列表格里没有任何一行' }
    const box = row.querySelector('.el-checkbox')
    if (!box) return { ok: false, reason: '第一行没有复选框' }
    const cells = row.querySelectorAll('td')
    const orderNo = ((cells[1] && cells[1].innerText) || '').trim()
    box.click()
    return { ok: true, orderNo }
  }, QUEUE_TABLE_ROW_SELECTOR)
  if (!picked.ok) throw new Error(`勾选队列第一行失败：${picked.reason}`)
  const checked = await readQueueCheckedRows(page)
  if (!checked.includes(picked.orderNo)) {
    throw new Error(
      `勾选后没读回选中行：勾的是 ${picked.orderNo}，实际勾着 ${checked.join(',') || '无'}`,
    )
  }
  return picked.orderNo
}

/**
 * 打开一个假的 `/api/admin/queue` 返回，其余请求照常放行。
 *
 * `buildData(pageSize)` 每次请求都会重新造一份数据，并拿到页面这次请求的 page_size：
 * 要验证「加载更多之后条数不缩水」，假数据就必须像真后端一样按请求条数返回，
 * 否则测到的只是「一份固定 60 条的假数据」。
 * `records` 按顺序记下页面真实请求过的 `page_size`，后面的断言全靠这份记录：
 * 「点过加载更多之后，下一拍轮询按多少条拉」就是要看请求参数，而不是看页面文案。
 */
async function openFakeQueue(page, buildData) {
  const records = []
  await page.setRequestInterception(true)
  const handler = (request) => {
    const url = request.url()
    if (request.method() === 'GET' && url.includes('/api/admin/queue')) {
      const pageSize = Number(new URL(url).searchParams.get('page_size')) || 0
      records.push(pageSize)
      request.respond({
        status: 200,
        contentType: 'application/json',
        // 页面与接口不同源，不带 CORS 头浏览器会直接拦掉，测到的就只是网络错误
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Headers': '*',
          'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
        },
        body: JSON.stringify({ code: 0, message: 'ok', data: buildData(pageSize) }),
      })
      return
    }
    request.continue()
  }
  page.on('request', handler)
  return {
    records,
    async close() {
      page.off('request', handler)
      await page.setRequestInterception(false)
    },
  }
}

/**
 * 把真实队列项复制成 n 条假数据（每条 id / 订单号 / 文件 id 都唯一）。
 *
 * 造 200 条不能直接复用同一个对象：el-table 的 row-key 是 id，重复 key 会让行渲染
 * 与 DOM 数量对不上，断言就变成测 Element Plus 的容忍度了。
 */
function cloneQueueItems(items, count) {
  const source = items.length ? items : [{}]
  return Array.from({ length: count }, (_, index) => {
    const base = source[index % source.length]
    const files = (base.files || []).map((file, fileIndex) => ({
      ...file,
      id: `fake-${index}-file-${fileIndex + 1}`,
    }))
    return {
      ...base,
      id: `fake-${index + 1}`,
      order_no: `FAKE${String(index + 1).padStart(4, '0')}`,
      pickup_code: `F-${index + 1}`,
      files,
    }
  })
}

/**
 * 用学生端真实接口批量下单，造出「超过 200 单」的真实数据。
 *
 * 只调学生端自己的 `/api/order/create`，复用回归里刚上传的文件（下单宽限期 48 小时），
 * 不碰后端代码、不直接写库。实测 19ms/ 单，造 200 多单不到 5 秒。
 * 第一单用哪个文件当模版是逐个试的：历史上传的文件可能已被定时清理，
 * 换下一单再试比直接报错稳。返回 `{ created, elapsed }` 或 `{ error, created }`。
 */
async function bulkCreateOrders(page, count) {
  return page.evaluate(
    async ({ backendUrl, count }) => {
      const key = Object.keys(localStorage).find((item) => item.includes('zhiyin_token'))
      const token = key ? localStorage.getItem(key) : ''
      if (!token) {
        return { error: `localStorage 里没有 token：${Object.keys(localStorage).join(',')}` }
      }
      const auth = { Authorization: `Bearer ${token}` }
      const list = await fetch(`${backendUrl}/api/order/list`, { headers: auth }).then((r) =>
        r.json(),
      )
      if (list.code !== 0) return { error: `订单列表返回 ${list.code} ${list.message}` }
      const items = (list.data.items || []).filter((item) => item.status !== 'cancelled')
      if (items.length === 0) return { error: '学生端一单都没有，拿不到可复用的文件' }

      const started = Date.now()
      const create = (payload) =>
        fetch(`${backendUrl}/api/order/create`, {
          method: 'POST',
          headers: { ...auth, 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        }).then((r) => r.json())

      let template = null
      let created = 0
      for (const item of items.slice(0, 5)) {
        const detail = await fetch(`${backendUrl}/api/order/${item.id}`, { headers: auth }).then(
          (r) => r.json(),
        )
        if (detail.code !== 0 || !detail.data || !detail.data.file_key) continue
        const source = detail.data
        const payload = {
          file_key: source.file_key,
          file_name: source.file_name,
          page_count: source.page_count,
          print_side: 2,
          color_mode: 0,
          copies: 1,
          paper_size: 'A4',
        }
        const first = await create(payload)
        // 这一单的文件已经被清理或过期：换下一单当模版，而不是直接失败
        if (first.code !== 0) continue
        template = payload
        created = 1
        break
      }
      if (!template) return { error: '5 个候选单的文件都下不了单（可能已被定时清理）' }

      while (created < count) {
        const body = await create(template)
        if (body.code !== 0) {
          return {
            error: `第 ${created + 1} 单造单失败：${body.code} ${body.message}`,
            created,
            elapsed: Date.now() - started,
          }
        }
        created += 1
      }
      return { created, elapsed: Date.now() - started }
    },
    { backendUrl: BACKEND_URL, count },
  )
}

/**
 * 等到页面再发一次指定 page_size 的队列请求（轮询每 5 秒一次）。
 * `from` 之后新出现的记录才算，避免拿历史请求充数。
 */
async function waitQueuePageSize(records, size, from = 0, timeout = 25000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const hit = records.slice(from).findIndex((value) => value === size)
    if (hit !== -1) return from + hit
    await sleep(200)
  }
  return -1
}

/**
 * 点一次「加载更多」，等到页面真的按预期条数重拉了队列。
 *
 * Element Plus 的按钮在 loading 时会吞掉点击，而队列每 5 秒正好在轮询（那一瞬按钮刚好
 * 在 loading），所以点空一次是可能的。这里只重试「点击没被页面接受」这一种情况：
 * 断言本身不变——必须真的发出预期条数的请求。
 */
async function clickLoadMore(page, records, size, { attempts = 3, timeout = 8000 } = {}) {
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const before = records.length
    await clickByText(page, '加载更多')
    const at = await waitQueuePageSize(records, size, before, timeout)
    if (at !== -1) return at
  }
  return -1
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

/** 记录老师端「开始打印」接口调用，用来断言第一次点击真的没有调接口。 */function recordStartCalls(page) {
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

/**
 * 摘掉「这一步有意制造的失败」在异常汇总里的记录。
 *
 * watchPage 会记下所有 console 错误与 4xx/5xx 接口；本轮的回归里有两步要故意
 * 让报价与设置页接口失败，不清掉就会把整轮判红。只按「这一步新增的 + 匹配谓词」
 * 摘除，其他地方的同类异常照旧计数。
 */
function dropExpectedProblems(problems, from, predicate) {
  let dropped = 0
  for (let index = problems.length - 1; index >= from; index -= 1) {
    if (predicate(problems[index])) {
      problems.splice(index, 1)
      dropped += 1
    }
  }
  return dropped
}

/** 下单页的「确认下单」按钮现在是不是禁用状态。 */
function orderButtonState(page) {
  return page.evaluate(() => {
    const button = document.querySelector('.plan-card .primary')
    if (!button) return 'missing'
    return button.hasAttribute('disabled') || button.disabled === true ? 'disabled' : 'enabled'
  })
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
      await submitChangeRequest(student, '2. 确认变更')
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
    const priceAndRefund = await runPriceAndRefundPhase({ student, teacher, viewport, step })
    const darkMode = await runDarkModePhase({
      student,
      teacher,
      viewport,
      step,
      orderId: startGuard.id,
    })
    const copyAndRetry = await runCopyAndRetryPhase({
      student,
      teacher,
      viewport,
      problems,
      step,
    })
    // 自带 AI 密钥两条：界面不在本分支时显式跳过并标注依赖（不写恒真断言）
    const studentKey = await runStudentKeyPhase({ student, step })

    if (problems.length) {
      throw Object.assign(
        new Error(
          `页面/接口有 ${problems.length} 条异常记录：${problems.slice(0, 6).join('；')}`,
        ),
        {
          step: '页面错误汇总',
          stepIndex,
        },
      )
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
      copyAndRetry,
      priceAndRefund,
      studentKey,
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
    await submitChangeRequest(student, '3. 确认变更')
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
    await submitChangeRequest(student, '2. 确认变更')
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

// ---------------------------------------------------------------- 改价与退款
/**
 * 资金口径收口：确认价不能被系统价抹掉，已付款单的取消与退款各有出口。
 *
 * 流程（一单到底，不另造脏数据）：
 * 1. 用 2 页的 v2 下一单并支付（已付款排队单）；用 1 页的 v1 换文件，
 *    这样「系统重算价 < 学生已付」——确认价可以落在两者之间，既与系统价不同，
 *    又不超过已收，后面的退款才说得清。
 * 2. 老师把确认价改成两者之间（不是系统价），走完「开始打印 → 打印完成」，
 *    断言弹窗预填的是确认价，落库金额仍是确认价。
 * 3. 这一单再标记异常 → 关单时填退款金额，断言「已退款 X 元」与超额拦截。
 * 4. 另下一单不付款：待支付单仍能自助取消；已付款单只能找老师。
 */
async function runPriceAndRefundPhase({ student, teacher, viewport, step }) {
  const order = { orderNo: '', pickup: '', id: 0 }
  const result = {}

  // ---- 1. 已付款排队单：取消按钮必须收起来，换成找老师的提示
  await step('改价：下一单并支付，学生端已付款排队单看不到取消按钮', async () => {
    await gotoFresh(student, `${STUDENT_URL}/#/pages/order/order`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '选择要打印的文件', 90000)
    // 用 2 页的 v2：换成 1 页的 v1 后系统价会降，确认价才有夹在中间的空间
    await pickFile(student, '选择文档', FIXTURES.v2)
    await waitBody(student, '3. 打印方案', 90000)
    await waitBody(student, '确认下单', 60000)
    await clickByText(student, '确认下单')
    await waitBody(student, '下单成功', 60000)
    Object.assign(order, await readOrderResult(student))
    order.id = await orderIdFromApi(student, order.orderNo)
    await clickByTextRetry(student, '模拟支付')
    await waitGone(student, '模拟支付', 30000)

    // 列表页：已付款排队单不该有「取消」，要有“找文印室老师”的提示
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/mine`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '全部', 60000)
    await waitBody(student, order.orderNo, 60000)
    const row = await student.evaluate((orderNo) => {
      const cards = [...document.querySelectorAll('.card.order')]
      const card = cards.find((element) => (element.innerText || '').includes(orderNo))
      if (!card) return { found: false }
      return {
        found: true,
        // uni-app 的 <button> 在 H5 里渲染成 <uni-button>，两种都要算上
        buttons: [...card.querySelectorAll('button, uni-button')].map((item) =>
          (item.innerText || item.textContent || '').trim(),
        ),
        hint: (() => {
          const node = card.querySelector('.cancel-hint')
          return node ? (node.innerText || node.textContent || '').trim() : ''
        })(),
      }
    }, order.orderNo)
    if (!row.found) throw new Error(`「我的订单」里找不到 ${order.orderNo}`)
    if (row.buttons.some((text) => text.includes('取消'))) {
      throw new Error(`已付款排队单还显示取消按钮：${row.buttons.join(' / ')}`)
    }
    if (!row.hint.includes('请联系文印室老师')) {
      throw new Error(`已付款排队单没有给出“找老师”的提示，实际提示：「${row.hint}」`)
    }
    result.mineHint = row.hint

    // 详情页同样不给取消入口
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/detail?id=${order.id}`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '取件码 · 凭此码到文印室取件', 60000)
    const detail = await student.evaluate(() => ({
      buttons: [...document.querySelectorAll('.actions button, .actions uni-button')].map(
        (item) => (item.innerText || item.textContent || '').trim(),
      ),
      hint: (() => {
        const node = document.querySelector('.cancel-hint')
        return node ? (node.innerText || node.textContent || '').trim() : ''
      })(),
    }))
    if (detail.buttons.some((text) => text.includes('取消'))) {
      throw new Error(`已付款排队单详情页还显示取消按钮：${detail.buttons.join(' / ')}`)
    }
    if (!detail.hint.includes('请联系文印室老师')) {
      throw new Error(`详情页没有给出“找老师”的提示，实际提示：「${detail.hint}」`)
    }
    result.detailHint = detail.hint
    if (viewport.mobile) await assertNoOverflow(student, '学生端已付款订单详情')
  })

  // ---- 2. 换文件 → 老师按「不是系统价」的确认价通过
  await step('改价：学生换文件，老师按高于系统价的确认价通过', async () => {
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/replace?id=${order.id}`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '当前订单', 60000)
    await pickFile(student, '选择文档', FIXTURES.v1)
    await submitChangeRequest(student, '2. 确认变更')
    await waitBody(student, '已提交一份换文件申请', 45000)

    const before = await orderDetailFromApi(student, order.id)
    result.paidCents = Number(before.amount_cents) || 0
    if (!before.file_change) throw new Error('学生提交后订单上没有待确认的换文件申请')
    const systemCents = Number(before.file_change.new_amount_cents) || 0
    if (!(result.paidCents > systemCents)) {
      throw new Error(
        `换文件后系统价 ${systemCents} 分不低于已付 ${result.paidCents} 分：` +
          '这一轮需要「已付 > 系统价」才能夹出确认价',
      )
    }
    // 确认价夹在“系统价”与“已付”之间：既不等于系统价（能证伪预填），也不超过已收（退款才成立）
    const confirmed = systemCents + Math.floor((result.paidCents - systemCents) / 2)
    result.systemCents = systemCents
    result.confirmCents = confirmed

    await searchQueue(teacher, order.pickup)
    await waitRowText(teacher, order.pickup, '文件待确认', 45000)
    await clickRowButton(teacher, order.pickup, '确认换文件')
    await waitBody(teacher, '系统重算价', 30000)
    const shownSystem = await visibleDialogText(teacher)
    if (!shownSystem.includes(money(systemCents))) {
      throw new Error(`确认弹窗里的系统重算价不是 ${money(systemCents)}：${shownSystem.slice(0, 200)}`)
    }
    await setNumberInput(teacher, '.file-change-dialog .el-input-number', confirmed / 100)
    await clickDialog(teacher, '通过')
    // 「排队中」在换文件之前就已经是这个状态，不能当信号；「已替换」才是确认落库后的标记
    await waitRowText(teacher, order.pickup, '已替换', 45000)

    const after = await waitOrderAmount(student, order.id, confirmed, 30000)
    if (Number(after.amount_cents) !== confirmed) {
      throw new Error(`确认换文件后落库金额是 ${after.amount_cents} 分，预期确认价 ${confirmed} 分`)
    }
  })

  // ---- 3. 打印完成：预填与提交都用当前成交价（本轮最高优先）
  await step('改价：「打印完成」预填成交价，提交后金额不被抹回系统价', async () => {
    // 换过文件后队列带「已替换未确认」：先下载确认，再走正常开始打印
    await teacher.evaluate(() => {
      window.open = () => null
    })
    await clickRowButton(teacher, order.pickup, '下载')
    await waitBody(teacher, '已确认看到新文件', 30000)
    await sleep(800)
    await clickRowButton(teacher, order.pickup, '开始打印')
    await clickDialog(teacher, '开始')
    await waitRowText(teacher, order.pickup, '打印中', 45000)

    await clickRowButton(teacher, order.pickup, '打印完成')
    await waitBody(teacher, '确认完成', 30000)
    const dialog = await visibleDialogText(teacher)
    if (!dialog.includes('当前成交价')) {
      throw new Error(`「打印完成」弹窗没有「当前成交价」这一项：${dialog.slice(0, 200)}`)
    }
    if (!dialog.includes(money(result.confirmCents))) {
      throw new Error(`弹窗里成交价不是 ${money(result.confirmCents)}：${dialog.slice(0, 200)}`)
    }
    const prefilled = await readNumberInput(teacher, '.complete-amount-input')
    const prefilledCents = Math.round(Number(prefilled) * 100)
    if (prefilledCents !== result.confirmCents) {
      throw new Error(
        `弹窗预填 ${prefilled} 元（${prefilledCents} 分），预期确认价 ${result.confirmCents} 分；` +
          `系统价 ${result.systemCents} 分又被拿回来了`,
      )
    }
    result.prefilledCents = prefilledCents

    await clickDialog(teacher, '确认完成')
    await waitRowText(teacher, order.pickup, '待取件', 45000)
    const done = await waitOrderAmount(student, order.id, result.confirmCents, 30000)
    if (Number(done.amount_cents) !== result.confirmCents) {
      throw new Error(
        `打印完成后落库金额是 ${done.amount_cents} 分，确认价 ${result.confirmCents} 分被抹掉了`,
      )
    }
    if (Number(done.amount_cents) === result.systemCents) {
      throw new Error('落库金额等于系统价：确认价被系统价覆盖了')
    }
    result.completedCents = Number(done.amount_cents)
    if (!done.paid) {
      throw new Error('确认价没超过已付款，这一单不该变成待补款，退款口径就测不到了')
    }
  })

  // ---- 4. 异常单处置：退款金额入口 + 超额拦截
  await step('退款：关闭异常单可填退款金额，超额被拦，成功提示写明已退款', async () => {
    await clickRowButton(teacher, order.pickup, '异常')
    await waitBody(teacher, '确认标记', 30000)
    await clickDialog(teacher, '确认标记')
    // 「异常」两个字在按钮里也有，不能当信号；只有异常单才有关闭异常单按钮
    await waitRowText(teacher, order.pickup, '关闭异常单', 45000)

    await clickRowButton(teacher, order.pickup, '关闭异常单')
    await waitVisible(teacher, '.cancel-abnormal-dialog', 30000)
    const dialog = await visibleDialogText(teacher)
    if (!dialog.includes('已收金额')) {
      throw new Error(`关闭异常单弹窗没有「已收金额」：${dialog.slice(0, 200)}`)
    }
    if (!dialog.includes(money(result.completedCents))) {
      throw new Error(`弹窗里的已收金额不是 ${money(result.completedCents)}：${dialog.slice(0, 200)}`)
    }
    const defaultRefund = await readNumberInput(teacher, '.cancel-refund-input')
    if (Math.round(Number(defaultRefund) * 100) !== result.completedCents) {
      throw new Error(`退款金额默认值 ${defaultRefund} 元，预期全额 ${money(result.completedCents)}`)
    }
    result.defaultRefundYuan = defaultRefund

    // 超过已收：前端当场拦住，用的就是后端 40062 的那句话
    await setNumberInput(teacher, '.cancel-refund-input', 999)
    await clickDialog(teacher, '确认关闭')
    await waitBody(teacher, '金额超出允许范围', 20000)
    const stillOpen = await teacher.evaluate(
      () =>
        [...document.querySelectorAll('.cancel-abnormal-dialog')].filter(
          (element) => element.getClientRects().length > 0,
        ).length > 0,
    )
    if (!stillOpen) throw new Error('退款金额超额时弹窗被关掉了，应该留在原地报错')
    result.overLimitBlocked = true

    // 回到全额再提交
    await setNumberInput(teacher, '.cancel-refund-input', result.completedCents / 100)
    await clickDialog(teacher, '确认关闭')
    const expected = `已退款 ${money(result.completedCents)}`
    await waitMessageText(teacher, expected, 30000)
    result.successMessage = expected
    await waitRowText(teacher, order.pickup, '已取消', 45000)
    const closed = await waitOrderStatus(student, order.id, 'cancelled', 30000)
    if (closed.status !== 'cancelled') {
      throw new Error(`关单后状态是 ${closed.status}，预期 cancelled`)
    }
  }, teacher)

  // ---- 5. 待支付单：学生仍能自助取消
  await step('取消：待支付单学生仍能自助取消，取消后状态变成已取消', async () => {
    const unpaid = { orderNo: '', pickup: '', id: 0 }
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
    Object.assign(unpaid, await readOrderResult(student))
    unpaid.id = await orderIdFromApi(student, unpaid.orderNo)

    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/mine`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '全部', 60000)
    await waitBody(student, unpaid.orderNo, 60000)
    const row = await student.evaluate((orderNo) => {
      const cards = [...document.querySelectorAll('.card.order')]
      const card = cards.find((element) => (element.innerText || '').includes(orderNo))
      if (!card) return { found: false }
      return {
        found: true,
        // uni-app 的 <button> 在 H5 里渲染成 <uni-button>，两种都要算上
        buttons: [...card.querySelectorAll('button, uni-button')].map((item) =>
          (item.innerText || item.textContent || '').trim(),
        ),
        hint: (() => {
          const node = card.querySelector('.cancel-hint')
          return node ? (node.innerText || node.textContent || '').trim() : ''
        })(),
      }
    }, unpaid.orderNo)
    if (!row.found) throw new Error(`「我的订单」里找不到 ${unpaid.orderNo}`)
    if (!row.buttons.some((text) => text === '取消')) {
      throw new Error(`待支付单找不到取消按钮，实际按钮：${row.buttons.join(' / ')}`)
    }
    if (row.hint) throw new Error(`待支付单不该出现“找老师”的提示：「${row.hint}」`)

    await clickCancelButtonInRow(student, unpaid.orderNo)
    await confirmUniModal(student)
    const cancelled = await waitOrderStatus(student, unpaid.id, 'cancelled', 30000)
    if (cancelled.status !== 'cancelled') {
      throw new Error(`学生取消后状态是 ${cancelled.status}，预期 cancelled`)
    }
    result.cancelledUnpaid = cancelled.order_no
  })

  // ---- 6. 队列只拉一屏时，必须说清“还有多少单没显示”
  await step('队列：只拉一屏时提示还有多少单未显示，并能加载更多', async () => {
    // 页面列出来多少单，取决于它这次请求的 page_size：轮询 60、手点查询/刷新 200、
    // 「加载更多」+60。把页面真实请求的 page_size 记下来，失败时能一眼看出按哪个口径算的。
    const seenPageSizes = []
    const onQueueResponse = (response) => {
      const url = response.url()
      if (!url.includes('/api/admin/queue')) return
      if (response.request().method() !== 'GET') return
      const size = Number(new URL(url).searchParams.get('page_size')) || 0
      if (!seenPageSizes.includes(size)) seenPageSizes.push(size)
    }
    teacher.on('response', onQueueResponse)
    try {
      await searchQueue(teacher, '')
      // 手点「查询」按后端一页上限 200 条拉，不是轮询的第一屏 60 条
      let waited = 0
      while (!seenPageSizes.includes(QUEUE_PAGE_SIZE_MAX) && waited < 30000) {
        await sleep(200)
        waited += 200
      }
      if (!seenPageSizes.includes(QUEUE_PAGE_SIZE_MAX)) {
        throw new Error(
          `点「查询」后没有按一页 ${QUEUE_PAGE_SIZE_MAX} 条拉队列，` +
            `实际请求 page_size：${seenPageSizes.join(',') || '无'}`,
        )
      }

      // 轮询每 5 秒把条数切回 60 条，两种状态都必须自洽，谁先对上算谁
      const deadline = Date.now() + 30000
      let snapshot = await readQueueSnapshot(teacher)
      let total = Number((await fetchQueueFromPage(teacher, 60)).total) || 0
      let problem = queueNoteProblem(snapshot, total)
      while (problem && Date.now() < deadline) {
        await sleep(700)
        snapshot = await readQueueSnapshot(teacher)
        total = Number((await fetchQueueFromPage(teacher, 60)).total) || 0
        problem = queueNoteProblem(snapshot, total)
      }
      if (problem) {
        throw new Error(
          `${problem}（页面请求过的 page_size：${seenPageSizes.join(',') || '无'}）`,
        )
      }
      result.listedRows = snapshot.rows
      result.realTotal = total
      result.realHidden = Math.max(total - snapshot.rows, 0)
      result.realNote = snapshot.note
      result.queuePageSizes = seenPageSizes.slice()
      console.log(
        `      队列：页面列出 ${snapshot.rows} 单 / 接口 total ${total} / ` +
          `隐藏 ${result.realHidden} / 页面请求过 page_size ${seenPageSizes.join(',')} / ` +
          `提示「${snapshot.note || '无'}」`,
      )
    } finally {
      teacher.off('response', onQueueResponse)
    }

    // 造一个必然超量的返回：total 抬高 34 单，items 仍是真实数据。
    // 这一单得在开拦截之前拿：开了拦截之后页面里的 fetch 也会被拦到假数据上。
    const real = await fetchQueueFromPage(teacher, 60)
    const faked = {
      ...real,
      total: (real.items || []).length + 34,
      page: 1,
      page_size: 60,
    }
    await teacher.setRequestInterception(true)
    const requests = []
    const handler = (request) => {
      const url = request.url()
      if (request.method() === 'GET' && url.includes('/api/admin/queue')) {
        requests.push(Number(new URL(url).searchParams.get('page_size')) || 0)
        request.respond({
          status: 200,
          contentType: 'application/json',
          // 页面在 3219、接口在 8218，是跳源请求；不带 CORS 头浏览器会直接拦掉，
          // 页面只能看到网络错误，测不到提示逻辑。
          headers: {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': '*',
            'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
          },
          body: JSON.stringify({ code: 0, message: 'ok', data: faked }),
        })
        return
      }
      request.continue()
    }
    teacher.on('request', handler)
    try {
      await clickByText(teacher, '查询')
      await waitBody(teacher, '还有 34 单未显示', 30000)
      const fakedNote = await readQueueNote(teacher)
      if (!fakedNote.includes(`共 ${faked.total} 单`)) {
        throw new Error(`提示里没写总条数：「${fakedNote}」`)
      }
      result.fakedNote = fakedNote

      // 先清空：上面「查询」自己就会发一个 200 条的请求，留着会让「加载更多」白捡一个
      requests.length = 0
      const loadMoreAt = await clickLoadMore(
        teacher,
        requests,
        QUEUE_PAGE_SIZE_POLL + QUEUE_LOAD_MORE_STEP,
      )
      if (loadMoreAt === -1) {
        throw new Error(
          `「加载更多」没有按更大条数重拉队列，点击后实际请求 page_size：${requests.join(',') || '无'}`,
        )
      }
      result.loadMorePageSizes = requests.slice()
      console.log(
        `      队列（假 total ${faked.total}）：点击后请求 page_size ${requests.join(',') || '无'}`,
      )
    } finally {
      teacher.off('request', handler)
      await teacher.setRequestInterception(false)
    }
    // 回到真实数据：刷新一次，别把假 total 带到后面的步骤里
    await clickByText(teacher, '刷新')
    await sleep(1200)
  }, teacher)

  // ---- 7. 点过「加载更多」之后，自动刷新必须保留已加载条数
  await step('队列：点过「加载更多」后，自动刷新保留已加载条数（不缩回第一屏）', async () => {
    // 老板已拍板：老师按 120 条在看某一单，下一拍 5 秒轮询不能把列表缩回 60 条。
    // 页头文案与实际行为必须同一个口径，先锁文案。
    const headerText = await teacher.evaluate(() => document.body.innerText || '')
    if (!headerText.includes('不会把你点「加载更多」多显示的单缩回第一屏')) {
      throw new Error(
        '队列页头文案没写「保留已加载条数」的新口径（点过「加载更多」后自动刷新不回第一屏）',
      )
    }

    // 假数据按请求的 page_size 造条数（上限 fakeTotal）：请求 60 给 60 条，请求 120 就给
    // 120 条。不这样做的话，固定返回 60 条的假数据会把「条数不缩水」测成恒真。
    // 先取真实单当模版：开了拦截之后页面里的 fetch 也会被拦到假数据上。
    const real = await fetchQueueFromPage(teacher, QUEUE_PAGE_SIZE_POLL)
    const fakeTotal = 150
    const fakePageSize = (pageSize) => {
      const items = cloneQueueItems(real.items || [], Math.min(pageSize, fakeTotal))
      return { ...real, items, total: fakeTotal, page: 1, page_size: pageSize }
    }
    // 库里没那么多单，造不出 150 单：真实单克隆到 150 总数（理由见 e2e/README.md）
    const queue = await openFakeQueue(teacher, fakePageSize)
    try {
      // 等一拍轮询把第一屏 60 条拉进来，这时「加载更多」按钮才出现
      if ((await waitQueuePageSize(queue.records, QUEUE_PAGE_SIZE_POLL, 0, 30000)) === -1) {
        throw new Error(
          `没等到第一屏 ${QUEUE_PAGE_SIZE_POLL} 条的轮询请求：${queue.records.join(',') || '无'}`,
        )
      }
      const firstScreen = await waitQueueRows(teacher, QUEUE_PAGE_SIZE_POLL)
      if (firstScreen.rows !== QUEUE_PAGE_SIZE_POLL) {
        throw new Error(`第一屏应列出 ${QUEUE_PAGE_SIZE_POLL} 单，实际 ${firstScreen.rows} 单`)
      }
      // 超量提示与「加载更多」按钮仍然要在（原有断言，不放宽）
      const firstProblem = queueNoteProblem(firstScreen, fakeTotal)
      if (firstProblem) throw new Error(`${firstProblem}（假 total ${fakeTotal}）`)
      if (!firstScreen.hasLoadMore) {
        throw new Error(
          `还隐藏 ${fakeTotal - QUEUE_PAGE_SIZE_POLL} 单却没给「加载更多」：「${firstScreen.note}」`,
        )
      }

      // 桌面视口先勾一行：整体替换列表会把这一行的勾选弄丢，按 id 合并更新不会
      const checkedRow = viewport.mobile ? '' : await checkFirstQueueRow(teacher)

      // 点「加载更多」：在第一屏 60 条的基础上 +60 = 120
      const loadMoreAt = await clickLoadMore(
        teacher,
        queue.records,
        QUEUE_PAGE_SIZE_POLL + QUEUE_LOAD_MORE_STEP,
      )
      if (loadMoreAt === -1) {
        throw new Error(
          `「加载更多」没有按 ${QUEUE_PAGE_SIZE_POLL + QUEUE_LOAD_MORE_STEP} 条重拉，实际：` +
            `${queue.records.slice(-5).join(',') || '无'}`,
        )
      }
      const afterLoadMore = await waitQueueRows(
        teacher,
        QUEUE_PAGE_SIZE_POLL + QUEUE_LOAD_MORE_STEP,
      )
      if (afterLoadMore.rows !== QUEUE_PAGE_SIZE_POLL + QUEUE_LOAD_MORE_STEP) {
        throw new Error(
          `「加载更多」后应列出 ${QUEUE_PAGE_SIZE_POLL + QUEUE_LOAD_MORE_STEP} 单，` +
            `实际 ${afterLoadMore.rows} 单`,
        )
      }

      // 连续两拍自动刷新：请求条数不能回落到第一屏，列表条数不能缩水，勾选不能丢
      let cursor = loadMoreAt + 1
      for (let tick = 1; tick <= 2; tick += 1) {
        const at = await waitQueuePageSize(
          queue.records,
          QUEUE_PAGE_SIZE_POLL + QUEUE_LOAD_MORE_STEP,
          cursor,
          25000,
        )
        if (at === -1) {
          throw new Error(
            `第 ${tick} 拍自动刷新没有按已加载条数 ` +
              `${QUEUE_PAGE_SIZE_POLL + QUEUE_LOAD_MORE_STEP} 条拉，已发出的请求：` +
              `${queue.records.slice(loadMoreAt).join(',') || '无'}`,
          )
        }
        cursor = at + 1
        const snapshot = await waitQueueRows(
          teacher,
          QUEUE_PAGE_SIZE_POLL + QUEUE_LOAD_MORE_STEP,
        )
        if (snapshot.rows < QUEUE_PAGE_SIZE_POLL + QUEUE_LOAD_MORE_STEP) {
          throw new Error(
            `第 ${tick} 拍自动刷新把列表从 ` +
              `${QUEUE_PAGE_SIZE_POLL + QUEUE_LOAD_MORE_STEP} 单缩水到 ${snapshot.rows} 单`,
          )
        }
        if (viewport.mobile) continue
        const stillChecked = await readQueueCheckedRows(teacher)
        if (!stillChecked.includes(checkedRow)) {
          throw new Error(
            `第 ${tick} 拍自动刷新把勾选弄丢了（勾的是 ${checkedRow}，` +
              `现在勾着：${stillChecked.join(',') || '无'}）`,
          )
        }
      }
      // 加载更多之后不许再出现按第一屏条数的请求（旧行为就是回落到 60）
      const tail = queue.records.slice(loadMoreAt)
      if (tail.includes(QUEUE_PAGE_SIZE_POLL)) {
        throw new Error(
          `加载更多之后自动刷新又按第一屏 ${QUEUE_PAGE_SIZE_POLL} 条拉过：${tail.join(',')}`,
        )
      }
      result.pollKeepRows = {
        sizedTail: tail.slice(),
        rows: afterLoadMore.rows,
        checkedRow,
      }
      console.log(
        `      队列：加载更多后两拍轮询请求 page_size ${tail.join('→')}，` +
          `列表保持 ${afterLoadMore.rows} 单` +
          (checkedRow ? `，勾选行 ${checkedRow} 未丢` : '（手机视口无勾选）'),
      )
    } finally {
      await queue.close()
    }
    // 回到真实数据：刷新一次，别把假数据带到后面的步骤里
    await clickByText(teacher, '刷新')
    await sleep(1200)
  }, teacher)

  // ---- 8. 超过 200 单：必须按 200 条渲染、说「已显示前 200 单」、并不再给「加载更多」
  await step('队列：超过 200 单时按 200 条渲染、提示封顶且不再给「加载更多」', async () => {
    // 真造超量数据：复用回归里刚上传的文件（下单宽限期 48 小时），用学生端自己的
    // /api/order/create 批量下单，不伪造接口返回。这样测到的是真后端、真分页、真 total。
    // 实测 19ms/单，造到 200 单以上不到 5 秒（数字与实测见 e2e/README.md）。
    // 造出来的单留在本轮专用的 zhiyin_r12b 库里（学生端没有删除接口）；同库第二次跑到
    // 这一步时条数已经够，need 会算成 0，不会重复造。
    const targetTotal = QUEUE_PAGE_SIZE_MAX + 60
    const beforeQueue = await fetchQueueFromPage(teacher, QUEUE_PAGE_SIZE_MAX)
    const need = Math.max(0, targetTotal - (Number(beforeQueue.total) || 0))
    const built = need > 0 ? await bulkCreateOrders(student, need) : { created: 0, elapsed: 0 }
    if (built.error) {
      throw new Error(`造超量数据失败：${built.error}（已造 ${built.created || 0} 单）`)
    }
    if (need > 0) {
      console.log(
        `      造超量数据：调学生端下单接口造 ${built.created} 单，耗时 ${built.elapsed} ms`,
      )
    }

    // 封顶状态只维持到下一拍轮询（轮询按第一屏 60 条拉），所以重试着看：
    // 每次手点「刷新」后盯 12 秒，没碰上 200 条的那一瞬就再刷一次。
    let snapshot = null
    for (let attempt = 1; attempt <= 3 && !snapshot; attempt += 1) {
      await clickByText(teacher, '刷新')
      const until = Date.now() + 12000
      while (Date.now() < until) {
        const current = await readQueueSnapshot(teacher)
        if (current.rows === QUEUE_PAGE_SIZE_MAX) {
          snapshot = current
          break
        }
        await sleep(150)
      }
    }
    if (!snapshot) {
      throw new Error(`手点「刷新」后始终没看到按 ${QUEUE_PAGE_SIZE_MAX} 条渲染的封顶状态`)
    }

    const afterQueue = await fetchQueueFromPage(teacher, QUEUE_PAGE_SIZE_MAX)
    const total = Number(afterQueue.total) || 0
    if (total <= QUEUE_PAGE_SIZE_MAX) {
      throw new Error(`造单后接口 total 仍只有 ${total}，没到封顶条件（应多于 ${QUEUE_PAGE_SIZE_MAX}）`)
    }
    // 三件都要单独验：页面按 200 条渲染 / 提示写还有 N 单 / 「加载更多」按钮消失
    if (snapshot.rows !== QUEUE_PAGE_SIZE_MAX) {
      throw new Error(
        `顶到单页上限时页面应正好按 ${QUEUE_PAGE_SIZE_MAX} 条渲染，实际 ${snapshot.rows} 单`,
      )
    }
    const hidden = total - QUEUE_PAGE_SIZE_MAX
    if (!snapshot.note.includes(`还有 ${hidden} 单未显示`)) {
      throw new Error(
        `封顶提示没写剩余单数（应写「还有 ${hidden} 单未显示」）：「${snapshot.note}」`,
      )
    }
    if (snapshot.hasLoadMore) {
      throw new Error(`已经顶到单页上限，还给了「加载更多」：「${snapshot.note}」`)
    }
    const problem = queueNoteProblem(snapshot, total)
    if (problem) throw new Error(`${problem}（接口 total ${total}）`)
    result.queueCap = {
      rows: snapshot.rows,
      note: snapshot.note,
      total,
      created: built.created || 0,
      hasLoadMore: snapshot.hasLoadMore,
    }
    console.log(
      `      队列封顶：列出 ${snapshot.rows} 单 / 接口 total ${total} / 提示「${snapshot.note}」/ 加载更多 ${snapshot.hasLoadMore ? '还在' : '已隐藏'}`,
    )

    // 回到真实数据：刷新一次，并等轮询把条数接回第一屏
    await clickByText(teacher, '刷新')
    await sleep(1200)
  }, teacher)

  // ---- 9. 生产构建里不许出现内网与本机回环地址
  await step('配置：学生端生产构建产物里不出现内网与本机回环地址', async () => {
    const scan = await studentProductionBundleScan()
    result.bundle = { dir: scan.dir, files: scan.files, bytes: scan.bytes, hits: scan.hits.length }
    if (scan.hits.length) {
      throw new Error(
        `生产产物里出现了本机/内网地址：${scan.hits
          .slice(0, 5)
          .map((hit) => `${hit.file} → ${hit.match}`)
          .join('；')}`,
      )
    }
  })

  // ---- 10. 老师端生产构建同样不许带地址出去（上线当天最先炸的就是这条）
  await step('配置：老师端生产构建产物里不出现内网与本机回环地址', async () => {
    const scan = await teacherProductionBundleScan()
    result.teacherBundle = {
      dir: scan.dir,
      files: scan.files,
      bytes: scan.bytes,
      hits: scan.hits.length,
      apiBase: scan.apiBase,
    }
    if (scan.hits.length) {
      throw new Error(
        `老师端生产产物里出现了本机/内网地址：${scan.hits
          .slice(0, 5)
          .map((hit) => `${hit.file} → ${hit.match}`)
          .join('；')}`,
      )
    }
    // 配置契约：.env.production 必须有 VITE_API_BASE_URL 且留空（同源相对路径）
    if (!scan.hasApiBaseKey) {
      throw new Error(`${scan.envFile} 里没有 VITE_API_BASE_URL，生产构建的接口地址没有唯一来源`)
    }
    if (scan.apiBase !== '') {
      throw new Error(
        `老师端 .env.production 的 VITE_API_BASE_URL 应该留空（同源相对路径），实际是「${scan.apiBase}」`,
      )
    }
    console.log(
      `      老师端产物：${scan.files} 个文件 / ${Math.round(scan.bytes / 1024)} KB / 本机与内网地址 ${scan.hits.length} 处 / VITE_API_BASE_URL 留空`,
    )
  })

  return { orderNo: order.orderNo, pickup: order.pickup, ...result }
}

/** 点「我的订单」里某一单自己的取消按钮（卡片上有多个按钮，按订单号定位）。 */
async function clickCancelButtonInRow(page, orderNo) {
  const clicked = await page.evaluate((orderNo) => {
    const cards = [...document.querySelectorAll('.card.order')].filter(
      (element) => (element.innerText || '').includes(orderNo),
    )
    const card = cards[0]
    if (!card) return 'no-row'
    const button = [...card.querySelectorAll('button, uni-button')].find(
      (item) => (item.innerText || item.textContent || '').trim() === '取消',
    )
    if (!button) return 'no-button'
    button.click()
    return 'ok'
  }, orderNo)
  if (clicked !== 'ok') throw new Error(`订单 ${orderNo} 上点不到取消（${clicked}）`)
}

/** 轮询订单详情直到状态变成预期值（学生取消后列表要等一次刷新）。 */
async function waitOrderStatus(page, orderId, status, timeout = 30000) {
  const deadline = Date.now() + timeout
  let last = null
  while (Date.now() < deadline) {
    last = await orderDetailFromApi(page, orderId)
    if (last.status === status) return last
    await sleep(500)
  }
  return last
}

/** 轮询订单详情直到金额变成预期值（后台落库与页面读之间有拍延迟）。 */
async function waitOrderAmount(page, orderId, cents, timeout = 30000) {
  const deadline = Date.now() + timeout
  let last = null
  while (Date.now() < deadline) {
    last = await orderDetailFromApi(page, orderId)
    if (Number(last.amount_cents) === cents) return last
    await sleep(500)
  }
  return last
}

/**
 * 用 .env.production 打一次学生端 H5 生产包，再扫产物里有没有本机/内网地址。
 *
 * 只打一次：两个视口共用同一份结果（构建要一分多钟，没必要跑两遍）。
 * 构建命令走 node 直接执行 uni CLI，与本文件启动 dev server 的做法一致，
 * 并把 VITE_API_BASE_URL 从环境里摘掉——就是要验证「留空」这一支的行为。
 */
let bundleScanPromise = null
function studentProductionBundleScan() {
  if (!bundleScanPromise) bundleScanPromise = buildAndScanStudentBundle()
  return bundleScanPromise
}

function buildAndScanStudentBundle() {
  return (async () => {
    const appDir = path.join(REPO_ROOT, 'student-app')
    const cli = path.join(appDir, 'node_modules', '@dcloudio', 'vite-plugin-uni', 'bin', 'uni.js')
    if (!fs.existsSync(cli)) throw new Error(`学生端 CLI 不存在：${cli}`)
    const env = { ...process.env }
    delete env.VITE_API_BASE_URL
    const outDir = path.join(appDir, 'dist', 'build', 'h5')
    fs.rmSync(outDir, { recursive: true, force: true })
    const build = spawnSync(process.execPath, [cli, 'build'], {
      cwd: appDir,
      env,
      encoding: 'utf8',
      timeout: 900000,
    })
    if (build.status !== 0) {
      throw new Error(
        `学生端生产构建失败（code=${build.status}）：${(build.stderr || build.stdout || '').slice(-1500)}`,
      )
    }
    if (!fs.existsSync(path.join(outDir, 'index.html'))) {
      throw new Error(`构建产物没有 index.html：${outDir}`)
    }

    const patterns = [
      { name: '本机回环 127.0.0.1', regex: /127\.0\.0\.1/g },
      { name: '本机回环 localhost', regex: /\/\/localhost/g },
      { name: 'IPv6 回环 [::1]', regex: /\[::1\]/g },
      { name: '内网 192.168.x.x', regex: /192\.168\.\d{1,3}\.\d{1,3}/g },
      { name: '内网 10.x.x.x', regex: /(?<![\d.])10\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\d.])/g },
      {
        name: '内网 172.16-31.x.x',
        regex: /(?<![\d.])172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}(?![\d.])/g,
      },
    ]
    const hits = []
    let files = 0
    let bytes = 0
    const walk = (dir) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name)
        if (entry.isDirectory()) {
          walk(full)
          continue
        }
        if (!/\.(js|mjs|css|html|json|map)$/i.test(entry.name)) continue
        const text = fs.readFileSync(full, 'utf8')
        files += 1
        bytes += text.length
        for (const pattern of patterns) {
          pattern.regex.lastIndex = 0
          let match = pattern.regex.exec(text)
          while (match) {
            hits.push({
              file: path.relative(outDir, full),
              pattern: pattern.name,
              match: match[0],
            })
            match = pattern.regex.exec(text)
          }
        }
      }
    }
    walk(outDir)
    return { dir: path.relative(REPO_ROOT, outDir), files, bytes, hits }
  })()
}

/**
 * 用 `.env.production` 打一次老师端生产包，再扫产物里有没有本机/内网地址。
 *
 * 为什么单独扫一次：老师端以前没有生产配置文件，请求地址兜底写死了
 * `http://127.0.0.1:8000`，构建产物里真的带着它——柜台机上线就全站打到自己电脑。
 * 这条断言就是那个事故的回归：产物里一个回环地址都不许有。
 *
 * 只打一次：两个视口共用同一份结果。`VITE_API_BASE_URL` 从环境里摘掉，
 * 就是要验证「生产配置留空 → 同源相对路径」这一支的真实行为。
 */
let teacherBundleScanPromise = null
function teacherProductionBundleScan() {
  if (!teacherBundleScanPromise) teacherBundleScanPromise = buildAndScanTeacherBundle()
  return teacherBundleScanPromise
}

function buildAndScanTeacherBundle() {
  return (async () => {
    const appDir = path.join(REPO_ROOT, 'teacher-web')
    const cli = path.join(appDir, 'node_modules', 'vite', 'bin', 'vite.js')
    if (!fs.existsSync(cli)) throw new Error(`老师端 Vite CLI 不存在：${cli}`)
    const envFile = path.join(appDir, '.env.production')
    if (!fs.existsSync(envFile)) {
      throw new Error(`老师端没有生产配置文件：${envFile}（生产接口地址就没有唯一来源）`)
    }
    const envValues = readEnvFile(envFile)
    const hasApiBaseKey = Object.prototype.hasOwnProperty.call(envValues, 'VITE_API_BASE_URL')
    const apiBase = String(envValues.VITE_API_BASE_URL || '').trim()

    const env = { ...process.env }
    delete env.VITE_API_BASE_URL
    const outDir = path.join(appDir, 'dist')
    const build = spawnSync(process.execPath, [cli, 'build'], {
      cwd: appDir,
      env,
      encoding: 'utf8',
      timeout: 900000,
    })
    if (build.status !== 0) {
      throw new Error(
        `老师端生产构建失败（code=${build.status}）：${(build.stderr || build.stdout || '').slice(-1500)}`,
      )
    }
    if (!fs.existsSync(path.join(outDir, 'index.html'))) {
      throw new Error(`老师端构建产物没有 index.html：${outDir}`)
    }

    // 不把裸 `//localhost` 当命中：axios 内部有 `window.location.href || "http://localhost"`
    // 这种第三方兜底字符串，它不带端口也不指向任何服务。这里扫的是「能被当成服务地址用」
    // 的写法：回环、IPv6 回环、带端口的 localhost、三段内网网段。
    const patterns = [
      { name: '本机回环 127.0.0.1', regex: /127\.0\.0\.1/g },
      { name: 'IPv6 回环 [::1]', regex: /\[::1\]/g },
      { name: '本机回环 localhost:端口', regex: /localhost:\d+/g },
      { name: '内网 192.168.x.x', regex: /192\.168\.\d{1,3}\.\d{1,3}/g },
      { name: '内网 10.x.x.x', regex: /(?<![\d.])10\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\d.])/g },
      {
        name: '内网 172.16-31.x.x',
        regex: /(?<![\d.])172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}(?![\d.])/g,
      },
    ]
    const hits = []
    let files = 0
    let bytes = 0
    const walk = (dir) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name)
        if (entry.isDirectory()) {
          walk(full)
          continue
        }
        if (!/\.(js|mjs|css|html|json|map)$/i.test(entry.name)) continue
        const text = fs.readFileSync(full, 'utf8')
        files += 1
        bytes += text.length
        for (const pattern of patterns) {
          pattern.regex.lastIndex = 0
          let match = pattern.regex.exec(text)
          while (match) {
            hits.push({
              file: path.relative(outDir, full),
              pattern: pattern.name,
              match: match[0],
            })
            match = pattern.regex.exec(text)
          }
        }
      }
    }
    walk(outDir)
    return {
      dir: path.relative(REPO_ROOT, outDir),
      files,
      bytes,
      hits,
      envFile: path.relative(REPO_ROOT, envFile),
      hasApiBaseKey,
      apiBase,
    }
  })()
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

// ---------------------------------------------------------------- 口径与重试
/**
 * 本轮新增的三条回归：收费口径说明、报价失败后的手动重试、设置页失败不白屏。
 *
 * 都跑在原有 41 步之后（两个视口各自编号 42–44），不改动也不放宽原有断言：
 * 42 下单页只写「预计张数、实际以老师确认为准」，金额仍等于服务端报价；
 * 43 让报价连续失败四次（首轮 + 3 次自动重试）后点「重新报价」能恢复并解锁下单；
 * 44 老师端设置页两个接口失败时给出中文提示、页面不白屏、输入内容不丢。
 */
async function runCopyAndRetryPhase({ student, teacher, viewport, problems, step }) {
  const result = { savingsTip: '', quoteAmount: '', retryAborts: 0, settingsNotice: '' }
  let orderPayload = null

  await step('口径：下单页写明「预计张数」，金额仍取服务端报价', async () => {
    await gotoFresh(student, `${STUDENT_URL}/#/pages/order/order`)
    await waitBody(student, '选择要打印的文件', 90000)
    const quotes = recordQuoteRequests(student)
    try {
      await pickFile(student, '选择文档', FIXTURES.v1)
      await waitBody(student, '3. 打印方案', 90000)
      await sleep(1500)
      await waitVisible(student, '.savings-tip', 30000)
      if (!quotes.items.length) throw new Error('页面没有调用 /api/order/quote')
      orderPayload = quotes.items[quotes.items.length - 1].payload
    } finally {
      quotes.stop()
    }

    const savings = await student.evaluate(() => {
      const box = document.querySelector('.savings')
      const tip = document.querySelector('.savings-tip')
      return {
        all: box ? (box.textContent || '').replace(/\s+/g, ' ').trim() : '',
        tip: tip ? (tip.textContent || '').replace(/\s+/g, ' ').trim() : '',
      }
    })
    if (!savings.tip.includes('预计张数')) {
      throw new Error(`下单页没有写明「预计张数」：${savings.all || '（找不到说明行）'}`)
    }
    if (!savings.tip.includes('实际以老师确认为准')) {
      throw new Error(`下单页没有写明「实际以老师确认为准」：${savings.tip}`)
    }
    // 不再出现「省 N 张」这种收费承诺
    if (/省\s*\d+\s*张/.test(savings.all)) {
      throw new Error(`下单页还在承诺省张数：${savings.all}`)
    }

    // 同页金额仍以服务端报价为准：脚本自己调一次报价接口，数字必须一致
    const body = await quoteFromPage(student, orderPayload)
    if (!body || body.code !== 0) throw new Error(`报价接口返回 ${JSON.stringify(body)}`)
    const summary = await readQuoteSummary(student)
    if (summary['应付金额'] !== money(body.data.amount_cents)) {
      throw new Error(
        `页面应付金额显示「${summary['应付金额'] || '无'}」，服务端报价是 ${money(body.data.amount_cents)}`,
      )
    }
    result.savingsTip = savings.tip
    result.quoteAmount = summary['应付金额']
    if (viewport.mobile) await assertNoOverflow(student, '学生端预计张数说明')
  })

  await step('报价失败后点「重新报价」能恢复并解锁下单', async () => {
    // uni.request 走 XHR，把它当网络中断：首轮 + 3 次自动重试共 4 次全部失败
    const expected = { aborts: 0, wanted: 4 }
    const quotes = recordQuoteRequests(student)
    await student.setRequestInterception(true)
    const abortQuote = (request) => {
      if (
        expected.aborts < expected.wanted &&
        request.method() === 'POST' &&
        request.url().includes('/api/order/quote')
      ) {
        expected.aborts += 1
        request.abort('failed')
        return
      }
      request.continue()
    }
    student.on('request', abortQuote)
    const problemsBefore = problems.length
    let retryPayload = null
    try {
      // 改一次颜色触发报价：自动重试 3 次全部会失败，之后页面必须给出手动出口
      const switched = await student.evaluate(() => {
        const rows = [...document.querySelectorAll('.plan-card .opt-row')]
        const row = rows.find((item) =>
          ((item.querySelector('.opt-label') || {}).textContent || '').includes('颜色'),
        )
        if (!row) return null
        const target = [...row.querySelectorAll('.seg-item')].find(
          (item) => !item.classList.contains('on'),
        )
        if (!target) return null
        target.click()
        return (target.textContent || '').trim()
      })
      if (!switched) throw new Error('下单页找不到可切换的颜色选项')
      await waitBody(student, '服务端报价失败', 90000)
      await waitBody(student, '重新报价', 30000)
      if (expected.aborts !== expected.wanted) {
        throw new Error(
          `预期拦下 ${expected.wanted} 次报价请求（首轮 + 3 次重试），实际 ${expected.aborts} 次`,
        )
      }
    } finally {
      student.off('request', abortQuote)
      await student.setRequestInterception(false)
    }
    dropExpectedProblems(
      problems,
      problemsBefore,
      (text) =>
        text.includes('/api/order/quote') ||
        text.includes('ERR_FAILED') ||
        text.includes('报价'),
    )
    result.retryAborts = expected.aborts

    // 报价没成功之前不能拿旧金额下单
    if ((await orderButtonState(student)) !== 'disabled') {
      throw new Error('报价失败后「确认下单」仍然可以点，会拿旧金额下单')
    }

    // 点手动重试：立刻重新请求并解锁
    await clickByText(student, '重新报价')
    await waitGone(student, '服务端报价失败', 30000)
    await waitGone(student, '重新报价', 30000)
    await sleep(500)
    retryPayload = quotes.items.length
      ? quotes.items[quotes.items.length - 1].payload
      : orderPayload
    quotes.stop()
    const body = await quoteFromPage(student, retryPayload)
    if (!body || body.code !== 0) throw new Error(`重试后报价接口仍返回 ${JSON.stringify(body)}`)
    const summary = await readQuoteSummary(student)
    if (summary['应付金额'] !== money(body.data.amount_cents)) {
      throw new Error(
        `重试后页面应付金额「${summary['应付金额'] || '无'}」，服务端报价 ${money(body.data.amount_cents)}`,
      )
    }
    const state = await orderButtonState(student)
    if (state !== 'enabled') throw new Error(`重试成功后「确认下单」仍不可点（${state}）`)
  })

  await step('设置页：接口失败有中文提示、页面不白屏、公告不丢内容', async () => {
    const failures = { sessions: 0, notice: 0 }
    await teacher.setRequestInterception(true)
    const failSettings = (request) => {
      const url = request.url()
      const isGet = request.method() === 'GET'
      if (isGet && url.includes('/api/admin/sessions')) {
        failures.sessions += 1
        request.respond({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ code: 50000, message: '模拟服务端故障' }),
        })
        return
      }
      if (request.method() === 'POST' && url.includes('/api/admin/notice')) {
        failures.notice += 1
        request.respond({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ code: 50000, message: '模拟公告存储故障' }),
        })
        return
      }
      request.continue()
    }
    teacher.on('request', failSettings)
    const problemsBefore = problems.length
    try {
      await gotoFresh(teacher, `${TEACHER_URL}/#/settings`)
      // 1) 时段列表失败：必须出现中文提示，并且页面主体还在（不白屏）
      await waitBody(teacher, '收单管理', 60000)
      await waitBody(teacher, '收单时段失败', 30000)
      if (!failures.sessions) throw new Error('没有拦到 /api/admin/sessions 请求')
      const layout = await teacher.evaluate(() => {
        const root = document.querySelector('.settings')
        return {
          settings: Boolean(root),
          cards: document.querySelectorAll('.settings .page-card').length,
          text: (document.body.innerText || '').slice(0, 4000),
        }
      })
      if (!layout.settings || layout.cards < 2) {
        throw new Error(`设置页白屏了：${JSON.stringify({ ...layout, text: undefined })}`)
      }
      if (!layout.text.includes('发布公告')) {
        throw new Error('设置页少了「发布公告」卡片，页面没有完整渲染')
      }
      result.settingsNotice = '收单时段失败：已提示且页面不白屏'

      // 2) 发布公告失败：提示要出现，输入的内容不能被清空
      await clickSelector(teacher, '.notice-input textarea')
      await teacher.type('.notice-input textarea', '回归用公告：设备检修', { delay: 10 })
      await clickByText(teacher, '发布公告')
      await waitBody(teacher, '公告发布失败', 30000)
      if (!failures.notice) throw new Error('没有拦到 /api/admin/notice 请求')
      const kept = await teacher.evaluate(() => {
        const box = document.querySelector('.notice-input textarea')
        return box ? box.value : ''
      })
      if (!kept.includes('设备检修')) {
        throw new Error(`发布失败后公告内容被清空了：「${kept}」`)
      }
      result.settingsNotice += '；公告失败：已提示且内容保留'
    } finally {
      teacher.off('request', failSettings)
      await teacher.setRequestInterception(false)
    }
    dropExpectedProblems(
      problems,
      problemsBefore,
      (text) =>
        text.includes('/api/admin/sessions') ||
        text.includes('/api/admin/notice') ||
        text.includes('模拟服务端故障') ||
        text.includes('模拟公告存储故障') ||
        // Chrome 对 5xx 的 console 记录只写状态码、不带路径，
        // 这一步范围内的同类记录都属于本次有意制造的失败
        text.includes('Failed to load resource'),
    )
  })

  return result
}

// ---------------------------------------------------------------- 学生端自带 AI 密钥
/**
 * 学生自带 AI 密钥（两条回归）。
 *
 * 依赖另一路的界面：「我的」页里的 AI 密钥面板。面板不在本分支时，这两步
 * **显式跳过并标注依赖**（打印出原因、写进 result），**不写成恒真断言**：
 * 界面合入后同一条代码路径直接生效，不需要回头改回归。
 */
const KEY_PANEL_MISSING =
  '依赖未合入：「我的」页 AI 密钥面板不在本分支（另一路负责界面），步骤已跳过'
const KEY_PANEL_TEXT_RE = 'AI\\s*密钥|自带密钥|我的密钥'

/**
 * 密钥面板的容器与面板内可点元素。
 *
 * 面板兼容三种类名（`.key-panel` / `.ai-key-panel` / `.key-sheet`）。
 * 点击一律按面板限定选择器：「我的」页顶上还有一个昵称的「保存」按钮，
 * 全局按文字找会点到它，把「保存密钥」变成「保存昵称」。
 */
const KEY_PANEL_SELECTOR = '.key-panel, .ai-key-panel, .key-sheet'
const KEY_PANEL_INPUT = '.key-panel input, .ai-key-panel input, .key-sheet input'
const KEY_PANEL_CLICKABLE = ['button', 'uni-button', '.key-btn']
  .flatMap((kind) => ['.key-panel', '.ai-key-panel', '.key-sheet'].map((panel) => `${panel} ${kind}`))
  .join(', ')
/** 与 student-app/src/services/llmKey.js 的 LLM_KEY_HEADER 一致；请求头名按小写比对。 */
const KEY_HEADER = 'x-llm-api-key'
/** 「测试连通」打的接口：拿请求头里那把做一次最小调用，响应里不回显密钥。 */
const KEY_CHECK_PATH = '/api/ai/key-check'

/** 在「我的」页里找 AI 密钥面板的入口（找不到说明那一版界面还没合入）。 */
async function findKeyPanelEntry(page) {
  return page.evaluate((pattern) => {
    const regex = new RegExp(pattern)
    const nodes = [
      ...document.querySelectorAll('.page button, .page uni-button, .page view, .page [role="button"]'),
    ].filter((node) => node.getClientRects().length > 0)
    const hit = nodes.find((node) => regex.test(node.innerText || node.textContent || ''))
    return hit ? (hit.innerText || hit.textContent || '').trim().slice(0, 40) : ''
  }, KEY_PANEL_TEXT_RE)
}

/** 给页面上第一个匹配的输入框填值（v-model 跟随 input 事件）。 */
async function fillFirstInput(page, selector, value) {
  const ok = await page.evaluate(
    ({ selector, value }) => {
      const input =
        document.querySelector(`${selector} input`) || document.querySelector(selector)
      if (!input) return false
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set
      setter.call(input, String(value))
      input.dispatchEvent(new Event('input', { bubbles: true }))
      input.dispatchEvent(new Event('change', { bubbles: true }))
      input.blur()
      return true
    },
    { selector, value },
  )
  if (!ok) throw new Error(`找不到输入框：${selector}`)
}

async function runStudentKeyPhase({ student, step }) {
  const result = {
    skipped: false,
    reason: '',
    panelEntry: '',
    testReason: '',
    headerOnly: null,
    keyCheckPath: KEY_CHECK_PATH,
    headerName: '',
    testMessage: '',
    storedLocally: false,
    clearedToShared: false,
  }

  await step('密钥：「我的」页打开 AI 密钥面板，无效密钥给出可读原因，清除后回到学校共享', async () => {
    await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/mine`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    })
    await waitBody(student, '全部', 60000)
    result.panelEntry = await findKeyPanelEntry(student)
    if (!result.panelEntry) {
      result.skipped = true
      result.reason = KEY_PANEL_MISSING
      console.log(`\n      跳过：${KEY_PANEL_MISSING}`)
      return
    }
    await clickByText(student, result.panelEntry)
    await waitVisible(student, KEY_PANEL_SELECTOR, 20000)

    // 1) 填一个必然无效的密钥，点「测试连通」：必须给出能读的原因，不能只说“失败”
    await fillFirstInput(student, KEY_PANEL_INPUT, 'sk-e2e-invalid-key')
    await clickByText(student, '测试连通', KEY_PANEL_CLICKABLE)
    await student.waitForFunction(
      () => /失败|无效|不可用|错误|超时/.test(document.body.innerText || ''),
      { timeout: 30000, polling: 300 },
    )
    const failureText = await visibleKeyPanelText(student)
    if (/连通正常|可用|成功/.test(failureText) && !/失败|无效|不可用/.test(failureText)) {
      throw new Error(`无效密钥居然测通了：「${failureText.slice(0, 200)}」`)
    }
    result.testReason = failureText.slice(0, 300)

    // 2) 清除后必须回到「学校共享」
    await clickByText(student, '清除', KEY_PANEL_CLICKABLE)
    await waitBody(student, '学校共享', 30000)
    result.clearedToShared = true
  })

  await step('密钥：只出现在请求头里，页面地址与请求体里都没有', async () => {
    if (result.skipped || !result.panelEntry) {
      result.skipped = true
      result.reason = result.reason || KEY_PANEL_MISSING
      console.log(`\n      跳过：${result.reason}`)
      return
    }
    const key = 'sk-e2e-header-probe-0123456789'

    // 这一步必须真的发出一次 AI 请求：面板上的「保存」只写本机存储、一个请求都不发
    // （services/llmKey.js saveLlmKey），光保存永远抓不到请求头。
    //
    // 顺序是「先保存 → 再点测试连通」：保存后输入框会被清空，这时点「测试连通」
    // 只能取本机存着的那把（llmKey.js getLlmKey），于是这条断言证的是「刚保存的
    // 密钥已经生效」，而不是「输入框里的值被顺手带出去了」。
    // 「测试连通」打 POST /api/ai/key-check，密钥经 api.js 的 llmKeyHeader() 只进请求头。
    const seen = []
    const collect = (request) => {
      const headers = request.headers() || {}
      seen.push({
        url: request.url(),
        method: request.method(),
        postData: request.postData() || '',
        headerNames: Object.entries(headers)
          .filter(([, value]) => String(value).includes(key))
          .map(([name]) => name.toLowerCase()),
      })
    }

    // waitForRequest 必须在点击之前登记，否则可能错过这一拍
    let keyCheckPending = null
    let keyCheck = null
    student.on('request', collect)
    try {
      await gotoFresh(student, `${STUDENT_URL}/#/pages/mine/mine`, {
        waitUntil: 'domcontentloaded',
        timeout: 60000,
      })
      await waitBody(student, '全部', 60000)
      await clickByText(student, result.panelEntry)
      await waitVisible(student, KEY_PANEL_SELECTOR, 20000)

      // 1) 保存进本机存储（这一步本身不发任何请求）
      await fillFirstInput(student, KEY_PANEL_INPUT, key)
      await clickByText(student, '保存', KEY_PANEL_CLICKABLE)
      await waitKeyPanelText(student, '已保存到本机', 30000)
      result.storedLocally = (await storedLocalText(student)).includes(key)
      if (!result.storedLocally) {
        throw new Error('点「保存」后本机存储里没有这把密钥，保存没有真的生效')
      }

      // 2) 真发一次 AI 请求，全程拦截请求看密钥去哪了
      keyCheckPending = student.waitForRequest(
        (request) => request.url().includes(KEY_CHECK_PATH),
        { timeout: 60000 },
      )
      // 点按钮本身抛错时 Promise 不会被 await，先挂一个空 catch 防空告警
      keyCheckPending.catch(() => {})
      await clickByText(student, '测试连通', KEY_PANEL_CLICKABLE)
      keyCheck = await keyCheckPending.catch(() => null)
      await waitGone(student, '正在用这把密钥做一次最小调用…', 90000)
    } finally {
      student.off('request', collect)
    }

    if (!keyCheck) {
      throw new Error(
        `点「测试连通」后没有发出 ${KEY_CHECK_PATH} 请求，无法确认密钥真的生效（本步只看到 ${seen.length} 个请求）`,
      )
    }
    const keyCheckHeaders = Object.fromEntries(
      Object.entries(keyCheck.headers() || {}).map(([name, value]) => [name.toLowerCase(), String(value)]),
    )
    const sentKey = keyCheckHeaders[KEY_HEADER] || ''
    if (sentKey !== key) {
      throw new Error(
        `${KEY_CHECK_PATH} 的请求头里没带上刚保存的密钥（${KEY_HEADER} ${sentKey ? '的值不对' : '为空'}）`,
      )
    }
    if (keyCheck.url().includes(key)) {
      throw new Error(`密钥出现在请求地址里：${keyCheck.url().replace(key, '<密钥>')}`)
    }
    if ((keyCheck.postData() || '').includes(key)) {
      throw new Error(`密钥出现在请求体里：${keyCheck.url().replace(key, '<密钥>')}`)
    }

    // 3) 本步里所有请求：密钥只在请求头，不进 URL 参数、不进请求体
    const withKeyHeader = seen.filter((item) => item.headerNames.length)
    const inUrl = seen.filter((item) => item.url.includes(key))
    const inBody = seen.filter((item) => item.postData.includes(key))
    const extraHeader = withKeyHeader.find((item) =>
      item.headerNames.some((name) => name !== KEY_HEADER),
    )
    result.headerName = withKeyHeader.length ? withKeyHeader[0].headerNames.join(',') : ''
    result.headerOnly = {
      requests: seen.length,
      withKeyHeader: withKeyHeader.length,
      inUrl: inUrl.length,
      inBody: inBody.length,
      keyCheckPath: new URL(keyCheck.url()).pathname,
      keyCheckMethod: keyCheck.method(),
    }
    if (inUrl.length) {
      throw new Error(`密钥出现在请求地址里：${inUrl[0].url.replace(key, '<密钥>')}`)
    }
    if (inBody.length) {
      throw new Error(`密钥出现在请求体里：${inBody.map((item) => item.url).join(',')}`)
    }
    if (extraHeader) {
      throw new Error(`密钥还出现在别的请求头上（不止 ${KEY_HEADER}）：${extraHeader.headerNames.join(',')}`)
    }

    // 4) 页面地址与页面可见文本里都不能有明文（面板里只允许显示后四位）
    const pageUrl = await student.evaluate(() => window.location.href)
    if (pageUrl.includes(key)) {
      throw new Error('密钥出现在页面地址里')
    }
    const pageText = await student.evaluate(() => document.body.innerText || '')
    if (pageText.includes(key)) {
      throw new Error('密钥以明文整串显示在页面上（应只显示后四位）')
    }
    result.testMessage = (await visibleKeyPanelText(student)).slice(0, 200)

    // 5) 收尾：清掉密钥，后面还有步骤，别让它们带着学生密钥跑
    await clickByText(student, '清除并改用学校共享', KEY_PANEL_CLICKABLE)
    await waitKeyPanelText(student, '学校共享', 30000)
    if ((await storedLocalText(student)).includes(key)) {
      throw new Error('点「清除并改用学校共享」后本机存储里还留着这把密钥')
    }
    if ((await student.evaluate(() => document.body.innerText || '')).includes(key)) {
      throw new Error('清除后页面上仍然出现了密钥明文')
    }
    result.clearedToShared = true
  })

  return result
}

/** 密钥面板的正文（面板没渲染就是空串）。 */
async function visibleKeyPanelText(page) {
  return page.evaluate((selector) => {
    const panels = [...document.querySelectorAll(selector)].filter(
      (node) => node.getClientRects().length > 0,
    )
    const panel = panels[panels.length - 1]
    return panel ? panel.innerText || panel.textContent || '' : ''
  }, KEY_PANEL_SELECTOR)
}

/** 等密钥面板的正文里出现某句话（面板没渲染就一直等到超时）。 */
async function waitKeyPanelText(page, text, timeout = 30000) {
  await page.waitForFunction(
    ({ selector, text }) => {
      const panels = [...document.querySelectorAll(selector)].filter(
        (node) => node.getClientRects().length > 0,
      )
      const panel = panels[panels.length - 1]
      return Boolean(panel) && (panel.innerText || panel.textContent || '').includes(text)
    },
    { timeout, polling: 200 },
    { selector: KEY_PANEL_SELECTOR, text },
  )
}

/**
 * 本机存储（uni H5 落在 localStorage）里所有键值拼成一段文本。
 *
 * 只用来回答「密钥到底有没有写到本机」与「清掉之后还在不在」，
 * 不依赖 uni 把键名改写成什么，只扫值。
 */
async function storedLocalText(page) {
  return page.evaluate(() => {
    const parts = []
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const name = window.localStorage.key(index)
      parts.push(`${name}=${window.localStorage.getItem(name) || ''}`)
    }
    return parts.join('\n')
  })
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
  for (const item of results) {
    if (item.studentKey && item.studentKey.skipped) {
      console.log(`  视口 ${item.viewport} 跳过：${item.studentKey.reason}`)
    }
  }
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
