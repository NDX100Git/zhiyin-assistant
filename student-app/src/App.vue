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
  background: #e9e3d4;
  color: #17161a;
  font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
/* 页面底衬跟着系统深浅走；页面内容区由下面的主题变量负责 */
@media (prefers-color-scheme: dark) {
  page {
    background: #14130f;
    color: #ece7dc;
  }
}
button::after {
  border: none;
}
input {
  outline: none;
}
/* uni H5 的输入占位文字是固定灰（#808080），深色下读不动，统一走变量 */
.uni-input-placeholder,
.uni-textarea-placeholder {
  color: var(--zi-placeholder, #808080);
}

/*
 * 界面主题变量。
 * 页面根节点加 zi-theme-press / zi-theme-signal / zi-theme-classic 即可换风格，
 * 只影响颜色、边框、圆角与阴影，不涉及任何业务数据。
 * 主题入口见 src/ui/theme.js。
 */
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
  --zi-line-strong: #8e8370;
  --zi-soft: #f1ecdf;
  --zi-soft-2: #f7f1e4;
  --zi-accent: #d02d10;
  --zi-accent-ink: #ffffff;
  --zi-ok: #2f6b3f;
  --zi-ok-bg: #e4ecdf;
  --zi-warn: #8a4b00;
  --zi-warn-bg: #f4e5c8;
  --zi-danger: #b3261e;
  --zi-danger-bg: #f6ded9;
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
  /* 实心地块（按钮/角标）上的字色，与语义色分开，深色下才不会白字压亮底 */
  --zi-on-solid: #ffffff;
  --zi-ok-solid: #2f6b3f;
  --zi-warn-solid: #8a4b00;
  --zi-danger-solid: #b3261e;
  /* 弹层遮罩 */
  --zi-overlay: rgba(23, 22, 26, 0.42);
  /* 输入占位文字 */
  --zi-placeholder: #808080;
  /* 纸面（打印出来的东西、订单里的工单纸）不受深浅影响，深浅都用这一组 */
  --zi-print-paper: #f8f4e9;
  --zi-print-ink: #17161a;
  --zi-button-border: 3rpx solid #17161a;
  --zi-button-radius: 0rpx;
  --zi-pill-radius: 0rpx;
  --zi-control-radius: 0rpx;
  --zi-primary-shadow: none;
}

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
  --zi-line-strong: #8a8377;
  --zi-soft: #efe9dd;
  --zi-soft-2: #f6f0e6;
  --zi-accent: #d22b1e;
  --zi-accent-ink: #ffffff;
  --zi-ok: #1f6b3a;
  --zi-ok-bg: #e3efe4;
  --zi-warn: #8a4a00;
  --zi-warn-bg: #f5e6c8;
  --zi-danger: #b3261e;
  --zi-danger-bg: #f6ded9;
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
  --zi-on-solid: #ffffff;
  --zi-ok-solid: #1f6b3a;
  --zi-warn-solid: #8a4a00;
  --zi-danger-solid: #b3261e;
  --zi-overlay: rgba(18, 17, 16, 0.44);
  --zi-placeholder: #808080;
  --zi-print-paper: #fbf8f1;
  --zi-print-ink: #121110;
  --zi-button-border: 4rpx solid #121110;
  --zi-button-radius: 0rpx;
  --zi-pill-radius: 0rpx;
  --zi-control-radius: 0rpx;
  --zi-primary-shadow: 6rpx 6rpx 0 #121110;
}

.zi-theme-classic {
  --zi-bg: #f5f7fb;
  --zi-bg-image: none;
  --zi-paper: #ffffff;
  --zi-ink: #1f2d3d;
  --zi-muted: #7a869a;
  --zi-line: #e3e8f0;
  --zi-line-strong: #c9d3e0;
  --zi-soft: #f2f4f8;
  --zi-soft-2: #f7f9fc;
  --zi-accent: #2f6bff;
  --zi-accent-ink: #ffffff;
  --zi-ok: #3f8f5c;
  --zi-ok-bg: #eaf7ef;
  --zi-warn: #b25e09;
  --zi-warn-bg: #fff4e6;
  --zi-danger: #c0392b;
  --zi-danger-bg: #fdecea;
  --zi-hero-bg: #2f6bff;
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
  --zi-on-solid: #ffffff;
  --zi-ok-solid: #3f8f5c;
  --zi-warn-solid: #b25e09;
  --zi-danger-solid: #c0392b;
  --zi-overlay: rgba(31, 45, 61, 0.45);
  --zi-placeholder: #808080;
  --zi-print-paper: #ffffff;
  --zi-print-ink: #1f2d3d;
  --zi-button-border: 0 solid transparent;
  --zi-button-radius: 16rpx;
  --zi-pill-radius: 999rpx;
  --zi-control-radius: 16rpx;
  --zi-primary-shadow: none;
}
/* ---------------------------------------------------------------------------
 * 深色配色（跟随系统 prefers-color-scheme，由页面根节点的 zi-scheme-dark 触发）
 * 每套皮肤一份，只覆盖颜色类变量：圆角、边框粗细与版式沿用浅色那份。
 * 正文与背景对比度全部不低于 4.5:1，边框线也保留在 1.6:1 以上。
 * ------------------------------------------------------------------------- */

