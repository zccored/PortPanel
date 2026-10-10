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
  2. 把全局调色板也设成深色（`dark_theme`）—— 主程序里这一步由 MainWindow 做，
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

from PySide6.QtWidgets import QApplication                     # noqa: E402


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
