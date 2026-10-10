<script setup lang="ts">
// 表单字段：标签在上、控件在中间、可选说明在下。控件由默认插槽给（input / select / 自定义）。
defineProps<{ label: string; hint?: string; grow?: boolean }>()
</script>

<template>
  <label class="field" :class="{ 'field--grow': grow }">
    <span class="field__label muted">{{ label }}</span>
    <slot />
    <span v-if="hint" class="field__hint muted">{{ hint }}</span>
  </label>
</template>

<style scoped>
.field { display: flex; flex-direction: column; gap: var(--sp-1); font-size: var(--fs-2); min-width: 0; }
.field--grow { flex: 1; }
.field__hint { font-size: var(--fs-1); }
</style>
