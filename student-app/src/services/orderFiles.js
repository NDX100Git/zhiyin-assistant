/**
 * 订单文件清单归一化与学生端公共口径。
 *
 * 后端多文件订单带 `files` 数组，每项是
 * `{file_index, file_key, file_name, page_count, print_pages, print_side,
 * color_mode, paper_size, copies, amount_cents}`（报价接口的项也是同一套键名）；
 * 老订单、或后端清单还没回填时只有订单上的单文件字段
 * （`file_key` / `file_name` / `page_count`），这里统一兜底成一条，
 * 页面只认本模块的输出，字段缺失既不报错也不会空白。
 */

/** 可选的纸张规格：与后端 order_rules.json 的 allowed_paper_sizes 同口径。 */
export const PAPER_SIZES = ['A4', 'A3', 'B5']

/** 页数统一成非负整数，脏数据按 0 处理。 */
function toCount(value) {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0
}

/** 单双面：1=单面 2=双面，缺省按双面。 */
function toSide(value) {
  return Number(value) === 1 ? 1 : 2
}

/** 颜色：0=黑白 1=彩色，缺省按黑白。 */
function toColor(value) {
  return Number(value) === 1 ? 1 : 0
}

function toPaper(value) {
  const paper = value ? String(value) : ''
  return PAPER_SIZES.includes(paper) ? paper : 'A4'
}

function toCopies(value) {
  const n = Number(value)
  return Number.isFinite(n) && n >= 1 ? Math.floor(n) : 1
}

/** 学生端展示用：把「第几份」说清楚，单文件不啰嗦。 */
function labelOf(files, index) {
  return files.length > 1 ? `第 ${index + 1} 个文件` : '文件'
}

/**
 * 返回 `[{file_index, file_key, file_name, page_count, print_pages, print_side,
 * color_mode, paper_size, copies, amount_cents}]`，按 file_index 升序。
 * 单文件老订单返回一条，file_index 恒为 1。
 */
export function orderFiles(order) {
  if (!order) return []
  const raw = Array.isArray(order.files) ? order.files.filter((item) => item) : []
  if (raw.length) {
    return raw
      .map((item, index) => {
        const pageCount = toCount(item.page_count)
        const copies = toCopies(item.copies)
        return {
          file_index: toCount(item.file_index) || toCount(item.seq) || index + 1,
          file_key: item.file_key || '',
          file_name: item.file_name ? String(item.file_name) : `文件 ${index + 1}`,
          page_count: pageCount,
          print_pages: toCount(item.print_pages) || pageCount * copies,
          print_side: toSide(item.print_side),
          color_mode: toColor(item.color_mode),
          paper_size: toPaper(item.paper_size),
          copies,
          amount_cents: toCount(item.amount_cents),
        }
      })
      .sort((a, b) => a.file_index - b.file_index)
  }
  // 兜底：订单自带单文件字段（老数据、迁移未回填）
  if (order.file_key || order.file_name || toCount(order.page_count)) {
    const pageCount = toCount(order.page_count)
    const copies = toCopies(order.copies)
    return [
      {
        file_index: 1,
        file_key: order.file_key || '',
        file_name: order.file_name || '论文文档',
        page_count: pageCount,
        print_pages: toCount(order.print_page_count) || pageCount * copies,
        print_side: toSide(order.print_side),
        color_mode: toColor(order.color_mode),
        paper_size: toPaper(order.paper_size),
        copies,
        amount_cents: toCount(order.amount_cents),
      },
    ]
  }
  return []
}

/** 整单打印页数：各份 page_count × copies 之和，缺失时退回订单自带值。 */
export function totalPrintPages(order) {
  const files = orderFiles(order)
  const sum = files.reduce((acc, file) => acc + file.print_pages, 0)
  return sum > 0 ? sum : toCount(order && order.print_page_count)
}

/** 一份文件的参数摘要：单面 · 黑白 · A4 · 2 份。 */
export function paramSummary(file) {
  if (!file) return ''
  return [
    file.print_side === 1 ? '单面' : '双面',
    file.color_mode === 1 ? '彩色' : '黑白',
    file.paper_size,
    `${file.copies} 份`,
  ].join(' · ')
}

/** 一单几份文件；老订单恒为 1。 */
export function fileCount(order) {
  return orderFiles(order).length
}

/** 是否多文件订单：换文件时必须先选换哪一份。 */
export function isMultiFile(order) {
  return fileCount(order) > 1
}

/** 总页数：清单累加，清单页数缺失时退回订单自带的 page_count。 */
export function totalPages(order) {
  const files = orderFiles(order)
  const sum = files.reduce((acc, file) => acc + file.page_count, 0)
  return sum > 0 ? sum : toCount(order && order.page_count)
}

/** 订单卡片里的一行摘要：「论文.pdf · 42 页」或「3 个文件 · 共 42 页」。 */
export function fileSummary(order) {
  const files = orderFiles(order)
  if (files.length === 0) return '文件待确认'
  if (files.length === 1) {
    const pages = totalPages(order)
    return pages > 0 ? `${files[0].file_name} · ${pages} 页` : files[0].file_name
  }
  return `${files.length} 个文件 · 共 ${totalPages(order)} 页`
}

/** 换文件申请针对哪一份文件：返回带「第 N 个文件」标签的一条。 */
export function changeTarget(order) {
  const files = orderFiles(order)
  const change = (order && order.file_change) || null
  if (!change) return null
  const changeIndex = toCount(change.file_index) || toCount(change.seq)
  let index = files.findIndex((file) => file.file_index === changeIndex)
  if (index < 0 && change.new_file_name) {
    index = files.findIndex((file) => file.file_name === change.new_file_name)
  }
  if (index < 0) index = files.length > 0 ? files.length - 1 : -1
  if (index < 0) {
    return {
      file_index: changeIndex || 1,
      label: '文件',
      file_name: change.new_file_name || '新文件',
      page_count: toCount(change.new_page_count),
    }
  }
  const file = files[index]
  return {
    file_index: file.file_index,
    label: labelOf(files, index),
    file_name: file.file_name,
    page_count: file.page_count,
  }
}

/** 文件被替换后老师还没确认看到过：详情页据此显示「文件待确认」。 */
export function teacherUnconfirmed(order) {
  if (!order) return false
  if (typeof order.files_unconfirmed === 'boolean') return order.files_unconfirmed
  return Boolean(order.file_changed_at) && !order.files_seen_at
}
