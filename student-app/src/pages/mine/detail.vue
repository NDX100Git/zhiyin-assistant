<template>
  <view class="page">
    <view v-if="!order" class="card center">加载中…</view>
    <template v-else>
      <view class="card hero">
        <text class="status">{{ order.status_label }}</text>
        <text class="code">{{ order.pickup_code }}</text>
        <text class="code-tip">取件码 · 凭此码到文印室取件</text>
        <text v-if="isActive" class="queue">前面 {{ order.queue_position }} 单 · 预计 {{ order.estimated_minutes }} 分钟</text>
        <text v-else-if="order.status === 'ready'" class="queue">已打印完成，请到文印室取件</text>
        <text v-else-if="order.status === 'completed'" class="queue">已完成，感谢使用</text>
      </view>

      <view class="card">
        <view class="row"><text class="k">订单号</text><text class="v">{{ order.order_no }}</text></view>
        <view class="row"><text class="k">文件</text><text class="v">{{ order.file_name }}</text></view>
        <view class="row"><text class="k">页数</text><text class="v">{{ order.page_count }} 页</text></view>
        <view class="row"><text class="k">打印</text><text class="v">{{ order.print_side === 2 ? '双面' : '单面' }} · {{ order.color_mode === 1 ? '彩色' : '黑白' }} · {{ order.copies }} 份</text></view>
        <view class="row"><text class="k">金额</text><text class="v money">{{ formatMoney(order.amount_cents) }}{{ order.paid ? '（已支付）' : '（未支付）' }}</text></view>
        <view class="row"><text class="k">下单时间</text><text class="v">{{ order.created_at }}</text></view>
        <view v-if="order.cancel_reason" class="row"><text class="k">取消原因</text><text class="v">{{ order.cancel_reason }}</text></view>
      </view>

      <view class="card">
        <text class="card-title">进度</text>
        <view v-for="(step, index) in timeline" :key="step.key" class="timeline">
          <view class="dot" :class="{ on: step.done }"></view>
          <text class="step" :class="{ on: step.done }">{{ step.label }}</text>
          <text v-if="index < timeline.length - 1" class="line"></text>
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

const order = ref(null)
const orderId = ref('')
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
  background: #f5f7fb;
  box-sizing: border-box;
}
.card {
  margin-bottom: 20rpx;
  padding: 28rpx;
  background: #fff;
  border-radius: 24rpx;
  box-shadow: 0 8rpx 24rpx rgba(31, 45, 61, 0.06);
}
.card.center {
  text-align: center;
  color: #9aa7b5;
  font-size: 26rpx;
}
.hero {
  text-align: center;
  color: #fff;
  background: linear-gradient(135deg, #2f6bff 0%, #5b8cff 60%, #7aa2ff 100%);
}
.hero .status {
  display: block;
  font-size: 28rpx;
  opacity: 0.9;
}
.hero .code {
  display: block;
  margin: 12rpx 0 6rpx;
  font-size: 72rpx;
  font-weight: 700;
  letter-spacing: 8rpx;
}
.hero .code-tip {
  display: block;
  font-size: 22rpx;
  opacity: 0.85;
}
.hero .queue {
  display: block;
  margin-top: 20rpx;
  font-size: 26rpx;
}
.row {
  display: flex;
  padding: 12rpx 0;
  border-bottom: 2rpx solid #f2f4f8;
  font-size: 26rpx;
}
.row:last-child {
  border-bottom: none;
}
.row .k {
  width: 160rpx;
  color: #9aa7b5;
}
.row .v {
  flex: 1;
  color: #1f2d3d;
  word-break: break-all;
}
.row .money {
  color: #2f6bff;
  font-weight: 600;
}
.card-title {
  font-size: 28rpx;
  font-weight: 600;
  color: #1f2d3d;
}
.timeline {
  display: flex;
  align-items: center;
  margin-top: 18rpx;
}
.dot {
  width: 20rpx;
  height: 20rpx;
  margin-right: 16rpx;
  border-radius: 50%;
  background: #dfe5ee;
}
.dot.on {
  background: #2f6bff;
}
.step {
  font-size: 26rpx;
  color: #9aa7b5;
}
.step.on {
  color: #1f2d3d;
  font-weight: 600;
}
.line {
  flex: 1;
  height: 2rpx;
  margin-left: 16rpx;
  background: #eef1f6;
}
.actions {
  display: flex;
  gap: 16rpx;
}
.primary,
.ghost {
  flex: 1;
  margin: 0;
  border-radius: 16rpx;
  font-size: 28rpx;
}
.primary {
  color: #fff;
  background: #2f6bff;
}
.ghost {
  color: #2f6bff;
  background: #eef3ff;
}
</style>
