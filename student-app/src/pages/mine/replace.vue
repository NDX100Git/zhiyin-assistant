<template>
  <view class="page" :class="[uiThemeClass, uiSchemeClass]">
    <view v-if="!order" class="card center loading">加载中…</view>

    <template v-else>
      <view class="card">
        <view class="card-head">
          <text class="card-title">当前订单</text>
          <text class="tag">{{ order.status_label }}</text>
        </view>
        <view class="row"><text class="k">订单号</text><text class="v">{{ order.order_no }}</text></view>
        <view class="row"><text class="k">取件码</text><text class="v">{{ order.pickup_code }} · 换文件后不变</text></view>
        <view v-if="files.length > 1" class="file-block">
          <text class="k">当前文件（{{ files.length }} 份 · 文档 {{ pagesTotal }} 页 · 打印 {{ printPagesTotal }} 页）</text>
          <view class="file-body">
            <view v-for="(item, index) in files" :key="item.file_index" class="file-line">
              <text class="file-index">{{ index + 1 }}</text>
              <view class="file-line-main">
                <text class="file-name">{{ item.file_name }}</text>
                <text class="file-params">{{ paramSummary(item) }}</text>
                <text class="file-pages">文档 {{ item.page_count }} 页 · 打印 {{ item.print_pages }} 页</text>
              </view>
              <text class="file-amount">{{ formatMoney(item.amount_cents) }}</text>
            </view>
          </view>
        </view>
        <view v-else class="row">
          <text class="k">当前文件</text>
          <text class="v">{{ files.length ? files[0].file_name : (order.file_name || '论文文档') }}</text>
        </view>
        <view class="row"><text class="k">文档页数</text><text class="v">{{ pagesTotal }} 页</text></view>
        <view class="row"><text class="k">打印页数</text><text class="v">{{ printPagesTotal }} 页</text></view>
        <view class="row">
          <text class="k">当前金额</text>
          <text class="v money">{{ formatMoney(order.amount_cents) }}{{ order.paid ? '（已支付）' : '（未支付）' }}</text>
        </view>
      </view>

      <view v-if="blockedReason" class="card blocked">
        <text class="blocked-title">当前不能换文件</text>
        <text class="blocked-text">{{ blockedReason }}</text>
        <button class="ghost block" @click="goDetail">返回订单详情</button>
      </view>

      <template v-else>
        <!-- 多文件订单换文件必须先选换哪一份，否则老师不知道该替掉哪份 -->
        <view v-if="files.length > 1" class="card">
          <view class="card-head">
            <text class="card-title">1. 选要换哪一份</text>
            <text class="tag">{{ hasTarget ? '已选' : '请选择' }}</text>
          </view>
          <view
            v-for="(item, index) in files"
            :key="item.file_index"
            class="target-row"
            :class="{ on: targetIndex === index }"
            @click="selectTarget(index)"
          >
            <text class="target-index">{{ index + 1 }}</text>
            <view class="target-main">
              <text class="target-name">{{ item.file_name }}</text>
              <text class="target-meta">{{ paramSummary(item) }} · 文档 {{ item.page_count }} 页 · 打印 {{ item.print_pages }} 页 · 第 {{ item.file_index }} 份</text>
            </view>
            <text class="target-check">{{ targetIndex === index ? '已选' : '选它' }}</text>
          </view>
        </view>

        <view class="card">
          <view class="card-head">
            <text class="card-title">{{ files.length > 1 ? '2. 选择新文件' : '1. 选择新文件' }}</text>
            <text v-if="fileKey" class="tag ok">已选择</text>
          </view>
          <view v-if="fileKey" class="file-info">
            <text class="file-name">{{ fileName }}</text>
            <text class="file-meta">{{ formatSize(fileSize) }}</text>
          </view>
          <view v-else class="empty">支持 PDF / Word / PPT / Excel，单文件不超过 50MB</view>
          <text v-if="preparing" class="preparing">正在读取页数…</text>

          <view class="actions">
            <button class="action" :disabled="busy" @click="pickDocument">选择文档</button>
            <button class="action" :disabled="busy" @click="useDemo">载入示例论文</button>
          </view>
        </view>

        <view v-if="fileKey" class="card">
          <view class="card-head">
            <text class="card-title">{{ files.length > 1 ? '3. 确认变更' : '2. 确认变更' }}</text>
            <text class="tag">{{ immediate ? '立即生效' : '需老师确认' }}</text>
          </view>
          <view v-if="files.length > 1" class="row">
            <text class="k">替换哪份</text>
            <text class="v">{{ targetLabel }}</text>
          </view>
          <view class="row"><text class="k">新页数</text><text class="v">{{ pageCount }} 页</text></view>
          <view v-if="targetFile" class="row">
            <text class="k">这份参数</text>
            <text class="v">{{ paramSummary(targetFile) }}（换文件不改参数）</text>
          </view>
          <!-- 换后金额由服务端报价接口算，页面不乘单价 -->
          <view v-if="quoted" class="row">
            <text class="k">换后金额</text>
            <text class="v money">{{ formatMoney(quoted.amount_cents) }}</text>
          </view>
          <view v-if="quoted" class="row">
            <text class="k">换后页数</text>
            <text class="v">文档 {{ quoted.page_count }} 页 · 打印 {{ quoted.print_page_count }} 页</text>
          </view>
          <text v-if="quoteError" class="quote-error">{{ quoteError }}（以老师确认后服务端重算为准）</text>
          <view class="row"><text class="k">订单号</text><text class="v">{{ order.order_no }}（不变）</text></view>

          <textarea
            v-model="reason"
            class="reason"
            maxlength="100"
            placeholder="换文件原因（可选），如：传错版本、内容有误"
          />

          <view class="notice">
            <text v-if="immediate" class="notice-line">待支付订单换文件立即生效：页数与金额重算，订单号和取件码不变。</text>
            <text v-else class="notice-line">已付款订单需要老师确认后生效；确认后订单排到队尾重新排队，取件码不变。</text>
            <text class="notice-line">价格差不出在线支付：新价更低时多付部分不退，新价更高时差额在取件时现场补缴。</text>
          </view>

          <button
            class="primary block"
            :disabled="busy || preparing || !fileKey || !targetReady"
            @click="submit"
          >
            {{ busy ? '提交中…' : (targetReady ? submitLabel : '请先选要换哪一份') }}
          </button>
        </view>
      </template>
    </template>
  </view>
