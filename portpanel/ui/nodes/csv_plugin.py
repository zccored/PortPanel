# -*- coding: utf-8 -*-
"""插件：数据库框（CsvDataNode）。

CSV 数据库：ingest_mode=upsert 时按主键去重；也可当作“外层循环数据行”，每轮自动推进一行。主键列用于去重与回写。
"""
from __future__ import annotations

from .spec import DataKind, NodeSpec, PortSpec, Protocol, REGISTRY


def _factory(ndata: dict):
    from portpanel.ui import flow_editor as _fe
    cls = getattr(_fe, 'CsvDataNode')
    if hasattr(cls, 'from_dict'):
        return cls.from_dict(ndata)
    return cls()


def _serialize(node) -> dict:
    fn = getattr(node, 'to_dict', None)
    return fn() if fn else {}


SPEC = REGISTRY.register(NodeSpec(
    type_name='CsvDataNode',
    title='数据库框',
    protocol=Protocol.VIRTUAL,
    inputs=[PortSpec('in', DataKind.ARRAY, '上游数组，逐行写入/按主键 upsert', multi=True)],
    outputs=[PortSpec('out', DataKind.ARRAY, '当前行（外层循环用）')],
    # 仅登记元信息：创建仍走 flow_editor 原来那条分支，避免重建时丢掉参数控件/连线
    factory=None,
    to_dict=None,
    doc=('CSV 数据库：ingest_mode=upsert 时按主键去重；也可当作“外层循环数据行”，每轮自动推进一行。主键列用于去重与回写。'),
))
