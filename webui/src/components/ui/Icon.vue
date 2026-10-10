<script setup lang="ts">
import { computed } from 'vue'
import { ICONS } from '../../assets/icons'

// 图标一律走这里：几何来自 design/icons.json（生成物），颜色用 currentColor，
// 尺寸随字号（1em）—— 不再用 emoji 当图标。size 直接给字体大小，用于放大/缩小（如空状态）。
const props = withDefaults(defineProps<{ name: string; title?: string; size?: string }>(), {
  title: '',
  size: '',
})
const html = computed(() => ICONS[props.name] ?? '')
const style = computed(() => (props.size ? { fontSize: props.size } : undefined))
</script>

<template>
  <span class="icon-slot" :style="style" :title="title || undefined" v-html="html" />
</template>

<style scoped>
.icon-slot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1em;
  height: 1em;
  line-height: 0;
}
</style>