</template>

<script setup>
import { onLoad } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'
import { quoteOrder, request, uploadFile } from '@/services/api'
import { ensureLogin } from '@/services/auth'
import { chooseDocument, loadDemoDocument } from '@/services/filePicker'
import { notify, notifyError } from '@/services/notify'
import { orderFiles, paramSummary, totalPages, totalPrintPages } from '@/services/orderFiles'
import { useUiTheme } from '@/ui/theme'

/** 允许换文件的状态：待支付直接生效，排队中与异常单走老师确认。 */
const REPLACEABLE = ['pending_pay', 'queuing', 'abnormal']

const BLOCKED_TEXT = {
  printing: '文件正在打印，不能再换。确实传错了请到文印室联系老师处理。',
  ready: '文件已经打印完成，不能再换文件。',
  completed: '订单已完成，不能再换文件。',
  cancelled: '订单已取消，不能再换文件。',
}

const { uiThemeClass, uiSchemeClass } = useUiTheme()

const orderId = ref('')
const order = ref(null)
const busy = ref(false)
const preparing = ref(false)
const fileKey = ref('')
const fileName = ref('')
const fileSize = ref(0)
const pageCount = ref(1)
const reason = ref('')
/** 多文件订单里要换第几份（数组下标）；单文件订单恒为 0。 */
const targetIndex = ref(0)

const immediate = computed(() => !!order.value && order.value.status === 'pending_pay')

/** 当前文件清单：多文件订单逐份列出，要换哪份必须由学生显式选。 */
const files = computed(() => orderFiles(order.value))
const pagesTotal = computed(() => totalPages(order.value))
const printPagesTotal = computed(() => totalPrintPages(order.value))
const multiFile = computed(() => files.value.length > 1)
/** 单文件订单不用选，直接就是那唯一一份；多文件必须选过（targetIndex 从 -1 起）。 */
const hasTarget = computed(() => files.value.length > 0 && targetIndex.value >= 0)
const targetFile = computed(() => files.value[targetIndex.value] || null)
const targetReady = computed(() => hasTarget.value && !!targetFile.value)
const targetLabel = computed(() =>
  targetFile.value ? `第 ${targetFile.value.file_index} 个文件：${targetFile.value.file_name}` : '未选择',
)

/**
 * 换完之后的整单报价（`{files, page_count, print_page_count, amount_cents}`）。
 *
 * 只换目标那一份的文件内容，其余份的参数与行金额照旧；金额由服务端算，
 * 页面不乘单价，也不自己抬页数×份数的结论。
 */
