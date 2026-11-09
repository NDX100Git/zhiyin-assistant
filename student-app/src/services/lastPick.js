/**
 * 文档转换页「上次选的文件」的本机存档。
 *
 * 上传完文件切出去回消息，应用被系统回收，回来时页面内存里的东西全没了，
 * 学生只看到一张空白页，会以为刚才白传了一次。这里把最近一次选择和转换结果
 * 写进本机存储，回到页面时先判断那份文件还在不在：
 * - 还在：把「转换完成」卡片恢复出来，不用重新上传、重新转换；
 * - 确认没了：提示「上次选的文件已失效，请重新选择」。
 *
 * 只对能可靠检查的路径下结论（应用私有目录、file:// 地址、微信临时文件）。
 * 安卓的 content:// 授权地址、H5 的 blob 地址这类检查不了，一律算「判断不了」：
 * 既不提示也不删档。误报一次就让学生白白重选一遍，比漏报更让人恼火。
 */

/** 存档键名。只有本模块读写它，页面不直接碰。 */
const STORAGE_KEY = 'zhiyin_tools_last_pick'

/** 存档最长留一天：太久以前的文件本来也该重新选，别在下次进入时吓人一跳。 */
const MAX_AGE_MS = 24 * 60 * 60 * 1000

/** 检查结果：文件还在。 */
export const PICK_OK = 'ok'
/** 检查结果：确认文件已经不在了。 */
export const PICK_INVALID = 'invalid'
/** 检查结果：这个路径没法检查（或当前平台查不了），不下结论。 */
export const PICK_UNKNOWN = 'unknown'

/**
 * 这些开头的路径能用平台的文件接口问到「在不在」：
 * - `_doc/` `_downloads/` `_documents/`：App 的应用私有目录（图片另存后就在这里）
 * - `file://`：App 的本地绝对地址
 * - `wxfile://` `http://tmp/`：微信选完文件给的临时路径
 */
const CHECKABLE_PREFIXES = ['_doc/', '_downloads/', '_documents/', 'file://', 'wxfile://', 'http://tmp/']

/** 这个路径能不能检查。 */
export function canCheckPickPath(path) {
  const value = String(path || '')
  if (!value) return false
  return CHECKABLE_PREFIXES.some((prefix) => value.indexOf(prefix) === 0)
}

// #ifdef APP-PLUS
/** App：问运行环境这个地址还打不打得开。打不开才算失效。 */
function checkWithPlus(path) {
  return new Promise((resolve) => {
    try {
      plus.io.resolveLocalFileSystemURL(
        path,
        () => resolve(PICK_OK),
        () => resolve(PICK_INVALID)
      )
    } catch (error) {
      resolve(PICK_UNKNOWN)
    }
  })
}
// #endif

// #ifdef MP-WEIXIN
/** 微信：用文件系统接口探一下这个临时文件还在不在。 */
function checkWithFileSystem(path) {
  return new Promise((resolve) => {
    const fs = typeof uni.getFileSystemManager === 'function' ? uni.getFileSystemManager() : null
    if (!fs || typeof fs.access !== 'function') {
      resolve(PICK_UNKNOWN)
      return
    }
    fs.access({
      path,
      success: () => resolve(PICK_OK),
      fail: () => resolve(PICK_INVALID),
    })
  })
}
// #endif

/**
 * 判断存档里的文件还在不在。
 *
 * @param {string} path 上次选的文件路径
 * @returns {Promise<'ok'|'invalid'|'unknown'>}
 */
export function checkPickFile(path) {
  const value = String(path || '')
  if (!canCheckPickPath(value)) return Promise.resolve(PICK_UNKNOWN)

  // #ifdef APP-PLUS
  return checkWithPlus(value)
  // #endif

  // #ifdef MP-WEIXIN
  return checkWithFileSystem(value)
  // #endif

  // #ifndef APP-PLUS || MP-WEIXIN
  return Promise.resolve(PICK_UNKNOWN)
  // #endif
}

function readRaw() {
  try {
    return uni.getStorageSync(STORAGE_KEY)
  } catch (error) {
    return ''
  }
}

/**
 * 读上次的存档。
 *
 * @returns {{toolKey: string, name: string, path: string, size: number,
 *   result: object|null, savedAt: number}|null} 没有存档、存档坏了、放太久都返回 null
 */
export function loadLastPick() {
  const raw = readRaw()
  if (!raw) return null
  let draft = raw
  if (typeof raw === 'string') {
    try {
      draft = JSON.parse(raw)
    } catch (error) {
      return null
    }
  }
  if (!draft || typeof draft !== 'object') return null

  const savedAt = Number(draft.savedAt) || 0
  if (!savedAt || Date.now() - savedAt > MAX_AGE_MS) {
    clearLastPick()
    return null
  }
  return {
    toolKey: draft.toolKey ? String(draft.toolKey) : '',
    name: draft.name ? String(draft.name) : '',
    path: draft.path ? String(draft.path) : '',
    size: Number(draft.size) || 0,
    result: draft.result && typeof draft.result === 'object' ? draft.result : null,
    savedAt,
  }
}

/**
 * 存下这次选的文件与转换结果。
 *
 * @param {{toolKey: string, name: string, path: string, size: number, result: object|null}} pick
 * @returns {boolean} 写进本机存储返回 true；写不进去返回 false（不影响本次流程）
 */
export function saveLastPick(pick) {
  const draft = {
    toolKey: pick && pick.toolKey ? String(pick.toolKey) : '',
    name: pick && pick.name ? String(pick.name) : '',
    path: pick && pick.path ? String(pick.path) : '',
    size: Number(pick && pick.size) || 0,
    result: pick && pick.result ? pick.result : null,
    savedAt: Date.now(),
  }
  try {
    uni.setStorageSync(STORAGE_KEY, JSON.stringify(draft))
    return true
  } catch (error) {
    return false
  }
}

/** 删掉存档。学生重新选过文件、或者确认上次的文件失效时调用。 */
export function clearLastPick() {
  try {
    uni.removeStorageSync(STORAGE_KEY)
  } catch (error) {
    // 删不掉也只是下次进入时再判断一遍，不影响当前操作
  }
}
