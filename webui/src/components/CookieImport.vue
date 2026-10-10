<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { PilotBridge } from '../bridge'
import type { CookieParse } from '../types'
import { Field, Icon, Modal, Tag } from './ui'

// 「导入 Cookie」：与 Qt 侧 CookieImportDialog 同流程（粘贴 → 实时预览 → 环境变量或明文 → 导入），
// 但**解析在 Python 侧**（复用 portpanel.ui.image_source.parse_cookie_text 的 7 种格式，不重写第二套）。
// 明文边界：界面只显示 describe（名字 + 长度），**不回显 cookie 值**。
const props = defineProps<{ bridge: PilotBridge; baseUrl: string; current?: string }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'apply', p: { value: string; envName: string; describe: string; envReady: boolean }): void
}>()

const text = ref('')
const parsed = ref<CookieParse | null>(null)
const parsing = ref(false)
const useEnv = ref(true)
const envName = ref('')
const envText = ref('')
const envReady = ref(false)
const envPersisted = ref(false)
const envResult = ref('')
const setxCmd = ref('')
const showSetx = ref(false)

// 已有值是 ${ENV:名} 时只把变量名带过来（占位符不是 cookie，粘回去只会解析失败）
const existing = (props.current || '').trim()
const m = existing.match(/^\$\{ENV:([^}]+)\}$/)
envName.value = m?.[1] ?? ''

let timer: number | undefined
watch(text, () => {
  if (timer) window.clearTimeout(timer)
  parsing.value = true
  timer = window.setTimeout(refresh, 250) // 去抖：别每次按键都过一次桥
})
onBeforeUnmount(() => {
  if (timer) window.clearTimeout(timer)
})

async function refresh() {
  const raw = text.value.trim()
  if (!raw) {
    parsed.value = null
    parsing.value = false
    return
  }
  const d = await props.bridge.parseCookie(raw, props.baseUrl)
  parsed.value = d
  if (!envName.value) envName.value = d.envName
  envText.value = d.envText
  envReady.value = d.envReady
  envResult.value = ''
  showSetx.value = false
  parsing.value = false
}

async function refreshEnvStatus() {
  const n = envName.value.trim()
  if (!n) return
  const st = await props.bridge.envStatus(n)
  envText.value = st.text
  envReady.value = st.ready
  envPersisted.value = st.persisted
}

async function applyEnvNow() {
  const n = envName.value.trim()
  const v = parsed.value?.value ?? ''
  if (!n || !v) return
  const r = await props.bridge.envApply(n, v)
  envResult.value = r.text
  await refreshEnvStatus()
}

async function reloadEnv() {
  const r = await props.bridge.envReload()
  envResult.value = r.text
  await refreshEnvStatus()
}

async function toggleSetx() {
  if (showSetx.value) {
    showSetx.value = false
    return
  }
  const r = await props.bridge.setxCommand(envName.value.trim(), parsed.value?.value ?? '')
  setxCmd.value = r.cmd
  showSetx.value = true
}

const canApply = computed(() => !!parsed.value?.ok)
/** 「将写入」只给形态与长度，**不给明文**（明文边界：界面不回显 cookie 值）。 */
const willStore = computed(() => {
  if (useEnv.value && envName.value.trim()) return '${ENV:' + envName.value.trim() + '}（运行时取真值）'
  if (parsed.value?.ok) return '明文模式 · 试点不落明文，仅演示流程（长度 ' + parsed.value.length + '）'
  return '—'
})

function onApply() {
  const d = parsed.value
  if (!d?.ok) return
  const n = envName.value.trim()
  emit('apply', {
    value: useEnv.value && n ? '${ENV:' + n + '}' : d.value,
    envName: useEnv.value ? n : '',
    describe: d.describe,
    envReady: useEnv.value ? envReady.value : true,
  })
}
</script>

