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
        <text class="card-title">1. 选择要打印的文件</text>
        <text v-if="files.length" class="tag ok">
          {{ files.length > 1 ? `${files.length} 个文件 · 共 ${totalPages} 页` : '已选择' }}
        </text>
      </view>

      <view v-if="files.length" class="file-list">
        <view v-for="(item, index) in files" :key="item.uid" class="file-row">
          <view class="file-main">
            <text class="file-name">
              {{ files.length > 1 ? `${index + 1}. ` : '' }}{{ item.file_name }}
            </text>
            <text class="file-meta">
              {{ item.page_count ? `${item.page_count} 页` : '读取页数中…' }}
              <text v-if="item.size"> · {{ formatSize(item.size) }}</text>
            </text>
          </view>
          <!-- 移除 = 撤回这份还没下单的上传文件，服务端同时删对象与上传记录 -->
          <view class="file-remove" :class="{ busy: removingUid === item.uid }" @click="removeFile(index)">
            {{ removingUid === item.uid ? '撤回中' : '移除' }}
          </view>
        </view>
      </view>
      <view v-if="uploading" class="preparing">正在上传 {{ uploadingName }}…</view>
      <view v-else-if="!files.length" class="empty">
        支持 PDF / Word / PPT / Excel，单个文件不超过 50MB；一次可选多份，合成一单只付一次
      </view>

      <view class="actions">
        <button class="action" :disabled="busy" @click="pickDocuments">
          {{ files.length ? '继续添加文件' : '选择文档' }}
        </button>
        <button class="action" :disabled="busy" @click="pickImage">拍照 / 相册</button>
      </view>
      <button class="action demo" :disabled="busy" @click="useDemo">载入示例论文（演示兜底）</button>
      <view v-if="ocrText" class="ocr-box">
        <text class="ocr-title">图片识别结果</text>
        <text class="ocr-text">{{ ocrText }}</text>
        <text class="ocr-tip">如需体检与打印，请上传 PDF / Word 文档。</text>
      </view>
    </view>

    <view v-if="files.length && singleFile" class="card ai-entry">
      <view class="card-head">
        <text class="card-title">AI 体检与修复（可选）</text>
        <text class="tag" :class="{ ok: aiChecked }">{{ aiChecked ? '已完成' : '可跳过' }}</text>
      </view>
      <text class="ai-entry-desc">
        {{ aiChecked ? '已按体检结果更新打印方案，也可以重新体检。' : '检查结构、参考文献与版式，并可选择生成修复副本。不做体检也能直接下单。' }}
      </text>
      <view class="ai-actions">
        <button class="action ghost" :disabled="checking || preparing" @click="runCheck">
          {{ checking ? 'AI 正在体检…' : (aiChecked ? '重新体检' : '开始 AI 体检') }}
        </button>
        <button class="action skip" :disabled="busy || preparing || !plan" @click="skipCheckAndOrder">
          跳过体检，直接下单
        </button>
      </view>
    </view>

    <view v-else-if="files.length > 1" class="card ai-entry">
      <view class="card-head">
        <text class="card-title">多文件不参与体检</text>
        <text class="tag">直接下单</text>
      </view>
      <text class="ai-entry-desc">
        一单多份文件按基础方案下单，AI 体检只针对单份文件；确需体检可先只传这一份，体检后单独下单。
      </text>
    </view>

    <view v-if="checking && !report" class="card center">
      <text class="loading-text">AI 正在体检论文格式…</text>
      <text class="loading-sub">解析版式、检查结构与参考文献</text>
    </view>

    <view v-if="report && singleFile" class="card">
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
        <text class="tag">
          {{ planUsesAi ? 'AI 优化方案' : '基础方案' }} · 共 {{ totalPages }} 页{{ files.length > 1 ? ` · ${files.length} 个文件` : '' }}
        </text>
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
      <text class="plan-reason">{{ singleFile ? 'AI 建议理由' : '方案估算依据' }}：{{ plan.reason }}</text>
      <view v-if="singleFile" class="savings">
        <text>纸张 {{ plan.original_sheets }} → {{ plan.sheets_needed }} 张</text>
        <text class="green">省 {{ plan.saved_sheets }} 张</text>
        <text class="money">预计 {{ formatMoney(plan.amount_cents) }}</text>
      </view>
      <view v-else class="savings">
        <text>{{ files.length }} 个文件 · 共 {{ totalPages }} 页 × {{ copies }} 份</text>
        <text class="money">预计 {{ formatMoney(quoteCents) }}</text>
      </view>
      <button class="primary block" :disabled="busy" @click="createOrder">
        {{ files.length > 1 ? `确认下单（${files.length} 个文件 · ${totalPages} 页）` : '确认下单' }}
      </button>
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
import { deleteUploadedFile, request, uploadFile } from '@/services/api'
import { ensureLogin } from '@/services/auth'
import { MAX_FILES_PER_PICK, chooseDocuments, chooseImage, loadDemoDocument } from '@/services/filePicker'
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

