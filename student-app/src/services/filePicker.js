/**
 * 选文件适配层（页面里禁止直接调用 uni.chooseFile / plus API）。
 *
 * - H5 / 小程序：uni.chooseFile
 * - App：优先尝试 5+ 的 plus.io.chooseFile，不可用时明确报错
 * - 图片/拍照：uni.chooseImage（全端支持）
 * - 任何平台都有「载入示例论文」兜底（服务端直接返回已上传的示例文档）
 */

import { request } from './api'

const DOC_EXTENSIONS = ['.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx']

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
        reject(new Error(error.errMsg || '选择文件失败'))
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
  // #ifdef APP-PLUS
  return chooseWithPlus()
  // #endif

  // #ifndef APP-PLUS
  return chooseWithUniFile()
  // #endif
}

/** 服务端示例论文：直接返回已上传好的 file_key，任何平台都能用。 */
export async function loadDemoDocument() {
  const data = await request('/api/file/demo', { method: 'POST' })
  return { uploaded: true, ...data }
}