const quoted = ref(null)
const quoteError = ref('')
let quoteSeq = 0

/** 把「目标那一份换成新文件」之后的完整清单交给报价接口。 */
async function refreshQuote() {
  if (!order.value || !targetFile.value || !fileKey.value) {
    quoted.value = null
    quoteError.value = ''
    return
  }
  if (!(Number(pageCount.value) >= 1)) return
  const target = targetFile.value
  const list = files.value.map((item) =>
    item.file_index === target.file_index
      ? {
          file_key: fileKey.value,
          file_name: fileName.value,
          page_count: Number(pageCount.value),
          print_side: item.print_side,
          color_mode: item.color_mode,
          paper_size: item.paper_size,
          copies: item.copies,
        }
      : {
          file_key: item.file_key,
          file_name: item.file_name,
          page_count: Math.max(1, Number(item.page_count) || 1),
          print_side: item.print_side,
          color_mode: item.color_mode,
          paper_size: item.paper_size,
          copies: item.copies,
        },
  )
  const seq = ++quoteSeq
  try {
    const data = await quoteOrder({
      files: list,
      print_side: order.value.print_side === 1 ? 1 : 2,
      color_mode: order.value.color_mode === 1 ? 1 : 0,
      copies: Math.max(1, Number(order.value.copies) || 1),
      paper_size: order.value.paper_size || 'A4',
    })
    if (seq !== quoteSeq) return
    quoted.value = data
    quoteError.value = ''
  } catch (error) {
    if (seq !== quoteSeq) return
    // 拿不到报价时不给数字，只把服务端的话原样告诉学生
    quoted.value = null
    quoteError.value = error.message || '服务端报价失败'
  }
}

function selectTarget(index) {
  targetIndex.value = index
  notify(`已选择第 ${index + 1} 个文件`, 'success')
  refreshQuote()
}

const blockedReason = computed(() => {
  const current = order.value
  if (!current) return ''
  if (current.file_change) return '已提交一份换文件申请，等老师确认后才能再换。'
  if (!REPLACEABLE.includes(current.status)) {
    return BLOCKED_TEXT[current.status] || '当前订单状态不能换文件。'
  }
  return ''
})

const submitLabel = computed(() =>
  immediate.value ? '确认换文件（立即生效）' : '提交换文件申请',
)

function formatMoney(cents) {
  return `¥${((Number(cents) || 0) / 100).toFixed(2)}`
}

function formatSize(size) {
  if (!size) return ''
  return size > 1024 * 1024
    ? `${(size / 1024 / 1024).toFixed(1)} MB`
    : `${Math.round(size / 1024)} KB`
}

async function load() {
  if (!orderId.value) return
  try {
    order.value = await request(`/api/order/${orderId.value}`)
    // 单文件订单不用选；多文件订单从「未选择」开始，避免默认替掉第一份
    targetIndex.value = files.value.length > 1 ? -1 : 0
  } catch (error) {
    notifyError(error)
  }
}

/** 上传后立刻取页数：与下单页走的是同一条「上传 + page-count」链路。 */
async function useUploaded(data) {
  fileKey.value = data.file_key
  fileName.value = data.file_name || '文档'
  fileSize.value = data.size || 0
  pageCount.value = 1
  preparing.value = true
  try {
    const result = await request('/api/file/page-count', {
      method: 'POST',
      data: { file_key: fileKey.value },
    })
    pageCount.value = Math.max(1, Number(result.page_count) || 1)
    await refreshQuote()
  } catch (error) {
    fileKey.value = ''
    notifyError(error)
  } finally {
    preparing.value = false
  }
}

async function pickDocument() {
  if (busy.value) return
  try {
    await ensureLogin()
    const picked = await chooseDocument()
    busy.value = true
    const uploaded = await uploadFile(picked.path, picked.name)
    await useUploaded(uploaded)
    notify('上传成功', 'success')
  } catch (error) {
    notifyError(error)
  } finally {
    busy.value = false
  }
}

async function useDemo() {
  if (busy.value) return
  try {
    await ensureLogin()
    busy.value = true
    await useUploaded(await loadDemoDocument())
    notify('已载入示例论文', 'success')
  } catch (error) {
    notifyError(error)
  } finally {
    busy.value = false
  }
}

