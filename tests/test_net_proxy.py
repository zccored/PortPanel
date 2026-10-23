# -*- coding: utf-8 -*-
"""系统代理解析：本机绕过 / 环境变量优先 / ProxyOverride 通配 / 直连开关。"""
from __future__ import annotations

import pytest

from portpanel.core import net


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv('PORTPANEL_NO_PROXY', raising=False)
    for k in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy',
              'ALL_PROXY', 'all_proxy'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(net, '_override_entries', lambda: [])
    net.refresh()
    yield
    net.refresh()


def test_localhost_bypasses_proxy(monkeypatch):
    monkeypatch.setattr(net, '_win_proxies', lambda: {'http': 'http://p:1', 'https': 'http://p:1'})
    assert net.proxies_for('http://127.0.0.1:8080/x') is None
    assert net.proxies_for('http://localhost:8080/x') is None


def test_env_proxy_used(monkeypatch):
    monkeypatch.setattr(net, '_win_proxies', lambda: {'http': 'http://reg:1', 'https': 'http://reg:1'})
    monkeypatch.setenv('HTTPS_PROXY', 'http://env:2')
    net.refresh()
    assert net.proxies_for('https://example.com/a')['https'] == 'http://env:2'


def test_windows_proxy_used(monkeypatch):
    monkeypatch.setattr(net, '_win_proxies', lambda: {'http': 'http://reg:7890', 'https': 'http://reg:7890'})
    net.refresh()
    assert net.proxies_for('https://example.com/a') == {'http': 'http://reg:7890',
                                                       'https': 'http://reg:7890'}


def test_override_wildcard_bypass(monkeypatch):
    monkeypatch.setattr(net, '_win_proxies', lambda: {'http': 'http://p:1', 'https': 'http://p:1'})
    monkeypatch.setattr(net, '_override_entries', lambda: ['*zhihu.com', '192.168.*'])
    net.refresh()
    assert net.proxies_for('https://www.zhihu.com/a') is None
    assert net.proxies_for('http://192.168.1.5/x') is None
    assert net.proxies_for('https://api.example.com/a') is not None


def test_no_proxy_switch(monkeypatch):
    monkeypatch.setattr(net, '_win_proxies', lambda: {'http': 'http://p:1', 'https': 'http://p:1'})
    monkeypatch.setenv('PORTPANEL_NO_PROXY', '1')
    net.refresh()
    assert net.proxies_for('https://example.com/a') is None


def test_no_proxy_returns_none(monkeypatch):
    monkeypatch.setattr(net, '_win_proxies', lambda: None)
    net.refresh()
    assert net.proxies_for('https://example.com/a') is None
