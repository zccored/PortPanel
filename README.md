# 端口画板 · PortPanel

> **可视化节点画板 + API 图源配置器** —— 从「全栈图库管理器」里抽出来的独立工具。
> 拖拽连线搭一条下载流水线，挂上并发/间隔/超时参数，按图纸跑，按图纸存。
>
> A standalone **visual node board & API source configurator**, extracted from the
> *全栈图库管理器* (Full-Stack Gallery Manager) suite. Wire nodes into a download
> pipeline, tune per-drawing rate limits, run it, save it — all without the host app.

[中文](#中文) ｜ [English](#english)

---

# 中文

## 这是什么

**端口画板**前身叫「平台整合器」（2026-10-06 正式更名），原本是全栈图库管理器里的一个窗口。
现在它被拆出来，可以：

- **单独运行**（源码 `python port_panel.py`，或直接用打包版 `端口画板.exe`）；
- **仍然能被主程序打开**（两边是同一套代码，不存在"两处各异"）；
- **被「图库检索管理器 image-search」一键切换拉起**。

它的核心是一张**画板**：左边从「API 入口」里拖一个接口到画布上，右边挂容器框、数据处理框、
数据库框，中间用线连起来 —— 一张图纸就是一条完整的下载流水线。

## 快速开始

### 方式一：用打包版（普通用户推荐）

1. 到 [Releases](https://github.com/zccored/PortPanel/releases) 下载
   `PortPanel-vX.Y.Z-win64.zip`；
2. 解压到任意目录（**别放在 `C:\Program Files` 之类需要管理员权限的地方** ——
   程序要往自己的 `data/` 里写配置与日志）；
3. 双击 `端口画板.exe`。

绿色免安装：整个目录拷走就能用，卸载 = 删目录。

### 方式二：源码运行

```bash
git clone https://github.com/zccored/PortPanel.git
cd PortPanel
python -m pip install -r requirements.txt
python port_panel.py
```

要求 **Python 3.12+**（开发环境 3.12.x）。

### 方式三：从主程序打开

如果同目录下装了「全栈图库管理器」，在它的工具栏点 **📷 图源配置** 或
**连接 → 端口画板** 即可 —— 打开的是一模一样的窗口。

## 界面速览

```
┌─ 工具栏 ────────────────────────────────────────────────────────────────────┐
│ ▶执行  ▸轮询参数  📷图源配置  🗄️数据库框  📦容器框  💾保存流程  📂加载流程  🛠️工具栏 │
├─ 图纸选项卡 ────────────────────────────────────────────────────────────────┤
│ [图纸A*] [图纸B] [图纸C]                                                    │
├──────────────┬─────────────────────────────────────────────┬────────────────┤
│ 🗂 侧栏    ◀ │            📊 数据传递参数 ▼                │ ▶ 数据处理/输出 │
│ 📡 API 入口   │  并发 4 · 等待 0.5s · 重试 3×1s    [恢复默认] │  API 收发日志   │
│ 📁 已保存流程图│ ┌─────────────────────────────────────────┐ │  …             │
│              │ │         可视 化 画 板（节点图）          │ │  输出           │
│              │ └─────────────────────────────────────────┘ │                │
└──────────────┴─────────────────────────────────────────────┴────────────────┘
```

- **黑夜模式固定**：无论你的 Windows 是深色还是浅色主题，界面永远是深色 —— 与作者本机一致。
- **两侧栏可整栏横收**：点侧边竖条上的 `◀` / `▶`，面板缩成 18px 细边，画板拿到全部宽度。

## 功能一览

### 一、画板与节点

| 节点 | 作用 |
|---|---|
| **API 节点** | 请求引擎。参数替换（`{name}` 占位符）+ 数组迭代逐项请求；自动识别 JSON / 二进制响应；请求头可自定义 |
| **数据库框** | CSV / Excel 逐行输出与消费；上游数据可自动写入（upsert / 追加，可指定主键列）；游标按轮次推进 |
| **数据处理框** | 正则替换、按路径从上游 JSON 取字段、拼接 |
| **容器框** | 下载落盘。存储路径、**动态根目录输入点**（由上游决定写进哪个文件夹）、命名规则拼合（常量/时间/上游字段）、字节与文件数统计 |
| **步进器** | 数值发生器：起始值 + 步长 + 公式，喂给下游做分页参数 |
| **站点解析** | 抓 HTML 页面，按规则抽出文件列表 |
| **图片节点** | 参考元素（不参与执行，用于备查） |
| **类框** | 把若干元素圈成一组：独立轮询次数、独立自增开关、类间触发连线 |

其它画板能力：

- 多图纸**选项卡**，每张图纸独立场景 / 日志 / 输出 / 参数；
- 撤销栈（20 步）、整块框选拖动、连线拖拽到视口边缘自动滚动；
- **悬停 SVG 提示框**：鼠标悬停任一元素框，弹出一张按该框真实端口**实时生成**的示意图 ——
  端口键名、当前值，以及你在「图源配置」里为每个参数写的**注释**、为每个子端口写的**说明**；
  框外还有 26px 不可视缓冲带，鼠标移进去不会闪退，可以直接在框内移动。

### 二、执行引擎

- **拓扑排序**决定执行顺序（不是按摆放位置）；
- **上下轮询锁**：上游本轮没就绪，下游不会拿上一轮的老数据往下写；
- **大轮询**两种模式：固定轮数拨盘 / **自增**（1 → 99，遇下载层错误或数据走完即停）；
- **类嵌套循环**：外层跑完 1 轮就把控制权交给子类，子类按自己的规则把**整段**跑完才回外层 ——
  信息层层向下传递，不会"主类跑完了子类没收到"；
- **类内分页双层循环**：步进器当**内层页码**、数据库框当**外层数据行**；
  每个作者从第 1 页翻到翻不动为止，才换下一个作者（而不是"每个作者只下一页"）；
- 元素间等待、随时取消、执行中**禁止重复启动**（再点会提示去左下角取消）。

### 三、下载器

- **三种下载模式**自动选择：小文件进内存 / 大文件 Range 分块并行 / 超大文件流式写临时文件；
- **批内并发**（默认 4 个文件同时下）+ **单文件分块并发**（默认 4 线程）；
- 失败重试（次数与间隔可调）、连接/读取超时分离、断点续传；
- **403 专判**：服务端明确拒绝时不当作"文件内容"读进来反复重试；
- **curl_cffi 浏览器指纹回退**：`requests` 被 TLS 指纹风控拦住时自动换通道；
- **去重清单** `download_manifest.json`（写读同根）+ **本地 sha256 完整性复核** ——
  同尺寸但内容已损坏的文件不会被误判成"已下载"。

### 四、数据传递参数仪表盘（本工具的特色）

画板顶部那一条「📊 数据传递参数」，把散落各处的**14 项速率/频次参数**收进一处：

| 分组 | 参数 |
|---|---|
| 并发与频次 | 并行下载文件数、单文件重试次数、重试间隔、元素间等待、大轮询次数、全局自增轮询 |
| 超时 | 连接超时、读取超时、API 请求超时 |
| 分块与阈值 | 单文件分块并发、分块大小、图片小文件阈值、其他小文件阈值、流式写盘阈值 |

设计要点：

- **随图纸保存**（存进 `.wbt` 的 `transfer` 键）—— 参数因图纸而异，换图纸就换一套；
- **执行时快照一次**：运行中再改参数不影响本轮，下一轮/下次执行才生效；
- **每个下载线程显式持有那份快照**（不可变对象）—— 为将来"多张图纸并发下载"预留了线程隔离；
- **默认值 = 引入该功能之前的原值**，没动过参数 = 行为与以前逐字节一致；
- 老图纸首次打开会用默认值在内存里**缓冲更新**一份，保存时会多问一次「是否更新下载参数？」。

### 五、图源配置

独立窗口，也可以从主程序的「API 和云服务配置」里进去（同一套代码）：

- **API 入口**（一个站点）→ **子端口**（一条路径）→ **参数**（名 / 类型 / 值 / **注释**）；
- 请求头：自定义、`${ENV:VAR}` 环境变量占位符、实时时间头、Cookie 导入与验证、批量粘贴；
- **调试**：真实发包、响应诊断（是不是风控页、缺哪个头、Cookie 有没有生效）；
- **📥 导入入口 / 📤 分享入口**：一个入口 = 一个 `.apientry.json`，导出即分享，
  别人拿到直接导入就能用（重名自动加 `(2)`，也兼容整套配置文件）。

### 六、日志与输出

- 数据处理页日志**实时落盘** `logs/run_<时间戳>/session.log`，崩了也能查；
- 超 1000 行自动**窗口化折叠**，滚动到边缘按需加载，跑几万行也不卡；
- 纯文本 / 富文本（JSON 树状着色）两种视图，且**内容永远一致**；
- 输出面板按条记录每次下载：根目录 / 子文件夹、进度条、完成后点击**直达资源管理器**。

### 七、与「图库检索管理器 image-search」的互切

两边是一对可以互相拉起的程序：

| 方向 | 触发 | 行为 |
|---|---|---|
| 端口画板 → image-search | 工具栏「🔁 下载完成→图库检索」 | 写交接请求 → 分离启动过渡进程 → **自己退出让出内存** → image-search 自动建库并回写结果 |
| image-search → 端口画板 | 主界面的「⇄ 切换启动」 | 校验目标 → 启动对端 → 关掉自己 |

**目标优先级**（image-search 侧）：

1. 环境里有 `main.py`（全栈图库管理器主程序）→ **优先且只用它**，并做 SHA256 白名单校验（防篡改）；
2. 没有 `main.py`，但有 `端口画板.exe`（或源码入口）→ 用它，**不做哈希校验**；
3. 都没有 → 按钮不予激活。

也就是说：把打包版单独发给别人，对方机器上照样能从 image-search 一键切过来。

## 目录结构

```
PortPanel/
├─ port_panel.py            # 独立启动入口（打包也是它）
├─ img_server.py            # 画板主体（节点、执行引擎、下载器、悬停提示、仪表盘…）
├─ api_config_dialog.py     # 图源配置 / 请求头 / Cookie / 调试
├─ dark_theme.py            # 黑夜模式主题（与主程序共用的唯一一份）
├─ logger_manager.py        # 日志
├─ secure_store.py          # 明文仅内存的密文缓存
├─ aliyun_client.py         # 本地同步服务（api_config_dialog 依赖）
├─ port_panel.spec          # PyInstaller 打包配置
├─ requirements.txt
└─ data/
    ├─ node_svg/            # 悬停提示框的 SVG 模板（9 个，纯 UI 模板）
    └─ fonts/               # 渲染用字体
```

> **这个仓库里没有图纸、没有 API 入口内容、没有任何凭据。** 这是刻意的：
> `.wbt` 图纸会原样存下请求头（含真实 Cookie/session），API 入口内容属于使用者自己的数据，
> 都不应该随代码分发。仓库与发行包里只有**程序本身和纯 UI 资源**。
> 首次运行时程序会自己生成一份空的加密配置，图纸也要你自己新建 —— 从零开始搭，或导入别人给你的图纸。

## 自己打包

```bash
python -m pip install pyinstaller==6.15.0
python -m PyInstaller --noconfirm --clean port_panel.spec
# 产物：dist_panel/端口画板/端口画板.exe（onedir，整个目录一起分发）
```

`port_panel.spec` 里做了几件事，自己改的话注意别破坏：

- **onedir 而不是 onefile**：onefile 每次启动都要解压 ~200MB，首启 10 秒起；onedir 秒开，
  也方便 image-search 按路径找到 exe；
- **排除用不到的 Qt 模块**（WebEngine / Quick / 3D / Multimedia…），不排的话包会大三四百 MB；
- **打包清单是白名单**：只列 `data/node_svg` 与 `data/fonts` 两个纯资源目录。
  文件里还有一道**兜底闸门** —— 一旦 `datas` 里出现 `webtree` / `manifest` / `webAPI` /
  `api_config.json`，构建会**直接报错退出**，而不是把用户数据悄悄打进发布包。
  （这道闸门是补出来的：第一版用的是"排除已知敏感目录"的黑名单写法，
  结果 `data/manifest/`、`data/webAPI/` 这两个运行时数据目录被打了进去 ——
  里面是使用者本机的下载记录和抓取到的名录。教训：**打包清单要用白名单**，
  因为 `data/` 下面随时会长出新的用户数据目录。）

## 隐私与安全

**仓库与发行包里不含任何使用者数据。** 具体来说，下面这些**一律不分发**：

| 类别 | 为什么 |
|---|---|
| `.wbt` 图纸 | 里面既有 API 入口（`api_ref`），又有**原样存盘的请求头**（真实 Cookie / session） |
| API 入口内容 | 站点地址、子端口路径、参数、请求头、Cookie —— 属于使用者自己的配置 |
| `data/api_config.json` | 真实图源配置（已加密），首次运行时由程序自己生成 |
| `data/manifest/` | 下载清单 —— 含**本机存储路径、内容标题、文件哈希** |
| `data/webAPI/` 等抓取结果 | 抓下来的名录/表格等，是使用者的数据 |

程序侧的保护：

- `data/api_config.json` 用 **AES（Fernet）+ 机器绑定密钥**加密落盘，备份文件
  `*.plain.backup` 也走密文缓存（明文只在内存里）；
- 程序只在你按下「执行」或「调试」时才会向外发请求，不会后台偷偷联网。

如果你要**分享自己的图纸**给别人：`.wbt` 会把请求头原样带出去，
请先把里面的 Cookie / Authorization 之类清掉或换成 `${ENV:...}` 占位符再发。

## 许可证

**AGPL-3.0-only**，见 [LICENSE](LICENSE)。

你可以自由使用、修改、再分发；但如果你把它（或修改版）作为网络服务提供给他人，
必须公开你的完整源码。

---

# English

## What is this

**PortPanel** (formerly "Platform Integrator") used to be a window inside the
*Full-Stack Gallery Manager*. It now stands on its own:

- **Runs standalone** — either from source (`python port_panel.py`) or as the packaged
  `端口画板.exe`;
- **Still opens from the host app** — both entry points share the exact same code, so
  they can never drift apart;
- **Can be launched by the [ImageSearchTool](https://github.com/zccored/ImageSearchTool)
  with one click.**

At its core is a **canvas**: drag an endpoint from the "API entry" tree onto the board,
attach a container node, a data-process node and a CSV node, wire them together — one
drawing *is* one download pipeline.

## Quick start

### Option 1 — packaged build (recommended for end users)

1. Grab `PortPanel-vX.Y.Z-win64.zip` from
   [Releases](https://github.com/zccored/PortPanel/releases);
2. Unzip anywhere **except** privileged locations such as `C:\Program Files` — the app
   writes its own config and logs into its `data/` folder;
3. Double-click `端口画板.exe`.

Portable: copy the folder anywhere, uninstall by deleting it.

### Option 2 — from source

```bash
git clone https://github.com/zccored/PortPanel.git
cd PortPanel
python -m pip install -r requirements.txt
python port_panel.py
```

Requires **Python 3.12+** (developed on 3.12.x).

### Option 3 — from the host application

If the Full-Stack Gallery Manager sits next to it, click **📷 Image Sources** on its
toolbar (or *Connect → PortPanel*). You get exactly the same window.

## Features at a glance

**Canvas & nodes** — API node (parameter substitution + array iteration, JSON/binary
auto-detection), CSV/Excel node (row-by-row in/out, upstream auto-ingest with an
optional key column), data-process node (regex, JSON path extraction), container node
(storage path, **dynamic root folder driven by an upstream value**, naming-rule
composition), stepper, site parser, image node, and *class* boxes that group elements
with their own polling count and auto-increment switch.

**Execution engine** — topological ordering, upstream/downstream poll gating (a
downstream node never writes stale data), fixed-count or auto-increment (1→99) polling,
**nested class loops** (outer runs one round, hands over to the inner class which
finishes its *entire* loop before control returns), **two-level pagination inside a
class** (stepper = inner page, CSV row = outer record), per-element wait, cancel
anytime, and a re-entrancy guard that refuses a second start while a run is in flight.

**Downloader** — three automatic modes (in-memory / parallel Range chunks / streaming
to a temp file), configurable batch concurrency and per-file chunk concurrency, retry
count and delay, separate connect/read/API timeouts, resume support, dedicated 403
handling, `curl_cffi` browser-fingerprint fallback, a dedup manifest plus **local
sha256 integrity re-check** so a corrupted file is never mistaken for "already
downloaded".

**Transfer-parameter dashboard** — the 14 rate/frequency knobs (concurrency, retries,
retry delay, connect/read/API timeouts, chunk size and workers, size thresholds,
inter-element wait, poll count, auto-increment) live in one collapsible bar. They are
**saved per drawing** (`.wbt` key `transfer`), snapshotted once when a run starts, and
each worker thread holds its own immutable copy — groundwork for concurrent
multi-drawing downloads. Defaults equal the pre-feature constants byte for byte.

**Image-source configurator** — API entry → sub-endpoint → parameters (name / type /
value / **note**); custom headers with `${ENV:VAR}` placeholders, live timestamp
headers, cookie import & verification, bulk paste; a real-request debugger with
response diagnostics; and **import/share an entry** as a single `.apientry.json`.

**Logging & output** — logs stream to `logs/run_<timestamp>/session.log`, windowed at
1000 lines with on-demand paging, plain-text and syntax-highlighted views that always
agree; the output panel tracks every download with a progress bar and a click-through
to Windows Explorer.

**Interop with ImageSearchTool** — "🔁 Download done → gallery indexing" writes a
handoff request, spawns a detached transition process, then quits to free memory.
In the other direction ImageSearchTool's "⇄ Switch" button starts PortPanel,
preferring `main.py` (SHA256 whitelist verified) and falling back to the packaged
`端口画板.exe` (no verification) when the host app isn't present.

**Always dark.** The UI is forced to the dark theme regardless of your Windows theme,
matching the author's environment. Both sidebars collapse to an 18-pixel edge strip,
handing the full width to the canvas.

## Packaging it yourself

```bash
python -m pip install pyinstaller==6.15.0
python -m PyInstaller --noconfirm --clean port_panel.spec
# output: dist_panel/端口画板/端口画板.exe  (onedir — ship the whole folder)
```

## Privacy & security

**Neither the repository nor the release archive contains any user data.** The
following are deliberately **not distributed**:

| Category | Why |
|---|---|
| `.wbt` drawings | They embed both the API entry (`api_ref`) and request headers **stored verbatim** (real cookies / session tokens) |
| API entry content | Site URLs, endpoint paths, parameters, headers, cookies — that is your own configuration |
| `data/api_config.json` | The real (encrypted) source config; the app generates it on first run |
| `data/manifest/` | Download manifest — contains **local storage paths, content titles and file hashes** |
| `data/webAPI/` and similar scrape output | Data you harvested, i.e. yours |

Protections built into the app:

- `data/api_config.json` is encrypted (Fernet with a machine-bound key), and its
  `*.plain.backup` also goes through the ciphertext cache — plaintext stays in memory only;
- The app only talks to the network when you press *Run* or *Debug*.

Sharing **your own** drawing with someone else? `.wbt` carries request headers
verbatim — strip cookies / Authorization first, or replace them with `${ENV:...}`
placeholders.

## License

**AGPL-3.0-only** — see [LICENSE](LICENSE).
