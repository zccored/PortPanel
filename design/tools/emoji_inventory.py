# -*- coding: utf-8 -*-
"""图标清点：扫出仓库里当图标用的 emoji / 符号，产出**替换清单**。

用法（仓库根目录）：
    python design/tools/emoji_inventory.py        # 写 design/generated/emoji-inventory.md

为什么：现状 UI 里共 172 处 emoji 当图标（38 种字形）。它们跨 Windows 版本字形不一致、
无法统一配色、无法做悬停态 → 统一换 `design/icons.json` 里的 SVG。
本脚本把它们按「用途（UI 控件文本 / 日志文本 / 其它）」分类，并给出建议替换的图标名。

只读源码，只写上面那一份 md。
"""
from __future__ import annotations

import collections
import datetime
import os
import re
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, 'design', 'generated', 'emoji-inventory.md')
FILES = ['portpanel/ui/flow_editor.py', 'portpanel/ui/image_source.py', 'port_panel.py',
         'portpanel/ui/tour_layer.py', 'portpanel/ui/tour_script_panel.py',
         'portpanel/ui/tour_script_image_source.py', 'web_config_pilot.py']

# 「像日志/纯文本」的上下文 → emoji 可保留（日志里直观，且已在 _strip_emoji 处理过富文本）
LOG_HINT = re.compile(r'\.append\(|print\(|\.log\b|log_received|_log_|session_log|f["\']')
# 「像 UI 控件文本」的上下文 → 必须换 SVG
UI_HINT = re.compile(
    r'setText\(|setToolTip\(|setWindowTitle\(|setPlaceholderText\(|QPushButton\(|addAction\('
    r'|QMessageBox|QLabel\(|addTab\(|setTabText\(')

# emoji → 建议图标名（对应 design/icons.json）；没映射的标「待定」
EMOJI_ICON = {
    '\U0001F36A': 'cookie', '\U0001F4BE': 'save', '\U0001F4E5': 'import',
    '\U0001F4E4': 'share', '\U0001F4E6': 'package', '\U0001F4C2': 'folder',
    '\U0001F4C1': 'folder', '\U0001F5D1': 'trash', '\U0001F504': 'refresh',
    '\U0001F501': 'refresh', '\U0001F527': 'sliders', '\U0001F6E0': 'sliders',
    '\U0001F393': 'graduation', '\U0001F5C4': 'database', '\U0001F4F7': 'image',
    '\U0001F4F8': 'image', '\U0001F4C8': 'sliders', '\U0001F310': 'folder',
    '\u25B6': 'play', '\u23F9': 'stop', '\U0001F7E2': 'play',
    '\U0001F4E5\uFE0F': 'import', '\U0001F4E4\uFE0F': 'share',
    '\U0001F5D1\uFE0F': 'trash', '\U0001F6E0\uFE0F': 'sliders',
}
# BMP 级「符号图标」（不是 emoji，但同样当图标用）—— 也建议在新 UI 里换 SVG
BMP_ICONS = {'\u2705': 'check', '\u274C': 'close', '\u26A0': 'warning',
             '\u25B6': 'play', '\u25C0': 'play', '\u00D7': 'close',
             '\u2191': None, '\u2193': None}


def classify(line: str) -> str:
    if UI_HINT.search(line):
        return 'UI 控件文本（必须换）'
    if LOG_HINT.search(line):
        return '日志 / 纯文本（可保留）'
    return '其它（人工判断）'


def main() -> int:
    per_glyph = collections.defaultdict(lambda: collections.Counter())
    where = collections.defaultdict(list)
    kind_count = collections.Counter()
    bmp_count = collections.Counter()

    for fn in FILES:
        path = os.path.join(ROOT, fn)
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
        for i, line in enumerate(lines, 1):
            glyphs = [c for c in line if ord(c) > 0xFFFF]
            if not glyphs:
                for c in line:
                    if c in BMP_ICONS:
                        bmp_count[c] += 1
                continue
            kind = classify(line)
            kind_count[kind] += len(glyphs)
            for g in glyphs:
                per_glyph[g][fn] += 1
                if len(where[g]) < 3:
                    where[g].append('%s:%d' % (fn, i))

    total = sum(sum(c.values()) for c in per_glyph.values())
    today = datetime.date.today().isoformat()
    md = ['# 图标清点 · emoji / 符号 → SVG 替换清单', '',
          '- 生成：%s ｜ 脚本：`design/tools/emoji_inventory.py`（只读源码）' % today,
          '- 目标：**新增 UI 一律用 `design/icons.json` 里的 SVG 图标**，不再用 emoji 当图标；',
          '  存量按本表分批替换（`webui/src/components/Icon.vue` 是唯一入口）。', '',
          '## 汇总', '',
          '- 非 BMP emoji：**%d 处 / %d 种字形**' % (total, len(per_glyph)), '',
          '| 用途分类（按所在行判定） | 处数 | 处理 |', '| --- | --- | --- |']
    for k, v in kind_count.most_common():
        md.append('| %s | %d | %s |' % (k, v, '换 SVG' if '必须换' in k else '可保留 / 人工判断'))

    md += ['', '## 明细（按出现次数排序）', '',
           '| 码点 | Unicode 名 | 次数 | 建议图标 | 主要位置 |', '| --- | --- | --- | --- | --- |']
    for g, cnt in sorted(per_glyph.items(), key=lambda kv: -sum(kv[1].values())):
        name = unicodedata.name(g, '?')
        icon = EMOJI_ICON.get(g, '待定')
        md.append('| `U+%05X` | %s | %d | %s | %s |' % (
            ord(g), name, sum(cnt.values()),
            '`%s`' % icon if icon != '待定' else '待定',
            '、'.join('%s' % w for w in where[g])))

    md += ['', '## BMP 级符号图标（不是 emoji，但同样当图标用）', '',
           '| 字符 | 建议图标 | 次数 |', '| --- | --- | --- |']
    for c, n in bmp_count.most_common():
        icon = BMP_ICONS.get(c)
        if icon is None:
            continue
        md.append('| `U+%04X` | `%s` | %d |' % (ord(c), icon, n))

    md += ['', '## 用法（新代码怎么写）', '',
           '```vue',
           '<Icon name="cookie" title="凭据" />   <!-- 颜色随文字（currentColor），尺寸随字号 -->',
           '```', '',
           '- 需要新图标：往 `design/icons.json` 加一条 → 跑 `python design/tools/gen_icons.py`；',
           '- **不要**在 Vue 里直接写 `<svg>` 或再用 emoji；',
           '- 日志里的 emoji 可保留（纯文本，`_strip_emoji` 已处理富文本注释行）。']

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(md) + '\n')
    print('非 BMP emoji：%d 处 / %d 种字形' % (total, len(per_glyph)))
    for k, v in kind_count.most_common():
        print('  %-22s %d' % (k, v))
    print('已写：%s' % OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
