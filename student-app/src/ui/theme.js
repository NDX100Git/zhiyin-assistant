/**
 * 界面主题入口（只影响样式，不涉及接口、字段与文案）。
 *
 * 当前样板页：pages/mine/detail.vue。
 * 换风格有两种方式：
 *   1. 改下面的 DEFAULT_THEME；
 *   2. 在任意页面调用 setUiTheme('signal' | 'press' | 'classic')，
 *      重新进入订单详情页即可看到效果。
 *
 * signal  —— 红黑信号牌：黑底反白取件码、红色块面、硬边按钮。
 * press   —— 纸面工单：米白网格纸、墨线表格、红色印章。
 * classic —— 原有蓝白风格，保留给习惯旧界面的使用者。
 */

export const UI_THEME_KEY = 'zhiyin_ui_theme'

export const UI_THEMES = ['signal', 'press', 'classic']

export const DEFAULT_THEME = 'signal'

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