/**
 * 本轮要打印的文件清单（多文件合成一单）。
 * 每个元素：`{uid, file_key, file_name, size, page_count}`；uid 只给列表 key 用。
 * 还没下单，所以这里随时可以移除——移除时会调后端的撤回接口。
 */
const files = ref([])
const uploading = ref(false)
const uploadingName = ref('')
const removingUid = ref('')
const uidSeq = ref(0)

/** 一单最多几份：后端 order_rules.json 的 max_files 是 20，界面留同口径。 */
const MAX_ORDER_FILES = 20

const report = ref(null)
const selectedIssues = ref([])
const wantFix = ref(false)
const plan = ref(null)
const copies = ref(1)
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

/** 只有一份文件时才有 AI 体检与修复：多文件的体检口径产品还没定。 */
const singleFile = computed(() => files.value.length === 1)

/** 订单总页数：清单里各文件页数之和，下单与计费都按它算。 */
const totalPages = computed(() =>
  files.value.reduce((sum, item) => sum + (Number(item.page_count) || 0), 0)
)

/** 方案与体检都针对第一份文件取参数，多文件只借用它的单价与推荐值。 */
const primaryFile = computed(() => files.value[0] || null)

/**
 * 多文件合计金额。
 *
 * 打印方案接口只算单份文件（且按 impression 取整），下单与后端计费用的是
 * 「单价 × 总页数 × 份数」，所以这里从方案里反推单价，再乘总页数，
 * 保证界面数字与真实扣款一致，不让“预计”和“应付”打打。
 */
const quoteCents = computed(() => {
  const current = plan.value
  if (!current) return 0
  if (files.value.length <= 1) return Number(current.amount_cents) || 0
  const impressions = Number(current.impressions) || 0
  const unit = impressions > 0 ? Math.round(Number(current.amount_cents) / impressions) : 0
  if (!unit) return Number(current.amount_cents) || 0
  return unit * totalPages.value * copies.value
})

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

function resetCheck() {
  report.value = null
  selectedIssues.value = []
  wantFix.value = false
  aiChecked.value = false
  planUsesAi.value = false
}

/** 把一次上传结果加进清单；重复选同一个文件不重复入单。 */
function pushFile(data) {
  const fileKey = data.file_key
  if (!fileKey) return false
  if (files.value.some((item) => item.file_key === fileKey)) return false
  uidSeq.value += 1
  files.value.push({
    uid: `f${uidSeq.value}`,
    file_key: fileKey,
    file_name: data.file_name || '文档',
    size: Number(data.size) || 0,
    page_count: 0,
  })
  return true
}

