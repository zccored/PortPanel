# -*- coding: utf-8 -*-
"""离屏跑图纸并“自己收尾”：固定超时 + 独立日志 + JSON 摘要。

为什么有这个脚本：
    以前用 `sleep 100` 在会话里等后台进程，会话一被截断子进程就被杀，
    结果既没有结果也没有日志。这个脚本会在 N 秒后**自己取消并退出**，
    所以一次调用必定在 N+安全余量 内返回，且每次日志都是独立文件名。

用法：
    python tools/run_drawing_probe.py --drawing data/webtree/xxx.wbt --seconds 35

退出码：
    0 = 正常收尾（跑够时间或被图纸自身结束）
    2 = 图纸加载失败 / 没有可执行分组
    3 = 超时后仍未退出（被强制结束）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

RE_REQ = re.compile(r'▶ 请求 \[[^\]]+\]: (\w+) (\S+)')
RE_FAIL = re.compile(r'✕ 请求失败')
RE_STATUS = re.compile(r'"Bili-Status-Code": "(-?\d+)"')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--drawing', required=True)
    ap.add_argument('--seconds', type=int, default=35, help='跑多久后自动取消')
    ap.add_argument('--out', default='', help='日志路径（默认 .private/probe_<时间戳>.log）')
    ap.add_argument('--trace', action='store_true', help='统计各执行函数的调用次数（不改源码）')
    args = ap.parse_args()

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    for _name in ('warning', 'information', 'critical'):
        setattr(QMessageBox, _name, staticmethod(lambda *a, **k: None))
    app = QApplication(sys.argv)

    from portpanel.ui.flow_editor import FlowEditorDialog

    stamp = time.strftime('%Y%m%d_%H%M%S')
    log_path = args.out or os.path.join(ROOT, '.private', 'probe_%s.log' % stamp)
    fh = open(log_path, 'w', encoding='utf-8', newline='\n')

    counters: dict[str, int] = {}
    if args.trace:
        from portpanel.ui import flow_editor as _fe
        for _nm in ('_execute_api_node', '_process_api_result', '_download_batch',
                    '_compute_api_url', '_resolve_array_values'):
            _fn = getattr(_fe.FlowEditorDialog, _nm, None)
            if _fn is None:
                continue

            def _mk(fn=_fn, nm=_nm):
                def _inner(self, *a, **k):
                    counters[nm] = counters.get(nm, 0) + 1
                    return fn(self, *a, **k)
                return _inner
            setattr(_fe.FlowEditorDialog, _nm, _mk())

    dlg = FlowEditorDialog()
    try:
        dlg._load_flow_from(args.drawing)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({'ok': False, 'stage': 'load', 'error': str(exc)[:200]},
                         ensure_ascii=False))
        return 2

    lines: list[str] = []
    _orig = dlg.api_log_text.append

    def _append(text):
        _orig(text)
        lines.append(str(text))
        fh.write(str(text) + '\n')
        fh.flush()

    dlg.api_log_text.append = _append

    groups = [(g[0], len(g[1])) for g in dlg._build_group_tree(1, False)]
    if not groups:
        print(json.dumps({'ok': False, 'stage': 'groups', 'error': '未检测到可执行分组'},
                         ensure_ascii=False))
        return 2

    # 到时取消；再给 20 秒收尾时间，否则硬退
    QTimer.singleShot(args.seconds * 1000, lambda: setattr(dlg, '_inner_cancel', True))
    QTimer.singleShot(args.seconds * 1000 + 20000, lambda: os._exit(3))

    started = time.time()
    dlg.run_flow()
    elapsed = round(time.time() - started, 1)

    reqs = RE_REQ.findall('\n'.join(lines))
    paths: dict[str, int] = {}
    for _m, url in reqs:
        key = url.split('?')[0]
        paths[key] = paths.get(key, 0) + 1
    summary = {
        'ok': True,
        'drawing': os.path.basename(args.drawing),
        'seconds': args.seconds,
        'elapsed': elapsed,
        'groups': groups,
        'requests': len(reqs),
        'by_path': dict(sorted(paths.items(), key=lambda kv: -kv[1])[:8]),
        'request_failures': len(RE_FAIL.findall('\n'.join(lines))),
        'bili_status': sorted(set(RE_STATUS.findall('\n'.join(lines)))),
        'log': log_path,
        'trace': counters if args.trace else None,
    }
    fh.write('\n=== SUMMARY ===\n' + json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    fh.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
