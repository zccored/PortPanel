# -*- coding: utf-8 -*-
"""接口翻页（offset / cursor 型）——纯函数 + 一段人话说明。

B 站这类接口翻页长这样：
    第 1 页：offset=空        → 返回 data.items[20] + data.offset=abc + data.has_more=true
    第 2 页：offset=abc       → 返回下一批 + 新 offset
    …直到 data.has_more=false

所以“翻页”只有三件事，全都写进节点的 `paginate` 配置里：
    param        : 哪个参数是翻页游标（如 offset，或 pn 页码）
    next_path    : 从响应里哪儿取下一页的游标（如 $.data.offset）
    has_more_path: 从响应里哪儿判断还有没有（如 $.data.has_more；没有就靠“空页”停）
    max_pages    : 最多翻几页（保险丝，默认 5）
    items_path   : 结果数组在哪（如 $.data.items），多页按顺序拼起来
    delay        : 每页之间等多久（默认 0.6s，给限流留余地）

这样画板上一个节点就代表“这个接口翻完 N 页”，下游拿到的是一份合并后的结果，
不用在图上画循环线，也不用记 offset。
"""
from __future__ import annotations

import copy

__all__ = ['get_path', 'merge_pages', 'plan_pages', 'MergedResponse']


def get_path(obj, path: str, default=None):
    """极简 JSONPath：只支持 $.a.b[0].c 与 $.a[*].b（[*] 返回列表）。

    例：get_path(resp, '$.data.offset') / get_path(resp, '$.data.items[*].opus_id')
    """
    if not path or not path.startswith('$'):
        return default
    cur = obj
    for raw in path[2:].split('.'):
        if not raw:
            continue
        name, idx = raw, None
        if '[' in raw and raw.endswith(']'):
            name, idx = raw[:-1].split('[')
        if isinstance(cur, dict):
            cur = cur.get(name) if name else cur
        elif isinstance(cur, list):
            cur = [x.get(name) if isinstance(x, dict) else None for x in cur]
        if idx is not None:
            if idx == '*':
                continue
            i = int(idx)
            if isinstance(cur, list) and len(cur) > i:
                cur = cur[i]
            else:
                return default
        if cur is None:
            return default
    return cur


def merge_pages(pages, items_path: str = '$.data.items') -> dict:
    """把多页响应合成一份：items 按顺序拼接，其余字段取第 1 页（has_more 取最后一页）。"""
    pages = [p for p in pages if isinstance(p, dict)]
    if not pages:
        return {}
    first = copy.deepcopy(pages[0])
    merged = []
    for p in pages:
        got = get_path(p, items_path, []) or []
        if isinstance(got, list):
            merged.extend(got)
    container = first
    parts = [x for x in items_path[2:].split('.') if x]
    for part in parts[:-1]:
        if isinstance(container, dict):
            container = container.setdefault(part, {})
    if parts and isinstance(container, dict):
        container[parts[-1]] = merged
    last = pages[-1]
    more = get_path(last, '$.data.has_more', None)
    if isinstance(first.get('data'), dict) and more is not None:
        first['data']['has_more'] = more
        first['data']['offset'] = get_path(last, '$.data.offset', '')
    first['_pages'] = len(pages)
    return first


def plan_pages(max_pages: int, delay: float) -> dict:
    """给 UI/日志用的一行摘要。"""
    return {'max_pages': int(max_pages), 'delay': float(delay)}


class MergedResponse:
    """多页合并后的响应壳：字段与 requests/HttpResult 兼容，供结果处理器直接消费。"""

    def __init__(self, payload: dict, template=None):
        import json as _json
        self._json = payload
        self.content = _json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.status_code = getattr(template, 'status_code', 200) if template else 200
        self.headers = dict(getattr(template, 'headers', {}) or {})
        self.reason = getattr(template, 'reason', 'OK')
        self.url = getattr(template, 'url', '')

    @property
    def text(self):
        return self.content.decode('utf-8', 'replace')

    def json(self):
        return self._json
