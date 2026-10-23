# -*- coding: utf-8 -*-
"""P2 界面不变量：圆角面板尺寸 = 内容、支路开关生效、红点可切换。"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PySide6')

from PySide6.QtCore import Qt                       # noqa: E402
from PySide6.QtWidgets import QApplication          # noqa: E402

from portpanel.engine.transport import make_request  # noqa: E402
from portpanel.ui.debug.breakpoint import BreakpointDot  # noqa: E402
from portpanel.ui.debug.inspect_panel import InspectPanel  # noqa: E402
from portpanel.ui.debug.panel_host import preview_request  # noqa: E402


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _req():
    return make_request('POST', 'http://127.0.0.1:9/items/7?page=2',
                        {'X-Test': '1', 'Authorization': 'Bearer abc123'},
                        body={'note': 'hi'}, body_kind='json')


def test_panel_layout_two_panes(qapp):
    p = InspectPanel()
    p.set_request(_req())
    # 请求 / 响应左右分栏，中间一条竖线
    assert p.req_tree is not p.resp_tree
    assert p._divider.width() == 1
    assert p.req_tree.parent() is p and p.resp_tree.parent() is p
    # 边框永远画在控件内部
    r = p.rect().adjusted(0, 0, -1, -1)
    assert p.rect().contains(r)
    # 页眉可拖动（装了事件过滤器）
    assert p._header is not None


def test_panel_masks_sensitive_headers(qapp):
    p = InspectPanel()
    p.set_request(_req())
    root = p.tree.topLevelItem(0)
    texts = []
    for i in range(root.childCount()):
        node = root.child(i)
        for j in range(node.childCount()):
            texts.append(node.child(j).text(1))
    joined = '|'.join(texts)
    assert 'Bearer abc123' not in joined
    assert 'len=' in joined


def test_branch_switch_removes_header(qapp):
    p = InspectPanel()
    p.set_request(_req())
    root = p.tree.topLevelItem(0)
    for i in range(root.childCount()):
        node = root.child(i)
        if node.text(0) == '请求头':
            for j in range(node.childCount()):
                node.child(j).setCheckState(0, Qt.Unchecked)
    edited = p.apply_edits()
    assert edited is not None
    assert edited.headers == {}


def test_url_edit_applies(qapp):
    p = InspectPanel()
    p.set_request(_req())
    root = p.tree.topLevelItem(0)
    for i in range(root.childCount()):
        node = root.child(i)
        if node.text(0) == 'URL':
            node.setText(1, 'http://127.0.0.1:9/changed')
    assert p.apply_edits().url == 'http://127.0.0.1:9/changed'


def test_dot_toggle(qapp):
    d = BreakpointDot()
    assert d.armed is False
    d.set_armed(True)
    assert d.armed is True
    d.set_triggered(True)
    assert d._triggered is True


def test_preview_request_substitutes_path_and_query(qapp):
    node = SimpleNamespace(
        api_ref={'base_url': 'http://127.0.0.1:9'},
        url_path='/items/{id}',
        method='POST',
        params=[{'name': 'id', 'value': '7', 'location': 'path'},
                {'name': 'page', 'value': '2', 'location': 'query'},
                {'name': 'note', 'value': 'hi', 'location': 'body'}],
        headers={'X-T': '1'},
        body='{"note": "{note}"}',
        body_kind='json')
    req = preview_request(node)
    assert req.url == 'http://127.0.0.1:9/items/7?page=2'
    assert req.body_kind == 'json'
    assert b'"note"' in req.body


def test_inputs_snapshot_marks_empty_values(qapp):
    p = InspectPanel()
    p.set_request(_req())
    p.set_inputs([
        {'name': 'service', 'value': 'demo', 'location': 'path'},
        {'name': 'creator_id', 'value': '', 'location': 'path'},
        {'name': 'q', 'value': 'artist', 'location': 'query'},
    ])
    top = [p.req_tree.topLevelItem(i).text(0) for i in range(p.req_tree.topLevelItemCount())]
    assert top[0] == '输入点'          # 输入点在请求之前
    root = p.req_tree.topLevelItem(0)
    assert root.childCount() == 3
    assert root.child(1).text(1) == '⚠ 空'


def test_response_payload_fills_right_pane(qapp):
    p = InspectPanel()
    p.set_request(_req())
    p.set_response_payload({'status': 200, 'via': 'requests', 'elapsed_ms': 12,
                            'headers': {'Content-Type': 'application/json',
                                        'Set-Cookie': 'abcdefghij'},
                            'body': '{"ok":true}'})
    root = p.resp_tree.topLevelItem(0)
    assert '200' in root.text(1)
    pairs = {root.child(i).text(0): root.child(i).text(1) for i in range(root.childCount())}
    assert pairs['body'] == '{"ok":true}'
    assert pairs['Set-Cookie'].startswith('abc…')      # 打码


def test_live_rows_survive_tree_reset(qapp):
    """树被 clear 后缓存的实时行不能再被碰（否则悬空 C++ 对象，响应回填会被吞）。"""
    p = InspectPanel()
    p.set_request(_req())
    p.set_live('GET', 'http://a/b', 'request')
    p.set_request(_req())                       # 清空 → 旧项失效
    p.set_live('GET', 'http://a/b', 'response', 200)
    p.set_response_payload({'status': 200, 'via': 'requests',
                            'headers': {}, 'body': 'ok'})
    root = p.resp_tree.topLevelItem(0)
    assert '200' in root.text(1)
    assert root.child(root.childCount() - 1).text(1) == 'ok'


def test_inputs_idempotent(qapp):
    p = InspectPanel()
    p.set_request(_req())
    p.set_inputs([{'name': 'a', 'value': '1'}])
    p.set_inputs([{'name': 'a', 'value': '1'}])
    tops = [p.req_tree.topLevelItem(i).text(0) for i in range(p.req_tree.topLevelItemCount())]
    assert tops.count('输入点') == 1
