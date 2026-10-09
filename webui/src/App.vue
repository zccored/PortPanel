<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { connectBridge, type PilotBridge } from './bridge'
import type { ApiSource, PilotState } from './types'
import SourceList from './components/SourceList.vue'
import ParamTable from './components/ParamTable.vue'
import HeaderPanel from './components/HeaderPanel.vue'
import Icon from './components/Icon.vue'

const bridge = ref<PilotBridge | null>(null)
const state = ref<PilotState | null>(null)
const currentSourceId = ref('')
const currentEndpoint = ref(0)
const keyword = ref('')
const status = ref('正在连接…')
const dirty = ref(false)

const sources = computed<ApiSource[]>(() => state.value?.sources ?? [])
const currentSource = computed<ApiSource | undefined>(
  () => sources.value.find((s) => s.id === currentSourceId.value) ?? sources.value[0],
)
const endpoints = computed(() => currentSource.value?.endpoints ?? [])
const endpoint = computed(() => endpoints.value[currentEndpoint.value])

onMounted(async () => {
  const b = await connectBridge()
  bridge.value = b
  const s = await b.getState()
  state.value = s
  currentSourceId.value = s.sources[0]?.id ?? ''
  status.value = `已就绪 · 数据源 ${b.mode === 'qt' ? 'QWebChannel（Qt）' : '浏览器 mock'}`
  b.onStateChanged((next) => {
    state.value = next
    dirty.value = false
  })
})

function selectSource(id: string) {
  currentSourceId.value = id
  currentEndpoint.value = 0
}

function addSource() {
  const id = `new-${Date.now().toString(36)}`
  sources.value.push({
    id,
    name: '新建入口',
    base_url: 'https://',
    endpoints: [{ path: '/', desc: '新建子端口', params: [] }],
  })
  currentSourceId.value = id
  currentEndpoint.value = 0
  dirty.value = true
}

function removeSource(id: string) {
  const i = sources.value.findIndex((s) => s.id === id)
  if (i < 0) return
  sources.value.splice(i, 1)
  if (currentSourceId.value === id) currentSourceId.value = sources.value[0]?.id ?? ''
  dirty.value = true
}

function addEndpoint() {
  const s = currentSource.value
  if (!s) return
  s.endpoints.push({ path: '/new', desc: '新建子端口', params: [] })
  currentEndpoint.value = s.endpoints.length - 1
  dirty.value = true
}

function removeEndpoint(i: number) {
  const s = currentSource.value
  if (!s || s.endpoints.length <= 1) return
  s.endpoints.splice(i, 1)
  currentEndpoint.value = Math.max(0, Math.min(i, s.endpoints.length - 1))
  dirty.value = true
}

async function exportState() {
  if (!bridge.value || !state.value) return
  const where = await bridge.value.exportState(state.value)
  dirty.value = false
  status.value = `已导出：${where}`
}
</script>

<template>
  <div class="app">
    <header class="topbar">
      <div class="title">
        <strong>图源配置</strong>
        <span class="tag tag--accent">Web 试点</span>
        <span v-if="bridge" class="tag">{{ bridge.mode === 'qt' ? 'QWebChannel' : 'mock' }}</span>
        <span v-if="state?.origin === 'sample'" class="tag tag--warn">伪造样本（无真实凭据）</span>
        <span v-if="dirty" class="tag tag--warn">未导出</span>
      </div>
      <div class="actions">
        <button class="btn btn--ghost" @click="addEndpoint">
          <Icon name="plus" /> 子端口
        </button>
        <button class="btn btn--primary" :disabled="!dirty" @click="exportState">
          <Icon name="share" /> 导出副本
        </button>
      </div>
    </header>

    <main class="body">
      <aside class="left">
        <SourceList
          :sources="sources"
          :current-id="currentSource?.id ?? ''"
          v-model:keyword="keyword"
          @select="selectSource"
          @add="addSource"
          @remove="removeSource"
        />
      </aside>

      <section class="right">
        <div class="meta panel" v-if="currentSource">
          <label class="field">
            <span class="muted">名称</span>
            <input class="input" v-model="currentSource.name" @input="dirty = true" />
          </label>
          <label class="field field--grow">
            <span class="muted">Base URL</span>
            <input class="input input--mono" v-model="currentSource.base_url" @input="dirty = true" />
          </label>
        </div>

        <div class="tabs" v-if="currentSource">
          <button
            v-for="(ep, i) in endpoints"
            :key="i"
            class="tab"
            :class="{ 'tab--active': i === currentEndpoint }"
            @click="currentEndpoint = i"
          >
            <code>{{ ep.path }}</code>
            <span
              v-if="endpoints.length > 1"
              class="tab__x"
              title="删除该子端口"
              @click.stop="removeEndpoint(i)"
              >×</span
            >
          </button>
        </div>

        <div class="grid">
          <ParamTable v-if="endpoint" :endpoint="endpoint" @change="dirty = true" />
          <HeaderPanel :headers="state?.headers ?? []" />
        </div>
      </section>
    </main>

    <footer class="statusbar">
      <span>{{ status }}</span>
      <span class="muted">{{ state?.config_path }}</span>
    </footer>
  </div>
</template>

<style scoped>
.app { display: flex; flex-direction: column; height: 100%; }
.topbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: var(--sp-4); padding: var(--sp-3) var(--sp-4);
  background: var(--bg-1); border-bottom: 1px solid var(--line-soft);
}
.title { display: flex; align-items: center; gap: var(--sp-2); font-size: var(--fs-4); }
.actions { display: flex; gap: var(--sp-2); }
.body { display: flex; flex: 1; min-height: 0; }
.left { width: 280px; flex: 0 0 280px; border-right: 1px solid var(--line-soft); min-height: 0; }
.right { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-4); }
.meta { display: flex; gap: var(--sp-4); padding: var(--sp-3); }
.field { display: flex; flex-direction: column; gap: var(--sp-1); font-size: var(--fs-2); }
.field--grow { flex: 1; }
.tabs { display: flex; gap: var(--sp-2); flex-wrap: wrap; }
.tab {
  display: inline-flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3); background: var(--bg-2); color: var(--fg-1);
  border: 1px solid var(--line); border-radius: var(--r-2); cursor: pointer; font-size: var(--fs-2);
}
.tab:hover { color: var(--fg-0); }
.tab--active { background: var(--accent-soft); border-color: var(--accent); color: #d7e6ff; }
.tab__x { color: var(--fg-2); padding: 0 2px; }
.tab__x:hover { color: var(--danger); }
.grid {
  display: grid; grid-template-columns: minmax(0, 1fr) 340px;
  gap: var(--sp-4); flex: 1; min-height: 0;
}
.statusbar {
  display: flex; justify-content: space-between; gap: var(--sp-4);
  padding: var(--sp-2) var(--sp-4); background: var(--bg-1);
  border-top: 1px solid var(--line-soft); font-size: var(--fs-2);
}
</style>

