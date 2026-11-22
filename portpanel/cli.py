# -*- coding: utf-8 -*-
"""PortPanel 命令行接口（headless，给外部工具/脚本调用）。

    python -m portpanel.cli nodes  <drawing.wbt>
    python -m portpanel.cli exec   <drawing.wbt> --match /x/relation/followings --set "ps=5;pn=1"
    python -m portpanel.cli exec   <drawing.wbt> --match /x/polymer --sign
    python -m portpanel.cli sign   --query "host_mid=2&offset=&timezone_offset=-480"
    python -m portpanel.cli run    <drawing.wbt> --max-seconds 60

约定：
- 自动 `QT_QPA_PLATFORM=offscreen`，不会弹窗；
- Cookie/密钥从环境变量或 data/secrets.local.json 取，**只打印长度**；
- 退出码：0 成功；1 业务失败（code≠0）；2 参数/文件问题。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QMessageBox
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    return QApplication.instance() or QApplication(sys.argv)


def _load(drawing):
    _app()
    from portpanel.ui.flow_editor import FlowEditorDialog
    if not os.path.exists(drawing):
        raise FileNotFoundError(drawing)
    dlg = FlowEditorDialog()
    dlg._load_flow_from(drawing)
    return dlg


def _parse_set(spec: str) -> dict:
    out = {}
    for part in (spec or '').replace(',', ';').split(';'):
        if '=' in part:
            k, v = part.split('=', 1)
            out[k.strip()] = v.strip()
    return out


def cmd_nodes(args) -> int:
    dlg = _load(args.drawing)
    from portpanel.ui.flow_editor import APINode
    rows = []
    for i, n in enumerate(dlg.scene.nodes):
        t = type(n).__name__
        if isinstance(n, APINode):
            rows.append((i, t, n.method, n.url_path,
                         sum(1 for p in n.params if p.get('location') == 'query')))
        else:
            rows.append((i, t, '', getattr(n, 'class_name', '') or '', 0))
    for i, t, m, p, q in rows:
        print('%3d  %-16s %-6s %s%s' % (i, t, m, p, ('  [query:%d]' % q) if q else ''))
        if args.json:
            pass
    if args.json:
        print(json.dumps([{'i': i, 'type': t, 'method': m, 'path': p}
                          for i, t, m, p, _q in rows], ensure_ascii=False))
    return 0


def _node_by_match(dlg, match):
    from portpanel.ui.flow_editor import APINode
    for n in dlg.scene.nodes:
        if isinstance(n, APINode) and match in (n.url_path or ''):
            return n
    return None


def cmd_exec(args) -> int:
    dlg = _load(args.drawing)
    node = _node_by_match(dlg, args.match)
    if node is None:
        print('没找到匹配 %s 的节点' % args.match)
        return 2

    for p in node.params:
        if p['name'] in args.overrides:
            p['value'] = args.overrides[p['name']]

    if args.sign:
        names = {p['name'] for p in node.params}
        if {'wts', 'w_rid'} & names:
            import requests
            q = '&'.join('%s=%s' % (p['name'], p['value']) for p in node.params
                         if p['name'] not in ('wts', 'w_rid'))
            j = requests.get('http://127.0.0.1:15200/wbi', params={'query': q},
                             timeout=15).json()
            for p in node.params:
                if p['name'] == 'wts':
                    p['value'] = str(j['wts'])
                elif p['name'] == 'w_rid':
                    p['value'] = j['w_rid']
            print('WBI 签名 ok: wts=%s' % j['wts'])

    res = dlg._execute_api_node(node, 0)
    data = res if isinstance(res, dict) else None
    if data is None:
        try:
            data = json.loads(getattr(res, 'text', '') or '')
        except Exception:
            data = None
    if not isinstance(data, dict):
        print('返回类型异常:', type(res))
        return 1
    payload = data.get('data') or {}
    items = payload.get('list') or payload.get('items') or []
    print(json.dumps({'code': data.get('code'), 'message': data.get('message'),
                      'count': len(items),
                      'total': payload.get('total'),
                      'offset': payload.get('offset'),
                      'has_more': payload.get('has_more')}, ensure_ascii=False))
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        print('已保存', args.out)
    return 0 if data.get('code') == 0 else 1


def cmd_sign(args) -> int:
    sys.path.insert(0, ROOT)
    from tools.bili_wbi_helper import sign
    j = sign(args.query)
    print(json.dumps(j, ensure_ascii=False))
    return 0


def cmd_run(args) -> int:
    dlg = _load(args.drawing)
    if not hasattr(dlg, 'run_flow'):
        print('该画板版本不支持 run')
        return 2
    dlg.run_flow()
    print('run_flow 已触发（GUI 版为阻塞执行；CLI 建议用 exec 单节点）')
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='portpanel.cli')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('nodes', help='列出图纸节点')
    p.add_argument('drawing')
    p.add_argument('--json', action='store_true')
    p.set_defaults(func=cmd_nodes)

    p = sub.add_parser('exec', help='执行单个 APINode')
    p.add_argument('drawing')
    p.add_argument('--match', required=True)
    p.add_argument('--set', default='')
    p.add_argument('--sign', action='store_true')
    p.add_argument('--out', default='')
    p.set_defaults(func=cmd_exec)

    p = sub.add_parser('sign', help='WBI 签名（本地助手）')
    p.add_argument('--query', required=True)
    p.set_defaults(func=cmd_sign)

    p = sub.add_parser('run', help='整图执行（GUI 引擎）')
    p.add_argument('drawing')
    p.set_defaults(func=cmd_run)

    args = ap.parse_args(argv)
    args.overrides = _parse_set(getattr(args, 'set', ''))
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
