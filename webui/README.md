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
│   ├── App.vue                # 试点窗口：布局 + 工具栏
│   ├── components/
│   │   ├── ui/                # **组件库**：Panel / Toolbar / Tag / Field / Icon / EmptyState / Modal
│   │   └── *.vue              # 试点专用：SourceList / ParamTable / HeaderPanel / CookieImport / EntryImport / EntryShare / DebugPanel / CloudPanel
│   ├── views/DemoView.vue     # 组件库画廊（地址栏带 #demo 时挂载它）
│   ├── assets/icons/          # **生成物**：design/icons.json → 19 个 SVG + index.ts
│   ├── styles/                # tokens.css（**生成物**）+ base.css（手写基础样式与原子类）
│   ├── bridge.ts              # QWebChannel 封装（无 Qt 时自动降级为 mock）
│   ├── mockData.ts            # 伪造样本（浏览器开发用）
│   ├── types.ts               # 业务数据类型（组件库的类型在 components/ui/types.ts）
│   └── main.ts                # 按 hash 选根组件：#demo → DemoView，否则 App
├── index.html
├── vite.config.ts
└── package.json / package-lock.json
```

## 组件库（`src/components/ui/`）

把试点里验证过的结构抽成了可复用组件 —— **新页面一律从这里 import**：

```ts
import { EmptyState, Field, Icon, Panel, Tag, Toolbar } from './components/ui'
```

| 组件 | 作用 | 关键约定 |
| ---- | ---- | -------- |
| `Panel` | 面板：`head`（标题 + 元信息 + 操作）+ `hint`（一行说明）+ `body`（滚动）+ `footer` | **body 不加内边距**（列表/表格/表单密度不同，padding 由内容给）；`#head` 槽可整体替换表头（那时右对齐要自己撑） |
| `Toolbar` | 通栏工具条：`#lead` + 动作区 | `dense` 变体用于面板内的小工具条 |
| `Tag` | 语义标签 | `tone` = `default / accent / warn / ok / danger` → `.tag--*` |
| `Field` | 表单字段：label + 控件 + 可选说明 | `grow` 让字段吃掉剩余宽度；控件走默认插槽 |
| `EmptyState` | 空状态 | 列表/表格/面板没内容时**统一用它**，别再各写一行 `.empty muted` |
| `Modal` | 模态浮层：遮罩 + 卡片 | Esc / 点遮罩 / 右上 ✕ 都能关；遮罩色用 `color-mix` 从 `--bg-0` 派生（不写死颜色） |
| `Icon` | 图标（唯一入口） | 名字 = `design/icons.json` 的键；`size` 可放大（默认随字号 1em） |

**看效果**：浏览器打开 `http://localhost:5180/#demo`（`npm run dev`）。Qt 壳用
`file://` 加载 `dist/index.html`**不带 hash** → 永远是试点窗口，两者互不干扰。
DemoView 还兼作图标清单（19 个图标全部渲染一遍，改名/漏图会立刻看出来）。

## 桥接 API（`window.__pilot.bridge`）

前端唯一出入口在 `src/bridge.ts`（Qt 走 QWebChannel，浏览器自动降级为 mock，**同一套签名**）。

