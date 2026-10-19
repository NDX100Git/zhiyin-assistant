<template>
  <view class="page" :class="uiThemeClass">
    <view v-if="!order" class="card center loading">加载中…</view>

    <template v-else>
      <view class="card">
        <view class="card-head">
          <text class="card-title">当前订单</text>
          <text class="tag">{{ order.status_label }}</text>
        </view>
        <view class="row"><text class="k">订单号</text><text class="v">{{ order.order_no }}</text></view>
        <view class="row"><text class="k">取件码</text><text class="v">{{ order.pickup_code }} · 换文件后不变</text></view>
        <view class="row"><text class="k">当前文件</text><text class="v">{{ order.file_name || '论文文档' }}</text></view>
        <view class="row"><text class="k">当前页数</text><text class="v">{{ order.page_count }} 页</text></view>
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
        <view class="card">
          <view class="card-head">
            <text class="card-title">1. 选择新文件</text>
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
            <text class="card-title">2. 确认变更</text>
            <text class="tag">{{ immediate ? '立即生效' : '需老师确认' }}</text>
          </view>
          <view class="row"><text class="k">新页数</text><text class="v">{{ pageCount }} 页</text></view>
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

          <button class="primary block" :disabled="busy || preparing || !fileKey" @click="submit">
            {{ busy ? '提交中…' : submitLabel }}
          </button>
        </view>
      </template>
    </template>
  </view>
</template>

<script setup>
import { onLoad } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'
import { request, uploadFile } from '@/services/api'
import { ensureLogin } from '@/services/auth'
import { chooseDocument, loadDemoDocument } from '@/services/filePicker'
import { notify, notifyError } from '@/services/notify'
import { useUiTheme } from '@/ui/theme'

/** 允许换文件的状态：待支付直接生效，排队中与异常单走老师确认。 */
const REPLACEABLE = ['pending_pay', 'queuing', 'abnormal']

const BLOCKED_TEXT = {
  printing: '文件正在打印，不能再换。确实传错了请到文印室联系老师处理。',
  ready: '文件已经打印完成，不能再换文件。',
  completed: '订单已完成，不能再换文件。',
  cancelled: '订单已取消，不能再换文件。',
}

const { uiThemeClass } = useUiTheme()

const orderId = ref('')
const order = ref(null)
const busy = ref(false)
const preparing = ref(false)
const fileKey = ref('')
const fileName = ref('')
const fileSize = ref(0)
const pageCount = ref(1)
const reason = ref('')

const immediate = computed(() => !!order.value && order.value.status === 'pending_pay')

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
  busy.value = true
  try {
    const result = await request(`/api/order/${orderId.value}/replace-file`, {
      method: 'POST',
      data: {
        file_key: fileKey.value,
        file_name: fileName.value,
        page_count: pageCount.value,
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