<template>
  <Modal title="导入 Cookie" @close="emit('close')">
    <div class="ci">
      <div class="ci__hint muted">
        把浏览器里复制到的 Cookie 粘进来，任意一种形式都能认：Request Headers 整行
        （<code>cookie: a=1; b=2</code>）／只有值／DevTools 的 Copy as cURL／
        Cookie-Editor 导出的 JSON 数组／Set-Cookie 整行（Path、Expires、HttpOnly 会自动丢掉）。
      </div>

      <div class="ci__warn">
        <Tag tone="warn">凭据</Tag>
        <span>
          Cookie 就是你的登录态。选「明文」会写进配置、并随图纸导出；
          <strong>推荐用环境变量</strong>（存 <code>${ENV:名字}</code>，运行时才取真值）。
        </span>
      </div>

      <textarea
        v-model="text"
        class="ci__paste input input--mono"
        spellcheck="false"
        placeholder="在这里粘贴 Cookie …"
      ></textarea>

      <div class="ci__preview">
        <template v-if="parsing"><span class="muted">解析中…</span></template>
        <template v-else-if="!parsed"><span class="muted">（等待粘贴）</span></template>
        <template v-else-if="parsed.ok">
          <Tag tone="ok">识别成功</Tag>
          <span>{{ parsed.describe }}</span>
          <span v-if="parsed.warnings.length" class="muted">{{ parsed.warnings.join('；') }}</span>
        </template>
        <template v-else>
          <Tag tone="danger">没认出</Tag>
          <span>{{ parsed.warnings.join('；') || '请检查是不是粘成了响应头（cookie 在 Request Headers 里）' }}</span>
        </template>
      </div>

      <label class="ci__envrow">
        <input v-model="useEnv" type="checkbox" />
        <span>用环境变量代替明文（推荐）—— 存 <code>${ENV:名字}</code>，运行时才取真值</span>
      </label>

      <div class="ci__envbox">
        <Field label="环境变量名" grow>
          <input
            v-model="envName"
            class="input input--mono"
            :disabled="!useEnv"
            @change="refreshEnvStatus"
          />
        </Field>
        <button class="btn btn--ghost" :disabled="!useEnv || !parsed?.ok" @click="applyEnvNow">
          <Icon name="save" /> 立即生效 + 复制 setx
        </button>
        <button class="btn btn--ghost" :disabled="!useEnv" @click="reloadEnv">
          <Icon name="refresh" /> 重新读取系统环境变量
        </button>
        <button class="btn btn--ghost" :disabled="!useEnv || !parsed?.ok" @click="toggleSetx">
          <Icon name="import" /> {{ showSetx ? '隐藏' : '显示' }} setx 命令
        </button>
      </div>

      <div class="ci__envtext" :class="{ 'ci__envtext--ok': envReady }">
        <span v-if="envName">{{ envText || '（未检查）' }}</span>
        <span v-if="envPersisted" class="muted">· 已写进系统（注册表）</span>
      </div>
      <div v-if="envResult" class="ci__envresult">{{ envResult }}</div>

      <div v-if="showSetx" class="ci__setx">
        <Tag tone="warn">含明文</Tag>
        <input v-model="setxCmd" class="input input--mono" readonly />
        <span class="muted">粘到终端执行一次即可永久保存（setx 上限 1024 字符）</span>
      </div>

      <div class="ci__store muted">将写入：<code>{{ willStore }}</code></div>
    </div>

    <template #footer>
      <button class="btn" @click="emit('close')">取消</button>
      <button class="btn btn--primary" :disabled="!canApply" @click="onApply">
        <Icon name="check" /> 导入
      </button>
    </template>
  </Modal>
</template>

<style scoped>
.ci { display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-4); }
.ci__hint { font-size: var(--fs-2); }
.ci__warn {
  display: flex; align-items: flex-start; gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3); font-size: var(--fs-2);
  background: var(--bg-2); border: 1px solid var(--warn);
  border-radius: var(--r-2);
}
.ci__paste { min-height: 120px; resize: vertical; padding: var(--sp-2); font-size: var(--fs-2); }
.ci__preview { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; font-size: var(--fs-2); min-height: 22px; }
.ci__envrow { display: flex; align-items: center; gap: var(--sp-2); font-size: var(--fs-2); }
.ci__envbox { display: flex; align-items: flex-end; gap: var(--sp-2); flex-wrap: wrap; }
.ci__envtext { font-size: var(--fs-2); color: var(--danger); }
.ci__envtext--ok { color: var(--ok); }
.ci__envresult { font-size: var(--fs-2); color: var(--fg-1); }
.ci__setx { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; font-size: var(--fs-1); }
.ci__setx .input { flex: 1; min-width: 240px; font-size: var(--fs-1); }
.ci__store { font-size: var(--fs-2); }
</style>
