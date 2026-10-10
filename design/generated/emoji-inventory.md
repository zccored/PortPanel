# 图标清点 · emoji / 符号 → SVG 替换清单

- 生成：2026-10-11 ｜ 脚本：`design/tools/emoji_inventory.py`（只读源码）
- 目标：**新增 UI 一律用 `design/icons.json` 里的 SVG 图标**，不再用 emoji 当图标；
  存量按本表分批替换（`webui/src/components/Icon.vue` 是唯一入口）。

## 汇总

- 非 BMP emoji：**172 处 / 38 种字形**

| 用途分类（按所在行判定） | 处数 | 处理 |
| --- | --- | --- |
| UI 控件文本（必须换） | 61 | 换 SVG |
| 日志 / 纯文本（可保留） | 56 | 可保留 / 人工判断 |
| 其它（人工判断） | 55 | 可保留 / 人工判断 |

## 明细（按出现次数排序）

| 码点 | Unicode 名 | 次数 | 建议图标 | 主要位置 |
| --- | --- | --- | --- | --- |
| `U+1F36A` | COOKIE | 13 | `cookie` | portpanel/ui/flow_editor.py:5950、portpanel/ui/flow_editor.py:6088、portpanel/ui/image_source.py:779 |
| `U+1F4BE` | FLOPPY DISK | 11 | `save` | portpanel/ui/flow_editor.py:10184、portpanel/ui/flow_editor.py:14310、portpanel/ui/flow_editor.py:14782 |
| `U+1F4E5` | INBOX TRAY | 11 | `import` | portpanel/ui/flow_editor.py:12821、portpanel/ui/image_source.py:891、portpanel/ui/image_source.py:974 |
| `U+1F4C2` | OPEN FILE FOLDER | 8 | `folder` | portpanel/ui/flow_editor.py:2824、portpanel/ui/flow_editor.py:2903、portpanel/ui/flow_editor.py:2913 |
| `U+1F4E6` | PACKAGE | 8 | `package` | portpanel/ui/flow_editor.py:10174、portpanel/ui/flow_editor.py:13033、portpanel/ui/flow_editor.py:13768 |
| `U+1F4CE` | PAPERCLIP | 7 | 待定 | portpanel/ui/flow_editor.py:2486、portpanel/ui/flow_editor.py:2629、portpanel/ui/flow_editor.py:6758 |
| `U+1F4CC` | PUSHPIN | 7 | 待定 | portpanel/ui/flow_editor.py:3016、portpanel/ui/flow_editor.py:3049、portpanel/ui/flow_editor.py:3052 |
| `U+1F504` | ANTICLOCKWISE DOWNWARDS AND UPWARDS OPEN CIRCLE ARROWS | 7 | `refresh` | portpanel/ui/flow_editor.py:5171、portpanel/ui/flow_editor.py:10687、portpanel/ui/flow_editor.py:10708 |
| `U+1F527` | WRENCH | 6 | `sliders` | portpanel/ui/flow_editor.py:6331、portpanel/ui/flow_editor.py:6331、portpanel/ui/flow_editor.py:10694 |
| `U+1F517` | LINK SYMBOL | 6 | 待定 | portpanel/ui/flow_editor.py:7033、portpanel/ui/flow_editor.py:7065、portpanel/ui/flow_editor.py:7067 |
| `U+1F393` | GRADUATION CAP | 6 | `graduation` | portpanel/ui/flow_editor.py:9948、portpanel/ui/flow_editor.py:10219、portpanel/ui/image_source.py:2446 |
| `U+1F5D1` | WASTEBASKET | 6 | `trash` | portpanel/ui/flow_editor.py:10755、portpanel/ui/flow_editor.py:16527、portpanel/ui/image_source.py:2437 |
| `U+1F4E4` | OUTBOX TRAY | 6 | `share` | portpanel/ui/image_source.py:2423、portpanel/ui/image_source.py:2429、portpanel/ui/image_source.py:2933 |
| `U+1F4C1` | FILE FOLDER | 5 | `folder` | portpanel/ui/flow_editor.py:2912、portpanel/ui/flow_editor.py:10357、portpanel/ui/flow_editor.py:12915 |
| `U+1F310` | GLOBE WITH MERIDIANS | 5 | `folder` | portpanel/ui/flow_editor.py:4220、portpanel/ui/flow_editor.py:10216、portpanel/ui/flow_editor.py:12844 |
| `U+1F41B` | BUG | 5 | 待定 | portpanel/ui/flow_editor.py:12685、portpanel/ui/flow_editor.py:13369、portpanel/ui/flow_editor.py:13596 |
| `U+1F50E` | RIGHT-POINTING MAGNIFYING GLASS | 5 | 待定 | portpanel/ui/flow_editor.py:15170、portpanel/ui/image_source.py:2887、portpanel/ui/image_source.py:3214 |
| `U+1F6D1` | OCTAGONAL SIGN | 5 | 待定 | portpanel/ui/flow_editor.py:15258、portpanel/ui/flow_editor.py:15639、portpanel/ui/flow_editor.py:15647 |
| `U+1F4CB` | CLIPBOARD | 4 | 待定 | portpanel/ui/flow_editor.py:5960、portpanel/ui/flow_editor.py:12080、portpanel/ui/image_source.py:1441 |
| `U+1F6E0` | HAMMER AND WRENCH | 4 | `sliders` | portpanel/ui/flow_editor.py:9948、portpanel/ui/flow_editor.py:10204、portpanel/ui/tour_script_panel.py:79 |
| `U+1F4F7` | CAMERA | 4 | `image` | portpanel/ui/flow_editor.py:10012、portpanel/ui/flow_editor.py:10152、portpanel/ui/image_source.py:2401 |
| `U+1F552` | CLOCK FACE THREE OCLOCK | 4 | 待定 | portpanel/ui/flow_editor.py:13683、portpanel/ui/flow_editor.py:15481、portpanel/ui/image_source.py:1739 |
| `U+1F4C4` | PAGE FACING UP | 3 | 待定 | portpanel/ui/flow_editor.py:2914、portpanel/ui/flow_editor.py:10757、portpanel/ui/flow_editor.py:15855 |
| `U+1F522` | INPUT SYMBOL FOR NUMBERS | 3 | 待定 | portpanel/ui/flow_editor.py:3348、portpanel/ui/flow_editor.py:10214、portpanel/ui/flow_editor.py:12784 |
| `U+1F4E1` | SATELLITE ANTENNA | 3 | 待定 | portpanel/ui/flow_editor.py:9989、portpanel/ui/flow_editor.py:10310、portpanel/ui/tour_script_image_source.py:228 |
| `U+1F680` | ROCKET | 3 | 待定 | portpanel/ui/image_source.py:2907、portpanel/ui/image_source.py:3026、portpanel/ui/tour_script_image_source.py:181 |
| `U+1F4DD` | MEMO | 2 | 待定 | portpanel/ui/flow_editor.py:10530、portpanel/ui/image_source.py:2818 |
| `U+1F4DC` | SCROLL | 2 | 待定 | portpanel/ui/flow_editor.py:11668、portpanel/ui/flow_editor.py:11682 |
| `U+1F4A1` | ELECTRIC LIGHT BULB | 2 | 待定 | portpanel/ui/flow_editor.py:13337、portpanel/ui/flow_editor.py:13865 |
| `U+1F4CA` | BAR CHART | 2 | 待定 | portpanel/ui/flow_editor.py:13935、portpanel/ui/flow_editor.py:15109 |
| `U+1F5C2` | CARD INDEX DIVIDERS | 2 | 待定 | portpanel/ui/flow_editor.py:15186、portpanel/ui/flow_editor.py:15652 |
| `U+1F3A8` | ARTIST PALETTE | 1 | 待定 | portpanel/ui/flow_editor.py:3997 |
| `U+1F4D6` | OPEN BOOK | 1 | 待定 | portpanel/ui/flow_editor.py:8345 |
| `U+1F5C4` | FILE CABINET | 1 | `database` | portpanel/ui/flow_editor.py:10165 |
| `U+1F501` | CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS | 1 | `refresh` | portpanel/ui/flow_editor.py:10228 |
| `U+1F6E1` | SHIELD | 1 | 待定 | portpanel/ui/flow_editor.py:13772 |
| `U+1F4CD` | ROUND PUSHPIN | 1 | 待定 | portpanel/ui/flow_editor.py:14905 |
| `U+1F50D` | LEFT-POINTING MAGNIFYING GLASS | 1 | 待定 | portpanel/ui/image_source.py:2955 |

## BMP 级符号图标（不是 emoji，但同样当图标用）

| 字符 | 建议图标 | 次数 |
| --- | --- | --- |
| `U+26A0` | `warning` | 57 |
| `U+2705` | `check` | 27 |
| `U+274C` | `close` | 17 |
| `U+25B6` | `play` | 15 |
| `U+00D7` | `close` | 14 |
| `U+25C0` | `play` | 7 |

## 用法（新代码怎么写）

```vue
<Icon name="cookie" title="凭据" />   <!-- 颜色随文字（currentColor），尺寸随字号 -->
```

- 需要新图标：往 `design/icons.json` 加一条 → 跑 `python design/tools/gen_icons.py`；
- **不要**在 Vue 里直接写 `<svg>` 或再用 emoji；
- 日志里的 emoji 可保留（纯文本，`_strip_emoji` 已处理富文本注释行）。
