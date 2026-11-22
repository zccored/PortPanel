# -*- coding: utf-8 -*-
"""逻辑门节点：与门 / 非门 / 异或门 的条件与放行规则。"""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from PySide6.QtWidgets import QApplication

from portpanel.ui.switch_node import (SwitchNode, blocked_by_switch, parse_bool)


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def up(text=None, param='in1'):
    node = SimpleNamespace(_last_body_text=text)
    return SimpleNamespace(start_obj=node, end_obj=None, end_param=param)


def wire(gate, text, param):
    """造一条“上游节点 → 门的某个输入”的连线。"""
    upstream = SimpleNamespace(_last_body_text=text)
    return SimpleNamespace(start_obj=upstream, end_obj=gate, end_param=param)


# ---------------- 解析 ----------------
def test_parse_bool_accepts_true_false(app):
    assert parse_bool('true') is True
    assert parse_bool('TRUE') is True
    assert parse_bool(' false \n') is False
    assert parse_bool('"true"') is True


def test_parse_bool_rejects_other_text(app):
    assert parse_bool('ok') is None
    assert parse_bool('') is None
    assert parse_bool(None) is None


# ---------------- 与门 ----------------
@pytest.mark.parametrize('a,b,want', [
    ('true', 'true', True),
    ('true', 'false', False),
    ('false', 'true', False),
    ('false', 'false', False),
])
def test_and_truth_table(app, a, b, want):
    g = SwitchNode(mode='AND')
    g.set_input('in1', a)
    g.set_input('in2', b)
    assert g.evaluate() is want


def test_and_waits_when_input_missing(app):
    g = SwitchNode(mode='AND')
    g.set_input('in1', 'true')
    assert g.evaluate() is None          # in2 还没来 → 继续等
    g.set_input('in2', '不是布尔')
    assert g.evaluate() is None


# ---------------- 非门 ----------------
def test_not_gate(app):
    g = SwitchNode(mode='NOT')
    g.set_input('in1', 'false')
    assert g.evaluate() is True
    g.set_input('in1', 'true')
    assert g.evaluate() is False
    g.set_input('in1', None)
    assert g.evaluate() is None


# ---------------- 异或门 ----------------
@pytest.mark.parametrize('a,b,want', [
    ('true', 'true', False),
    ('true', 'false', True),
    ('false', 'true', True),
    ('false', 'false', False),
])
def test_xor_truth_table(app, a, b, want):
    g = SwitchNode(mode='XOR')
    g.set_input('in1', a)
    g.set_input('in2', b)
    assert g.evaluate() is want


# ---------------- 放行规则 ----------------
def test_run_once_releases_data_only_when_true(app):
    g = SwitchNode(mode='AND')
    conns = [wire(g, 'true', 'in1'), wire(g, 'true', 'in2'), wire(g, 'PAYLOAD', 'data')]
    out = g.run_once(conns)
    assert out == {'OUT': 'true', 'data': 'PAYLOAD'}


def test_run_once_blocks_data_when_false(app):
    g = SwitchNode(mode='AND')
    conns = [wire(g, 'true', 'in1'), wire(g, 'false', 'in2'), wire(g, 'PAYLOAD', 'data')]
    assert g.run_once(conns) is False
    assert g.outputs['OUT'] == 'false'
    assert g.outputs['data'] is None     # 不成立 → data 不放行


def test_run_once_returns_none_while_waiting(app):
    g = SwitchNode(mode='AND')
    conns = [wire(g, 'true', 'in1'), wire(g, 'PAYLOAD', 'data')]
    assert g.run_once(conns) is None
    assert g.outputs['OUT'] is None


# ---------------- 下游阻断 ----------------
def _downstream(gate):
    """gate → 下游 API 节点 的连线结构（blocked_by_switch 从下游往上找门）。"""
    api = SimpleNamespace(_last_body_text=None)
    return api, [SimpleNamespace(start_obj=gate, end_obj=api, end_param='')]


def test_blocked_when_condition_false(app):
    g = SwitchNode(mode='AND')
    g.set_input('in1', 'true')
    g.set_input('in2', 'false')
    api, conns = _downstream(g)
    blocked, why = blocked_by_switch(api, conns)
    assert blocked is True
    assert '条件不成立' in why


def test_blocked_while_waiting(app):
    g = SwitchNode(mode='AND')
    g.set_input('in1', 'true')          # in2 还没到
    api, conns = _downstream(g)
    blocked, why = blocked_by_switch(api, conns)
    assert blocked is True
    assert '等' in why


def test_pass_when_condition_true(app):
    g = SwitchNode(mode='AND')
    g.set_input('in1', 'true')
    g.set_input('in2', 'true')
    api, conns = _downstream(g)
    blocked, why = blocked_by_switch(api, conns)
    assert blocked is False
    assert g.outputs['OUT'] == 'true'


def test_legacy_gate_mode_still_works(app):
    g = SwitchNode(mode='gate', enabled=False)
    api, conns = _downstream(g)
    blocked, why = blocked_by_switch(api, conns)
    assert blocked is True
    assert '已关闭' in why


def test_no_gate_means_no_block(app):
    api = SimpleNamespace()
    assert blocked_by_switch(api, []) == (False, '')


# ---------------- 序列化 ----------------
def test_serialization_roundtrip(app):
    g = SwitchNode(label='条件门', mode='XOR')
    d = g.to_dict()
    assert d['mode'] == 'XOR' and d['type'] == 'SwitchNode'
    g2 = SwitchNode.from_dict(d)
    assert g2.mode == 'XOR' and g2.label == '条件门'


def test_old_drawing_mode_gate_loads(app):
    g = SwitchNode.from_dict({'type': 'SwitchNode', 'name': '限流开关', 'enabled': True})
    assert g.mode == 'AND' or g.mode == 'gate'
