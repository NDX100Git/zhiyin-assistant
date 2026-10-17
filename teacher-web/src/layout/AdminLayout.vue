<template>
  <el-container class="layout">
    <el-aside width="200px" class="aside">
      <div class="brand">智印助手</div>
      <div class="brand-sub">文印室后台</div>
      <el-menu
        :default-active="route.path"
        router
        background-color="#1f2d3d"
        text-color="#c7d2df"
        active-text-color="#ffffff"
      >
        <el-menu-item index="/dashboard">
          <el-icon><DataBoard /></el-icon>
          <span>工作台</span>
        </el-menu-item>
        <el-menu-item index="/transactions">
          <el-icon><Money /></el-icon>
          <span>收款流水</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>收单与公告</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="title">{{ route.meta.title || '工作台' }}</div>
        <div class="right">
          <el-tag type="success" effect="plain" size="small">服务在线</el-tag>
          <span class="admin">{{ adminName }}</span>
          <el-button link type="primary" @click="logout">退出登录</el-button>
        </div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { DataBoard, Money, Setting } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()

const adminName = computed(() => {
  const raw = localStorage.getItem('zhiyin_admin_user')
  if (!raw) return '管理员'
  try {
    return JSON.parse(raw).username || '管理员'
  } catch {
    return '管理员'
  }
})

function logout() {
  localStorage.removeItem('zhiyin_admin_token')
  localStorage.removeItem('zhiyin_admin_user')
  router.replace('/login')
}
</script>

<style scoped>
.layout {
  height: 100vh;
}
.aside {
  background: #1f2d3d;
  color: #fff;
  padding-top: 18px;
}
.brand {
  padding: 0 20px;
  font-size: 20px;
  font-weight: 700;
}
.brand-sub {
  padding: 4px 20px 18px;
  font-size: 12px;
  color: #8fa3b8;
}
.aside :deep(.el-menu) {
  border-right: none;
}
.aside :deep(.el-menu-item.is-active) {
  background: #2f6bff;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #eef1f6;
}
.title {
  font-size: 18px;
  font-weight: 600;
}
.right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.admin {
  font-size: 14px;
  color: #4b5a6b;
}
.main {
  background: #f5f7fb;
  padding: 18px;
}
</style>
