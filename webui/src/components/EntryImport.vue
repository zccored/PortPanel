<script setup lang="ts">
import { computed, ref } from 'vue'
import type { PilotBridge } from '../bridge'
import type { EntryParse, EntryPayload } from '../types'
import { EmptyState, Icon, Modal, Tag } from './ui'

// 「导入入口」：与 Qt 侧 APIConfigDialog.import_image_sources 同契约（三种形态 + 重名加后缀）。
// 文件读取用浏览器 <input type=file>（file:// 下可用），**解析在 Python 侧**（复用 Qt 的 staticmethod）。
const props = defineProps<{ bridge: PilotBridge; existingNames: string[] }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'imported', entries: EntryPayload[]): void
}>()

const text = ref('')
const parsed = ref<EntryParse | null>(null)
const parsing = ref(false)
const fileName = ref('')
const error = ref('')

async function parse(raw: string) {
  parsing.value = true
  error.value = ''
  try {
    const r = await props.bridge.parseEntry(raw, props.existingNames)
    parsed.value = r
    if (!r.ok) error.value = r.error || '解析失败'
  } catch (e) {
    error.value = String(e)
  }
  parsing.value = false
}

async function onFile(ev: Event) {
  const f = (ev.target as HTMLInputElement).files?.[0]
  if (!f) return
  fileName.value = f.name
  text.value = await f.text()
  await parse(text.value)
}

async function onPasteParse() {
  fileName.value = ''
  await parse(text.value)
}

function onImport() {
  const list = parsed.value?.entries ?? []
  if (list.length) emit('imported', list)
}

const canImport = computed(() => !!parsed.value?.ok && (parsed.value?.entries.length ?? 0) > 0)
</script>

<template>
  <Modal title="导入入口" @close="emit('close')">
    <div class="ei">
      <div class="muted ei__hint">
        选一个 <code>.apientry.json</code>（别人用「分享入口」导出的），或直接把 JSON 粘进来。
        兼容三种形态：<code>{"kind":"tianji.api_entry",…,"entry":{…}}</code>、裸入口
        <code>{"name","base_url","endpoints"}</code>、整包配置
        <code>{"image_sources":[…]}</code>。重名会自动加 <code> (2)</code> 后缀。
      </div>

      <div class="ei__row">
        <input class="input" type="file" accept=".json,application/json" @change="onFile" />
        <button class="btn btn--ghost" :disabled="!text.trim()" @click="onPasteParse">
          <Icon name="import" /> 解析粘贴内容
        </button>
      </div>

      <textarea
        v-model="text"
        class="input input--mono ei__paste"
        spellcheck="false"
        placeholder="或把 .apientry.json 的内容粘在这里 …"
      ></textarea>

      <div v-if="parsing" class="muted">解析中…</div>
      <div v-else-if="error" class="ei__line">
        <Tag tone="danger">失败</Tag><span>{{ error }}</span>
      </div>
      <template v-else-if="parsed?.ok">
        <div class="ei__line">
          <Tag tone="ok">可导入 {{ parsed.entries.length }} 个入口</Tag>
          <span v-if="fileName" class="muted">来自 {{ fileName }}</span>
        </div>
        <ul class="ei__list">
          <li v-for="(e, i) in parsed.entries" :key="i">
            <strong>{{ e.name }}</strong>
            <span class="muted">{{ e.base_url }} · {{ e.endpoints.length }} 个子端口</span>
          </li>
        </ul>
        <div v-if="parsed.renames.length" class="muted ei__hint">
          重名自动改名：<span v-for="(r, i) in parsed.renames" :key="i">{{ r.from }} →
            {{ r.to }}<span v-if="i < parsed.renames.length - 1">；</span></span>
        </div>
      </template>

      <EmptyState
        v-if="!text.trim() && !parsed && !parsing"
        icon="import"
        text="还没有内容"
        hint="选一个 .apientry.json，或把 JSON 粘进来"
      />
    </div>

    <template #footer>
      <button class="btn" @click="emit('close')">取消</button>
      <button class="btn btn--primary" :disabled="!canImport" @click="onImport">
        <Icon name="check" /> 导入
      </button>
    </template>
  </Modal>
</template>

<style scoped>
.ei { display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-4); }
.ei__hint { font-size: var(--fs-2); }
.ei__row { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.ei__paste { min-height: 110px; resize: vertical; padding: var(--sp-2); font-size: var(--fs-2); }
.ei__line { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; font-size: var(--fs-2); }
.ei__list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--sp-1); font-size: var(--fs-2); }
.ei__list li { display: flex; align-items: baseline; gap: var(--sp-2); }
</style>
