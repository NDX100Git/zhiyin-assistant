<template>
  <router-view />
</template>

<script setup>
import { onMounted, onUnmounted } from 'vue'
import {
  UI_THEMES,
  applyUiScheme,
  applyUiTheme,
  getUiScheme,
  getUiTheme,
  themeClass,
  watchUiScheme,
} from './theme'

let stopWatching = null

/** 挂载时先按存储订皮肤，再按系统订深浅；系统深浅变了只换深浅，不动皮肤。 */
function applyAppearance() {
  const classes = UI_THEMES.map((theme) => `zi-theme-${theme}`)
  document.body.classList.remove(...classes)
  document.body.classList.add(themeClass(getUiTheme()))
  applyUiScheme(getUiScheme())
}

onMounted(() => {
  applyAppearance()
  stopWatching = watchUiScheme((scheme) => applyUiScheme(scheme))
})

onUnmounted(() => {
  if (stopWatching) stopWatching()
})
</script>
