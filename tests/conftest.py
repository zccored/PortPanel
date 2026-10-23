# -*- coding: utf-8 -*-
"""测试夹具：本地 echo 服务 + 禁外网守卫（05-test-baseline.md §3）。"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.echo_server import EchoServer  # noqa: E402


@pytest.fixture()
def echo():
    with EchoServer() as srv:
        yield srv


@pytest.fixture(autouse=True)
def _block_external_network(monkeypatch):
    """只允许 127.0.0.1 / ::1；任何外网 connect 直接失败（契约 §3）。"""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _is_local(address) -> bool:
        try:
            host = address[0]
        except Exception:
            return False
        return host in ('127.0.0.1', '::1', 'localhost')

    def guard_connect(self, address):
        if not _is_local(address):
            raise AssertionError('测试禁止外网请求: %r' % (address,))
        return real_connect(self, address)

    def guard_connect_ex(self, address):
        if not _is_local(address):
            raise AssertionError('测试禁止外网请求: %r' % (address,))
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, 'connect', guard_connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', guard_connect_ex)
    yield
