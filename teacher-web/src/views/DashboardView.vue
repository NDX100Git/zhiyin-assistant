<template>
  <div class="dashboard">
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

    <div class="page-card toolbar">
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
          placeholder="输入取件码核销，如 A-01"
          style="width: 220px"
          clearable
          @keyup.enter="submitVerifyCode"
        />
        <el-button type="success" @click="submitVerifyCode">核销取件</el-button>
      </div>
    </div>

    <div class="page-card table-card">
      <div class="table-head">
        <div>接单队列 <span class="muted">（按订单号排序，每 5 秒自动刷新）</span></div>
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
        v-loading="loading"
        :data="orders"
        row-key="id"
        border
        stripe
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="46" />
        <el-table-column prop="order_no" label="订单号" width="110" />
        <el-table-column prop="pickup_code" label="取件码" width="90">
          <template #default="{ row }">
            <el-tag effect="plain">{{ row.pickup_code }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="file_name" label="文件" min-width="180" show-overflow-tooltip />
        <el-table-column prop="page_count" label="页数" width="70" />
        <el-table-column label="打印参数" width="150">
          <template #default="{ row }">
            {{ row.print_side === 2 ? '双面' : '单面' }} ·
            {{ row.color_mode === 1 ? '彩色' : '黑白' }} ·
            {{ row.copies }} 份
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" effect="light">{{ row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="金额" width="100">
          <template #default="{ row }">
            <span class="money">{{ yuan(row.amount_cents) }}</span>
            <div v-if="row.paid" class="muted">已支付</div>
            <div v-else class="muted">待收款</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="{ row }">
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
    </div>

    <el-dialog v-model="completeDialog.visible" title="打印完成" width="420px">
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

    <el-dialog v-model="verifyDialog.visible" title="核销取件" width="420px">
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

    <el-dialog v-model="abnormalDialog.visible" title="标记异常" width="420px">
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
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  batchComplete,
  cancelAbnormal as cancelAbnormalApi,
  completePrint,
  downloadUrl,
  fetchQueue,
  fetchQuote,
  fetchSummary,
  markAbnormal,
  recoverOrder,
  startPrint,
  verifyByCode,
  verifyOrder,
} from '../api/admin'

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
const orders = ref([])
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

let timer = null

function yuan(cents) {
  return `¥${((Number(cents) || 0) / 100).toFixed(2)}`
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

onMounted(() => {
  refreshAll()
  startPolling()
})
onUnmounted(stopPolling)

async function download(row) {
  const data = await downloadUrl(row.id)
  window.open(data.signed_url, '_blank')
}

async function start(row) {
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

async function recover(row) {
  await ElMessageBox.confirm('确认异常已处理，恢复排队？', '恢复排队', {
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

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) refreshAll()
})
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
  gap: 16px;
}
.stat {
  background: #fff;
  border-radius: 12px;
  padding: 18px;
  box-shadow: 0 4px 16px rgba(31, 45, 61, 0.06);
}
.stat-label {
  font-size: 13px;
  color: #8b98a8;
}
.stat-value {
  margin-top: 8px;
  font-size: 26px;
  font-weight: 700;
}
.stat-value.danger {
  color: #e6a23c;
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
  padding-top: 12px;
}
.table-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  font-weight: 600;
}
.dialog-tip {
  margin: 12px 0 8px;
  font-size: 12px;
  color: #8b98a8;
}
</style>
