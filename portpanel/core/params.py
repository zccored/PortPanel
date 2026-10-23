# -*- coding: utf-8 -*-
"""参数位置解析（docs/architecture/06-directory.md §1：core/ 禁止 Qt）。

`api_ref` 的每个参数早就带了 `location` 字段，但画板从来没读过它 ——
现在只有「`{name}` 在 url_path 里做字符串替换」这一种落点。
本模块把四种落点补齐，且**默认仍是 path**，保证老图纸行为不变。

    page=1&q=x        → location='query'
    X-Token: abc      → location='header'
    {"page": 1}       → location='body'
    /items/42         → location='path'（默认）

用法：

    resolved = resolve_locations(param_defs, values)
    url = build_url('https://api.example.com', '/items/{id}', resolved)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

__all__ = [
    'ParamLocation', 'ResolvedRequest', 'normalize_location',
    'substitute_path', 'resolve_locations', 'build_url',
]

ParamLocation = str
VALID_LOCATIONS = ('path', 'query', 'header', 'body')


def normalize_location(raw) -> str:
    """未知 / 空值一律当 path（向后兼容第一原则）。"""
    loc = str(raw or '').strip().lower()
    return loc if loc in VALID_LOCATIONS else 'path'


@dataclass(frozen=True)
class ResolvedRequest:
    """一张请求里「参数该去哪儿」的结果。"""
    path: str = ''
    query: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    body_fields: dict = field(default_factory=dict)
    # 没被任何参数占用的原 values，供调用方排查
    unused: dict = field(default_factory=dict)


def substitute_path(path: str, values: Mapping) -> str:
    """把 `{name}` 换掉；没有对应值的占位符原样保留（便于日志暴露问题）。"""
    out = str(path or '')
    for key, val in (values or {}).items():
        out = out.replace('{%s}' % key, '' if val is None else str(val))
    return out


def resolve_locations(param_defs: Iterable[Mapping],
                      values: Mapping,
                      path_template: str = '') -> ResolvedRequest:
    """按 location 把参数分流。

    param_defs: [{'name': 'id', 'location': 'path'}, ...]
    values:     {'id': '42', ...}
    返回各落点的键值；path 落点已经替换进 `path`。
    """
    values = dict(values or {})
    path_values: dict = {}
    query: dict = {}
    headers: dict = {}
    body_fields: dict = {}
    used: set = set()

    for spec in param_defs or ():
        name = str(spec.get('name') or '').strip()
        if not name:
            continue
        loc = normalize_location(spec.get('location'))
        if name not in values:
            continue
        val = values[name]
        used.add(name)
        if loc == 'path':
            path_values[name] = val
        elif loc == 'query':
            query[name] = '' if val is None else str(val)
        elif loc == 'header':
            headers[name] = '' if val is None else str(val)
        else:                                   # body
            body_fields[name] = val

    path = substitute_path(path_template, path_values) if path_template else ''
    unused = {k: v for k, v in values.items() if k not in used}
    return ResolvedRequest(path=path, query=query, headers=headers,
                           body_fields=body_fields, unused=unused)


def build_url(base_url: str, path: str = '', query: Mapping | None = None) -> str:
    """拼 URL：保留 base 里已有的 query，再追加新 query（同名以新值为准）。"""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    base = str(base_url or '')
    parts = urlsplit(base)
    merged = dict(parse_qsl(parts.query, keep_blank_values=True))
    for k, v in (query or {}).items():
        merged[str(k)] = '' if v is None else str(v)
    new_path = parts.path.rstrip('/') + ('/' + str(path).lstrip('/') if path else '')
    return urlunsplit((parts.scheme, parts.netloc, new_path,
                       urlencode(merged, doseq=True), parts.fragment))