async function submit() {
  if (busy.value || preparing.value || !fileKey.value || blockedReason.value) return
  if (!targetReady.value) {
    notify('请先选要换哪一份文件')
    return
  }
  busy.value = true
  try {
    const result = await request(`/api/order/${orderId.value}/replace-file`, {
      method: 'POST',
      data: {
        file_key: fileKey.value,
        file_name: fileName.value,
        page_count: pageCount.value,
        // 多文件订单必须指明换哪一份；单文件订单传 1 与不传等价
        file_index: targetFile.value ? targetFile.value.file_index : null,
        reason: reason.value.trim() || null,
      },
    })
    const applied = result && typeof result.applied === 'boolean' ? result.applied : immediate.value
    notify(applied ? '文件已更新，页数与金额已重算' : '已提交，等老师确认后生效', 'success')
    // 退回详情页：详情页 onShow 会立刻重新拉一次订单，展示新页数或待确认状态
    setTimeout(() => uni.navigateBack(), 700)
  } catch (error) {
    notifyError(error)
  } finally {
    busy.value = false
  }
}

function goDetail() {
  uni.navigateBack()
}

onLoad(async (options) => {
  orderId.value = (options && options.id) || ''
  try {
    await ensureLogin()
    await load()
  } catch (error) {
    notifyError(error)
  }
})
</script>

