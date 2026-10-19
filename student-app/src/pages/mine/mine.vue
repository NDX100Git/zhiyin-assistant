<template>
  <view class="page" :class="[uiThemeClass, uiSchemeClass]">
    <!-- #ifdef H5 -->
    <view class="pull-refresh" :style="{ height: pullOffset + 'px' }">
      <text class="pull-refresh-text">
        {{ pullRefreshing ? '正在刷新…' : (pullOffset >= pullThreshold ? '松开刷新' : '下拉刷新') }}
      </text>
    </view>
    <!-- #endif -->
    <!-- 页头：标题左，齿轮右。齿轮固定 88rpx 见方，窄屏下标题自己省略，不挤压 -->
    <view class="page-head">
      <view class="head-text">
        <text class="head-title">我的</text>
        <text class="head-sub">订单与取件</text>
      </view>
      <button class="theme-entry" aria-label="界面皮肤" @click="openThemePanel">
        <text class="theme-entry-icon">⚙</text>
        <text class="theme-entry-label">皮肤</text>
      </button>
    </view>

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
        <text class="file">{{ fileLine(order) }}</text>
        <text class="amount">{{ formatMoney(order.amount_cents) }}</text>
      </view>
      <view class="order-meta">
        <text class="pickup">取件码 {{ order.pickup_code }}</text>
        <text v-if="isActive(order)">前面 {{ order.queue_position }} 单 · 约 {{ order.estimated_minutes }} 分钟</text>
        <text v-else>{{ order.created_at }}</text>
      </view>
      <view class="order-actions" @click.stop>
        <button v-if="order.status === 'pending_pay'" class="mini primary" @click="pay(order)">模拟支付</button>
        <button v-if="order.status === 'pending_pay' || order.status === 'queuing'" class="mini" @click="cancel(order)">取消</button>
        <button class="mini ghost" @click="openDetail(order.id)">详情</button>
      </view>
    </view>

    <!-- 皮肤面板：三选一，点一下当前页面立刻换肤，退出重进也记得 -->
    <view v-if="themePanelOpen" class="theme-mask" @click="closeThemePanel">
      <view class="theme-sheet" @click.stop>
        <view class="theme-sheet-head">
          <text class="theme-sheet-title">界面皮肤</text>
          <button class="theme-close" @click="closeThemePanel">关闭</button>
        </view>
        <button
          v-for="option in themeOptions"
          :key="option.value"
          class="theme-option"
          :class="{ active: uiTheme === option.value }"
          :data-theme="option.value"
          @click="chooseTheme(option.value)"
        >
          <text class="theme-name">{{ option.label }}</text>
          <text class="theme-hint">{{ option.hint }}</text>
          <text v-if="uiTheme === option.value" class="theme-check">当前</text>
        </button>
      </view>
    </view>
  </view>
</template>

