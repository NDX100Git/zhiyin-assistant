/**
 * 订单文件清单归一化。
 *
 * 后端多文件订单带 files 数组，每项是
 * {file_index, file_key, file_name, page_count, print_pages, print_side, color_mode,
 * paper_size, copies, amount_cents}；老订单、后端未升级时只有单文件的
 * file_key / file_name / page_count。
 * 换文件标记可能是 file_change_unseen 布尔，也可能是
 * file_changed_at + change_seen_at（后端现有写法叫 files_seen_at / files_unconfirmed）。
 * 老师端所有文件相关的展示都走这里，字段缺失不会报错也不会空白。
 */

/** 数组来源按优先级取第一个非空数组。 */
const FILE_ARRAY_KEYS = ['files', 'order_files', 'file_list']

/** 纸张规格选项：与后端 allowed_paper_sizes 同口径。 */
export const PAPER_SIZES = ['A4', 'A3', 'B5']

/** 页数统一成非负整数，脏数据按 0 处理。 */
function toCount(value) {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0
}

/** 单双面：1=单面 2=双面，缺省按双面；颜色：0=黑白 1=彩色。 */
function toSide(value) {
  return Number(value) === 1 ? 1 : 2
}

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

/** 一份文件的打印参数摘要：双面 · 黑白 · A4 · 2 份。 */
export function paramSummary(file) {
  if (!file) return ''
  return [
    file.print_side === 1 ? '单面' : '双面',
    file.color_mode === 1 ? '彩色' : '黑白',
    file.paper_size,
    `${file.copies} 份`,
  ].join(' · ')
}

function rawList(row) {
  if (!row) return []
  for (const key of FILE_ARRAY_KEYS) {
    const value = row[key]
    if (Array.isArray(value) && value.length > 0) return value
  }
  return []
}

/**
 * 返回 `[{id, seq, file_name, page_count, print_pages, print_side, color_mode,
 * paper_size, copies, amount_cents}]`，按 seq 升序。
 * 没有清单的老订单用单文件字段兜底成一条；文件名空时给「文件 N」占位。
 */
export function orderFiles(row) {
  const list = rawList(row)
  if (list.length === 0) {
    // 兜底：只有单文件字段。文件名空时沿用原来的「论文文档」占位，老订单观感不变。
    if (!row) return []
    if (!row.file_key && !row.file_name && !toCount(row.page_count)) return []
    const pageCount = toCount(row.page_count)
    const copies = toCopies(row.copies)
    return [
      {
        id: null,
        seq: 1,
        file_name: row.file_name || '论文文档',
        page_count: pageCount,
        print_pages: toCount(row.print_page_count) || pageCount * copies,
        print_side: toSide(row.print_side),
        color_mode: toColor(row.color_mode),
        paper_size: toPaper(row.paper_size),
        copies,
        amount_cents: toCount(row.amount_cents),
      },
    ]
  }
  const files = list.map((item, index) => {
    const file = item && typeof item === 'object' ? item : {}
    // 后端的顺序字段叫 file_index，从 1 开始；seq 是早期约定的别名
    const seq = toCount(file.file_index) || toCount(file.seq) || index + 1
    const pageCount = toCount(file.page_count)
    const copies = toCopies(file.copies)
    return {
      id: file.file_index ?? file.file_id ?? file.id ?? null,
      seq,
      file_name: file.file_name ? String(file.file_name) : `文件 ${seq}`,
      page_count: pageCount,
      print_pages: toCount(file.print_pages) || pageCount * copies,
      print_side: toSide(file.print_side),
      color_mode: toColor(file.color_mode),
      paper_size: toPaper(file.paper_size),
      copies,
      amount_cents: toCount(file.amount_cents),
    }
  })
  // sort 是稳定的，seq 相同时保持后端给的顺序
  files.sort((a, b) => a.seq - b.seq)
  return files
}

/** 一单几个文件；老订单恒为 1。 */
export function isMultiFile(row) {
  return orderFiles(row).length > 1
}