/** 逐份补页数：页数来自 page-count 接口，不调模型。 */
async function fillPageCounts() {
  for (const item of files.value) {
    if (item.page_count) continue
    try {
      const result = await request('/api/file/page-count', {
        method: 'POST',
        data: { file_key: item.file_key },
      })
      item.page_count = Math.max(1, Number(result.page_count) || 1)
    } catch (error) {
      notifyError(error)
    }
  }
}

/** 清单变了：体检结果作废，重新按当前参数出基础方案。 */
async function afterFilesChanged() {
  resetCheck()
  if (files.value.length === 0) {
    plan.value = null
    stepIndex.value = 0
    return
  }
  await fillPageCounts()
  stepIndex.value = 1
  await loadPlan(false)
}

async function pickDocuments() {
  if (busy.value) return
  try {
    await ensureLogin()
    const remaining = MAX_ORDER_FILES - files.value.length
    if (remaining <= 0) {
      notify(`一单最多 ${MAX_ORDER_FILES} 个文件，请先移除再选`)
      return
    }
    const picked = await chooseDocuments(Math.min(MAX_FILES_PER_PICK, remaining))
    busy.value = true
    uploading.value = true
    for (const item of picked) {
      uploadingName.value = item.name
      const uploaded = await uploadFile(item.path, item.name)
      pushFile(uploaded)
    }
    notify(`已添加 ${picked.length} 个文件`, 'success')
    await afterFilesChanged()
  } catch (error) {
    notifyError(error)
  } finally {
    busy.value = false
    uploading.value = false
  }
}

/**
 * 移除清单里的一份文件。
 *
 * 这是「还没下单的上传文件」的撤回：后端校验归属（只能删自己传的）与引用
 * （已被订单/文件版本/待确认请求引用的拒绝删除），并同时删对象与上传记录。
 * 被拒绝时也从本单移除，但把服务端的原话告诉学生，不假装删掉了。
 */
async function removeFile(index) {
  const item = files.value[index]
  if (!item || removingUid.value) return
  removingUid.value = item.uid
  try {
    await deleteUploadedFile(item.file_key)
    notify('已撤回该文件', 'success')
  } catch (error) {
    notify(`服务端未删除：${error.message}；已从本单移除，文件会在保留期后自动清理`)
    console.warn('[撤回文件]', error)
  } finally {
    files.value.splice(index, 1)
    removingUid.value = ''
    await afterFilesChanged()
  }
}