<script setup>
import { onHide, onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import { computed, onUnmounted, ref } from 'vue'
import { POLL_INTERVAL } from '@/config'
import { request } from '@/services/api'
import { ensureLogin, getCurrentUser } from '@/services/auth'
import { createPoller, notify, notifyError } from '@/services/notify'
import { fileCount, fileSummary } from '@/services/orderFiles'
import { usePullRefresh } from '@/ui/pullRefresh'
import { UI_THEME_OPTIONS, useUiTheme } from '@/ui/theme'

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
const { uiTheme, uiThemeClass, uiSchemeClass, changeUiTheme } = useUiTheme()

/** 皮肤面板的三选一选项，选中项在面板里会标出来。 */
const themeOptions = UI_THEME_OPTIONS
const themePanelOpen = ref(false)

function openThemePanel() {
  themePanelOpen.value = true
}

function closeThemePanel() {
  themePanelOpen.value = false
}

/** 选皮肤：写存储 + 当前页面立即换肤，不需要手动刷新。 */
function chooseTheme(theme) {
  const ok = changeUiTheme(theme)
  if (!ok) {
    notify('界面皮肤切换失败，请稍后重试')
    return
  }
  themePanelOpen.value = false
  notify('界面皮肤已切换', 'success')
}

function isActive(order) {
  return order.status === 'queuing' || order.status === 'printing'
}

function formatMoney(cents) {
  return `¥${((Number(cents) || 0) / 100).toFixed(2)}`
}

/** 列表一行说清这份单要打什么：单文件给文件名，多文件给「N 个文件 · 共 X 页」。 */
function fileLine(order) {
  if (fileCount(order) > 1) return fileSummary(order)
  return order.file_name || '论文文档'
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

/**
 * 轮询改成 30 秒后，下拉刷新是学生主动查看最新状态的主路径。
 * 小程序端由页面配置派发 onPullDownRefresh，H5 端由 usePullRefresh 的手势兜底。
 */
async function refreshOrders() {
  try {
    await ensureLogin()
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    uni.stopPullDownRefresh()
  }
}

onPullDownRefresh(refreshOrders)

const {
  offset: pullOffset,
  refreshing: pullRefreshing,
  threshold: pullThreshold,
} = usePullRefresh(refreshOrders)

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
  box-sizing: border-box;
  background-color: var(--zi-bg);
  background-image: var(--zi-bg-image);
  color: var(--zi-ink);
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

/* 页头：标题可省略，齿轮按钮固定 88rpx（390 宽下约 45.8px）不挤压标题 */
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
  margin-bottom: 20rpx;
}
.head-text {
  flex: 1;
  min-width: 0;
}
.head-title {
  display: block;
  overflow: hidden;
  color: var(--zi-ink);
  font-size: 38rpx;
  font-weight: 900;
  letter-spacing: 2rpx;
  line-height: 1.25;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.head-sub {
  display: block;
  margin-top: 2rpx;
  color: var(--zi-muted);
  font-size: 22rpx;
  font-weight: 700;
  letter-spacing: 1rpx;
  white-space: nowrap;
}
.theme-entry {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  gap: 6rpx;
  width: 88rpx;
  height: 88rpx;
  margin: 0;
  padding: 0;
  border: var(--zi-button-border);
  border-radius: var(--zi-button-radius);
  background: var(--zi-paper);
  color: var(--zi-ink);
  line-height: 1;
}
.theme-entry-icon {
  font-size: 38rpx;
  line-height: 1;
}
.theme-entry-label {
  display: none;
  font-size: 22rpx;
  font-weight: 800;
}

/* 皮肤面板：底部弹出，三选一，每项都高于 44 像素 */
.theme-mask {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  left: 0;
  /* 高于 uni H5 的 tabBar（z-index 998），面板整个铺满底部 */
  z-index: 999;
  display: flex;
  align-items: flex-end;
  background: var(--zi-overlay);
}
.theme-sheet {
  width: 100%;
  padding: 24rpx 24rpx calc(40rpx + env(safe-area-inset-bottom));
  box-sizing: border-box;
  border-top: var(--zi-card-border);
  background: var(--zi-paper);
  color: var(--zi-ink);
}
.theme-sheet-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
  margin-bottom: 16rpx;
}
.theme-sheet-title {
  color: var(--zi-ink);
  font-size: 30rpx;
  font-weight: 900;
  letter-spacing: 2rpx;
}
.theme-close {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 88rpx;
  margin: 0;
  padding: 0 24rpx;
  border: 2rpx solid var(--zi-line-strong);
  border-radius: var(--zi-pill-radius);
  background: transparent;
  color: var(--zi-muted);
  font-size: 24rpx;
  font-weight: 800;
  line-height: 1;
}
.theme-option {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4rpx 12rpx;
  width: 100%;
  min-height: 100rpx;
  margin: 0 0 12rpx;
  padding: 16rpx 20rpx;
  box-sizing: border-box;
  border: 2rpx solid var(--zi-line-strong);
  border-radius: var(--zi-radius);
  background: transparent;
  text-align: left;
  line-height: 1.35;
}
.theme-option.active {
  border-color: var(--zi-accent);
  background: var(--zi-soft);
}
.theme-name {
  color: var(--zi-ink);
  font-size: 28rpx;
  font-weight: 900;
  letter-spacing: 1rpx;
}
.theme-hint {
  flex: 1;
  min-width: 0;
  color: var(--zi-muted);
  font-size: 22rpx;
  font-weight: 700;
}
.theme-check {
  padding: 2rpx 12rpx;
  border: 2rpx solid var(--zi-accent);
  border-radius: var(--zi-pill-radius);
  color: var(--zi-accent);
  font-size: 20rpx;
  font-weight: 900;
  line-height: 1.4;
}

.card {
  margin-bottom: 20rpx;
  padding: 26rpx;
  background: var(--zi-paper);
  border: var(--zi-card-border);
  border-radius: var(--zi-radius);
  box-shadow: var(--zi-card-shadow);
}
.card.center {
  text-align: center;
  color: var(--zi-muted);
  font-size: 26rpx;
}

.profile {
  display: flex;
  align-items: center;
}
.avatar {
  width: 88rpx;
  height: 88rpx;
  border: 3rpx solid var(--zi-ink);
  border-radius: var(--zi-radius);
  background: var(--zi-ink);
  color: var(--zi-paper);
  text-align: center;
  line-height: 82rpx;
  font-size: 36rpx;
  font-weight: 900;
}
.profile-main {
  flex: 1;
  min-width: 0;
  margin: 0 20rpx;
}
.nickname {
  font-size: 30rpx;
  font-weight: 800;
  color: var(--zi-ink);
}
.device {
  display: block;
  margin-top: 6rpx;
  color: var(--zi-muted);
  font-size: 22rpx;
  line-height: 1.5;
}
.save {
  height: 60rpx;
  margin: 0;
  padding: 0 22rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border: var(--zi-button-border);
  border-radius: var(--zi-button-radius);
  background: transparent;
  color: var(--zi-primary-bg);
  font-size: 24rpx;
  font-weight: 800;
  line-height: 1;
}

.tabs {
  white-space: nowrap;
  margin-bottom: 16rpx;
}
.tab {
  display: inline-block;
  margin-right: 16rpx;
  padding: 10rpx 24rpx;
  border: 2rpx solid var(--zi-line-strong);
  border-radius: var(--zi-pill-radius);
  color: var(--zi-muted);
  background: transparent;
  font-size: 24rpx;
  font-weight: 700;
}
.tab.active {
  border-color: var(--zi-primary-bg);
  background: var(--zi-primary-bg);
  color: var(--zi-primary-ink);
}

.order {
  border-left: 8rpx solid var(--zi-accent);
}
.order-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.order-no {
  color: var(--zi-ink);
  font-size: 28rpx;
  font-weight: 800;
  letter-spacing: 1rpx;
}
.status {
  padding: 4rpx 14rpx;
  border: 2rpx solid var(--zi-line-strong);
  border-radius: var(--zi-pill-radius);
  background: transparent;
  color: var(--zi-muted);
  font-size: 22rpx;
  font-weight: 800;
  line-height: 1.3;
}
.status.pending_pay,
.status.queuing,
.status.printing {
  border-color: var(--zi-accent);
  color: var(--zi-accent);
}
.status.ready {
  border-color: var(--zi-warn);
  color: var(--zi-warn);
}
.status.completed {
  border-color: var(--zi-ok);
  color: var(--zi-ok);
}
.status.abnormal {
  border-color: var(--zi-danger);
  color: var(--zi-danger);
}
.order-body {
  display: flex;
  justify-content: space-between;
  margin-top: 14rpx;
}
.file {
  flex: 1;
  min-width: 0;
  color: var(--zi-ink);
  font-size: 26rpx;
  line-height: 1.5;
  word-break: break-all;
}
.amount {
  margin-left: 16rpx;
  color: var(--zi-accent);
  font-size: 30rpx;
  font-weight: 900;
}
.order-meta {
  display: flex;
  justify-content: space-between;
  gap: 12rpx;
  margin-top: 12rpx;
  color: var(--zi-muted);
  font-size: 24rpx;
  line-height: 1.5;
}
.pickup {
  color: var(--zi-ink);
  font-size: 26rpx;
  font-weight: 700;
}
.order-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 16rpx;
}
.mini {
  height: 60rpx;
  margin: 0 0 0 12rpx;
  padding: 0 20rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2rpx solid var(--zi-line-strong);
  border-radius: var(--zi-pill-radius);
  background: transparent;
  color: var(--zi-ink);
  font-size: 22rpx;
  font-weight: 800;
  line-height: 1;
}
.mini.primary {
  border-color: var(--zi-ink);
  background: var(--zi-primary-bg);
  color: var(--zi-primary-ink);
}
.mini.ghost {
  border-color: var(--zi-line-strong);
  background: transparent;
  color: var(--zi-muted);
}

