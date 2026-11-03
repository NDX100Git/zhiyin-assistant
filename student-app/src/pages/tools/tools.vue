<template>
  <view class="page" :class="[uiThemeClass, uiSchemeClass]">
    <!-- 功能选择：三张卡并排，选一个再选文件，避免一进页面就弹系统选择器 -->
    <view class="card">
      <view class="card-title">文档转换</view>
      <view class="tool-list">
        <view
          v-for="tool in TOOLS"
          :key="tool.key"
          class="tool"
          :class="{ active: activeKey === tool.key }"
          @click="activeKey = tool.key"
        >
          <text class="tool-name">{{ tool.name }}</text>
          <text class="tool-desc">{{ tool.desc }}</text>
        </view>
      </view>
    </view>

    <view class="card">
      <view class="card-title">{{ current.name }}</view>
      <text class="hint">{{ current.hint }}</text>
      <button class="action main" :disabled="busy" @click="start">
        {{ buttonText }}
      </button>
    </view>

    <!-- 转换结果：只在这一步才出现，避免空卡片占位 -->
    <view v-if="result" class="card">
      <view class="card-title">转换完成</view>
      <view class="file-row">
        <text class="file-name">{{ result.file_name }}</text>
        <text class="file-size">{{ formatSize(result.size) }}</text>
      </view>
      <button class="action main" @click="openResult">下载 / 打开</button>
      <button class="action demo" :disabled="busy" @click="reset">再转一份</button>
    </view>

    <view class="footnote">
      PDF 转 Word 与 Word 转 PDF 由服务端本地转换，不消耗 AI 额度；图片转 Word 需要识别文字，会用到模型。
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { API_BASE_URL } from '@/config'
import { getToken, request, uploadFile } from '@/services/api'
import { chooseDocuments, chooseImage } from '@/services/filePicker'
import { notify, notifyError } from '@/services/notify'
import { useUiTheme } from '@/ui/theme'

/**
 * 三个转换方向。
 *
 * endpoint 是服务端接口；picker 决定用哪种选择器（文档还是图片）；
 * ext 是允许的扩展名，选完先本地挡一次，不让用户等上传完才收到格式错误。
 */
const TOOLS = [
  {
    key: 'pdf2word',
    name: 'PDF 转 Word',
    desc: '转成可编辑的 .docx',
    hint: '适合拿到 PDF 但需要改内容的情况。扫描件转出来的正文可能是图片，那种要先做图片识别。',
    endpoint: '/api/ai/convert',
    picker: 'document',
    ext: ['pdf'],
    accept: '选择 PDF 文件',
  },
  {
    key: 'img2word',
    name: '图片转 Word',
    desc: '拍照或相册识别成 .docx',
    hint: '把纸质材料拍下来，识别文字后生成 Word。识别需要模型密钥，没配密钥会提示去填。',
    endpoint: '/api/ai/image-to-word',
    picker: 'image',
    ext: [],
    accept: '拍照或从相册选择',
  },
  {
    key: 'word2pdf',
    name: 'Word 转 PDF',
    desc: '转成可直接打印的 PDF',
    hint: '打印前统一格式用。转换是本地排版引擎完成，不调用大模型。',
    endpoint: '/api/ai/to-pdf',
    picker: 'document',
    ext: ['doc', 'docx'],
    accept: '选择 Word 文件',
  },
]

const activeKey = ref('pdf2word')
const busy = ref(false)
const uploading = ref(false)
const converting = ref(false)
const result = ref(null)

const { uiThemeClass, uiSchemeClass } = useUiTheme()

const current = computed(
  () => TOOLS.find((tool) => tool.key === activeKey.value) || TOOLS[0]
)

const buttonText = computed(() => {
  if (uploading.value) return '上传中…'
  if (converting.value) return '转换中，大文件可能要一两分钟…'
  return current.value.accept
})

function formatSize(bytes) {
  const size = Number(bytes) || 0
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  if (size >= 1024) return `${Math.round(size / 1024)} KB`
  return `${size} B`
}

/** 选文件。用户中途取消时返回 null，让调用方安静退出，不当成错误。 */
async function pickFile(tool) {
  try {
    if (tool.picker === 'image') {
      return await chooseImage()
    }
    const picked = await chooseDocuments(1)
    const file = picked[0]
    // 换扩展名判断要在这里做：选错格式上传上去，服务端也会拒绝，
    // 但那要浪费一次上传，当场提示更快。
    if (tool.ext.length) {
      const ext = String(file.name).split('.').pop().toLowerCase()
      if (!tool.ext.includes(ext)) {
        throw new Error(`这个功能只接受 ${tool.ext.join(' / ')}，当前选的是 .${ext}`)
      }
    }
    return file
  } catch (error) {
    if (error && error.canceled) return null
    throw error
  }
}

