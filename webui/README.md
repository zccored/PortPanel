# 端口画板 · Web 前端（阶段 0 试点）

「图源配置」窗口的 Web 化试点：**Vue 3 + TypeScript + Vite**，由 `web_config_pilot.py` 用
`QWebEngineView` + `QWebChannel` 装载。零侵入 —— 不改 `port_panel.py` / `img_server.py` / `api_config_dialog.py`。

## 开发 / 构建 / 运行

```bash
# 1) 装依赖（首次；package-lock.json 已入库，用 npm ci 复现）
npm ci

# 2) 开发（浏览器里直接调 UI，走 mock 数据，不需要 Qt）
npm run dev            # http://localhost:5180

# 3) 构建产物（阶段 0 起产物入库，发布不依赖 node）
npm run build          # 先 vue-tsc 类型检查，再 vite build → dist/

# 4) 用 Qt 壳跑真实窗口（仓库根目录）
python web_config_pilot.py            # 看界面
python web_config_pilot.py --selftest # 自测：量完四个数自动退出
python web_config_pilot.py --real     # 尝试只读真实配置（默认用伪造样本）
```

## 设计 token 与图标（**单一来源，别在代码里写死**）

颜色 / 间距 / 圆角 / 字号 / 图标几何都在 `design/*.json`，由脚本生成到本目录：

```bash
# 改 token / 图标后重跑（源在 design/，产物在 webui/src/ 与 design/generated/）
python design/tools/gen_tokens.py     # design/tokens.json  → src/styles/tokens.css（+ Qt 侧候选常量）
python design/tools/gen_icons.py      # design/icons.json   → src/assets/icons/*.svg + index.ts
python design/tools/emoji_inventory.py   # 只读：输出 emoji → SVG 替换清单
python design/tools/gen_tokens.py --check   # 校验漂移（CI 用）
python design/tools/gen_icons.py  --check
```

规则（已写进项目记忆的 `conventions.md`）：

1. **样式里不写死颜色/间距/圆角/字号** —— 一律 `var(--bg-1)` / `var(--sp-3)` / `var(--r-2)` / `var(--fs-3)`；
2. **图标一律 `<Icon name="…" />`**（`src/components/Icon.vue`）—— **不要**再手写 `<svg>`、**不要**用 emoji 当图标；
3. **不要手工改生成物**：`src/styles/tokens.css`、`src/assets/icons/*`、`design/generated/*`。

> 详情与「为什么」见 `../design/README.md`。

## 三条必须知道的工程约束

1. **产物必须是单文件 IIFE + 相对路径**（`vite.config.ts` 里 `format: 'iife'` + `base: './'`）：
   Qt 用 `file://` 直接加载 `dist/index.html`，而 `file://` 下 ES module 会被 Chromium 的 CORS 规则拦掉。
2. **`qwebchannel.js` 不进仓库**：运行时由 `web_config_pilot.py` 从 Qt 资源
   `:/qtwebchannel/qwebchannel.js` 读出并注入（DocumentCreation）。试点还对它做了一处防御性补丁：
   Qt 会回一条 id=0 的握手响应而没有登记回调，官方脚本会因此抛 TypeError。
3. **离线红线**：不得引用任何 CDN / 远程字体；`LocalContentCanAccessRemoteUrls` 显式关掉。

## 目录

```
webui/
├── src/
│   ├── App.vue                # 布局 + 工具栏
│   ├── components/            # SourceList / ParamTable / HeaderPanel / Icon
│   ├── assets/icons/          # **生成物**：design/icons.json → 19 个 SVG + index.ts
│   ├── styles/                # tokens.css（**生成物**）+ base.css（手写基础样式）
│   ├── bridge.ts              # QWebChannel 封装（无 Qt 时自动降级为 mock）
│   ├── mockData.ts            # 伪造样本（浏览器开发用）
│   ├── types.ts
│   └── styles/tokens.css      # 设计 token 首版（阶段 2 起由 design/tokens.json 生成）
├── index.html
├── vite.config.ts
└── package.json / package-lock.json
```

## 阶段 0 已量到的四个数（本机，offscreen）

| 判据 | 结果 | 门槛 |
| ---- | ---- | ---- |
| ① 冷启动（进程→桥接就绪） | ≈ 700 ms（其中窗口构造→就绪 ≈ 340 ms） | 与 Qt 版同量级或更好 |
| ② 桥接往返（中位 / 300 次） | ≈ 0.4 ms | ≤ 5 ms |
| ③ 内存增量 | 主进程 ≈ 120 MB + WebEngine 子进程 ≈ 80 MB | ≤ 150 MB |
| ④ 体感分 | 待人工填写 | 现代感 / 满意度 ≥ 4 |

对照：现有 Qt 版 `ImageSourceConfigDialog` 冷启动 ≈ 6.6 s（其中 import + 建 QApplication 就占 6.55 s，
对话框本身 26 ms）—— 因为那条链路要 import `api_config_dialog` 及其重依赖。
