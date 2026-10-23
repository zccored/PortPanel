# -*- coding: utf-8 -*-
"""画板侧的 HTTP 执行适配（Qt 只出现在这一层，引擎不感知）。

- `HttpTask`：把阻塞的 `Transport.send()` 丢到子线程，UI 用 processEvents 等；
- `HttpResultResponse`：给 `_process_api_result` 用的 Response 兼容壳；
- `send_with_event_loop`：一行调用，替代原来的 `_threaded_request`（仅非下载请求）。
"""
from __future__ import annotations

import json
import time

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication

from portpanel.engine.transport import AutoTransport, HttpRequest, HttpResult

__all__ = ['HttpTask', 'HttpResultResponse', 'send_with_event_loop']


class HttpTask(QThread):
    """一次 Transport.send()（QThread）。"""

    done = Signal(object)                      # HttpResult

    def __init__(self, req: HttpRequest, transport=None, parent=None):
        super().__init__(parent)
        self.req = req
        self.transport = transport or AutoTransport()

    def run(self) -> None:
        try:
            res = self.transport.send(self.req)
        except Exception as exc:               # 兜底：任何异常都变成 HttpResult
            res = HttpResult(status=0, via='error',
                             error='%s: %s' % (type(exc).__name__, exc))
        self.done.emit(res)


class HttpResultResponse:
    """`requests.Response` 的最小兼容壳（只实现 `_process_api_result` 用到的面）。"""

    def __init__(self, res: HttpResult):
        self.status_code = int(res.status or 0)
        self.headers = dict(res.headers or {})
        self.reason = ''
        self._body = res.body or b''
        self._via = res.via
        self.encoding = 'utf-8'

    # ---- 与 requests.Response 对齐 ----
    @property
    def content(self) -> bytes:
        return self._body

    @property
    def text(self) -> str:
        return self._body.decode(self.encoding, 'replace')

    def json(self, **kwargs):
        return json.loads(self.text, **kwargs)

    def close(self) -> None:
        pass

    def iter_content(self, chunk_size: int = 65536, **kwargs):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]


def send_with_event_loop(req: HttpRequest, owner=None, transport=None,
                         cancel_check=None, hard_timeout: float = 180.0) -> HttpResult:
    """同步等待一次请求，但保持 UI 事件循环转动（画板原来的做法）。

    owner: 用来挂住 QThread 引用（防止被 GC 掉）。
    cancel_check: 可选无参函数，返回 True 则放弃等待。
    """
    task = HttpTask(req, transport, parent=owner)
    box = {}

    def _on_done(res):
        box['res'] = res

    task.done.connect(_on_done)
    if owner is not None:
        owner._http_task = task               # 防 GC
    task.start()

    deadline = time.monotonic() + hard_timeout
    while 'res' not in box:
        QApplication.processEvents()
        if cancel_check is not None:
            try:
                if cancel_check():
                    return HttpResult(status=0, via='cancelled', error='已取消')
            except Exception:
                pass
        if time.monotonic() > deadline:
            return HttpResult(status=0, via='timeout',
                              error='请求超时（%.0fs）' % hard_timeout)
        time.sleep(0.005)

    task.wait(3000)
    return box['res']
