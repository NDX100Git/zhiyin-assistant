/**
 * 选文件适配层（页面里禁止直接调用平台 API）。
 *
 * - 微信小程序：wx.chooseMessageFile（从聊天记录里选，小程序没有文件管理器）
 * - H5：uni.chooseFile（浏览器弹文件选择框）
 * - App：优先尝试 5+ 的 plus.io.chooseFile，不可用时明确报错
 * - 图片/拍照：uni.chooseImage（全端支持）
 * - 任何平台都有「载入示例论文」兜底（服务端直接返回已上传的示例文档）
 *
 * 用户在选文件面板点取消不算错误，抛出的 Error 带 canceled 标记，页面据此静默返回。
 */

import { request } from './api'

const DOC_EXTENSIONS = ['.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx']
// 微信的文件筛选参数不带点
const WEIXIN_EXTENSIONS = DOC_EXTENSIONS.map((item) => item.slice(1))

/** 用户自己点了取消：抛出带 canceled 标记的错误，notify 层据此静默。 */
function canceledError() {
  const error = new Error('已取消选择')
  error.canceled = true
  return error
}

function checkExtension(name) {
  const lower = String(name || '').toLowerCase()
  const suffix = lower.slice(lower.lastIndexOf('.'))
  if (!DOC_EXTENSIONS.includes(suffix)) {
    throw new Error(`只支持 PDF、Word、PPT、Excel，当前选的是 ${suffix || '未知格式'}`)
  }
}

/** 微信小程序：只能从聊天记录里选文件，选完得到临时路径与原始文件名。 */
function chooseWithWeixinMessage() {
  return new Promise((resolve, reject) => {
    if (typeof wx === 'undefined' || typeof wx.chooseMessageFile !== 'function') {
      reject(new Error('当前微信版本不支持选择文档，请升级微信或改用「载入示例论文」'))
      return
    }

    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: WEIXIN_EXTENSIONS,
      success(result) {
        const file = (result.tempFiles || [])[0]
        const path = file && (file.path || file.tempFilePath)
        if (!path) {
          reject(new Error('没有选择文件'))
          return
        }
        const name = file.name || path.split('/').pop() || 'document.pdf'
        try {
          checkExtension(name)
        } catch (error) {
          reject(error)
          return
        }
        resolve({ path, name })
      },
      fail(error) {
        const message = (error && error.errMsg) || '选择文件失败'
        reject(message.includes('cancel') ? canceledError() : new Error(message))
      },
    })
  })
}

export function chooseImage() {
  return new Promise((resolve, reject) => {
    uni.chooseImage({
      count: 1,
      sizeType: ['compressed', 'original'],
      sourceType: ['album', 'camera'],
      success(result) {
        const path = (result.tempFilePaths || [])[0]
        if (!path) {
          reject(new Error('没有选择图片'))
          return
        }
        const rawName = path.split('/').pop() || 'photo.jpg'
        const name = /\.[a-zA-Z0-9]+$/.test(rawName) ? rawName : `${rawName}.jpg`
        resolve({ path, name })
      },
      fail(error) {
        reject(new Error(error.errMsg || '选择图片失败'))
      },
    })
  })
}

function chooseWithUniFile() {
  return new Promise((resolve, reject) => {
    uni.chooseFile({
      count: 1,
      extension: DOC_EXTENSIONS,
      success(result) {
        const tempFiles = result.tempFiles || []
        const tempFilePaths = result.tempFilePaths || []
        const name =
          (tempFiles[0] && tempFiles[0].name) ||
          (tempFilePaths[0] || '').split('/').pop() ||
          'document.pdf'
        const path = tempFilePaths[0] || (tempFiles[0] && tempFiles[0].path)
        if (!path) {
          reject(new Error('没有选择文件'))
          return
        }
        resolve({ path, name })
      },
      fail(error) {
        const message = (error && error.errMsg) || '选择文件失败'
        reject(message.includes('cancel') ? canceledError() : new Error(message))
      },
    })
  })
}

function chooseWithPlus() {
  return new Promise((resolve, reject) => {
    if (typeof plus === 'undefined' || !plus.io || typeof plus.io.chooseFile !== 'function') {
      reject(new Error('当前 App 环境不支持选择文档，请使用「载入示例论文」或拍照上传'))
      return
    }

    try {
      plus.io.chooseFile({
        success(result) {
          const list = result.files || (Array.isArray(result) ? result : [result])
          const file = list[0] || {}
          const path = file.path || file.fullPath
          if (!path) {
            reject(new Error('未取得文件路径，请改用示例论文'))
            return
          }
          resolve({ path, name: file.name || path.split('/').pop() || 'document.pdf' })
        },
        fail(error) {
          reject(new Error((error && error.message) || '选择文档失败，请改用示例论文'))
        },
      })
    } catch (error) {
      reject(new Error('选择文档异常，请改用示例论文'))
    }
  })
}

export function chooseDocument() {
  // #ifdef MP-WEIXIN
  return chooseWithWeixinMessage()
  // #endif

  // #ifdef APP-PLUS
  return chooseWithPlus()
  // #endif

  // #ifdef H5
  return chooseWithUniFile()
  // #endif

  // 兜底：其他小程序平台没有通用的文件选择能力
  // #ifndef MP-WEIXIN || APP-PLUS || H5
  return Promise.reject(new Error('当前平台不支持选择文档，请使用「载入示例论文」或拍照上传'))
  // #endif
}

/** 服务端示例论文：直接返回已上传好的 file_key，任何平台都能用。 */
export async function loadDemoDocument() {
  const data = await request('/api/file/demo', { method: 'POST' })
  return { uploaded: true, ...data }
}
