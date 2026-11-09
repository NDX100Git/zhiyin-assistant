/**
 * 测试用轨迹日志。
 *
 * 只为一件事服务：让脚本和人都能看清「现在在哪个页面、点了哪个元素、点在屏幕哪个位置、
 * 页面上有哪些可点元素、它们各自的中心点在哪」，省掉反复截屏量坐标。
 *
 * 开关：环境变量 VITE_TRACE=1 时才生效，默认关闭。测试包在
 * `.env.production.local` 里打开，正式包不要带。
 *
 * 输出：一行一条，前缀 `ZYTRACE `，后面是 JSON。用 `adb logcat` 抓下来后按行解析即可。
 *
 * 三种记录：
 * - `show`：页面出现，带页面路径、窗口尺寸、设备像素比
 * - `tap`：一次点击，带被点元素的可读名称、文字、CSS 坐标与设备像素坐标
 * - `screen`：页面可点元素快照，每个元素带中心点设备像素坐标，脚本可直接拿去点
 *
 * 不记录输入框内容，也不记录任何密钥。
 */

let enabledCache = null

/**
 * 开关是否打开。
 *
 * 惰性判断而不是模块加载时判断：App 端的环境变量对象要等运行时注入完成，
 * 模块一加载就读会读到空值，日志会静悄悄全丢。
 */
export function traceEnabled() {
  if (enabledCache === null) {
    try {
      enabledCache = String((import.meta.env && import.meta.env.VITE_TRACE) || '') === '1'
    } catch {
      enabledCache = false
    }
  }
  return enabledCache
}

/** 兼容旧的读法。 */
export const TRACE_ENABLED = true

// 接口地址与业务代码共用一份，别在这里另写一份常量
import { API_BASE_URL } from '../config/index.js'

const PREFIX = 'ZYTRACE '

/** 一次点击后多久拍页面快照。太早拍不到跳转后的页面，太晚又赶不上脚本下一步。 */
const SNAPSHOT_DELAY = 500

/** 快照最多记多少个元素。太多会把一行日志撑到 logcat 单行上限外。 */
const SNAPSHOT_LIMIT = 40

/** 视为「可点」的选择器，只用于快照与点击目标的兜底识别。 */
const HIT_SELECTOR =
  '[data-trace], button, uni-button, [class*="btn"], [class*="button"], [class*="tab"], [class*="card"]'

/** 元素上优先用作名称的属性，按顺序取第一个非空的。 */
const NAME_ATTRS = ['data-trace', 'id', 'name']

function emit(type, payload) {
  if (!traceEnabled()) return
  try {
    const line = Object.assign({ t: type, ts: Date.now() }, payload)
    console.log(PREFIX + JSON.stringify(line))
    bufferLine(line)
    pushPanel(type, payload)
  } catch {
    // 日志本身出错不许影响业务，静默丢弃
  }
}

/** 落盘缓冲：真机上看不到控制台输出时，靠这个文件把整段轨迹捞出来。 */
const FILE_LINES = 200
const fileBuffer = []
let fileDirty = false
let fileFlushing = false

const pending = []
let sending = false

/**
 * 把攒下的事件发回后端。
 *
 * 真机上控制台输出拿不到、屏幕面板也可能被系统遮挡、写外部存储在新安卓上又受限，
 * 回传到自己的后端是最稳的一条通道。只在测试开关打开时工作，失败就丢，不影响业务。
 */
function flushToBackend() {
  if (!pending.length || sending) return
  if (typeof uni === 'undefined' || !uni.request) return
  sending = true
  const lines = pending.slice(0, pending.length)
  const base = API_BASE_URL || ''
  const done = () => {
    sending = false
  }
  try {
    uni.request({
      url: `${base}/api/debug/trace`,
      method: 'POST',
      data: { device: 'android', lines },
      timeout: 5000,
      complete: () => {
        // 成功了才把这些事件从队列里删掉：发送失败时（比如后端停了）留着，
        // 等下一次连通再补发，否则那段时间的轨迹会整段缺失。
        pending.splice(0, lines.length)
        done()
      },
      fail: done,
    })
  } catch {
    done()
  }
}

function bufferLine(line) {
  try {
    fileBuffer.push(line)
    if (fileBuffer.length > FILE_LINES) fileBuffer.shift()
    fileDirty = true
    pending.push(JSON.stringify(line))
    if (pending.length > 200) pending.shift()
  } catch {
    // 忽略
  }
}

/**
 * 把缓冲写进手机上的文件（应用可见的文档目录）。
 *
 * 为什么需要：多数安卓机收不到应用里的 console 输出，屏幕面板也可能被挡住，
 * 文件是最后一个一定能拿到的通道。整份重写而不是追加，避免不同机型上
 * 追加接口行为不一致；日志上限 200 条，重写成本可以忽略。
 */
