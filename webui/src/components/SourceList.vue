<script setup lang="ts">
import { computed } from 'vue'
import type { ApiSource } from '../types'
import { EmptyState, Icon, Panel, Tag } from './ui'

const props = defineProps<{ sources: ApiSource[]; currentId: string; keyword: string }>()
const emit = defineEmits<{
  (e: 'select', id: string): void
  (e: 'add'): void
  (e: 'remove', id: string): void
  (e: 'update:keyword', v: string): void
}>()

const filtered = computed(() => {
  const k = props.keyword.trim().toLowerCase()
  if (!k) return props.sources
  return props.sources.filter(
    (s) =>
      s.name.toLowerCase().includes(k) ||
      s.base_url.toLowerCase().includes(k) ||
      s.endpoints.some((e) => e.path.toLowerCase().includes(k)),
  )
})

function onInput(ev: Event) {
  emit('update:keyword', (ev.target as HTMLInputElement).value)
}
</script>

<template>
  <Panel class="sources">
    <template #head>
      <input class="input src-search" :value="keyword" placeholder="搜索入口 / 路径…" @input="onInput" />
    </template>
    <template #actions>
      <button class="btn btn--ghost" title="新建入口" @click="emit('add')">
        <Icon name="plus" title="新建入口" />
      </button>
    </template>

    <ul class="list">
      <li
        v-for="s in filtered"
        :key="s.id"
        class="item"
        :class="{ 'item--active': s.id === currentId }"
        @click="emit('select', s.id)"
      >
        <div class="item__main">
          <span class="item__name">{{ s.name }}</span>
          <span class="item__url muted">{{ s.base_url }}</span>
        </div>
        <div class="item__side">
          <Tag>{{ s.endpoints.length }}</Tag>
          <button class="x" title="删除该入口" @click.stop="emit('remove', s.id)">
            <Icon name="close" title="删除该入口" />
          </button>
        </div>
      </li>
    </ul>

    <EmptyState v-if="!filtered.length" icon="folder" text="没有匹配的入口" hint="换个关键词，或点右上 ＋ 新建" />
  </Panel>
</template>

<style scoped>
.sources { height: 100%; }
.src-search { flex: 1; }
.list { list-style: none; margin: 0; padding: var(--sp-2); }
.item {
  display: flex; align-items: center; justify-content: space-between; gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3); border-radius: var(--r-2); cursor: pointer;
  border: 1px solid transparent;
}
.item:hover { background: var(--bg-2); }
.item--active { background: var(--accent-soft); border-color: var(--accent); }
.item__main { display: flex; flex-direction: column; min-width: 0; }
.item__name { font-size: var(--fs-3); }
.item__url { font-family: var(--font-mono); font-size: var(--fs-1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.item__side { display: flex; align-items: center; gap: var(--sp-2); }
.x { background: none; border: none; color: var(--fg-2); cursor: pointer; font-size: var(--fs-4); line-height: 1; }
.x:hover { color: var(--danger); }
</style>
