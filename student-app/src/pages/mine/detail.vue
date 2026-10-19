<template>
  <view class="page" :class="uiThemeClass">
    <view v-if="!order" class="card loading">加载中…</view>
    <template v-else>
      <view class="hero">
        <view class="hero-slash"></view>
        <view class="status"><text class="status-text">{{ order.status_label }}</text></view>
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
        <view class="row"><text class="k">文件</text><text class="v">{{ order.file_name || '论文文档' }}</text></view>
        <view class="row"><text class="k">页数</text><text class="v">{{ order.page_count }} 页</text></view>
        <view class="row"><text class="k">打印</text><text class="v">{{ order.print_side === 2 ? '双面' : '单面' }} · {{ order.color_mode === 1 ? '彩色' : '黑白' }} · {{ order.copies }} 份</text></view>
        <view class="row"><text class="k">金额</text><text class="v money">{{ formatMoney(order.amount_cents) }}{{ order.paid ? '（已支付）' : '（未支付）' }}</text></view>
        <view class="row"><text class="k">下单时间</text><text class="v">{{ formatTime(order.created_at) }}</text></view>
        <view v-if="order.cancel_reason" class="row"><text class="k">取消原因</text><text class="v">{{ order.cancel_reason }}</text></view>
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
        <button class="ghost" @click="goOrder">再来一单</button>
      </view>
    </template>
  </view>
</template>

<script setup>
import { onHide, onLoad, onShow } from '@dcloudio/uni-app'
import { computed, onUnmounted, ref } from 'vue'
import { POLL_INTERVAL } from '@/config'
import { request } from '@/services/api'
import { ensureLogin } from '@/services/auth'
import { createPoller, notify, notifyError } from '@/services/notify'
import { useUiTheme } from '@/ui/theme'

const order = ref(null)
const orderId = ref('')
const { uiThemeClass } = useUiTheme()
let visible = false

const isActive = computed(() => order.value && ['queuing', 'printing'].includes(order.value.status))

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
  padding-bottom: 8rpx;
  gap: 16rpx;
}
.primary,
.ghost {
  flex: 1;
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
  border: 4rpx solid var(--zi-ink);
  border-left: 14rpx solid var(--zi-accent);
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
  background: #dfe5ee;
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
