/**
 * 老师端界面主题入口（只影响样式，不涉及接口、字段与业务逻辑）。
 *
 * 默认 press——纸面工单：米白网格纸、墨线表格、红色印章。
 * 可选 signal——信号牌：黑底、红色块面、硬边按钮。
 * 可选 classic——旧蓝白皮肤：保留给习惯旧界面的使用者，主色改为纯色，不再使用渐变。
 *
 * 换风格：改 DEFAULT_THEME，或调用 setUiTheme('press' | 'signal' | 'classic') 后刷新页面。
 */

export const UI_THEME_KEY = 'zhiyin_teacher_ui_theme'

export const UI_THEMES = ['press', 'signal', 'classic']

export const DEFAULT_THEME = 'press'

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
