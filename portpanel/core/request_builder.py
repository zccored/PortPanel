# -*- coding: utf-8 -*-
"""把「节点配置 + 本轮参数值」编成一条 HttpRequest（禁止 Qt，可单测）。

这是 P1a 的**唯一构建入口**：画板、调试器、以后的端口节点都调它，
避免「配置页能发 body、画板发不出去」那种两张皮。
"""
from __future__ import annotations

import json
from urllib.parse import parse_qsl, urlsplit

from portpanel.core.params import build_url, resolve_locations, substitute_path
from portpanel.engine.transport import HttpRequest, make_request

__all__ = ['build_node_request', 'build_request_from_url', 'substitute_placeholders']


def substitute_placeholders(template: str, values: dict) -> str:
    """把 body 模板里的 `{name}` 换掉（与 url_path 同一套写法）。"""
    out = str(template or '')
    for key, val in (values or {}).items():
        out = out.replace('{%s}' % key, '' if val is None else str(val))
    return out


def build_node_request(*, base_url: str, url_path: str, method: str,
                       params=None, values=None, headers=None,
                       body_template: str = '', body_kind: str = 'none',
                       timeout=(10.0, 30.0)) -> HttpRequest:
    """节点级请求构建。

    params:  [{'name','location'}]，location 缺省 = path（老图纸行为不变）
    values:  {name: value}
    body_template: 节点上存的 body 文本（可为空；form 用 body_fields 拼）
    """
    params = list(params or [])
    values = dict(values or {})
    headers = dict(headers or {})
    body_kind = (body_kind or 'none').lower()
    if body_kind not in ('none', 'json', 'form', 'multipart', 'raw'):
        raise ValueError('未知 body_kind: %r' % (body_kind,))

    # url_path 里可能已经带了 ?a=1（老图纸常见），先拆开再合并 query
    split = urlsplit(str(url_path or ''))
    path_only = split.path
    inline_query = dict(parse_qsl(split.query, keep_blank_values=True))

    resolved = resolve_locations(params, values, path_only)
    query = dict(inline_query)
    query.update(resolved.query)                       # 参数位置优先

    # header 落点：不覆盖用户显式写的请求头
    for k, v in resolved.headers.items():
        headers.setdefault(k, v)

    url = build_url(base_url, resolved.path, query)

    # ---- body ----
    body = None
    if body_kind != 'none':
        if body_kind == 'json':
            if str(body_template or '').strip():
                text = substitute_placeholders(body_template, values)
                try:
                    json.loads(text)                   # 仅校验，不重排
                except Exception as exc:
                    raise ValueError('body_template 不是合法 JSON: %s' % exc) from exc
                body = text
            elif resolved.body_fields:
                body = dict(resolved.body_fields)
            else:
                body = {}
        elif body_kind == 'form':
            if resolved.body_fields:
                body = dict(resolved.body_fields)
            elif str(body_template or '').strip():
                body = substitute_placeholders(body_template, values).encode('utf-8')
            else:
                body = {}
        elif body_kind == 'raw':
            body = substitute_placeholders(body_template, values)
        else:                                          # multipart：P1c
            raise NotImplementedError('multipart 属于 P1c')

    return make_request(method, url, headers, body=body, body_kind=body_kind,
                        timeout=timeout)


def _encode_payload(body_template: str, values: dict, body_kind: str,
                    body_fields: dict):
    """body 编码的公共分支（build_node_request 与 build_request_from_url 共用）。"""
    body_kind = (body_kind or 'none').lower()
    if body_kind == 'none':
        return None
    if body_kind == 'json':
        if str(body_template or '').strip():
            text = substitute_placeholders(body_template, values)
            try:
                json.loads(text)
            except Exception as exc:
                raise ValueError('body_template 不是合法 JSON: %s' % exc) from exc
            return text
        return dict(body_fields) if body_fields else {}
    if body_kind == 'form':
        if body_fields:
            return dict(body_fields)
        if str(body_template or '').strip():
            return substitute_placeholders(body_template, values)
        return {}
    if body_kind == 'raw':
        return substitute_placeholders(body_template, values)
    raise NotImplementedError('multipart 属于 P1c')


def build_request_from_url(*, url: str, method: str, params=None, values=None,
                           headers=None, body_template: str = '',
                           body_kind: str = 'none',
                           timeout=(10.0, 30.0)) -> HttpRequest:
    """画板专用：URL 已由上层拼好（含拼合值 / 数组替换），这里只补
    `location != path` 的参数落点、body 与请求头。

    与 `build_node_request` 的区别：不重建 path —— 画板的 url_path 拼装
    （含 concat 值）在 `_compute_api_url` 里已经做完，重复拼会出错。
    """
    from urllib.parse import urlencode

    params = list(params or [])
    values = dict(values or {})
    headers = dict(headers or {})
    body_kind = (body_kind or 'none').lower()

    resolved = resolve_locations(params, values, '')       # path 落点已在 url 里
    final_url = str(url or '')
    if resolved.query:
        sep = '&' if ('?' in final_url) else '?'
        final_url = final_url + sep + urlencode(resolved.query, doseq=True)
    for k, v in resolved.headers.items():
        headers.setdefault(k, v)

    body = _encode_payload(body_template, values, body_kind, resolved.body_fields)
    return make_request(method, final_url, headers, body=body,
                        body_kind=body_kind, timeout=timeout)
