<template>
  <div class="wrap">
    <el-card class="card" shadow="always">
      <div class="brand">智印助手</div>
      <div class="sub">文印室管理后台</div>
      <el-form :model="form" label-position="top" @submit.prevent="submit">
        <el-form-item label="管理员账号">
          <el-input v-model="form.username" placeholder="请输入账号" size="large" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="请输入密码"
            size="large"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button type="primary" size="large" class="btn" :loading="loading" @click="submit">
          登录
        </el-button>
      </el-form>
      <div class="hint">账号密码由服务端统一配置，请联系系统管理员</div>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { login } from '../api/admin'

const router = useRouter()
const loading = ref(false)
const form = reactive({ username: '', password: '' })

async function submit() {
  if (!form.username || !form.password) {
    ElMessage.warning('请输入账号和密码')
    return
  }
  loading.value = true
  try {
    const data = await login(form.username.trim(), form.password)
    localStorage.setItem('zhiyin_admin_token', data.token)
    localStorage.setItem('zhiyin_admin_user', JSON.stringify(data.admin || {}))
    ElMessage.success('登录成功')
    router.replace('/dashboard')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background-color: var(--zi-bg);
  background-image: var(--zi-bg-image);
}
.card {
  width: 400px;
  background: var(--zi-paper);
  border: var(--zi-card-border);
  border-top: 8px solid var(--zi-accent);
  border-radius: var(--zi-radius);
  box-shadow: 12px 12px 0 rgba(23, 22, 26, 0.12);
}
.card :deep(.el-card__body) {
  padding: 28px;
}
.brand {
  display: inline-block;
  padding-bottom: 8px;
  border-bottom: 6px solid var(--zi-accent);
  color: var(--zi-ink);
  font-size: 30px;
  font-weight: 900;
  letter-spacing: 0.04em;
  line-height: 1.15;
}
.sub {
  margin: 10px 0 24px;
  color: var(--zi-muted);
  font-size: 13px;
  font-weight: 600;
}
.btn {
  width: 100%;
  height: 48px;
  font-size: 16px;
  font-weight: 800;
  letter-spacing: 0.08em;
}
.hint {
  margin-top: 18px;
  padding-left: 12px;
  border-left: 4px solid var(--zi-warn);
  color: var(--zi-muted);
  font-size: 12px;
  line-height: 1.6;
}

/* 旧蓝白皮肤 */
.zi-theme-classic .card {
  border: none;
  border-top: none;
  border-radius: 16px;
  box-shadow: 0 16px 40px rgba(31, 45, 61, 0.12);
}
.zi-theme-classic .brand {
  padding-bottom: 0;
  border-bottom: none;
  font-weight: 700;
  letter-spacing: 0;
}
.zi-theme-classic .sub {
  font-weight: 400;
}
.zi-theme-classic .btn {
  font-weight: 600;
  letter-spacing: 0;
}
.zi-theme-classic .hint {
  padding-left: 0;
  border-left: none;
}
</style>
