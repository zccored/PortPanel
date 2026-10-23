# -*- coding: utf-8 -*-
"""最小 RFC6455 WebSocket（服务端 + 客户端），零第三方依赖。

只实现工业转发用得到的那部分：握手、文本/二进制帧、ping/pong、关闭。
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading

GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA

__all__ = ['accept_key', 'encode_frame', 'read_frame', 'WSClient', 'WSServer',
           'OP_TEXT', 'OP_BIN', 'OP_CLOSE', 'OP_PING', 'OP_PONG']


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def encode_frame(opcode: int, payload: bytes, mask: bool = False) -> bytes:
    payload = payload or b''
    head = bytes([0x80 | opcode])
    ln = len(payload)
    if ln < 126:
        head += bytes([(0x80 if mask else 0) | ln])
    elif ln < 65536:
        head += bytes([(0x80 if mask else 0) | 126]) + struct.pack('>H', ln)
    else:
        head += bytes([(0x80 if mask else 0) | 127]) + struct.pack('>Q', ln)
    if not mask:
        return head + payload
    key = b'\x11\x22\x33\x44'
    return head + key + bytes(b ^ key[i % 4] for i, b in enumerate(payload))


def _recv_exact(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('WebSocket 连接关闭')
        buf += chunk
    return buf


def read_frame(sock):
    """返回 (opcode, payload)。控制帧内部处理（ping 自动回 pong）。"""
    while True:
        b1, b2 = _recv_exact(sock, 2)
        opcode = b1 & 0x0F
        masked = b2 & 0x80
        ln = b2 & 0x7F
        if ln == 126:
            ln = struct.unpack('>H', _recv_exact(sock, 2))[0]
        elif ln == 127:
            ln = struct.unpack('>Q', _recv_exact(sock, 8))[0]
        key = _recv_exact(sock, 4) if masked else b''
        data = _recv_exact(sock, ln) if ln else b''
        if masked:
            data = bytes(b ^ key[i % 4] for i, b in enumerate(data))
        if opcode == OP_PING:
            sock.sendall(encode_frame(OP_PONG, data))
            continue
        if opcode == OP_PONG:
            continue
        return opcode, data


def _server_handshake(sock, path_ok=None):
    raw = b''
    while b'\r\n\r\n' not in raw:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        raw += chunk
    text = raw.decode('latin-1')
    lines = text.split('\r\n')
    headers = {}
    for line in lines[1:]:
        if ':' in line:
            k, v = line.split(':', 1)
            headers[k.strip().lower()] = v.strip()
    path = lines[0].split(' ')[1] if len(lines[0].split(' ')) > 1 else '/'
    if path_ok and not path_ok(path):
        sock.sendall(b'HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n')
        return None
    key = headers.get('sec-websocket-key')
    if not key:
        sock.sendall(b'HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n')
        return None
    resp = ('HTTP/1.1 101 Switching Protocols\r\n'
            'Upgrade: websocket\r\nConnection: Upgrade\r\n'
            'Sec-WebSocket-Accept: %s\r\n\r\n' % accept_key(key))
    sock.sendall(resp.encode())
    return path


class WSClient:
    """最小 WS 客户端（连服务端、发文本/二进制、收一帧）。"""

    def __init__(self, url: str, timeout: float = 3.0):
        # ws://host:port/path
        assert url.startswith('ws://'), '只支持 ws://'
        rest = url[5:]
        hostport, _, path = rest.partition('/')
        host, _, port = hostport.partition(':')
        self.host, self.port = host, int(port or 80)
        self.path = '/' + path if path else '/'
        self.timeout = timeout
        self.sock = None

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        key = base64.b64encode(b'0123456789abcdef').decode()
        req = ('GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n'
               'Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n'
               'Sec-WebSocket-Version: 13\r\n\r\n' % (self.path, self.host, self.port, key))
        self.sock.sendall(req.encode())
        head = b''
        while b'\r\n\r\n' not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError('握手失败')
            head += chunk
        if b'101' not in head.split(b'\r\n')[0]:
            raise ConnectionError('握手被拒: %r' % head.split(b'\r\n')[0])
        return self

    def send_text(self, text: str):
        self.sock.sendall(encode_frame(OP_TEXT, text.encode(), mask=True))

    def send_binary(self, data: bytes):
        self.sock.sendall(encode_frame(OP_BIN, data, mask=True))

    def recv(self, timeout: float | None = None):
        if timeout is not None:
            self.sock.settimeout(timeout)
        return read_frame(self.sock)

    def close(self):
        try:
            self.sock.sendall(encode_frame(OP_CLOSE, b''))
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


class WSServer:
    """WS 服务端：维护客户端列表，支持广播。"""

    def __init__(self, host='127.0.0.1', port=0, path='/', on_message=None):
        self.host, self.port, self.path = host, port, path
        self.on_message = on_message
        self.clients = []
        self._srv = None
        self._stop = False
        self._lock = threading.Lock()

    def start(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port))
        self._srv.listen(8)
        self.port = self._srv.getsockname()[1]
        threading.Thread(target=self._accept_loop, daemon=True).start()
        return self

    def _accept_loop(self):
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        conn.settimeout(30)
        try:
            path = _server_handshake(conn, lambda p: p.split('?')[0] == self.path
                                     or self.path == '/')
            if path is None:
                return
        except Exception:
            return
        with self._lock:
            self.clients.append(conn)
        try:
            while not self._stop:
                opcode, data = read_frame(conn)
                if opcode == OP_CLOSE:
                    break
                if callable(self.on_message):
                    try:
                        self.on_message(data, opcode, conn)
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            with self._lock:
                if conn in self.clients:
                    self.clients.remove(conn)
            try:
                conn.close()
            except Exception:
                pass

    def broadcast(self, text: str):
        payload = text.encode('utf-8')
        with self._lock:
            dead = []
            for c in self.clients:
                try:
                    c.sendall(encode_frame(OP_TEXT, payload))
                except Exception:
                    dead.append(c)
            for c in dead:
                self.clients.remove(c)

    def stop(self):
        self._stop = True
        try:
            self._srv.close()
        except Exception:
            pass
        with self._lock:
            for c in list(self.clients):
                try:
                    c.close()
                except Exception:
                    pass
            self.clients.clear()
