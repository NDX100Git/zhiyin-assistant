/**
 * 选文件适配层（页面里禁止直接调用平台 API）。
 *
 * - 微信小程序：wx.chooseMessageFile（从聊天记录里选，小程序没有文件管理器）
 * - H5：uni.chooseFile（浏览器弹文件选择框，可多选）
 * - App：反射调用 Android 系统文件选择器（ACTION_GET_CONTENT），见 chooseWithPlus
 * - 图片/拍照：uni.chooseImage（全端支持）
 * - 任何平台都有「载入示例论文」兜底（服务端直接返回已上传的示例文档）
 *
 * 选文件一律走 chooseDocuments，返回数组；chooseDocument 是单选包装，
 * 换文件页只换其中一份，用后者。
 *
 * 用户在选文件面板点取消不算错误，抛出的 Error 带 canceled 标记，页面据此静默返回。
 */

import { request } from './api'

const DOC_EXTENSIONS = ['.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx']
// 微信的文件筛选参数不带点
const WEIXIN_EXTENSIONS = DOC_EXTENSIONS.map((item) => item.slice(1))

/**
 * 一次最多选几份。
 *
 * 后端 order_rules.json 的 max_files 是 20；界面给 10，既够用又不让人一次
 * 甩进二十份把转换排满（并发上限由后端控制，前端不承担这个职责）。
 */
export const MAX_FILES_PER_PICK = 10

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

/** 平台返回的文件数组形状不一，统一成 [{path, name, size}]。 */
function normalizePicked(list) {
  const picked = []
  for (const item of list) {
    const path = item && (item.path || item.tempFilePath || (item.tempFilePaths || [])[0])
    if (!path) continue
    const name = (item && item.name) || path.split('/').pop() || 'document.pdf'
    checkExtension(name)
    picked.push({ path, name, size: Number((item && item.size) || 0) })
  }
  if (picked.length === 0) throw new Error('没有选择文件')
  return picked
}