function flushToFile() {
  if (!fileDirty || fileFlushing) return
  if (typeof plus === 'undefined' || !plus.io || !plus.io.requestFileSystem) return
  fileFlushing = true
  const text = fileBuffer.join('\n') + '\n'
  const done = () => {
    fileDirty = false
    fileFlushing = false
  }
  try {
    plus.io.requestFileSystem(plus.io.PUBLIC_DOCUMENTS, (fs) => {
      fs.root.getFile('zytrace.log', { create: true }, (entry) => {
        entry.createWriter(
          (writer) => {
            writer.onwrite = () => {
              writer.write(text)
            }
            writer.onwriteend = done
            writer.onerror = done
            try {
              writer.truncate(0)
            } catch {
              done()
            }
          },
          done
        )
      }, done)
    }, done)
  } catch {
    done()
  }
}

/** 屏幕上最多留几行，留多了会挡住应用界面。 */
const PANEL_LINES = 7

const panelState = { el: null, lines: [] }

let panelRetry = 0

/** 把一条事件缩成一行短文字，给人看用；完整数据仍然在控制台那行 JSON 里。 */
function shortText(type, p) {
  const page = String(p.page || '').replace('pages/', '').replace(/\/index$/, '') || '-'
  if (type === 'api') {
    const code = p.code === null || typeof p.code === 'undefined' ? '-' : p.code
    const mark = p.ok ? 'OK' : 'FAIL'
    return `api ${mark} ${p.method} ${p.path} code=${code} ${p.ms}ms`
  }
  if (type === 'tap') {
    const t = p.target || {}
    const c = t.center || {}
    return `tap ${page} 「${t.text || t.name || t.cls || t.tag}」(${c.x},${c.y})`
  }
  if (type === 'nav') return `nav ${page} -> ${p.to || ''}`
  if (type === 'screen') return `screen ${page} ${p.count || 0}个可点元素`
  if (type === 'show') return `show ${page}`
  return `${type} ${page}`
}

/**
 * 屏幕上的调试面板。
 *
 * 存在的理由：侧载包里没有开发者控制台，系统日志在不同机型上未必收得到 WebView 的
 * 输出，而随时截图就能看到最近几条事件。指针对事件穿透，不会挡住点击。
 */
function pushPanel(type, payload) {
  if (typeof document === 'undefined') return
  try {
    // 首帧时 body 可能还没挂载好，等一会儿再试，最多试 20 次
    if (!document.body) {
      if (panelRetry < 20) {
        panelRetry += 1
        setTimeout(() => pushPanel(type, payload), 150)
      }
      return
    }
    if (!panelState.el) {
      const el = document.createElement('div')
      el.setAttribute('data-zytrace', 'panel')
      el.style.cssText = [
        'position:fixed',
        'left:0',
        'right:0',
        'bottom:0',
        'z-index:2147483000',
        'pointer-events:none',
        'background:rgba(0,0,0,0.72)',
        'color:#7dff9a',
        'font:10px/1.4 monospace',
        'padding:4px 6px',
        'max-height:30vh',
        'overflow:hidden',
        'white-space:pre-wrap',
        'word-break:break-all',
      ].join(';')
      document.body.appendChild(el)
      panelState.el = el
    }
    panelState.lines.push(shortText(type, payload))
    if (panelState.lines.length > PANEL_LINES) panelState.lines.shift()
    panelState.el.textContent = panelState.lines.join('\n')
  } catch {
    // 面板画不出来也不影响日志本身
  }
}

function systemInfo() {
  try {
    const i = uni.getSystemInfoSync()
    return {
      winW: i.windowWidth,
      winH: i.windowHeight,
      scrW: i.screenWidth,
      scrH: i.screenHeight,
      dpr: i.pixelRatio,
    }
  } catch {
    return {}
  }
}

function currentRoute() {
  try {
    const pages = getCurrentPages()
    if (!pages || !pages.length) return ''
    const page = pages[pages.length - 1]
    return page.route || page.$page?.route || page.__route__ || ''
  } catch {
    return ''
  }
}

function elementName(el) {
  for (const attr of NAME_ATTRS) {
    const value = el.getAttribute && el.getAttribute(attr)
    if (value) return String(value)
  }
  return ''
}

function elementText(el) {
  const raw = el.innerText || el.textContent || ''
  return String(raw).replace(/\s+/g, ' ').trim().slice(0, 30)
}

/** 可见性判断：尺寸为 0、或样式上隐藏的元素不记，免得脚本点到看不见的东西。 */
function visible(el) {
  if (!el.getBoundingClientRect) return false
  const rect = el.getBoundingClientRect()
  if (rect.width < 4 || rect.height < 4) return false
  if (typeof window !== 'undefined' && window.getComputedStyle) {
    const style = window.getComputedStyle(el)
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
      return false
    }
  }
  return true
}

/**
 * 把一个元素描述成日志里的一条。
 *
 * `css` 是页面里的 CSS 像素，和设备无关；`dev` 是乘过设备像素比的真实屏幕像素，
 * `adb shell input tap` 用的是后者。`center` 直接在设备像素上算好中心点，
 * 脚本拿这两数就能点，不用自己再换算。
 */
