/**
 * 学生自己的大模型密钥：**只存本地、只走请求头**。
 *
 * 产品三条硬要求落在这里：
 * 1. 学生要简单无脑 —— 一个输入框、三个按钮（保存 / 测试连通 / 清除），开关就是「填没填」；
 * 2. 密钥不上传服务器 —— 本模块没有、也不许有任何写到请求体或 URL 的出口，
 *    唯一的出口是 llmKeyHeader() 返回的 `X-LLM-Api-Key` 请求头（服务端用完即弃）；
 * 3. 学生要知道密钥存在本地 —— LLM_KEY_NOTICE 是面板里给学生的原话，别处不许另编一套。
 *
 * 另外两条纪律：
 * - 不许 console.log 密钥，也不许把它拼进错误文案（脱敏一律走 maskLlmKey）；
 * - 密钥不可用时**不静默回退**到学校共享密钥：isLlmKeyFailure() 让页面把原因说清楚，
 *   换不换钥匙由学生自己点「清除并改用学校共享」决定。
 */

import { STORAGE_KEYS } from '../config/index.js'
import { ERROR_MESSAGES, LLM_KEY_INVALID_TEXT } from '../utils/errors.js'

/** 服务端约定的密钥请求头。密钥只从这里出去，不进请求体、不进 URL。 */
export const LLM_KEY_HEADER = 'X-LLM-Api-Key'

/** 测试密钥的接口：用请求头里那把做一次最小调用，不回显密钥。 */
export const LLM_KEY_CHECK_PATH = '/api/ai/key-check'

/** 面板里那段说明，全端只此一份。 */
export const LLM_KEY_NOTICE =
  '密钥只保存在这台设备的本地存储里，不会上传服务器；换设备或清理缓存后需要重新填写。' +
  '清除后立刻改用学校共享密钥，下次 AI 请求只带学校那把。'

/** 密钥不可用时的标准提示（页面横幅、测试失败都用这一句打头）。
 *
 * 文案本体在 utils/errors.js 的码表里（就是 50035 的翻译）：一句提示只有一个出处，
 * 页面横幅、`explainApiCode(50035)` 与这里拿到的必须是同一句话。
 */
export { LLM_KEY_INVALID_TEXT }

/** 没有自己的密钥时，状态行显示的文案。 */
export const SCHOOL_KEY_LABEL = '使用学校共享密钥'

/** 密钥长度下限：明显短于这个长度只可能是没复制全。 */
const MIN_KEY_LENGTH = 8
/** 密钥长度上限：再长就不是密钥本身（多半把整段配置粘进来了）。 */
const MAX_KEY_LENGTH = 200

/**
 * 规范化一把密钥：去掉首尾与内部的所有空白。
 *
 * 学生从网页上复制密钥时常带上换行、行尾空格，甚至被终端折行成两行；
 * 不清掉的话写进请求头会直接构造失败，报错还看不懂。
 */
export function normalizeLlmKey(raw) {
  if (typeof raw !== 'string') return ''
  return raw.replace(/\s+/g, '')
}

/**
 * 校验一把密钥是否可用（纯本地判断，不发请求）。
 *
 * @returns {{ok: boolean, key: string, error: string}} key 是规范化后的值，error 是给学生看的中文
 */
export function validateLlmKey(raw) {
  const key = normalizeLlmKey(raw)
  if (!key) {
    return { ok: false, key: '', error: '请先粘贴你自己的密钥' }
  }
  if (key.length < MIN_KEY_LENGTH) {
    return { ok: false, key: '', error: `密钥看起来太短（${key.length} 位），请检查是否复制完整` }
  }
  if (key.length > MAX_KEY_LENGTH) {
    return { ok: false, key: '', error: '密钥太长，请只粘贴密钥本身，不要带上其他内容' }
  }
  // 请求头只能放可见 ASCII：混进中文、控制符或空格说明粘贴错了
  if (!/^[\x21-\x7E]+$/.test(key)) {
    return { ok: false, key: '', error: '密钥里含有不能用的字符（中文、空格或换行），请重新复制' }
  }
  return { ok: true, key, error: '' }
}

/** 读本机存着的密钥；没有、读不到都返回空串。 */
export function getLlmKey() {
  try {
    const raw = uni.getStorageSync(STORAGE_KEYS.llmKey)
    return normalizeLlmKey(typeof raw === 'string' ? raw : '')
  } catch (error) {
    // 小程序存储偶发不可用：当作「没有自己的密钥」，走学校共享
    return ''
  }
}

/** 本机有没有自己的密钥。 */
export function hasLlmKey() {
  return getLlmKey().length > 0
}

/**
 * 保存密钥到本机存储。
 *
 * @returns {{ok: boolean, key: string, error: string}} 不合法或写不进去时 ok=false，error 说明原因
 */
export function saveLlmKey(raw) {
  const checked = validateLlmKey(raw)
  if (!checked.ok) return checked
  try {
    uni.setStorageSync(STORAGE_KEYS.llmKey, checked.key)
    return checked
  } catch (error) {
    return { ok: false, key: '', error: '本地存储写入失败，密钥没有保存' }
  }
}

