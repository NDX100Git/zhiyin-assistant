<template>
  <router-view />
</template>

<script setup>
import { onMounted } from 'vue'
import { UI_THEMES, getUiTheme, themeClass } from './theme'

onMounted(() => {
  const classes = UI_THEMES.map((theme) => `zi-theme-${theme}`)
  document.body.classList.remove(...classes)
  document.body.classList.add(themeClass(getUiTheme()))
})
</script>
