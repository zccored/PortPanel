# -*- coding: utf-8 -*-
"""插件：数据处理框（DataProcessNode）。

纯计算节点：按 JSONPath（支持 [*] 通配）从上游响应取值，可选正则/替换二次加工。key_by 让“单值”升级成“按 key 分桶的字典”，用于每用户独立翻页游标。
"""
from __future__ import annotations

from .spec import DataKind, NodeSpec, PortSpec, Protocol, REGISTRY


def _factory(ndata: dict):
    from portpanel.ui import flow_editor as _fe
    cls = getattr(_fe, 'DataProcessNode')
    if hasattr(cls, 'from_dict'):
        return cls.from_dict(ndata)
    return cls()


def _serialize(node) -> dict:
    fn = getattr(node, 'to_dict', None)
    return fn() if fn else {}


SPEC = REGISTRY.register(NodeSpec(
    type_name='DataProcessNode',
    title='数据处理框',
    protocol=Protocol.VIRTUAL,
    inputs=[PortSpec('in', DataKind.ARRAY, '上游响应 JSON（可多条）', multi=True)],
    outputs=[PortSpec('out', DataKind.ARRAY, '按 JSONPath 抽出的值；带 key_by 时按 key 分桶')],
    # 仅登记元信息：创建仍走 flow_editor 原来那条分支，避免重建时丢掉参数控件/连线
    factory=None,
    to_dict=None,
    doc=('纯计算节点：按 JSONPath（支持 [*] 通配）从上游响应取值，可选正则/替换二次加工。key_by 让“单值”升级成“按 key 分桶的字典”，用于每用户独立翻页游标。'),
))
