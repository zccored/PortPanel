# -*- coding: utf-8 -*-
"""量出绘图区真实背景色，并算出"按背景预混后"的不透明网格色。"""
import os
import sys

sys.path.insert(0, os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.getcwd()))
os.chdir(sys.path[0])
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PORT_PANEL_NO_TOUR"] = "1"

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage, QPainter           # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsScene   # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
import portpanel.ui.flow_editor as FE                        # noqa: E402


class S:
    Yes, No, Ok = 1, 0, 1

    class _B:
        Yes, No, Ok = 1, 0, 1

    @staticmethod
    def information(*a, **k):
        return 1
    warning = critical = question = information


FE.QMessageBox = S

dlg = FE.FlowEditorDialog()
dlg.resize(1500, 940)
dlg.show()
for _ in range(20):
    app.processEvents()
tab = dlg._tabs[dlg._active_tab]
scene, view = tab["scene"], tab["view"]
vp = view.viewport()
dlg._load_flow_from(os.path.join(os.getcwd(), "data", "webtree", "demo.wbt"))
for _ in range(30):
    app.processEvents()

kind = type(scene)
orig = kind.drawBackground
kind.drawBackground = lambda self, p, r: QGraphicsScene.drawBackground(self, p, r)
for _ in range(5):
    vp.repaint()
img = QImage(vp.size(), QImage.Format_RGB32)
img.fill(QColor(255, 0, 255))
pt = QPainter(img)
pt.setRenderHint(QPainter.Antialiasing, False)
vp.render(pt, QPoint(0, 0))
pt.end()
kind.drawBackground = orig

w, h = vp.width(), vp.height()
cands = [(5, 5), (w - 5, 5), (5, h - 5), (w - 5, h - 5), (w // 2, 5)]
tally = {}
for x, y in cands:
    tally[img.pixelColor(x, y).name()] = tally.get(img.pixelColor(x, y).name(), 0) + 1
print("  视口取样点像素:", tally)
bgname = max(tally, key=tally.get)
bg = QColor(bgname)
print(f"  推断背景色 = {bgname}  RGB({bg.red()},{bg.green()},{bg.blue()})")


def blend(fg, alpha):
    r = round(bg.red() + (fg[0] - bg.red()) * alpha / 255)
    g = round(bg.green() + (fg[1] - bg.green()) * alpha / 255)
    b = round(bg.blue() + (fg[2] - bg.blue()) * alpha / 255)
    return r, g, b


mn = blend((220, 220, 220), 100)
mj = blend((180, 180, 180), 150)
print(f"  细线 QColor(220,220,220,100)  预混后 -> QColor({mn[0]},{mn[1]},{mn[2]})")
print(f"  粗线 QColor(180,180,180,150)  预混后 -> QColor({mj[0]},{mj[1]},{mj[2]})")
