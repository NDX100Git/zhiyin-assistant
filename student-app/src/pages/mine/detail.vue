<template>
  <view class="page" :class="[uiThemeClass, uiSchemeClass]">
    <!-- #ifdef H5 -->
    <view class="pull-refresh" :style="{ height: pullOffset + 'px' }">
      <text class="pull-refresh-text">
        {{ pullRefreshing ? '正在刷新…' : (pullOffset >= pullThreshold ? '松开刷新' : '下拉刷新') }}
      </text>
    </view>
    <!-- #endif -->
    <view v-if="!order" class="card loading">加载中…</view>
    <template v-else>
      <view class="hero">
        <view class="hero-slash"></view>
        <view class="status"><text class="status-text">{{ order.status_label }}</text></view>
        <view v-if="fileChange" class="pending-flag">
          <text class="pending-flag-text">文件待确认</text>
        </view>
        <view class="code-block">
          <text class="code">{{ order.pickup_code }}</text>
        </view>
        <text class="code-tip">取件码 · 凭此码到文印室取件</text>
        <view v-if="isActive || order.status === 'ready' || order.status === 'completed'" class="queue-row">
          <text v-if="isActive" class="queue">前面 {{ order.queue_position }} 单 · 预计 {{ order.estimated_minutes }} 分钟</text>
          <text v-else-if="order.status === 'ready'" class="queue">已打印完成，请到文印室取件</text>
          <text v-else-if="order.status === 'completed'" class="queue">已完成，感谢使用</text>
        </view>
      </view>

      <view class="card">
        <view class="row"><text class="k">订单号</text><text class="v">{{ order.order_no }}</text></view>
        <!-- 多文件订单一单一个取件码：清单逐份列出参数、文档页数、打印页数与行金额 -->
        <view v-if="files.length > 1" class="file-block">
          <text class="k">文件（{{ files.length }} 份 · 文档 {{ pagesTotal }} 页 · 打印 {{ printPagesTotal }} 页）</text>
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
        <view v-else class="row">
          <text class="k">文件</text>
          <text class="v">{{ files.length ? files[0].file_name : (order.file_name || '论文文档') }}</text>
        </view>
        <view class="row"><text class="k">文档页数</text><text class="v">{{ pagesTotal }} 页</text></view>
        <view class="row"><text class="k">打印页数</text><text class="v">{{ printPagesTotal }} 页</text></view>
        <view v-if="fileChangedAt" class="row">
          <text class="k">文件状态</text>
          <text class="v">{{ teacherUnconfirmed(order) ? '已替换，等老师重新下载确认' : '已替换，老师已确认' }}</text>
        </view>
        <view class="row">
          <text class="k">打印</text>
          <text class="v">{{ printSummary }}</text>
        </view>
        <view class="row"><text class="k">金额</text><text class="v money">{{ formatMoney(order.amount_cents) }}{{ order.paid ? '（已支付）' : '（未支付）' }}</text></view>
        <view class="row"><text class="k">下单时间</text><text class="v">{{ formatTime(order.created_at) }}</text></view>
        <view v-if="order.cancel_reason" class="row"><text class="k">取消原因</text><text class="v">{{ order.cancel_reason }}</text></view>
      </view>

      <view v-if="fileChange" class="card change-card">
        <view class="card-head">
          <view class="title-mark"></view>
          <text class="card-title">换文件待老师确认</text>
        </view>
        <view class="row"><text class="k">变更编号</text><text class="v">#{{ fileChange.change_id }}</text></view>
        <view v-if="changeInfo && files.length > 1" class="row">
          <text class="k">换的是</text>
          <text class="v">{{ changeInfo.label }}：{{ changeInfo.file_name }}</text>
        </view>
        <view class="row"><text class="k">新文件</text><text class="v">{{ fileChange.new_file_name || '新文件' }}</text></view>
        <view class="row"><text class="k">新页数</text><text class="v">{{ fileChange.new_page_count }} 页</text></view>
        <view class="row"><text class="k">新金额</text><text class="v money">{{ formatMoney(fileChange.new_amount_cents) }}</text></view>
        <view class="row"><text class="k">提交时间</text><text class="v">{{ formatTime(fileChange.submitted_at) }}</text></view>
        <view v-if="fileChange.reason" class="row"><text class="k">变更原因</text><text class="v">{{ fileChange.reason }}</text></view>
        <text class="change-tip">
          老师确认前仍按原文件与原金额处理。确认后取件码不变，订单排到队尾重新排队，金额差额在取件时现场结清（多付部分不退）。
        </text>
      </view>

      <view class="card">
        <view class="card-head">
          <view class="title-mark"></view>
          <text class="card-title">进度</text>
        </view>
        <view class="timeline">
          <view v-for="(step, index) in timeline" :key="step.key" class="timeline-item">
            <view class="rail">
              <view class="dot" :class="{ on: step.done }"></view>
              <view v-if="index < timeline.length - 1" class="line" :class="{ on: step.done }"></view>
            </view>
            <text class="step" :class="{ on: step.done }">{{ step.label }}</text>
          </view>
        </view>
      </view>

      <view class="actions">
        <button v-if="order.status === 'pending_pay'" class="primary" @click="pay">模拟支付</button>
        <button v-if="order.status === 'pending_pay' || order.status === 'queuing'" class="ghost" @click="cancel">取消订单</button>
        <button class="ghost" :class="{ locked: !canReplace }" @click="onReplace">换文件</button>
        <button class="ghost" @click="goOrder">再来一单</button>
      </view>
    </template>
  </view>
