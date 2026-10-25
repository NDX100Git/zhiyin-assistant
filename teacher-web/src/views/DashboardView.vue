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
        <div>
          接单队列 <span class="muted">（每 5 秒自动刷新第一屏 {{ QUEUE_PAGE_SIZE_POLL }} 条；统计每 15 秒刷一次；超过第一屏请用搜索定位，确认换文件后该单排到队尾；点「加载更多」多显示的部分只到下一次自动刷新，之后回到第一屏）</span>
          <!-- 队列只拉一屏，超过时必须让老师知道“下面还有单”，不能默默截断。
               封顶 200 条之后再说「点加载更多」就是骗人：按钮已经拉不出来，要说清楚
               现在看到的是前 200 单，剩下的只能用搜索精确定位。 -->
          <div v-if="queueAtCap" class="queue-more-note">
            已显示前 {{ QUEUE_PAGE_SIZE_MAX }} 单，请用搜索精确定位；还有 {{ queueHiddenCount }} 单未显示
          </div>
          <div v-else-if="queueHiddenCount > 0" class="queue-more-note">
            共 {{ queueTotal }} 单，还有 {{ queueHiddenCount }} 单未显示，请用搜索定位或点右侧「加载更多」
          </div>
        </div>
        <div>
          <el-button
            v-if="queueHiddenCount > 0 && !queueAtCap"
            type="primary"
            plain
            :loading="loading"
            class="queue-load-more"
            @click="loadMoreQueue"
          >
            加载更多（再显示 {{ QUEUE_LOAD_MORE_STEP }} 单）
          </el-button>
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
                <div class="file-line-top">
                  <span v-if="file.label" class="file-seq">#{{ file.seq }}</span>
                  <span class="file-name" :title="file.file_name">{{ file.file_name }}</span>
                  <span class="file-pages muted">{{ file.page_count ? file.page_count + ' 页' : '' }}</span>
                  <el-button link type="primary" class="file-dl" @click="downloadOne(row, file)">
                    下载
                  </el-button>
                </div>
                <!-- 逐份参数与行金额：老师按这份的份数、单双面、颜色、纸张上机 -->
                <div class="file-line-meta">
                  <span class="file-params">{{ paramSummary(file) }}</span>
                  <span class="muted file-print-pages">打印 {{ file.print_pages }} 页</span>
                  <span class="file-amount money">{{ yuan(file.amount_cents) }}</span>
                </div>
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
            <!-- 单文件：就是这一份的参数；多文件：逐份参数见左侧文件列 -->
            <div>{{ paramSummary(orderFiles(row)[0]) || '—' }}</div>
            <div class="muted">
              文档 {{ totalPages(row) || row.page_count }} 页 · 打印 {{ totalPrintPages(row) }} 页
            </div>
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
            <el-button v-if="row.status === 'abnormal'" link type="danger" @click="openCancelAbnormal(row)">
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
                <span class="oc-file-params">{{ paramSummary(file) }}</span>
                <span class="oc-file-amount">
                  打印 {{ file.print_pages }} 页 · <span class="money">{{ yuan(file.amount_cents) }}</span>
                </span>
              </div>
              <el-button size="large" class="oc-file-dl" @click="downloadOne(row, file)">
                下载
              </el-button>
            </div>
          </div>
          <div class="oc-line">
            文档 {{ totalPages(row) || row.page_count }} 页 · 打印 {{ totalPrintPages(row) }} 页
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
            <el-button v-if="row.status === 'abnormal'" size="large" type="danger" @click="openCancelAbnormal(row)">
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
          {{ filesDialog.files.length }} 份文件 · 文档 {{ totalPages(filesDialog.order) }} 页 ·
          打印 {{ totalPrintPages(filesDialog.order) }} 页 ·
          {{ yuan(totalAmountCents(filesDialog.order)) }}
        </div>
        <div v-for="file in filesDialog.files" :key="file.seq" class="files-dialog-row">
          <span class="files-dialog-seq">#{{ file.seq }}</span>
          <div class="files-dialog-main">
            <span class="files-dialog-name">{{ file.file_name }}</span>
            <span class="muted">{{ file.page_count ? file.page_count + ' 页' : '页数未知' }}</span>
            <span class="files-dialog-params">
              {{ paramSummary(file) }} · 打印 {{ file.print_pages }} 页 ·
              <span class="money">{{ yuan(file.amount_cents) }}</span>
            </span>
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
          <!--
            预填用「当前成交价」：换文件确认价、老师之前改过的价都落在这里。
            只拿系统价预填会把确认价抹回系统价（800 分变 60 分那种事故）。
          -->
          <el-descriptions-item label="当前成交价">
            <span class="money">{{ yuan(completeDialog.currentCents) }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="系统报价">
            {{ yuan(completeDialog.systemCents) }}
            <span v-if="completeDialog.currentCents !== completeDialog.systemCents" class="muted">
              （仅作参考，不会覆盖上面的成交价）
            </span>
          </el-descriptions-item>
          <el-descriptions-item label="页数/份数">
            {{ completeDialog.order.page_count }} 页 × {{ completeDialog.order.copies }} 份
          </el-descriptions-item>
        </el-descriptions>
        <div class="dialog-tip">
          默认按当前成交价提交；老师可按特殊情况修改金额（加急、装订、特殊纸张等）。
        </div>
        <el-input-number
          v-model="completeDialog.yuan"
          :min="0"
          :precision="2"
          :step="0.5"
          style="width: 100%"
          class="complete-amount-input"
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

    <!--
      关闭异常单（异常单唯一的关单入口）：已收过钱的单必须在这里登记退款，
      没有退款入口就只能让学生自己想办法，账也对不上。
    -->
    <el-dialog
      v-model="cancelDialog.visible"
      title="关闭异常单"
      class="cancel-abnormal-dialog"
      :width="isMobile ? '92%' : '460px'"
    >
      <div v-if="cancelDialog.order">
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item label="订单号">{{ cancelDialog.order.order_no }}</el-descriptions-item>
          <el-descriptions-item label="成交价">
            {{ yuan(cancelDialog.order.amount_cents) }}
          </el-descriptions-item>
          <el-descriptions-item label="已收金额">
            <span class="money">{{ yuan(cancelDialog.paidCents) }}</span>
          </el-descriptions-item>
        </el-descriptions>
        <el-input
          v-model="cancelDialog.reason"
          type="textarea"
          :rows="2"
          class="cancel-reason-input"
          placeholder="关闭原因，如：文件损坏，联系学生重传"
        />
        <div class="dialog-tip">
          退款金额（元）：默认全额，可改成部分退款；只能退实际收到的钱，同一单只能退一次。
        </div>
        <el-input-number
          v-model="cancelDialog.refundYuan"
          :min="0"
          :precision="2"
          :step="1"
          style="width: 100%"
          class="cancel-refund-input"
        />
        <div v-if="cancelDialog.error" class="dialog-error">{{ cancelDialog.error }}</div>
      </div>
      <template #footer>
        <el-button @click="cancelDialog.visible = false">取消</el-button>
        <el-button
          type="danger"
          :loading="cancelDialog.loading"
          class="cancel-abnormal-submit"
          @click="submitCancelAbnormal"
        >
          确认关闭
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
  paramSummary,
  totalAmountCents,
  totalPages,
  totalPrintPages,
} from '../api/orderFiles'
import { explainApiCode } from '../utils/errors'

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

