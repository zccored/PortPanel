# -*- coding: utf-8 -*-
"""生成工业链路测试图纸：`data/webtree/工业链路演示_ModbusTCP_PROFINET.wbt`

图纸结构（左→右）：

    [PortNode modbus 15020 → 15021]  ─┐
                                       ├─▶ [APINode POST /nx/ingest]
    [PortNode profinet 15030 → 15031] ─┘

- 两个端口节点代表「TIA 暴露的 Modbus TCP / PROFINET」入口，各自转发到 NX 机电模拟；
- POST 节点把汇总写进 NX 的 HTTP 入口（验证 P1a 的 POST/PUT 作为节点）。

注意：端口节点当前由 `tools/run_port_flow.py` 执行（画布渲染与属性面板是下一刀）。
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'webtree', '工业链路演示_ModbusTCP_PROFINET.wbt')


def build() -> dict:
    nodes = [
        {
            'type': 'PortNode', 'pos_x': 80, 'pos_y': 120, 'name': 'TIA.Modbus',
            'proto': 'modbus', 'listen_port': 15020,
            'target_host': '127.0.0.1', 'target_port': 15021,
            'unit': 1, 'forward': 'mirror', 'map_ranges': [[0, 4]],
        },
        {
            'type': 'PortNode', 'pos_x': 80, 'pos_y': 320, 'name': 'TIA.Profinet',
            'proto': 'profinet', 'listen_port': 15030,
            'target_host': '127.0.0.1', 'target_port': 15031,
            'frame_id': 32768, 'slot_spec': [[1, 1, 4], [2, 1, 4]],
        },
        {
            'type': 'APINode', 'pos_x': 460, 'pos_y': 220,
            'method': 'POST', 'url_path': '/nx/ingest',
            # POST 是末端发信节点：参数即「输入点」，上游端口节点把值送到这里再推送
            'params': [
                {'name': 'rpm', 'value': '1500', 'location': 'body'},
                {'name': 'torque', 'value': '42', 'location': 'body'},
                {'name': 'parts', 'value': '7', 'location': 'body'},
            ], 'headers': {'Content-Type': 'application/json'},
            'result_json': '', 'concat_widgets': [],
            'api_ref': {'name': 'NX_MCD', 'base_url': 'http://127.0.0.1:15022',
                        'endpoints': []},
            'body': json.dumps({'line': 'L1', 'source': 'tia',
                                'rpm': 1500, 'torque': 42, 'parts': 7}),
            'body_kind': 'json',
        },
    ]
    connections = [
        {'start_node': 0, 'end_node': 2, 'end_param': 'rpm'},
        {'start_node': 1, 'end_node': 2, 'end_param': 'torque'},
    ]
    return {'version': 1, 'nodes': nodes, 'connections': connections, 'classes': []}


def main() -> int:
    data = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print('WROTE', OUT)
    print('nodes=%d connections=%d' % (len(data['nodes']), len(data['connections'])))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
