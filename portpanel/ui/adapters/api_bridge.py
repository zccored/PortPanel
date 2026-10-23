# -*- coding: utf-8 -*-
"""画板执行 → 统一 transport 的桥（把接线从 flow_editor 里挪出来）。

flow_editor 里只留一行调用；构建、发送、结果转换、日志提示都在这里，
这样 06-directory.md「flow_editor 只减不增」不至于因为 P1a 破例太多。
"""
from __future__ import annotations

from portpanel.core.request_builder import build_request_from_url
from portpanel.engine.transport import HttpResult
from portpanel.ui.adapters.http_task import HttpResultResponse, send_with_event_loop

__all__ = ['send_via_transport']


def send_via_transport(owner, node, full_url: str, method: str, values: dict,
                       headers: dict, cancel_check=None):
    """返回 `(dl_result, is_tmp, used_curl, err, content_type, log_note)`。

    返回值形状与 `_threaded_request` 保持兼容，`_process_api_result` 不用改。
    """
    body_kind = getattr(node, 'body_kind', 'none') or 'none'
    node_body = getattr(node, 'body', '') or ''
    timeout = (10.0, 30.0)
    try:
        timeout = tuple(owner._run_transfer().api_timeout_tuple)
    except Exception:
        pass

    try:
        req = build_request_from_url(
            url=full_url, method=method,
            params=getattr(node, 'params', None), values=values,
            headers=headers, body_template=node_body,
            body_kind=body_kind, timeout=timeout)
    except Exception as exc:
        return (None, False, False, '%s: %s' % (type(exc).__name__, exc), ''), ''

    note = '请求体: %s, %d 字节' % (body_kind, len(req.body or b''))
    # 断点节点：把请求包进拦截器链（worker 线程会停在 pre-send 等主线程放行）
    transport = None
    try:
        from portpanel.ui.debug.bridge import get_bridge
        _b = get_bridge()
        if _b.is_armed(node):
            from portpanel.engine.intercept import (DebuggerInterceptor,
                                                    InterceptingTransport,
                                                    RequestJournal)
            from portpanel.engine.transport import AutoTransport
            _dbg = DebuggerInterceptor(_b.make_decider(node),
                                       journal=RequestJournal(_b.journal_path()))
            transport = InterceptingTransport(AutoTransport(), [_dbg])
    except Exception:
        transport = None

    res = send_with_event_loop(req, owner=owner, transport=transport,
                               cancel_check=cancel_check)
    if not isinstance(res, HttpResult) or res.error:
        err = res.error if isinstance(res, HttpResult) else '请求失败'
        return (None, False, False, err, ''), note

    content_type = (res.headers.get('content-type')
                    or res.headers.get('Content-Type') or '')
    return (HttpResultResponse(res), False, res.via == 'curl_cffi', None,
            content_type), note
