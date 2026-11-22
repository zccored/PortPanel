# -*- coding: utf-8 -*-
"""插件：站点解析（SiteParserNode）。

把一个页面按规则解析成条目数组（列表页/详情页通用）。
"""
from __future__ import annotations

from .spec import DataKind, NodeSpec, PortSpec, Protocol, REGISTRY


def _factory(ndata: dict):
    from portpanel.ui import flow_editor as _fe
    cls = getattr(_fe, 'SiteParserNode')
    if hasattr(cls, 'from_dict'):
        return cls.from_dict(ndata)
    return cls()


def _serialize(node) -> dict:
    fn = getattr(node, 'to_dict', None)
    return fn() if fn else {}


SPEC = REGISTRY.register(NodeSpec(
    type_name='SiteParserNode',
    title='站点解析',
    protocol=Protocol.HTTP,
    inputs=[PortSpec('in', DataKind.VALUE, '目标 URL（可由上游拼接）')],
    outputs=[PortSpec('out', DataKind.ARRAY, '解析出的条目数组')],
    # 仅登记元信息：创建仍走 flow_editor 原来那条分支，避免重建时丢掉参数控件/连线
    factory=None,
    to_dict=None,
    doc=('把一个页面按规则解析成条目数组（列表页/详情页通用）。'),
))
