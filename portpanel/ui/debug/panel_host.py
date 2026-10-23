# -*- coding: utf-8 -*-
"""把节点 → 预览请求 → 断点面板 串起来（UI 侧，不碰引擎）。"""
from __future__ import annotations

from PySide6.QtCore import QPoint

from portpanel.core.request_builder import build_request_from_url
from portpanel.ui.debug.inspect_panel import InspectPanel

__all__ = ['preview_request', 'open_for_node', 'get_panel', 'reset_panel']

_panel: InspectPanel | None = None


def preview_request(node):
    """按节点当前配置构建一条「将要发出」的请求（不改任何状态）。"""
    from portpanel.core.params import build_url, substitute_path

    values = {p.get('name', ''): p.get('value', '') for p in (node.params or [])}
    base = (node.api_ref or {}).get('base_url', '') or ''
    path_values = {p.get('name', ''): p.get('value', '')
                   for p in (node.params or [])
                   if (p.get('location') or 'path') == 'path'}
    path = substitute_path(getattr(node, 'url_path', '') or '', path_values)
    url = build_url(base, path)
    return build_request_from_url(
        url=url,
        method=getattr(node, 'method', 'GET') or 'GET',
        params=getattr(node, 'params', None), values=values,
        headers=getattr(node, 'headers', None) or {},
        body_template=getattr(node, 'body', '') or '',
        body_kind=getattr(node, 'body_kind', 'none') or 'none')


def get_panel() -> InspectPanel | None:
    return _panel


def reset_panel():
    global _panel
    _panel = None


def _view_of(node):
    try:
        views = node.scene().views()
        return views[0] if views else None
    except Exception:
        return None


def inputs_for_node(node):
    """元素框**入口各输入点**的实时值（断点时核对上游有没有把数据送到）。"""
    items = []
    try:
        for p in (getattr(node, 'params', None) or []):
            if not isinstance(p, dict):
                continue
            items.append({
                'name': p.get('name', ''),
                'value': p.get('value', ''),
                'location': p.get('location', 'path'),
                'source': p.get('source_path') or p.get('source') or '',
            })
    except Exception:
        pass
    try:
        for name, arr in (getattr(node, '_array_values', None) or {}).items():
            if arr:
                items.append({'name': '%s[]' % name,
                              'value': ', '.join(str(x) for x in arr)[:200],
                              'location': 'array'})
    except Exception:
        pass
    try:
        for cw in (getattr(node, '_concat_widgets', None) or []):
            items.append({'name': '拼合:%s' % getattr(cw, 'concat_id', ''),
                          'value': cw.get_value() if hasattr(cw, 'get_value') else '',
                          'location': 'concat'})
    except Exception:
        pass
    return items


def open_for_node(node, pos=None, view=None) -> InspectPanel:
    """打开/更新面板：居中于画板、盖虚化遮罩、常驻不抢焦点。"""
    global _panel
    view = view or _view_of(node)
    win = view.window() if view is not None else None
    if _panel is None:
        _panel = InspectPanel(win)
    elif win is not None and _panel.parent() is not win:
        _panel.setParent(win)

    try:
        _panel.title.setText('请求断点 · %s %s' % (
            getattr(node, 'method', 'GET'), getattr(node, 'url_path', '')))
        _panel.set_request(preview_request(node))
        _panel.set_inputs(inputs_for_node(node))
    except Exception as exc:
        _panel.title.setText('请求断点（预览失败：%s）' % exc)

    try:
        from portpanel.ui.debug.bridge import get_bridge
        _panel.set_armed(get_bridge().is_armed(node))
    except Exception:
        pass

    if view is not None:
        _panel.attach_veil(view)
        if pos is None:
            try:
                c = view.viewport().mapToGlobal(view.viewport().rect().center())
                _panel.adjustSize()
                pos = QPoint(c.x() - _panel.width() // 2,
                             c.y() - _panel.height() // 2)
            except Exception:
                pos = None

    if pos is not None:
        try:
            _panel.move(pos)
        except Exception:
            pass
    _panel.show()
    _panel.raise_()
    return _panel
