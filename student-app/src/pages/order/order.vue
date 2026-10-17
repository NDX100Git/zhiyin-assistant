<template>
  <view class="page">
    <view class="steps">
      <view v-for="(item, index) in steps" :key="item.key" class="step" :class="{ active: stepIndex >= index }">
        <text class="step-index">{{ index + 1 }}</text>
        <text class="step-text">{{ item.label }}</text>
      </view>
    </view>

    <view class="card">
      <view class="card-head">
        <text class="card-title">1. 选择论文</text>
        <text v-if="fileKey" class="tag ok">已选择</text>
      </view>
      <view v-if="fileKey" class="file-info">
        <text class="file-name">{{ fileName }}</text>
        <text class="file-meta">{{ formatSize(fileSize) }}</text>
      </view>
      <view v-else class="empty">支持 PDF / Word / PPT / Excel，单文件不超过 50MB</view>

      <view class="actions">
        <button class="action" :disabled="busy" @click="pickDocument">选择文档</button>
        <button class="action" :disabled="busy" @click="pickImage">拍照 / 相册</button>
      </view>
      <button class="action demo" :disabled="busy" @click="useDemo">载入示例论文（演示兜底）</button>
      <view v-if="ocrText" class="ocr-box">
        <text class="ocr-title">图片识别结果</text>
        <text class="ocr-text">{{ ocrText }}</text>
        <text class="ocr-tip">如需体检与打印，请上传 PDF / Word 文档。</text>
      </view>
    </view>

    <view v-if="checking && !report" class="card center">
      <text class="loading-text">AI 正在体检论文格式…</text>
      <text class="loading-sub">解析版式、检查结构与参考文献</text>
    </view>

    <view v-if="report" class="card">
      <view class="card-head">
        <text class="card-title">2. AI 体检报告</text>
        <text class="tag">{{ severitySummary }}</text>
      </view>
      <text class="summary">{{ report.summary }}</text>
      <view v-if="report.ai_errors && report.ai_errors.length" class="warn">
        {{ report.ai_errors.join('；') }}
      </view>
      <view v-if="report.issues.length === 0" class="empty">未发现明确问题，太棒了。</view>
      <view
        v-for="issue in report.issues"
        :key="issue.id"
        class="issue"
        :class="[issue.severity, { selected: selectedIssues.includes(issue.id) }]"
        @click="toggleIssue(issue.id)"
      >
        <view class="issue-head">
          <view class="check" :class="{ on: selectedIssues.includes(issue.id) }">{{ selectedIssues.includes(issue.id) ? '✓' : '' }}</view>
          <text class="issue-title">{{ severityLabel(issue.severity) }} · {{ typeLabel(issue.type) }}</text>
          <text class="issue-loc">{{ issue.location }}</text>
        </view>
        <text class="issue-original">原文：{{ issue.original }}</text>
        <text class="issue-suggestion">建议：{{ issue.suggestion }}</text>
        <text class="issue-reason">依据：{{ issue.reason }}</text>
      </view>
      <button v-if="selectedIssues.length" class="action ghost" :disabled="busy" @click="generateFixed">
        生成修复版（勾选 {{ selectedIssues.length }} 项）
      </button>
    </view>

    <view v-if="plan" class="card">
      <view class="card-head">
        <text class="card-title">3. 打印方案</text>
        <text class="tag">共 {{ pageCount }} 页</text>
      </view>
      <text class="ai-recommend">当前方案：{{ plan.recommend.print_side === 2 ? '双面' : '单面' }} · {{ plan.recommend.color_mode === 1 ? '彩色' : '黑白' }} · {{ layoutLabel(plan.recommend.layout) }}</text>
      <view class="opt-row">
        <text class="opt-label">单双面</text>
        <view class="seg">
          <view class="seg-item" :class="{ on: printSide === 1 }" @click.stop="choose('print_side', 1)">单面</view>
          <view class="seg-item" :class="{ on: printSide === 2 }" @click.stop="choose('print_side', 2)">双面</view>
        </view>
      </view>
      <view class="opt-row">
        <text class="opt-label">颜色</text>
        <view class="seg">
          <view class="seg-item" :class="{ on: colorMode === 0 }" @click.stop="choose('color_mode', 0)">黑白</view>
          <view class="seg-item" :class="{ on: colorMode === 1 }" @click.stop="choose('color_mode', 1)">彩色</view>
        </view>
      </view>
      <view class="opt-row">
        <text class="opt-label">版式</text>
        <view class="seg">
          <view v-for="item in layouts" :key="item.value" class="seg-item" :class="{ on: layout === item.value }" @click.stop="choose('layout', item.value)">{{ item.label }}</view>
        </view>
      </view>
      <view class="opt-row">
        <text class="opt-label">份数</text>
        <view class="stepper-wrap">
          <text class="stepper" @click.stop="changeCopies(-1)">－</text>
          <text class="copy-num">{{ copies }}</text>
          <text class="stepper" @click.stop="changeCopies(1)">＋</text>
        </view>
      </view>
      <text class="plan-reason">AI 建议理由：{{ plan.reason }}</text>
      <view class="savings">
        <text>纸张 {{ plan.original_sheets }} → {{ plan.sheets_needed }} 张</text>
        <text class="green">省 {{ plan.saved_sheets }} 张</text>
        <text class="money">预计 {{ formatMoney(plan.amount_cents) }}</text>
      </view>
      <button class="primary block" :disabled="busy" @click="createOrder">确认下单</button>
    </view>

    <view v-if="created" class="card success">
      <text class="card-title">下单成功</text>
      <view class="code-box">
        <view class="code-item"><text class="k">订单号</text><text class="v big">{{ created.order_no }}</text></view>
        <view class="code-item"><text class="k">取件码</text><text class="v code">{{ created.pickup_code }}</text></view>
      </view>
      <text v-if="created.session_notice && created.session_notice.hint" class="hint">{{ created.session_notice.hint }}</text>
      <text class="pay-amount">应付：{{ formatMoney(created.amount_cents) }}</text>
      <button v-if="created.status === 'pending_pay'" class="primary block" :disabled="busy" @click="payOrder">模拟支付</button>
      <button class="action block" @click="goMine">查看我的订单</button>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { request, uploadFile } from '@/services/api'
