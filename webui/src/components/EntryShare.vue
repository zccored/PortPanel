<script setup lang="ts">
import { onMounted, ref } from 'vue'
import type { PilotBridge } from '../bridge'
import type { ApiSource } from '../types'
import { Icon, Modal, Tag } from './ui'

// 「分享入口」：与 Qt 侧 APIConfigDialog.share_image_source 同契约。
// 打包在 **Python 侧**（复用 _entry_file_payload，不复制格式定义）；前端只管展示 / 落盘 / 复制。
const props = defineProps<{ bridge: PilotBridge; source: ApiSource }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const filename = ref('')
const text = ref('')
const payload = ref<unknown>(null)
const result = ref('')
const level = ref<'info' | 'ok' | 'err'>('info')
const busy = ref(false)

function say(msg: string, lv: 'info' | 'ok' | 'err' = 'info') {
  result.value = msg
  level.value = lv
}

onMounted(async () => {
  busy.value = true
  const r = await props.bridge.buildEntry(props.source)
  if (r.ok) {
    filename.value = r.filename ?? ''
    text.value = r.text ?? ''
    payload.value = r.payload ?? null
  } else {
    say(r.error || '打包失败', 'err')
  }
  busy.value = false
})

async function copyJson() {
  const r = await props.bridge.copyText(text.value)
  say(r.ok ? 'JSON 已复制到剪贴板（可直接贴给别人）' : (r.error || '复制失败'), r.ok ? 'ok' : 'err')
}

async function writeTmp() {
  const r = await props.bridge.writeEntry(payload.value, filename.value)
  say(r.ok ? `已写入：${r.path}（${r.bytes} 字节）` : (r.error || '写入失败'), r.ok ? 'ok' : 'err')
}

async function saveAs() {
  const r = await props.bridge.saveEntryAs(payload.value)
  if (r.canceled) return say('已取消（没有写文件）')
  say(r.ok ? `已保存：${r.path}（${r.bytes} 字节）` : (r.error || '保存失败'), r.ok ? 'ok' : 'err')
}
</script>

<template>
  <Modal title="分享入口" @close="emit('close')">
    <div class="es">
      <div class="es__line">
        <Tag tone="accent">{{ source.name }}</Tag>
        <span class="muted">{{ source.base_url }} · {{ source.endpoints.length }} 个子端口</span>
      </div>
      <div class="muted es__hint">
        文件名：<code>{{ filename || '（打包中…）' }}</code>
        —— 比对着 <code>.apientry.json</code> 直接发给别人，对方用「导入入口」即可使用。
      </div>

      <div class="es__row">
        <button class="btn btn--primary" :disabled="!payload || busy" @click="copyJson">
          <Icon name="share" /> 复制 JSON（最方便）
        </button>
        <button class="btn" :disabled="!payload || busy" @click="writeTmp">
          <Icon name="save" /> 写入临时目录
        </button>
        <button class="btn" :disabled="!payload || busy" @click="saveAs">
          <Icon name="folder" /> 另存为…
        </button>
      </div>

      <div v-if="result" class="es__line">
        <Tag :tone="level === 'ok' ? 'ok' : level === 'err' ? 'danger' : 'default'">结果</Tag>
        <span>{{ result }}</span>
      </div>

      <pre class="es__json input input--mono">{{ text || '（打包中…）' }}</pre>
    </div>

    <template #footer>
      <button class="btn" @click="emit('close')">关闭</button>
    </template>
  </Modal>
</template>

<style scoped>
.es { display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-4); }
.es__line { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; font-size: var(--fs-2); }
.es__hint { font-size: var(--fs-2); }
.es__row { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.es__json {
  margin: 0; padding: var(--sp-2); max-height: 300px; overflow: auto;
  font-size: var(--fs-1); white-space: pre-wrap; word-break: break-all;
}
</style>
