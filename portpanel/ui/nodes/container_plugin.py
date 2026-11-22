# -*- coding: utf-8 -*-
"""插件：容器框（ContainerNode）。

落盘终点：主目录 storage_path → 根目录 dyn_root（分组）→ 规则目录（标题/日期）。rule_enabled 打开后按 creation_path/fallback_path/时间生成子目录；只删空目录不动有内容的目录。
"""
from __future__ import annotations

from .spec import DataKind, NodeSpec, PortSpec, Protocol, REGISTRY


def _factory(ndata: dict):
    from portpanel.ui import flow_editor as _fe
    cls = getattr(_fe, 'ContainerNode')
    if hasattr(cls, 'from_dict'):
        return cls.from_dict(ndata)
    return cls()


def _serialize(node) -> dict:
    fn = getattr(node, 'to_dict', None)
    return fn() if fn else {}


SPEC = REGISTRY.register(NodeSpec(
    type_name='ContainerNode',
    title='容器框',
    protocol=Protocol.VIRTUAL,
    inputs=[PortSpec('file', DataKind.FILE, '待存盘的文件流', multi=True),
         PortSpec('root', DataKind.VALUE, '根目录名（分组/分组），可接多条，按顺序取第一个能用的')],
    outputs=[],
    # 仅登记元信息：创建仍走 flow_editor 原来那条分支，避免重建时丢掉参数控件/连线
    factory=None,
    to_dict=None,
    doc=('落盘终点：主目录 storage_path → 根目录 dyn_root（分组）→ 规则目录（标题/日期）。rule_enabled 打开后按 creation_path/fallback_path/时间生成子目录；只删空目录不动有内容的目录。'),
))
