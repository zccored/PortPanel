import { createApp } from 'vue'
import App from './App.vue'
import DemoView from './views/DemoView.vue'
import './styles/tokens.css'   // 生成物：design/tools/gen_tokens.py ← design/tokens.json
import './styles/base.css'     // 手写：基础样式与通用控件类
import { benchRoundTrip, connectBridge } from './bridge'

// 地址栏带 `#demo` → 组件库画廊（浏览器开发用）。
// Qt 壳用 file:// 加载 dist/index.html（不带 hash）→ 永远是试点窗口，互不影响。
const Root = location.hash.replace(/^#/, '').toLowerCase() === 'demo' ? DemoView : App
const app = createApp(Root)
app.mount('#app')

// 给 Qt 侧（web_config_pilot.py）留的探针：
//   window.__pilot.mode         → 'qt' | 'mock'
//   window.__pilot.benchRoundTrip(n) → 跑一次往返基准，结果经 bridge.report('roundtrip', ms) 回传
// 这样「桥接往返延迟」可以用 Python 一行 runJavaScript 触发，不需要前端写死测量逻辑。
void (async () => {
  const bridge = await connectBridge()
  ;(window as any).__pilot = {
    mode: bridge.mode,
    bridge,
    benchRoundTrip: async (n = 200) => {
      const ms = await benchRoundTrip(bridge, n)
      await bridge.report('roundtrip', ms)
      return ms
    },
  }
  if (bridge.mode === 'qt') {
    document.title = '图源配置 · Web 试点（QWebChannel 已连接）'
  }
})()
