<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import Icon from './Icon.vue'

// 模态浮层：遮罩 + 卡片。Esc / 点遮罩 / 右上 ✕ 都能关；`width` 控卡片宽度。
// 遮罩色用 color-mix 从 token 派生（token 里没有 overlay 项，也**不写死颜色**）。
withDefaults(defineProps<{ title: string; width?: string }>(), { width: '760px' })
const emit = defineEmits<{ (e: 'close'): void }>()

function onKey(ev: KeyboardEvent) {
  if (ev.key === 'Escape') emit('close')
}
onMounted(() => document.addEventListener('keydown', onKey))
onBeforeUnmount(() => document.removeEventListener('keydown', onKey))

/** 只有点在遮罩本身（不是卡片内部）才关闭 —— 卡片里拖选文本时不该误关。 */
function onBackdrop(ev: MouseEvent) {
  if ((ev.target as HTMLElement).classList.contains('modal')) emit('close')
}
</script>

<template>
  <div class="modal" @click="onBackdrop">
    <div class="modal__card" :style="{ width }">
      <header class="modal__head">
        <strong class="modal__title">{{ title }}</strong>
        <span class="modal__grow" />
        <slot name="head" />
        <button class="modal__x" title="关闭（Esc）" @click="emit('close')">
          <Icon name="close" title="关闭（Esc）" />
        </button>
      </header>
      <div class="modal__body"><slot /></div>
      <footer v-if="$slots.footer" class="modal__foot"><slot name="footer" /></footer>
    </div>
  </div>
</template>

<style scoped>
.modal {
  position: fixed; inset: 0; z-index: 50;
  display: flex; align-items: center; justify-content: center;
  padding: var(--sp-5);
  background: color-mix(in srgb, var(--bg-0) 72%, transparent);
}
.modal__card {
  display: flex; flex-direction: column; max-width: 100%; max-height: 100%;
  background: var(--bg-1); border: 1px solid var(--line);
  border-radius: var(--r-3); box-shadow: var(--sh-2); overflow: hidden;
}
.modal__head {
  display: flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4); border-bottom: 1px solid var(--line-soft);
}
.modal__title { font-size: var(--fs-4); }
.modal__grow { flex: 1; }
.modal__x { background: none; border: none; color: var(--fg-2); cursor: pointer; font-size: var(--fs-4); }
.modal__x:hover { color: var(--fg-0); }
.modal__body { flex: 1; min-height: 0; overflow: auto; }
.modal__foot {
  display: flex; align-items: center; justify-content: flex-end; gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4); border-top: 1px solid var(--line-soft);
}
</style>