async function start() {
  if (busy.value) return
  const tool = current.value
  busy.value = true
  result.value = null
  try {
    const picked = await pickFile(tool)
    if (!picked) return

    uploading.value = true
    const uploaded = await uploadFile(picked.path, picked.name)
    uploading.value = false

    converting.value = true
    // 转换耗时远大于普通接口：Word 转 PDF 走 LibreOffice 冷启动能到几十秒，
    // pdf2docx 处理大文件更久，所以这里单独放宽到 4 分钟，别用默认的 90 秒。
    const data = await request(tool.endpoint, {
      method: 'POST',
      data: { file_key: uploaded.file_key, file_name: picked.name },
      timeout: 240000,
    })
    result.value = data
    notify('转换完成', 'success')
  } catch (error) {
    notifyError(error)
  } finally {
    busy.value = false
    uploading.value = false
    converting.value = false
  }
}

function openResult() {
  if (!result.value) return
  const url = `${API_BASE_URL}/api/file/download?file_key=${encodeURIComponent(
    result.value.file_key
  )}`
  uni.downloadFile({
    url,
    // 走下载代理而不是预签名地址：后者指向存储服务，手机连不上；
    // 代理由后端鉴权，所以这里必须带上 token。
    header: { Authorization: `Bearer ${getToken()}` },
    success(response) {
      if (response.statusCode !== 200) {
        notify('下载失败，请稍后重试', 'error')
        return
      }
      uni.openDocument({
        filePath: response.tempFilePath,
        showMenu: true,
        fail() {
          notify('无法直接打开，可用其他应用查看', 'error')
        },
      })
    },
    fail() {
      notify('下载失败，请检查网络连接', 'error')
    },
  })
}

function reset() {
  result.value = null
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

.card {
  margin-bottom: 24rpx;
  padding: 28rpx;
  background: var(--zi-paper);
  border: var(--zi-card-border);
  border-radius: var(--zi-radius);
  box-shadow: var(--zi-card-shadow);
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

.tool-list {
  margin-top: 20rpx;
}

.tool {
  margin-bottom: 16rpx;
  padding: 22rpx 24rpx;
  border: var(--zi-button-border);
  border-radius: var(--zi-button-radius);
  background: var(--zi-soft-2);
}

.tool:last-child {
  margin-bottom: 0;
}

.tool.active {
  border-color: var(--zi-accent);
  background: var(--zi-accent);
}

.tool-name {
  display: block;
  color: var(--zi-ink);
  font-size: 28rpx;
  font-weight: 800;
  line-height: 1.5;
}

.tool-desc {
  display: block;
  margin-top: 4rpx;
  color: var(--zi-muted);
  font-size: 24rpx;
  line-height: 1.5;
}

.tool.active .tool-name,
.tool.active .tool-desc {
  color: var(--zi-accent-ink);
}

.hint {
  display: block;
  margin-top: 16rpx;
  color: var(--zi-muted);
  font-size: 24rpx;
  line-height: 1.7;
}

.action {
  width: 100%;
  height: 88rpx;
  margin: 20rpx 0 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border: var(--zi-button-border);
  border-radius: var(--zi-button-radius);
  background: transparent;
  color: var(--zi-ink);
  font-size: 28rpx;
  font-weight: 800;
}

.action.main {
  border-color: var(--zi-accent);
  background: var(--zi-accent);
  color: var(--zi-accent-ink);
}

.action.demo {
  border-style: dashed;
  background: var(--zi-soft-2);
  color: var(--zi-muted);
}

.action[disabled] {
  opacity: 0.6;
}

.file-row {
  margin-top: 16rpx;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.file-name {
  flex: 1;
  color: var(--zi-ink);
  font-size: 26rpx;
  font-weight: 700;
  line-height: 1.6;
  word-break: break-all;
}

.file-size {
  margin-left: 16rpx;
  color: var(--zi-muted);
  font-size: 24rpx;
}

.footnote {
  margin-top: 8rpx;
  color: var(--zi-muted);
  font-size: 22rpx;
  line-height: 1.7;
}
</style>
