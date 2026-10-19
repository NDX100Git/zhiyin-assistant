<template>
  <view class="page" :class="uiThemeClass">
    <view class="hero">
      <view class="hero-title">智印助手</view>
      <view class="hero-sub">自助打印 · AI 论文体检可选</view>
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

    <!-- 体检是可选步骤：入口不能再只写 AI，跳过体检一样能直接下单 -->
    <button class="primary block" @click="goOrder">上传文件，跳过体检直接下单</button>
    <button class="ghost block" @click="goOrder">先做 AI 体检（可选）</button>
    <view class="footnote">AI 体检是可选项，跳过它不影响下单、排队与打印。</view>
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
import { useUiTheme } from '@/ui/theme'

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
const { uiThemeClass } = useUiTheme()

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
  box-sizing: border-box;
  background-color: var(--zi-bg);
  background-image: var(--zi-bg-image);
  color: var(--zi-ink);
}

.hero {
  position: relative;
  overflow: hidden;
  padding: 36rpx 32rpx 32rpx;
  background: var(--zi-hero-bg);
  color: var(--zi-hero-ink);
  border: var(--zi-hero-border);
  border-left-width: 14rpx;
  border-left-color: var(--zi-accent);
  border-radius: var(--zi-radius);
  box-shadow: var(--zi-hero-shadow);
}
.hero-title {
  font-size: 46rpx;
  font-weight: 900;
  letter-spacing: 2rpx;
  line-height: 1.2;
}
.hero-sub {
  margin-top: 10rpx;
  color: var(--zi-hero-muted);
  font-size: 26rpx;
  font-weight: 600;
  line-height: 1.5;
}
.hero-row {
  display: flex;
  justify-content: space-between;
  margin-top: 32rpx;
  padding-top: 24rpx;
  border-top: 2rpx solid var(--zi-hero-line);
}
.hero-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  flex: 1;
  padding: 0 18rpx;
}
.hero-item:first-child {
  padding-left: 0;
}
.hero-item + .hero-item {
  border-left: 2rpx solid var(--zi-hero-line);
}
.hero-item .num {
  color: var(--zi-hero-ink);
  font-size: 52rpx;
  font-weight: 900;
  line-height: 1.05;
  letter-spacing: 1rpx;
}
.hero-item .label {
  margin-top: 8rpx;
  color: var(--zi-hero-muted);
  font-size: 22rpx;
  font-weight: 600;
  line-height: 1.4;
}

.card {
  position: relative;
  margin-top: 24rpx;
  padding: 28rpx;
  background: var(--zi-paper);
  border: var(--zi-card-border);
  border-radius: var(--zi-radius);
  box-shadow: var(--zi-card-shadow);
}
.card.closed {
  border-color: var(--zi-warn);
  background: var(--zi-warn-bg);
}
.row {
  display: flex;
  align-items: center;
}
.between {
  justify-content: space-between;
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
.badge {
  padding: 6rpx 16rpx;
  border: 2rpx solid var(--zi-accent);
  border-radius: var(--zi-pill-radius);
  color: var(--zi-accent);
  background: transparent;
  font-size: 22rpx;
  font-weight: 800;
  line-height: 1.3;
}
.hint {
  display: block;
  margin-top: 16rpx;
  padding-left: 16rpx;
  border-left: 6rpx solid var(--zi-warn);
  color: var(--zi-warn);
  font-size: 26rpx;
  font-weight: 600;
  line-height: 1.6;
}
.hint.ok {
  border-left-color: var(--zi-ok);
  color: var(--zi-ok);
}
.notice {
  display: flex;
  margin-top: 16rpx;
  padding-bottom: 14rpx;
  border-bottom: 2rpx dashed var(--zi-line);
}
.notice:last-child {
  padding-bottom: 0;
  border-bottom: none;
}
.dot {
  margin-right: 12rpx;
  color: var(--zi-accent);
  font-weight: 900;
  line-height: 1.6;
}
.notice-text {
  flex: 1;
  color: var(--zi-ink);
  font-size: 26rpx;
  font-weight: 500;
  line-height: 1.6;
}
.empty {
  margin-top: 16rpx;
  color: var(--zi-muted);
  font-size: 26rpx;
}
.primary {
  width: 100%;
  height: 96rpx;
  margin-top: 32rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border: var(--zi-button-border);
  border-radius: var(--zi-button-radius);
  background: var(--zi-primary-bg);
  color: var(--zi-primary-ink);
  box-shadow: var(--zi-primary-shadow);
  font-size: 30rpx;
  font-weight: 800;
  letter-spacing: 2rpx;
}
.block {
  width: 100%;
}
/* 次要入口：AI 体检放在这里，主入口是直接下单 */
.ghost {
  width: 100%;
  height: 88rpx;
  margin-top: 16rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2rpx dashed var(--zi-line-strong);
  border-radius: var(--zi-button-radius);
  background: transparent;
  color: var(--zi-muted);
  font-size: 26rpx;
  font-weight: 800;
  line-height: 1;
}
.footnote {
  margin: 24rpx 8rpx 60rpx;
  color: var(--zi-muted);
  font-size: 22rpx;
  text-align: center;
}

/* 旧蓝白皮肤：保留原来的轻量圆角观感 */
.zi-theme-classic .hero {
  border-left-width: 0;
}
.zi-theme-classic .hero-title {
  font-size: 44rpx;
  font-weight: 700;
  letter-spacing: 0;
}
.zi-theme-classic .hero-sub {
  font-weight: 400;
}
.zi-theme-classic .hero-item {
  align-items: center;
  padding: 0;
}
.zi-theme-classic .hero-item + .hero-item {
  border-left: none;
}
.zi-theme-classic .hero-item .num {
  font-size: 42rpx;
  font-weight: 700;
}
.zi-theme-classic .hero-item .label {
  font-weight: 400;
}
.zi-theme-classic .card-title {
  padding-left: 0;
  border-left: none;
  font-weight: 600;
  letter-spacing: 0;
}
.zi-theme-classic .card.closed {
  border: 2rpx solid #ffd8a8;
}
.zi-theme-classic .badge {
  border: none;
  background: var(--zi-soft);
}
.zi-theme-classic .hint {
  padding-left: 0;
  border-left: none;
  font-weight: 400;
}
.zi-theme-classic .notice {
  padding-bottom: 0;
  border-bottom: none;
}
.zi-theme-classic .primary {
  font-weight: 600;
  letter-spacing: 0;
}
</style>