/**
 * 轮询与拉取条数（本轮从「每 5 秒 × 200 条」收敛而来）。
 *
 * 旧实现每次轮询都同时拉统计与 200 条队列，柜台机开着一天就是两万多次请求，
 * 而且每次都把整张 200 单的表叠回去。现在拆成三件事：
 * - 队列：仍然 5 秒，但只拉当前视口能看到的条数（第一屏 60 单）；
 *   有筛选/关键词时手动查询走全量 200 条，搜索不受影响。
 * - 统计：每 3 次轮询（约 15 秒）拉一次；换文件、打印完成这类关键动作之后
 *   仍然立即 refreshAll() 全量刷新，统计不会漏。
 * - 页面隐藏时不轮询（原有行为不变）。
 *
 * 只拉一屏就必须把“没拉完”说出去：队列末尾写上总条数，多出的部分用
 * 「加载更多」分次拉（每次 +60，上限 200——后端 page_size 的上限），
 * 但**增量只归这一次展示**：queueExtra 在轮询与手点查询/刷新时清零，
 * 下一拍轮询重新只拉第一屏 60 条。不这样做的话，点过一次「加载更多」之后
 * 每 5 秒都按放大后的条数（120/180/200）拉，页头文案却还写着「第一屏 60 条」。
 */
