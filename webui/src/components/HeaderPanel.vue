<script setup lang="ts">
import type { HeaderItem } from '../types'
import { EmptyState, Panel, Tag } from './ui'

defineProps<{ headers: HeaderItem[] }>()
</script>

<template>
  <Panel class="headers" title="请求头">
    <template #actions><Tag>只读</Tag></template>

    <template #hint>
      值一律以掩码显示：<strong>明文永不进前端</strong>（交付文档 §3 凭据红线）。
    </template>

    <ul class="list">
      <li v-for="h in headers" :key="h.name" class="item">
        <div class="item__top">
          <code class="name">{{ h.name }}</code>
          <Tag v-if="h.sensitive" tone="warn">敏感</Tag>
          <Tag v-if="h.env" :tone="h.env_set ? 'ok' : 'warn'">
            ${ENV:{{ h.env }}} {{ h.env_set ? '已设置' : '未设置' }}
          </Tag>
        </div>
        <div class="value" :class="{ 'value--masked': h.sensitive }">{{ h.value }}</div>
      </li>
    </ul>

    <EmptyState v-if="!headers.length" icon="cookie" text="没有自定义请求头" />
  </Panel>
</template>

<style scoped>
.headers { min-height: 0; }
.list { list-style: none; margin: 0; padding: var(--sp-3); }
.item { padding: var(--sp-2) 0; border-bottom: 1px dashed var(--line-soft); }
.item:last-child { border-bottom: none; }
.item__top { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.name { color: #9ecbff; font-size: var(--fs-2); }
.value { font-family: var(--font-mono); font-size: var(--fs-1); color: var(--fg-1); word-break: break-all; margin-top: 2px; }
.value--masked { color: var(--warn); }
</style>
