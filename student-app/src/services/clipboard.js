/**
 * 复制一段文字到剪贴板。
 *
 * 取件码和老师的说明都是学生要转给别人的信息（找同学代取、发给老师看），
 * 长按文字在 App 和小程序里不会选中，所以界面上必须有一个按得动的按钮。
 *
 * 复制成功后只弹一句短提示：`已复制取件码`、`已复制老师说明`。
 * 微信与 H5 端点自己会弹一条「内容已复制」，先关掉再弹我们这句，
 * 不然两条提示会叠在一起，学生分不清哪条是应用的。
 */

import { notify } from './notify.js'

/**
 * 复制文字。
 *
 * @param {string} text 要复制的内容，首尾空白会被去掉
 * @param {string} [label] 提示里点的名字，例如「取件码」；不传就只说「已复制」
 * @returns {Promise<boolean>} 复制成功返回 true；空内容或失败返回 false（已经弹过提示）
 */
export function copyText(text, label = '') {
  const value = String(text == null ? '' : text).trim()
  if (!value) {
    notify('没有可复制的内容')
    return Promise.resolve(false)
  }

  return new Promise((resolve) => {
    uni.setClipboardData({
      data: value,
      // 关掉平台自带的提示，只留下面那句
      showToast: false,
      success() {
        notify(label ? `已复制${label}` : '已复制', 'success')
        resolve(true)
      },
      fail() {
        notify('复制失败，请稍后重试')
        resolve(false)
      },
    })
  })
}
