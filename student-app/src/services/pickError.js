/**
 * 选文件 / 拍照失败时给学生看的话。
 *
 * 平台失败时回传的是一串只有开发者看得懂的内部串，例如
 * `chooseImage:fail`、`chooseImage:fail auth deny`，
 * 直接念给学生既暴露了实现细节，他也不知道下一步该做什么。
 *
 * 这里只分三类，每一类都给一句能照做的话：
 * - 自己点了取消：不出声；
 * - 权限被拒：告诉他去哪儿把权限打开；
 * - 其他失败：说清是拍照/选图没成功，让他重试。
 */

/** 相机权限被拒时给学生看的话，全端只此一份。 */
export const CAMERA_PERMISSION_TEXT = '需要相机权限才能拍照，请到系统设置里允许'

/**
 * 平台内部串的通用样子：`xxx:fail`、`xxx:fail ...`。
 * 只要出现这个形状，就说明这条消息不该原样给用户。
 */
const INTERNAL_FAIL_RE = /:\s*fail\b/i

/**
 * 判断这条失败消息是不是权限问题。
 *
 * 三端的说法不一样：微信是 `auth deny`，App 是 `permission` 或中文「拒绝授权」，
 * 相机模块没打进包时是一条英文的 camera module 提示。三者对学生都是「相机用不了」，
 * 统一按权限处理，让他去系统设置里看一眼。
 */
export function isPermissionDenied(message) {
  const text = String(message || '')
  if (!text) return false
  return /auth|permission|denied|deny|camera|权限|拒绝/i.test(text)
}

/**
 * 把一次选择失败翻成给学生的一句话。
 *
 * @param {Error|object} error 选择器抛出来的错误
 * @param {'image'|'document'} picker 这次用的是相机/相册还是文件选择器
 * @returns {Error} 带 `canceled` 标记的错误：true 表示用户自己取消，调用方别弹提示
 */
export function describePickError(error, picker = 'document') {
  const raw = String((error && (error.message || error.errMsg)) || '')

  if (/cancel/i.test(raw)) {
    const canceled = new Error('已取消选择')
    canceled.canceled = true
    return canceled
  }

  if (isPermissionDenied(raw)) {
    return new Error(CAMERA_PERMISSION_TEXT)
  }

  if (INTERNAL_FAIL_RE.test(raw) || !raw.trim()) {
    return new Error(picker === 'image' ? '拍照或选图没成功，请重试' : '打开文件选择器没成功，请重试')
  }

  // 已经是中文说法的（格式不符、路径读不出来等），原样交给学生
  return new Error(raw)
}
