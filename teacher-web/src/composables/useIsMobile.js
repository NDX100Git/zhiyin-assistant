/**
 * 视口断点：老师端要在柜台前用手机操作，窄屏必须换一套版式。
 *
 * 用 matchMedia 而不是监听 resize：省掉高频回调，也让断点只有一处。
 * 断点 768 像素，对应手机横屏与平板竖屏之间。
 */

import { onUnmounted, ref } from 'vue'

export const MOBILE_BREAKPOINT = 768

export function useIsMobile(breakpoint = MOBILE_BREAKPOINT) {
  const query = `(max-width: ${breakpoint - 1}px)`
  const media = typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia(query)
    : null
  const isMobile = ref(media ? media.matches : false)

  const onChange = (event) => {
    isMobile.value = event.matches
  }
  if (media && media.addEventListener) {
    media.addEventListener('change', onChange)
    onUnmounted(() => media.removeEventListener('change', onChange))
  }

  return isMobile
}
