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
      <div class="hint">账号密码配置在服务端 backend/.env（ADMIN_USERNAME / ADMIN_PASSWORD）</div>
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
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #2f6bff 0%, #5b8cff 55%, #7aa2ff 100%);
}
.card {
  width: 380px;
  border-radius: 16px;
}
.brand {
  font-size: 26px;
  font-weight: 700;
  color: #1f2d3d;
}
.sub {
  margin: 4px 0 22px;
  font-size: 13px;
  color: #8b98a8;
}
.btn {
  width: 100%;
}
.hint {
  margin-top: 16px;
  font-size: 12px;
  color: #9aa7b5;
  line-height: 1.6;
}
</style>
