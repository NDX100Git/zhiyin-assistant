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

    <!-- AI 密钥入口：一行说清现在用的是哪把钥匙，点开是面板 -->
    <button class="key-entry" @click="openKeyPanel">
      <view class="key-entry-text">
        <text class="key-entry-title">AI 密钥</text>
        <text class="key-entry-sub">{{ keyStatusLabel }}</text>
      </view>
      <text class="key-entry-arrow">›</text>
    </button>

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
        <button v-if="canSelfCancel(order)" class="mini" @click="cancel(order)">取消</button>
        <text v-else-if="teacherCancelOnly(order)" class="cancel-hint">
          已支付订单如需取消请联系文印室老师
        </text>
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

    <!--
      AI 密钥面板：保存 / 测试连通 / 清除并改用学校共享三个动作。
      密钥只写本机存储，面板里任何地方都不回显完整密钥（只显示前 4 后 4）。
    -->
    <view v-if="keyPanelOpen" class="theme-mask" @click="closeKeyPanel">
      <view class="theme-sheet key-sheet" @click.stop>
        <view class="theme-sheet-head">
          <text class="theme-sheet-title">AI 密钥</text>
          <button class="theme-close" @click="closeKeyPanel">关闭</button>
        </view>
        <text class="key-state" :class="{ own: keyOwn }">当前：{{ keyStatusLabel }}</text>
        <text class="key-notice">{{ keyNotice }}</text>
        <input
          v-model="keyInput"
          class="key-input"
          :password="true"
          type="text"
          maxlength="200"
          placeholder="粘贴你自己的密钥（sk-…）"
          confirm-type="done"
        />
        <text v-if="keyInputPreview" class="key-preview">将保存为：{{ keyInputPreview }}</text>
        <text v-if="keyMessage" class="key-message" :class="keyMessageKind">{{ keyMessage }}</text>
        <view class="key-actions">
          <button class="key-btn primary" :disabled="keyBusy" @click="saveKey">
            {{ keyBusy ? '处理中…' : '保存' }}
          </button>
          <button class="key-btn" :disabled="keyBusy" @click="testKey">测试连通</button>
          <button class="key-btn ghost" :disabled="keyBusy" @click="clearKey">清除并改用学校共享</button>
        </view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { onHide, onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import { computed, onUnmounted, ref } from 'vue'
import { POLL_INTERVAL, STORAGE_KEYS } from '@/config'
import { checkLlmKey, request } from '@/services/api'
import { ensureLogin, getCurrentUser } from '@/services/auth'
import {
  LLM_KEY_NOTICE,
  clearLlmKey,
  getLlmKey,
  isLlmKeyFailure,
  llmKeyFailureText,
  llmKeyStatus,
  maskLlmKey,
  saveLlmKey,
  validateLlmKey,
} from '@/services/llmKey'
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

/* ---------- AI 密钥面板：只读写本机存储，密钥不上传 ---------- */

const keyPanelOpen = ref(false)
const keyInput = ref('')
const keyBusy = ref(false)
const keyMessage = ref('')
/** 'ok' | 'error' | ''：只影响颜色，不影响文案 */
const keyMessageKind = ref('')
/** 当前用哪把钥匙。存成 ref：保存/清除后状态行要立刻变 */
const keyState = ref(llmKeyStatus())

const keyStatusLabel = computed(() => keyState.value.label)
const keyOwn = computed(() => keyState.value.mode === 'own')
const keyNotice = LLM_KEY_NOTICE

/** 粘贴后立即看到的脱敏预览：输入框是密码型，这行让学生确认没粘错一整段。 */
const keyInputPreview = computed(() => maskLlmKey(keyInput.value))

function refreshKeyState() {
  keyState.value = llmKeyStatus()
}

function openKeyPanel() {
  keyInput.value = ''
  keyMessage.value = ''
  keyMessageKind.value = ''
  refreshKeyState()
  keyPanelOpen.value = true
}

function closeKeyPanel() {
  keyInput.value = ''
  keyPanelOpen.value = false
}

/** 保存：只写本机存储，一个请求都不发。 */
function saveKey() {
  const result = saveLlmKey(keyInput.value)
  if (!result.ok) {
    keyMessage.value = result.error
    keyMessageKind.value = 'error'
    return
  }
  keyInput.value = ''
  refreshKeyState()
  keyMessage.value = `已保存到本机，之后的 AI 请求都带这把密钥（${keyState.value.masked}）`
  keyMessageKind.value = 'ok'
  notify('密钥已保存到本机', 'success')
}

/**
 * 测试连通：把输入框里的密钥（没输入就用存着的那把）发给 /api/ai/key-check。
 * 失败时把服务端给的原因原样告诉学生，并提示可以清除后改用学校共享。
 */
async function testKey() {
  const typed = keyInput.value.trim()
  const checked = validateLlmKey(typed || getLlmKey())
  if (!checked.ok) {
    keyMessage.value = checked.error
    keyMessageKind.value = 'error'
    return
  }
  keyBusy.value = true
  keyMessage.value = '正在用这把密钥做一次最小调用…'
  keyMessageKind.value = ''
  try {
    await checkLlmKey(checked.key)
    keyMessage.value = '测试通过：这把密钥可以用，AI 请求会带着它。'
    keyMessageKind.value = 'ok'
  } catch (error) {
    keyMessage.value = isLlmKeyFailure(error)
      ? llmKeyFailureText(error)
      : `测试没通过：${(error && error.message) || '未知原因'}。可以确认密钥是否复制完整，或稍后再试。`
    keyMessageKind.value = 'error'
  } finally {
    keyBusy.value = false
  }
}

/** 清除：立刻回到学校共享密钥（状态行下一帧就变，不用退出重进）。 */
function clearKey() {
  const done = clearLlmKey()
  keyInput.value = ''
  refreshKeyState()
  keyMessage.value = done ? '已清除，现在改用学校共享密钥' : '本地存储删除失败，请稍后重试'
  keyMessageKind.value = done ? 'ok' : 'error'
  notify(done ? '已改用学校共享密钥' : '清除失败，请稍后重试', done ? 'success' : 'info')
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
    // 密钥可能在「AI 密钥」面板里被改过（也可能被系统清过缓存），每次回页重读
    refreshKeyState()
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
    // 键名统一走 config/index.js 的常量：以前这里手写 'zhiyin_user'，
    // 与 services/auth.js 用的常量各写一份，改键名时两边会不同步。
    uni.setStorageSync(STORAGE_KEYS.user, user)
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

/**
 * 学生能自助取消的只有「还没付钱」的订单。
 *
 * 已支付的排队单后端会直接回 40905（钱已经收了，只能老师在后台关单并登记退款），
 * 前端再给一个“点了必然失败”的按钮，只会让学生以为系统坏了。
 */
function canSelfCancel(order) {
  if (order.status === 'pending_pay') return true
  return order.status === 'queuing' && !order.paid
}

/** 已付款且还在排队：按钮换成一句“找老师”的提示，而不是留一个点不动的取消。 */
function teacherCancelOnly(order) {
  return order.status === 'queuing' && Boolean(order.paid)
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

/* AI 密钥入口：一行点开面板，高度与齿轮对齐（390 宽下约 50px，不小于 44px） */
.key-entry {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
  width: 100%;
  min-height: 96rpx;
  margin: 0 0 20rpx;
  padding: 16rpx 24rpx;
  box-sizing: border-box;
  border: var(--zi-card-border);
  border-radius: var(--zi-radius);
  background: var(--zi-paper);
  text-align: left;
  line-height: 1.35;
}
.key-entry-text {
  flex: 1;
  min-width: 0;
}
.key-entry-title {
  display: block;
  color: var(--zi-ink);
  font-size: 28rpx;
  font-weight: 900;
  letter-spacing: 1rpx;
}
.key-entry-sub {
  display: block;
  margin-top: 4rpx;
  color: var(--zi-muted);
  font-size: 22rpx;
  font-weight: 700;
  word-break: break-all;
}
.key-entry-arrow {
  flex: 0 0 auto;
  color: var(--zi-muted);
  font-size: 34rpx;
  font-weight: 900;
  line-height: 1;
}

/*
  AI 密钥面板：三个动作竖排、每个满宽且不低于 44 像素（88rpx），
  390 宽下不会横向溢出；密钥只在输入框（密码型）里出现，别处只给脱敏值。
*/
.key-sheet {
  max-height: 86vh;
  overflow-y: auto;
}
.key-state {
  display: block;
  color: var(--zi-muted);
  font-size: 24rpx;
  font-weight: 800;
  line-height: 1.5;
  word-break: break-all;
}
.key-state.own {
  color: var(--zi-accent);
}
.key-notice {
  display: block;
  margin-top: 8rpx;
  color: var(--zi-muted);
  font-size: 22rpx;
  line-height: 1.6;
}
.key-input {
  width: 100%;
  min-height: 88rpx;
  margin-top: 16rpx;
  padding: 0 20rpx;
  box-sizing: border-box;
  border: 2rpx solid var(--zi-line-strong);
  border-radius: var(--zi-radius);
  background: var(--zi-paper);
  color: var(--zi-ink);
  font-size: 26rpx;
}
.key-preview {
  display: block;
  margin-top: 8rpx;
  color: var(--zi-muted);
  font-size: 22rpx;
  line-height: 1.5;
  word-break: break-all;
}
.key-message {
  display: block;
  margin-top: 12rpx;
  color: var(--zi-muted);
  font-size: 24rpx;
  line-height: 1.5;
}
.key-message.ok {
  color: var(--zi-ok);
}
.key-message.error {
  color: var(--zi-danger);
}
.key-actions {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
  margin-top: 20rpx;
}
.key-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  min-height: 88rpx;
  margin: 0;
  padding: 10rpx 20rpx;
  box-sizing: border-box;
  border: 2rpx solid var(--zi-line-strong);
  border-radius: var(--zi-button-radius);
  background: transparent;
  color: var(--zi-ink);
  font-size: 26rpx;
  font-weight: 800;
  line-height: 1.25;
  white-space: normal;
}
.key-btn.primary {
  border-color: var(--zi-ink);
  background: var(--zi-primary-bg);
  color: var(--zi-primary-ink);
}
.key-btn.ghost {
  color: var(--zi-muted);
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

/* 已付款排队单不给取消按钮，换成一句“找老师”的提示（字号对齐小按钮） */
.cancel-hint {
  flex: 1;
  margin-left: 12rpx;
  color: var(--zi-muted);
  font-size: 22rpx;
  line-height: 1.4;
  text-align: right;
}

/* 旧蓝白皮肤：齿轮与面板恢复轻量圆角 */
.zi-theme-classic .theme-entry {
  border: none;
  border-radius: 999rpx;
  background: var(--zi-soft);
  color: var(--zi-accent);
}
.zi-theme-classic .key-entry {
  border: none;
  background: var(--zi-soft);
}
.zi-theme-classic .key-btn {
  border: none;
  border-radius: 999rpx;
  background: var(--zi-soft);
  color: var(--zi-accent);
  font-weight: 600;
}
.zi-theme-classic .key-btn.primary {
  background: var(--zi-accent);
  color: var(--zi-accent-ink);
}
.zi-theme-classic .key-btn.ghost {
  background: var(--zi-soft-2);
  color: var(--zi-muted);
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
