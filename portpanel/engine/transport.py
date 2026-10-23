# -*- coding: utf-8 -*-
"""出站 HTTP 的唯一实现（docs/architecture/01-transport.md，状态 FROZEN）。

- 引擎 / 调试器 / 端口节点以后都只走 `Transport.send()`；
- **下载链路豁免**：`DownloadWorker` 的 Range / 流式 / 取消本轮不动（契约 §4）；
- 本模块禁止 import PySide6（06-directory.md §2）。
"""
from __future__ import annotations

import json
import tempfile
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Literal, Protocol

__all__ = [
    'BodyKind', 'HttpRequest', 'HttpResult', 'Transport',
    'encode_body', 'default_content_type', 'make_request',
    'RequestsTransport', 'CurlCffiTransport', 'AutoTransport',
]

BodyKind = Literal['none', 'json', 'form', 'multipart', 'raw']
_BODY_KINDS = ('none', 'json', 'form', 'multipart', 'raw')

_DEFAULT_CT = {
    'json': 'application/json; charset=utf-8',
    'form': 'application/x-www-form-urlencoded',
    'raw': 'application/octet-stream',
}


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict = field(default_factory=dict)
    body: bytes | None = None
    body_kind: BodyKind = 'none'
    timeout: tuple = (10.0, 30.0)
    stream: bool = False

    def __post_init__(self):
        if self.body_kind not in _BODY_KINDS:
            raise ValueError('未知 body_kind: %r' % (self.body_kind,))
        # 契约 01 §2：body_kind='none' 却带 body → 早暴露
        if self.body_kind == 'none' and self.body:
            raise ValueError("body_kind='none' 但 body 非空（契约 01 §2）")
        object.__setattr__(self, 'method', str(self.method or 'GET').upper())


@dataclass
class HttpResult:
    status: int = 0
    headers: dict = field(default_factory=dict)
    body: bytes | None = None
    tmp_path: str | None = None
    via: str = 'requests'
    elapsed_ms: int = 0
    error: str = ''

    @property
    def ok(self) -> bool:
        return not self.error and 200 <= self.status < 400


class Transport(Protocol):
    def send(self, req: HttpRequest, on_progress: Callable | None = None) -> HttpResult: ...


# --------------------------------------------------------------------------- 编码
def default_content_type(kind: str) -> str:
    return _DEFAULT_CT.get(kind, '')


def encode_body(kind: BodyKind, payload) -> bytes | None:
    """按契约 01 §2 编码。multipart 留给 P1c（流式生成器）。"""
    if kind == 'none':
        if payload:
            raise ValueError("body_kind='none' 不接受 body")
        return None
    if kind == 'json':
        if payload is None:
            return None
        if isinstance(payload, bytes):
            return payload
        if isinstance(payload, str):
            return payload.encode('utf-8')
        return json.dumps(payload, ensure_ascii=False).encode('utf-8')
    if kind == 'form':
        if payload is None:
            return None
        if isinstance(payload, bytes):
            return payload
        if isinstance(payload, str):
            return payload.encode('utf-8')
        pairs = []
        for k, v in payload.items():
            if isinstance(v, (list, tuple)):
                pairs.extend((k, item) for item in v)
            else:
                pairs.append((k, v))
        return urllib.parse.urlencode(pairs, doseq=True).encode('utf-8')
    if kind == 'raw':
        if payload is None:
            return None
        return payload if isinstance(payload, bytes) else str(payload).encode('utf-8')
    if kind == 'multipart':
        raise NotImplementedError('multipart 属于 P1c（流式生成器），本轮未实现')
    raise ValueError('未知 body_kind: %r' % (kind,))


def make_request(method: str, url: str, headers: dict | None = None, *,
                 body=None, body_kind: BodyKind = 'none',
                 timeout=(10.0, 30.0), stream: bool = False) -> HttpRequest:
    """构建请求：编码 body + 补 Content-Type（已有则不动）。"""
    blob = encode_body(body_kind, body)
    hdrs = {str(k): str(v) for k, v in (headers or {}).items()}
    if blob is not None and body_kind != 'none':
        has_ct = any(k.lower() == 'content-type' for k in hdrs)
        if not has_ct:
            ct = default_content_type(body_kind)
            if ct:
                hdrs['Content-Type'] = ct
    return HttpRequest(method=method, url=url, headers=hdrs, body=blob,
                       body_kind=body_kind, timeout=tuple(timeout), stream=stream)


