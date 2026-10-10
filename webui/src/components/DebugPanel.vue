<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { PilotBridge } from '../bridge'
import type { ApiEndpoint, ApiSource, DebugResult } from '../types'
import { Icon, Modal, Tag } from './ui'

// 「调试当前子端口」：与 Qt 侧 _debug_endpoint_by_index + EndpointDebugWorker 同链路。
// 前端只给"打哪个子端口"（sourceName + path + 参数），**真实请求头由 Python 侧从配置里取**；
// 回来的 info 已经是 _format_debug_info 的成品（敏感头打码）。
const props = defineProps<{ bridge: PilotBridge; source: ApiSource; endpoint: ApiEndpoint }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const url = ref('')
const result = ref<DebugResult | null>(null)
const running = ref(false)
const err = ref('')
const hint = ref('')
const showInfo = ref(false)
let timer: number | undefined

/** 与 Qt 侧同口径：base_url + path，`{name}` 用参数值替换。 */
function buildUrl(): string {
  let p = props.endpoint.path || '/'
  for (const q of props.endpoint.params ?? []) {
    if (q.name) p = p.split('{' + q.name + '}').join(q.value ?? '')
  }
  return (props.source.base_url || '') + p
}

/** 没被路径用掉的参数 → 作为 query params 发出去（与 Qt 用 {} 传 query 的语义一致）。 */
const queryParams = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {}
  for (const q of props.endpoint.params ?? []) {
    if (!q.name || !q.value) continue
    if ((props.endpoint.path || '').includes('{' + q.name + '}')) continue
    out[q.name] = q.value
  }
  return out
})

function stopPoll() {
  if (timer) window.clearTimeout(timer)
  timer = undefined
}

async function start() {
  stopPoll()
  running.value = true
  err.value = ''
  hint.value = ''
  result.value = null
  showInfo.value = false
  url.value = buildUrl()
  const r = await props.bridge.debugEndpoint({
    method: 'GET',
    url: url.value,
    sourceName: props.source.name,
    path: props.endpoint.path,
    params: queryParams.value,
    body: '',
  })
  if (!r.ok || !r.jobId) {
    running.value = false
    err.value = r.error || '启动调试失败'
    return
  }
  hint.value = r.hint ?? ''
  poll(r.jobId, 0)
}

/** 结果由 Python 侧线程产出 → 轮询（最多 ~60 s，与 worker 的 30 s 超时留余量）。 */
function poll(jobId: string, n: number) {
  stopPoll()
  timer = window.setTimeout(async () => {
    const r = await props.bridge.debugResult(jobId)
    if (r.state === 'running' && n < 200) {
      poll(jobId, n + 1)
      return
    }
    result.value = r
    if (r.hint) hint.value = r.hint
    showInfo.value = r.state === 'done' && (r.attempts?.length ?? 0) > 1
    running.value = false
  }, 300)
}

const okLine = computed(() => {
  const r = result.value
  if (!r || r.state !== 'done') return ''
  const where = r.error ? '调试失败' : '调试成功'
  return `${where} | 状态码: ${r.statusCode} | 通道: ${r.via || 'requests'} | ${r.elapsedMs ?? 0} ms`
})

onMounted(() => {
  void start()
})
onBeforeUnmount(stopPoll)
</script>

<template>
  <Modal title="调试子端口" width="880px" @close="emit('close')">
    <div class="dp">
      <div class="dp__line">
        <Tag tone="accent">GET</Tag>
        <code class="dp__url">{{ url || '（构建中…）' }}</code>
      </div>
      <div class="muted dp__note">
        请求头由 <strong>Python 侧</strong>从真实配置里取（明文不出后端）；下面「实际发出的请求头」里的敏感值已打码。
      </div>
      <div v-if="hint" class="dp__line"><Tag tone="warn">提示</Tag><span>{{ hint }}</span></div>

      <div v-if="running" class="dp__line">
        <Tag>进行中</Tag><span class="muted">正在等待响应…（最多 30 s 超时）</span>
      </div>
      <div v-else-if="err" class="dp__line">
        <Tag tone="danger">失败</Tag><span>{{ err }}</span>
      </div>
      <template v-else-if="result?.state === 'done'">
        <div class="dp__line">
          <Tag :tone="result.error ? 'danger' : 'ok'">{{ okLine }}</Tag>
          <span class="dp__grow" />
          <button class="btn btn--ghost" @click="showInfo = !showInfo">
            <Icon name="sliders" /> {{ showInfo ? '收起详情' : '请求头 / 响应头 / 诊断' }}
          </button>
        </div>
        <div v-if="result.error" class="dp__err">{{ result.error }}</div>
        <pre v-if="showInfo" class="dp__info input input--mono">{{ result.info }}</pre>
        <pre class="dp__body input input--mono">{{ result.body || '（空响应体）' }}</pre>
      </template>
    </div>

    <template #footer>
      <button class="btn" :disabled="running" @click="start">
        <Icon name="refresh" /> 重新调试
      </button>
      <button class="btn btn--primary" @click="emit('close')">关闭</button>
    </template>
  </Modal>
</template>

<style scoped>
.dp { display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-4); }
.dp__line { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; font-size: var(--fs-2); }
.dp__grow { flex: 1; }
.dp__url { font-family: var(--font-mono); font-size: var(--fs-2); word-break: break-all; }
.dp__note { font-size: var(--fs-2); }
.dp__err { font-size: var(--fs-2); color: var(--danger); }
.dp__info, .dp__body {
  margin: 0; padding: var(--sp-2); max-height: 260px; overflow: auto;
  font-size: var(--fs-1); white-space: pre-wrap; word-break: break-all;
}
.dp__info { background: var(--bg-2); }
</style>
