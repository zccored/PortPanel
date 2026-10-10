# -*- coding: utf-8 -*-
"""端口画板 · 流畅度实测探针（不改任何业务代码）。

回答一个静态分析答不了的问题：**"卡"到底卡在哪一环**。

测四件事，各自给出 p50 / p95 / max：
  1. 加载图纸        —— `_load_flow_from()` 的耗时与场景规模
  2. 纯重绘          —— `viewport().repaint()`：节点 paint + drawBackground 的成本
  3. 平移/缩放       —— 模拟拖拽与滚轮：真正的交互手感
  4. 命中测试        —— `itemAt()`：鼠标移动时每帧都要做的那个查询

第 2 与第 3 分开测是关键：
  · 若「纯重绘」快而「平移」慢 → 慢在**事件处理/布局重算**，不在画
  · 若两者都慢              → 慢在**画**（节点数 × 每节点 paint 成本）
  · 若「命中测试」慢        → 慢在**场景索引**，鼠标一动就卡

用法：
    python perf_probe.py <项目根> [图纸名] [每个样本的重复次数]
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
os.environ["PORT_PANEL_NO_TOUR"] = "1"          # 别让引导层干扰计时

from PySide6.QtCore import QPoint, QPointF, Qt                  # noqa: E402
from PySide6.QtGui import QWheelEvent                           # noqa: E402
from PySide6.QtWidgets import QApplication                      # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import portpanel.ui.flow_editor as FE                          # noqa: E402


class _Silent:
    Yes, No, Ok = 1, 0, 1

    class _B:
        Yes, No, Ok = 1, 0, 1

    @staticmethod
    def information(*a, **k):
        return 1
    warning = critical = question = information


FE.QMessageBox = _Silent


def stats(name, samples):
    if not samples:
        print(f"  {name:<26} （无样本）")
        return None
    s = sorted(samples)
    p50 = statistics.median(s)
    p95 = s[min(len(s) - 1, int(len(s) * 0.95))]
    print(f"  {name:<26} n={len(s):<4} "
          f"p50={p50*1000:8.2f} ms  p95={p95*1000:8.2f} ms  max={s[-1]*1000:8.2f} ms")
    return p50


def timed(fn, repeat):
    out = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t)
    return out


def main():
    print("=" * 72)
    print(f"  端口画板 · 流畅度实测   图纸 = {FLOW}")
    print("=" * 72)

    dlg = FE.FlowEditorDialog()
    dlg.resize(1500, 940)
    dlg.show()
    for _ in range(20):
        app.processEvents()

    tab = dlg._tabs[dlg._active_tab]
    scene, view = tab["scene"], tab["view"]

    # ---------- 1. 加载图纸 ----------
    path = os.path.join(ROOT, "data", "webtree", FLOW)
    print(f"\n[1] 加载图纸  {path}")
    if not os.path.isfile(path):
        print("    ❌ 找不到图纸")
        return 2
    t0 = time.perf_counter()
    try:
        dlg._load_flow_from(path)
    except Exception as e:                                       # noqa: BLE001
        print(f"    ⚠ 加载抛异常（继续测）：{type(e).__name__}: {e}")
    load_s = time.perf_counter() - t0
    for _ in range(30):
        app.processEvents()

    items = scene.items()
    rects = [it for it in items if hasattr(it, "boundingRect")]
    conns = [it for it in items if hasattr(it, "update_path")]
    print(f"    加载耗时 = {load_s*1000:.1f} ms")
    print(f"    场景规模 = {len(items)} 个 item（{len(rects)} 可绘制 / {len(conns)} 连线）")
    sc = scene.sceneRect()
    print(f"    场景范围 = {sc.width():.0f} x {sc.height():.0f}")

    # ---------- 2. 纯重绘 ----------
    print("\n[2] 纯重绘（只画，不处理事件）")
    vp = view.viewport()
    stats("viewport.repaint()", timed(lambda: vp.repaint(), REPEAT))

    # ---------- 3. 平移 / 缩放 ----------
    print("\n[3] 平移 / 缩放（含事件处理与布局重算）")
    hbar, vbar = view.horizontalScrollBar(), view.verticalScrollBar()

    def pan():
        hbar.setValue(hbar.value() + 37)
        vbar.setValue(vbar.value() + 23)
        app.processEvents()

    stats("平移一步", timed(pan, REPEAT))

    def zoom():
        ev = QWheelEvent(QPointF(vp.rect().center()), QPointF(vp.mapToGlobal(vp.rect().center())),
                         QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                         Qt.ScrollUpdate, False)
        view.wheelEvent(ev)
        app.processEvents()

    stats("缩放一步", timed(zoom, REPEAT))

    # ---------- 4. 命中测试 ----------
    print("\n[4] 命中测试（鼠标移动每帧都要做）")
    c = vp.rect().center()

    def hit():
        scene.itemAt(view.mapToScene(c), view.transform())

    stats("scene.itemAt()", timed(hit, REPEAT * 5))

    # ---------- 5. 单项 paint 成本（谁最贵） ----------
    print("\n[5] 各类 item 数量（数量 × 单项成本 = 总成本）")
    kinds = {}
    for it in items:
        k = type(it).__name__
        kinds[k] = kinds.get(k, 0) + 1
    for k, n in sorted(kinds.items(), key=lambda x: -x[1])[:12]:
        print(f"    {k:<32} {n:>5} 个")

    print("\n" + "=" * 72)
    print("  读法：")
    print("   · [2] 快而 [3] 慢  -> 慢在事件处理/布局，不在画")
    print("   · [2][3] 都慢      -> 慢在画（看 [5] 哪类 item 多）")
    print("   · [4] 慢           -> 慢在场景索引，鼠标一动就卡")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
