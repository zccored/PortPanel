<script setup lang="ts">
import type { Tone } from './types'

// 标签：语义色调 → base.css 的 .tag--*；default 只有描边。
withDefaults(defineProps<{ tone?: Tone; title?: string }>(), { tone: 'default', title: '' })
</script>

<template>
  <span class="tag" :class="tone !== 'default' ? `tag--${tone}` : undefined" :title="title || undefined">
    <slot />
  </span>
</template>