const QUEUE_PAGE_SIZE_POLL = 60
const QUEUE_PAGE_SIZE_FULL = 200
const QUEUE_LOAD_MORE_STEP = 60
/** 后端 /api/admin/queue 的 page_size 上限（Query(le=200)），超过直接 422。 */
const QUEUE_PAGE_SIZE_MAX = 200
const POLL_INTERVAL_MS = 5000
const SUMMARY_POLL_TICKS = 3
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
/** 队列里一共多少单（后端 total）与本次实际拉到多少单，两者差就是“还没显示”的。 */
const queueTotal = ref(0)
/** 「加载更多」已经额外要了多少条（一次 +60，封顶 200）。 */
const queueExtra = ref(0)

const queueHiddenCount = computed(() => Math.max(queueTotal.value - orders.value.length, 0))
/**
 * 已经拉到后端单页上限（200）但库里还有单没显示。
 *
 * 这种情况不能再说“点加载更多”：page_size 已经顶到上限，再点也拉不到新单，
 * 老师会以为按钮坏了或者已经没有更多的单。要说清楚「已显示前 200 单，请用搜索精确定位」。
 */
const queueAtCap = computed(
  () => orders.value.length >= QUEUE_PAGE_SIZE_MAX && queueHiddenCount.value > 0,
)

const completeDialog = reactive({
  visible: false,
  order: null,
  systemCents: 0,
  currentCents: 0,
  yuan: 0,
  loading: false,
})
const verifyDialog = reactive({ visible: false, order: null, loading: false })
const abnormalDialog = reactive({ visible: false, order: null, reason: '', loading: false })
/** 关闭异常单弹窗：reason 可空，refundYuan 默认全额（= 已收金额）。 */
const cancelDialog = reactive({
  visible: false,
  order: null,
  reason: '',
  refundYuan: 0,
  paidCents: 0,
  error: '',
  loading: false,
})
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
 * 「已替换未确认」订单的两段式拦截计数。
 *
 * 计数跟着当前操作的订单走：换到别的订单就从头算（第一下仍然是硬阻止），
 * 同一单里第一下只提醒先下载，第二下及以后才弹确认框允许强打。
 */
const startGuard = reactive({ orderId: null, blocks: 0 })

function resetStartGuard(row) {
  if (startGuard.orderId === row.id) return
  startGuard.orderId = row.id
  startGuard.blocks = 0
}

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

/**
 * 本次该拉多少条（只给「加载更多」用）。
 *
 * 「加载更多」在各自基数上每次 +60，封顶后端上限 200；轮询与手点查询/刷新
 * 不走这个函数（见 pollQueueSize），否则增量会粘住不放。
 */
function queuePageSize() {
  const hasFilter = Boolean(filters.status || filters.keyword.trim())
  const base = hasFilter ? QUEUE_PAGE_SIZE_FULL : QUEUE_PAGE_SIZE_POLL
  return Math.min(base + queueExtra.value, QUEUE_PAGE_SIZE_MAX)
}