function describe(el, info) {
  const rect = el.getBoundingClientRect()
  const dpr = info.dpr || 1
  const round = (n) => Math.round(n)
  return {
    tag: el.tagName ? String(el.tagName).toLowerCase() : '',
    name: elementName(el),
    cls: String(el.className || '').slice(0, 48),
    text: elementText(el),
    css: { x: round(rect.left), y: round(rect.top), w: round(rect.width), h: round(rect.height) },
    dev: {
      x: round(rect.left * dpr),
      y: round(rect.top * dpr),
      w: round(rect.width * dpr),
      h: round(rect.height * dpr),
    },
    center: {
      x: round((rect.left + rect.width / 2) * dpr),
      y: round((rect.top + rect.height / 2) * dpr),
    },
  }
}

/** 从点击落点往上找，找到第一个可点元素；找不到就退回落点本身。 */
function resolveTarget(start) {
  let node = start
  let depth = 0
  let fallback = null
  while (node && node.getBoundingClientRect && depth < 8) {
    if (node.matches && node.matches(HIT_SELECTOR)) return node
    if (!fallback && elementText(node)) fallback = node
    node = node.parentElement
    depth += 1
  }
  return fallback || start
}

function snapshot(tag) {
  if (typeof document === 'undefined') return
  try {
    const info = systemInfo()
    const nodes = Array.from(document.querySelectorAll(HIT_SELECTOR)).filter(visible)
    const items = nodes.slice(0, SNAPSHOT_LIMIT).map((el) => describe(el, info))
    emit('screen', { tag: tag || '', page: currentRoute(), count: nodes.length, items })
  } catch {
    // 快照失败不影响使用
  }
}

/** 手动拍一次当前页面的可点元素快照。控制台里可以调 `window.__zytrace.dump()`。 */
export function dumpScreen(tag = 'manual') {
  snapshot(tag)
}

/**
 * 记一条请求日志：方法、路径、HTTP 状态、业务码、耗时。
 *
 * 只记这五样，不记请求体、不记请求头：登录串和学生自己的 AI 密钥都在这两处里。
 * 开关没开时这是个空函数，调用方不用自己判断。
 */
export function traceApi(info) {
  emit('api', Object.assign({ page: currentRoute() }, info))
}

function bindTap() {
  if (typeof document === 'undefined' || typeof window === 'undefined') return
  const info = systemInfo()
  let lastAt = 0
  const handler = (event) => {
    // 触屏与鼠标事件可能连着来两次，300 毫秒内只记第一次
    const now = Date.now()
    if (now - lastAt < 300) return
    lastAt = now
    try {
      let x = 0
      let y = 0
      let point = event
      if (event.touches && event.touches.length) point = event.touches[0]
      else if (event.changedTouches && event.changedTouches.length) point = event.changedTouches[0]
      x = point.clientX || 0
      y = point.clientY || 0
      const start =
        (document.elementFromPoint && document.elementFromPoint(x, y)) || event.target || null
      if (!start) return
      const target = resolveTarget(start)
      const dpr = info.dpr || 1
      emit('tap', {
        page: currentRoute(),
        at: { css: { x: Math.round(x), y: Math.round(y) }, dev: { x: Math.round(x * dpr), y: Math.round(y * dpr) } },
        target: describe(target, info),
      })
      window.setTimeout(() => snapshot('after-tap'), SNAPSHOT_DELAY)
    } catch {
      // 记日志失败不许拦住点击
    }
  }
  document.addEventListener('touchstart', handler, true)
  document.addEventListener('mousedown', handler, true)
}

function bindNavigation() {
  const how = ['navigateTo', 'redirectTo', 'switchTab', 'reLaunch', 'navigateBack']
  if (typeof uni === 'undefined' || !uni.addInterceptor) return
  how.forEach((name) => {
    try {
      uni.addInterceptor(name, {
        invoke(args) {
          emit('nav', { how: name, from: currentRoute(), to: (args && args.url) || '' })
          return args
        },
      })
    } catch {
      // 某个平台不支持该拦截器就算了
    }
  })
}

/**
 * 装上轨迹日志。main.js 里调用一次即可；没开开关时什么都不做。
 *
 * 页面出现这件事靠全局 mixin 的 onShow 抓，所以能覆盖所有页面，不用逐页改。
 */
export function installTrace(app) {
  if (!app) return
  if (!traceEnabled()) return
  try {
    app.mixin({
      onShow() {
        const info = systemInfo()
        emit('show', { page: currentRoute(), info })
        if (typeof window !== 'undefined') window.setTimeout(() => snapshot('after-show'), 300)
      },
    })
    bindTap()
    bindNavigation()
    if (typeof window !== 'undefined') {
      window.__zytrace = { dump: dumpScreen, enabled: true }
    }
    if (typeof setInterval === 'function') {
      setInterval(flushToBackend, 2000)
      setInterval(flushToFile, 3000)
    }
    emit('boot', { info: systemInfo(), hasPlus: typeof plus !== 'undefined' })
  } catch {
    // 装不上就退化成没有日志，不影响应用本身
  }
}
