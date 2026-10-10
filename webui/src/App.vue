<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { connectBridge, type PilotBridge } from './bridge'
import type { ApiSource, EntryPayload, HeaderItem, PilotState } from './types'
import SourceList from './components/SourceList.vue'
import ParamTable from './components/ParamTable.vue'
import HeaderPanel from './components/HeaderPanel.vue'
import CookieImport from './components/CookieImport.vue'
import EntryImport from './components/EntryImport.vue'
import EntryShare from './components/EntryShare.vue'
import DebugPanel from './components/DebugPanel.vue'
import CloudPanel from './components/CloudPanel.vue'
import { Field, Icon, Panel, Tag, Toolbar } from './components/ui'

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

// ---- A3：Cookie 导入 ----
const showCookie = ref(false)
/** 已有 Cookie 头如果是 ${ENV:名}，把占位符传进去（不是明文，安全）；明文/掩码都不传。 */
const cookieEnv = computed(() => {
  const h = (state.value?.headers ?? []).find((x) => x.name.toLowerCase() === 'cookie')
  return h?.value?.startsWith('${ENV:') ? h.value : ''
})

function applyCookie(p: { value: string; envName: string; describe: string; envReady: boolean }) {
  const s = state.value
  if (!s) return
  // 试点的红线：**不把明文留在页面状态里**（真值只在导入那一次跨过桥）。
  const row: HeaderItem = {
    name: 'Cookie',
    value: p.envName ? p.value : '（明文模式：试点不落明文）',
    sensitive: true,
  }
  if (p.envName) {
    row.env = p.envName
    row.env_set = p.envReady
  }
  const i = s.headers.findIndex((h) => h.name.toLowerCase() === 'cookie')
  if (i >= 0) s.headers.splice(i, 1, row)
  else s.headers.push(row)
  dirty.value = true
  status.value = '已导入 Cookie（%s）→ %s'
    .replace('%s', p.describe || '已解析')
    .replace('%s', p.envName ? '环境变量 ' + p.envName : '明文模式（未落明文）')
  showCookie.value = false
}

// ---- A3-2：入口导入 / 分享 ----
const showEntryImport = ref(false)
const showEntryShare = ref(false)
const showDebug = ref(false)
const showCloud = ref(false)
const existingNames = computed(() => sources.value.map((s) => s.name))

function addEntries(list: EntryPayload[]) {
  for (const e of list) {
    sources.value.push({
      id: 'imp-' + Math.random().toString(36).slice(2, 8),
      name: e.name,
      base_url: e.base_url,
      endpoints: e.endpoints ?? [],
    })
  }
  const last = sources.value[sources.value.length - 1]
  if (last) {
    currentSourceId.value = last.id
    currentEndpoint.value = 0
  }
  dirty.value = true
  status.value = `已导入 ${list.length} 个入口（重名已自动加后缀）`
  showEntryImport.value = false
}

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
    <Toolbar>
      <template #lead>
        <strong>图源配置</strong>
        <Tag tone="accent">Web 试点</Tag>
        <Tag v-if="bridge">{{ bridge.mode === 'qt' ? 'QWebChannel' : 'mock' }}</Tag>
        <Tag v-if="state?.origin === 'sample'" tone="warn">伪造样本（无真实凭据）</Tag>
        <Tag v-if="dirty" tone="warn">未导出</Tag>
      </template>
      <button class="btn btn--ghost" @click="showEntryImport = true">
        <Icon name="import" /> 导入入口
      </button>
      <button
        class="btn btn--ghost"
        :disabled="!currentSource"
        @click="showEntryShare = true"
      >
        <Icon name="share" /> 分享入口
      </button>
      <button class="btn btn--ghost" @click="showCookie = true"><Icon name="cookie" /> 导入 Cookie</button>
      <button class="btn btn--ghost" :disabled="!endpoint" @click="showDebug = true">
        <Icon name="play" /> 调试
      </button>
      <button class="btn btn--ghost" @click="showCloud = true">
        <Icon name="database" /> 云服务
      </button>
      <button class="btn btn--ghost" @click="addEndpoint"><Icon name="plus" /> 子端口</button>
      <button class="btn btn--primary" :disabled="!dirty" @click="exportState">
        <Icon name="share" /> 导出副本
      </button>
    </Toolbar>

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
        <Panel class="meta" v-if="currentSource">
          <div class="meta__row">
            <Field label="名称">
              <input class="input" v-model="currentSource.name" @input="dirty = true" />
            </Field>
            <Field label="Base URL" grow>
              <input class="input input--mono" v-model="currentSource.base_url" @input="dirty = true" />
            </Field>
          </div>
        </Panel>

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
            >
              <Icon name="close" title="删除该子端口" />
            </span>
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

    <CookieImport
      v-if="showCookie && bridge"
      :bridge="bridge"
      :base-url="currentSource?.base_url ?? ''"
      :current="cookieEnv"
      @close="showCookie = false"
      @apply="applyCookie"
    />

    <EntryImport
      v-if="showEntryImport && bridge"
      :bridge="bridge"
      :existing-names="existingNames"
      @close="showEntryImport = false"
      @imported="addEntries"
    />

    <EntryShare
      v-if="showEntryShare && bridge && currentSource"
      :bridge="bridge"
      :source="currentSource"
      @close="showEntryShare = false"
    />

    <DebugPanel
      v-if="showDebug && bridge && currentSource && endpoint"
      :bridge="bridge"
      :source="currentSource"
      :endpoint="endpoint"
      @close="showDebug = false"
    />

    <CloudPanel v-if="showCloud && bridge" :bridge="bridge" @close="showCloud = false" />
  </div>
</template>

<style scoped>
.app { display: flex; flex-direction: column; height: 100%; }
.body { display: flex; flex: 1; min-height: 0; }
.left { width: 280px; flex: 0 0 280px; border-right: 1px solid var(--line-soft); min-height: 0; }
.right { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-4); }
/* 名称 / Base URL 两栏：Panel 的 body 不带内边距，这里给内容加（组件库约定） */
.meta__row { display: flex; gap: var(--sp-4); padding: var(--sp-3); }
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

