# -*- coding: utf-8 -*-
"""端口节点运行时：把一条工业链路的「入口」和「出口」拆开。

三个可独立测试的部件：

- ``ModbusPort``   : 在 listen_port 上扮演 Modbus 从站（TIA/PLC 写进来），
                     每次写入按策略转发到 target（NX 机电模拟）。
- ``RtPort``       : 在 listen_port 上收 PROFINET RT 隧道帧，原样/解析后转发到 target。
- ``RawPort``      : 纯 TCP 双向透传（未知协议兜底）。

统一由 ``PortBridge`` 持有，提供 start/stop/stats，不依赖 Qt，可脱离界面跑。
"""
from __future__ import annotations

import collections
import json
import socket
import threading
import time
from dataclasses import dataclass, field

from .protocols import modbus as mb
from .protocols import profinet as pn
from .protocols.websocket import WSServer


@dataclass
class PortStats:
    frames_in: int = 0
    frames_out: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    errors: int = 0
    last_error: str = ''
    last_payload: dict = field(default_factory=dict)
    recent: object = field(default_factory=lambda: collections.deque(maxlen=200))

    def log(self, direction, summary, raw=None):
        import time as _t
        self.recent.append({'ts': _t.time(), 'dir': direction,
                            'summary': str(summary)[:300], 'raw': raw})

    def as_dict(self):
        return {'frames_in': self.frames_in, 'frames_out': self.frames_out,
                'bytes_in': self.bytes_in, 'bytes_out': self.bytes_out,
                'errors': self.errors, 'last_error': self.last_error}


class _Base:
    proto = 'raw'

    def __init__(self, listen_port=0, target_host='127.0.0.1', target_port=0,
                 host='127.0.0.1', timeout=3.0, on_event=None):
        self.host, self.listen_port = host, int(listen_port)
        self.target_host, self.target_port = target_host, int(target_port)
        # 多目标（交换机式输出）：[(host, port), ...]；为空时退回 target_host/target_port
        self.targets = []
        self.timeout = timeout
        self.on_event = on_event
        self.stats = PortStats()
        self._stop = False
        self._threads = []

    # ---- 生命周期 ----
    def start(self):
        raise NotImplementedError

    def stop(self):
        self._stop = True
        for t in self._threads:
            t.join(timeout=0.5)

    @property
    def url(self):
        return '%s:%d' % (self.host, self.listen_port)

    def target_url(self):
        return '%s:%d' % (self.target_host, self.target_port)

    def _target_list(self):
        return list(self.targets) or ([(self.target_host, self.target_port)]
                                      if self.target_port else [])

    def _emit(self, kind, **kw):
        if callable(self.on_event):
            try:
                self.on_event(kind, kw)
            except Exception:
                pass


class ModbusPort(_Base):
    """Modbus 从站 → 转发为 Modbus 客户端写入目标从站。

    ``forward='auto'``：把**本次写入的地址段和值**原样写进目标；
    ``forward='mirror'``：把整个寄存器镜像按 ``map_ranges`` 批量写过去。
    """
    proto = 'modbus'

    def __init__(self, *a, unit=1, forward='auto', map_ranges=None, **kw):
        super().__init__(*a, **kw)
        self.unit = unit
        self.forward = forward
        self.map_ranges = map_ranges or []
        self.slave = None

    def start(self):
        self.slave = mb.ModbusSlave(host=self.host, port=self.listen_port,
                                    unit=self.unit, on_write=self._on_write).start()
        self.listen_port = self.slave.port
        return self

    def stop(self):
        if self.slave:
            self.slave.stop()
        super().stop()

    def _on_write(self, addr, values, unit):
        self.stats.frames_in += 1
        self.stats.bytes_in += 2 * len(values)
        self.stats.last_payload = {'fc': 'write', 'addr': addr, 'values': values}
        self.stats.log('in', 'write addr=%s values=%s' % (addr, values),
                       {'addr': addr, 'values': list(values)})
        self._emit('in', addr=addr, values=values)
        if self.forward == 'mirror' and self.map_ranges:
            for start, count in self.map_ranges:
                vals = [self.slave.registers.get(start + i, 0) for i in range(count)]
                self._send(start, vals)
        else:
            self._send(addr, values)

    def _send(self, addr, values):
        for host, port in self._target_list():
            try:
                mb.ModbusClient(host, port, timeout=self.timeout,
                                unit=self.unit).write_registers(addr, values)
                self.stats.frames_out += 1
                self.stats.bytes_out += 2 * len(values)
                self.stats.log('out', '→%s:%s write addr=%s values=%s'
                               % (host, port, addr, values))
                self._emit('out', addr=addr, values=list(values))
            except Exception as exc:
                self.stats.errors += 1
                self.stats.last_error = str(exc)
                self._emit('error', error=str(exc))


