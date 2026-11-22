# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# 端口画板（PortPanel）· 独立启动入口
# Copyright (C) 2026 zccored
#
# 本程序是自由软件：你可以再发布和/或修改它，但必须遵守 GNU Affero 通用公共
# 许可证 v3.0（AGPL-3.0-only）的条款；本程序不提供任何担保。完整条款见根目录 LICENSE。
# This program is free software under the GNU Affero General Public License
# v3.0 (AGPL-3.0-only), WITHOUT ANY WARRANTY. See the LICENSE file for terms.
# ---------------------------------------------------------------------------

"""端口画板 · 独立启动入口。

「端口画板」原本是主程序（全栈图库管理器）里的一个窗口 —— 即「平台整合器」，
2026-10-06 起正式更名并**支持独立运行/独立分发**。本文件就是那个独立入口：

    python port_panel.py            # 源码运行
    端口画板.exe                     # 打包后双击（PyInstaller 的入口也是本文件）

它做的事情很少，一眼能看完：
  1. 建 QApplication；
  2. 把全局调色板也设成深色（`portpanel.integration.theme`）—— 主程序里这一步由 MainWindow 做，
     单独跑时没人做，不设的话在浅色 Windows 上会是一片白；
  3. 开 `FlowEditorDialog`（就是端口画板本体）并进入事件循环。

**为什么不要 `-E` 之外的花样**：这个入口要同时满足三种跑法（源码 / 打包 exe / 被
image-search 的「切换启动」拉起），所以刻意不做任何参数解析与环境探测，
需要换目录时用工作目录或 `IMG_PEER_DIR` 那一侧去处理。
"""
from __future__ import annotations

import os
import sys

# 打包后 sys.path 里没有源码目录，这里兜一个（源码运行时也顺带保证能 import 同级模块）
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ---- 压掉 Qt 的字体探测噪音 ----
# 现象：控制台刷 "qt.text.font.db: OpenType support missing for \"Consolas\", script 19"
# 原因：Qt 枚举系统字体时会按「书写系统(script)」探测 OpenType 表，很多中英字体没有
#       对应表，Qt 就把警告打到 stderr。它不影响功能，但会盖住真正的日志。
# 做法：① 关掉 qt.text.font.db 这个日志类别；② 再兜一层消息处理器，按文本丢弃同款警告。
_qt_rules = os.environ.get('QT_LOGGING_RULES', '')
if 'qt.text.font.db' not in _qt_rules:
    os.environ['QT_LOGGING_RULES'] = (
        (_qt_rules + ';') if _qt_rules else '') + 'qt.text.font.db=false'

from PySide6.QtWidgets import QApplication                     # noqa: E402

from PySide6.QtCore import qInstallMessageHandler            # noqa: E402


def _qt_message_handler(mode, context, message):
    """只放行有用的 Qt 日志；字体探测类整类丢弃。

    Qt 在枚举系统字体时会按「书写系统(script)」探测 OpenType 表，
    中英字体大多没有对应表，于是 stderr 被
        qt.text.font.db: OpenType support missing for "Consolas", script 19
    刷屏。这类信息对排查问题零价值，直接丢掉；其余日志原样输出。
    """
    try:
        category = getattr(context, 'category', '') or ''
    except Exception:
        category = ''
    if category == 'qt.text.font.db' or 'OpenType support missing' in message:
        return
    if category and category != 'default':
        print('%s: %s' % (category, message), file=sys.stderr)
    else:
        print(message, file=sys.stderr)
    sys.stderr.flush()


qInstallMessageHandler(_qt_message_handler)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("端口画板")
    app.setApplicationDisplayName("端口画板")

    # 全局深色：单独运行时没有 MainWindow 帮忙设，这里补上；
    # FlowEditorDialog 自己还会再套一份（防止被当子窗口用时继承到浅色）。
    try:
        from portpanel.integration.theme import build_dark_palette
        app.setPalette(build_dark_palette())
    except Exception:
        pass

    from portpanel.ui.flow_editor import FlowEditorDialog
    dlg = FlowEditorDialog()
    dlg.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
