<script>
import { warnOnPlatformConfig } from '@/config'
import { ensureLogin } from '@/services/auth'

export default {
  onLaunch() {
    warnOnPlatformConfig()
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

/*
 * 界面主题变量。
 * 页面根节点加 zi-theme-signal / zi-theme-press / zi-theme-classic 即可换风格，
 * 只影响颜色、边框、圆角与阴影，不涉及任何业务数据。
 * 主题入口见 src/ui/theme.js。
 */
.zi-theme-signal {
  --zi-bg: #ece7dc;
  --zi-bg-image: repeating-linear-gradient(
    135deg,
    transparent 0,
    transparent 26rpx,
    rgba(210, 43, 30, 0.05) 26rpx,
    rgba(210, 43, 30, 0.05) 28rpx
  );
  --zi-paper: #fbf8f1;
  --zi-ink: #121110;
  --zi-muted: #5c574e;
  --zi-line: #d9d2c3;
  --zi-accent: #d22b1e;
  --zi-accent-ink: #ffffff;
  --zi-hero-bg: #121110;
  --zi-hero-ink: #fbf8f1;
  --zi-hero-muted: #bdb6a8;
  --zi-hero-line: rgba(251, 248, 241, 0.22);
  --zi-radius: 0rpx;
  --zi-card-border: 4rpx solid #121110;
  --zi-card-shadow: none;
  --zi-hero-border: 4rpx solid #121110;
  --zi-hero-shadow: 10rpx 10rpx 0 #d22b1e;
  --zi-primary-bg: #d22b1e;
  --zi-primary-ink: #ffffff;
  --zi-ghost-bg: transparent;
  --zi-ghost-ink: #121110;
  --zi-button-border: 4rpx solid #121110;
  --zi-button-radius: 0rpx;
  --zi-primary-shadow: 6rpx 6rpx 0 #121110;
}

.zi-theme-press {
  --zi-bg: #e9e3d4;
  --zi-bg-image: repeating-linear-gradient(
      0deg,
      rgba(23, 22, 26, 0.07) 0,
      rgba(23, 22, 26, 0.07) 1rpx,
      transparent 1rpx,
      transparent 32rpx
    ),
    repeating-linear-gradient(
      90deg,
      rgba(23, 22, 26, 0.07) 0,
      rgba(23, 22, 26, 0.07) 1rpx,
      transparent 1rpx,
      transparent 32rpx
    );
  --zi-paper: #f8f4e9;
  --zi-ink: #17161a;
  --zi-muted: #67614f;
  --zi-line: #c3b9a4;
  --zi-accent: #d02d10;
  --zi-accent-ink: #ffffff;
  --zi-hero-bg: #f8f4e9;
  --zi-hero-ink: #17161a;
  --zi-hero-muted: #67614f;
  --zi-hero-line: rgba(23, 22, 26, 0.22);
  --zi-radius: 0rpx;
  --zi-card-border: 3rpx solid #17161a;
  --zi-card-shadow: none;
  --zi-hero-border: 3rpx solid #17161a;
  --zi-hero-shadow: none;
  --zi-primary-bg: #17161a;
  --zi-primary-ink: #f8f4e9;
  --zi-ghost-bg: transparent;
  --zi-ghost-ink: #17161a;
  --zi-button-border: 3rpx solid #17161a;
  --zi-button-radius: 0rpx;
  --zi-primary-shadow: none;
}

.zi-theme-classic {
  --zi-bg: #f5f7fb;
  --zi-bg-image: none;
  --zi-paper: #ffffff;
  --zi-ink: #1f2d3d;
  --zi-muted: #9aa7b5;
  --zi-line: #f2f4f8;
  --zi-accent: #2f6bff;
  --zi-accent-ink: #ffffff;
  --zi-hero-bg: linear-gradient(135deg, #2f6bff 0%, #5b8cff 60%, #7aa2ff 100%);
  --zi-hero-ink: #ffffff;
  --zi-hero-muted: rgba(255, 255, 255, 0.85);
  --zi-hero-line: rgba(255, 255, 255, 0.25);
  --zi-radius: 24rpx;
  --zi-card-border: 0 solid transparent;
  --zi-card-shadow: 0 8rpx 24rpx rgba(31, 45, 61, 0.06);
  --zi-hero-border: 0 solid transparent;
  --zi-hero-shadow: none;
  --zi-primary-bg: #2f6bff;
  --zi-primary-ink: #ffffff;
  --zi-ghost-bg: #eef3ff;
  --zi-ghost-ink: #2f6bff;
  --zi-button-border: 0 solid transparent;
  --zi-button-radius: 16rpx;
  --zi-primary-shadow: none;
}
</style>
