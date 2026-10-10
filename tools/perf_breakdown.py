# -*- coding: utf-8 -*-
"""对照实验：把重绘成本拆到具体来源。

同一场景，逐个"关掉一样东西"再测重绘，差值就是那一样的成本：
  A 基线
  B 隐藏 QGraphicsProxyWidget  -> 差值 = 代理控件的成本
  C 让 drawBackground 空转      -> 差值 = 背景网格的成本
  D 隐藏 ConnectionPath         -> 差值 = 连线的成本
  E 隐藏全部节点               -> 差值 = 节点的成本

这样能直接指出"该优化谁"，不用猜。
"""
from __future__ import annotations

import os
import statistics
import sys
import time

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.getcwd())
FLOW = sys.argv[2] if len(sys.argv) > 2 else "demo.wbt"
REPEAT = int(sys.argv[3]) if len(sys.argv) > 3 else 40

sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PORT_PANEL_NO_TOUR"] = "1"

from PySide6.QtWidgets import QApplication, QGraphicsProxyWidget      # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
import portpanel.ui.flow_editor as FE                                # noqa: E402


class _Silent:
    Yes, No, Ok = 1, 0, 1

    class _B:
        Yes, No, Ok = 1, 0, 1

    @staticmethod
    def information(*a, **k):
        return 1
    warning = critical = question = information


FE.QMessageBox = _Silent


def measure(vp, label, repeat=REPEAT):
    for _ in range(3):
        vp.repaint()
    xs = []
    for _ in range(repeat):
        t = time.perf_counter()
        vp.repaint()
        xs.append(time.perf_counter() - t)
    s = sorted(xs)
    p50 = statistics.median(s)
    p95 = s[min(len(s) - 1, int(len(s) * 0.95))]
    print(f"  {label:<38} p50={p50*1000:7.2f} ms   p95={p95*1000:7.2f} ms")
    return p50


def main():
    print("=" * 74)
    print(f"  重绘成本拆解   图纸 = {FLOW}")
    print("=" * 74)

    dlg = FE.FlowEditorDialog()
    dlg.resize(1500, 940)
    dlg.show()
    for _ in range(20):
        app.processEvents()
    tab = dlg._tabs[dlg._active_tab]
    scene, view = tab["scene"], tab["view"]
    vp = view.viewport()

    path = os.path.join(ROOT, "data", "webtree", FLOW)
    dlg._load_flow_from(path)
    for _ in range(30):
        app.processEvents()

    items = list(scene.items())
    proxies = [i for i in items if isinstance(i, QGraphicsProxyWidget)]
    conns = [i for i in items if type(i).__name__ == "ConnectionPath"]
    nodes = [i for i in items
             if i not in proxies and type(i).__name__ != "ConnectionPath"]
    print(f"  场景 = {len(items)} item  "
          f"（代理控件 {len(proxies)} / 连线 {len(conns)} / 其它节点 {len(nodes)}）\n")

    res = {}
    res["基线"] = measure(vp, "A 基线（全开）")

    for p in proxies:
        p.setVisible(False)
    res["代理控件"] = measure(vp, "B 隐藏 QGraphicsProxyWidget")
    base = res["基线"]
    print(f"      -> 代理控件成本 ≈ {(base - res['代理控件'])*1000:.2f} ms "
          f"（占基线 {100.0*(base-res['代理控件'])/base:.0f}%）")
    for p in proxies:
        p.setVisible(True)

    real_bg = type(scene).drawBackground
    type(scene).drawBackground = lambda self, painter, rect: None
    res["背景"] = measure(vp, "C drawBackground 空转")
    print(f"      -> 背景网格成本 ≈ {(base - res['背景'])*1000:.2f} ms "
          f"（占基线 {100.0*(base-res['背景'])/base:.0f}%）")
    type(scene).drawBackground = real_bg

    for c in conns:
        c.setVisible(False)
    res["连线"] = measure(vp, "D 隐藏 ConnectionPath")
    print(f"      -> 连线成本 ≈ {(base - res['连线'])*1000:.2f} ms "
          f"（占基线 {100.0*(base-res['连线'])/base:.0f}%）")
    for c in conns:
        c.setVisible(True)

    for n in nodes:
        n.setVisible(False)
    res["节点"] = measure(vp, "E 隐藏全部节点")
    print(f"      -> 节点成本 ≈ {(base - res['节点'])*1000:.2f} ms "
          f"（占基线 {100.0*(base-res['节点'])/base:.0f}%）")
    for n in nodes:
        n.setVisible(True)

    print("\n" + "=" * 74)
    print("  结论：上面『成本 ≈』最大的一项就是首要优化目标。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