class RtPort(_Base):
    """PROFINET RT 隧道帧：收 → （按 lane 表拆槽位）→ 转发到目标。"""

    proto = 'profinet'

    def __init__(self, *a, frame_id=0x8000, slot_spec=None, rewrite_frame_id=None, **kw):
        super().__init__(*a, **kw)
        self.frame_id = frame_id
        self.slot_spec = slot_spec or [(0x1, 0x1, 4), (0x2, 0x1, 4)]
        self.rewrite_frame_id = rewrite_frame_id

    def start(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.listen_port))
        self._srv.listen(8)
        self.listen_port = self._srv.getsockname()[1]
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        self._threads.append(t)
        return self

    def _accept_loop(self):
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            conn.settimeout(self.timeout)
            while not self._stop:
                try:
                    frame = pn.read_frame(conn, self.slot_spec)
                except Exception:
                    return
                self.stats.frames_in += 1
                self.stats.bytes_in += len(frame.payload())
                self.stats.last_payload = {
                    'frame_id': hex(frame.frame_id),
                    'slots': {(s.slot, s.subslot): s.as_u16() for s in frame.slots}}
                self.stats.log('in', 'RT %s slots=%s' % (
                    hex(frame.frame_id),
                    {k: v for k, v in self.stats.last_payload['slots'].items()}))
                self._emit('in', frame_id=frame.frame_id)
                out = pn.RtFrame(frame_id=self.rewrite_frame_id or frame.frame_id,
                                 slots=frame.slots)
                for host, port in self._target_list():
                    try:
                        with socket.create_connection((host, port),
                                                      timeout=self.timeout) as s:
                            s.sendall(pn.encode(out))
                        self.stats.frames_out += 1
                        self.stats.bytes_out += len(out.payload())
                        self.stats.log('out', '→%s:%s RT %s' % (host, port,
                                                                hex(out.frame_id)))
                        self._emit('out', frame_id=out.frame_id)
                    except Exception as exc:
                        self.stats.errors += 1
                        self.stats.last_error = str(exc)
                        self._emit('error', error=str(exc))

    def stop(self):
        try:
            self._srv.close()
        except Exception:
            pass
        super().stop()


class RawPort(_Base):
    """TCP 双向透传（未知协议兜底，字节两端都记）。"""
    proto = 'raw'

    def start(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.listen_port))
        self._srv.listen(8)
        self.listen_port = self._srv.getsockname()[1]
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        self._threads.append(t)
        return self

    def _accept_loop(self):
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            conn.settimeout(self.timeout)
            try:
                up = socket.create_connection((self.target_host, self.target_port),
                                              timeout=self.timeout)
            except Exception as exc:
                self.stats.errors += 1
                self.stats.last_error = str(exc)
                return
            with up:
                def pump(src, dst, inbound):
                    while not self._stop:
                        try:
                            data = src.recv(65536)
                        except Exception:
                            return
                        if not data:
                            return
                        dst.sendall(data)
                        if inbound:
                            self.stats.frames_in += 1
                            self.stats.bytes_in += len(data)
                        else:
                            self.stats.frames_out += 1
                            self.stats.bytes_out += len(data)
                t = threading.Thread(target=pump, args=(up, conn, False), daemon=True)
                t.start()
                pump(conn, up, True)


