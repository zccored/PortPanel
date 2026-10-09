# 图标清点 · emoji / 符号 → SVG 替换清单

- 生成：2026-10-10 ｜ 脚本：`design/tools/emoji_inventory.py`（只读源码）
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
| `U+1F36A` | COOKIE | 13 | `cookie` | img_server.py:5910、img_server.py:6048、api_config_dialog.py:778 |
| `U+1F4BE` | FLOPPY DISK | 11 | `save` | img_server.py:10056、img_server.py:14126、img_server.py:14598 |
| `U+1F4E5` | INBOX TRAY | 11 | `import` | img_server.py:12637、api_config_dialog.py:890、api_config_dialog.py:973 |
| `U+1F4C2` | OPEN FILE FOLDER | 8 | `folder` | img_server.py:2784、img_server.py:2863、img_server.py:2873 |
| `U+1F4E6` | PACKAGE | 8 | `package` | img_server.py:10046、img_server.py:12849、img_server.py:13584 |
| `U+1F4CE` | PAPERCLIP | 7 | 待定 | img_server.py:2446、img_server.py:2589、img_server.py:6718 |
| `U+1F4CC` | PUSHPIN | 7 | 待定 | img_server.py:2976、img_server.py:3009、img_server.py:3012 |
| `U+1F504` | ANTICLOCKWISE DOWNWARDS AND UPWARDS OPEN CIRCLE ARROWS | 7 | `refresh` | img_server.py:5131、img_server.py:10559、img_server.py:10580 |
| `U+1F527` | WRENCH | 6 | `sliders` | img_server.py:6291、img_server.py:6291、img_server.py:10566 |
| `U+1F517` | LINK SYMBOL | 6 | 待定 | img_server.py:6993、img_server.py:7025、img_server.py:7027 |
| `U+1F393` | GRADUATION CAP | 6 | `graduation` | img_server.py:9874、img_server.py:10091、api_config_dialog.py:2445 |
| `U+1F5D1` | WASTEBASKET | 6 | `trash` | img_server.py:10627、img_server.py:16324、api_config_dialog.py:2436 |
| `U+1F4E4` | OUTBOX TRAY | 6 | `share` | api_config_dialog.py:2422、api_config_dialog.py:2428、api_config_dialog.py:2932 |
| `U+1F4C1` | FILE FOLDER | 5 | `folder` | img_server.py:2872、img_server.py:10229、img_server.py:12731 |
| `U+1F310` | GLOBE WITH MERIDIANS | 5 | `folder` | img_server.py:4180、img_server.py:10088、img_server.py:12660 |
| `U+1F41B` | BUG | 5 | 待定 | img_server.py:12501、img_server.py:13185、img_server.py:13412 |
| `U+1F50E` | RIGHT-POINTING MAGNIFYING GLASS | 5 | 待定 | img_server.py:14986、api_config_dialog.py:2886、api_config_dialog.py:3213 |
| `U+1F6D1` | OCTAGONAL SIGN | 5 | 待定 | img_server.py:15074、img_server.py:15455、img_server.py:15463 |
| `U+1F4CB` | CLIPBOARD | 4 | 待定 | img_server.py:5920、img_server.py:11896、api_config_dialog.py:1440 |
| `U+1F6E0` | HAMMER AND WRENCH | 4 | `sliders` | img_server.py:9874、img_server.py:10076、tour_script_panel.py:79 |
| `U+1F4F7` | CAMERA | 4 | `image` | img_server.py:9938、img_server.py:10024、api_config_dialog.py:2400 |
| `U+1F552` | CLOCK FACE THREE OCLOCK | 4 | 待定 | img_server.py:13499、img_server.py:15297、api_config_dialog.py:1738 |
| `U+1F4C4` | PAGE FACING UP | 3 | 待定 | img_server.py:2874、img_server.py:10629、img_server.py:15671 |
| `U+1F522` | INPUT SYMBOL FOR NUMBERS | 3 | 待定 | img_server.py:3308、img_server.py:10086、img_server.py:12600 |
| `U+1F4E1` | SATELLITE ANTENNA | 3 | 待定 | img_server.py:9915、img_server.py:10182、tour_script_image_source.py:228 |
| `U+1F680` | ROCKET | 3 | 待定 | api_config_dialog.py:2906、api_config_dialog.py:3025、tour_script_image_source.py:181 |
| `U+1F4DD` | MEMO | 2 | 待定 | img_server.py:10402、api_config_dialog.py:2817 |
| `U+1F4DC` | SCROLL | 2 | 待定 | img_server.py:11484、img_server.py:11498 |
| `U+1F4A1` | ELECTRIC LIGHT BULB | 2 | 待定 | img_server.py:13153、img_server.py:13681 |
| `U+1F4CA` | BAR CHART | 2 | 待定 | img_server.py:13751、img_server.py:14925 |
| `U+1F5C2` | CARD INDEX DIVIDERS | 2 | 待定 | img_server.py:15002、img_server.py:15468 |
| `U+1F3A8` | ARTIST PALETTE | 1 | 待定 | img_server.py:3957 |
| `U+1F4D6` | OPEN BOOK | 1 | 待定 | img_server.py:8303 |
| `U+1F5C4` | FILE CABINET | 1 | `database` | img_server.py:10037 |
| `U+1F501` | CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS | 1 | `refresh` | img_server.py:10100 |
| `U+1F6E1` | SHIELD | 1 | 待定 | img_server.py:13588 |
| `U+1F4CD` | ROUND PUSHPIN | 1 | 待定 | img_server.py:14721 |
| `U+1F50D` | LEFT-POINTING MAGNIFYING GLASS | 1 | 待定 | api_config_dialog.py:2954 |

## BMP 级符号图标（不是 emoji，但同样当图标用）

| 字符 | 建议图标 | 次数 |
| --- | --- | --- |
| `U+26A0` | `warning` | 55 |
| `U+2705` | `check` | 27 |
| `U+274C` | `close` | 17 |
| `U+25B6` | `play` | 15 |
| `U+00D7` | `close` | 13 |
| `U+25C0` | `play` | 7 |

## 用法（新代码怎么写）

```vue
<Icon name="cookie" title="凭据" />   <!-- 颜色随文字（currentColor），尺寸随字号 -->
```

- 需要新图标：往 `design/icons.json` 加一条 → 跑 `python design/tools/gen_icons.py`；
- **不要**在 Vue 里直接写 `<svg>` 或再用 emoji；
- 日志里的 emoji 可保留（纯文本，`_strip_emoji` 已处理富文本注释行）。