.zi-theme-press.zi-scheme-dark {
  --zi-bg: #17150f;
  --zi-bg-image: repeating-linear-gradient(
      0deg,
      rgba(236, 231, 220, 0.06) 0,
      rgba(236, 231, 220, 0.06) 1rpx,
      transparent 1rpx,
      transparent 32rpx
    ),
    repeating-linear-gradient(
      90deg,
      rgba(236, 231, 220, 0.06) 0,
      rgba(236, 231, 220, 0.06) 1rpx,
      transparent 1rpx,
      transparent 32rpx
    );
  --zi-paper: #221f18;
  --zi-ink: #ece7dc;
  --zi-muted: #a9a291;
  --zi-line: #4a4436;
  --zi-line-strong: #91897a;
  --zi-soft: #262218;
  --zi-soft-2: #2b271d;
  --zi-accent: #ff7a5c;
  --zi-accent-ink: #1a1811;
  --zi-ok: #86d5a0;
  --zi-ok-bg: #1e2a22;
  --zi-warn: #f3bd74;
  --zi-warn-bg: #32281a;
  --zi-danger: #ff9d92;
  --zi-danger-bg: #3a211d;
  --zi-hero-bg: #221f18;
  --zi-hero-ink: #ece7dc;
  --zi-hero-muted: #a9a291;
  --zi-hero-line: rgba(236, 231, 220, 0.22);
  --zi-card-border: 3rpx solid #91897a;
  --zi-hero-border: 3rpx solid #91897a;
  --zi-primary-bg: #e4ddcd;
  --zi-primary-ink: #1a1811;
  --zi-ghost-bg: transparent;
  --zi-ghost-ink: #ece7dc;
  --zi-button-border: 3rpx solid #91897a;
  --zi-on-solid: #f7f3ea;
  --zi-ok-solid: #2b7346;
  --zi-warn-solid: #8a5a12;
  --zi-danger-solid: #a8352a;
  --zi-overlay: rgba(8, 7, 5, 0.66);
  --zi-placeholder: #91897a;
}

.zi-theme-signal.zi-scheme-dark {
  --zi-bg: #14120f;
  --zi-bg-image: repeating-linear-gradient(
    135deg,
    transparent 0,
    transparent 26rpx,
    rgba(255, 96, 68, 0.07) 26rpx,
    rgba(255, 96, 68, 0.07) 28rpx
  );
  --zi-paper: #1f1c19;
  --zi-ink: #f3efe6;
  --zi-muted: #a9a196;
  --zi-line: #474138;
  --zi-line-strong: #91897a;
  --zi-soft: #262220;
  --zi-soft-2: #2b2623;
  --zi-accent: #ff6044;
  --zi-accent-ink: #1a1613;
  --zi-ok: #8ad6a4;
  --zi-ok-bg: #1d2b22;
  --zi-warn: #f4c078;
  --zi-warn-bg: #33291a;
  --zi-danger: #ffa095;
  --zi-danger-bg: #3b211d;
  --zi-hero-bg: #221f1c;
  --zi-hero-ink: #f3efe6;
  --zi-hero-muted: #b8b1a4;
  --zi-hero-line: rgba(243, 239, 230, 0.22);
  --zi-card-border: 4rpx solid #91897a;
  --zi-hero-border: 4rpx solid #91897a;
  --zi-hero-shadow: 10rpx 10rpx 0 #ff6044;
  --zi-primary-bg: #ff6044;
  --zi-primary-ink: #1a1613;
  --zi-ghost-bg: transparent;
  --zi-ghost-ink: #f3efe6;
  --zi-button-border: 4rpx solid #91897a;
  --zi-primary-shadow: 6rpx 6rpx 0 #8b8476;
  --zi-on-solid: #f8f4ec;
  --zi-ok-solid: #2b7346;
  --zi-warn-solid: #8a5a12;
  --zi-danger-solid: #b03a2c;
  --zi-overlay: rgba(7, 6, 5, 0.68);
  --zi-placeholder: #91897a;
}

.zi-theme-classic.zi-scheme-dark {
  --zi-bg: #12161d;
  --zi-bg-image: none;
  --zi-paper: #1b2029;
  --zi-ink: #e6ebf2;
  --zi-muted: #9fabbc;
  --zi-line: #3a4658;
  --zi-line-strong: #8b99b0;
  --zi-soft: #222a35;
  --zi-soft-2: #262f3b;
  --zi-accent: #7aa7ff;
  --zi-accent-ink: #111828;
  --zi-ok: #6fd39a;
  --zi-ok-bg: #1b2b23;
  --zi-warn: #f0b968;
  --zi-warn-bg: #2e2717;
  --zi-danger: #ff9a8f;
  --zi-danger-bg: #34201e;
  --zi-hero-bg: #22406f;
  --zi-hero-ink: #eef3fb;
  --zi-hero-muted: #c2d1e6;
  --zi-hero-line: rgba(238, 243, 251, 0.25);
  --zi-card-border: 0 solid transparent;
  --zi-card-shadow: 0 8rpx 24rpx rgba(0, 0, 0, 0.42);
  --zi-hero-border: 0 solid transparent;
  --zi-primary-bg: #7aa7ff;
  --zi-primary-ink: #111828;
  --zi-ghost-bg: #1e2632;
  --zi-ghost-ink: #7aa7ff;
  --zi-button-border: 0 solid transparent;
  --zi-on-solid: #f6f9ff;
  --zi-ok-solid: #2b7346;
  --zi-warn-solid: #8a5a12;
  --zi-danger-solid: #a8352a;
  --zi-overlay: rgba(6, 9, 14, 0.68);
  --zi-placeholder: #8b99b0;
}
</style>
