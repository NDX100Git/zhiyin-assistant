/**
 * 界面主题入口（只影响样式，不涉及接口、字段与文案）。
 *
 * 默认 press——纸面工单：米白网格纸、墨线表格、红色印章。
 * 可选 signal——信号牌：黑底反白取件码、红色块面。
 * 可选 classic——旧蓝白皮肤：保留给习惯旧界面的使用者，hero 改为纯色，不再使用渐变。
 *
 * 深色模式跟随系统：系统开深色就用该皮肤的深色配色，回浅色自动回浅色。
 * 每套皮肤各有一份深浅配色，没有第四套皮肤，也没有手动深浅开关。
 *
 * 换风格有两种方式：
 *   1. 改下面的 DEFAULT_THEME；
 *   2. 在「我的」页右上角齿轮里选一套（调用 changeUiTheme），当前页面立刻换肤，
 *      退出重进仍然记住。
 */

import { computed, onUnmounted, ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'

export const UI_THEME_KEY = 'zhiyin_ui_theme'

export const UI_THEMES = ['press', 'signal', 'classic']

export const DEFAULT_THEME = 'press'

/** 「我的」页皮肤面板的三选一选项（只描述观感，不改任何业务文案）。 */
export const UI_THEME_OPTIONS = [
  { value: 'press', label: '纸面工单', hint: '米白网格纸 · 墨线表格' },
  { value: 'signal', label: '信号牌', hint: '黑底反白取件码 · 红色块面' },
  { value: 'classic', label: '旧蓝白', hint: '浅蓝白底 · 圆角卡片' },
]

/** 深浅只有两种，跟着系统走，不由用户手动选。 */
export const UI_SCHEMES = ['light', 'dark']

const DARK_QUERY = '(prefers-color-scheme: dark)'

/** 读取当前主题；读不到或值不合法时回到默认主题。 */
export function getUiTheme() {
  try {
    const saved = uni.getStorageSync(UI_THEME_KEY)
    if (UI_THEMES.indexOf(saved) >= 0) return saved
  } catch (error) {
    // 小程序存储偶发不可用，退回默认主题即可
  }
  return DEFAULT_THEME
}

/** 设置主题；返回是否写入成功，方便以后接设置页。 */
export function setUiTheme(theme) {
  if (UI_THEMES.indexOf(theme) < 0) return false
  try {
    uni.setStorageSync(UI_THEME_KEY, theme)
    return true
  } catch (error) {
    return false
  }
}

/** 把主题名转成页面根节点的 class。 */
export function themeClass(theme) {
  const safe = UI_THEMES.indexOf(theme) >= 0 ? theme : DEFAULT_THEME
  return `zi-theme-${safe}`
}

/**
 * 系统当前是深色还是浅色。
 *
 * H5 读 prefers-color-scheme；小程序没有 matchMedia，读 uni.getSystemInfoSync().theme。
 * 两者都拿不到时按浅色处理（老设备默认观感）。
 */
export function getUiScheme() {
  try {
    if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
      return window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light'
    }
  } catch (error) {
    // 极端环境没有 matchMedia，继续往平台接口里找
  }
  try {
    const info = uni.getSystemInfoSync()
    if (info && info.theme === 'dark') return 'dark'
  } catch (error) {
    // 拿不到系统信息就当浅色
  }
  return 'light'
}

/** 把深浅转成页面根节点的 class（与皮肤类叠加使用）。 */
export function schemeClass(scheme) {
  return scheme === 'dark' ? 'zi-scheme-dark' : 'zi-scheme-light'
}

/**
 * 原生导航栏与 tabBar 的颜色。
 *
 * 每套皮肤各有一份浅色与一份深色；frontColor 平台只认 #000000 / #ffffff 两个值。
 */
