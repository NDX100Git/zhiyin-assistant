<template>
  <view class="page" :class="[uiThemeClass, uiSchemeClass]">
    <view class="steps">
      <view v-for="(item, index) in steps" :key="item.key" class="step" :class="{ active: stepIndex >= index }">
        <text class="step-index">{{ index + 1 }}</text>
        <text class="step-text">{{ item.label }}</text>
      </view>
    </view>

    <!--
      学生自己的密钥不可用：把原因和出口说清楚。
      不静默退回学校密钥——在学生自己点「清除并改用学校共享」之前，AI 请求仍带这把钥匙。
    -->
    <view v-if="keyError" class="card key-error-card">
      <text class="key-error-title">你的 AI 密钥不可用</text>
      <text class="key-error-text">{{ keyError }}</text>
      <text class="key-error-hint">去「我的」→「AI 密钥」可以清除后改用学校共享密钥。</text>
    </view>

    <view class="card">
      <view class="card-head">
        <text class="card-title">1. 选择要打印的文件</text>
        <text v-if="files.length" class="tag ok">
          {{ files.length > 1 ? `${files.length} 个文件 · 共 ${totalPages} 页` : '已选择' }}
        </text>
      </view>

      <view v-if="files.length" class="file-list">
        <view
          v-for="(item, index) in files"
          :key="item.uid"
          class="file-row"
          :data-file-index="index + 1"
        >
          <view class="file-head">
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
          <!--
            多文件时每份文件单独设单双面、颜色、纸张、份数；
            行金额与打印页数只从服务端报价取，页面不乘单价。
          -->
          <view v-if="!singleFile" class="file-params">
            <view class="file-param">
              <text class="file-param-label">单双面</text>
              <view class="seg">
                <view class="seg-item" :class="{ on: item.print_side === 1 }" @click.stop="setFileParam(index, 'print_side', 1)">单面</view>
                <view class="seg-item" :class="{ on: item.print_side === 2 }" @click.stop="setFileParam(index, 'print_side', 2)">双面</view>
              </view>
            </view>
            <view class="file-param">
              <text class="file-param-label">颜色</text>
              <view class="seg">
                <view class="seg-item" :class="{ on: item.color_mode === 0 }" @click.stop="setFileParam(index, 'color_mode', 0)">黑白</view>
                <view class="seg-item" :class="{ on: item.color_mode === 1 }" @click.stop="setFileParam(index, 'color_mode', 1)">彩色</view>
              </view>
            </view>
            <view class="file-param">
              <text class="file-param-label">纸张</text>
              <view class="seg">
                <view
                  v-for="paper in paperSizes"
                  :key="paper"
                  class="seg-item"
                  :class="{ on: item.paper_size === paper }"
                  @click.stop="setFileParam(index, 'paper_size', paper)"
                >{{ paper }}</view>
              </view>
            </view>
            <view class="file-param">
              <text class="file-param-label">份数</text>
              <view class="stepper-wrap">
                <text class="stepper" @click.stop="changeFileCopies(index, -1)">－</text>
                <text class="copy-num">{{ item.copies }}</text>
                <text class="stepper" @click.stop="changeFileCopies(index, 1)">＋</text>
              </view>
            </view>
            <view class="file-quote">
              <text class="file-print-pages">
                {{ lineOf(index) ? `打印 ${lineOf(index).print_pages} 页` : '打印页数读取中…' }}
              </text>
              <text class="file-amount">
                {{ lineOf(index) ? formatMoney(lineOf(index).amount_cents) : '金额读取中…' }}
              </text>
            </view>
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
      <!-- 学生要知道自己在用哪把钥匙：学校共享还是自己填的那把 -->
      <text class="ai-key-line" :class="{ own: keyOwn }">当前使用：{{ keyUsage }}</text>
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
      <view v-if="reportIssues.length === 0" class="empty">未发现明确问题，太棒了。</view>
      <view
        v-for="issue in reportIssues"
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

    <view v-if="plan" class="card plan-card">
      <view class="card-head">
        <text class="card-title">3. 打印方案</text>
        <text class="tag">
          {{ planUsesAi ? 'AI 优化方案' : '基础方案' }} · 共 {{ docPages }} 页{{ files.length > 1 ? ` · ${files.length} 个文件` : '' }}
        </text>
      </view>
      <text class="ai-recommend">当前方案：{{ plan.recommend.print_side === 2 ? '双面' : '单面' }} · {{ plan.recommend.color_mode === 1 ? '彩色' : '黑白' }} · {{ layoutLabel(plan.recommend.layout) }}</text>
      <!-- 单文件维持旧界面：参数在订单级设一次，提交时继承给那一份 -->
      <template v-if="singleFile">
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
      </template>
      <view v-else class="multi-tip">
        <text class="multi-tip-text">多文件逐份设置：每份的单双面、颜色、纸张、份数在上面文件清单里单独改，行金额与整单金额按服务端报价。</text>
        <view class="opt-row">
          <text class="opt-label">版式</text>
          <view class="seg">
            <view v-for="item in layouts" :key="item.value" class="seg-item" :class="{ on: layout === item.value }" @click.stop="choose('layout', item.value)">{{ item.label }}</view>
          </view>
        </view>
      </view>
      <!-- 页数与金额全部来自 /api/order/quote，页面不乘单价 -->
      <view class="quote-summary">
        <view class="quote-row">
          <text class="quote-k">文档页数</text>
          <text class="quote-v">{{ docPages }} 页</text>
        </view>
        <view class="quote-row">
          <text class="quote-k">打印页数</text>
          <text class="quote-v">{{ printPages === null ? '读取中…' : `${printPages} 页` }}</text>
        </view>
        <view class="quote-row">
          <text class="quote-k">应付金额</text>
          <text class="quote-v money">{{ formatMoney(quoteAmount) }}</text>
        </view>
      </view>
      <text v-if="quoteError" class="quote-error">
        服务端报价失败：{{ quoteError }}；上面显示的是上一次服务端算出的金额，点下面「重新报价」或改参数都会重新请求。
      </text>
      <!--
        自动重试用完后的手动出口：不加这个按钮，用户只能改参数才能重新报价，
        下单按钮会被一直禁用，看起来像「页面坏了」。
      -->
      <button
        v-if="quoteError"
        class="action retry-quote"
        :disabled="quoting"
        @click="retryQuote"
      >
        {{ quoting ? '重新报价中…' : '重新报价' }}
      </button>
      <text class="plan-reason">{{ singleFile ? 'AI 建议理由' : '方案估算依据' }}：{{ plan.reason }}</text>
      <!--
        收费按文档页数算，这里只是「预计张数」，不能写成「省多少张」当收费承诺：
        学生把省张数当成该收的钱，实际收费按页数算，对不上就会投诉。
        金额仍只取服务端报价（quoteAmount），页面不乘单价。
      -->
      <view v-if="singleFile" class="savings">
        <text class="savings-tip">纸张 {{ plan.original_sheets }} → {{ plan.sheets_needed }} 张是预计张数，实际以老师确认为准</text>
        <text class="money">服务端报价 {{ formatMoney(quoteAmount) }}</text>
      </view>
      <view v-else class="savings">
        <text>{{ files.length }} 个文件 · 文档 {{ docPages }} 页 · 打印 {{ printPages === null ? '—' : printPages }} 页</text>
        <text class="money">预计 {{ formatMoney(quoteAmount) }}</text>
      </view>
      <button class="primary block" :disabled="busy || !plan || !!quoteError || quoting || !!created" @click="createOrder">
        {{ files.length > 1 ? `确认下单（${files.length} 个文件 · ${docPages} 页）` : '确认下单' }}
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
import { computed, onUnmounted, ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { deleteUploadedFile, quoteOrder, request, uploadFile } from '@/services/api'
import { ensureLogin } from '@/services/auth'
import { MAX_FILES_PER_PICK, chooseDocuments, chooseImage, loadDemoDocument } from '@/services/filePicker'
import { isLlmKeyFailure, llmKeyFailureText, llmKeyUsageLabel } from '@/services/llmKey'
import { PAPER_SIZES } from '@/services/orderFiles'
import { notify, notifyError } from '@/services/notify'
import { useUiTheme } from '@/ui/theme'

const { uiThemeClass, uiSchemeClass } = useUiTheme()

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
 * 每个元素：
 * `{uid, file_key, file_name, size, page_count, print_side, color_mode, paper_size, copies}`。
 * uid 只给列表 key 用；四个逐份参数在这里存本地选择，提交时随清单一起交给服务端。
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

/* ---------- 学生自己的密钥：只影响提示，不影响请求（请求头由 services/api.js 统一带） ---------- */

/** 「当前使用：学校共享 / 我的密钥」那一行。 */
const keyUsage = ref(llmKeyUsageLabel())
const keyOwn = computed(() => keyUsage.value === '我的密钥')
/** 密钥不可用的原因：非空时页面顶部给一条横幅，不静默退回学校密钥。 */
const keyError = ref('')

// 学生可能在「我的」页刚换过钥匙，每次回到本页重读
onShow(() => {
  keyUsage.value = llmKeyUsageLabel()
})

/**
 * AI 请求失败时的统一出口。
 *
 * 密钥不可用就说清楚「你的密钥不可用 + 去哪儿清除」，其余错误照旧提示；
 * 两种情况都不自动重试、不自动丢密钥（那就是静默回退了）。
 */
function reportAiError(error) {
  if (isLlmKeyFailure(error)) {
    keyError.value = llmKeyFailureText(error)
    notify('你的密钥不可用，请到「我的」→「AI 密钥」清除后改用学校共享')
    return
  }
  notifyError(error)
}

/** 订单总页数：清单里各文件页数之和，下单与计费都按它算。 */
const totalPages = computed(() =>
  files.value.reduce((sum, item) => sum + (Number(item.page_count) || 0), 0)
)

/** 方案与体检都针对第一份文件取参数，多文件只借用它的推荐值。 */
const primaryFile = computed(() => files.value[0] || null)

/** 纸张规格选项：与后端 allowed_paper_sizes 同口径。 */
const paperSizes = PAPER_SIZES

/**
 * 服务端报价结果（`{files, page_count, print_page_count, amount_cents}`）。
 *
 * 行金额、文档页数、打印页数、整单总额全部取这里的数，页面不做任何乘法。
 */
const quote = ref(null)
/** 报价失败的原因：失败时保留上一次结果，并把原因写清楚。 */
const quoteError = ref('')
// 报价进行中（含自动重试）。重试间隙不能解锁下单按钮，否则会拿还没取到的金额下单。
const quoting = ref(false)
let quoteTimer = null
let quoteSeq = 0
// 报价失败最多自动重试几次：后端重启、网络抖一下都会让报价失败一次。
// 早期实现失败后只有改参数才会重新报价，等于把下单按钮永久锁死。
const MAX_QUOTE_RETRIES = 3

/** 当前报价里对应第 index 份文件的那一行（按 file_key 对，不靠下标猜）。 */
function lineOf(index) {
  const item = files.value[index]
  if (!item || !quote.value || !Array.isArray(quote.value.files)) return null
  return quote.value.files.find((row) => row.file_key === item.file_key) || null
}

/** 整单金额（分）：有报价用报价，单文件还没报价时用打印方案（也是服务端给的）。 */
const quoteAmount = computed(() => {
  if (quote.value) return Number(quote.value.amount_cents) || 0
  return plan.value ? Number(plan.value.amount_cents) || 0 : 0
})
/** 文档页数（各份页数之和）。 */
const docPages = computed(() =>
  quote.value ? Number(quote.value.page_count) || 0 : totalPages.value
)
/** 打印页数（各份 page_count × copies 之和）；还没报价时为 null，不编数字。 */
const printPages = computed(() =>
  quote.value ? Number(quote.value.print_page_count) || 0 : null
)

/**
 * 报价请求体：与下单完全同一份。
 *
 * 单文件不传逐份参数，由订单级参数继承（旧流程不变）；多文件逐份传自己的四项。
 */
function quotePayload() {
  const multi = files.value.length > 1
  return {
    files: files.value.map((item, index) => {
      const base = {
        file_key: item.file_key,
        file_name: item.file_name,
        page_count: Math.max(1, Number(item.page_count) || 1),
        file_index: index + 1,
      }
      if (!multi) return base
      return {
        ...base,
        print_side: item.print_side,
        color_mode: item.color_mode,
        paper_size: item.paper_size,
        copies: item.copies,
      }
    }),
    print_side: printSide.value === 1 ? 1 : 2,
    color_mode: colorMode.value === 1 ? 1 : 0,
    copies: Math.max(1, Number(copies.value) || 1),
    paper_size: 'A4',
  }
}

/**
 * 改参数后 300 ms 内只发一次报价。
 *
 * 连点单双面、加减份数会产生很多次变更，逐次请求既浪费也会让界面抢屏。
 */
function scheduleQuote() {
  if (quoteTimer) clearTimeout(quoteTimer)
  quoteTimer = setTimeout(() => {
    quoteTimer = null
    refreshQuote()
  }, 300)
}

/** 调服务端报价；失败时保留上一次结果并记下原因，不静默显示错误金额。 */
async function refreshQuote(retry = 0) {
  if (!files.value.length) {
    quote.value = null
    quoteError.value = ''
    return
  }
  // 页数还没读到就报不了价（page_count 必须 ≥ 1）。这里不能静默 return：
  // 按钮会一直是灰的，用户完全不知道在等什么。也要补一句后端还会按真实页数核对，
  // 前端这个 1 页兜底不会真的少付钱。
  if (files.value.some((item) => !(Number(item.page_count) >= 1))) {
    quote.value = null
    quoteError.value = '正在读取文件页数，请稍候；长时间没有结果请删掉该文件重新上传'
    quoting.value = false
    return
  }
  const seq = ++quoteSeq
  quoting.value = true
  try {
    const data = await quoteOrder(quotePayload())
    // 已经有更新的请求发出去了，旧结果直接丢掉
    if (seq !== quoteSeq) return
    quote.value = data
    quoteError.value = ''
  } catch (error) {
    if (seq !== quoteSeq) return
    if (retry < MAX_QUOTE_RETRIES) {
      // 退避重试；用户改参数时 scheduleQuote 会清掉这个定时器，等于取消重试
      quoteTimer = setTimeout(() => {
        quoteTimer = null
        refreshQuote(retry + 1)
      }, 1000 * (retry + 1))
      return
    }
    quoteError.value = error.message || '报价失败，请稍后重试'
  } finally {
    if (seq === quoteSeq && !quoteTimer) quoting.value = false
  }
}

/**
 * 手动重新报价。
 *
 * 自动重试 3 次用完后 quoteError 会一直留着，下单按钮随之永久禁用;
 * 早期实现里用户只能改参数才能重新报价。这里给一个明确出口：
 * 点一下立刻清掉错误（同时解锁下单按钮）并重新请求，失败会把错误写回来。
 */
async function retryQuote() {
  if (quoting.value || !files.value.length) return
  if (quoteTimer) {
    clearTimeout(quoteTimer)
    quoteTimer = null
  }
  quoteError.value = ''
  await refreshQuote(0)
}

const severitySummary = computed(() => {
  const counts = (report.value && report.value.by_severity) || {}
  return `高 ${counts['高'] || 0} · 中 ${counts['中'] || 0} · 低 ${counts['低'] || 0}`
})
/** AI 降级或超时返回的 report 可能没有 issues 字段，模板直接取 .length 会整页渲染失败。 */
const reportIssues = computed(() => {
  const issues = report.value && report.value.issues
  return Array.isArray(issues) ? issues : []
})
const highIssueIds = computed(() =>
  reportIssues.value.filter((issue) => issue.severity === 'high').map((issue) => issue.id)
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

/** 新加入的一份文件：逐份参数先取当前订单级的值，之后各改各的。 */
function defaultFileParams() {
  return {
    print_side: printSide.value === 1 ? 1 : 2,
    color_mode: colorMode.value === 1 ? 1 : 0,
    paper_size: 'A4',
    copies: Math.max(1, Number(copies.value) || 1),
  }
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
    ...defaultFileParams(),
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
      const pages = Number(result.page_count)
      // 不用「1 页」兜底：解析失败时假装 1 页，报价就按 1 页算，
      // 学生以为很便宜、下单后被后端按真实页数驳回。留 0 让报价停在
      // 「正在读取页数」的提示上，用户能看出是文件的问题
      item.page_count = pages >= 1 ? Math.floor(pages) : 0
    } catch (error) {
      notifyError(error)
    }
  }
}

