<template>
  <view class="page" :class="uiThemeClass">
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
      <text v-if="preparing" class="preparing">正在读取页数与打印参数…</text>

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

    <view v-if="fileKey" class="card ai-entry">
      <view class="card-head">
        <text class="card-title">AI 体检与修复（可选）</text>
        <text class="tag" :class="{ ok: aiChecked }">{{ aiChecked ? '已完成' : '可跳过' }}</text>
      </view>
      <text class="ai-entry-desc">
        {{ aiChecked ? '已按体检结果更新打印方案，也可以重新体检。' : '检查结构、参考文献与版式，并可选择生成修复副本。着急打印可以直接在下方下单。' }}
      </text>
      <button class="action ghost" :disabled="checking || preparing" @click="runCheck">
        {{ checking ? 'AI 正在体检…' : (aiChecked ? '重新体检' : '开始 AI 体检') }}
      </button>
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
      <view class="ai-fix-row">
        <view class="ai-fix-text">
          <text class="ai-fix-title">AI 修复（可选）</text>
          <text class="ai-fix-sub">{{ wantFix ? '勾选要修复的问题，会生成一个新副本，原文件保留' : '默认只给建议，不会修改原文档' }}</text>
        </view>
        <view class="ai-toggle" :class="{ on: wantFix }" @click="toggleWantFix">
          <view class="ai-toggle-knob"></view>
        </view>
      </view>
      <view v-if="report.issues.length === 0" class="empty">未发现明确问题，太棒了。</view>
      <view
        v-for="issue in report.issues"
        :key="issue.id"
        class="issue"
        :class="[issue.severity, { selected: selectedIssues.includes(issue.id) }]"
        @click="wantFix && toggleIssue(issue.id)"
      >
        <view class="issue-head">
          <view v-if="wantFix" class="check" :class="{ on: selectedIssues.includes(issue.id) }">{{ selectedIssues.includes(issue.id) ? '✓' : '' }}</view>
          <text class="issue-title">{{ severityLabel(issue.severity) }} · {{ typeLabel(issue.type) }}</text>
          <text class="issue-loc">{{ issue.location }}</text>
        </view>
        <text class="issue-original">原文：{{ issue.original }}</text>
        <text class="issue-suggestion">建议：{{ issue.suggestion }}</text>
        <text class="issue-reason">依据：{{ issue.reason }}</text>
      </view>
      <view v-if="wantFix && highIssueIds.length" class="fix-tools">
        <button class="mini-action" @click="selectHigh">全选高优先级</button>
        <button class="mini-action" @click="clearSelected">清空</button>
      </view>
      <button v-if="wantFix" class="action ghost" :disabled="busy || selectedIssues.length === 0" @click="generateFixed">
        {{ selectedIssues.length ? `生成修复版（勾选 ${selectedIssues.length} 项）` : '请先勾选要修复的问题' }}
      </button>
    </view>

    <view v-if="plan" class="card">
      <view class="card-head">
        <text class="card-title">3. 打印方案</text>
        <text class="tag">{{ planUsesAi ? 'AI 优化方案' : '基础方案' }} · 共 {{ pageCount }} 页</text>
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
import { useUiTheme } from '@/ui/theme'

const { uiThemeClass } = useUiTheme()

const steps = [
  { key: 'upload', label: '上传' },
  { key: 'check', label: '体检（可选）' },
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
const wantFix = ref(false)
const plan = ref(null)
const copies = ref(1)
const pageCount = ref(1)
const preparing = ref(false)
const aiChecked = ref(false)
const planUsesAi = ref(false)
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

const severitySummary = computed(() => {
  const counts = (report.value && report.value.by_severity) || {}
  return `高 ${counts['高'] || 0} · 中 ${counts['中'] || 0} · 低 ${counts['低'] || 0}`
})
const highIssueIds = computed(() =>
  report.value ? report.value.issues.filter((issue) => issue.severity === 'high').map((issue) => issue.id) : []
)

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
  wantFix.value = false
  aiChecked.value = false
  pageCount.value = 1
  planUsesAi.value = false
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
  await prepareBasicPlan()
}