/**
 * 轮询该拉多少条：永远只拉第一屏（有筛选/关键词时才拉满一页）。
 *
 * 页头文案写的是「每 5 秒自动刷新第一屏 60 条」，轮询就必须只拉 60 条：
 * 「加载更多」多要的部分只影响当次展示，下一拍回到第一屏，文案与行为才一致。
 */
function pollQueueSize() {
  const hasFilter = Boolean(filters.status || filters.keyword.trim())
  return hasFilter ? QUEUE_PAGE_SIZE_FULL : QUEUE_PAGE_SIZE_POLL
}

async function loadQueue(pageSize = QUEUE_PAGE_SIZE_FULL) {
  loading.value = true
  try {
    const params = { page: 1, page_size: pageSize }
    if (filters.status) params.status = filters.status
    if (filters.keyword) params.keyword = filters.keyword.trim()
    const data = await fetchQueue(params)
    orders.value = data.items || []
    // total 是满足筛选条件的总条数：没给就按本页条数算，不能凭空报“还有 N 单”
    const total = Number(data.total)
    queueTotal.value = Number.isFinite(total) ? total : orders.value.length
  } catch (error) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

/** 「加载更多」：多要 60 条，拉完还是一屏就继续点。 */
async function loadMoreQueue() {
  queueExtra.value = Math.min(queueExtra.value + QUEUE_LOAD_MORE_STEP, QUEUE_PAGE_SIZE_MAX)
  await loadQueue(queuePageSize())
}

/** 全量刷新：手点查询/刷新、以及每次改完订单后调，统计与队列一起拉最新。 */
async function refreshAll() {
  // 条件变了或重新查询时别把上一轮的“加载更多”带到新结果上
  queueExtra.value = 0
  await Promise.all([loadSummary(), loadQueue(QUEUE_PAGE_SIZE_FULL)])
}

let pollTicks = 0

function startPolling() {
  stopPolling()
  pollTicks = 0
  timer = setInterval(() => {
    if (document.hidden) return
    pollTicks += 1
    // 轮询回到第一屏：先把「加载更多」的增量复位，再按第一屏条数拉。
    // 不复位的话，点过一次「加载更多」就每 5 秒都按放大后的条数拉，页面越跑越重；
    // 复位的代价是下一拍显示条数从 120 退回 60，这正是页头写的「刷新第一屏 60 条」。
    queueExtra.value = 0
    // 有筛选/关键词时轮询也用全量：搜索结果被截断比多拉几条更难解释
    loadQueue(pollQueueSize())
    if (pollTicks % SUMMARY_POLL_TICKS === 0) loadSummary()
  }, POLL_INTERVAL_MS)
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
  // 已确认看到新文件，这一单回到「第一次点击就弹常规确认」，拦截计数清零
  if (startGuard.orderId === row.id) {
    startGuard.orderId = null
    startGuard.blocks = 0
  }
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
  resetStartGuard(row)
  if (hasUnseenChange(row)) {
    // 第一下只拦不弹框：老师本地很可能还是旧文件，先让他重新下载
    if (startGuard.blocks === 0) {
      startGuard.blocks = 1
      ElMessage.warning('这单文件已替换，请先下载新文件再打印')
      return
    }
    // 第二下及以后：老师已经知道情况，确认后允许强打
    try {
      await ElMessageBox.confirm(
        '这单换过文件，你本地可能还是旧版本，仍要现在开始打印吗？',
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
  completeDialog.systemCents = Number(quote.system_amount_cents) || 0
  // 当前成交价才是学生实际要付的那笔钱（换文件确认价 / 之前改过的价）。
  // 后端没给（老接口）时才退回系统价，不能反过来拿系统价盖掉成交价。
  const current = quote.current_amount_cents
  const hasCurrent =
    current !== null && current !== undefined && Number.isFinite(Number(current))
  completeDialog.currentCents = hasCurrent ? Number(current) : completeDialog.systemCents
  completeDialog.yuan = completeDialog.currentCents / 100
  completeDialog.visible = true
}

async function submitComplete() {
  if (!completeDialog.order) return
  const cents = Math.round(Number(completeDialog.yuan) * 100)
  if (!Number.isFinite(cents) || cents < 0) {
    ElMessage.warning(explainApiCode(40061))
    return
  }
  completeDialog.loading = true
  try {
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

/**
 * 打开「关闭异常单」弹窗。
 *
 * 已收款才能退：默认把实际收到的钱全额退回去（队列里只有 amount_cents 与 paid，
 * 没有逐笔流水，按成交价作为上限；后端还会再按真实已收校一次，超了回 40062）。
 */
function openCancelAbnormal(row) {
  cancelDialog.order = row
  cancelDialog.reason = ''
  cancelDialog.paidCents = row.paid ? Number(row.amount_cents) || 0 : 0
  cancelDialog.refundYuan = cancelDialog.paidCents / 100
  cancelDialog.error = ''
  cancelDialog.visible = true
}

async function submitCancelAbnormal() {
  if (!cancelDialog.order) return
  const refund = Math.round(Number(cancelDialog.refundYuan) * 100)
  // 先在前端拦一道，文案与后端 40061/40062 同源，免得老师填完才被拒
  if (!Number.isFinite(refund) || refund < 0) {
    cancelDialog.error = explainApiCode(40061)
    return
  }
  if (refund > cancelDialog.paidCents) {
    cancelDialog.error = `${explainApiCode(40062)}（本单已收 ${yuan(cancelDialog.paidCents)}）`
    return
  }
  cancelDialog.error = ''
  cancelDialog.loading = true
  try {
    const result = await cancelAbnormalApi(
      cancelDialog.order.id,
      cancelDialog.reason,
      refund,
    )
    const refunded = Number(result.refund_cents)
    const amount = Number.isFinite(refunded) ? refunded : refund
    ElMessage.success(
      amount > 0 ? `已关闭异常单，已退款 ${yuan(amount)}` : '已关闭异常单，未收款无需退款',
    )
    cancelDialog.visible = false
    await refreshAll()
  } catch (error) {
    // 后端 40061/40062/40906 都在这里兜底显示成中文（40062 可能来自真实已收）
    cancelDialog.error = error.message || '关闭异常单失败，请稍后重试'
  } finally {
    cancelDialog.loading = false
  }
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
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  line-height: 1.5;
}
.file-line-top {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
/* 逐份参数行：单双面 · 颜色 · 纸张 · 份数 + 打印页数 + 行金额 */
.file-line-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  line-height: 1.5;
}
.file-params {
  color: var(--zi-ink);
  font-weight: 700;
}
.file-print-pages {
  font-size: 12px;
}
.file-amount {
  font-size: 12px;
  font-weight: 900;
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
/* 手机上每份的参数与行金额：可折行，390 宽不横向溢出 */
.oc-file-params {
  color: var(--zi-ink);
  font-size: 12px;
  line-height: 1.5;
}
.oc-file-amount {
  color: var(--zi-muted);
  font-size: 12px;
  line-height: 1.5;
}
.files-dialog-params {
  display: block;
  color: var(--zi-ink);
  font-size: 12px;
  line-height: 1.5;
  word-break: break-all;
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
  background: var(--zi-warn-solid);
  color: var(--zi-on-solid);
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
/* 队列未拉完的提示：不能与表头说明一样轻，否则等于没提示 */
.queue-more-note {
  margin-top: 4px;
  color: var(--zi-warn);
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0;
}
/* 弹窗内联报错：负金额 / 超过已收 / 后端校验失败都写在这里 */
.dialog-error {
  margin-top: 8px;
  color: var(--zi-danger);
  font-size: 13px;
  font-weight: 800;
  line-height: 1.5;
}
.cancel-abnormal-dialog .cancel-reason-input {
  margin-top: 12px;
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
  background: var(--zi-paper);
  box-shadow: var(--zi-card-shadow-soft);
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
