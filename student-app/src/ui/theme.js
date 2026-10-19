/**
 * 界面主题入口（只影响样式，不涉及接口、字段与文案）。
 *
 * 默认 press——纸面工单：米白网格纸、墨线表格、红色印章。
 * 可选 signal——信号牌：黑底反白取件码、红色块面。
 * 可选 classic——旧蓝白皮肤：保留给习惯旧界面的使用者，hero 改为纯色，不再使用渐变。
 *
 * 换风格有两种方式：
 *   1. 改下面的 DEFAULT_THEME；
 *   2. 在任意页面调用 setUiTheme('press' | 'signal' | 'classic')，
 *      重新进入页面即可看到效果。
 */

import { computed, ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'

export const UI_THEME_KEY = 'zhiyin_ui_theme'

export const UI_THEMES = ['press', 'signal', 'classic']

export const DEFAULT_THEME = 'press'

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

/** 原生导航栏与 tabBar 的颜色，跟随页面主题一起切换。 */
const NATIVE_STYLES = {
  press: {
    tab: { color: '#67614f', selectedColor: '#d02d10', backgroundColor: '#f8f4e9', borderStyle: 'black' },
    nav: { frontColor: '#000000', backgroundColor: '#f8f4e9' },
  },
  signal: {
    tab: { color: '#5c574e', selectedColor: '#d22b1e', backgroundColor: '#fbf8f1', borderStyle: 'black' },
    nav: { frontColor: '#000000', backgroundColor: '#fbf8f1' },
  },
  classic: {
    tab: { color: '#7a869a', selectedColor: '#2f6bff', backgroundColor: '#ffffff', borderStyle: 'white' },
    nav: { frontColor: '#000000', backgroundColor: '#ffffff' },
  },
}

function applyNativeTheme(theme) {
  const style = NATIVE_STYLES[theme] || NATIVE_STYLES[DEFAULT_THEME]
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

/** 页面里统一用这个组合式函数，主题变化在重新进入页面时生效。 */
export function useUiTheme() {
  const uiTheme = ref(getUiTheme())
  const uiThemeClass = computed(() => themeClass(uiTheme.value))
  onShow(() => {
    uiTheme.value = getUiTheme()
    applyNativeTheme(uiTheme.value)
  })
  return { uiTheme, uiThemeClass }
}
