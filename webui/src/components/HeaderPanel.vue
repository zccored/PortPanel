<script setup lang="ts">
import type { HeaderItem } from '../types'

defineProps<{ headers: HeaderItem[] }>()
</script>

<template>
  <div class="panel headers">
    <div class="headers__head">
      <strong>请求头</strong>
      <span class="tag">只读</span>
    </div>
    <div class="headers__hint muted">
      值一律以掩码显示：<strong>明文永不进前端</strong>（交付文档 §3 凭据红线）。
    </div>
    <ul class="list">
      <li v-for="h in headers" :key="h.name" class="item">
        <div class="item__top">
          <code class="name">{{ h.name }}</code>
          <span v-if="h.sensitive" class="tag tag--warn">敏感</span>
          <span v-if="h.env" class="tag" :class="h.env_set ? 'tag--ok' : 'tag--warn'">
            ${ENV:{{ h.env }}} {{ h.env_set ? '已设置' : '未设置' }}
          </span>
        </div>
        <div class="value" :class="{ 'value--masked': h.sensitive }">{{ h.value }}</div>
      </li>
      <li v-if="!headers.length" class="empty muted">没有自定义请求头</li>
    </ul>
  </div>
</template>

<style scoped>
.headers { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.headers__head { display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-3) var(--sp-4); border-bottom: 1px solid var(--line-soft); }
.headers__hint { padding: var(--sp-2) var(--sp-4); font-size: var(--fs-1); border-bottom: 1px solid var(--line-soft); }
.list { list-style: none; margin: 0; padding: var(--sp-3); overflow: auto; flex: 1; min-height: 0; }
.item { padding: var(--sp-2) 0; border-bottom: 1px dashed var(--line-soft); }
.item:last-child { border-bottom: none; }
.item__top { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.name { color: #9ecbff; font-size: var(--fs-2); }
.value { font-family: var(--font-mono); font-size: var(--fs-1); color: var(--fg-1); word-break: break-all; margin-top: 2px; }
.value--masked { color: var(--warn); }
.empty { padding: var(--sp-4); text-align: center; font-size: var(--fs-2); }
</style>