/** 清除本机密钥，之后自动回到学校共享密钥。 */
export function clearLlmKey() {
  try {
    uni.removeStorageSync(STORAGE_KEYS.llmKey)
    return true
  } catch (error) {
    return false
  }
}

/**
 * 脱敏展示：前 4 位 + 省略号 + 后 4 位（`sk-12…cd34`）。
 *
 * 密钥太短时（≤9 位）前 4 后 4 就等于把整把印出来，只给位数。
 */
export function maskLlmKey(key) {
  const normalized = normalizeLlmKey(key)
  if (!normalized) return ''
  const HEAD = 4
  const TAIL = 4
  if (normalized.length <= HEAD + TAIL + 1) {
    return `••••（${normalized.length} 位）`
  }
  return `${normalized.slice(0, HEAD)}…${normalized.slice(-TAIL)}`
}

/**
 * 当前用哪把钥匙。
 *
 * @returns {{mode: 'school'|'own', own: boolean, masked: string, label: string}}
 */
export function llmKeyStatus() {
  const key = getLlmKey()
  if (!key) {
    return { mode: 'school', own: false, masked: '', label: SCHOOL_KEY_LABEL }
  }
  const masked = maskLlmKey(key)
  return { mode: 'own', own: true, masked, label: `使用我自己的密钥（${masked}）` }
}

/** 下单页那行小字用的短标签：「学校共享」或「我的密钥」。 */
export function llmKeyUsageLabel() {
  return getLlmKey() ? '我的密钥' : '学校共享'
}

/**
 * 组出密钥请求头。
 *
 * @param {string} [explicitKey] 显式指定一把钥匙（「测试连通」要测还没保存的那把）；
 *                               不传则用本机存的那把
 * @returns {Object} 有密钥时形如 `{'X-LLM-Api-Key': '...'}`，没有时是空对象
 */
export function llmKeyHeader(explicitKey) {
  const key = normalizeLlmKey(
    explicitKey === undefined || explicitKey === null ? getLlmKey() : explicitKey
  )
  if (!key) return {}
  return { [LLM_KEY_HEADER]: key }
}

/**
 * 「学生自带的密钥不可用」的三个后端业务码（见 backend/app/services/llm.py 与 routers/ai.py）。
 *
 * 50035 = 拿这把钥匙调模型失败（失效 / 额度用完 / 被模型端拒）；
 * 50036 = 请求头里没有密钥或格式明显不对；
 * 50037 = 运维关了「学生自带密钥」开关，填了也不生效（也要引导学生去清掉）。
 * 这三个码只会在学生自己填了钥匙的前提下出现，所以命中就是「你该处理你的钥匙」。
 */
export const LLM_KEY_FAILURE_CODES = [50035, 50036, 50037]

/**
 * 这三个码对应的文案集合（从码表派生，不是从后端顺手抄来的句子）。
 *
 * 它只做刀口的一句用：判断一次失败是不是「密钥不可用」。依据仍然是码——
 * 句子是本端按码翻译出来的，后端改措辞（或改错别字）不会让这里失灵。
 */
const KEY_FAILURE_TEXTS = new Set(LLM_KEY_FAILURE_CODES.map((code) => ERROR_MESSAGES[code]))

/**
 * 这次失败是不是「学生的密钥不可用」。
 *
 * 判定依据是**后端业务码**，不是后端的中文措辞（早先按 /密钥|unauthor|quota/ 之类
 * 的正则去认消息，后端把「密钥」换成别的说法、或改了错别字，判定就失效了）：
 * 1. 错误对象上带 `code`（业务码）就只看码；表里已知且不是这三个码 → 直接排除；
 * 2. HTTP 401/403 由 api.js 打 `llmKeyRejected`，同样是确定的密钥问题；
 * 3. 没带学生密钥的请求，谈不上「你的密钥不可用」（不能引学生去清一把不存在的钥匙）；
 * 4. 请求层（api.js）会把业务码挂到错误对象的 `code` 上，所以第 1 步通常就能定论；
 *    只有在拿不到码的少数路径上，才退一步用上面那张**由码表生成的文案集合**认领。
 *    这也是「按码」的：码表变，集合跟着变，后端措辞变，集合不受影响。
 */
export function isLlmKeyFailure(error) {
  if (!error) return false
  const code = Number(error.code ?? error.bizCode)
  if (Number.isInteger(code) && code !== 0) {
    if (LLM_KEY_FAILURE_CODES.includes(code)) return true
    if (Object.prototype.hasOwnProperty.call(ERROR_MESSAGES, code)) return false
  }
  if (error.llmKeyRejected === true) return true
  // 只有确实把学生密钥带上去了，才谈得上「你的密钥不可用」
  if (error.llmKeySent !== true) return false
  return KEY_FAILURE_TEXTS.has(String(error.message || '').trim())
}

/** 把一次密钥失败翻成一句学生能照做的话（码表没覆盖到码时把原文带上，不静默说「请求失败」）。 */
export function llmKeyFailureText(error) {
  const reason = String((error && error.message) || '').trim()
  if (!reason || reason === LLM_KEY_INVALID_TEXT) return LLM_KEY_INVALID_TEXT
  return `${LLM_KEY_INVALID_TEXT}（服务器返回：${reason}）`
}
