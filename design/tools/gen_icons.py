# -*- coding: utf-8 -*-
"""图标生成器：`design/icons.json` → 每个图标一个 SVG + 一个 TS 索引。

用法（仓库根目录）：
    python design/tools/gen_icons.py            # 生成
    python design/tools/gen_icons.py --check    # 只检查漂移（退出码 1 = 有漂移）

产物：
    webui/src/assets/icons/<name>.svg    ← 单色描边、currentColor、随字号缩放（1em）
    webui/src/assets/icons/index.ts      ← `import x from './x.svg?raw'` 形式的索引

为什么生成而不是手写：图标几何要单点维护（改一次线宽/尺寸只改这里），
且新增图标只加一行 JSON —— 避免"19 个 SVG 各写一套 stroke-width"。
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DESIGN = os.path.join(ROOT, 'design')
ICONS_JSON = os.path.join(DESIGN, 'icons.json')
OUT_DIR = os.path.join(ROOT, 'webui', 'src', 'assets', 'icons')

SVG_TMPL = (
    '<!-- 由 design/tools/gen_icons.py 从 design/icons.json 生成 —— 不要手工改 -->\n'
    '<svg class="icon" xmlns="http://www.w3.org/2000/svg" viewBox="{viewBox}"\n'
    '     width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="1.6"\n'
    '     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">\n'
    '  {body}\n'
    '</svg>\n'
)


def render_all() -> dict:
    with open(ICONS_JSON, encoding='utf-8') as f:
        data = json.load(f)
    view_box = data.get('viewBox', '0 0 24 24')
    out = {}
    for name, spec in data['icons'].items():
        out['%s.svg' % name] = SVG_TMPL.format(viewBox=view_box, body=spec['body'].strip())
    names = sorted(data['icons'])
    ts = ['// 由 design/tools/gen_icons.py 从 design/icons.json 生成 —— 不要手工改', '']
    # 注意：图标名可能是 JS 保留字（import / stop / delete…）→ 一律用 icon_<name> 别名 + 引号键
    for n in names:
        ts.append("import icon_%s from './%s.svg?raw'" % (n, n))
    ts += ['', 'export const ICONS: Record<string, string> = {']
    for n in names:
        ts.append("  '%s': icon_%s," % (n, n))
    ts += ['}', '', 'export type IconName = string', '']
    out['index.ts'] = '\n'.join(ts)
    return out


def main() -> int:
    check = '--check' in sys.argv
    files = render_all()
    drift = []
    for fname, content in files.items():
        path = os.path.join(OUT_DIR, fname)
        if not os.path.exists(path):
            drift.append(fname)
            continue
        with open(path, encoding='utf-8') as f:
            if f.read().replace('\r\n', '\n') != content.replace('\r\n', '\n'):
                drift.append(fname)

    if check:
        if drift:
            print('ICONS_DRIFT: %s' % '、'.join(drift))
            return 1
        print('ICONS_OK（%d 个图标与 design/icons.json 一致）' % (len(files) - 1))
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    for fname, content in files.items():
        with open(os.path.join(OUT_DIR, fname), 'w', encoding='utf-8', newline='\n') as f:
            f.write(content)
    print('已生成 %d 个图标 + index.ts → %s' % (len(files) - 1, OUT_DIR))
    if drift:
        print('（本次修正的漂移项：%s）' % '、'.join(drift))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