</template>

<script setup>
import { onHide, onLoad, onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import { computed, onUnmounted, ref } from 'vue'
import { POLL_INTERVAL } from '@/config'
import { request } from '@/services/api'
import { ensureLogin } from '@/services/auth'
import { createPoller, notify, notifyError } from '@/services/notify'
import { changeTarget, orderFiles, paramSummary, teacherUnconfirmed, totalPages, totalPrintPages } from '@/services/orderFiles'
import { usePullRefresh } from '@/ui/pullRefresh'
import { useUiTheme } from '@/ui/theme'

const order = ref(null)
const orderId = ref('')
const { uiThemeClass, uiSchemeClass } = useUiTheme()
let visible = false

const isActive = computed(() => order.value && ['queuing', 'printing'].includes(order.value.status))

/** 待老师确认的换文件申请；没有时为 null。 */
const fileChange = computed(() => (order.value && order.value.file_change) || null)

/** 当前生效的文件清单（多文件订单多行，老订单自动兜底成一行）。 */
const files = computed(() => orderFiles(order.value))
const pagesTotal = computed(() => totalPages(order.value))
const printPagesTotal = computed(() => totalPrintPages(order.value))
/** 整单打印参数：单文件直接念这一份的参数，多文件逐份设置。 */
const printSummary = computed(() => {
  if (files.value.length > 1) return '逐份设置，见上方文件清单'
  if (files.value.length === 1) return paramSummary(files.value[0])
  const current = order.value || {}
  return `${current.print_side === 2 ? '双面' : '单面'} · ${current.color_mode === 1 ? '彩色' : '黑白'} · ${current.copies} 份`
})
const fileChangedAt = computed(() => (order.value && order.value.file_changed_at) || '')
/** 这次换文件针对哪一份，多文件时说清「第 N 个文件」。 */
const changeInfo = computed(() => changeTarget(order.value))

/** 允许换文件的状态：待支付直接生效，排队中与异常单走老师确认。 */
const REPLACEABLE = ['pending_pay', 'queuing', 'abnormal']

const canReplace = computed(
  () => !!order.value && REPLACEABLE.includes(order.value.status) && !fileChange.value,
)

const replaceBlockedReason = computed(() => {
  if (!order.value) return ''
  if (fileChange.value) return '已有一份换文件申请等老师确认，确认后才能再换。'
  return (
    {
      printing: '文件正在打印，不能再换。确实传错了请到文印室联系老师处理。',
      ready: '文件已经打印完成，不能再换文件。',
      completed: '订单已完成，不能再换文件。',
      cancelled: '订单已取消，不能再换文件。',
    }[order.value.status] || '当前订单状态不能换文件。'
  )
})

const timeline = computed(() => {
  const current = order.value ? order.value.status : ''
  const steps = [
    { key: 'pending_pay', label: '待支付' },
    { key: 'queuing', label: '排队中' },
    { key: 'printing', label: '打印中' },
    { key: 'ready', label: '待取件' },
    { key: 'completed', label: '已完成' },
  ]
  const orderIndex = steps.findIndex((step) => step.key === current)
  const cancelled = current === 'cancelled'
  return steps.map((step, index) => ({
    ...step,
    done: !cancelled && orderIndex >= 0 && index <= orderIndex,
  }))
})

function formatMoney(cents) {
  return `¥${((Number(cents) || 0) / 100).toFixed(2)}`
}

/** 后端返回的是 ISO 时间串，直接显示会带一个 T，这里换成 年-月-日 时:分。 */
function formatTime(value) {
  if (!value) return ''
  return String(value).replace('T', ' ').slice(0, 16)
}

async function load() {
  if (!orderId.value) return
  order.value = await request(`/api/order/${orderId.value}`)
}

const poller = createPoller(load, POLL_INTERVAL)

onLoad((options) => {
  orderId.value = (options && options.id) || ''
})

onShow(async () => {
  visible = true
  try {
    await ensureLogin()
    poller.start()
  } catch (error) {
    notifyError(error)
  }
})

onHide(() => {
  visible = false
  poller.stop()
})
onUnmounted(() => poller.stop())

async function pay() {
  try {
    await request(`/api/order/${orderId.value}/pay`, { method: 'POST' })
    notify('支付成功，已进入队列', 'success')
    poller.refresh()
  } catch (error) {
    notifyError(error)
  }
}

function cancel() {
  uni.showModal({
    title: '取消订单',
    content: '确认取消这个订单吗？',
    success: async (result) => {
      if (!result.confirm) return
      try {
        await request(`/api/order/${orderId.value}/cancel`, {
          method: 'POST',
          data: { reason: '学生取消订单' },
        })
        notify('订单已取消', 'success')
        poller.refresh()
      } catch (error) {
        notifyError(error)
      }
    },
  })
}

/**
 * 下拉刷新：小程序端由页面配置派发 onPullDownRefresh，H5 端由 usePullRefresh 的手势兜底。
 * 轮询是 30 秒一次，学生想马上看老师确认结果时用下拉。
 */
async function refreshOrder() {
  try {
    await ensureLogin()
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    uni.stopPullDownRefresh()
  }
}

onPullDownRefresh(refreshOrder)

const {
  offset: pullOffset,
  refreshing: pullRefreshing,
  threshold: pullThreshold,
} = usePullRefresh(refreshOrder)

/** 不能换时给明确原因，而不是把入口藏起来让人猜。 */
function onReplace() {
  if (!order.value) return
  if (canReplace.value) {
    uni.navigateTo({ url: `/pages/mine/replace?id=${orderId.value}` })
    return
  }
  uni.showModal({
    title: '不能换文件',
    content: replaceBlockedReason.value,
    showCancel: false,
    confirmText: '知道了',
  })
}

function goOrder() {
  uni.switchTab({ url: '/pages/order/order' })
}
</script>

<style scoped>
.page {
  min-height: 100vh;
  padding: 24rpx;
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
.loading {
  padding: 60rpx 28rpx;
  text-align: center;
  color: var(--zi-muted);
  font-size: 26rpx;
}

/* H5 下拉提示：撑开的高度由手势位移决定，松手后收回 */
.pull-refresh {
  display: flex;
  align-items: flex-end;
  justify-content: center;
  overflow: hidden;
  margin: -24rpx -24rpx 8rpx;
}
.pull-refresh-text {
  padding-bottom: 8rpx;
  color: var(--zi-muted);
  font-size: 24rpx;
  font-weight: 700;
  letter-spacing: 1rpx;
}

/* 取件码主视觉：一眼看到码，再看到状态与提示 */
.hero {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  overflow: hidden;
  margin-bottom: 28rpx;
  padding: 32rpx 28rpx 30rpx;
  box-sizing: border-box;
  background: var(--zi-hero-bg);
  color: var(--zi-hero-ink);
  border: var(--zi-hero-border);
  border-radius: var(--zi-radius);
  box-shadow: var(--zi-hero-shadow);
}
.hero-slash {
  position: absolute;
  right: -120rpx;
  bottom: -80rpx;
  width: 420rpx;
  height: 220rpx;
  background: var(--zi-accent);
  transform: rotate(-14deg);
  z-index: 0;
}
.status {
  position: relative;
  z-index: 1;
  display: inline-block;
  padding: 8rpx 18rpx;
  border: 4rpx solid transparent;
  background: var(--zi-accent);
  color: var(--zi-accent-ink);
  font-size: 26rpx;
  font-weight: 800;
  line-height: 1.2;
  letter-spacing: 2rpx;
}
.status-text {
  color: inherit;
}
.code-block {
  position: relative;
  z-index: 1;
  align-self: stretch;
  margin-top: 22rpx;
  padding-bottom: 10rpx;
  border-bottom: 8rpx solid var(--zi-accent);
}
.code {
  display: block;
  color: var(--zi-hero-ink);
  font-family: Menlo, 'SF Mono', Consolas, 'Courier New', monospace;
  font-size: 70rpx;
  font-weight: 900;
  line-height: 1.15;
  letter-spacing: 2rpx;
  white-space: nowrap;
}
.code-tip {
  position: relative;
  z-index: 1;
  display: block;
  margin-top: 14rpx;
  color: var(--zi-hero-muted);
  font-size: 24rpx;
  font-weight: 600;
  line-height: 1.4;
  letter-spacing: 1rpx;
}
.queue-row {
  position: relative;
  z-index: 1;
  align-self: stretch;
  margin-top: 26rpx;
  padding-top: 20rpx;
  border-top: 2rpx solid var(--zi-hero-line);
}

/* 有换文件申请待确认时，在取件码上方挂一个告示，避免学生以为已经生效 */
.pending-flag {
  position: relative;
  z-index: 1;
  margin-top: 18rpx;
  padding: 6rpx 16rpx;
  border: 3rpx solid var(--zi-warn);
  background: var(--zi-warn-bg);
}
.pending-flag-text {
  color: var(--zi-warn);
  font-size: 24rpx;
  font-weight: 800;
  letter-spacing: 1rpx;
}

/* 换文件待确认卡片：与订单信息卡区分开，靠左侧提示条 */
.change-card {
  border-left: 8rpx solid var(--zi-warn);
}
.change-tip {
  display: block;
  margin-top: 16rpx;
  padding-top: 14rpx;
  border-top: 2rpx solid var(--zi-line);
  color: var(--zi-muted);
  font-size: 24rpx;
  line-height: 1.6;
}
.queue {
  display: block;
  color: var(--zi-hero-ink);
  font-size: 28rpx;
  font-weight: 700;
  line-height: 1.45;
}

/* 订单信息：标签固定宽度，值加粗，长文件名可以折行 */
.row {
  display: flex;
  align-items: flex-start;
  padding: 18rpx 0;
  border-bottom: 2rpx solid var(--zi-line);
  font-size: 28rpx;
}
.row:first-child {
  padding-top: 4rpx;
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
  letter-spacing: 1rpx;
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

/* 多文件清单：与 .row 同一套分隔线，逐份列出顺序、文件名与页数 */
.file-block {
  display: flex;
  flex-direction: column;
  padding: 18rpx 0;
  border-bottom: 2rpx solid var(--zi-line);
  font-size: 28rpx;
}
.file-block .k {
  flex: 0 0 auto;
  width: auto;
  margin-bottom: 6rpx;
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
  font-size: 24rpx;
  line-height: 1.6;
  word-break: break-all;
}

.card-head {
  display: flex;
  align-items: center;
  margin-bottom: 8rpx;
}
.title-mark {
  width: 12rpx;
  height: 30rpx;
  margin-right: 14rpx;
  background: var(--zi-accent);
}
.card-title {
  color: var(--zi-ink);
  font-size: 30rpx;
  font-weight: 900;
  letter-spacing: 2rpx;
}

/* 进度：竖排时间线，五个状态一行一个，窄屏也不会挤 */
.timeline {
  margin-top: 18rpx;
}
.timeline-item {
  display: flex;
  align-items: stretch;
}
.rail {
  flex: 0 0 28rpx;
  width: 28rpx;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.dot {
  flex: 0 0 auto;
  width: 18rpx;
  height: 18rpx;
  margin-top: 8rpx;
  box-sizing: border-box;
  background: var(--zi-paper);
  border: 3rpx solid var(--zi-muted);
}
.dot.on {
  background: var(--zi-accent);
  border-color: var(--zi-accent);
}
.line {
  flex: 1;
  width: 3rpx;
  min-height: 30rpx;
  margin: 6rpx 0;
  background: var(--zi-line);
}
.line.on {
  background: var(--zi-accent);
}
.step {
  flex: 1;
  padding: 0 0 26rpx 16rpx;
  color: var(--zi-muted);
  font-size: 28rpx;
  font-weight: 500;
  line-height: 1.5;
}
.step.on {
  color: var(--zi-ink);
  font-weight: 800;
}
.timeline-item:last-child .step {
  padding-bottom: 0;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  padding-bottom: 8rpx;
  gap: 16rpx;
}
.primary,
.ghost {
  flex: 1 1 44%;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 96rpx;
  margin: 0;
  padding: 0 8rpx;
  box-sizing: border-box;
  border-radius: var(--zi-button-radius);
  font-size: 30rpx;
  font-weight: 800;
  line-height: 1.2;
  letter-spacing: 2rpx;
}
.ghost.locked {
  border-style: dashed;
  border-color: var(--zi-line-strong);
  color: var(--zi-muted);
}
.primary {
  background: var(--zi-primary-bg);
  color: var(--zi-primary-ink);
  border: var(--zi-button-border);
  box-shadow: var(--zi-primary-shadow);
}
.ghost {
  background: var(--zi-ghost-bg);
  color: var(--zi-ghost-ink);
  border: var(--zi-button-border);
}

/* ---------- 方向一：信号牌 ---------- */
.zi-theme-signal .hero-slash {
  opacity: 0.95;
}
.zi-theme-signal .code-block {
  padding-bottom: 12rpx;
}

/* ---------- 方向二：纸面工单 ---------- */
/*
 * 这一块是「订单里的纸面工单」：打印出来的那张单子长什么样，深色模式下也不改，
 * 所以颜色走 --zi-print-paper / --zi-print-ink（深浅两套配色里都是同一个值）。
 */
.zi-theme-press .hero {
  border-left-width: 14rpx;
  border-left-color: var(--zi-accent);
}
.zi-theme-press .hero-slash {
  top: 0;
  right: auto;
  bottom: 0;
  left: 0;
  width: 12rpx;
  height: auto;
  transform: none;
  display: none;
}
.zi-theme-press .status {
  transform: rotate(-3deg);
  background: transparent;
  border-color: var(--zi-accent);
  color: var(--zi-accent);
}
.zi-theme-press .code-block {
  margin-top: 24rpx;
  padding: 18rpx 22rpx;
  background: var(--zi-print-paper);
  border: 4rpx solid var(--zi-print-ink);
  border-left: 14rpx solid var(--zi-accent);
}
.zi-theme-press .code-block .code {
  color: var(--zi-print-ink);
}
.zi-theme-press .code {
  font-size: 64rpx;
  letter-spacing: 1rpx;
}
.zi-theme-press .queue-row {
  padding: 14rpx 18rpx;
  border-top: none;
  background: var(--zi-accent);
}
.zi-theme-press .queue {
  color: var(--zi-accent-ink);
}

/* ---------- 旧蓝白皮肤（可选，纯色不使用渐变） ---------- */
.zi-theme-classic .hero {
  align-items: center;
  text-align: center;
}
.zi-theme-classic .status {
  align-self: center;
  padding: 0;
  border-color: transparent;
  background: transparent;
  color: var(--zi-hero-ink);
  font-size: 28rpx;
  font-weight: 400;
  letter-spacing: 0;
  opacity: 0.9;
}
.zi-theme-classic .code-block {
  align-self: center;
  margin-top: 12rpx;
  padding-bottom: 0;
  border-bottom: none;
}
.zi-theme-classic .code {
  font-size: 48rpx;
  font-weight: 700;
}
.zi-theme-classic .code-tip {
  align-self: center;
  margin-top: 6rpx;
  font-size: 24rpx;
  font-weight: 400;
  letter-spacing: 0;
  opacity: 0.85;
}
.zi-theme-classic .queue-row {
  align-self: center;
  margin-top: 20rpx;
  padding-top: 0;
  border-top: none;
}
.zi-theme-classic .queue {
  font-size: 26rpx;
  font-weight: 400;
}
.zi-theme-classic .title-mark {
  display: none;
}
.zi-theme-classic .card-title {
  font-size: 28rpx;
  font-weight: 600;
  letter-spacing: 0;
}
.zi-theme-classic .dot {
  width: 20rpx;
  height: 20rpx;
  border: none;
  border-radius: 50%;
  background: var(--zi-line);
}
.zi-theme-classic .dot.on {
  background: var(--zi-accent);
}
.zi-theme-classic .line {
  width: 2rpx;
  background: var(--zi-line);
}
.zi-theme-classic .step {
  font-size: 26rpx;
  font-weight: 400;
}
.zi-theme-classic .step.on {
  font-weight: 600;
}
.zi-theme-classic .primary,
.zi-theme-classic .ghost {
  font-size: 28rpx;
  font-weight: 600;
  letter-spacing: 0;
}
</style>
