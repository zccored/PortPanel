# -*- coding: utf-8 -*-
"""CLI 纯函数：参数解析与子命令注册（不触网）。"""
from __future__ import annotations

import sys

from portpanel.cli import _parse_set, main


def test_parse_set_semicolon_and_comma():
    assert _parse_set('ps=5;pn=1') == {'ps': '5', 'pn': '1'}
    assert _parse_set('a=1,b=2') == {'a': '1', 'b': '2'}
    assert _parse_set('host_mid=2;offset=;timezone_offset=-480')['offset'] == ''
    assert _parse_set('') == {}


def test_cli_requires_subcommand(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['cli'])
    try:
        main([])
    except SystemExit as exc:
        assert exc.code != 0
