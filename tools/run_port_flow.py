# -*- coding: utf-8 -*-
"""执行端口节点图纸（不需要开界面）。

用法：
    python tools/run_port_flow.py                      # 只起端口节点（等外部 TIA/NX）
    python tools/run_port_flow.py --simulate           # 自建 TIA 仿真 + NX 机电模拟，闭环跑一遍
    python tools/run_port_flow.py --duration 10        # 跑 10 秒收报文（默认 3 秒）

图纸里的 PortNode 会被实例化成对应的 PortBridge；APINode 用真实 transport 发一次，
用来验证 POST/PUT 作为节点的链路（P1a）。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from portpanel.core.net import proxies_for                       # noqa: E402
from portpanel.core.request_builder import build_node_request    # noqa: E402
from portpanel.engine.ports import ModbusPort, RtPort, wait_for  # noqa: E402
from portpanel.engine.protocols import modbus as mb              # noqa: E402
from portpanel.engine.protocols import profinet as pn            # noqa: E402
from portpanel.engine.transport import RequestsTransport         # noqa: E402

DEFAULT_DRAWING = os.path.join(ROOT, 'data', 'webtree',
                               '工业链路演示_ModbusTCP_PROFINET.wbt')


class _Sink:
    """NX 机电模拟替身：Modbus 从站 + PN 接收 + HTTP 入口（固定端口，便于图纸直连）。"""

    def __init__(self, mb_port=15021, pn_port=15031, http_port=15022):
        self.modbus = mb.ModbusSlave(port=mb_port).start()
        self.pn_frames = []
        self.posts = []
        self._pn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._pn.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._pn.bind(('127.0.0.1', pn_port))
        self._pn.listen(4)
        import threading
        threading.Thread(target=self._pn_loop, daemon=True).start()

        store = self.posts

        class H(BaseHTTPRequestHandler):
            def do_POST(self):                       # noqa: N802
                n = int(self.headers.get('Content-Length') or 0)
                store.append(self.rfile.read(n).decode('utf-8', 'replace'))
                self.send_response(202)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *a):
                pass

        self.http = ThreadingHTTPServer(('127.0.0.1', http_port), H)
        import threading as _t
        _t.Thread(target=self.http.serve_forever, daemon=True).start()

    def _pn_loop(self):
        while True:
            try:
                conn, _ = self._pn.accept()
            except OSError:
                return
            with conn:
                try:
                    self.pn_frames.append(pn.read_frame(conn, [(1, 1, 4), (2, 1, 4)]))
                except Exception:
                    pass


def _tia_sim(mb_listen=15020, pn_listen=15030, slots=((1, 1, 4), (2, 1, 4))):
    """模拟博图侧：写寄存器 + 推一帧 PROFINET。"""
    mb.ModbusClient('127.0.0.1', mb_listen).write_registers(0, [1500, 42, 7, 1])
    with socket.create_connection(('127.0.0.1', pn_listen), timeout=3) as s:
        s.sendall(pn.encode(pn.RtFrame(0x8000, pn.build_slots(list(slots),
                                                             [0x000005DC, 0x0000002A]))))


def start_ports(nodes):
    started = []
    for nd in nodes:
        if nd.get('type') != 'PortNode':
            continue
        proto = nd.get('proto', 'modbus')
        _outs = nd.get('outputs') or []
        _tgt = [(o.get('host', '127.0.0.1'), int(o.get('port') or 0)) for o in _outs]
        kw = dict(listen_port=int(nd.get('listen_port') or 0),
                  target_host=nd.get('target_host') or (_tgt[0][0] if _tgt else '127.0.0.1'),
                  target_port=int(nd.get('target_port') or (_tgt[0][1] if _tgt else 0)),
                  targets=_tgt)
        if proto == 'modbus':
            impl = ModbusPort(unit=int(nd.get('unit') or 1),
                              forward=nd.get('forward', 'auto'),
                              map_ranges=[tuple(x) for x in nd.get('map_ranges') or []],
                              **kw)
        elif proto == 'profinet':
            impl = RtPort(frame_id=int(nd.get('frame_id') or 0x8000),
                          slot_spec=[tuple(x) for x in nd.get('slot_spec') or []],
                          **kw)
        else:
            from portpanel.engine.ports import RawPort
            impl = RawPort(**kw)
        impl.start()
        started.append((nd, impl))
        print('▶ 端口节点 [%s] %s  %d → %d' % (
            nd.get('name', proto), proto, impl.listen_port, impl.target_port))
    return started


def send_api_node(nd):
    base = (nd.get('api_ref') or {}).get('base_url', '')
    if not base:
        return None
    req = build_node_request(
        base_url=base, url_path=nd.get('url_path', '/'), method=nd.get('method', 'GET'),
        params=nd.get('params') or [], values={}, headers=nd.get('headers') or {},
        body_template=nd.get('body', ''), body_kind=nd.get('body_kind', 'none'),
        timeout=(5, 10))
    res = RequestsTransport().send(req)
    print('▶ API 节点 [%s %s] → %s (%s)' % (req.method, req.url, res.status, res.via))
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--drawing', default=DEFAULT_DRAWING)
    ap.add_argument('--simulate', action='store_true', help='自建 TIA 仿真 + NX 模拟并灌一遍数据')
    ap.add_argument('--duration', type=float, default=3.0)
    ap.add_argument('--api', action='store_true', help='顺带执行图纸里的 APINode（POST）')
    args = ap.parse_args(argv)

    with open(args.drawing, encoding='utf-8') as fh:
        flow = json.load(fh)
    nodes = flow.get('nodes') or []
    print('图纸:', os.path.basename(args.drawing), '| 节点', len(nodes),
          '| 连线', len(flow.get('connections') or []))

    sink = _Sink() if args.simulate else None
    started = start_ports(nodes)
    ok = True
    try:
        if args.simulate:
            time.sleep(0.2)
            _tia_sim()
            ok_mb = wait_for(lambda: sink.modbus.registers.get(0) == 1500, timeout=3)
            ok_pn = wait_for(lambda: bool(sink.pn_frames), timeout=3)
            print('  Modbus 到达 NX :', 'OK' if ok_mb else 'FAIL', sink.modbus.registers)
            print('  PN 帧到达 NX   :', 'OK' if ok_pn else 'FAIL',
                  [(f.frame_id, len(f.slots)) for f in sink.pn_frames])
            ok = ok and ok_mb and ok_pn
        if args.api or args.simulate:
            for nd in nodes:
                if nd.get('type') == 'APINode':
                    res = send_api_node(nd)
                    ok = ok and bool(res and res.status < 400)
            if sink is not None:
                print('  NX HTTP 收到   :', sink.posts[-1][:80] if sink.posts else '（无）')
                ok = ok and bool(sink.posts)
        if not args.simulate:
            time.sleep(args.duration)
    finally:
        for nd, impl in started:
            st = impl.stats.as_dict()
            print('■ 统计 [%s]: %s' % (nd.get('name', ''), st))
            ok = ok and st['errors'] == 0
            impl.stop()
        if sink is not None:
            sink.modbus.stop()
    print('RESULT:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