async function useDemo() {
  if (busy.value) return
  try {
    await ensureLogin()
    busy.value = true
    const data = await loadDemoDocument()
    if (!pushFile(data)) {
      notify('示例论文已经在本单里了')
      return
    }
    notify('已载入示例论文', 'success')
    await afterFilesChanged()
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
  if (checking.value || !singleFile.value || !primaryFile.value) return
  checking.value = true
  try {
    report.value = await request('/api/ai/check', {
      method: 'POST',
      data: { file_key: primaryFile.value.file_key },
    })
    aiChecked.value = true
    const checked = Math.max(1, Number(report.value.page_count) || 1)
    // 体检可能发现实际页数与 page-count 不一致，以体检为准
    primaryFile.value.page_count = checked
    selectedIssues.value = []
    wantFix.value = false
    await loadPlan(true)
  } catch (error) {
    notifyError(error)
  } finally {
    checking.value = false
  }
}

/** 明示的「跳过体检，直接下单」：不做任何体检，按基础方案下单。 */
async function skipCheckAndOrder() {
  if (busy.value || checking.value) return
  if (!plan.value) await loadPlan(false)
  if (plan.value) await createOrder()
}

async function loadPlan(useAi = false) {
  if (!primaryFile.value) return
  preparing.value = true
  try {
    const options = { copies: copies.value, use_ai: useAi }
    if (printSide.value) options.print_side = printSide.value
    if (colorMode.value === 0 || colorMode.value === 1) options.color_mode = colorMode.value
    if (layout.value) options.layout = layout.value

    plan.value = await request('/api/ai/print-plan', {
      method: 'POST',
      data: { file_key: primaryFile.value.file_key, options },
    })
    planUsesAi.value = useAi
    printSide.value = plan.value.recommend.print_side
    colorMode.value = plan.value.recommend.color_mode
    layout.value = plan.value.recommend.layout
    stepIndex.value = 2
  } catch (error) {
    notifyError(error)
  } finally {
    preparing.value = false
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
  if (busy.value || selectedIssues.value.length === 0 || !primaryFile.value) return
  busy.value = true
  try {
    const fixed = await request('/api/ai/fix', {
      method: 'POST',
      data: {
        file_key: primaryFile.value.file_key,
        issue_ids: selectedIssues.value,
        file_name: `修复版-${primaryFile.value.file_name}`,
      },
    })
    notify(`已生成修复版（${fixed.applied_actions.length} 项修改）`, 'success')
    // 修复版替掉当前这份文件：学生已经选了「修复」，没修复的原件不该混在清单里
    primaryFile.value.file_key = fixed.file_key
    primaryFile.value.file_name = fixed.file_name || primaryFile.value.file_name
    primaryFile.value.size = Number(fixed.size) || primaryFile.value.size
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
  if (busy.value || !plan.value || files.value.length === 0) return
  busy.value = true
  try {
    // 文件清单是权威字段；单文件时同时带上旧的顶层字段，老后端也能跑
    const payload = {
      files: files.value.map((item, index) => ({
        file_key: item.file_key,
        file_name: item.file_name,
        page_count: Math.max(1, Number(item.page_count) || 1),
        file_index: index + 1,
      })),
      print_side: plan.value.recommend.print_side,
      color_mode: plan.value.recommend.color_mode,
      copies: copies.value,
      paper_size: 'A4',
    }
    if (files.value.length === 1) {
      payload.file_key = files.value[0].file_key
      payload.file_name = files.value[0].file_name
      payload.page_count = Math.max(1, Number(files.value[0].page_count) || 1)
    }
    created.value = await request('/api/order/create', { method: 'POST', data: payload })
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
.file-list {
  margin-top: 18rpx;
}
.file-row {
  display: flex;
  align-items: center;
  margin-bottom: 12rpx;
  padding: 18rpx;
  border: 2rpx solid var(--zi-line);
  border-radius: var(--zi-radius);
  background: var(--zi-soft);
}
.file-main {
  flex: 1;
  min-width: 0;
}
.file-name {
  display: block;
  color: var(--zi-ink);
  font-size: 26rpx;
  font-weight: 700;
  line-height: 1.5;
  word-break: break-all;
}
.file-meta {
  display: block;
  margin-top: 4rpx;
  color: var(--zi-muted);
  font-size: 22rpx;
}
.file-remove {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 108rpx;
  min-height: 60rpx;
  margin-left: 16rpx;
  padding: 0 20rpx;
  border: 2rpx solid var(--zi-danger);
  border-radius: var(--zi-pill-radius);
  background: transparent;
  color: var(--zi-danger);
  font-size: 22rpx;
  font-weight: 800;
  line-height: 1;
  white-space: nowrap;
}
.file-remove.busy {
  opacity: 0.5;
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
/* 明示的「跳过体检，直接下单」：与体检按钮并排但用实心主色，不能再靠读者意会 */
.action.skip {
  width: 100%;
  margin: 16rpx 0 0;
  border-color: var(--zi-accent);
  background: var(--zi-accent);
  color: var(--zi-accent-ink);
}
.ai-actions {
  display: flex;
  flex-direction: column;
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
.zi-theme-classic .file-row,
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
.zi-theme-classic .action.skip {
  background: var(--zi-accent);
  color: var(--zi-accent-ink);
}
.zi-theme-classic .file-remove {
  border: none;
  background: var(--zi-danger);
  color: #fff;
  font-weight: 400;
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
