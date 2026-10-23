# -*- coding: utf-8 -*-
"""网络出口：系统代理解析（Windows 注册表 / 环境变量）。

背景：本机走 ``127.0.0.1:7890`` 的 HTTP 代理访问外网；直连时 TLS 会被拦，
表现为 ``curl: (60) unable to get local issuer certificate`` 或 15s 超时。
requests / curl_cffi 默认都不读 Windows 的「Internet 选项」代理，所以必须显式喂给它们。

优先级：环境变量（HTTPS_PROXY / HTTP_PROXY / ALL_PROXY）> Windows 系统代理。
``ProxyOverride`` 里的 localhost / 127.* / *域名 也要遵守，否则访问本机服务会被代理坑。
"""
from __future__ import annotations

import fnmatch
import os
import time
from urllib.parse import urlparse

__all__ = ['proxies_for', 'system_proxies', 'bypass_for', 'refresh']

_cache = {'ts': 0.0, 'value': None}
_TTL = 5.0
_LOCAL_HOSTS = ('localhost', '127.0.0.1', '::1', '0.0.0.0')


def _norm(server: str) -> str:
    server = (server or '').strip()
    if not server:
        return ''
    if '://' not in server:
        server = 'http://' + server
    return server.rstrip('/')


def _env_proxies():
    """环境变量优先；大小写都认（Windows 上常见 HTTPS_PROXY）。"""
    get = os.environ.get
    https = get('HTTPS_PROXY') or get('https_proxy')
    http = get('HTTP_PROXY') or get('http_proxy')
    allp = get('ALL_PROXY') or get('all_proxy')
    https = https or allp or http
    http = http or allp or https
    if not https and not http:
        return None
    return {'http': _norm(http or ''), 'https': _norm(https or '')}


def _win_proxies():
    """读 HKCU\\...\\Internet Settings；ProxyServer 支持 'host:port' 或 'http=..;https=..'。"""
    if os.name != 'nt':
        return None
    try:
        import winreg
    except Exception:
        return None
    key = r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as h:
            enabled, _ = winreg.QueryValueEx(h, 'ProxyEnable')
            if not enabled:
                return None
            server, _ = winreg.QueryValueEx(h, 'ProxyServer')
    except Exception:
        return None
    server = str(server or '').strip()
    if not server:
        return None
    if '=' in server:                      # http=127.0.0.1:7890;https=127.0.0.1:7890
        parts = {}
        for item in server.split(';'):
            if '=' in item:
                k, v = item.split('=', 1)
                parts[k.strip().lower()] = _norm(v)
        if not parts:
            return None
        return {'http': parts.get('http', parts.get('https', '')),
                'https': parts.get('https', parts.get('http', ''))}
    one = _norm(server)
    return {'http': one, 'https': one}


def system_proxies(force: bool = False):
    """返回 {'http': url, 'https': url} 或 None；5 秒缓存，避免每次请求都读注册表。

    设 ``PORTPANEL_NO_PROXY=1`` 可强制直连（排查代理问题时用）。
    """
    if os.environ.get('PORTPANEL_NO_PROXY'):
        return None
    now = time.time()
    if not force and (now - _cache['ts']) < _TTL:
        return _cache['value']
    value = _env_proxies() or _win_proxies()
    _cache.update(ts=now, value=value)
    return value


def refresh():
    _cache.update(ts=0.0, value=None)


def _override_entries():
    if os.name != 'nt':
        return []
    try:
        import winreg
        key = r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as h:
            raw, _ = winreg.QueryValueEx(h, 'ProxyOverride')
    except Exception:
        return []
    return [x.strip() for x in str(raw or '').split(';') if x.strip()]


def bypass_for(url: str) -> bool:
    """该 URL 是否应绕过代理（本机 / ProxyOverride 命中）。"""
    host = (urlparse(url).hostname or '').lower()
    if not host:
        return True
    if host in _LOCAL_HOSTS or host.startswith('127.') or host == '::1':
        return True
    for entry in _override_entries():
        if entry == '<local>':
            if '.' not in host:
                return True
            continue
        if fnmatch.fnmatch(host, entry.lower()):
            return True
    return False


def proxies_for(url: str):
    """给 requests / curl_cffi 用的 proxies 字典；本机地址返回 None。"""
    if bypass_for(url):
        return None
    return system_proxies()
