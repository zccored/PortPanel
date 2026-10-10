<script setup lang="ts">
import Icon from './Icon.vue'

// 空状态：列表/表格/面板没内容时**统一用它**，别再各写一行 `.empty muted`。
withDefaults(defineProps<{ text: string; icon?: string; hint?: string }>(), {
  icon: '',
  hint: '',
})
</script>

<template>
  <div class="empty-state muted">
    <Icon v-if="icon" :name="icon" size="24px" />
    <div class="empty-state__text">{{ text }}</div>
    <div v-if="hint" class="empty-state__hint">{{ hint }}</div>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: var(--sp-2); padding: var(--sp-5); text-align: center; font-size: var(--fs-2);
}
.empty-state__hint { font-size: var(--fs-1); opacity: 0.8; }
</style>
