<template>
  <div class="dashboard" :class="{ 'is-mobile': isMobile }">
    <div class="stats">
      <div class="stat">
        <div class="stat-label">今日接单</div>
        <div class="stat-value">{{ stats.order_count }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">今日收款</div>
        <div class="stat-value money">{{ yuan(stats.income_cents) }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">待取件</div>
        <div class="stat-value">{{ stats.ready_count }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">异常单</div>
        <div class="stat-value danger">{{ stats.abnormal_count }}</div>
      </div>
    </div>

    <div class="page-card toolbar" :class="{ stacked: isMobile }">
      <div class="left">
        <el-select v-model="filters.status" placeholder="全部状态" clearable style="width: 140px">
          <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-input
          v-model="filters.keyword"
          placeholder="订单号 / 取件码 / 文件名"
          clearable
          style="width: 240px"
          @keyup.enter="refreshAll"
        />
        <el-button type="primary" @click="refreshAll">查询</el-button>
        <el-button :loading="loading" @click="refreshAll">刷新</el-button>
      </div>
      <div class="right">
        <el-input
          v-model="verifyCodeValue"
          placeholder="取件码：扫码用完整码，手输当天短码如 0-01"
          style="width: 300px"
          clearable
          @keyup.enter="submitVerifyCode"
        />
        <el-button type="success" @click="submitVerifyCode">核销取件</el-button>
      </div>
    </div>

    <div class="page-card table-card">
      <div class="table-head">
        <div>接单队列 <span class="muted">（每 5 秒自动刷新，确认换文件后该单排到队尾）</span></div>
        <div>
          <el-button
            type="warning"
            :disabled="selected.length === 0"
            @click="submitBatchComplete"
          >
            批量完成（{{ selected.length }}）
          </el-button>
        </div>
      </div>

      <el-table
        v-if="!isMobile"
        v-loading="loading"
        :data="orders"
        row-key="id"
        border
        stripe
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="46" />
        <el-table-column prop="order_no" label="订单号" width="140" />
        <el-table-column prop="pickup_code" label="取件码" width="130" class-name="pickup-cell">
          <template #default="{ row }">
            <el-tag effect="plain">{{ row.pickup_code }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="文件" min-width="250">
          <template #default="{ row }">
            <div class="file-cell">
              <div v-for="file in fileRows(row).lines" :key="file.key" class="file-line">
                <span v-if="file.label" class="file-seq">#{{ file.seq }}</span>
                <span class="file-name" :title="file.file_name">{{ file.file_name }}</span>
                <span class="file-pages muted">{{ file.page_count ? file.page_count + ' 页' : '' }}</span>
                <el-button link type="primary" class="file-dl" @click="downloadOne(row, file)">
                  下载
                </el-button>
              </div>
              <!-- 超过三份时给明确入口：点开逐份列出，每份都能单独下载 -->
              <div v-if="fileRows(row).rest > 0" class="file-more">
                <span class="muted">还有 {{ fileRows(row).rest }} 份未展开</span>
                <el-button link type="primary" class="file-more-btn" @click="openFiles(row)">
                  查看全部 {{ fileRows(row).total }} 个文件
                </el-button>
              </div>
              <div v-if="fileRows(row).total === 0" class="muted">{{ fileFormatter(row) }}</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="打印参数" width="150">
          <template #default="{ row }">
            <div>
              {{ row.print_side === 2 ? '双面' : '单面' }} ·
              {{ row.color_mode === 1 ? '彩色' : '黑白' }} ·
              {{ row.copies }} 份
            </div>
            <!-- 页数列并进来了：多文件时整单页数是各份之和，老师看这一行就够 -->
            <div class="muted">共 {{ totalPages(row) || row.page_count }} 页</div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" effect="light">{{ row.status_label }}</el-tag>
            <div v-if="row.file_change" class="status-flag">
              <el-tag type="warning" effect="dark" size="small">文件待确认</el-tag>
            </div>
            <div v-if="hasUnseenChange(row)" class="status-flag">
              <el-tag
                type="warning"
                effect="light"
                size="small"
                class="flag-tag"
                title="文件已替换，请重新下载后再打印"
              >
                已替换
              </el-tag>
              <el-button link type="primary" class="flag-link" @click="markSeen(row)">
                标记已看到
              </el-button>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="金额" width="90">
          <template #default="{ row }">
            <span class="money">{{ yuan(row.amount_cents) }}</span>
            <div v-if="row.paid" class="muted">已支付</div>
            <div v-else class="muted">待收款</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.file_change"
              link
              type="warning"
              @click="openFileChange(row)"
            >
              确认换文件
            </el-button>
            <el-button link type="primary" @click="download(row)">下载</el-button>
            <el-button v-if="row.status === 'queuing'" link type="primary" @click="start(row)">
              开始打印
            </el-button>
            <el-button v-if="row.status === 'printing'" link type="success" @click="openComplete(row)">
              打印完成
            </el-button>
            <el-button v-if="row.status === 'ready'" link type="success" @click="openVerify(row)">
              核销
            </el-button>
            <el-button
              v-if="['queuing', 'printing', 'ready'].includes(row.status)"
              link
              type="warning"
              @click="openAbnormal(row)"
            >
              异常
            </el-button>
            <el-button v-if="row.status === 'abnormal'" link type="primary" @click="recover(row)">
              恢复排队
            </el-button>
            <el-button v-if="row.status === 'abnormal'" link type="danger" @click="closeAbnormal(row)">
              关闭异常单
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 手机上八列宽表会被压扁，改成一条订单一张卡，取件码和按钮优先 -->
      <div v-else class="order-cards" v-loading="loading">
        <div v-for="row in orders" :key="row.id" class="order-card">
          <div class="oc-head">
            <span class="oc-code">{{ row.pickup_code }}</span>
            <div class="oc-tags">
              <el-tag v-if="row.file_change" type="warning" effect="dark" size="small">
                文件待确认
              </el-tag>
              <el-tag v-if="hasUnseenChange(row)" type="warning" effect="light" size="small">
                文件已替换，待下载确认
              </el-tag>
              <el-tag :type="statusType(row.status)" effect="light">{{ row.status_label }}</el-tag>
            </div>
          </div>
          <div v-if="hasUnseenChange(row)" class="oc-flag-action">
            <el-button link type="primary" @click="markSeen(row)">标记已看到</el-button>
          </div>
          <div class="oc-line muted">{{ row.order_no }} · {{ fileSummary(row) }}</div>
          <!-- 多文件订单一单一个取件码，手机上必须逐条列清文件名与页数 -->
          <div v-if="fileRows(row).total > 0" class="oc-files">
            <div
              v-for="(file, index) in orderFiles(row)"
              :key="file.id ?? index"
              class="oc-file"
            >
              <div class="oc-file-main">
                <span v-if="isMultiFile(row)" class="oc-file-seq">第 {{ file.seq }} 个文件</span>
                <span class="oc-file-name">{{ file.file_name }}</span>
                <span class="muted">{{ file.page_count ? file.page_count + ' 页' : '页数未知' }}</span>
              </div>
              <el-button size="large" class="oc-file-dl" @click="downloadOne(row, file)">
                下载
              </el-button>
            </div>
          </div>
          <div class="oc-line">
            {{ totalPages(row) || row.page_count }} 页 · {{ row.print_side === 2 ? '双面' : '单面' }} ·
            {{ row.color_mode === 1 ? '彩色' : '黑白' }} · {{ row.copies }} 份
          </div>
          <div class="oc-line">
            <span class="money">{{ yuan(row.amount_cents) }}</span>
            <span class="muted">{{ row.paid ? '已支付' : '待收款' }}</span>
          </div>
          <div v-if="row.file_change" class="oc-change">
            <span class="oc-change-title">新文件待确认</span>
            <span>{{ row.file_change.new_file_name || '未命名' }}</span>
            <span>
              {{ row.file_change.new_page_count }} 页 ·
              {{ yuan(row.file_change.new_amount_cents) }}
            </span>
            <span v-if="row.file_change.reason">原因：{{ row.file_change.reason }}</span>
          </div>
          <div class="oc-actions">
            <el-button v-if="row.file_change" size="large" type="warning" @click="openFileChange(row)">
              确认换文件
            </el-button>
            <el-button size="large" @click="download(row)">下载</el-button>
            <el-button v-if="row.status === 'queuing'" size="large" type="primary" @click="start(row)">
              开始打印
            </el-button>
            <el-button v-if="row.status === 'printing'" size="large" type="success" @click="openComplete(row)">
              打印完成
            </el-button>
            <el-button v-if="row.status === 'ready'" size="large" type="success" @click="openVerify(row)">
              核销
            </el-button>
            <el-button
              v-if="['queuing', 'printing', 'ready'].includes(row.status)"
              size="large"
              type="warning"
              @click="openAbnormal(row)"
            >
              异常
            </el-button>
            <el-button v-if="row.status === 'abnormal'" size="large" type="primary" @click="recover(row)">
              恢复排队
            </el-button>
            <el-button v-if="row.status === 'abnormal'" size="large" type="danger" @click="closeAbnormal(row)">
              关闭异常单
            </el-button>
          </div>
        </div>
        <el-empty v-if="!loading && orders.length === 0" description="没有订单" />
      </div>
    </div>

    <el-dialog
      v-model="fileChangeDialog.visible"
      title="确认换文件"
      class="file-change-dialog"
      :width="isMobile ? '92%' : '520px'"
    >
      <div v-if="fileChangeDialog.order && fileChangeDialog.change">
        <!-- 多文件订单先点明这次换的是哪一份，再给整单文件清单概览 -->
        <div v-if="isMultiFile(fileChangeDialog.order)" class="change-target">
          <div class="change-target-title">
            本次换的是{{ changeTarget(fileChangeDialog.order).label }}：{{ changeTarget(fileChangeDialog.order).file_name }}
          </div>
          <div class="change-target-list">
            <div
              v-for="(file, index) in changeFileList(fileChangeDialog.order)"
              :key="file.id ?? index"
              class="change-target-file"
              :class="{ current: file.current }"
            >
              第 {{ file.seq }} 个文件 · {{ file.file_name }} ·
              {{ file.page_count ? file.page_count + ' 页' : '页数未知' }}
              <span v-if="file.current" class="change-target-mark">本次更换</span>
            </div>
          </div>
        </div>
        <el-descriptions :column="1" size="small" label-width="104" border>
          <el-descriptions-item label="订单号">
            {{ fileChangeDialog.order.order_no }}
          </el-descriptions-item>
          <!-- 这一份自己的新旧对照：老师要看的是被换掉的那份，不是整单一句话 -->
          <el-descriptions-item label="原文件（这份）">
            {{ fileChangeDialog.change.old_file_name || changeTarget(fileChangeDialog.order).file_name }} ·
            {{ fileOldPages }} 页 ·
            {{ yuan(fileOldAmount) }}
          </el-descriptions-item>
          <el-descriptions-item label="新文件（这份）">
            {{ fileChangeDialog.change.new_file_name || '未命名' }} ·
            {{ fileNewPages }} 页 ·
            {{ yuan(fileNewAmount) }}
          </el-descriptions-item>
          <el-descriptions-item label="整单页数">
            {{ fileChangeDialog.change.old_page_count ?? fileChangeDialog.order.page_count }} 页
            <span class="muted">→</span>
            {{ fileChangeDialog.change.new_page_count }} 页
          </el-descriptions-item>
          <el-descriptions-item label="系统重算价">
            <span class="money">{{ yuan(fileChangeDialog.change.new_amount_cents) }}</span>
            <span class="muted">
              （整单原价
              {{ yuan(fileChangeDialog.change.old_amount_cents ?? fileChangeDialog.order.amount_cents) }}）
            </span>
          </el-descriptions-item>
          <el-descriptions-item v-if="fileChangeDialog.change.reason" label="学生原因">
            {{ fileChangeDialog.change.reason }}
          </el-descriptions-item>
          <el-descriptions-item label="提交时间">
            {{ formatTime(fileChangeDialog.change.submitted_at) }}
          </el-descriptions-item>
        </el-descriptions>
        <div class="dialog-tip">
          通过后按下面的最终价落库，订单排到队尾重新排队，取件码不变；异常单同时恢复为排队中。
          新价高于已付时差额在取件时现场收取，低于已付时多付部分不退。
        </div>
        <el-input-number
          v-model="fileChangeDialog.yuan"
          :min="0"
          :precision="2"
          :step="5"
          style="width: 100%"
        />
        <div class="dialog-tip">最终价（元），提交时转换为分</div>
      </div>
      <template #footer>
        <el-button @click="fileChangeDialog.visible = false">取消</el-button>
        <el-button
          type="danger"
          :loading="fileChangeDialog.loading"
          @click="submitFileChange(false)"
        >
          驳回
        </el-button>
        <el-button
          type="primary"
          :loading="fileChangeDialog.loading"
          @click="submitFileChange(true)"
        >
          通过
        </el-button>
      </template>
    </el-dialog>

    <!-- 桌面文件列只展开前三份，其余在这里逐份列出并下载 -->
    <el-dialog
      v-model="filesDialog.visible"
      title="订单文件"
      class="files-dialog"
      :width="isMobile ? '92%' : '560px'"
    >
      <div v-if="filesDialog.order" class="files-dialog-body">
        <div class="files-dialog-meta">
          {{ filesDialog.order.order_no }} · {{ filesDialog.order.pickup_code }} ·
          {{ filesDialog.files.length }} 份文件 · 共 {{ totalPages(filesDialog.order) }} 页
        </div>
        <div v-for="file in filesDialog.files" :key="file.seq" class="files-dialog-row">
          <span class="files-dialog-seq">#{{ file.seq }}</span>
          <div class="files-dialog-main">
            <span class="files-dialog-name">{{ file.file_name }}</span>
            <span class="muted">{{ file.page_count ? file.page_count + ' 页' : '页数未知' }}</span>
          </div>
          <el-button
            size="large"
            type="primary"
            plain
            class="files-dialog-dl"
            @click="downloadOne(filesDialog.order, file)"
          >
            下载第 {{ file.seq }} 份
          </el-button>
        </div>
      </div>
      <template #footer>
        <el-button @click="filesDialog.visible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="completeDialog.visible" title="打印完成" :width="isMobile ? '92%' : '420px'">
      <div v-if="completeDialog.order">
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item label="订单号">{{ completeDialog.order.order_no }}</el-descriptions-item>
          <el-descriptions-item label="系统报价">
            {{ yuan(completeDialog.systemCents) }}
          </el-descriptions-item>
          <el-descriptions-item label="页数/份数">
            {{ completeDialog.order.page_count }} 页 × {{ completeDialog.order.copies }} 份
          </el-descriptions-item>
        </el-descriptions>
        <div class="dialog-tip">老师可按特殊情况修改金额（加急、装订、特殊纸张等）。</div>
        <el-input-number
          v-model="completeDialog.yuan"
          :min="0"
          :precision="2"
          :step="0.5"
          style="width: 100%"
        />
        <div class="dialog-tip">单位：元（提交时转换为分）</div>
      </div>
      <template #footer>
        <el-button @click="completeDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="completeDialog.loading" @click="submitComplete">
          确认完成
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="verifyDialog.visible" title="核销取件" :width="isMobile ? '92%' : '420px'">
      <div v-if="verifyDialog.order">
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item label="订单号">{{ verifyDialog.order.order_no }}</el-descriptions-item>
          <el-descriptions-item label="取件码">
            <el-tag type="success">{{ verifyDialog.order.pickup_code }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="金额">
            {{ yuan(verifyDialog.order.amount_cents) }}
          </el-descriptions-item>
          <el-descriptions-item label="支付状态">
            {{ verifyDialog.order.paid ? '已在线支付' : '未支付，核销时现场收款' }}
          </el-descriptions-item>
        </el-descriptions>
      </div>
      <template #footer>
        <el-button @click="verifyDialog.visible = false">取消</el-button>
        <el-button type="success" :loading="verifyDialog.loading" @click="submitVerify">
          确认核销
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="abnormalDialog.visible" title="标记异常" :width="isMobile ? '92%' : '420px'">
      <el-input
        v-model="abnormalDialog.reason"
        type="textarea"
        :rows="3"
        placeholder="请填写原因，如：卡纸、缺墨、文件损坏、学生有异议"
      />
      <template #footer>
        <el-button @click="abnormalDialog.visible = false">取消</el-button>
        <el-button type="warning" :loading="abnormalDialog.loading" @click="submitAbnormal">
          确认标记
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useIsMobile } from '../composables/useIsMobile'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  batchComplete,
  cancelAbnormal as cancelAbnormalApi,
  completePrint,
  confirmFileChange,
  confirmFileSeen,
  downloadFileUrl,
  fetchQueue,
  fetchQuote,
  fetchSummary,
  markAbnormal,
  recoverOrder,
  startPrint,
  verifyByCode,
  verifyOrder,
} from '../api/admin'
import {
  changeTarget,
  fileSummary,
  hasUnseenChange,
  isMultiFile,
  orderFiles,
  totalPages,
} from '../api/orderFiles'

const statusOptions = [
  { label: '待支付', value: 'pending_pay' },
  { label: '排队中', value: 'queuing' },
  { label: '打印中', value: 'printing' },
  { label: '待取件', value: 'ready' },
  { label: '已完成', value: 'completed' },
  { label: '异常', value: 'abnormal' },
  { label: '已取消', value: 'cancelled' },
]

const stats = reactive({
  order_count: 0,
  income_cents: 0,
  ready_count: 0,
  abnormal_count: 0,
})
const isMobile = useIsMobile()
const orders = ref([])

/** 桌面表格文件列最多显示几行，其余用「等 N 个文件」收口。 */
const MAX_FILE_LINES = 3

/** 桌面文件列的行数据：文件名 + 多文件时的「第 N 个文件」序号。 */
function fileRows(row) {
  const files = orderFiles(row)
  const multi = files.length > 1
  return {
    lines: files.slice(0, MAX_FILE_LINES).map((file, index) => ({
      ...file,
      key: file.id ?? `${file.seq}-${index}`,
      label: multi ? `第 ${file.seq} 个文件` : '',
    })),
    rest: Math.max(files.length - MAX_FILE_LINES, 0),
    total: files.length,
  }
}

/** 换文件弹窗里的文件清单，标出本次更换的那一份。 */
function changeFileList(row) {
  const target = changeTarget(row)
  const isSame = (file) =>
    target.id !== null && file.id !== null
      ? String(file.id) === String(target.id)
      : file.seq === target.seq
  return orderFiles(row).map((file) => ({
    ...file,
    current: isSame(file),
  }))
}

/** 单文件文件名（走归一化模块），无清单老订单显示与原来一致。 */
function fileFormatter(row) {
  const files = orderFiles(row)
  if (files.length === 0) return '论文文档'
  if (files.length === 1) return files[0].file_name
  return `${files[0].file_name} 等 ${files.length} 个文件`
}

/** 换文件针对的那份文件；对不上时退回第一份，用于「先下载」。 */
function targetFile(row) {
  const target = changeTarget(row)
  const files = orderFiles(row)
  if (target.id !== null && target.id !== undefined) {
    const byId = files.find((file) => String(file.id) === String(target.id))
    if (byId) return byId
  }
  return files.find((file) => file.seq === target.seq) || files[0] || null
}

const loading = ref(false)
const selected = ref([])
const verifyCodeValue = ref('')
const filters = reactive({ status: '', keyword: '' })

const completeDialog = reactive({
  visible: false,
  order: null,
  systemCents: 0,
  yuan: 0,
  loading: false,
})
const verifyDialog = reactive({ visible: false, order: null, loading: false })
const abnormalDialog = reactive({ visible: false, order: null, reason: '', loading: false })
/** 换文件确认弹窗：change 是队列项里的 file_change，未确认前一直跟着订单走。 */
const fileChangeDialog = reactive({
  visible: false,
  order: null,
  change: null,
  yuan: 0,
  loading: false,
})
/** 订单全部文件弹窗：桌面文件列放不下时逐份列出，每份都有自己的下载按钮。 */
const filesDialog = reactive({ visible: false, order: null, files: [] })

/**
 * 弹窗里「这一份文件」的新旧页数与行金额。
 *
 * 后端给了 `*_file_*` 就用它（多文件时这才是被替换那一份自己的数），
 * 老数据没有这些字段时退回整单值，口径与单文件时代一致。
 */
const fileOldPages = computed(() => {
  const change = fileChangeDialog.change || {}
  return change.old_file_page_count ?? changeTarget(fileChangeDialog.order || {}).page_count ?? 0
})
const fileNewPages = computed(() => {
  const change = fileChangeDialog.change || {}
  return change.new_file_page_count ?? change.new_page_count ?? 0
})
const fileOldAmount = computed(() => {
  const change = fileChangeDialog.change || {}
  return change.old_file_amount_cents ?? change.old_amount_cents ?? 0
})
const fileNewAmount = computed(() => {
  const change = fileChangeDialog.change || {}
  return change.new_file_amount_cents ?? change.new_amount_cents ?? 0
})

let timer = null

function yuan(cents) {
  return `¥${((Number(cents) || 0) / 100).toFixed(2)}`
}

/** 后端时间串带 T，表格与弹窗里统一换成 年-月-日 时:分。 */
function formatTime(value) {
  if (!value) return ''
  return String(value).replace('T', ' ').slice(0, 16)
}

function statusType(status) {
  return (
    {
      pending_pay: 'info',
      queuing: 'primary',
      printing: 'warning',
      ready: 'warning',
      completed: 'success',
      abnormal: 'danger',
      cancelled: 'info',
    }[status] || 'info'
  )
}

function onSelectionChange(rows) {
  selected.value = rows
}

async function loadSummary() {
  try {
    Object.assign(stats, await fetchSummary())
  } catch (error) {
    /* 拦截器已提示 */
  }
}

async function loadQueue() {
  loading.value = true
  try {
    const params = { page: 1, page_size: 200 }
    if (filters.status) params.status = filters.status
    if (filters.keyword) params.keyword = filters.keyword.trim()
    const data = await fetchQueue(params)
    orders.value = data.items || []
  } catch (error) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

async function refreshAll() {
  await Promise.all([loadSummary(), loadQueue()])
}

function startPolling() {
  stopPolling()
  timer = setInterval(() => {
    if (!document.hidden) {
      refreshAll()
    }
  }, 5000)
}

function stopPolling() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

function handleVisibilityChange() {
  if (!document.hidden) refreshAll()
}

onMounted(() => {
  refreshAll()
  startPolling()
  document.addEventListener('visibilitychange', handleVisibilityChange)
})
onUnmounted(() => {
  stopPolling()
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})

/**
 * 下载单份文件。
 *
 * fileIndex 取文件自己的序号（后端 file_index，清单缺字段时按顺序补），
 * 不能拿行标识顶替：现在两者刚好相等，字段一改就会下错文件。
 * 不传 file 时按第 1 份处理，老订单行为不变。
 */
async function downloadOne(row, file) {
  const fileIndex = file && Number(file.seq) > 0 ? Number(file.seq) : undefined
  try {
    const data = await downloadFileUrl(row.id, fileIndex)
    if (data && data.signed_url) {
      window.open(data.signed_url, '_blank')
    } else {
      ElMessage.warning('未取到下载地址，请稍后重试')
    }
  } catch (error) {
    // 拦截器已提示，下载失败不往下走确认流程
    return
  }
  // 确认口径保持订单级：队列只给出「文件已替换、老师还没确认」这一个整单标记，
  // 不告诉你具体换的是哪一份；没换过文件的订单只下载，不调确认接口，
  // 避免下载把无关订单的标记状态搞乱。
  if (!hasUnseenChange(row)) return
  // 接口失败只提示，不阻断已完成的下载
  try {
    await confirmFileSeen(row.id)
    markRowSeen(row)
    ElMessage.success('已确认看到新文件')
    await refreshAll()
  } catch (error) {
    /* 拦截器已提示 */
  }
}

/** 打开「全部文件」弹窗：清单从当前行重新归一化，刷新后也不会拿旧数据。 */
function openFiles(row) {
  filesDialog.order = row
  filesDialog.files = orderFiles(row)
  filesDialog.visible = true
}

/**
 * 把这一行就地标成「已确认看到」，不等下一次拉队列。
 *
 * 老师刚点完下载就该看到标记消失；只靠刷新会有一拍延迟，队列没刷到时
 * 标记还留在屏幕上，容易让人以为没确认成功。
 */
function markRowSeen(row) {
  row.files_unconfirmed = false
  row.files_seen_at = new Date().toISOString()
}

/** 操作列的整单下载：等同于下载第 1 份。 */
async function download(row) {
  await downloadOne(row, orderFiles(row)[0] || null)
}

/** 标记已看到新文件：调确认接口后刷新队列。 */
async function markSeen(row) {
  try {
    await confirmFileSeen(row.id)
    markRowSeen(row)
    ElMessage.success('已确认看到新文件')
    await refreshAll()
  } catch (error) {
    /* 拦截器已提示 */
  }
}

async function start(row) {
  if (hasUnseenChange(row)) {
    // 换过文件：先提醒老师本地可能还是旧版本
    try {
      await ElMessageBox.confirm(
        '这单换过文件，你本地可能还是旧版本，建议先重新下载再打印。',
        '文件已替换',
        { confirmButtonText: '仍然开始', cancelButtonText: '先下载', type: 'warning' },
      )
    } catch (action) {
      if (action === 'cancel') {
        await downloadOne(row, targetFile(row))
      }
      return
    }
    await startPrint(row.id)
    ElMessage.success('已开始打印')
    await refreshAll()
    return
  }
  await ElMessageBox.confirm(`确认开始打印订单 ${row.order_no}？`, '开始打印', {
    confirmButtonText: '开始',
    cancelButtonText: '取消',
  })
  await startPrint(row.id)
  ElMessage.success('已开始打印')
  refreshAll()
}

async function openComplete(row) {
  const quote = await fetchQuote(row.id)
  completeDialog.order = row
  completeDialog.systemCents = quote.system_amount_cents
  completeDialog.yuan = quote.system_amount_cents / 100
  completeDialog.visible = true
}

async function submitComplete() {
  if (!completeDialog.order) return
  completeDialog.loading = true
  try {
    const cents = Math.round(Number(completeDialog.yuan) * 100)
    const result = await completePrint(completeDialog.order.id, cents)
    ElMessage.success(
      result.price_changed
        ? `已完成，改价为 ${yuan(result.amount_cents)}`
        : `已完成，金额 ${yuan(result.amount_cents)}`,
    )
    completeDialog.visible = false
    refreshAll()
  } finally {
    completeDialog.loading = false
  }
}

function openVerify(row) {
  verifyDialog.order = row
  verifyDialog.visible = true
}

async function submitVerify() {
  if (!verifyDialog.order) return
  verifyDialog.loading = true
  try {
    const result = await verifyOrder(verifyDialog.order.id)
    ElMessage.success(
      result.collected_onsite ? '已核销，并完成现场收款' : '已核销，订单完成',
    )
    verifyDialog.visible = false
    refreshAll()
  } finally {
    verifyDialog.loading = false
  }
}

async function submitVerifyCode() {
  if (!verifyCodeValue.value.trim()) {
    ElMessage.warning('请输入取件码')
    return
  }
  const result = await verifyByCode(verifyCodeValue.value.trim())
  ElMessage.success(
    result.collected_onsite
      ? `已核销 ${result.order.order_no}，并完成现场收款`
      : `已核销 ${result.order.order_no}`,
  )
  verifyCodeValue.value = ''
  refreshAll()
}

function openAbnormal(row) {
  abnormalDialog.order = row
  abnormalDialog.reason = ''
  abnormalDialog.visible = true
}

async function submitAbnormal() {
  if (!abnormalDialog.order) return
  abnormalDialog.loading = true
  try {
    await markAbnormal(abnormalDialog.order.id, abnormalDialog.reason)
    ElMessage.success('已标记异常')
    abnormalDialog.visible = false
    refreshAll()
  } finally {
    abnormalDialog.loading = false
  }
}

async function recover(row) {  await ElMessageBox.confirm('确认异常已处理，恢复排队？', '恢复排队', {
    confirmButtonText: '恢复',
    cancelButtonText: '取消',
  })
  await recoverOrder(row.id)
  ElMessage.success('已恢复排队')
  refreshAll()
}

async function closeAbnormal(row) {
  const { value } = await ElMessageBox.prompt('请填写关闭原因', '关闭异常单', {
    confirmButtonText: '关闭订单',
    cancelButtonText: '取消',
    inputValue: '文件损坏，联系学生重传',
  })
  await cancelAbnormalApi(row.id, value)
  ElMessage.success('异常单已关闭')
  refreshAll()
}

/** 打开确认弹窗：默认按系统重算价，老师可改最终价。 */
function openFileChange(row) {
  if (!row.file_change) return
  fileChangeDialog.order = row
  fileChangeDialog.change = row.file_change
  fileChangeDialog.yuan = (Number(row.file_change.new_amount_cents) || 0) / 100
  fileChangeDialog.visible = true
}

async function submitFileChange(approve) {
  const order = fileChangeDialog.order
  if (!order) return
  fileChangeDialog.loading = true
  try {
    const cents = Math.round(Number(fileChangeDialog.yuan) * 100)
    const result = await confirmFileChange(
      order.id,
      approve,
      approve ? cents : undefined,
    )
    if (!approve) {
      ElMessage.success('已驳回，订单保持原文件与原金额')
    } else if (Number(result.supplement_cents) > 0) {
      ElMessage.success(
        `已通过，订单按 ${yuan(result.amount_cents ?? cents)} 排到队尾，取件时现场补收 ${yuan(
          result.supplement_cents,
        )}`,
      )
    } else {
      ElMessage.success(`已通过，订单按 ${yuan(result.amount_cents ?? cents)} 排到队尾`)
    }
    fileChangeDialog.visible = false
    // 通过后该单会出现在队尾，重新拉队列与统计
    await refreshAll()
  } finally {
    fileChangeDialog.loading = false
  }
}

async function submitBatchComplete() {
  if (selected.value.length === 0) return
  await ElMessageBox.confirm(
    `确认把选中的 ${selected.value.length} 个订单全部标记为打印完成？`,
    '批量完成',
    { confirmButtonText: '确认', cancelButtonText: '取消' },
  )
  const result = await batchComplete(selected.value.map((item) => item.id))
  ElMessage.success(`完成 ${result.succeeded.length} 单，失败 ${result.failed.length} 单`)
  refreshAll()
}

</script>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  background: var(--zi-paper);
  border: var(--zi-card-border);
  border-top: 6px solid var(--zi-accent);
  border-radius: var(--zi-radius);
  box-shadow: var(--zi-card-shadow);
}
.stat {
  padding: 18px 20px;
  border-left: 2px solid var(--zi-line);
}
.stat:first-child {
  border-left: none;
}
.stat-label {
  color: var(--zi-muted);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.08em;
}
.stat-value {
  margin-top: 6px;
  color: var(--zi-ink);
  font-size: 34px;
  font-weight: 900;
  line-height: 1.05;
  font-variant-numeric: tabular-nums;
}
.stat-value.money {
  color: var(--zi-accent);
}
.stat-value.danger {
  color: var(--zi-danger);
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.toolbar .left,
.toolbar .right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.table-card {
  padding-top: 14px;
}

/* 手机版：统计换两列，工具栏竖排，订单换成卡片 */
.is-mobile .table-head {
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
}
.is-mobile .table-head :deep(.el-button) {
  width: 100%;
  min-height: 44px;
}
.is-mobile .stats {
  grid-template-columns: repeat(2, 1fr);
}
.is-mobile .toolbar.stacked {
  flex-direction: column;
  align-items: stretch;
}
.is-mobile .toolbar.stacked .left,
.is-mobile .toolbar.stacked .right {
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
}
.is-mobile .toolbar.stacked :deep(.el-select),
.is-mobile .toolbar.stacked :deep(.el-input) {
  width: 100% !important;
}
.is-mobile .toolbar.stacked :deep(.el-button) {
  width: 100%;
  min-height: 44px;
}
.order-cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.order-card {
  padding: 14px;
  min-width: 0;
  background: var(--zi-paper);
  border: var(--zi-card-border);
  border-left: 6px solid var(--zi-accent);
  border-radius: var(--zi-radius);
}
.oc-head {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.status-flag {
  margin-top: 4px;
}
/* 未确认标记：长文案允许折行，不要把状态列撑破 */
.flag-tag {
  height: auto;
  padding: 2px 6px;
  line-height: 1.4;
  white-space: normal;
}
.flag-link {
  margin-left: 4px;
  padding: 0;
  height: auto;
}
/* 桌面文件列：一行一份文件，超出省略，行高不鼓 */
.file-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.file-line {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  line-height: 1.5;
}
.file-seq {
  flex: none;
  color: var(--zi-muted);
  font-size: 12px;
}
.file-name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-pages {
  flex: none;
  font-size: 12px;
}
.file-dl {
  flex: none;
  height: auto;
  padding: 0 2px;
  font-size: 12px;
  line-height: 1.5;
}
.file-more {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  line-height: 1.5;
}
.file-more-btn {
  height: auto;
  padding: 0;
  font-size: 12px;
  line-height: 1.5;
}
/* 手机上逐份文件：名字可折行，下载按钮保持 44px 不掉手 */
.oc-files {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
}
.oc-file {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 6px 0;
  border-top: 1px dashed var(--zi-line);
  min-width: 0;
}
.oc-file-main {
  display: flex;
  flex-direction: column;
  flex: 1 1 140px;
  min-width: 0;
  font-size: 14px;
  line-height: 1.5;
  color: var(--zi-ink);
  word-break: break-all;
}
.oc-file-seq {
  color: var(--zi-muted);
  font-size: 12px;
}
.oc-file-name {
  min-width: 0;
  word-break: break-all;
}
.oc-file-dl {
  flex: 0 0 auto;
  min-height: 44px;
  margin-left: 0;
  padding: 0 18px;
}
.oc-flag-action :deep(.el-button) {
  min-height: 44px;
  padding: 0;
}
/* 换文件弹窗顶部的定位区 */
.change-target {
  margin-bottom: 12px;
  padding: 10px 12px;
  border-left: 4px solid var(--zi-warn);
  background: var(--zi-warn-bg);
  color: var(--zi-warn);
  font-size: 13px;
  line-height: 1.6;
}
.change-target-title {
  font-weight: 900;
  letter-spacing: 0.02em;
  word-break: break-all;
}
.change-target-list {
  margin-top: 6px;
  color: var(--zi-ink);
}
.change-target-file {
  word-break: break-all;
}
.change-target-file.current {
  font-weight: 900;
}
.change-target-mark {
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--zi-warn);
  color: #fff;
  font-size: 12px;
}
.oc-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
}
.oc-change {
  margin-top: 8px;
  padding: 8px 10px;
  border-left: 4px solid var(--zi-warn);
  background: var(--zi-warn-bg);
  color: var(--zi-warn);
  font-size: 13px;
  line-height: 1.6;
}
.oc-change span {
  display: block;
  word-break: break-all;
}
.oc-change-title {
  font-weight: 900;
  letter-spacing: 0.04em;
}
.oc-code {
  font-family: Menlo, Consolas, monospace;
  font-size: 22px;
  font-weight: 900;
  letter-spacing: 0.02em;
  color: var(--zi-accent);
  white-space: nowrap;
  word-break: normal;
}
.oc-line {
  font-size: 14px;
  line-height: 1.7;
  color: var(--zi-ink);
  word-break: break-all;
}
.oc-line .muted {
  margin-left: 6px;
}
.oc-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
.oc-actions :deep(.el-button) {
  flex: 1 1 44%;
  min-height: 44px;
  margin-left: 0;
}
.table-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  color: var(--zi-ink);
  font-size: 16px;
  font-weight: 900;
  letter-spacing: 0.02em;
}
.table-head .muted {
  font-weight: 400;
  letter-spacing: 0;
}
:deep(.pickup-cell .el-tag) {
  font-family: Menlo, 'SF Mono', Consolas, 'Courier New', monospace;
  font-size: 14px;
  font-weight: 900;
  letter-spacing: 0.04em;
}
.dialog-tip {
  margin: 12px 0 8px;
  color: var(--zi-muted);
  font-size: 12px;
}

/* 旧蓝白皮肤 */
.zi-theme-classic .stats {
  gap: 16px;
  border: none;
  border-top: none;
  background: transparent;
  box-shadow: none;
}
.zi-theme-classic .stat {
  padding: 18px;
  border-left: none;
  border-radius: 12px;
  background: #ffffff;
  box-shadow: 0 4px 16px rgba(31, 45, 61, 0.06);
}
.zi-theme-classic .stat-label {
  font-weight: 400;
  letter-spacing: 0;
}
.zi-theme-classic .stat-value {
  font-size: 26px;
  font-weight: 700;
}
.zi-theme-classic .table-head {
  font-weight: 600;
  letter-spacing: 0;
}
</style>