async function prepareBasicPlan() {
  preparing.value = true
  try {
    const pc = await request('/api/file/page-count', {
      method: 'POST',
      data: { file_key: fileKey.value },
    })
    pageCount.value = Math.max(1, Number(pc.page_count) || 1)
    await loadPlan(false)
  } catch (error) {
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
  if (checking.value) return
  checking.value = true
  try {
    report.value = await request('/api/ai/check', {
      method: 'POST',
      data: { file_key: fileKey.value },
    })
    aiChecked.value = true
    pageCount.value = Math.max(1, Number(report.value.page_count) || pageCount.value)
    selectedIssues.value = []
    wantFix.value = false
    await loadPlan(true)
  } catch (error) {
    notifyError(error)
  } finally {
    checking.value = false
  }
}

async function loadPlan(useAi = false) {
  try {
    const options = { copies: copies.value, use_ai: useAi }
    if (printSide.value) options.print_side = printSide.value
    if (colorMode.value === 0 || colorMode.value === 1) options.color_mode = colorMode.value
    if (layout.value) options.layout = layout.value

    plan.value = await request('/api/ai/print-plan', {
      method: 'POST',
      data: { file_key: fileKey.value, options },
    })
    planUsesAi.value = useAi
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
  await loadPlan(planUsesAi.value)
}

function toggleIssue(id) {
  const index = selectedIssues.value.indexOf(id)
  if (index >= 0) {
    selectedIssues.value.splice(index, 1)
  } else {
    selectedIssues.value.push(id)
  }
}

function toggleWantFix() {
  wantFix.value = !wantFix.value
  if (!wantFix.value) {
    selectedIssues.value = []
  }
}

function selectHigh() {
  selectedIssues.value = [...highIssueIds.value]
}

function clearSelected() {
  selectedIssues.value = []
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
    wantFix.value = false
    selectedIssues.value = []
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
  await loadPlan(planUsesAi.value)
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
  box-sizing: border-box;
  background-color: var(--zi-bg);
  background-image: var(--zi-bg-image);
  color: var(--zi-ink);
}

.steps {
  display: flex;
  justify-content: space-between;
  padding: 18rpx 0;
  border-bottom: 3rpx solid var(--zi-ink);
}
.step {
  display: flex;
  align-items: center;
  flex: 1;
  color: var(--zi-muted);
  font-size: 24rpx;
  font-weight: 700;
}
.step.active {
  color: var(--zi-ink);
  font-weight: 900;
}
.step-index {
  width: 40rpx;
  height: 40rpx;
  margin-right: 10rpx;
  border: 2rpx solid var(--zi-line-strong);
  border-radius: var(--zi-radius);
  background: transparent;
  color: var(--zi-muted);
  text-align: center;
  line-height: 36rpx;
  font-size: 22rpx;
  font-weight: 800;
}
.step.active .step-index {
  border-color: var(--zi-accent);
  background: var(--zi-accent);
  color: var(--zi-accent-ink);
}

.card {
  margin-bottom: 24rpx;
  padding: 28rpx;
  background: var(--zi-paper);
  border: var(--zi-card-border);
  border-radius: var(--zi-radius);
  box-shadow: var(--zi-card-shadow);
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
}
.preparing {
  display: block;
  margin-top: 12rpx;
  color: var(--zi-accent);
  font-size: 24rpx;
  font-weight: 700;
}
.ai-entry {
  border: 2rpx dashed var(--zi-line-strong);
}
.ai-entry-desc {
  display: block;
  margin-top: 12rpx;
  color: var(--zi-muted);
  font-size: 24rpx;
  line-height: 1.6;
}
.ai-fix-row {
  display: flex;
  align-items: center;
  margin-top: 18rpx;
  padding: 16rpx;
  border: 2rpx solid var(--zi-line);
  border-radius: var(--zi-radius);
  background: var(--zi-soft);
}
.ai-fix-text {
  flex: 1;
  margin-right: 16rpx;
}
.ai-fix-title {
  display: block;
  color: var(--zi-ink);
  font-size: 26rpx;
  font-weight: 800;
}
.ai-fix-sub {
  display: block;
  margin-top: 4rpx;
  color: var(--zi-muted);
  font-size: 22rpx;
}
.ai-toggle {
  width: 86rpx;
  height: 48rpx;
  padding: 4rpx;
  box-sizing: border-box;
  border: 2rpx solid var(--zi-line-strong);
  border-radius: var(--zi-radius);
  background: var(--zi-soft-2);
  transition: background 0.2s;
}
.ai-toggle.on {
  border-color: var(--zi-accent);
  background: var(--zi-accent);
}
.ai-toggle-knob {
  width: 36rpx;
  height: 36rpx;
  border-radius: var(--zi-radius);
  background: var(--zi-paper);
  transition: transform 0.2s;
}
.ai-toggle.on .ai-toggle-knob {
  background: var(--zi-accent-ink);
  transform: translateX(38rpx);
}
.fix-tools {
  display: flex;
  margin-top: 16rpx;
}
.mini-action {
  height: 56rpx;
  margin: 0 12rpx 0 0;
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
  margin-top: 20rpx;
}
.action {
  flex: 1;
  height: 84rpx;
  margin: 0 8rpx;
  padding: 0 16rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border: var(--zi-button-border);
  border-radius: var(--zi-button-radius);
  background: transparent;
  color: var(--zi-ink);
  font-size: 26rpx;
  font-weight: 800;
  line-height: 1;
}
.action.demo {
  width: 100%;
  margin: 16rpx 0 0;
  border-style: dashed;
  background: var(--zi-soft-2);
  color: var(--zi-muted);
}
.action.ghost {
  width: 100%;
  margin: 20rpx 0 0;
  border-color: var(--zi-warn);
  background: var(--zi-warn-bg);
  color: var(--zi-warn);
}
.primary {
  width: 100%;
  height: 96rpx;
  margin-top: 20rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border: var(--zi-button-border);
  border-radius: var(--zi-button-radius);
  background: var(--zi-primary-bg);
  color: var(--zi-primary-ink);
  box-shadow: var(--zi-primary-shadow);
  font-size: 28rpx;
  font-weight: 800;
  letter-spacing: 2rpx;
}
.block {
  width: 100%;
}
.loading-text {
  display: block;
  color: var(--zi-accent);
  font-size: 28rpx;
  font-weight: 800;
}
.loading-sub {
  margin-top: 8rpx;
  color: var(--zi-muted);
  font-size: 24rpx;
}
.summary {
  display: block;
  margin-top: 14rpx;
  color: var(--zi-ink);
  font-size: 26rpx;
  line-height: 1.6;
}
.warn {
  margin-top: 12rpx;
  padding: 14rpx;
  border-left: 6rpx solid var(--zi-warn);
  background: var(--zi-warn-bg);
  color: var(--zi-warn);
  font-size: 24rpx;
  line-height: 1.6;
}
.issue {
  margin-top: 18rpx;
  padding: 20rpx;
  border: 2rpx solid var(--zi-line);
  border-radius: var(--zi-radius);
  background: var(--zi-soft-2);
}
.issue.selected {
  border-color: var(--zi-accent);
  background: var(--zi-soft);
}
.issue.high {
  border-left: 8rpx solid var(--zi-danger);
}
.issue.medium {
  border-left: 8rpx solid var(--zi-warn);
}
.issue.low {
  border-left: 8rpx solid var(--zi-muted);
}
.issue-head {
  display: flex;
  align-items: center;
}
.check {
  width: 36rpx;
  height: 36rpx;
  margin-right: 12rpx;
  box-sizing: border-box;
  border: 2rpx solid var(--zi-line-strong);
  border-radius: var(--zi-radius);
  color: var(--zi-accent-ink);
  text-align: center;
  line-height: 32rpx;
  font-size: 24rpx;
}
.check.on {
  border-color: var(--zi-accent);
  background: var(--zi-accent);
}
.issue-title {
  color: var(--zi-ink);
  font-size: 26rpx;
  font-weight: 800;
}
.issue-loc {
  margin-left: auto;
  color: var(--zi-muted);
  font-size: 22rpx;
}
.issue-original,
.issue-suggestion,
.issue-reason {
  display: block;
  margin-top: 10rpx;
  color: var(--zi-ink);
  font-size: 24rpx;
  line-height: 1.6;
}
.issue-suggestion {
  color: var(--zi-accent);
  font-weight: 700;
}
.issue-reason {
  color: var(--zi-muted);
}
.ai-recommend {
  display: block;
  margin-top: 14rpx;
  color: var(--zi-muted);
  font-size: 24rpx;
  line-height: 1.6;
}
.opt-row {
  display: flex;
  align-items: center;
  margin-top: 18rpx;
}
.opt-label {
  width: 120rpx;
  color: var(--zi-muted);
  font-size: 24rpx;
  font-weight: 700;
}
.seg {
  display: flex;
  flex: 1;
  padding: 5rpx;
  border: 2rpx solid var(--zi-line);
  border-radius: var(--zi-control-radius);
  background: var(--zi-soft);
}
.seg-item {
  flex: 1;
  padding: 10rpx 0;
  border-radius: var(--zi-radius);
  color: var(--zi-muted);
  text-align: center;
  font-size: 24rpx;
  font-weight: 700;
}
.seg-item.on {
  background: var(--zi-primary-bg);
  color: var(--zi-primary-ink);
  font-weight: 900;
}
.stepper-wrap {
  display: flex;
  align-items: center;
  padding: 5rpx 12rpx;
  border: 2rpx solid var(--zi-line);
  border-radius: var(--zi-control-radius);
  background: var(--zi-soft);
}
.stepper-wrap .copy-num {
  min-width: 60rpx;
  text-align: center;
  color: var(--zi-ink);
  font-size: 28rpx;
  font-weight: 900;
}
.stepper {
  padding: 0 12rpx;
  color: var(--zi-accent);
  font-size: 30rpx;
  font-weight: 900;
}
.plan-reason {
  display: block;
  margin-top: 14rpx;
  color: var(--zi-muted);
  font-size: 24rpx;
  line-height: 1.6;
}
.savings {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10rpx;
  margin-top: 16rpx;
  padding: 18rpx;
  border: 2rpx solid var(--zi-line);
  border-radius: var(--zi-radius);
  background: var(--zi-soft);
  color: var(--zi-ink);
  font-size: 24rpx;
}
.savings .green {
  color: var(--zi-ok);
  font-weight: 700;
}
.savings .money {
  color: var(--zi-ink);
  font-size: 28rpx;
  font-weight: 900;
}
.code-box {
  display: flex;
  margin: 20rpx 0;
  padding: 20rpx 0;
  border-top: 3rpx solid var(--zi-ink);
  border-bottom: 3rpx solid var(--zi-ink);
}
.code-item {
  flex: 1;
  text-align: center;
}
.code-item .k {
  display: block;
  color: var(--zi-muted);
  font-size: 22rpx;
  font-weight: 700;
}
.code-item .v {
  display: block;
  margin-top: 6rpx;
  color: var(--zi-ink);
  font-size: 32rpx;
  font-weight: 900;
  word-break: break-all;
}
.code-item .code {
  color: var(--zi-accent);
  font-size: 32rpx;
  font-weight: 900;
  letter-spacing: 2rpx;
}
.hint {
  display: block;
  padding: 16rpx;
  border-left: 6rpx solid var(--zi-warn);
  background: var(--zi-warn-bg);
  color: var(--zi-warn);
  font-size: 24rpx;
  line-height: 1.6;
}
.pay-amount {
  display: block;
  margin-top: 16rpx;
  color: var(--zi-ink);
  font-size: 28rpx;
  font-weight: 900;
}
.ocr-box {
  margin-top: 20rpx;
  padding: 18rpx;
  border: 2rpx solid var(--zi-line);
  border-radius: var(--zi-radius);
  background: var(--zi-soft);
}
.ocr-title {
  display: block;
  color: var(--zi-ink);
  font-size: 24rpx;
  font-weight: 800;
}
.ocr-text {
  display: block;
  margin-top: 8rpx;
  color: var(--zi-ink);
  font-size: 24rpx;
  line-height: 1.6;
  white-space: pre-wrap;
}
.ocr-tip {
  display: block;
  margin-top: 8rpx;
  color: var(--zi-muted);
  font-size: 22rpx;
}

/* 旧蓝白皮肤：恢复原有轻量圆角与填充标签 */
.zi-theme-classic .steps {
  border-bottom: none;
}
.zi-theme-classic .step-index {
  border: none;
  border-radius: 50%;
  background: var(--zi-line);
  line-height: 40rpx;
  font-weight: 400;
}
.zi-theme-classic .step.active .step-index {
  background: var(--zi-accent);
}
.zi-theme-classic .card-title {
  padding-left: 0;
  border-left: none;
  font-weight: 600;
  letter-spacing: 0;
}
.zi-theme-classic .tag {
  border: none;
  background: var(--zi-soft);
}
.zi-theme-classic .tag.ok {
  background: var(--zi-ok-bg);
}
.zi-theme-classic .ai-entry {
  border: 2rpx dashed var(--zi-line-strong);
}
.zi-theme-classic .ai-fix-row,
.zi-theme-classic .file-info,
.zi-theme-classic .savings,
.zi-theme-classic .ocr-box {
  border: none;
  border-radius: 16rpx;
}
.zi-theme-classic .ai-toggle {
  border: none;
  border-radius: 999rpx;
}
.zi-theme-classic .ai-toggle-knob {
  border-radius: 50%;
}
.zi-theme-classic .mini-action {
  border: none;
  border-radius: 999rpx;
  background: var(--zi-soft);
  color: var(--zi-accent);
  font-weight: 400;
}
.zi-theme-classic .action {
  border: none;
  border-radius: 16rpx;
  background: var(--zi-soft);
  color: var(--zi-accent);
  font-weight: 400;
}
.zi-theme-classic .action.demo {
  background: var(--zi-soft-2);
  color: var(--zi-muted);
}
.zi-theme-classic .action.ghost {
  background: var(--zi-warn-bg);
  color: var(--zi-warn);
}
.zi-theme-classic .primary {
  border: none;
  border-radius: 16rpx;
  font-weight: 600;
  letter-spacing: 0;
}
.zi-theme-classic .warn,
.zi-theme-classic .hint {
  border-left: none;
  border-radius: 14rpx;
}
.zi-theme-classic .issue {
  border-radius: 16rpx;
}
.zi-theme-classic .check {
  border-radius: 10rpx;
}
.zi-theme-classic .seg,
.zi-theme-classic .stepper-wrap {
  border: none;
}
.zi-theme-classic .seg {
  border-radius: 14rpx;
}
.zi-theme-classic .seg-item {
  border-radius: 10rpx;
  font-weight: 400;
}
.zi-theme-classic .seg-item.on {
  background: var(--zi-accent);
  font-weight: 600;
}
.zi-theme-classic .code-box {
  border: none;
  padding: 0;
}
.zi-theme-classic .code-item .code {
  color: var(--zi-accent);
}
</style>
