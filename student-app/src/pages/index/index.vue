<template>
  <view class="page">
    <view class="hero">
      <view class="hero-title">智印助手</view>
      <view class="hero-sub">AI 论文格式体检 · 自助打印</view>
      <view class="hero-row">
        <view class="hero-item">
          <text class="num">{{ summary.waiting_count }}</text>
          <text class="label">排队中</text>
        </view>
        <view class="hero-item">
          <text class="num">{{ summary.estimated_minutes }}</text>
          <text class="label">预计等待（分钟）</text>
        </view>
        <view class="hero-item">
          <text class="num">{{ summary.ready_count }}</text>
          <text class="label">我的待取件</text>
        </view>
      </view>
    </view>

    <view class="card" :class="{ closed: !session.is_open }">
      <view class="row between">
        <text class="card-title">{{ session.is_open ? '当前可排单' : '当前已截单' }}</text>
        <text class="badge">{{ session.label || '非收单时段' }}</text>
      </view>
      <text v-if="session.hint" class="hint">{{ session.hint }}</text>
      <text v-else class="hint ok">现在下单会排入当前打印时段。</text>
    </view>

    <view class="card">
      <view class="card-title">文印室公告</view>
      <view v-if="notices.length === 0" class="empty">暂无公告</view>
      <view v-for="notice in notices" :key="notice.id" class="notice">
        <text class="dot">•</text>
        <text class="notice-text">{{ notice.content }}</text>
      </view>
    </view>

    <button class="primary block" @click="goOrder">上传论文 · AI 体检</button>
    <view class="footnote">仅用取件码作为取件凭据，不收集学号等敏感信息。</view>
  </view>
</template>

<script setup>
import { onHide, onShow } from '@dcloudio/uni-app'
import { onUnmounted, reactive, ref } from 'vue'
import { POLL_INTERVAL } from '@/config'
import { request } from '@/services/api'
import { ensureLogin } from '@/services/auth'
import { createPoller, notifyError } from '@/services/notify'

const summary = reactive({
  waiting_count: 0,
  estimated_minutes: 0,
  ready_count: 0,
})
const session = reactive({
  is_open: false,
  label: '',
  hint: '',
  can_order: true,
})
const notices = ref([])

async function load() {
  const data = await request('/api/home/summary')
  Object.assign(summary, {
    waiting_count: data.waiting_count,
    estimated_minutes: data.estimated_minutes,
    ready_count: data.ready_count,
  })
  Object.assign(session, data.order_session || {})

  const noticeData = await request('/api/notice/list?limit=5')
  notices.value = noticeData.items || []
}

const poller = createPoller(load, POLL_INTERVAL)

onShow(async () => {
  try {
    await ensureLogin()
    poller.start()
  } catch (error) {
    notifyError(error)
  }
})
onHide(() => poller.stop())
onUnmounted(() => poller.stop())

function goOrder() {
  uni.switchTab({ url: '/pages/order/order' })
}
</script>

<style scoped>
.page {
  min-height: 100vh;
  padding: 24rpx 24rpx 160rpx;
  background: #f5f7fb;
  box-sizing: border-box;
}
.hero {
  padding: 40rpx 32rpx;
  border-radius: 28rpx;
  color: #fff;
  background: linear-gradient(135deg, #2f6bff 0%, #5b8cff 55%, #7aa2ff 100%);
  box-shadow: 0 16rpx 40rpx rgba(47, 107, 255, 0.25);
}
.hero-title {
  font-size: 44rpx;
  font-weight: 700;
}
.hero-sub {
  margin-top: 8rpx;
  font-size: 26rpx;
  opacity: 0.9;
}
.hero-row {
  display: flex;
  justify-content: space-between;
  margin-top: 36rpx;
}
.hero-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 1;
}
.hero-item .num {
  font-size: 42rpx;
  font-weight: 700;
}
.hero-item .label {
  margin-top: 4rpx;
  font-size: 22rpx;
  opacity: 0.85;
}
.card {
  margin-top: 24rpx;
  padding: 28rpx;
  background: #fff;
  border-radius: 24rpx;
  box-shadow: 0 8rpx 24rpx rgba(31, 45, 61, 0.06);
}
.card.closed {
  border: 2rpx solid #ffd8a8;
  background: #fffaf0;
}
.row {
  display: flex;
  align-items: center;
}
.between {
  justify-content: space-between;
}
.card-title {
  font-size: 30rpx;
  font-weight: 600;
  color: #1f2d3d;
}
.badge {
  padding: 6rpx 16rpx;
  border-radius: 999rpx;
  font-size: 22rpx;
  color: #2f6bff;
  background: #eef3ff;
}
.hint {
  display: block;
  margin-top: 16rpx;
  font-size: 26rpx;
  line-height: 1.6;
  color: #b25e09;
}
.hint.ok {
  color: #3f8f5c;
}
.notice {
  display: flex;
  margin-top: 16rpx;
}
.dot {
  margin-right: 12rpx;
  color: #2f6bff;
}
.notice-text {
  flex: 1;
  font-size: 26rpx;
  line-height: 1.6;
  color: #4b5a6b;
}
.empty {
  margin-top: 16rpx;
  font-size: 26rpx;
  color: #9aa7b5;
}
.primary {
  margin-top: 32rpx;
  border-radius: 18rpx;
  background: #2f6bff;
  color: #fff;
  font-size: 30rpx;
}
.block {
  width: 100%;
}
.footnote {
  margin: 24rpx 8rpx 60rpx;
  font-size: 22rpx;
  color: #9aa7b5;
  text-align: center;
}
</style>
