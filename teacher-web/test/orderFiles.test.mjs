/**
 * 老师端最小单测：跑 `node --test test/` 即可，不依赖浏览器与 Element Plus。
 *
 * 被测对象是 src/api/orderFiles.js 里的纯函数——工作台所有文件、页数、金额的
 * 展示口径都从它出来，这里锁住几条兜底规则，避免哪天归一化改动把界面数字改错。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  fileSummary,
  orderFiles,
  paramSummary,
  totalAmountCents,
  totalPages,
  totalPrintPages,
} from '../src/api/orderFiles.js'

test('多文件清单按 seq 升序归一化，缺失字段按约定兜底', () => {
  const row = {
    page_count: 99,
    files: [
      { seq: 2, file_name: 'b.pdf', page_count: 5, copies: 2, print_side: 1 },
      { seq: 1, file_name: 'a.pdf', page_count: 3 },
    ],
  }
  const files = orderFiles(row)
  assert.equal(files.length, 2)
  assert.equal(files[0].seq, 1)
  assert.equal(files[0].file_name, 'a.pdf')
  // 缺 copies 时按 1 份，缺 print_side 时按双面，缺 paper_size 时按 A4
  assert.equal(files[0].copies, 1)
  assert.equal(files[0].print_side, 2)
  assert.equal(files[0].paper_size, 'A4')
  // 缺 print_pages 时按 page_count × copies 兜底
  assert.equal(files[0].print_pages, 3)
  assert.equal(files[1].print_pages, 10)
})

test('老订单没有 files 时退回订单自带的单文件字段', () => {
  const row = { file_name: 'thesis.pdf', page_count: 42, print_page_count: 84, amount_cents: 1234 }
  assert.deepEqual(orderFiles(row).map((file) => file.file_name), ['thesis.pdf'])
  assert.equal(totalPages(row), 42)
  assert.equal(totalPrintPages(row), 84)
  assert.equal(totalAmountCents(row), 1234)
  assert.equal(fileSummary(row), 'thesis.pdf · 42 页')
})

test('整单页数、打印页数、金额都是逐份累加', () => {
  const row = {
    files: [
      { seq: 1, page_count: 3, copies: 2, amount_cents: 600 },
      { seq: 2, page_count: 5, copies: 1, amount_cents: 500 },
    ],
  }
  assert.equal(totalPages(row), 8)
  assert.equal(totalPrintPages(row), 11)
  assert.equal(totalAmountCents(row), 1100)
  assert.equal(fileSummary(row), '2 个文件 · 共 8 页')
})

test('逐份参数摘要按「单双面 · 颜色 · 纸张 · 份数」拼接', () => {
  assert.equal(
    paramSummary({ print_side: 1, color_mode: 1, paper_size: 'A3', copies: 2 }),
    '单面 · 彩色 · A3 · 2 份',
  )
  assert.equal(paramSummary(null), '')
})
