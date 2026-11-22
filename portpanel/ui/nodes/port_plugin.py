# -*- coding: utf-8 -*-
"""插件：PORT（工业端口节点）。

一个节点同时支持三种协议（Modbus/TCP、PROFINET RT 隧道帧、WebSocket），
交换机式多输入多输出：有多少条就在框里画多少条输入点，
输出目标是 `host:port` 列表，可配置多个。
"""
from __future__ import annotations

from .spec import DataKind, NodeSpec, PortSpec, Protocol, REGISTRY

_PROTO = {
    'modbus': Protocol.MODBUS,
    'profinet': Protocol.PROFINET,
    'websocket': Protocol.WEBSOCKET,
    'raw': Protocol.LOCAL,
}


def _factory(ndata: dict):
    from portpanel.ui.port_node import PortNode
    node = PortNode.from_dict(ndata)
    return node


def _serialize(node) -> dict:
    return node.to_dict()


SPEC = REGISTRY.register(NodeSpec(
    type_name='PortNode',
    title='端口节点',
    protocol=Protocol.MODBUS,          # 实际协议由节点自身 proto 字段决定
    inputs=[PortSpec('in', DataKind.ARRAY, '任意路输入；按条数在框里画输入点', multi=True)],
    outputs=[PortSpec('out', DataKind.ARRAY, '按 target 列表转发到多个 host:port', multi=True)],
    factory=_factory,
    to_dict=_serialize,
    doc=('工业链路收发信节点：Modbus/TCP（MBAP+PDU，FC1/3/4/5/6/15/16）、'
         'PROFINET RT 隧道帧、WebSocket（RFC6455）三选一；'
         '支持多输入多输出、消息池、断点只收不发、放行后与下游 POST 做心跳同步。'),
))
