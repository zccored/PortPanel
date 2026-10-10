# -*- coding: utf-8 -*-
"""决定性实验：网格的成本是不是"半透明混合"造成的？

同一场景、同样的线数与几何，只改**笔的颜色透明度**再测重绘：
  A 原样（alpha=100 / 150，半透明）
  B 同色但不透明（alpha=255）        <- 若明显变快，则成本 = 逐像素 alpha 混合
  C 只画大网格（100px），不画小网格   <- 若大幅变快，则成本 = 像素总数
  D 完全不画                         <- 地板值

三个对照合起来能把"4 ms 花在哪"钉死。
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

from PySide6.QtCore import QLineF, Qt                            # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPen                 # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
import portpanel.ui.flow_editor as FE                           # noqa: E402


class _Silent:
    Yes, No, Ok = 1, 0, 1

    class _B:
        Yes, No, Ok = 1, 0, 1

    @staticmethod
    def information(*a, **k):
        return 1
    warning = critical = question = information


FE.QMessageBox = _Silent
NodeScene = None


def grid_impl(minor_alpha, major_alpha, skip_minor=False):
    """生成一个 drawBackground 实现，几何与原版逐点一致，只改透明度/是否画小网格。"""
    def impl(self, painter, rect):
        QGraphicsScene_drawBackground(self, painter, rect)
        painter.setRenderHint(QPainter.Antialiasing, False)

        def lines(step):
            xs, ys = [], []
            x = int(rect.left() // step) * step
            right, bottom = rect.right(), rect.bottom()
            while x < right:
                xs.append(x)
                x += step
            y = int(rect.top() // step) * step
            while y < bottom:
                ys.append(y)
                y += step
            top, left = rect.top(), rect.left()
            return ([QLineF(x, top, x, bottom) for x in xs]
                    + [QLineF(left, y, right, y) for y in ys])

        if not skip_minor:
            p = QPen(QColor(220, 220, 220, minor_alpha))
            p.setWidthF(0.5)
            painter.setPen(p)
            painter.drawLines(lines(20))
        p = QPen(QColor(180, 180, 180, major_alpha))
        p.setWidthF(1.0)
        painter.setPen(p)
        painter.drawLines(lines(100))
    return impl


QGraphicsScene_drawBackground = None


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
    print(f"  {label:<40} p50={p50*1000:7.2f} ms   p95={p95*1000:7.2f} ms")
    return p50


def main():
    global QGraphicsScene_drawBackground
    from PySide6.QtWidgets import QGraphicsScene
    QGraphicsScene_drawBackground = QGraphicsScene.drawBackground

    print("=" * 78)
    print("  网格成本归因实验")
    print("=" * 78)

    dlg = FE.FlowEditorDialog()
    dlg.resize(1500, 940)
    dlg.show()
    for _ in range(20):
        app.processEvents()
    tab = dlg._tabs[dlg._active_tab]
    scene, view = tab["scene"], tab["view"]
    vp = view.viewport()
    dlg._load_flow_from(os.path.join(ROOT, "data", "webtree", FLOW))
    for _ in range(30):
        app.processEvents()

    kind = type(scene)
    orig = kind.drawBackground

    # 先量一下"完全不画"的地板
    kind.drawBackground = lambda self, painter, rect: QGraphicsScene_drawBackground(
        self, painter, rect)
    floor = measure(vp, "D 完全不画网格（地板）")

    kind.drawBackground = grid_impl(100, 150)
    base = measure(vp, "A 原样（alpha 100/150）")

    kind.drawBackground = grid_impl(255, 255)
    opaque = measure(vp, "B 同色但不透明（alpha 255）")

    kind.drawBackground = grid_impl(100, 150, skip_minor=True)
    major_only = measure(vp, "C 只画大网格（线条数 ÷ ~6）")

    kind.drawBackground = orig

    print("\n" + "=" * 78)
    print(f"  网格总成本           = {(base-floor)*1000:6.2f} ms  （占基线 {100*(base-floor)/base:.0f}%）")
    print(f"  其中『半透明混合』   = {(base-opaque)*1000:6.2f} ms  <- B 与 A 的差")
    print(f"  其中『像素总数』     = {(base-major_only)*1000:6.2f} ms  <- C 与 A 的差（小网格那部分）")
    print("=" * 78)
    print("  判读：")
    print("   · 若『半透明混合』占大头 -> 把网格颜色按背景预混成不透明即可，视觉不变")
    print("   · 若『像素总数』占大头   -> 要么降密度，要么做网格贴图缓存")
    return 0


if __name__ == "__main__":
    sys.exit(main())
