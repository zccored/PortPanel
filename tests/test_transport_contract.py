# -*- coding: utf-8 -*-
"""Transport 契约测试（docs/architecture/01-transport.md §5）。

双适配器 × 5 种 body_kind，对着本地 echo 服务做**逐字节**断言。
"""
from __future__ import annotations

import json

import pytest

from portpanel.engine.transport import (AutoTransport, CurlCffiTransport,
                                        HttpRequest, RequestsTransport,
                                        encode_body, make_request)


def _transports():
    out = [pytest.param(RequestsTransport, id='requests')]
    try:
        import curl_cffi  # noqa: F401
        out.append(pytest.param(CurlCffiTransport, id='curl_cffi'))
    except Exception:
        out.append(pytest.param(CurlCffiTransport, id='curl_cffi',
                                marks=pytest.mark.skip(reason='curl_cffi 未安装')))
    return out


@pytest.mark.parametrize('transport_cls', _transports())
def test_get_no_body(echo, transport_cls):
    t = transport_cls()
    req = make_request('GET', echo.url_for('/hello'), {'X-A': '1'})
    res = t.send(req)
    assert res.error == '', res.error
    assert res.status == 200
    rec = echo.records[-1]
    assert rec['method'] == 'GET'
    assert rec['path'] == '/hello'
    assert rec['body_len'] == 0
    assert rec['headers'].get('x-a') == '1'


@pytest.mark.parametrize('transport_cls', _transports())
def test_post_json_bytes_exact(echo, transport_cls):
    payload = {'page': 2, 'q': '中文', 'nested': {'a': [1, 2]}}
    req = make_request('POST', echo.url_for('/json'), body=payload, body_kind='json')
    res = transport_cls().send(req)
    assert res.error == '', res.error
    rec = echo.records[-1]
    assert rec['method'] == 'POST'
    assert rec['headers'].get('content-type', '').startswith('application/json')
    import base64
    sent = base64.b64decode(rec['body_b64'])
    assert sent == json.dumps(payload, ensure_ascii=False).encode('utf-8')
    assert '中文'.encode('utf-8') in sent        # ensure_ascii=False


@pytest.mark.parametrize('transport_cls', _transports())
def test_form_encoding(echo, transport_cls):
    req = make_request('PUT', echo.url_for('/form'),
                       body={'a': '1', 'b': ['x', 'y']}, body_kind='form')
    res = transport_cls().send(req)
    assert res.error == '', res.error
    rec = echo.records[-1]
    assert rec['method'] == 'PUT'
    assert rec['headers'].get('content-type', '').startswith('application/x-www-form-urlencoded')
    import base64
    assert base64.b64decode(rec['body_b64']) == b'a=1&b=x&b=y'


@pytest.mark.parametrize('transport_cls', _transports())
def test_raw_body(echo, transport_cls):
    req = make_request('PATCH', echo.url_for('/raw'), body='raw文本', body_kind='raw')
    res = transport_cls().send(req)
    assert res.error == '', res.error
    rec = echo.records[-1]
    import base64
    assert base64.b64decode(rec['body_b64']) == 'raw文本'.encode('utf-8')
    assert rec['headers'].get('content-type') == 'application/octet-stream'


@pytest.mark.parametrize('transport_cls', _transports())
def test_delete_no_body(echo, transport_cls):
    res = transport_cls().send(make_request('DELETE', echo.url_for('/d')))
    assert res.error == '', res.error
    assert echo.records[-1]['method'] == 'DELETE'
    assert echo.records[-1]['body_len'] == 0


def test_none_with_body_rejected():
    with pytest.raises(ValueError):
        HttpRequest(method='POST', url='http://127.0.0.1/x', body=b'x',
                    body_kind='none')


def test_json_content_type_not_overridden():
    req = make_request('POST', 'http://127.0.0.1/x', {'Content-Type': 'application/vnd.api+json'},
                       body={'a': 1}, body_kind='json')
    assert req.headers['Content-Type'] == 'application/vnd.api+json'


def test_encode_body_multipart_not_implemented_yet():
    with pytest.raises(NotImplementedError):
        encode_body('multipart', {'f': b'x'})


def test_auto_transport_returns_primary_result(echo):
    res = AutoTransport().send(make_request('GET', echo.url_for('/auto')))
    assert res.status == 200 and res.via in ('requests', 'curl_cffi')
