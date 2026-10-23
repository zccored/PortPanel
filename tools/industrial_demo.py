# -*- coding: utf-8 -*-
"""工业互联网链路端到端演示（无需博图 / NX，全部本机仿真）。

    TIA 仿真                    端口节点                      NX 机电模拟
  ┌──────────────┐   FC16   ┌────────────────┐   FC16   ┌──────────────────┐
  │ PLC 寄存器    │ ───────▶ │ ModbusPort     │ ───────▶ │ NxSink.modbus    │
  │ 15020        │          │ 15020 → 15021  │          │ 15021            │
  └──────────────┘          └────────────────┘          │                  │
  ┌──────────────┐  PN RT   ┌────────────────┐   PN RT  │ NxSink.pn        │
  │ IO 控制器     │ ───────▶ │ RtPort         │ ───────▶ │ 15031            │
  └──────────────┘          │ 15030 → 15031  │          └──────────────────┘
                            └────────────────┘
  另外用 POST 节点把汇总推到 NX 的 HTTP 接口（验证「POST/PUT 作为节点」的猜想）。

跑法：python tools/industrial_demo.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from portpanel.core.request_builder import build_node_request      # noqa: E402
from portpanel.engine.ports import ModbusPort, RtPort, wait_for    # noqa: E402
from portpanel.engine.protocols import modbus as mb                # noqa: E402
from portpanel.engine.protocols import profinet as pn              # noqa: E402
from portpanel.engine.transport import RequestsTransport           # noqa: E402

SLOTS = [(0x1, 0x1, 4), (0x2, 0x1, 4)]        # 槽1：主轴转速/扭矩；槽2：工位状态/计数
REG_MAP = [(0, 4)]                             # 40001..40004


class NxSink:
    """Designcenter NX 机电模拟的替身：Modbus 从站 + PN 接收器 + HTTP 接口。"""

    def __init__(self):
        self.modbus = mb.ModbusSlave(unit=1).start()
        self.pn_frames = []
        self.posts = []
        self._pn_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._pn_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._pn_srv.bind(('127.0.0.1', 0))
        self._pn_srv.listen(4)
        self.pn_port = self._pn_srv.getsockname()[1]
        threading.Thread(target=self._pn_loop, daemon=True).start()
        self.http = _HttpSink(self.posts).start()

    def _pn_loop(self):
        while True:
            try:
                conn, _ = self._pn_srv.accept()
            except OSError:
                return
            with conn:
                try:
                    frame = pn.read_frame(conn, SLOTS)
                    self.pn_frames.append(frame.slot_map())
                except Exception:
                    pass

    def stop(self):
        self.modbus.stop()
        self.http.stop()
        try:
            self._pn_srv.close()
        except Exception:
            pass


class _HttpSink:
    """最小的 NX HTTP 入口（只记录收到的 POST/PUT）。"""

    def __init__(self, store):
        self.store = store
        store_ref = store

        class H(BaseHTTPRequestHandler):
            def do_POST(self):                       # noqa: N802
                n = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(n).decode('utf-8', 'replace')
                store_ref.append({'method': 'POST', 'path': self.path, 'body': body,
                                  'ct': self.headers.get('Content-Type', '')})
                self.send_response(202)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def do_PUT(self):                        # noqa: N802
                n = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(n).decode('utf-8', 'replace')
                store_ref.append({'method': 'PUT', 'path': self.path, 'body': body,
                                  'ct': self.headers.get('Content-Type', '')})
                self.send_response(202)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.srv.server_address[1]

    def start(self):
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.srv.shutdown()


def main() -> int:
    nx = NxSink()
    mb_port = ModbusPort(listen_port=0, target_host='127.0.0.1', target_port=nx.modbus.port,
                         unit=1, forward='mirror', map_ranges=REG_MAP).start()
    pn_port = RtPort(listen_port=0, target_host='127.0.0.1',
                     target_port=nx.pn_port, slot_spec=SLOTS).start()
    print('NX  : modbus=%d  pn=%d  http=%d' % (nx.modbus.port, nx.pn_port, nx.http.port))
    print('端口: modbus %d→%d   profinet %d→%d' % (
        mb_port.listen_port, nx.modbus.port, pn_port.listen_port, nx.pn_port))

    # ---- 1) TIA 侧写寄存器（FC16）：转速/扭矩/计数/状态 ----
    t0 = time.time()
    tia = mb.ModbusClient('127.0.0.1', mb_port.listen_port)
    tia.write_registers(0, [1500, 42, 7, 1])
    ok_mb = wait_for(lambda: nx.modbus.registers.get(0) == 1500
                     and nx.modbus.registers.get(3) == 1, timeout=3)
    print('Modbus 转发:', 'OK' if ok_mb else 'FAIL',
          nx.modbus.registers, '%.2fs' % (time.time() - t0))

    # ---- 2) TIA 侧发 PROFINET RT 帧 ----
    slot_data = pn.build_slots(SLOTS, [0x000005DC, 0x0000002A])   # 1500 / 42
    with socket.create_connection(('127.0.0.1', pn_port.listen_port), timeout=3) as s:
        s.sendall(pn.encode(pn.RtFrame(0x8000, slot_data)))
    ok_pn = wait_for(lambda: bool(nx.pn_frames), timeout=3)
    print('PROFINET 转发:', 'OK' if ok_pn else 'FAIL',
          {k: [hex(x) for x in v] for k, v in (nx.pn_frames[-1] if nx.pn_frames else {}).items()})

    # ---- 3) POST 节点：把两侧数据汇总推给 NX 的 HTTP 入口 ----
    req = build_node_request(
        base_url='http://127.0.0.1:%d' % nx.http.port,
        url_path='/nx/ingest',
        method='POST',
        headers={'Content-Type': 'application/json'},
        body_template=json.dumps({'line': 'L1', 'rpm': 1500, 'torque': 42,
                                  'parts': 7, 'pn_slots': 2}),
        body_kind='json', timeout=(3, 5))
    res = RequestsTransport().send(req)
    ok_post = res.status == 202 and bool(nx.posts)
    print('POST 节点:', 'OK' if ok_post else 'FAIL',
          'status=%s via=%s' % (res.status, res.via),
          'sink=%s' % (nx.posts[-1]['body'][:60] if nx.posts else ''))

    # ---- 4) 统计 ----
    print('端口统计 modbus:', mb_port.stats.as_dict())
    print('端口统计 pn    :', pn_port.stats.as_dict())
    ok = ok_mb and ok_pn and ok_post
    print('RESULT:', 'PASS' if ok else 'FAIL')
    mb_port.stop()
    pn_port.stop()
    nx.stop()
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