/** 清单变了：体检结果作废，重新按当前参数出基础方案，并重新报价。 */
async function afterFilesChanged() {
  resetCheck()
  if (files.value.length === 0) {
    plan.value = null
    quote.value = null
    quoteError.value = ''
    stepIndex.value = 0
    return
  }
  await fillPageCounts()
  stepIndex.value = 1
  await loadPlan(false)
  scheduleQuote()
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
    keyError.value = ''
    notify('识别完成', 'success')
  } catch (error) {
    reportAiError(error)
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
    keyError.value = ''
    const checked = Math.max(1, Number(report.value.page_count) || 1)
    // 体检可能发现实际页数与 page-count 不一致，以体检为准
    primaryFile.value.page_count = checked
    selectedIssues.value = []
    wantFix.value = false
    await loadPlan(true)
    scheduleQuote()
  } catch (error) {
    reportAiError(error)
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
    // 方案拿到了 = 这把密钥这次没被拒，之前那条失效横幅已经过期
    keyError.value = ''
    printSide.value = plan.value.recommend.print_side
    colorMode.value = plan.value.recommend.color_mode
    layout.value = plan.value.recommend.layout
    stepIndex.value = 2
  } catch (error) {
    reportAiError(error)
  } finally {
    preparing.value = false
  }
}

