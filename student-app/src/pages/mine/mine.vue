<template>
  <view class="page">
    <view class="card profile">
      <view class="avatar">{{ initial }}</view>
      <view class="profile-main">
        <input v-model="nickname" class="nickname" maxlength="64" placeholder="设置昵称（非真实姓名）" />
        <text class="device">仅使用设备标识与取件码，不收集学号信息</text>
      </view>
      <button class="save" :disabled="saving" @click="saveNickname">保存</button>
    </view>

    <scroll-view scroll-x class="tabs">
      <view
        v-for="tab in tabs"
        :key="tab.value"
        class="tab"
        :class="{ active: status === tab.value }"
        @click="selectTab(tab.value)"
      >
        {{ tab.label }}
      </view>
    </scroll-view>

    <view v-if="loading && orders.length === 0" class="card center">加载中…</view>
    <view v-else-if="orders.length === 0" class="card center empty">还没有订单，去「下单」页上传论文试试</view>

    <view v-for="order in orders" :key="order.id" class="card order" @click="openDetail(order.id)">
      <view class="order-head">
        <text class="order-no">{{ order.order_no }}</text>
        <text class="status" :class="order.status">{{ order.status_label }}</text>
      </view>
      <view class="order-body">
        <text class="file">{{ order.file_name || '论文文档' }}</text>
        <text class="amount">{{ formatMoney(order.amount_cents) }}</text>
      </view>
      <view class="order-meta">
        <text>取件码 {{ order.pickup_code }}</text>
        <text v-if="isActive(order)">前面 {{ order.queue_position }} 单 · 约 {{ order.estimated_minutes }} 分钟</text>
        <text v-else>{{ order.created_at }}</text>
      </view>
      <view class="order-actions" @click.stop>
        <button v-if="order.status === 'pending_pay'" class="mini primary" @click="pay(order)">模拟支付</button>
        <button v-if="order.status === 'pending_pay' || order.status === 'queuing'" class="mini" @click="cancel(order)">取消</button>
        <button class="mini ghost" @click="openDetail(order.id)">详情</button>
      </view>
    </view>
  </view>
</template>

<script setup>
import { onHide, onShow } from '@dcloudio/uni-app'
import { computed, onUnmounted, ref } from 'vue'
import { POLL_INTERVAL } from '@/config'
import { request } from '@/services/api'
import { ensureLogin, getCurrentUser } from '@/services/auth'
import { createPoller, notify, notifyError } from '@/services/notify'

const tabs = [
  { label: '全部', value: '' },
  { label: '待支付', value: 'pending_pay' },
  { label: '排队中', value: 'queuing' },
  { label: '待取件', value: 'ready' },
  { label: '已完成', value: 'completed' },
]

const status = ref('')
const orders = ref([])
const loading = ref(false)
const saving = ref(false)
const nickname = ref('')

const initial = computed(() => (nickname.value || '同').slice(0, 1))

function isActive(order) {
  return order.status === 'queuing' || order.status === 'printing'
}

function formatMoney(cents) {
  return `¥${((Number(cents) || 0) / 100).toFixed(2)}`
}

