<script setup lang="ts">
import type { ApiEndpoint, ParamKind } from '../types'
import { EmptyState, Icon, Panel } from './ui'

const props = defineProps<{ endpoint: ApiEndpoint }>()
const emit = defineEmits<{ (e: 'change'): void }>()

const KINDS: ParamKind[] = ['string', 'int', 'float', 'bool', 'array']

function addRow() {
  props.endpoint.params.push({ name: 'new_param', kind: 'string', value: '', note: '' })
  emit('change')
}

function removeRow(i: number) {
  props.endpoint.params.splice(i, 1)
  emit('change')
}

function move(i: number, d: number) {
  const j = i + d
  const ps = props.endpoint.params
  if (j < 0 || j >= ps.length) return
  const [row] = ps.splice(i, 1)
  if (row) ps.splice(j, 0, row)
  emit('change')
}
</script>

<template>
  <Panel class="table" title="参数表" :meta="`${endpoint.path} · ${endpoint.params.length} 项`">
    <template #actions>
      <button class="btn btn--ghost" @click="addRow"><Icon name="plus" /> 参数</button>
    </template>

    <template #hint>
      「注释」会显示在画板的悬停提示里 —— 这是图源配置里最值得写清楚的一列。
    </template>

    <div class="rows">
      <div class="row row--head muted">
        <span>名称</span><span>类型</span><span>值</span><span>注释</span><span></span>
      </div>
      <div v-for="(p, i) in endpoint.params" :key="i" class="row">
        <input class="input input--mono" v-model="p.name" @input="emit('change')" />
        <select class="input" v-model="p.kind" @change="emit('change')">
          <option v-for="k in KINDS" :key="k" :value="k">{{ k }}</option>
        </select>
        <input class="input input--mono" v-model="p.value" @input="emit('change')" />
        <input class="input" v-model="p.note" placeholder="写清这个参数干什么用" @input="emit('change')" />
        <span class="row__ops">
          <button class="x" title="上移" @click="move(i, -1)">↑</button>
          <button class="x" title="下移" @click="move(i, 1)">↓</button>
          <button class="x x--danger" title="删除" @click="removeRow(i)">
            <Icon name="close" title="删除" />
          </button>
        </span>
      </div>
    </div>

    <EmptyState
      v-if="!endpoint.params.length"
      icon="sliders"
      text="这个子端口还没有参数"
      hint="点右上「＋ 参数」加一条"
    />
  </Panel>
</template>

<style scoped>
.table { min-height: 0; }
.rows { padding: var(--sp-2) var(--sp-4) var(--sp-4); }
.row {
  display: grid; grid-template-columns: 1.1fr 84px 1.3fr 2fr 84px;
  gap: var(--sp-2); align-items: center; padding: var(--sp-1) 0;
}
.row--head { font-size: var(--fs-2); padding-bottom: var(--sp-2); }
.row__ops { display: flex; gap: 2px; }
.x { background: none; border: none; color: var(--fg-2); cursor: pointer; font-size: var(--fs-3); }
.x:hover { color: var(--fg-0); }
.x--danger:hover { color: var(--danger); }
</style>
