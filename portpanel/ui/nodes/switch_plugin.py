# -*- coding: utf-8 -*-
"""插件：SWITCH（逻辑开关）。

闸门节点：关闭后，其下游整条支路本轮直接跳过。
本身不产生数据（`signal`），只决定“放不放行”。
"""
from __future__ import annotations

from .spec import DataKind, NodeSpec, PortSpec, Protocol, REGISTRY


def _factory(ndata: dict):
    from portpanel.ui.switch_node import SwitchNode
    return SwitchNode.from_dict(ndata)


def _serialize(node) -> dict:
    return node.to_dict()


SPEC = REGISTRY.register(NodeSpec(
    type_name='SwitchNode',
    title='逻辑开关',
    protocol=Protocol.VIRTUAL,
    inputs=[PortSpec('in', DataKind.SIGNAL, '上游数据（也可不接）')],
    outputs=[PortSpec('out', DataKind.SIGNAL, '放行时把信号继续往下传')],
    factory=_factory,
    to_dict=_serialize,
    doc='关闭时下游整条支路本轮跳过；不改数据，只做通断。用于临时停掉限流/调试某条链。',
))