# --------------------------------------------------------------------------- 适配器
def _write_tmp(resp) -> str:
    fd = tempfile.NamedTemporaryFile(delete=False, suffix='.dlpack')
    try:
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                fd.write(chunk)
    finally:
        fd.close()
    return fd.name


class RequestsTransport:
    name = 'requests'

    def send(self, req: HttpRequest, on_progress: Callable | None = None) -> HttpResult:
        import requests

        t0 = time.perf_counter()
        try:
            from portpanel.core.net import proxies_for
            resp = requests.request(req.method, req.url, headers=dict(req.headers),
                                    data=req.body, timeout=req.timeout,
                                    stream=req.stream, allow_redirects=True,
                                    proxies=proxies_for(req.url))
            elapsed = int((time.perf_counter() - t0) * 1000)
            headers = {k.lower(): v for k, v in resp.headers.items()}
            if req.stream:
                path = _write_tmp(resp)
                resp.close()
                return HttpResult(status=resp.status_code, headers=headers,
                                  body=None, tmp_path=path, via=self.name,
                                  elapsed_ms=elapsed)
            return HttpResult(status=resp.status_code, headers=headers,
                              body=resp.content, via=self.name, elapsed_ms=elapsed)
        except Exception as exc:                        # 网络层异常不抛给调用方
            return HttpResult(status=0, via=self.name,
                              elapsed_ms=int((time.perf_counter() - t0) * 1000),
                              error='%s: %s' % (type(exc).__name__, exc))


class CurlCffiTransport:
    name = 'curl_cffi'
    impersonate = 'chrome124'

    def send(self, req: HttpRequest, on_progress: Callable | None = None) -> HttpResult:
        try:
            from curl_cffi import requests as curl_requests
        except Exception as exc:                        # 未安装
            return HttpResult(status=0, via=self.name, error='curl_cffi 不可用: %s' % exc)

        t0 = time.perf_counter()
        try:
            from portpanel.core.net import proxies_for
            resp = curl_requests.request(req.method, req.url, headers=dict(req.headers),
                                         data=req.body, timeout=req.timeout,
                                         stream=req.stream,
                                         impersonate=self.impersonate,
                                         allow_redirects=True,
                                         proxies=proxies_for(req.url))
            elapsed = int((time.perf_counter() - t0) * 1000)
            headers = {k.lower(): v for k, v in resp.headers.items()}
            if req.stream:
                path = _write_tmp(resp)
                return HttpResult(status=resp.status_code, headers=headers,
                                  body=None, tmp_path=path, via=self.name,
                                  elapsed_ms=elapsed)
            return HttpResult(status=resp.status_code, headers=headers,
                              body=resp.content, via=self.name, elapsed_ms=elapsed)
        except Exception as exc:
            return HttpResult(status=0, via=self.name,
                              elapsed_ms=int((time.perf_counter() - t0) * 1000),
                              error='%s: %s' % (type(exc).__name__, exc))


class AutoTransport:
    """先 requests，遇到网络异常 / 403·429·503 再换 curl_cffi（沿用现状策略）。"""

    name = 'auto'
    FALLBACK_STATUS = (403, 429, 503)

    def __init__(self, primary=None, fallback=None):
        self.primary = primary or RequestsTransport()
        self.fallback = fallback or CurlCffiTransport()

    def send(self, req: HttpRequest, on_progress: Callable | None = None) -> HttpResult:
        res = self.primary.send(req, on_progress)
        need = (not res.error and res.status in self.FALLBACK_STATUS) or bool(res.error)
        if not need:
            return res
        alt = self.fallback.send(req, on_progress)
        if alt.error:
            return res                                 # 回退也失败 → 报第一轮结果
        return alt