/* 旧蓝白皮肤：齿轮与面板恢复轻量圆角 */
.zi-theme-classic .theme-entry {
  border: none;
  border-radius: 999rpx;
  background: var(--zi-soft);
  color: var(--zi-accent);
}
.zi-theme-classic .theme-close {
  border: none;
  background: var(--zi-soft);
  color: var(--zi-accent);
  font-weight: 600;
}
.zi-theme-classic .theme-option {
  border: none;
  border-radius: 16rpx;
  background: var(--zi-soft-2);
}
.zi-theme-classic .theme-option.active {
  background: var(--zi-soft);
}
.zi-theme-classic .theme-name,
.zi-theme-classic .head-title {
  font-weight: 600;
  letter-spacing: 0;
}

/* 旧蓝白皮肤：恢复轻量圆角和填充标签 */
.zi-theme-classic .avatar {
  border: none;
  border-radius: 50%;
  background: var(--zi-soft);
  color: var(--zi-accent);
  line-height: 88rpx;
}
.zi-theme-classic .save {
  border: none;
  border-radius: 999rpx;
  background: var(--zi-soft);
  color: var(--zi-accent);
  font-weight: 600;
}
.zi-theme-classic .tab {
  border: none;
  background: var(--zi-soft);
  font-weight: 400;
}
.zi-theme-classic .tab.active {
  background: var(--zi-accent);
  color: var(--zi-accent-ink);
}
.zi-theme-classic .order {
  border-left: none;
}
.zi-theme-classic .status {
  border: none;
  background: var(--zi-soft);
  font-weight: 400;
}
.zi-theme-classic .status.pending_pay,
.zi-theme-classic .status.queuing,
.zi-theme-classic .status.printing {
  background: var(--zi-soft);
  color: var(--zi-accent);
}
.zi-theme-classic .status.ready {
  background: var(--zi-warn-bg);
  color: var(--zi-warn);
}
.zi-theme-classic .status.completed {
  background: var(--zi-ok-bg);
  color: var(--zi-ok);
}
.zi-theme-classic .status.abnormal {
  background: var(--zi-danger-bg);
  color: var(--zi-danger);
}
.zi-theme-classic .amount {
  color: var(--zi-ink);
  font-weight: 600;
}
.zi-theme-classic .mini {
  border: none;
  background: var(--zi-soft);
  color: var(--zi-accent);
  font-weight: 400;
}
.zi-theme-classic .mini.primary {
  background: var(--zi-accent);
  color: var(--zi-accent-ink);
}
.zi-theme-classic .mini.ghost {
  background: var(--zi-soft-2);
  color: var(--zi-muted);
}
</style>