| 方法 | 作用 | 备注 |
| ---- | ---- | ---- |
| `getState` / `exportState` / `echo` / `report` / `onStateChanged` | 阶段 0 原有：取状态、导出副本、往返基准、状态推送 | `exportState` 只写 `%TEMP%` 副本，**不写回**真实配置 |
| `parseCookie(text, baseUrl)` | Cookie 文本 → 条数 / 描述 / 环境变量提示（认 7 种粘贴格式） | 解析在 **Python 侧**，复用 `api_config_dialog.parse_cookie_text` |
| `envStatus` / `envReload` | 环境变量在不在本进程 / 从注册表回读（只读） | 对应 Qt 侧的「环境变量状态」与「重新读取系统环境变量」 |
| `envApply(name, value)` | 写进**本进程** + 把 `setx` 命令抄进剪贴板 | 与 Qt 侧同口径：**不替用户写注册表**（杀软眼里的持久化行为）；**明文不回前端** |
| `setxCommand(name, value)` | 显式点按钮才调用：回 setx 命令（含明文）供手动复制 | 明文边界清楚、可审计 |
| `parseEntry(text, names)` | `.apientry.json` 文本 → 可导入的入口列表 + 重名改名记录 | 兼容三种形态（入口文件 / 裸入口 / 整包 `image_sources`）；解析复用 Qt 侧 `_extract_entries_from_file` / `_unique_entry_name`（staticmethod） |
| `buildEntry(source)` | 入口 → 可分享的 `.apientry.json` 内容 + 建议文件名 + 可复制文本 | 格式（`kind` / `version`）由 `api_config_dialog._entry_file_payload` 定义，**不在试点另写一份** |
| `writeEntry(payload, name)` / `saveEntryAs(payload)` | 写进 `%TEMP%/portpanel_pilot/` ／ 原生「另存为」对话框 | 前者给**无人值守测试**用；后者与 Qt 侧 `share_image_source` 同口径（取消不算错） |
| `copyText(text)` | 写系统剪贴板 | `file://` 下前端拿不到系统剪贴板 → 走桥（与 Qt 侧同口径） |
| `debugEndpoint(req)` / `debugResult(jobId)` | 起一次子端口调试（**异步 job + 轮询**）：真的发一次包，回状态码 / 响应体 / 请求头·响应头·诊断文本 | 与 Qt 侧同链路（`EndpointDebugWorker` → requests，被风控/403 自动降级 `curl_cffi`）；**真实请求头只在 Python 侧解析**（前端只给 `sourceName` + `path`），回来的 `info` 是 `_format_debug_info` 的成品（敏感头已打码）。一次调试最长 30 s 超时 → 不能阻塞 GUI 线程，所以用 jobId 轮询 |
| `cloudState()` | 只读云服务配置现状（endpoint / bucket / region / KeyId 掩码 / 密钥是否已设置 / 客户端是否可用） | AccessKeySecret **不回前端**；配置键 `aliyun_*` 与 `image_sources` 在同一个 `data/api_config.json` 里 |
| `aliyunTest()` / `aliyunResult(jobId)` | 起一次阿里云连接测试（同样是 job + 轮询） | 复用 `AliyunTestWorker`（`initialize_aliyun_services` → `test_aliyun_connection`）；配置不全时明确报"缺少哪些字段" |

**为什么这些 API 用「按需懒加载」**：`api_config_dialog` 顶部就 import
`requests / bs4 / cryptography / aliyun_client`，冷启动约 6.5 s。试点对它做的是
**第一次用到才 import**（实测 ≈1.0–1.1 s，冷启动不付这笔钱）—— 这个数正是阶段 2 的
`EngineAPI` 最关心的。**不重写第二套解析**：口径与 Qt 侧一致，避免漂移。

端到端检查（真的在页面里调这些方法，跑握手 + 回调 + JSON 编解码）：本机有一份试点检查脚本
（**私有、不入库**，见 `web_config_pilot.py` 里 `Bridge` 各槽的注释）；日常回归也可以只用
`python web_config_pilot.py --selftest`（Qt 壳会走同一条链路）。

## 阶段 0 已量到的四个数（本机，offscreen）

| 判据 | 结果 | 门槛 |
| ---- | ---- | ---- |
| ① 冷启动（进程→桥接就绪） | ≈ 700 ms（其中窗口构造→就绪 ≈ 340 ms） | 与 Qt 版同量级或更好 |
| ② 桥接往返（中位 / 300 次） | ≈ 0.4 ms | ≤ 5 ms |
| ③ 内存增量 | 主进程 ≈ 120 MB + WebEngine 子进程 ≈ 80 MB | ≤ 150 MB |
| ④ 体感分 | 待人工填写 | 现代感 / 满意度 ≥ 4 |

对照：现有 Qt 版 `ImageSourceConfigDialog` 冷启动 ≈ 6.6 s（其中 import + 建 QApplication 就占 6.55 s，
对话框本身 26 ms）—— 因为那条链路要 import `api_config_dialog` 及其重依赖。