import { ensureLogin } from '@/services/auth'
import { chooseDocument, chooseImage, loadDemoDocument } from '@/services/filePicker'
import { notify, notifyError } from '@/services/notify'

const steps = [
  { key: 'upload', label: '上传' },
  { key: 'check', label: '体检' },
  { key: 'plan', label: '下单' },
]

const stepIndex = ref(0)
const busy = ref(false)
const checking = ref(false)
const fileKey = ref('')
const fileName = ref('')
const fileSize = ref(0)
const report = ref(null)
const selectedIssues = ref([])
const plan = ref(null)
const copies = ref(1)
const printSide = ref(null)
const colorMode = ref(null)
const layout = ref('')
const layouts = [
  { value: 'single', label: '单页' },
  { value: 'handout_2in1', label: '2合1' },
  { value: 'handout_4in1', label: '4合1' },
]
const created = ref(null)
const ocrText = ref('')

const pageCount = computed(() => Math.max(1, Number(report.value && report.value.page_count) || 1))
const severitySummary = computed(() => {
  const counts = (report.value && report.value.by_severity) || {}
  return `高 ${counts['高'] || 0} · 中 ${counts['中'] || 0} · 低 ${counts['低'] || 0}`
})

function severityLabel(severity) {
  return severity === 'high' ? '高' : severity === 'medium' ? '中' : '低'
}

function typeLabel(type) {
  if (type === 'reference') return '参考文献'
  if (type === 'structure') return '结构'
  if (type === 'typography') return '排版'
  return '版式'
}

function layoutLabel(layout) {
  if (layout === 'handout_2in1') return '讲义 2 合 1'
  if (layout === 'handout_4in1') return '讲义 4 合 1'
  return '单页版式'
}

