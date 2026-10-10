# -*- coding: utf-8 -*-
# 本文件由 design/tools/gen_tokens.py 生成 —— 不要手工改。
#
# 用途：Qt 侧（portpanel/integration/theme.py）的**候选**常量。
# ⚠️ 尚未接线：把 theme.py 里的字面量换成这些常量属于一次真实重构，
#    要单独评估并跑一遍界面回归后再做（本仓库的 theme.py 是自有模块）。
#    当前只保证「同一份 token 能同时产出 Qt 与 Web 两边的值」。

COLORS = {
    'bg0':       '#16181d',
    'bg1':       '#1c1f26',
    'bg2':       '#22262f',
    'bg3':       '#2a2f3a',
    'fg0':       '#e8ecf3',
    'fg1':       '#aab4c4',
    'fg2':       '#7c8798',
    'line':      '#333a47',
    'line-soft': '#262c36',
    'accent':    '#4c8dff',
    'accent-fg': '#ffffff',
    'accent-soft': 'rgba(76, 141, 255, 0.16)',
    'ok':        '#3ecf8e',
    'warn':      '#e8b53c',
    'danger':    '#ef5f5f',
}

SPACE = {
    '1':         '4px',
    '2':         '8px',
    '3':         '12px',
    '4':         '16px',
    '5':         '24px',
    '6':         '32px',
}

RADIUS = {
    '1':         '4px',
    '2':         '6px',
    '3':         '10px',
}

FONT_SIZE = {
    '1':         '11px',
    '2':         '12px',
    '3':         '13px',
    '4':         '15px',
    '5':         '18px',
}

FONT_UI = "'Segoe UI', 'Microsoft YaHei UI', 'Microsoft YaHei', system-ui, sans-serif"
FONT_MONO = "Consolas, 'Courier New', monospace"

TOKENS_VERSION = 1
