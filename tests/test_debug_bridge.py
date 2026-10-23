# -*- coding: utf-8 -*-
"""断点总线：worker 阻塞 → 主线程放行；红点颜色/同步；面板层级与勾选样式。"""
from __future__ import annotations

import os
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PySide6')

from PySide6.QtCore import Qt                       # noqa: E402
from PySide6.QtWidgets import QApplication          # noqa: E402

from portpanel.engine.intercept import DebugDecision  # noqa: E402
from portpanel.engine.transport import make_request   # noqa: E402
from portpanel.ui.debug.breakpoint import BreakpointDot  # noqa: E402
from portpanel.ui.debug.bridge import DebugBridge     # noqa: E402
from portpanel.ui.debug.inspect_panel import InspectPanel  # noqa: E402


@pytest.fixture(scope='module')
def qapp():
    yield QApplication.instance() or QApplication([])


def _req():
    return make_request('GET', 'http://127.0.0.1:9/x')


def test_bridge_blocks_until_submit(qapp):
    b = DebugBridge()
    node = object()
    out = {}

    def worker():
        out['decision'] = b.make_decider(node)(_req())

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    deadline = time.time() + 2
    while not b.is_paused() and time.time() < deadline:
        time.sleep(0.01)
    assert b.is_paused(), '断点没有阻塞住 worker'

    b.submit(DebugDecision(action='release'))
    th.join(2)
    assert not th.is_alive()
    assert out['decision'].action == 'release'
    assert not b.is_paused()


def test_bridge_armed_syncs_dot(qapp):
    b = DebugBridge()

    class N:
        breakpoint_dot = BreakpointDot()

    n = N()
    b.set_armed(n, True)
    assert b.is_armed(n) and n.breakpoint_dot.armed
    b.set_armed(n, False)
    assert not b.is_armed(n) and not n.breakpoint_dot.armed


def test_dot_colors_gray_red(qapp):
    d = BreakpointDot()
    off = d.styleSheet()
    assert 'transparent' in off and '#8b98a8' in off      # 不触发：灰色空心
    d.set_armed(True)
    on = d.styleSheet()
    assert '#e05252' in on and 'transparent' not in on    # 触发：实心红
    d.set_triggered(True)
    assert '#ffb3b3' in d.styleSheet()


def test_panel_not_always_on_top(qapp):
    p = InspectPanel()
    assert not (int(p.windowFlags()) & int(Qt.WindowStaysOnTopHint))
    assert int(p.windowFlags()) & int(Qt.Tool)


def test_panel_checkbox_is_blue_square(qapp):
    p = InspectPanel()
    qss = p.tree.styleSheet()
    assert '#2f81f7' in qss and 'indicator:checked' in qss
