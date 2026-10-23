<template>
  <div class="transactions" :class="{ 'is-mobile': isMobile }">
    <div class="page-card toolbar" :class="{ stacked: isMobile }">
      <div class="left">
        <span class="label">日期</span>
        <el-date-picker
          v-model="day"
          type="date"
          value-format="YYYY-MM-DD"
          :clearable="false"
          style="width: 160px"
          @change="load"
        />
        <el-button type="primary" :loading="loading" @click="load">查询</el-button>
      </div>
      <div class="summary">
        <span>共 <b>{{ data.count }}</b> 笔</span>
        <span>合计 <b class="money">{{ yuan(data.total_cents) }}</b></span>
      </div>
    </div>

    <div class="page-card">
      <el-table v-if="!isMobile" v-loading="loading" :data="data.items" border stripe>
        <el-table-column label="时间" min-width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="order_no" label="订单号" min-width="150" />
        <el-table-column label="金额" min-width="120">
          <template #default="{ row }">
            <span class="money">{{ yuan(row.amount_cents) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="方式" min-width="120">
          <template #default="{ row }">
            <el-tag :type="row.method === 'onsite' ? 'success' : 'primary'" effect="plain">
              {{ row.method === 'onsite' ? '现场收款' : '模拟支付' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="operator" label="操作人" min-width="160" />
        <el-table-column prop="order_id" label="订单编号" min-width="110" />
      </el-table>

      <!-- 手机上一张流水一张卡，时间和金额先看到 -->
      <div v-else class="flow-cards" v-loading="loading">
        <div v-for="row in data.items" :key="row.id" class="flow-card">
          <div class="fc-head">
            <span class="fc-money">{{ yuan(row.amount_cents) }}</span>
            <el-tag :type="row.method === 'onsite' ? 'success' : 'primary'" effect="plain">
              {{ row.method === 'onsite' ? '现场收款' : '模拟支付' }}
            </el-tag>
          </div>
          <div class="fc-line">{{ formatTime(row.created_at) }}</div>
          <div class="fc-line muted">{{ row.order_no }} · 操作人 {{ row.operator || '系统' }}</div>
        </div>
        <el-empty v-if="!loading && data.items.length === 0" description="当天没有流水" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { fetchTransactions } from '../api/admin'
import { useIsMobile } from '../composables/useIsMobile'

const isMobile = useIsMobile()
const day = ref(new Date().toISOString().slice(0, 10))
const loading = ref(false)
const data = reactive({ count: 0, total_cents: 0, items: [] })

function yuan(cents) {
  return `¥${((Number(cents) || 0) / 100).toFixed(2)}`
}

/** 后端返回 ISO 串，直接显示会带一个 T，这里换成 年-月-日 时:分。 */
function formatTime(value) {
  if (!value) return ''
  return String(value).replace('T', ' ').slice(0, 16)
}

async function load() {
  loading.value = true
  try {
    const result = await fetchTransactions(day.value)
    data.count = result.count
    data.total_cents = result.total_cents
    data.items = result.items || []
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.is-mobile .toolbar.stacked {
  flex-direction: column;
  align-items: stretch;
  gap: 10px;
}
.is-mobile .toolbar.stacked .left {
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
}
.is-mobile .toolbar.stacked :deep(.el-date-editor),
.is-mobile .toolbar.stacked :deep(.el-button) {
  width: 100% !important;
  min-height: 44px;
}
.flow-cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.flow-card {
  padding: 14px;
  background: var(--zi-paper);
  border: var(--zi-card-border);
  border-left: 6px solid var(--zi-accent);
  border-radius: var(--zi-radius);
}
.fc-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.fc-money {
  font-size: 22px;
  font-weight: 900;
  color: var(--zi-accent);
}
.fc-line {
  font-size: 14px;
  line-height: 1.7;
}
.transactions {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.label {
  color: var(--zi-muted);
  font-size: 13px;
  font-weight: 700;
}
.summary {
  display: flex;
  gap: 24px;
  color: var(--zi-ink);
  font-size: 14px;
}
.summary b {
  color: var(--zi-accent);
  font-size: 20px;
  font-weight: 900;
}
</style>
