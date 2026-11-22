# -*- coding: utf-8 -*-
"""插件：图片框（ImageNode）。

画板上的图片装饰/参考图，不参与执行。
"""
from __future__ import annotations

from .spec import DataKind, NodeSpec, PortSpec, Protocol, REGISTRY


def _factory(ndata: dict):
    from portpanel.ui import flow_editor as _fe
    cls = getattr(_fe, 'ImageNode')
    if hasattr(cls, 'from_dict'):
        return cls.from_dict(ndata)
    return cls()


def _serialize(node) -> dict:
    fn = getattr(node, 'to_dict', None)
    return fn() if fn else {}


SPEC = REGISTRY.register(NodeSpec(
    type_name='ImageNode',
    title='图片框',
    protocol=Protocol.VIRTUAL,
    inputs=[],
    outputs=[],
    # 仅登记元信息：创建仍走 flow_editor 原来那条分支，避免重建时丢掉参数控件/连线
    factory=None,
    to_dict=None,
    doc=('画板上的图片装饰/参考图，不参与执行。'),
))
