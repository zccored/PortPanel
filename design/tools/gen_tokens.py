# -*- coding: utf-8 -*-
"""设计 token 生成器：`design/tokens.json` → CSS 变量 + Qt 侧常量。

用法（仓库根目录）：
    python design/tools/gen_tokens.py            # 生成
    python design/tools/gen_tokens.py --check    # 只检查是否漂移（退出码 1 = 有漂移）

产物：
    webui/src/styles/tokens.css          ← Web 侧（Vue/Vite 直接 import）
    design/generated/qt_tokens.py        ← Qt 侧常量（**尚未接线**；接进
                                            portpanel/integration/theme.py 属后续动作，要单独评估）

零副作用原则：只写上面两个产物；`--check` 模式完全不写。
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DESIGN = os.path.join(ROOT, 'design')
TOKENS_JSON = os.path.join(DESIGN, 'tokens.json')
CSS_OUT = os.path.join(ROOT, 'webui', 'src', 'styles', 'tokens.css')
QT_OUT = os.path.join(DESIGN, 'generated', 'qt_tokens.py')


def load_tokens() -> dict:
    with open(TOKENS_JSON, encoding='utf-8') as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith('$')}


def camel_to_kebab(name: str) -> str:
    """lineSoft → line-soft；bg0 → bg0（数字结尾不拆）。"""
    out = []
    for ch in name:
        if ch.isupper():
            out.append('-')
            out.append(ch.lower())
        else:
            out.append(ch)
    return ''.join(out)


def css_var_name(group: str, key: str) -> str:
    prefix = {'color': '', 'space': 'sp-', 'radius': 'r-', 'fontSize': 'fs-',
              'shadow': 'sh-', 'font': 'font-'}[group]
    k = camel_to_kebab(key)
    if group == 'color':
        # bg0 → --bg-0；fg1 → --fg-1；其余（accent / line-soft / ok…）保持原样
        for p in ('bg', 'fg'):
            if k.startswith(p) and len(k) > len(p):
                return '--%s-%s' % (p, k[len(p):])
        return '--%s' % k
    if group == 'font':
        return '--font-%s' % k
    return '--%s%s' % (prefix, k)


def render_css(tokens: dict) -> str:
    lines = [
        '/* 本文件由 design/tools/gen_tokens.py 生成 —— 不要手工改。',
        '   改 token 请改 design/tokens.json，然后重跑生成器。 */',
        ':root {',
        '  color-scheme: dark;',
        '',
    ]
    for group in ('color', 'space', 'radius', 'fontSize', 'shadow', 'font'):
        if group not in tokens:
            continue
        lines.append('  /* %s */' % group)
        for key, value in tokens[group].items():
            lines.append('  %s: %s;' % (css_var_name(group, key), value))
        lines.append('')
    lines.append('}')
    lines.append('')
    return '\n'.join(lines)


def render_qt(tokens: dict) -> str:
    lines = [
        '# -*- coding: utf-8 -*-',
        '# 本文件由 design/tools/gen_tokens.py 生成 —— 不要手工改。',
        '#',
        '# 用途：Qt 侧（portpanel/integration/theme.py）的**候选**常量。',
        '# ⚠️ 尚未接线：把 theme.py 里的字面量换成这些常量属于一次真实重构，',
        '#    要单独评估并跑一遍界面回归后再做（本仓库的 theme.py 是自有模块）。',
        '#    当前只保证「同一份 token 能同时产出 Qt 与 Web 两边的值」。',
        '',
        'COLORS = {',
    ]
    for key, value in tokens['color'].items():
        lines.append('    %-12s %r,' % ("'%s':" % camel_to_kebab(key), value))
    lines.append('}')
    lines.append('')
    lines.append('SPACE = {')
    for key, value in tokens['space'].items():
        lines.append('    %-12s %r,' % ("'%s':" % camel_to_kebab(key), value))
    lines.append('}')
    lines.append('')
    lines.append('RADIUS = {')
    for key, value in tokens['radius'].items():
        lines.append('    %-12s %r,' % ("'%s':" % camel_to_kebab(key), value))
    lines.append('}')
    lines.append('')
    lines.append('FONT_SIZE = {')
    for key, value in tokens['fontSize'].items():
        lines.append('    %-12s %r,' % ("'%s':" % camel_to_kebab(key), value))
    lines.append('}')
    lines.append('')
    lines.append('FONT_UI = %r' % tokens['font']['ui'])
    lines.append('FONT_MONO = %r' % tokens['font']['mono'])
    lines.append('')
    lines.append('TOKENS_VERSION = 1')
    lines.append('')
    return '\n'.join(lines)


def main() -> int:
    check = '--check' in sys.argv
    tokens = load_tokens()
    css = render_css(tokens)
    qt = render_qt(tokens)

    drift = []
    for path, content, label in ((CSS_OUT, css, 'webui/src/styles/tokens.css'),
                                 (QT_OUT, qt, 'design/generated/qt_tokens.py')):
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                cur = f.read()
            if cur.replace('\r\n', '\n') != content.replace('\r\n', '\n'):
                drift.append(label)
        else:
            drift.append('%s（缺失）' % label)

    if check:
        if drift:
            print('TOKENS_DRIFT: %s' % '、'.join(drift))
            print('→ 跑 python design/tools/gen_tokens.py 重新生成')
            return 1
        print('TOKENS_OK（CSS 与 Qt 侧产物都与 design/tokens.json 一致）')
        return 0

    os.makedirs(os.path.dirname(QT_OUT), exist_ok=True)
    with open(CSS_OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(css)
    with open(QT_OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(qt)
    print('已生成：')
    print('  %s' % CSS_OUT)
    print('  %s' % QT_OUT)
    if drift:
        print('（本次修正的漂移项：%s）' % '、'.join(drift))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
