/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}

// 图标以「原始 SVG 字符串」引入（design/tools/gen_icons.py 生成）
declare module '*.svg?raw' {
  const content: string
  export default content
}

// QWebChannel 由 Qt 侧在 DocumentCreation 时注入（web_config_pilot.py），不是 npm 依赖
declare interface Window {
  qt?: { webChannelTransport: unknown }
  QWebChannel?: new (transport: unknown, cb: (channel: any) => void) => void
}
