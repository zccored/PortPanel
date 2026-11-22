# -*- coding: utf-8 -*-
"""插件：接口元素框（APINode）。

一次 HTTP 请求。方法决定终端语义：POST/PUT/PATCH 视为终端发信节点（不生成输出点）；body 形态见 BodyKind（none/json/form/raw）；参数落点见 ParamLocation。输入点按参数动态生成，一个参数一条线；输出为响应 JSON / 文件流。
"""
from __future__ import annotations

from .spec import DataKind, NodeSpec, PortSpec, Protocol, REGISTRY


def _factory(ndata: dict):
    from portpanel.ui import flow_editor as _fe
    cls = getattr(_fe, 'APINode')
    if hasattr(cls, 'from_dict'):
        return cls.from_dict(ndata)
    return cls()


def _serialize(node) -> dict:
    fn = getattr(node, 'to_dict', None)
    return fn() if fn else {}


SPEC = REGISTRY.register(NodeSpec(
    type_name='APINode',
    title='接口元素框',
    protocol=Protocol.HTTP,
    inputs=[PortSpec('params', DataKind.ARRAY, '每个参数一个输入点（path/query/header/body 落点由参数自己声明）', multi=True)],
    outputs=[],
    # 仅登记元信息：创建仍走 flow_editor 原来那条分支，避免重建时丢掉参数控件/连线
    factory=None,
    to_dict=None,
    doc=('一次 HTTP 请求。方法决定终端语义：POST/PUT/PATCH 视为终端发信节点（不生成输出点）；body 形态见 BodyKind（none/json/form/raw）；参数落点见 ParamLocation。输入点按参数动态生成，一个参数一条线；输出为响应 JSON / 文件流。'),
))
