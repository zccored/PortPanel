// 组件库入口：新页面一律从这里 import（`import { Panel, Tag } from '@/components/ui'`），
// 别再逐个引文件路径 —— 这样将来挪目录不会到处改 import。
//
// 三条约定（与 webui/README.md 一致）：
//   ① 样式只用 token 变量（var(--bg-1) / var(--sp-3) / var(--r-2) / var(--fs-3)），不写死值；
//   ② 图标只用 <Icon>，不用 emoji / 手写 <svg>；
//   ③ 组件自身的样式放各自 <style scoped>，跨组件复用的原子类才进 styles/base.css。
export { default as Icon } from './Icon.vue'
export { default as Panel } from './Panel.vue'
export { default as Toolbar } from './Toolbar.vue'
export { default as Tag } from './Tag.vue'
export { default as Field } from './Field.vue'
export { default as EmptyState } from './EmptyState.vue'
export { default as Modal } from './Modal.vue'
export type { Tone } from './types'
