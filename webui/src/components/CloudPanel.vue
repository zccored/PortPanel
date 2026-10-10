<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import type { PilotBridge } from '../bridge'
import type { AliyunTestResult, CloudState } from '../types'
import { Field, Icon, Modal, Tag } from './ui'

// 「云服务」：与 Qt 侧「阿里云配置」页同口径 —— **只读**展示 + 连接测试
// （测试链路复用 `AliyunTestWorker`：initialize_aliyun_services → test_aliyun_connection）。
// 密钥不回前端：AccessKeySecret 只报"有没有设置"，AccessKeyId 只回前 6 位 + 掩码。
const props = defineProps<{ bridge: PilotBridge }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const state = ref<CloudState | null>(null)
const testing = ref(false)
const result = ref<AliyunTestResult | null>(null)
const err = ref('')
let timer: number | undefined

async function load() {
  try {
    state.value = await props.bridge.cloudState()
  } catch (e) {
    err.value = String(e)
  }
}

function stopPoll() {
  if (timer) window.clearTimeout(timer)
  timer = undefined
}

async function test() {
  stopPoll()
  testing.value = true
  err.value = ''
  result.value = null
  const r = await props.bridge.aliyunTest()
  if (!r.ok || !r.jobId) {
    testing.value = false
    err.value = r.error || '启动测试失败'
    return
  }
  poll(r.jobId, 0)
}

/** 结果由 Python 侧线程产出 → 轮询（阿里云 SDK 自己带超时，60 次 ≈ 18 s 兜底）。 */
function poll(jobId: string, n: number) {
  stopPoll()
  timer = window.setTimeout(async () => {
    const r = await props.bridge.aliyunResult(jobId)
    if (r.state === 'running' && n < 60) {
      poll(jobId, n + 1)
      return
    }
    result.value = r
    testing.value = false
    await load()
  }, 300)
}

onMounted(() => {
  void load()
})
onBeforeUnmount(stopPoll)
</script>

<template>
  <Modal title="云服务（阿里云 OSS）" width="720px" @close="emit('close')">
    <div class="cp">
      <div class="cp__line">
        <Tag :tone="state?.configured ? 'ok' : 'warn'">
          {{ state?.configured ? '已配置' : '未配置完整' }}
        </Tag>
        <Tag :tone="state?.clientAvailable ? 'ok' : 'warn'">
          客户端{{ state?.clientAvailable ? '可用' : '不可用' }}
        </Tag>
        <span v-if="state" class="muted">
          {{ state.found ? '已读到真实配置（只读）' : '没有找到 data/api_config.json' }}
        </span>
      </div>

      <div v-if="err" class="cp__line"><Tag tone="danger">失败</Tag><span>{{ err }}</span></div>

      <div class="cp__grid">
        <Field label="Endpoint" grow>
          <input class="input input--mono" :value="state?.endpoint ?? ''" disabled />
        </Field>
        <Field label="Bucket">
          <input class="input input--mono" :value="state?.bucket || '（未设置）'" disabled />
        </Field>
      </div>
      <div class="cp__grid">
        <Field label="Region" grow>
          <input class="input input--mono" :value="state?.region ?? ''" disabled />
        </Field>
        <Field label="AccessKeyId">
          <input class="input input--mono" :value="state?.keyId || '（未设置）'" disabled />
        </Field>
        <Field label="AccessKeySecret">
          <input
            class="input input--mono"
            :value="state?.hasSecret ? '••••••••（已设置）' : '（未设置）'"
            disabled
          />
        </Field>
      </div>

      <div class="muted cp__note">
        这是<strong>只读视图</strong>：试点不会写回 <code>data/api_config.json</code>，也不显示密钥明文。
        要改配置请用 Qt 版「阿里云配置」页（或等阶段 2 的 Web 版）。
      </div>

      <div v-if="testing" class="cp__line">
        <Tag>进行中</Tag><span class="muted">正在测试 OSS 连接…</span>
      </div>
      <div v-else-if="result?.state === 'done'" class="cp__line">
        <Tag :tone="result.success ? 'ok' : 'danger'">
          {{ result.success ? '连接成功' : '连接失败' }}
        </Tag>
        <span>{{ result.message }}</span>
      </div>
    </div>

    <template #footer>
      <button class="btn" :disabled="testing" @click="load">
        <Icon name="refresh" /> 重新读取配置
      </button>
      <button class="btn btn--primary" :disabled="testing" @click="test">
        <Icon name="play" /> 测试连接
      </button>
      <button class="btn" @click="emit('close')">关闭</button>
    </template>
  </Modal>
</template>

<style scoped>
.cp { display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-4); }
.cp__line { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; font-size: var(--fs-2); }
.cp__grid { display: flex; gap: var(--sp-4); flex-wrap: wrap; }
.cp__grid .field { min-width: 200px; }
.cp__note { font-size: var(--fs-2); }
</style>
