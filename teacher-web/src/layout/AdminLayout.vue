<template>
  <el-container class="layout" :class="{ 'is-mobile': isMobile }">
    <el-aside v-if="!isMobile" width="200px" class="aside">
      <div class="brand">智印助手</div>
      <div class="brand-sub">文印室后台</div>
      <el-menu :default-active="route.path" router>
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
        <div class="title-wrap">
          <el-button
            v-if="isMobile"
            class="menu-trigger"
            text
            aria-label="打开菜单"
            @click="drawerVisible = true"
          >
            <el-icon :size="22"><Menu /></el-icon>
          </el-button>
          <div class="title">{{ route.meta.title || '工作台' }}</div>
        </div>
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

    <!-- 手机上侧边栏改成抽屉：柜台前一只手就能切页面，不占宽度 -->
    <el-drawer v-model="drawerVisible" direction="ltr" size="240px" :with-header="false">
      <div class="drawer-body">
        <div class="brand">智印助手</div>
        <div class="brand-sub">文印室后台</div>
        <el-menu :default-active="route.path" router @select="drawerVisible = false">
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
        <el-button class="drawer-logout" @click="logout">退出登录</el-button>
      </div>
    </el-drawer>
  </el-container>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { DataBoard, Menu, Money, Setting } from '@element-plus/icons-vue'
import { useIsMobile } from '../composables/useIsMobile'

const route = useRoute()
const router = useRouter()
const isMobile = useIsMobile()
const drawerVisible = ref(false)

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
  background: var(--zi-bg);
}
.aside {
  background: var(--zi-aside-bg);
  color: var(--zi-aside-active);
  padding-top: 18px;
  border-right: 6px solid var(--zi-accent);
}
.brand {
  margin: 0 12px;
  padding: 0 0 0 12px;
  border-left: 8px solid var(--zi-accent);
  color: var(--zi-aside-active);
  font-size: 21px;
  font-weight: 900;
  letter-spacing: 0.04em;
  line-height: 1.2;
}
.brand-sub {
  margin-top: 6px;
  padding: 0 20px 18px 32px;
  color: var(--zi-aside-text);
  font-size: 12px;
  font-weight: 600;
}
.aside :deep(.el-menu) {
  border-right: none;
  background: transparent;
  --el-menu-bg-color: transparent;
  --el-menu-text-color: var(--zi-aside-text);
  --el-menu-active-color: var(--zi-aside-active);
  --el-menu-hover-bg-color: var(--zi-aside-hover);
}
.aside :deep(.el-menu-item) {
  height: 48px;
  font-weight: 700;
  letter-spacing: 0.02em;
}
.aside :deep(.el-menu-item.is-active) {
  background: var(--zi-accent);
  color: var(--zi-accent-ink);
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--zi-header-bg);
  border-bottom: var(--zi-header-border);
}
.title {
  color: var(--zi-ink);
  font-size: 20px;
  font-weight: 900;
  letter-spacing: 0.03em;
}
.right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.admin {
  color: var(--zi-muted);
  font-size: 13px;
  font-weight: 600;
}
.main {
  background: transparent;
  padding: 18px;
}
.title-wrap {
  display: flex;
  align-items: center;
  gap: 4px;
}
.menu-trigger {
  padding: 0 6px;
  color: var(--zi-ink);
}
.drawer-body {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 18px 0;
  background: var(--zi-aside-bg);
}
.drawer-body :deep(.el-menu) {
  border-right: none;
  background: transparent;
  --el-menu-bg-color: transparent;
  --el-menu-text-color: var(--zi-aside-text);
  --el-menu-active-color: var(--zi-aside-active);
  --el-menu-hover-bg-color: var(--zi-aside-hover);
}
.drawer-body :deep(.el-menu-item) {
  height: 52px;
  font-weight: 700;
}
.drawer-body :deep(.el-menu-item.is-active) {
  background: var(--zi-accent);
  color: var(--zi-accent-ink);
}
.drawer-logout {
  margin: auto 16px 8px;
  min-height: 44px;
}

/* 窄屏：标题缩小、右侧信息收起，把宽度全留给队列 */
.is-mobile .header {
  padding: 0 10px;
}
.is-mobile .title {
  font-size: 17px;
}
.is-mobile .admin,
.is-mobile .right :deep(.el-tag) {
  display: none;
}
.is-mobile .main {
  padding: 10px;
}

/* 旧蓝白皮肤 */
.zi-theme-classic .aside {
  border-right: none;
}
.zi-theme-classic .brand {
  margin-left: 0;
  padding-left: 20px;
  border-left: none;
  font-weight: 700;
  letter-spacing: 0;
}
.zi-theme-classic .brand-sub {
  padding-left: 20px;
  font-weight: 400;
}
.zi-theme-classic .aside :deep(.el-menu-item) {
  font-weight: 500;
  letter-spacing: 0;
}
.zi-theme-classic .title {
  font-weight: 600;
  letter-spacing: 0;
}
</style>
