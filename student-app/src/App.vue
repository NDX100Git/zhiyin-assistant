<script>
import { ensureLogin } from '@/services/auth'

export default {
  onLaunch() {
    // 启动即静默登录（设备标识），失败不影响浏览，页面会再兜底一次
    ensureLogin().catch((error) => console.warn('[auth] 静默登录失败', error))
  },
}
</script>

<style>
page {
  background: #f5f7fb;
  color: #1f2d3d;
  font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
button::after {
  border: none;
}
input {
  outline: none;
}
</style>
