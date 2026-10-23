# -*- coding: utf-8 -*-
"""拦截器链 / 断点 / 留痕的契约测试（02-interceptor.md §4）。"""
from __future__ import annotations

import json
import os

import pytest

from portpanel.engine.intercept import (DebugDecision, DebuggerInterceptor,
                                        InterceptingTransport, RequestJournal,
                                        SecretScanInterceptor, TraceInterceptor,
                                        diff_requests)
from portpanel.engine.transport import (HttpRequest, RequestsTransport,
                                        make_request)


def _req(url, body=None, kind='none', headers=None, method='POST'):
    return make_request(method, url, headers or {}, body=body, body_kind=kind)


def test_empty_chain_passthrough(echo):
    t = InterceptingTransport(RequestsTransport())
    res = t.send(_req(echo.url_for('/x'), method='GET'))
    assert res.status == 200


def test_chain_order_and_after_receive(echo):
    calls = []

    class A:
        name = 'a'
        def before_send(self, req):
            calls.append('a.before'); return req
        def after_receive(self, req, res):
            calls.append('a.after')

    class B:
        name = 'b'
        def before_send(self, req):
            calls.append('b.before'); return req
        def after_receive(self, req, res):
            calls.append('b.after')

    t = InterceptingTransport(RequestsTransport(), [A(), B()])
    t.send(_req(echo.url_for('/chain'), method='GET'))
    assert calls == ['a.before', 'b.before', 'a.after', 'b.after']


def test_exception_isolation(echo):
    class Boom:
        name = 'boom'
        def before_send(self, req):
            raise RuntimeError('boom')
        def after_receive(self, req, res):
            raise RuntimeError('boom-after')

    t = InterceptingTransport(RequestsTransport(), [Boom()])
    res = t.send(_req(echo.url_for('/boom'), method='GET'))
    assert res.status == 200 and not res.error


def test_drop_returns_blocked(echo):
    class Drop:
        name = 'drop'
        def before_send(self, req):
            return None
        def after_receive(self, req, res):
            pass

    t = InterceptingTransport(RequestsTransport(), [Drop()])
    res = t.send(_req(echo.url_for('/drop'), method='GET'))
    assert res.status == 0 and res.via == 'blocked'
    assert echo.records == []


def test_content_length_recomputed_after_edit(echo):
    class Rewrite:
        name = 'rewrite'
        def before_send(self, req):
            from dataclasses import replace
            return replace(req, body=b'{"a":1}', body_kind='json',
                           headers={**req.headers, 'Content-Length': '999'})
        def after_receive(self, req, res):
            pass

    t = InterceptingTransport(RequestsTransport(), [Rewrite()])
    res = t.send(_req(echo.url_for('/cl'), body=b'xx', kind='raw'))
    assert res.status == 200
    rec = echo.records[-1]
    assert rec['headers'].get('content-length') == '7'
    import base64
    assert base64.b64decode(rec['body_b64']) == b'{"a":1}'


def test_secret_scan_masks_values(echo):
    scan = SecretScanInterceptor()
    t = InterceptingTransport(RequestsTransport(), [scan])
    req = _req(echo.url_for('/secret'), headers={'Authorization': 'Bearer abc123'})
    t.send(req)
    assert scan.findings
    text = json.dumps(scan.findings, ensure_ascii=False)
    assert 'abc123' not in text
    assert 'Bearer token' in text or 'Authorization' in text


def test_trace_summary(echo):
    tr = TraceInterceptor()
    t = InterceptingTransport(RequestsTransport(), [tr])
    t.send(_req(echo.url_for('/t1'), method='GET'))
    t.send(_req(echo.url_for('/t2'), method='GET'))
    s = tr.summary()
    assert s['count'] == 2 and s['errors'] == 0


def test_debugger_release_records_two_entries(echo, tmp_path):
    journal = RequestJournal(str(tmp_path / 'requests.jsonl'))
    dbg = DebuggerInterceptor(lambda req: DebugDecision(action='release'), journal=journal)
    t = InterceptingTransport(RequestsTransport(), [dbg])
    t.send(make_request('POST', echo.url_for('/dbg'), {'X': '1'},
                        body={'a': 1}, body_kind='json'))
    lines = open(journal.path, encoding='utf-8').read().strip().splitlines()
    assert len(lines) >= 2
    rec = json.loads(lines[0])
    assert rec['method'] == 'POST' and rec['body_kind'] == 'json'


def test_debugger_edit_records_patch(echo, tmp_path):
    journal = RequestJournal(str(tmp_path / 'requests.jsonl'))

    def decider(req):
        from dataclasses import replace
        return DebugDecision(action='release',
                             request=replace(req, headers={**req.headers, 'X-Edited': '1'}))

    dbg = DebuggerInterceptor(decider, journal=journal)
    t = InterceptingTransport(RequestsTransport(), [dbg])
    t.send(make_request('GET', echo.url_for('/edit')))
    recs = [json.loads(x) for x in open(journal.path, encoding='utf-8').read().splitlines()]
    edited = [r for r in recs if r['patch']]
    assert edited, recs
    assert any(p['path'] == '/headers/X-Edited' for p in edited[0]['patch'])
    assert echo.records[-1]['headers'].get('x-edited') == '1'


def test_debugger_drop(echo, tmp_path):
    journal = RequestJournal(str(tmp_path / 'requests.jsonl'))
    dbg = DebuggerInterceptor(lambda req: DebugDecision(action='drop'), journal=journal)
    t = InterceptingTransport(RequestsTransport(), [dbg])
    res = t.send(make_request('GET', echo.url_for('/drop2')))
    assert res.via == 'blocked'
    assert echo.records == []


def test_diff_requests_masks_header_values():
    a = make_request('GET', 'http://x/y', {'Authorization': 'Bearer s3cr3t'})
    b = make_request('GET', 'http://x/z', {'Authorization': 'Bearer s3cr3t2'})
    patch = diff_requests(a, b)
    text = json.dumps(patch, ensure_ascii=False)
    assert 's3cr3t2' not in text
    assert any(p['path'] == '/url' for p in patch)