const NATIVE_STYLES = {
  press: {
    light: {
      tab: { color: '#67614f', selectedColor: '#d02d10', backgroundColor: '#f8f4e9', borderStyle: 'black' },
      nav: { frontColor: '#000000', backgroundColor: '#f8f4e9' },
    },
    dark: {
      tab: { color: '#a9a291', selectedColor: '#ff7a5c', backgroundColor: '#1a1811', borderStyle: 'white' },
      nav: { frontColor: '#ffffff', backgroundColor: '#1a1811' },
    },
  },
  signal: {
    light: {
      tab: { color: '#5c574e', selectedColor: '#d22b1e', backgroundColor: '#fbf8f1', borderStyle: 'black' },
      nav: { frontColor: '#000000', backgroundColor: '#fbf8f1' },
    },
    dark: {
      tab: { color: '#a9a196', selectedColor: '#ff6044', backgroundColor: '#1a1817', borderStyle: 'white' },
      nav: { frontColor: '#ffffff', backgroundColor: '#1a1817' },
    },
  },
  classic: {
    light: {
      tab: { color: '#7a869a', selectedColor: '#2f6bff', backgroundColor: '#ffffff', borderStyle: 'white' },
      nav: { frontColor: '#000000', backgroundColor: '#ffffff' },
    },
    dark: {
      tab: { color: '#9fabbc', selectedColor: '#7aa7ff', backgroundColor: '#151a22', borderStyle: 'white' },
      nav: { frontColor: '#ffffff', backgroundColor: '#151a22' },
    },
  },
}

function applyNativeTheme(theme, scheme) {
  const skin = NATIVE_STYLES[theme] || NATIVE_STYLES[DEFAULT_THEME]
  const style = skin[scheme] || skin.light
  try {
    uni.setTabBarStyle({ ...style.tab, fail() {} })
  } catch (error) {
    // 详情页等非 tabBar 页面会失败，不影响页面内容
  }
  try {
    uni.setNavigationBarColor({ ...style.nav, fail() {} })
  } catch (error) {
    // H5 或部分平台没有原生导航栏，忽略即可
  }
}

/** 立即换肤：写存储并同步原生导航栏颜色，不需要手动刷新页面。 */
export function applyUiTheme(theme, scheme = getUiScheme()) {
  if (UI_THEMES.indexOf(theme) < 0) return false
  const saved = setUiTheme(theme)
  applyNativeTheme(theme, scheme)
  return saved
}

/** 监听系统深浅变化；H5 用 matchMedia，小程序用 uni.onThemeChange。 */
function watchScheme(handler) {
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
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
    return null
  }
  try {
    if (typeof uni !== 'undefined' && typeof uni.onThemeChange === 'function') {
      uni.onThemeChange((result) => handler(result && result.theme === 'dark' ? 'dark' : 'light'))
    }
  } catch (error) {
    // 平台不支持监听，只能靠 onShow 时再对一次
  }
  return null
}

/**
 * 页面里统一用这个组合式函数。
 * changeUiTheme 让当前页面立刻换肤（根节点 class 跟着变），
 * 同时把选择写进存储，退出重进仍然记得；
 * uiSchemeClass 让根节点带上系统深浅状态（zi-scheme-light / zi-scheme-dark）。
 */
export function useUiTheme() {
  const uiTheme = ref(getUiTheme())
  const uiScheme = ref(getUiScheme())
  const uiThemeClass = computed(() => themeClass(uiTheme.value))
  const uiSchemeClass = computed(() => schemeClass(uiScheme.value))

  const stopWatching = watchScheme((scheme) => {
    uiScheme.value = scheme
    applyNativeTheme(uiTheme.value, scheme)
  })
  onUnmounted(() => {
    if (typeof stopWatching === 'function') stopWatching()
  })

  onShow(() => {
    uiTheme.value = getUiTheme()
    uiScheme.value = getUiScheme()
    applyNativeTheme(uiTheme.value, uiScheme.value)
  })

  function changeUiTheme(theme) {
    if (!applyUiTheme(theme, uiScheme.value)) return false
    uiTheme.value = theme
    return true
  }
  return { uiTheme, uiThemeClass, uiScheme, uiSchemeClass, changeUiTheme }
}
