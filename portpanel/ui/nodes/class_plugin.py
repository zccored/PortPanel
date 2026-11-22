# -*- coding: utf-8 -*-
"""插件：类框（ClassNode）。

把若干节点圈成一组：main 类由引擎直接执行，非 main 类只有被上游触发才执行。组内可设置固定轮次或自增轮询。
"""
from __future__ import annotations

from .spec import DataKind, NodeSpec, PortSpec, Protocol, REGISTRY


def _factory(ndata: dict):
    from portpanel.ui import flow_editor as _fe
    cls = getattr(_fe, 'ClassNode')
    if hasattr(cls, 'from_dict'):
        return cls.from_dict(ndata)
    return cls()


def _serialize(node) -> dict:
    fn = getattr(node, 'to_dict', None)
    return fn() if fn else {}


SPEC = REGISTRY.register(NodeSpec(
    type_name='ClassNode',
    title='类框',
    protocol=Protocol.VIRTUAL,
    inputs=[],
    outputs=[],
    # 仅登记元信息：创建仍走 flow_editor 原来那条分支，避免重建时丢掉参数控件/连线
    factory=None,
    to_dict=None,
    doc=('把若干节点圈成一组：main 类由引擎直接执行，非 main 类只有被上游触发才执行。组内可设置固定轮次或自增轮询。'),
))
