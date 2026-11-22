# -*- coding: utf-8 -*-
"""插件：步进器（StepperNode）。

轮次/页码发生器：每轮按 step_len 自增，可写公式（可用 {步数} / {titleN}）。输出是整数语义时会自动去掉 .0，避免 pn=1.0 这类参数被服务器判错。
"""
from __future__ import annotations

from .spec import DataKind, NodeSpec, PortSpec, Protocol, REGISTRY


def _factory(ndata: dict):
    from portpanel.ui import flow_editor as _fe
    cls = getattr(_fe, 'StepperNode')
    if hasattr(cls, 'from_dict'):
        return cls.from_dict(ndata)
    return cls()


def _serialize(node) -> dict:
    fn = getattr(node, 'to_dict', None)
    return fn() if fn else {}


SPEC = REGISTRY.register(NodeSpec(
    type_name='StepperNode',
    title='步进器',
    protocol=Protocol.VIRTUAL,
    inputs=[PortSpec('in', DataKind.VALUE, '输入值（用于公式/自增起点）', multi=True)],
    outputs=[PortSpec('out', DataKind.VALUE, '本轮输出值（整数浮点自动去掉 .0）')],
    # 仅登记元信息：创建仍走 flow_editor 原来那条分支，避免重建时丢掉参数控件/连线
    factory=None,
    to_dict=None,
    doc=('轮次/页码发生器：每轮按 step_len 自增，可写公式（可用 {步数} / {titleN}）。输出是整数语义时会自动去掉 .0，避免 pn=1.0 这类参数被服务器判错。'),
))
