<script setup lang="ts">
// 工具栏：左侧 `#lead`（标题 + 标签），右侧默认插槽（动作按钮）。
// dense 用于「面板内的小工具条」这种紧凑场合。
defineProps<{ dense?: boolean }>()
</script>

<template>
  <header class="toolbar" :class="{ 'toolbar--dense': dense }">
    <div class="toolbar__lead"><slot name="lead" /></div>
    <div class="toolbar__grow" />
    <div class="toolbar__actions"><slot /></div>
  </header>
</template>

<style scoped>
.toolbar {
  display: flex; align-items: center; gap: var(--sp-4);
  padding: var(--sp-3) var(--sp-4);
  background: var(--bg-1); border-bottom: 1px solid var(--line-soft);
}
.toolbar--dense { padding: var(--sp-2) var(--sp-3); }
.toolbar__lead { display: flex; align-items: center; gap: var(--sp-2); font-size: var(--fs-4); min-width: 0; }
.toolbar__grow { flex: 1; }
.toolbar__actions { display: flex; gap: var(--sp-2); }
</style>
