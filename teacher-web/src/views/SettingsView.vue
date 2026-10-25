<template>
  <div class="settings">
    <div class="page-card">
      <div class="section-head">
        <div>
          <div class="title">收单管理</div>
          <div class="muted">关闭后新订单会排到下一个打印时段，已在队列中的订单不受影响</div>
        </div>
        <el-date-picker
          v-model="day"
          type="date"
          value-format="YYYY-MM-DD"
          :clearable="false"
          style="width: 160px"
          @change="loadSessions"
        />
      </div>

      <el-table v-loading="loadingSessions" :data="sessions" border>
        <el-table-column prop="label" label="时段" width="100" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="row.is_open ? 'success' : 'info'">
              {{ row.is_open ? '收单中' : '已截单' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="容量" width="160">
          <template #default="{ row }">{{ row.used }} / {{ row.capacity }}</template>
        </el-table-column>
        <el-table-column prop="closed_at" label="截单时间" />
        <el-table-column label="操作" width="140">
          <template #default="{ row }">
            <el-button
              v-if="row.is_open"
              link
              type="warning"
              @click="switchSession(row, false)"
            >
              关闭收单
            </el-button>
            <el-button v-else link type="success" @click="switchSession(row, true)">
              恢复收单
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="page-card">
      <div class="title">发布公告</div>
      <div class="muted">公告会显示在学生端首页，最多 1000 字</div>
      <el-input
        v-model="notice"
        type="textarea"
        :rows="4"
        maxlength="1000"
        show-word-limit
        placeholder="例如：上午设备检修，打印时间可能顺延"
        class="notice-input"
      />
      <el-button type="primary" :loading="publishing" @click="submitNotice">发布公告</el-button>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  closeSession,
  fetchSessions,
  openSession,
  publishNotice,
} from '../api/admin'

const day = ref(new Date().toISOString().slice(0, 10))
const sessions = ref([])
const loadingSessions = ref(false)
const notice = ref('')
const publishing = ref(false)

async function loadSessions() {
  loadingSessions.value = true
  try {
    const data = await fetchSessions(day.value)
    sessions.value = data.items || []
  } catch (error) {
    // 只有 try/finally 时，失败会抛成未处理的 Promise，页面停在旧数据上没有任何提示。
    // 拦截器已经弹过一次服务端原话，这里补一句带上下文的提示，并保住表格不白屏。
    console.warn('[设置] 加载收单时段失败', error)
    ElMessage.error(`加载 ${day.value} 的收单时段失败：${error.message || '网络错误'}，请稍后重试`)
  } finally {
    loadingSessions.value = false
  }
}

async function switchSession(row, open) {
  try {
    if (open) {
      await openSession(day.value, row.session)
      ElMessage.success(`${row.label}已恢复收单`)
    } else {
      await closeSession(day.value, row.session)
      ElMessage.success(`${row.label}已截单，新订单将排到下一时段`)
    }
  } catch (error) {
    // 开/截单失败必须说清「哪个时段没改成」：未捕获时按钮看起来像没反应
    console.warn('[设置] 切换收单状态失败', error)
    ElMessage.error(
      `${row.label}${open ? '恢复收单' : '截单'}失败：${error.message || '网络错误'}，列表保持原状态`,
    )
  }
  loadSessions()
}

async function submitNotice() {
  if (!notice.value.trim()) {
    ElMessage.warning('请填写公告内容')
    return
  }
  publishing.value = true
  try {
    await publishNotice(notice.value.trim())
    ElMessage.success('公告已发布')
    notice.value = ''
  } catch (error) {
    // 发布失败不能把输入框清空：老师写的公告还留在框里，可以直接重试
    console.warn('[设置] 发布公告失败', error)
    ElMessage.error(`公告发布失败：${error.message || '网络错误'}，内容已保留`)
  } finally {
    publishing.value = false
  }
}

onMounted(loadSessions)
</script>

<style scoped>
.settings {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.section-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.title {
  padding-left: 12px;
  border-left: 6px solid var(--zi-accent);
  color: var(--zi-ink);
  font-size: 18px;
  font-weight: 900;
  line-height: 1.3;
  letter-spacing: 0.02em;
}
.notice-input {
  margin: 14px 0;
}

/* 旧蓝白皮肤 */
.zi-theme-classic .title {
  padding-left: 0;
  border-left: none;
  font-weight: 600;
  letter-spacing: 0;
}
</style>
