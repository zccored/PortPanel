# -*- coding: utf-8 -*-
"""拦截器契约实现（docs/architecture/02-interceptor.md，FROZEN）。

- 挂在 `Transport` 外面的一层包装（`InterceptingTransport`），不在业务里插桩；
- 空链零开销（直接透传）；
- 拦截器异常隔离：记日志 + 跳过，不影响主请求；
- 出去前强制重算 Content-Length（改了 body 也不会发出错误长度）。

本模块禁止 import PySide6（06-directory.md §2）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Protocol

from portpanel.engine.transport import HttpRequest, HttpResult

__all__ = [
    'Interceptor', 'InterceptingTransport',
    'SecretScanInterceptor', 'TraceInterceptor',
    'DebuggerInterceptor', 'DebugDecision',
    'RequestRecord', 'RequestJournal', 'diff_requests', 'mask_headers',
]

log = logging.getLogger(__name__)


class Interceptor(Protocol):
    name: str

    def before_send(self, req: HttpRequest) -> HttpRequest | None: ...
    def after_receive(self, req: HttpRequest, res: HttpResult) -> None: ...


# --------------------------------------------------------------------------- 工具
_SENSITIVE_HEADERS = ('authorization', 'cookie', 'set-cookie', 'x-api-key',
                      'x-auth-token', 'token', 'proxy-authorization')

_SECRET_PATTERNS = [
    (re.compile(r'(?i)bearer\s+[A-Za-z0-9._\-]{12,}'), 'Bearer token'),
    (re.compile(r'(?i)basic\s+[A-Za-z0-9+/=]{12,}'), 'Basic 凭据'),
    (re.compile(r'\bLTAI[A-Za-z0-9]{12,}\b'), '阿里云 AccessKeyId'),
    (re.compile(r'eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}'), 'JWT'),
    (re.compile(r'(?i)(password|passwd|pwd)\s*[=:]\s*\S{3,}'), '明文密码'),
    (re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'), '手机号'),
    (re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)'), '身份证号'),
    (re.compile(r'(?<!\d)\d{16,19}(?!\d)'), '疑似银行卡号'),
]


def mask_value(value: str, keep: int = 3) -> str:
    """只留前 keep 个字符 + 长度，绝不回显原值。"""
    v = str(value or '')
    if not v:
        return ''
    return '%s…(len=%d)' % (v[:keep], len(v))


def mask_headers(headers: dict) -> dict:
    out = {}
    for k, v in (headers or {}).items():
        if str(k).lower() in _SENSITIVE_HEADERS:
            out[str(k)] = mask_value(v)
        else:
            out[str(k)] = str(v)
    return out


def scan_for_secrets(text: str) -> list:
    hits = []
    for pattern, label in _SECRET_PATTERNS:
        m = pattern.search(text or '')
        if m:
            hits.append((label, mask_value(m.group(0))))
    return hits


def _recompute_content_length(req: HttpRequest) -> HttpRequest:
    """body 改了以后强制重算 Content-Length（契约 02 §5）。"""
    body = req.body
    if body is None or not isinstance(body, (bytes, bytearray)):
        return req
    length = str(len(body))
    headers = dict(req.headers or {})
    for k in list(headers.keys()):
        if str(k).lower() == 'content-length':
            if headers[k] == length:
                return req
            headers[k] = length
            return replace(req, headers=headers)
    # 原本没有 Content-Length：让底层库自己加，不主动塞（避免 multipart/分块语义变化）
    return req


# --------------------------------------------------------------------------- 链
class InterceptingTransport:
    """把拦截器链包在任意 Transport 外面。"""

    def __init__(self, inner, interceptors=None, slow_threshold_s: float = 0.0):
        self.inner = inner
        self.interceptors = list(interceptors or [])
        self.slow_threshold_s = slow_threshold_s
        self._callbacks = []

    def add(self, interceptor) -> None:
        self.interceptors.append(interceptor)

    def send(self, req: HttpRequest, on_progress: Callable | None = None) -> HttpResult:
        if not self.interceptors:                      # 空链：零开销透传
            return self.inner.send(req, on_progress)

        cur = req
        for it in self.interceptors:
            name = getattr(it, 'name', type(it).__name__)
            try:
                out = it.before_send(cur)
            except Exception as exc:                   # 异常隔离
                log.warning('拦截器 %s.before_send 异常（已跳过）: %s', name, exc)
                continue
            if out is None:
                log.info('拦截器 %s 丢弃了本次请求: %s %s', name, cur.method, cur.url)
                return HttpResult(status=0, via='blocked', error='请求被拦截器丢弃')
            if isinstance(out, HttpRequest):
                cur = out

        cur = _recompute_content_length(cur)
        res = self.inner.send(cur, on_progress)

        for it in self.interceptors:
            name = getattr(it, 'name', type(it).__name__)
            try:
                it.after_receive(cur, res)
            except Exception as exc:
                log.warning('拦截器 %s.after_receive 异常（已跳过）: %s', name, exc)
        return res


# --------------------------------------------------------------------------- 内置
class TraceInterceptor:
    """只记内存统计：耗时 / 状态码 / 通道。"""

    name = 'trace'

    def __init__(self):
        self.records = []

    def before_send(self, req):
        return req

    def after_receive(self, req, res):
        self.records.append({
            'method': req.method,
            'url': req.url.split('?')[0],
            'status': res.status,
            'via': res.via,
            'elapsed_ms': res.elapsed_ms,
        })

    def summary(self) -> dict:
        if not self.records:
            return {'count': 0}
        return {
            'count': len(self.records),
            'avg_ms': int(sum(r['elapsed_ms'] for r in self.records) / len(self.records)),
            'errors': sum(1 for r in self.records if r['status'] == 0),
        }


class SecretScanInterceptor:
    """敏感信息检测：命中只记「类型 + 打码值」，不落原值。"""

    name = 'secret-scan'

    def __init__(self):
        self.findings = []

    def before_send(self, req):
        hits = scan_for_secrets(req.url)
        for k, v in (req.headers or {}).items():
            if str(k).lower() in _SENSITIVE_HEADERS and str(v).strip():
                hits.append(('请求头 %s' % k, mask_value(v)))
        if req.body:
            try:
                body_text = req.body.decode('utf-8', 'replace')
            except Exception:
                body_text = ''
            hits.extend(scan_for_secrets(body_text))
        if hits:
            self.findings.append({'method': req.method, 'url': req.url.split('?')[0],
                                  'hits': hits})
        return req

    def after_receive(self, req, res):
        pass


# --------------------------------------------------------------------------- 断点（P2 核心）
@dataclass
class DebugDecision:
    """UI 侧对一次断点的决定。"""
    action: str = 'release'          # release | drop
    request: HttpRequest | None = None   # 改包后的新请求（None = 原样放行）


@dataclass
class RequestRecord:
    id: str
    parent: str
    ts: float
    method: str
    url: str
    headers: dict
    body_kind: str
    body_len: int
    body_preview: str
    patch: list = field(default_factory=list)
    note: str = ''


def diff_requests(orig: HttpRequest, new: HttpRequest) -> list:
    """JSON Patch 风格的差异（只比 method/url/headers/body）。"""
    patch = []
    if orig.method != new.method:
        patch.append({'op': 'replace', 'path': '/method', 'value': new.method})
    if orig.url != new.url:
        patch.append({'op': 'replace', 'path': '/url', 'value': new.url})
    old_h, new_h = dict(orig.headers or {}), dict(new.headers or {})
    for k in sorted(set(old_h) | set(new_h)):
        if old_h.get(k) != new_h.get(k):
            patch.append({'op': 'replace' if k in new_h else 'remove',
                          'path': '/headers/%s' % k,
                          'value': mask_value(new_h.get(k, ''))})
    if (orig.body or b'') != (new.body or b''):
        old_len = len(orig.body or b'')
        new_len = len(new.body or b'')
        patch.append({'op': 'replace', 'path': '/body',
                      'value': '<%d→%d bytes>' % (old_len, new_len)})
    return patch


class RequestJournal:
    """一张图纸一次运行一个 jsonl（logs/run_<ts>/requests.jsonl）。"""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def append(self, record: RequestRecord) -> None:
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record.__dict__, ensure_ascii=False) + '\n')


class DebuggerInterceptor:
    """断点：在 pre-send 阶段阻塞，等 UI 决定 放行 / 改包 / 丢弃。

    decider(req) 由 UI 提供，必须带超时（默认 120s）。
    """

    name = 'debugger'

    def __init__(self, decider: Callable[[HttpRequest], DebugDecision | None],
                 journal: RequestJournal | None = None, timeout_s: float = 120.0):
        self.decider = decider
        self.journal = journal
        self.timeout_s = timeout_s
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return 'req_%04d' % self._seq

    def _record(self, req: HttpRequest, parent: str = '', patch=None, note='') -> RequestRecord:
        body = req.body or b''
        return RequestRecord(
            id=self._next_id(), parent=parent, ts=time.time(),
            method=req.method, url=req.url.split('?')[0],
            headers=mask_headers(req.headers or {}), body_kind=req.body_kind,
            body_len=len(body),
            body_preview=body[:200].decode('utf-8', 'replace'),
            patch=list(patch or []), note=note)

    def before_send(self, req: HttpRequest):
        orig = self._record(req, note='原始请求')
        if self.journal:
            self.journal.append(orig)

        try:
            decision = self.decider(req)
        except Exception as exc:
            log.warning('断点决策异常，按放行处理: %s', exc)
            return req

        if decision is None:
            return req
        if decision.action == 'drop':
            if self.journal:
                self.journal.append(self._record(req, parent=orig.id, note='丢弃'))
            return None
        if decision.request is not None and decision.request is not req:
            patch = diff_requests(req, decision.request)
            if self.journal:
                self.journal.append(self._record(decision.request, parent=orig.id,
                                                 patch=patch, note='改包后'))
            return decision.request
        return req

    def after_receive(self, req, res):
        if self.journal:
            self.journal.append(self._record(req, note='响应 %s' % res.status))