function formatSize(size) {
  if (!size) return '-'
  return size > 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${Math.round(size / 1024)} KB`
}

function formatMoney(cents) {
  return `¥${((Number(cents) || 0) / 100).toFixed(2)}`
}

function resetFlow() {
  report.value = null
  selectedIssues.value = []
  plan.value = null
  created.value = null
  ocrText.value = ''
  copies.value = 1
  printSide.value = null
  colorMode.value = null
  layout.value = ''
}

async function useUploaded(data) {
  fileKey.value = data.file_key
  fileName.value = data.file_name || '文档'
  fileSize.value = data.size || 0
  resetFlow()
  stepIndex.value = 1
  await runCheck()
}

async function pickDocument() {
  if (busy.value) return
  try {
    await ensureLogin()
    const picked = await chooseDocument()
    busy.value = true
    const data = await uploadFile(picked.path, picked.name)
    await useUploaded(data)
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
    const data = await loadDemoDocument()
    await useUploaded(data)
    notify('已载入示例论文', 'success')
  } catch (error) {
    notifyError(error)
  } finally {
    busy.value = false
  }
}

async function pickImage() {
  if (busy.value) return
  try {
    await ensureLogin()
    const picked = await chooseImage()
    busy.value = true
    const uploaded = await uploadFile(picked.path, picked.name)
    const ocr = await request('/api/ai/ocr', {
      method: 'POST',
      data: { file_key: uploaded.file_key },
    })
    ocrText.value = ocr.text
    notify('识别完成', 'success')
  } catch (error) {
    notifyError(error)
  } finally {
    busy.value = false
  }
}

async function runCheck() {
  checking.value = true
  try {
    report.value = await request('/api/ai/check', {
      method: 'POST',
      data: { file_key: fileKey.value },
    })
    selectedIssues.value = report.value.issues
      .filter((issue) => issue.severity === 'high')
      .map((issue) => issue.id)
    await loadPlan()
  } catch (error) {
    notifyError(error)
  } finally {
    checking.value = false
  }
}

async function loadPlan() {
  try {
    const options = { copies: copies.value }
    if (printSide.value) options.print_side = printSide.value
    if (colorMode.value === 0 || colorMode.value === 1) options.color_mode = colorMode.value
    if (layout.value) options.layout = layout.value

    plan.value = await request('/api/ai/print-plan', {
      method: 'POST',
      data: { file_key: fileKey.value, options },
    })
    printSide.value = plan.value.recommend.print_side
    colorMode.value = plan.value.recommend.color_mode
    layout.value = plan.value.recommend.layout
    stepIndex.value = 2
  } catch (error) {
    notifyError(error)
  }
}

async function choose(field, value) {
  if (field === 'print_side') printSide.value = value
  if (field === 'color_mode') colorMode.value = value
  if (field === 'layout') layout.value = value
  await loadPlan()
}

function toggleIssue(id) {
  const index = selectedIssues.value.indexOf(id)
  if (index >= 0) {
    selectedIssues.value.splice(index, 1)
  } else {
    selectedIssues.value.push(id)
  }
}

async function generateFixed() {
  if (busy.value || selectedIssues.value.length === 0) return
  busy.value = true
  try {
    const fixed = await request('/api/ai/fix', {
      method: 'POST',
      data: {
        file_key: fileKey.value,
        issue_ids: selectedIssues.value,
        file_name: `修复版-${fileName.value}`,
      },
    })
    notify(`已生成修复版（${fixed.applied_actions.length} 项修改）`, 'success')
    fileKey.value = fixed.file_key
    fileName.value = fixed.file_name || fileName.value
    fileSize.value = fixed.size || fileSize.value
    await runCheck()
  } catch (error) {
    notifyError(error)
  } finally {
    busy.value = false
  }
}

async function changeCopies(delta) {
  const next = Math.max(1, Math.min(50, copies.value + delta))
  if (next === copies.value) return
  copies.value = next
  await loadPlan()
}

async function createOrder() {
  if (busy.value || !plan.value) return
  busy.value = true
  try {
    created.value = await request('/api/order/create', {
      method: 'POST',
      data: {
        file_key: fileKey.value,
        file_name: fileName.value,
        page_count: pageCount.value,
        print_side: plan.value.recommend.print_side,
        color_mode: plan.value.recommend.color_mode,
        copies: copies.value,
        paper_size: 'A4',
      },
    })
    stepIndex.value = 2
  } catch (error) {
    notifyError(error)
  } finally {
    busy.value = false
  }
}

async function payOrder() {
  if (busy.value || !created.value) return
  busy.value = true
  try {
    const paid = await request(`/api/order/${created.value.id}/pay`, { method: 'POST' })
    created.value = { ...created.value, status: paid.status, queue: paid.queue }
    notify('支付成功，已进入打印队列', 'success')
  } catch (error) {
    notifyError(error)
  } finally {
    busy.value = false
  }
}

function goMine() {
  uni.switchTab({ url: '/pages/mine/mine' })
}
</script>

<style scoped>
.page {
  min-height: 100vh;
  padding: 24rpx 24rpx 160rpx;
  background: #f5f7fb;
  box-sizing: border-box;
}
.steps {
  display: flex;
  justify-content: space-between;
  padding: 20rpx 12rpx;
}
.step {
  display: flex;
  align-items: center;
  flex: 1;
  color: #9aa7b5;
  font-size: 24rpx;
}
.step.active {
  color: #2f6bff;
  font-weight: 600;
}
.step-index {
  width: 40rpx;
  height: 40rpx;
  margin-right: 8rpx;
  border-radius: 50%;
  background: #e6ebf5;
  color: #7a869a;
  text-align: center;
  line-height: 40rpx;
  font-size: 22rpx;
}
.step.active .step-index {
  background: #2f6bff;
  color: #fff;
}
.card {
  margin-bottom: 24rpx;
  padding: 28rpx;
  background: #fff;
  border-radius: 24rpx;
  box-shadow: 0 8rpx 24rpx rgba(31, 45, 61, 0.06);
}
.card.center {
  text-align: center;
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-title {
  font-size: 30rpx;
  font-weight: 600;
  color: #1f2d3d;
}
.tag {
  padding: 4rpx 14rpx;
  border-radius: 999rpx;
  font-size: 22rpx;
  color: #2f6bff;
  background: #eef3ff;
}
.tag.ok {
  color: #3f8f5c;
  background: #eaf7ef;
}
.empty {
  margin-top: 16rpx;
  font-size: 26rpx;
  color: #9aa7b5;
}
.file-info {
  display: flex;
  justify-content: space-between;
  margin-top: 18rpx;
  padding: 18rpx;
  border-radius: 16rpx;
  background: #f7f9fc;
}
.file-name {
  flex: 1;
  font-size: 26rpx;
  color: #1f2d3d;
  word-break: break-all;
}
.file-meta {
  margin-left: 16rpx;
  font-size: 24rpx;
  color: #9aa7b5;
}
.actions {
  display: flex;
  margin-top: 20rpx;
}
.action {
  flex: 1;
  margin: 0 8rpx;
  border-radius: 16rpx;
  font-size: 26rpx;
  color: #2f6bff;
  background: #eef3ff;
}
.action.demo {
  width: 100%;
  margin: 16rpx 0 0;
  color: #6b7a90;
  background: #f2f4f8;
}
.action.ghost {
  width: 100%;
  margin: 20rpx 0 0;
  color: #b25e09;
  background: #fff4e6;
}
.primary {
  width: 100%;
  margin-top: 20rpx;
  border-radius: 16rpx;
  background: #2f6bff;
  color: #fff;
  font-size: 28rpx;
}
.block {
  width: 100%;
}
.loading-text {
  display: block;
  font-size: 28rpx;
  color: #2f6bff;
}
.loading-sub {
  margin-top: 8rpx;
  font-size: 24rpx;
  color: #9aa7b5;
}
.summary {
  display: block;
  margin-top: 14rpx;
  font-size: 26rpx;
  color: #4b5a6b;
}
.warn {
  margin-top: 12rpx;
  padding: 14rpx;
  border-radius: 12rpx;
  font-size: 24rpx;
  color: #b25e09;
  background: #fff4e6;
}
.issue {
  margin-top: 18rpx;
  padding: 20rpx;
  border-radius: 16rpx;
  background: #fbfcfe;
  border: 2rpx solid #eef1f6;
}
.issue.selected {
  border-color: #2f6bff;
  background: #f5f8ff;
}
.issue.high {
  border-left: 8rpx solid #ff5b5b;
}
.issue.medium {
  border-left: 8rpx solid #ffb020;
}
.issue.low {
  border-left: 8rpx solid #8aa4c8;
}
.issue-head {
  display: flex;
  align-items: center;
}
.check {
  width: 36rpx;
  height: 36rpx;
  margin-right: 12rpx;
  border-radius: 10rpx;
  border: 2rpx solid #c9d3e0;
  text-align: center;
  line-height: 36rpx;
  font-size: 24rpx;
  color: #fff;
}
.check.on {
  border-color: #2f6bff;
  background: #2f6bff;
}
.issue-title {
  font-size: 26rpx;
  font-weight: 600;
  color: #1f2d3d;
}
.issue-loc {
  margin-left: auto;
  font-size: 22rpx;
  color: #9aa7b5;
}
.issue-original,
.issue-suggestion,
.issue-reason {
  display: block;
  margin-top: 10rpx;
  font-size: 24rpx;
  line-height: 1.6;
  color: #4b5a6b;
}
.issue-suggestion {
  color: #2f6bff;
}
.issue-reason {
  color: #8b98a8;
}
.ai-recommend {
  display: block;
  margin-top: 14rpx;
  font-size: 24rpx;
  color: #6b7a90;
}
.opt-row {
  display: flex;
  align-items: center;
  margin-top: 18rpx;
}
.opt-label {
  width: 120rpx;
  font-size: 24rpx;
  color: #9aa7b5;
}
.seg {
  display: flex;
  flex: 1;
  padding: 6rpx;
  border-radius: 14rpx;
  background: #f2f4f8;
}
.seg-item {
  flex: 1;
  padding: 10rpx 0;
  border-radius: 10rpx;
  text-align: center;
  font-size: 24rpx;
  color: #6b7a90;
}
.seg-item.on {
  color: #fff;
  background: #2f6bff;
}
.stepper-wrap {
  display: flex;
  align-items: center;
  padding: 6rpx 12rpx;
  border-radius: 14rpx;
  background: #f2f4f8;
}
.stepper-wrap .copy-num {
  min-width: 60rpx;
  text-align: center;
  font-size: 26rpx;
  color: #1f2d3d;
}
.stepper {
  padding: 0 12rpx;
  color: #2f6bff;
}
.plan-reason {
  display: block;
  font-size: 24rpx;
  line-height: 1.6;
  color: #6b7a90;
}
.savings {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 16rpx;
  padding: 18rpx;
  border-radius: 14rpx;
  background: #f7f9fc;
  font-size: 24rpx;
  color: #4b5a6b;
}
.savings .green {
  color: #3f8f5c;
}
.savings .money {
  font-weight: 600;
  color: #1f2d3d;
}
.code-box {
  display: flex;
  margin: 20rpx 0;
}
.code-item {
  flex: 1;
  text-align: center;
}
.code-item .k {
  display: block;
  font-size: 22rpx;
  color: #9aa7b5;
}
.code-item .v {
  display: block;
  margin-top: 6rpx;
  font-size: 32rpx;
  font-weight: 700;
  color: #1f2d3d;
}
.code-item .code {
  font-size: 44rpx;
  letter-spacing: 4rpx;
  color: #2f6bff;
}
.hint {
  display: block;
  padding: 16rpx;
  border-radius: 14rpx;
  background: #fffaf0;
  font-size: 24rpx;
  line-height: 1.6;
  color: #b25e09;
}
.pay-amount {
  display: block;
  margin-top: 16rpx;
  font-size: 28rpx;
  font-weight: 600;
  color: #1f2d3d;
}
.ocr-box {
  margin-top: 20rpx;
  padding: 18rpx;
  border-radius: 14rpx;
  background: #f7f9fc;
}
.ocr-title {
  display: block;
  font-size: 24rpx;
  font-weight: 600;
  color: #1f2d3d;
}
.ocr-text {
  display: block;
  margin-top: 8rpx;
  font-size: 24rpx;
  line-height: 1.6;
  color: #4b5a6b;
  white-space: pre-wrap;
}
.ocr-tip {
  display: block;
  margin-top: 8rpx;
  font-size: 22rpx;
  color: #9aa7b5;
}
</style>
