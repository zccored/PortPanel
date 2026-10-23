# -*- coding: utf-8 -*-
"""空转保护：自增/分页组「本轮零执行」必须判定为停，固定轮数不受影响。"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PySide6')

from portpanel.ui.flow_editor import FlowEditorDialog  # noqa: E402


def _stalled(g_auto, paged, executed):
    obj = SimpleNamespace(_round_executed=executed)
    return FlowEditorDialog._round_stalled(obj, g_auto, paged)


def test_auto_group_stalls_when_nothing_ran():
    assert _stalled(True, False, 0) is True


def test_paged_group_stalls_when_nothing_ran():
    assert _stalled(False, True, 0) is True


def test_auto_group_continues_when_something_ran():
    assert _stalled(True, False, 1) is False


def test_fixed_count_group_never_stalls():
    # 固定轮数（既非自增也非分页）保持原语义：跑满用户指定的轮数
    assert _stalled(False, False, 0) is False


def test_missing_counter_defaults_to_stall():
    obj = SimpleNamespace()
    assert FlowEditorDialog._round_stalled(obj, True, False) is True
