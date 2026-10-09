# design/ —— 设计单一来源（token 与图标）

这个目录回答一个问题：**颜色 / 间距 / 圆角 / 字号 / 图标长什么样，谁说了算。**
答案是这里的两个 JSON：**其余全部是它们生成的**。

## 源（改这里）

| 文件 | 内容 |
| --- | --- |
| `tokens.json` | 语义色（bg/fg/line/accent/ok/warn/danger）、间距、圆角、字号、阴影、字体族 |
| `icons.json` | 图标几何（24×24、单色描边、`currentColor`）；加图标就是加一条 |

## 生成物（不要手工改）

| 产物 | 给谁用 | 生成命令 |
| --- | --- | --- |
| `webui/src/styles/tokens.css` | Web 侧（Vite 直接 import）→ CSS 变量 `--bg-0` / `--sp-2` / `--r-2` … | `python design/tools/gen_tokens.py` |
| `design/generated/qt_tokens.py` | Qt 侧**候选**常量（**尚未接线**，见下） | 同上 |
| `webui/src/assets/icons/*.svg` + `index.ts` | Web 侧图标（`?raw` 内联，随字号缩放、随文字变色） | `python design/tools/gen_icons.py` |
| `design/generated/emoji-inventory.md` | 存量 emoji → SVG 的替换清单（只读产物） | `python design/tools/emoji_inventory.py` |

## 校验（可进 CI / 每次改动后跑）

```bash
python design/tools/gen_tokens.py --check    # TOKENS_OK / TOKENS_DRIFT（退出码 1）
python design/tools/gen_icons.py  --check    # ICONS_OK  / ICONS_DRIFT
```

## 为什么要有这个目录

改动前的实测：**172 个去重硬编码色值**（散在 213 处内联样式里）、字号 7 种、圆角 7 种、间距 19 种、
**172 处 emoji 当图标（38 种字形）**。在 Qt + Web 双栈并存期，这种散落必然导致两边视觉漂移。
把「取值」和「图标几何」各收敛成一份源之后：

- Qt 侧与 Web 侧**引用同一组值**（只是产物形式不同）；
- 换肤 / 调间距 / 加图标都只改一处；
- 漂移可以被脚本**机械检出**（`--check`）。

## 尚未接线（重要）

`design/generated/qt_tokens.py` 目前只是**候选常量**，还没有被 `dark_theme.py` 引用。
原因：`dark_theme.py` 与宿主程序（全栈图库管理器）**共用**，把它的字面量替换成这些常量属于一次真实重构，
必须单独评估（否则会影响宿主的外观）。当前这一步只保证：**同一份 token 能同时产出 Qt 与 Web 两边可用的值。**