async function load() {
  loading.value = true
  try {
    const query = status.value ? `?status=${encodeURIComponent(status.value)}` : ''
    const data = await request(`/api/order/list${query}`)
    orders.value = data.items || []
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

const poller = createPoller(load, POLL_INTERVAL)

onShow(async () => {
  try {
    await ensureLogin()
    const user = getCurrentUser()
    nickname.value = (user && user.nickname) || ''
    poller.start()
  } catch (error) {
    notifyError(error)
  }
})
onHide(() => poller.stop())
onUnmounted(() => poller.stop())

function selectTab(value) {
  status.value = value
  load()
}

async function saveNickname() {
  const value = nickname.value.trim()
  if (!value) {
    notify('昵称不能为空')
    return
  }
  saving.value = true
  try {
    const user = await request('/api/auth/profile', { method: 'POST', data: { nickname: value } })
    uni.setStorageSync('zhiyin_user', user)
    notify('昵称已保存', 'success')
  } catch (error) {
    notifyError(error)
  } finally {
    saving.value = false
  }
}

async function pay(order) {
  try {
    await request(`/api/order/${order.id}/pay`, { method: 'POST' })
    notify('支付成功，已进入队列', 'success')
    poller.refresh()
  } catch (error) {
    notifyError(error)
  }
}

function cancel(order) {
  uni.showModal({
    title: '取消订单',
    content: `确认取消订单 ${order.order_no} 吗？`,
    success: async (result) => {
      if (!result.confirm) return
      try {
        await request(`/api/order/${order.id}/cancel`, {
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

function openDetail(id) {
  uni.navigateTo({ url: `/pages/mine/detail?id=${id}` })
}
</script>

<style scoped>
.page {
  min-height: 100vh;
  padding: 24rpx 24rpx 160rpx;
  background: #f5f7fb;
  box-sizing: border-box;
}
.card {
  margin-bottom: 20rpx;
  padding: 26rpx;
  background: #fff;
  border-radius: 24rpx;
  box-shadow: 0 8rpx 24rpx rgba(31, 45, 61, 0.06);
}
.card.center {
  text-align: center;
  color: #9aa7b5;
  font-size: 26rpx;
}
.profile {
  display: flex;
  align-items: center;
}
.avatar {
  width: 88rpx;
  height: 88rpx;
  border-radius: 50%;
  background: #eef3ff;
  color: #2f6bff;
  text-align: center;
  line-height: 88rpx;
  font-size: 36rpx;
  font-weight: 700;
}
.profile-main {
  flex: 1;
  margin: 0 20rpx;
}
.nickname {
  font-size: 30rpx;
  color: #1f2d3d;
}
.device {
  display: block;
  margin-top: 6rpx;
  font-size: 22rpx;
  color: #9aa7b5;
}
.save {
  margin: 0;
  padding: 0 22rpx;
  height: 60rpx;
  line-height: 60rpx;
  border-radius: 999rpx;
  font-size: 24rpx;
  color: #2f6bff;
  background: #eef3ff;
}
.tabs {
  white-space: nowrap;
  margin-bottom: 16rpx;
}
.tab {
  display: inline-block;
  margin-right: 16rpx;
  padding: 10rpx 24rpx;
  border-radius: 999rpx;
  font-size: 24rpx;
  color: #6b7a90;
  background: #eef1f6;
}
.tab.active {
  color: #fff;
  background: #2f6bff;
}
.order-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.order-no {
  font-size: 28rpx;
  font-weight: 600;
  color: #1f2d3d;
}
.status {
  padding: 4rpx 16rpx;
  border-radius: 999rpx;
  font-size: 22rpx;
  background: #eef1f6;
  color: #6b7a90;
}
.status.queuing,
.status.printing {
  color: #2f6bff;
  background: #eef3ff;
}
.status.ready {
  color: #b25e09;
  background: #fff4e6;
}
.status.completed {
  color: #3f8f5c;
  background: #eaf7ef;
}
.status.abnormal {
  color: #c0392b;
  background: #fdecea;
}
.order-body {
  display: flex;
  justify-content: space-between;
  margin-top: 14rpx;
}
.file {
  flex: 1;
  font-size: 26rpx;
  color: #4b5a6b;
  word-break: break-all;
}
.amount {
  margin-left: 16rpx;
  font-size: 28rpx;
  font-weight: 600;
  color: #1f2d3d;
}
.order-meta {
  display: flex;
  justify-content: space-between;
  margin-top: 12rpx;
  font-size: 22rpx;
  color: #9aa7b5;
}
.order-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 16rpx;
}
.mini {
  margin: 0 0 0 12rpx;
  padding: 0 22rpx;
  height: 56rpx;
  line-height: 56rpx;
  border-radius: 999rpx;
  font-size: 22rpx;
  color: #2f6bff;
  background: #eef3ff;
}
.mini.primary {
  color: #fff;
  background: #2f6bff;
}
.mini.ghost {
  color: #6b7a90;
  background: #f2f4f8;
}
</style>