/** 微信小程序：只能从聊天记录里选文件，选完得到临时路径与原始文件名。 */
function chooseWithWeixinMessage(count) {
  return new Promise((resolve, reject) => {
    if (typeof wx === 'undefined' || typeof wx.chooseMessageFile !== 'function') {
      reject(new Error('当前微信版本不支持选择文档，请升级微信或改用「载入示例论文」'))
      return
    }

    wx.chooseMessageFile({
      count,
      type: 'file',
      extension: WEIXIN_EXTENSIONS,
      success(result) {
        try {
          resolve(normalizePicked(result.tempFiles || []))
        } catch (error) {
          reject(error)
        }
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

function chooseWithUniFile(count) {
  return new Promise((resolve, reject) => {
    uni.chooseFile({
      count,
      extension: DOC_EXTENSIONS,
      success(result) {
        // H5 上 tempFiles 带 path/name/size；部分平台只给 tempFilePaths
        const raw = (result.tempFiles && result.tempFiles.length
          ? result.tempFiles
          : (result.tempFilePaths || []).map((path) => ({ path })))
        try {
          resolve(normalizePicked(raw))
        } catch (error) {
          reject(error)
        }
      },
      fail(error) {
        const message = (error && error.errMsg) || '选择文件失败'
        reject(message.includes('cancel') ? canceledError() : new Error(message))
      },
    })
  })
}

/**
 * 把系统文件选择器返回的 content:// URI 解析成可上传的信息。
 *
 * 为什么不能直接用 URI：Android 4.4 之后选择器给的是 content:// 授权地址，
 * 不是磁盘路径，plus.io 与后端都不认。可上传的真实路径要从 ContentResolver
 * 的 _data 列取；Android 10 的分区存储下这列可能为空（云端文件、部分 ROM），
 * 这时退一步把 content:// 原样交给 uni.uploadFile —— 它在 App 端能读这类 URI。
 * 两条都走不通才报错，提示改用示例论文，不让用户卡在一个没有出路的死胡同里。
 *
 * uri 是原生对象，必须先 importClass 才能调它的 Java 方法（toString 等），
 * 不导入就只能拿到一个看不见内部属性的空壳。
 */
function resolveAndroidDocument(main, uri) {
  plus.android.importClass(uri)

  let name = ''
  let dataPath = ''
  let size = 0

  // query 可能因为 provider 不支持某列而抛异常，失败不该让整个选择流程崩掉：
  // 拿不到元数据还有 URI 兜底，后面照样能传。
  try {
    const resolver = main.getContentResolver()
    plus.android.importClass(resolver)
    const cursor = resolver.query(uri, null, null, null, null)
    if (cursor) {
      plus.android.importClass(cursor)
      try {
        if (cursor.moveToFirst()) {
          const nameIndex = cursor.getColumnIndex('_display_name')
          if (nameIndex >= 0) name = String(cursor.getString(nameIndex) || '')
          const dataIndex = cursor.getColumnIndex('_data')
          if (dataIndex >= 0) {
            const raw = cursor.getString(dataIndex)
            if (raw) dataPath = String(raw)
          }
          const sizeIndex = cursor.getColumnIndex('_size')
          if (sizeIndex >= 0) size = Number(cursor.getLong(sizeIndex)) || 0
        }
      } finally {
        cursor.close()
      }
    }
  } catch (error) {
    name = ''
  }

  const uriText = String(uri.toString())

  if (!name) {
    // provider 没给显示名时从 URI 末段猜一个，至少保证扩展名校验有东西可查
    const tail = uriText.split('/').pop() || ''
    try {
      name = decodeURIComponent(tail)
    } catch (error) {
      name = tail
    }
  }
  if (!name) name = 'document'

  if (dataPath) return { path: dataPath, name, size }

  if (uriText.indexOf('content://') === 0 || uriText.indexOf('file://') === 0) {
    return { path: uriText, name, size }
  }

  throw new Error('无法读取该文件的路径，请换一份文件，或使用「载入示例论文」')
}

/**
 * App 端选文档。
 *
 * 不能用 plus.io.chooseFile —— 这个 API 从来就不存在，plus.io 只做文件系统读写。
 * 也不能用 uni.chooseFile —— 它在 Android 上走媒体库，选不到 PDF 与 Office 文档。
 * 所以直接反射调系统选择器：ACTION_GET_CONTENT + CATEGORY_OPENABLE。
 * CATEGORY_OPENABLE 必须加，否则华为等部分 ROM 根本不出文件列表。
 *
 * 取结果只能靠 main.onActivityResult。这里有两处历史坑：
 *
 * 1. plus 的 startActivityForResult 没有回调参数，写成第三个参数会被直接忽略，
 *    Promise 永远挂着，表现是「点了选文件毫无反应」。
 * 2. 回调里的 resultCode 不能用 plus.android.importClass('android.app.Activity').RESULT_OK
 *    去比对：反射取静态常量并不可靠，取不到就是 undefined，于是「选了文件」被判成
 *    「用户取消」，而取消走的是静默分支——界面上什么都不显示，症状同样是「没反应」。
 *    Android 规范里 RESULT_OK 恒为 -1、RESULT_CANCELED 恒为 0，直接写死更安全。
 *
 * 回调既用属性赋值挂一次，也用 setAttribute 挂一次：不同 HBuilderX 运行时版本
 * 对 Native.js 属性映射的支持不一致，两条都试，重复触发由 settled 挡掉。
 */
function chooseWithPlus() {
  return new Promise((resolve, reject) => {
    if (typeof plus === 'undefined' || !plus.android) {
      reject(new Error('当前 App 环境不支持选择文档，请使用「载入示例论文」或拍照上传'))
      return
    }

    const REQUEST_CODE = 10087
    const RESULT_OK = -1
    const RESULT_CANCELED = 0

    // 回调可能被挂两次，也可能因为用户反复进出选择器而触发多次，只认第一次结果
    let settled = false
    const finish = (fn, value) => {
      if (settled) return
      settled = true
      fn(value)
    }

    // 回调可能因为用户一直不选而不触发，这里的 Promise 不能设超时：
    // 设了就会在用户还在文件管理器里翻目录时先把请求判失败。
    try {
      const main = plus.android.runtimeMainActivity()
      const Intent = plus.android.importClass('android.content.Intent')
      const intent = new Intent(Intent.ACTION_GET_CONTENT)
      intent.setType('*/*')
      intent.addCategory(Intent.CATEGORY_OPENABLE)
      // 只给本地文件：避免选中网盘里还没下载的条目，那种文件读出来是空的
      intent.putExtra('android.intent.extra.LOCAL_ONLY', true)

      const handler = (requestCode, resultCode, data) => {
        console.log('[filePicker] onActivityResult', requestCode, resultCode, !!data)
        if (requestCode !== REQUEST_CODE) return

        if (resultCode === RESULT_CANCELED) {
          finish(reject, canceledError())
          return
        }
        // 既不是成功也不是取消：把真实结果码带出来，别再静默吞掉
        if (resultCode !== RESULT_OK || !data) {
          finish(
            reject,
            new Error(`没有取到文件（返回码 ${resultCode}），请重试或改用「载入示例论文」`)
          )
          return
        }
        try {
          const uri = data.getData()
          if (!uri) {
            finish(reject, new Error('没有取得文件，请重新选择'))
            return
          }
          const picked = resolveAndroidDocument(main, uri)
          checkExtension(picked.name)
          finish(resolve, [picked])
        } catch (error) {
          finish(
            reject,
            error instanceof Error ? error : new Error('读取文件失败，请改用「载入示例论文」')
          )
        }
      }

      try {
        main.onActivityResult = handler
      } catch (error) {
        console.warn('[filePicker] 属性赋值挂回调失败，改用 setAttribute')
      }
      try {
        plus.android.setAttribute(main, 'onActivityResult', handler)
      } catch (error) {
        console.warn('[filePicker] setAttribute 挂回调失败', error)
      }

      main.startActivityForResult(intent, REQUEST_CODE)
    } catch (error) {
      finish(reject, new Error('打开系统文件选择器失败，请改用「载入示例论文」或拍照上传'))
    }
  })
}

/**
 * 选文档，一次可以选多份，返回 `[{path, name, size}]`。
 * App 端的文件选择器只有单选，返回的数组长度为 1。
 */
export function chooseDocuments(count = MAX_FILES_PER_PICK) {
  // #ifdef MP-WEIXIN
  return chooseWithWeixinMessage(count)
  // #endif

  // #ifdef APP-PLUS
  return chooseWithPlus()
  // #endif

  // #ifdef H5
  return chooseWithUniFile(count)
  // #endif

  // 兜底：其他小程序平台没有通用的文件选择能力
  // #ifndef MP-WEIXIN || APP-PLUS || H5
  return Promise.reject(new Error('当前平台不支持选择文档，请使用「载入示例论文」或拍照上传'))
  // #endif
}

/** 只选一份（换文件页用）：多选结果只取第一份，避免误换别的文件。 */
export async function chooseDocument() {
  const picked = await chooseDocuments(1)
  return picked[0]
}

/** 服务端示例论文：直接返回已上传好的 file_key，任何平台都能用。 */
export async function loadDemoDocument() {
  const data = await request('/api/file/demo', { method: 'POST' })
  return { uploaded: true, ...data }
}
