"""离屏冒烟测试：NodeHoverTip 的 thinking → expand 全流程 + 锚点计算。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QGraphicsScene
from PySide6.QtGui import QPixmap
from PySide6.QtCore import QRect

app = QApplication([])
import portpanel.ui.flow_editor as fe

# 1) 浮窗本体：thinking → 展开（含淡入）
tip = fe.NodeHoverTip()
tip.show_thinking(100, 100)
app.processEvents()
assert tip.isVisible(), "thinking 阶段应该可见"

pm = QPixmap(320, 240)
pm.fill()
tip.expand_to(120, 140, pm.width() + 20, pm.height() + 20, pm)
app.processEvents()
assert tip.isVisible(), "展开后应可见"
assert tip._has_svg, "应进入 SVG 状态"
assert tip._fade.opacity() >= 0.0, "淡入效果应存在"
print(f"[1] 浮窗 OK  size={tip.width()}x{tip.height()}  fade={tip._fade.opacity():.2f}")

# 动画推进到结束
for _ in range(40):
    app.processEvents()
    import time
    time.sleep(0.02)
print(f"[2] 展开结束 _expanding={tip._expanding}  geometry={tip.geometry()}")

# 2) 隐藏复位
tip.hide_tip()
app.processEvents()
assert not tip.isVisible(), "hide_tip 后应隐藏"
print("[3] 隐藏复位 OK")

# 3) 锚点计算（用假 item 的 mapRectToScene / boundingRect）
class FakeRect:
    def __init__(self, r):
        self._r = r

    def topLeft(self):
        return self._r.topLeft()

    def bottomRight(self):
        return self._r.bottomRight()


class FakeItem:
    def boundingRect(self):
        return QRect(0, 0, 200, 120)

    def mapRectToScene(self, r):
        return QRect(300, 200, r.width(), r.height())


view = fe.NodeView(QGraphicsScene())
x, y = view._hover_anchor(FakeItem(), 320, 240)
print(f"[4] 锚点 = ({x}, {y})   （应贴在元素框右侧 16px）")
assert isinstance(x, int) and isinstance(y, int)

print("SMOKE OK")
