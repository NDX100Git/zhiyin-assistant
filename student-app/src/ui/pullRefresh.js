/**
 * 页面下拉刷新。
 *
 * 小程序端：走 pages.json 的 `enablePullDownRefresh` 与页面的 `onPullDownRefresh`，
 * 本文件在这里不做事。
 *
 * H5 端：这一版 uni-app 的 H5 运行时虽然会渲染下拉指示器，但下拉松手后不会把事件
 * 派发到页面的 `onPullDownRefresh`（实测：指示器停在「刷新中」不动，页面数据也不重拉）。
 * 所以 H5 用一个最小实现兜底：只在页面滚到顶部、手指向下拖时接管手势，
 * 松手超过阈值就调用刷新任务，并自己画一条提示。
 *
 * 用法：
 *   const { offset, refreshing, threshold } = usePullRefresh(load)
 * 模板里按 offset 撑开一条提示，refreshing 时显示「正在刷新…」。
 */

import { onMounted, onUnmounted, ref } from 'vue'

/** 松手触发刷新的位移（像素），比它小的回落。 */
export const PULL_THRESHOLD = 56
/** 提示区最大高度，避免拉出一条大片空白。 */
const MAX_OFFSET = 88
/** 手指位移到提示高度按这个系数缩放，手感与系统下拉接近。 */
const DAMPING = 0.5

export function usePullRefresh(task) {
  const offset = ref(0)
  const refreshing = ref(false)

  // #ifdef H5
  let startY = null
  let mounted = false

  function atTop() {
    return (window.scrollY || document.documentElement.scrollTop || 0) <= 0
  }

  function onTouchStart(event) {
    if (event.touches && event.touches.length > 1) return
    if (refreshing.value || !atTop()) {
      startY = null
      return
    }
    const touch = event.touches && event.touches[0]
    startY = touch ? touch.clientY : null
  }

  function onTouchMove(event) {
    if (startY === null || refreshing.value) return
    const touch = event.touches && event.touches[0]
    if (!touch) return
    const delta = touch.clientY - startY
    if (delta <= 0) {
      // 手指往上走：交回页面滚动，不要抢手势
      if (offset.value !== 0) offset.value = 0
      startY = touch.clientY
      return
    }
    if (!atTop()) {
      startY = null
      offset.value = 0
      return
    }
    offset.value = Math.min(MAX_OFFSET, delta * DAMPING)
    if (event.cancelable) event.preventDefault()
  }

  async function onTouchEnd() {
    if (startY === null) return
    startY = null
    if (refreshing.value) return
    const pulled = offset.value
    if (pulled < PULL_THRESHOLD) {
      offset.value = 0
      return
    }
    refreshing.value = true
    offset.value = PULL_THRESHOLD
    try {
      await task()
    } catch (error) {
      // 刷新任务自己会提示错误，这里只保证提示条收回去
    } finally {
      refreshing.value = false
      offset.value = 0
    }
  }

  onMounted(() => {
    if (typeof document === 'undefined') return
    mounted = true
    document.addEventListener('touchstart', onTouchStart, { passive: true })
    document.addEventListener('touchmove', onTouchMove, { passive: false })
    document.addEventListener('touchend', onTouchEnd)
    document.addEventListener('touchcancel', onTouchEnd)
  })

  onUnmounted(() => {
    if (!mounted) return
    document.removeEventListener('touchstart', onTouchStart)
    document.removeEventListener('touchmove', onTouchMove)
    document.removeEventListener('touchend', onTouchEnd)
    document.removeEventListener('touchcancel', onTouchEnd)
  })
  // #endif

  return { offset, refreshing, threshold: PULL_THRESHOLD }
}
