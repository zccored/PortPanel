<script setup lang="ts">
// 面板：head（标题 + 元信息 + 操作）+ hint（一行说明）+ body（滚动）+ footer（可选）。
// body **不加内边距** —— 列表 / 表格 / 表单各有各的密度，padding 由内容自己决定。
// `#head` 槽可整体替换表头（例如把搜索框放进表头）；那时右对齐要自己用 flex 撑开，
// 因为默认的占位撑杆只在没有 `#head` 时才渲染。
defineProps<{ title?: string; meta?: string }>()
</script>

<template>
  <section class="panel panel-box">
    <header v-if="title || meta || $slots.head || $slots.actions" class="panel-box__head">
      <slot name="head">
        <strong v-if="title" class="panel-box__title">{{ title }}</strong>
        <span v-if="meta" class="panel-box__meta muted">{{ meta }}</span>
      </slot>
      <span v-if="!$slots.head" class="panel-box__grow" />
      <slot name="actions" />
    </header>

    <div v-if="$slots.hint" class="panel-box__hint muted"><slot name="hint" /></div>

    <div class="panel-box__body"><slot /></div>

    <footer v-if="$slots.footer" class="panel-box__foot"><slot name="footer" /></footer>
  </section>
</template>

<style scoped>
.panel-box { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.panel-box__head {
  display: flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4); border-bottom: 1px solid var(--line-soft);
}
.panel-box__title { font-size: var(--fs-3); }
.panel-box__meta { font-size: var(--fs-2); }
.panel-box__grow { flex: 1; }
.panel-box__hint { padding: var(--sp-2) var(--sp-4); font-size: var(--fs-2); border-bottom: 1px solid var(--line-soft); }
.panel-box__body { flex: 1; min-height: 0; overflow: auto; }
.panel-box__foot {
  padding: var(--sp-2) var(--sp-4); border-top: 1px solid var(--line-soft); font-size: var(--fs-2);
}
</style>
