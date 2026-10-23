/**
 * 老师端界面主题入口（只影响样式，不涉及接口、字段与业务逻辑）。
 *
 * 默认 press——纸面工单：米白网格纸、墨线表格、红色印章。
 * 可选 signal——信号牌：黑底、红色块面、硬边按钮。
 * 可选 classic——旧蓝白皮肤：保留给习惯旧界面的使用者，主色改为纯色，不再使用渐变。
 *
 * 深色模式跟随系统 prefers-color-scheme：系统开深色就用该皮肤的深色配色，回浅色自动回浅色。
 * 每套皮肤各有一份深浅配色，没有第四套皮肤，也没有手动深浅开关。
 *
 * 换风格：改 DEFAULT_THEME，或在侧边栏的皮肤入口里选一套（立即生效并记住）。
 */

export const UI_THEME_KEY = 'zhiyin_teacher_ui_theme'

export const UI_THEMES = ['press', 'signal', 'classic']

export const DEFAULT_THEME = 'press'

/** 深浅只有两种，跟着系统走，不由用户手动选。 */
export const UI_SCHEMES = ['light', 'dark']

const DARK_QUERY = '(prefers-color-scheme: dark)'

/** 侧边栏皮肤入口的三选一选项（只描述观感，不改任何业务文案）。 */
export const UI_THEME_OPTIONS = [
  { value: 'press', label: '纸面工单', hint: '米白网格纸 · 墨线表格' },
  { value: 'signal', label: '信号牌', hint: '黑底反白 · 红色块面' },
  { value: 'classic', label: '旧蓝白', hint: '浅蓝白底 · 圆角卡片' },
]

export function getUiTheme() {
  try {
    const saved = localStorage.getItem(UI_THEME_KEY)
    if (UI_THEMES.indexOf(saved) >= 0) return saved
  } catch (error) {
    // 隐私模式或存储不可用时退回默认主题
  }
  return DEFAULT_THEME
}

export function setUiTheme(theme) {
  if (UI_THEMES.indexOf(theme) < 0) return false
  try {
    localStorage.setItem(UI_THEME_KEY, theme)
    return true
  } catch (error) {
    return false
  }
}

export function themeClass(theme) {
  const safe = UI_THEMES.indexOf(theme) >= 0 ? theme : DEFAULT_THEME
  return `zi-theme-${safe}`
}

/** 系统当前是深色还是浅色；拿不到就当浅色。 */
export function getUiScheme() {
  try {
    if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
      return window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light'
    }
  } catch (error) {
    // 老浏览器没有 matchMedia
  }
  return 'light'
}

/** 把深浅转成 body 上的 class（与皮肤类叠加）。 */
export function schemeClass(scheme) {
  return scheme === 'dark' ? 'zi-scheme-dark' : 'zi-scheme-light'
}

/** 只把深浅状态挂到 body 上，不动已选皮肤。 */
export function applyUiScheme(scheme) {
  const safe = UI_SCHEMES.indexOf(scheme) >= 0 ? scheme : 'light'
  document.body.classList.remove(...UI_SCHEMES.map((item) => schemeClass(item)))
  document.body.classList.add(schemeClass(safe))
  return safe
}

/** 监听系统深浅变化，返回取消监听的函数。 */
export function watchUiScheme(handler) {
  try {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null
    const query = window.matchMedia(DARK_QUERY)
    const listener = () => handler(query.matches ? 'dark' : 'light')
    if (typeof query.addEventListener === 'function') {
      query.addEventListener('change', listener)
      return () => query.removeEventListener('change', listener)
    }
    if (typeof query.addListener === 'function') {
      query.addListener(listener)
      return () => query.removeListener(listener)
    }
  } catch (error) {
    // 监听不上就只在挂载时对一次
  }
  return null
}

/**
 * 立即换肤：写本地存储并把主题类挂到 body 上，页面不用刷新。
 * 样式变量都定义在 body 的 zi-theme-* 类下（见 src/styles.css）。
 */
export function applyUiTheme(theme) {
  if (UI_THEMES.indexOf(theme) < 0) return false
  const saved = setUiTheme(theme)
  const classes = UI_THEMES.map((item) => `zi-theme-${item}`)
  document.body.classList.remove(...classes)
  document.body.classList.add(themeClass(theme))
  return saved
}
