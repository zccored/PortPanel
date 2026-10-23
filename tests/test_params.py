# -*- coding: utf-8 -*-
"""参数位置解析（core/params.py）的契约测试。"""
from __future__ import annotations

import pytest

from portpanel.core.params import (ResolvedRequest, build_url, normalize_location,
                                   resolve_locations, substitute_path)


def test_default_is_path_and_backward_compatible():
    defs = [{'name': 'id'}, {'name': 'page'}]
    r = resolve_locations(defs, {'id': '42', 'page': '2'}, '/items/{id}')
    assert r.path == '/items/42'
    assert r.query == {} and r.headers == {} and r.body_fields == {}


def test_four_locations_split():
    defs = [
        {'name': 'id', 'location': 'path'},
        {'name': 'page', 'location': 'query'},
        {'name': 'X-Token', 'location': 'header'},
        {'name': 'note', 'location': 'body'},
    ]
    values = {'id': '7', 'page': '3', 'X-Token': 'abc', 'note': 'hi'}
    r = resolve_locations(defs, values, '/items/{id}')
    assert r.path == '/items/7'
    assert r.query == {'page': '3'}
    assert r.headers == {'X-Token': 'abc'}
    assert r.body_fields == {'note': 'hi'}
    assert r.unused == {}


def test_unknown_location_falls_back_to_path():
    assert normalize_location('QUERY') == 'query'
    assert normalize_location('nonsense') == 'path'
    assert normalize_location(None) == 'path'
    r = resolve_locations([{'name': 'a', 'location': 'weird'}], {'a': '1'}, '/x/{a}')
    assert r.path == '/x/1'


def test_missing_value_keeps_placeholder():
    r = resolve_locations([{'name': 'id'}], {}, '/items/{id}')
    assert r.path == '/items/{id}'


def test_build_url_merges_query():
    url = build_url('https://api.example.com/v1?token=t', '/items',
                    {'page': 2, 'q': '中文'})
    assert url.startswith('https://api.example.com/v1/items?')
    assert 'token=t' in url
    assert 'page=2' in url
    assert 'q=%E4%B8%AD%E6%96%87' in url


def test_substitute_path_multiple():
    assert substitute_path('/a/{x}/b/{y}', {'x': 1, 'y': 2}) == '/a/1/b/2'