class WsPort(_Base):
    """WebSocket ↔ PROFINET RT 互转。

    - ``direction='ws2pn'``：listen 是 WS 服务端，收到 JSON 文本 → 组成 RT 帧 → 转发到 target；
    - ``direction='pn2ws'``：listen 是 PN 入口（TCP），WS 服务端在 ``ws_port``（默认 listen+1），
      每收到一帧就广播成 JSON 给所有 WS 客户端。
    """
    proto = 'websocket'

    def __init__(self, *a, direction='ws2pn', path='/pn', slot_spec=None,
                 ws_port=0, **kw):
        super().__init__(*a, **kw)
        self.direction = direction if direction in ('ws2pn', 'pn2ws') else 'ws2pn'
        self.path = path or '/pn'
        self.slot_spec = slot_spec or [(1, 1, 4), (2, 1, 4)]
        self.ws_port = int(ws_port or 0)
        self.server = None
        self._pn_srv = None

    def start(self):
        if self.direction == 'ws2pn':
            self.server = WSServer(self.host, self.listen_port, self.path,
                                   on_message=self._on_ws).start()
            self.listen_port = self.server.port
        else:
            self._pn_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._pn_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._pn_srv.bind((self.host, self.listen_port))
            self._pn_srv.listen(8)
            self.listen_port = self._pn_srv.getsockname()[1]
            threading.Thread(target=self._pn_loop, daemon=True).start()
            self.server = WSServer(self.host, self.ws_port or (self.listen_port + 1),
                                   self.path).start()
            self.ws_port = self.server.port
        return self

    # ---- WS → PN ----
    def _on_ws(self, data, opcode, conn):
        self.stats.frames_in += 1
        self.stats.bytes_in += len(data)
        try:
            obj = json.loads(data.decode('utf-8'))
            frame = pn.frame_from_json(obj, self.slot_spec)
        except Exception as exc:
            self.stats.errors += 1
            self.stats.last_error = 'WS 报文解析失败: %s' % str(exc)[:60]
            return
        self.stats.last_payload = pn.frame_to_json(frame)
        self.stats.log('in', 'WS→PN %s' % json.dumps(self.stats.last_payload,
                                                     ensure_ascii=False)[:200],
                       self.stats.last_payload)
        for host, port in self._target_list():
            try:
                with socket.create_connection((host, port), timeout=self.timeout) as s:
                    s.sendall(pn.encode(frame))
                self.stats.frames_out += 1
                self.stats.bytes_out += len(pn.encode(frame))
                self.stats.log('out', '→%s:%s PN %s'
                               % (host, port,
                                  json.dumps(self.stats.last_payload,
                                             ensure_ascii=False)[:160]))
            except Exception as exc:
                self.stats.errors += 1
                self.stats.last_error = str(exc)

    # ---- PN → WS ----
    def _pn_loop(self):
        while not self._stop:
            try:
                conn, _ = self._pn_srv.accept()
            except OSError:
                return
            threading.Thread(target=self._pn_serve, args=(conn,), daemon=True).start()

    def _pn_serve(self, conn):
        with conn:
            conn.settimeout(self.timeout)
            while not self._stop:
                try:
                    frame = pn.read_frame(conn, self.slot_spec)
                except Exception:
                    return
                self.stats.frames_in += 1
                self.stats.bytes_in += len(frame.payload())
                payload = json.dumps(pn.frame_to_json(frame, self.slot_spec),
                                     ensure_ascii=False)
                self.stats.last_payload = pn.frame_to_json(frame, self.slot_spec)
                if self.server is not None:
                    self.server.broadcast(payload)
                    self.stats.frames_out += len(self.server.clients)
                    self.stats.bytes_out += len(payload)

    def stop(self):
        if self.server is not None:
            self.server.stop()
        try:
            if self._pn_srv:
                self._pn_srv.close()
        except Exception:
            pass
        super().stop()


class PortBridge:
    """一条链路的门面：按协议选实现，统一 start/stop/stats。"""

    def __init__(self, proto='modbus', **kw):
        self.proto = proto
        cls = {'modbus': ModbusPort, 'profinet': RtPort, 'websocket': WsPort,
               'raw': RawPort}.get(proto, RawPort)
        self.impl = cls(**kw)
        self.impl.targets = [tuple(x) for x in (kw.get('targets') or [])]

    def start(self):
        self.impl.start()
        return self

    def stop(self):
        self.impl.stop()

    @property
    def stats(self):
        return self.impl.stats

    @property
    def listen_port(self):
        return self.impl.listen_port

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def wait_for(predicate, timeout=5.0, interval=0.02):
    """轮询等待（测试/演示用）。"""
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())
