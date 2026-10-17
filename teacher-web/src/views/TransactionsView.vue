<template>
  <div class="transactions">
    <div class="page-card toolbar">
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
      <el-table v-loading="loading" :data="data.items" border stripe>
        <el-table-column prop="created_at" label="时间" width="180" />
        <el-table-column prop="order_no" label="订单号" width="120" />
        <el-table-column label="金额" width="120">
          <template #default="{ row }">
            <span class="money">{{ yuan(row.amount_cents) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="方式" width="120">
          <template #default="{ row }">
            <el-tag :type="row.method === 'onsite' ? 'success' : 'primary'" effect="plain">
              {{ row.method === 'onsite' ? '现场收款' : '模拟支付' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="operator" label="操作人" width="160" />
        <el-table-column prop="order_id" label="订单 ID" width="100" />
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { fetchTransactions } from '../api/admin'

const day = ref(new Date().toISOString().slice(0, 10))
const loading = ref(false)
const data = reactive({ count: 0, total_cents: 0, items: [] })

function yuan(cents) {
  return `¥${((Number(cents) || 0) / 100).toFixed(2)}`
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
.transactions {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.label {
  font-size: 13px;
  color: #8b98a8;
}
.summary {
  display: flex;
  gap: 24px;
  font-size: 14px;
  color: #4b5a6b;
}
</style>