/** 总页数：优先按清单累加，清单页数缺失时退回订单自带的 page_count。 */
export function totalPages(row) {
  const files = orderFiles(row)
  const sum = files.reduce((acc, file) => acc + file.page_count, 0)
  return sum > 0 ? sum : toCount(row && row.page_count)
}

/** 整单打印页数：各份 page_count × copies 之和，缺失时退回订单自带值。 */
export function totalPrintPages(row) {
  const files = orderFiles(row)
  const sum = files.reduce((acc, file) => acc + file.print_pages, 0)
  return sum > 0 ? sum : toCount(row && row.print_page_count)
}

/** 整单金额（分）：各份行金额之和，缺失时退回订单自带金额。 */
export function totalAmountCents(row) {
  const files = orderFiles(row)
  const sum = files.reduce((acc, file) => acc + file.amount_cents, 0)
  return sum > 0 ? sum : toCount(row && row.amount_cents)
}

/** 单文件给「文件名 · 42 页」，多文件给「3 个文件 · 共 42 页」。 */
export function fileSummary(row) {
  const files = orderFiles(row)
  if (files.length === 0) return '暂无文件'
  const pages = totalPages(row)
  const tail = pages > 0 ? `${pages} 页` : '页数未知'
  if (files.length === 1) return `${files[0].file_name} · ${tail}`
  return `${files.length} 个文件 · 共 ${tail}`
}

function toTime(value) {
  if (!value) return 0
  const time = new Date(value).getTime()
  return Number.isFinite(time) ? time : 0
}

/**
 * 换文件未确认标记。
 * 有布尔 file_change_unseen 就用它；否则「有 file_changed_at 且（没有
 * change_seen_at 或 file_changed_at 晚于 change_seen_at）」算未确认。
 */
export function hasUnseenChange(row) {
  if (!row) return false
  // 布尔标记：file_change_unseen 是约定的名字，files_unconfirmed 是后端现有写法
  const flag = row.file_change_unseen ?? row.files_unconfirmed
  if (flag !== undefined && flag !== null) {
    return Boolean(flag)
  }
  if (!row.file_changed_at) return false
  const changedAt = toTime(row.file_changed_at)
  // 有时间但解析不出来，宁可信其有，别让老师印了旧文件
  if (!changedAt) return true
  const seenAt = toTime(row.change_seen_at ?? row.files_seen_at)
  if (!seenAt) return true
  return changedAt > seenAt
}

/**
 * 这次换文件针对哪一份文件：返回 {id, seq, label, file_name, page_count}。
 * 多文件时 label 形如「第 2 个文件」，能让老师一眼知道换的是哪一份。
 */
export function changeTarget(row) {
  const files = orderFiles(row)
  const change = (row && row.file_change) || {}
  const changeName = change.new_file_name ? String(change.new_file_name) : ''
  const multi = files.length > 1
  const labelOf = (index) => (multi ? `第 ${index + 1} 个文件` : '文件')

  const hit = (index) => {
    const file = files[index]
    return {
      id: file.id,
      seq: file.seq,
      label: labelOf(index),
      file_name: file.file_name,
      page_count: file.page_count,
    }
  }

  // 换文件申请里带的文件 id 最准
  const changeId = change.file_id ?? change.new_file_id ?? null
  if (changeId !== null && changeId !== undefined) {
    const index = files.findIndex((file) => String(file.id) === String(changeId))
    if (index >= 0) return hit(index)
  }
  // 其次是请求里的 file_index（后端当前给的就是这个，1 起算）
  const changeSeq = toCount(change.file_index ?? change.seq)
  if (changeSeq) {
    const index = files.findIndex((file) => file.seq === changeSeq)
    if (index >= 0) return hit(index)
  }
  // 再退一步按新文件名对
  if (changeName) {
    const index = files.findIndex((file) => file.file_name === changeName)
    if (index >= 0) return hit(index)
  }
  // 再兜底：新文件通常是清单里最后一份
  if (files.length > 0) return hit(files.length - 1)
  return {
    id: null,
    seq: 1,
    label: '文件',
    file_name: changeName || (row && row.file_name) || '未命名',
    page_count: toCount(change.new_page_count),
  }
}
