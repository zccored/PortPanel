# -*- coding: utf-8 -*-
"""请求构建器（core/request_builder.py）：画板/调试器共用的唯一入口。"""
from __future__ import annotations

import json

import pytest

from portpanel.core.request_builder import build_node_request


PARAMS = [
    {'name': 'id', 'location': 'path'},
    {'name': 'page', 'location': 'query'},
    {'name': 'X-Trace', 'location': 'header'},
    {'name': 'note', 'location': 'body'},
]


def test_get_with_query_and_path():
    req = build_node_request(
        base_url='http://127.0.0.1:9/api', url_path='/items/{id}', method='GET',
        params=PARAMS, values={'id': '7', 'page': '2'})
    assert req.url == 'http://127.0.0.1:9/api/items/7?page=2'
    assert req.body is None and req.body_kind == 'none'


def test_header_location_does_not_override_explicit():
    req = build_node_request(
        base_url='http://127.0.0.1:9', url_path='/x', method='GET',
        params=PARAMS, values={'X-Trace': 'from-param'},
        headers={'X-Trace': 'explicit'})
    assert req.headers['X-Trace'] == 'explicit'


def test_post_json_from_template_substitutes():
    req = build_node_request(
        base_url='http://127.0.0.1:9', url_path='/items', method='POST',
        params=PARAMS, values={'page': '3', 'note': 'hello'},
        body_kind='json',
        body_template='{"page": {page}, "note": "{note}", "fixed": true}')
    sent = json.loads(req.body.decode('utf-8'))
    assert sent == {'page': 3, 'note': 'hello', 'fixed': True}
    assert req.headers['Content-Type'].startswith('application/json')


def test_post_json_from_body_fields_when_template_empty():
    req = build_node_request(
        base_url='http://127.0.0.1:9', url_path='/items', method='POST',
        params=PARAMS, values={'note': 'hi'}, body_kind='json')
    assert json.loads(req.body.decode('utf-8')) == {'note': 'hi'}


def test_bad_json_template_rejected_early():
    with pytest.raises(ValueError):
        build_node_request(
            base_url='http://127.0.0.1:9', url_path='/x', method='POST',
            body_kind='json', body_template='{oops')


def test_form_body_fields():
    req = build_node_request(
        base_url='http://127.0.0.1:9', url_path='/f', method='PUT',
        params=PARAMS, values={'note': 'a b'}, body_kind='form')
    assert req.body == b'note=a+b'
    assert req.headers['Content-Type'].startswith('application/x-www-form-urlencoded')


def test_raw_template():
    req = build_node_request(
        base_url='http://127.0.0.1:9', url_path='/r', method='PATCH',
        params=PARAMS, values={'page': '5'}, body_kind='raw',
        body_template='page={page}')
    assert req.body == b'page=5'


def test_inline_query_in_url_path_is_preserved():
    req = build_node_request(
        base_url='http://127.0.0.1:9', url_path='/x?token=t', method='GET',
        params=PARAMS, values={'page': '2'})
    assert 'token=t' in req.url and 'page=2' in req.url