<style scoped>
.page {
  min-height: 100vh;
  padding: 24rpx 24rpx 120rpx;
  box-sizing: border-box;
  background-color: var(--zi-bg);
  background-image: var(--zi-bg-image);
  color: var(--zi-ink);
}
.card {
  margin-bottom: 20rpx;
  padding: 28rpx;
  box-sizing: border-box;
  background: var(--zi-paper);
  border: var(--zi-card-border);
  border-radius: var(--zi-radius);
  box-shadow: var(--zi-card-shadow);
}
.card.center,
.loading {
  text-align: center;
  color: var(--zi-muted);
  font-size: 26rpx;
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-title {
  padding-left: 14rpx;
  border-left: 8rpx solid var(--zi-accent);
  color: var(--zi-ink);
  font-size: 30rpx;
  font-weight: 900;
  line-height: 1.4;
  letter-spacing: 2rpx;
}
.tag {
  padding: 4rpx 14rpx;
  border: 2rpx solid var(--zi-accent);
  border-radius: var(--zi-pill-radius);
  background: transparent;
  color: var(--zi-accent);
  font-size: 22rpx;
  font-weight: 800;
  line-height: 1.3;
}
.tag.ok {
  border-color: var(--zi-ok);
  color: var(--zi-ok);
}
.empty {
  margin-top: 16rpx;
  color: var(--zi-muted);
  font-size: 26rpx;
  line-height: 1.5;
}
.preparing {
  display: block;
  margin-top: 12rpx;
  color: var(--zi-accent);
  font-size: 24rpx;
  font-weight: 700;
}

.row {
  display: flex;
  align-items: flex-start;
  padding: 16rpx 0;
  border-bottom: 2rpx solid var(--zi-line);
  font-size: 28rpx;
}
.row:last-child {
  padding-bottom: 0;
  border-bottom: none;
}
.k {
  flex: 0 0 150rpx;
  width: 150rpx;
  color: var(--zi-muted);
  font-size: 26rpx;
  font-weight: 600;
  line-height: 1.6;
}
.v {
  flex: 1;
  min-width: 0;
  color: var(--zi-ink);
  font-size: 28rpx;
  font-weight: 700;
  line-height: 1.6;
  word-break: break-all;
}
.money {
  color: var(--zi-accent);
  font-weight: 900;
}

.file-info {
  display: flex;
  justify-content: space-between;
  margin-top: 18rpx;
  padding: 18rpx;
  border: 2rpx solid var(--zi-line);
  border-radius: var(--zi-radius);
  background: var(--zi-soft);
}
.file-name {
  flex: 1;
  min-width: 0;
  color: var(--zi-ink);
  font-size: 26rpx;
  font-weight: 700;
  line-height: 1.5;
  word-break: break-all;
}
.file-meta {
  margin-left: 16rpx;
  color: var(--zi-muted);
  font-size: 24rpx;
}

/* 多文件清单与「选哪一份」列表：整行可点，触控高度不小于 96rpx */
.file-block {
  display: flex;
  flex-direction: column;
  padding: 16rpx 0;
  border-bottom: 2rpx solid var(--zi-line);
  font-size: 28rpx;
}
.file-block .k {
  flex: 0 0 auto;
  width: auto;
  margin-bottom: 6rpx;
}
.file-body {
  flex: 1;
  min-width: 0;
}
.file-line {
  display: flex;
  align-items: flex-start;
  padding: 6rpx 0;
}
.file-index {
  flex: 0 0 34rpx;
  margin-right: 10rpx;
  color: var(--zi-muted);
  font-size: 24rpx;
  font-weight: 800;
  line-height: 1.6;
}
.file-line .file-name {
  flex: 1;
  min-width: 0;
  color: var(--zi-ink);
  font-size: 26rpx;
  font-weight: 700;
  line-height: 1.6;
  word-break: break-all;
}
.file-line-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.file-params {
  color: var(--zi-ink);
  font-size: 22rpx;
  line-height: 1.6;
}
.file-amount {
  flex: 0 0 auto;
  margin-left: 14rpx;
  color: var(--zi-ink);
  font-size: 26rpx;
  font-weight: 900;
  white-space: nowrap;
}
.file-pages {
  margin-left: 0;
  color: var(--zi-muted);
  font-size: 22rpx;
  line-height: 1.6;
  word-break: break-all;
}
.quote-error {
  display: block;
  margin-top: 12rpx;
  color: var(--zi-danger);
  font-size: 22rpx;
  font-weight: 700;
  line-height: 1.6;
}
.target-row {
  display: flex;
  align-items: center;
  min-height: 96rpx;
  margin-top: 16rpx;
  padding: 18rpx;
  border: 2rpx solid var(--zi-line);
  border-radius: var(--zi-radius);
  background: var(--zi-soft-2);
}
.target-row.on {
  border-color: var(--zi-accent);
  background: var(--zi-soft);
}
.target-index {
  flex: 0 0 40rpx;
  margin-right: 12rpx;
  color: var(--zi-muted);
  font-size: 24rpx;
  font-weight: 800;
  line-height: 1.6;
}
.target-main {
  flex: 1;
  min-width: 0;
}
.target-name {
  display: block;
  color: var(--zi-ink);
  font-size: 26rpx;
  font-weight: 700;
  line-height: 1.5;
  word-break: break-all;
}
.target-meta {
  display: block;
  margin-top: 4rpx;
  color: var(--zi-muted);
  font-size: 22rpx;
}
.target-check {
  display: flex;
  align-items: center;
  min-height: 56rpx;
  margin-left: 16rpx;
  padding: 0 20rpx;
  border: 2rpx solid var(--zi-accent);
  border-radius: var(--zi-pill-radius);
  color: var(--zi-accent);
  font-size: 22rpx;
  font-weight: 800;
  line-height: 1;
  white-space: nowrap;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  margin-top: 20rpx;
  gap: 16rpx;
}
.action {
  flex: 1 1 45%;
  min-height: 88rpx;
  margin: 0;
  padding: 0 16rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  border: var(--zi-button-border);
  border-radius: var(--zi-button-radius);
  background: transparent;
  color: var(--zi-ink);
  font-size: 26rpx;
  font-weight: 800;
  line-height: 1.2;
}
.reason {
  width: 100%;
  height: 140rpx;
  margin-top: 20rpx;
  padding: 18rpx;
  box-sizing: border-box;
  border: 2rpx solid var(--zi-line-strong);
  border-radius: var(--zi-radius);
  background: var(--zi-soft-2);
  color: var(--zi-ink);
  font-size: 26rpx;
  line-height: 1.5;
}
.notice {
  margin-top: 20rpx;
  padding: 18rpx;
  border-left: 8rpx solid var(--zi-warn);
  background: var(--zi-warn-bg);
}
.notice-line {
  display: block;
  color: var(--zi-warn);
  font-size: 24rpx;
  line-height: 1.6;
}
.primary,
.ghost {
  width: 100%;
  min-height: 96rpx;
  margin: 24rpx 0 0;
  padding: 0 16rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  border: var(--zi-button-border);
  border-radius: var(--zi-button-radius);
  font-size: 28rpx;
  font-weight: 800;
  line-height: 1.2;
  letter-spacing: 2rpx;
}
.primary {
  background: var(--zi-primary-bg);
  color: var(--zi-primary-ink);
  box-shadow: var(--zi-primary-shadow);
}
.ghost {
  background: var(--zi-ghost-bg);
  color: var(--zi-ghost-ink);
}
.blocked {
  border-left: 8rpx solid var(--zi-danger);
}
.blocked-title {
  display: block;
  color: var(--zi-danger);
  font-size: 28rpx;
  font-weight: 900;
  letter-spacing: 1rpx;
}
.blocked-text {
  display: block;
  margin-top: 12rpx;
  color: var(--zi-ink);
  font-size: 26rpx;
  line-height: 1.6;
}
</style>
