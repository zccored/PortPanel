# -*- coding: utf-8 -*-
"""翻页合并：游标提取 / 多页拼接 / has_more 透传。"""
from __future__ import annotations

from portpanel.engine.pagination import get_path, merge_pages


def test_get_path_basic():
    d = {'data': {'offset': 'abc', 'items': [{'id': 1}, {'id': 2}], 'has_more': True}}
    assert get_path(d, '$.data.offset') == 'abc'
    assert get_path(d, '$.data.has_more') is True
    assert get_path(d, '$.data.items[*].id') == [1, 2]
    assert get_path(d, '$.data.nope', 'x') == 'x'


def test_merge_pages_concats_items():
    p1 = {'code': 0, 'data': {'items': [{'id': 1}, {'id': 2}], 'offset': 'A', 'has_more': True}}
    p2 = {'code': 0, 'data': {'items': [{'id': 3}], 'offset': 'B', 'has_more': False}}
    m = merge_pages([p1, p2])
    assert [x['id'] for x in m['data']['items']] == [1, 2, 3]
    assert m['data']['has_more'] is False
    assert m['data']['offset'] == 'B'
    assert m['_pages'] == 2


def test_merge_pages_single():
    p = {'code': 0, 'data': {'items': [{'id': 9}]}}
    m = merge_pages([p])
    assert len(m['data']['items']) == 1 and m['_pages'] == 1
