# -*- coding: utf-8 -*-
"""本地 echo 服务：给 transport / 参数位置解析做**逐字节**断言。

约束（见 docs/architecture/05-test-baseline.md）：
- 只绑 127.0.0.1:0（随机端口），测试进程不允许发外网；
- 收到什么原样回什么（method / path / query / headers / body），body 用 base64 回传；
- 不写日志、不落盘。
"""
from __future__ import annotations

import base64
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _EchoHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _handle(self) -> None:
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        raw_path = self.path
        parsed = urllib.parse.urlsplit(raw_path)
        payload = {
            'method': self.command,
            'raw_path': raw_path,
            'path': parsed.path,
            'query': dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)),
            'headers': {k.lower(): v for k, v in self.headers.items()},
            'body_b64': base64.b64encode(body).decode('ascii'),
            'body_len': len(body),
        }
        blob = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        # 先记账再回包：否则客户端读到响应时服务端可能还没 append，断言会偶发空列表
        if self.server.records is not None:                 # type: ignore[attr-defined]
            with self.server.records_lock:                  # type: ignore[attr-defined]
                self.server.records.append(payload)         # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle
    do_HEAD = _handle
    do_OPTIONS = _handle

    def handle_one_request(self):
        """把客户端正常关闭/复位连接当正常收尾（否则 keep-alive 会刷一屏 traceback）。"""
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def log_message(self, format, *args) -> None:           # 静音
        pass


class EchoServer:
    """上下文管理器：with EchoServer() as srv: srv.url / srv.records。"""

    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), _EchoHandler)
        self.httpd.records = []                             # type: ignore[attr-defined]
        self.httpd.records_lock = threading.Lock()          # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        kwargs={'poll_interval': 0.01}, daemon=True)

    def __enter__(self) -> 'EchoServer':
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=3)

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    @property
    def url(self) -> str:
        return 'http://127.0.0.1:%d' % self.port

    @property
    def records(self) -> list:
        with self.httpd.records_lock:                       # type: ignore[attr-defined]
            return list(self.httpd.records)                 # type: ignore[attr-defined]

    def url_for(self, path: str) -> str:
        return self.url + path


if __name__ == '__main__':                                  # 手动调试用
    with EchoServer() as srv:
        print('echo server:', srv.url, '(Ctrl+C 退出)')
        try:
            srv._thread.join()
        except KeyboardInterrupt:
            pass
