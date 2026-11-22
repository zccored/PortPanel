# -*- coding: utf-8 -*-
"""开关节点：序列化往返 + 上游阻断判定。"""
from __future__ import annotations

import pytest

pytest.importorskip('PySide6')


class _Conn:
    def __init__(self, a, b, end_param=''):
        self.start_obj, self.end_obj = a, b
        self.end_param = end_param


def test_switch_roundtrip():
    from portpanel.ui.switch_node import SwitchNode
    n = SwitchNode(label='限流', enabled=False, mode='gate')
    d = n.to_dict()
    assert d['type'] == 'SwitchNode' and d['enabled'] is False
    n2 = SwitchNode.from_dict(d)
    assert n2.label == '限流' and n2.enabled is False


def test_blocked_by_switch_direct_and_indirect():
    # 旧闸门语义（mode='gate'）：enabled=False 阻断、True 放行
    from portpanel.ui.switch_node import SwitchNode, blocked_by_switch
    sw = SwitchNode(label='S', enabled=False, mode='gate')
    api = object()
    mid = object()
    conns = [_Conn(sw, mid), _Conn(mid, api)]
    off, why = blocked_by_switch(api, conns)
    assert off and 'S' in why
    sw.enabled = True
    assert blocked_by_switch(api, conns) == (False, '')


def test_new_gate_blocks_until_true():
    # 新逻辑门语义：缺输入=等；条件不成立=阻断；成立=放行
    from portpanel.ui.switch_node import SwitchNode, blocked_by_switch
    g = SwitchNode(label='G', mode='AND')
    api = object()
    conns = [_Conn(g, api)]
    off, why = blocked_by_switch(api, conns)
    assert off and '等' in why
    g.set_input('in1', 'true')
    g.set_input('in2', 'false')
    off, why = blocked_by_switch(api, conns)
    assert off and '条件不成立' in why
    g.set_input('in2', 'true')
    assert blocked_by_switch(api, conns) == (False, '')


def test_no_switch_upstream_is_open():
    from portpanel.ui.switch_node import blocked_by_switch
    a, b = object(), object()
    assert blocked_by_switch(b, [_Conn(a, b)]) == (False, '')