async function choose(field, value) {
  if (field === 'print_side') printSide.value = value
  if (field === 'color_mode') colorMode.value = value
  if (field === 'layout') layout.value = value
  await loadPlan(planUsesAi.value)
  // 单文件的金额靠订单级参数算，改完重算报价
  scheduleQuote()
}

/** 多文件时某一份的单双面 / 颜色 / 纸张：只改这一份，然后重算整单报价。 */
function setFileParam(index, field, value) {
  const item = files.value[index]
  if (!item || item[field] === value) return
  item[field] = value
  scheduleQuote()
}

/** 多文件时某一份的份数加减：1..50，与该份的份数限制一致。 */
function changeFileCopies(index, delta) {
  const item = files.value[index]
  if (!item) return
  const next = Math.max(1, Math.min(50, (Number(item.copies) || 1) + delta))
  if (next === item.copies) return
  item.copies = next
  scheduleQuote()
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
    reportAiError(error)
  } finally {
    busy.value = false
  }
}

async function changeCopies(delta) {
  const next = Math.max(1, Math.min(50, copies.value + delta))
  if (next === copies.value) return
  copies.value = next
  await loadPlan(planUsesAi.value)
  scheduleQuote()
}

async function createOrder() {
  // created 非空表示这一单已经下过了：不清空 files/plan 的情况下连点会出第二张订单
  if (busy.value || !plan.value || files.value.length === 0 || created.value) return
  busy.value = true
  try {
    // 文件清单是权威字段；逐份参数随清单一起交给服务端（与报价接口同一份参数）
    const payload = quotePayload()
    if (files.value.length === 1) {
      // 单文件同时带上旧的顶层字段，老后端也能跑
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

// 页面关掉时把未发出的报价防抖请求取消掉
onUnmounted(() => {
  if (quoteTimer) clearTimeout(quoteTimer)
  quoteTimer = null
})
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
/* 「当前使用：学校共享 / 我的密钥」一行小字 */
.ai-key-line {
  display: block;
  margin-top: 10rpx;
  color: var(--zi-muted);
  font-size: 22rpx;
  font-weight: 700;
  line-height: 1.5;
  word-break: break-all;
}
.ai-key-line.own {
  color: var(--zi-accent);
}
/* 密钥不可用的横幅：说清原因与出口 */
.key-error-card {
  border-left: 6rpx solid var(--zi-danger);
  background: var(--zi-danger-bg);
}
.key-error-title {
  display: block;
  color: var(--zi-danger);
  font-size: 26rpx;
  font-weight: 900;
  line-height: 1.5;
}
.key-error-text {
  display: block;
  margin-top: 8rpx;
  color: var(--zi-danger);
  font-size: 24rpx;
  line-height: 1.6;
  word-break: break-all;
}
.key-error-hint {
  display: block;
  margin-top: 8rpx;
  color: var(--zi-ink);
  font-size: 22rpx;
  line-height: 1.6;
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
  flex-direction: column;
  margin-bottom: 12rpx;
  padding: 18rpx;
  border: 2rpx solid var(--zi-line);
  border-radius: var(--zi-radius);
  background: var(--zi-soft);
}
.file-head {
  display: flex;
  align-items: center;
}
/* 逐份参数：每份文件自己一块，宽度不够时换行，390 宽不横向溢出 */
.file-params {
  display: flex;
  flex-wrap: wrap;
  gap: 10rpx 16rpx;
  margin-top: 14rpx;
  padding-top: 12rpx;
  border-top: 2rpx dashed var(--zi-line);
}
.file-param {
  display: flex;
  align-items: center;
  flex: 1 1 100%;
}
.file-param-label {
  flex: 0 0 96rpx;
  color: var(--zi-muted);
  font-size: 22rpx;
  font-weight: 700;
}
.file-param .seg {
  flex: 1;
}
.file-param .seg-item {
  font-size: 22rpx;
  padding: 8rpx 0;
}
.file-quote {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex: 1 1 100%;
  margin-top: 4rpx;
  padding-top: 10rpx;
  border-top: 2rpx solid var(--zi-line);
}
.file-print-pages {
  color: var(--zi-muted);
  font-size: 22rpx;
  font-weight: 700;
}
.file-amount {
  color: var(--zi-ink);
  font-size: 26rpx;
  font-weight: 900;
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
/* 服务端报价汇总：文档页数 / 打印页数 / 应付金额 */
.quote-summary {
  margin-top: 18rpx;
  padding: 16rpx 18rpx;
  border: 2rpx solid var(--zi-line-strong);
  border-radius: var(--zi-radius);
  background: var(--zi-soft-2);
}
.quote-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6rpx 0;
}
.quote-k {
  color: var(--zi-muted);
  font-size: 24rpx;
  font-weight: 700;
}
.quote-v {
  color: var(--zi-ink);
  font-size: 26rpx;
  font-weight: 900;
}
.quote-v.money {
  font-size: 32rpx;
}
.quote-error {
  display: block;
  margin-top: 12rpx;
  color: var(--zi-danger);
  font-size: 22rpx;
  font-weight: 700;
  line-height: 1.6;
}
/* 手动重新报价：报价失败后唯一的出口，禁用时也不要看起来像主按钮 */
.action.retry-quote {
  width: 100%;
  margin: 12rpx 0 0;
  border-color: var(--zi-danger);
  color: var(--zi-danger);
}
.action.retry-quote[disabled] {
  opacity: 0.5;
}
/* 多文件时参数逐份设置，订单级只留版式 */
.multi-tip {
  margin-top: 14rpx;
}
.multi-tip-text {
  display: block;
  padding: 14rpx 16rpx;
  border: 2rpx dashed var(--zi-line-strong);
  border-radius: var(--zi-radius);
  color: var(--zi-muted);
  font-size: 22rpx;
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
/* 预计张数说明：一行读完，不再单独放大「省 N 张」 */
.savings-tip {
  flex: 1 1 auto;
  line-height: 1.5;
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
  background: var(--zi-danger-solid);
  color: var(--zi-on-solid);
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
