# -*- coding: utf-8 -*-
"""元素框右下角的断点开关（空心红点 → 触发后实心）。

编辑者要求：位置在**请求头按钮下面**、元素框右下角，尺寸小，未触发时空心。
用 QLabel + 样式圆点实现：挂在节点的布局里，`_build_ui()` 重建时跟着重建，不会丢。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QHBoxLayout, QWidget

__all__ = ['BreakpointDot']


class BreakpointDot(QLabel):
    toggled = Signal(bool)

    SIZE = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._armed = False
        self._triggered = False
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip('断点：空心=关闭，实心=已开启；命中时闪烁')
        self._refresh()

    # ---- 状态 ----
    @property
    def armed(self) -> bool:
        return self._armed

    def set_armed(self, on: bool):
        self._armed = bool(on)
        self._refresh()

    def set_triggered(self, on: bool):
        self._triggered = bool(on)
        self._refresh()

    def _refresh(self):
        # 编辑者口径：**不触发 = 灰色空心；触发（开启）= 实心红**
        red = '#e05252'
        grey = '#8b98a8'
        if self._armed:
            css = 'background:%s;border:1px solid %s;border-radius:%dpx;' % (
                red, red, self.SIZE // 2)
        else:
            css = ('background:transparent;border:1px solid %s;'
                   'border-radius:%dpx;' % (grey, self.SIZE // 2))
        if self._triggered:
            css += 'border:2px solid #ffb3b3;'
        self.setStyleSheet('QLabel{%s}' % css)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.set_armed(not self._armed)
            self.toggled.emit(self._armed)
            event.accept()
            return
        super().mousePressEvent(event)


def attach_dot(node, layout: QHBoxLayout, on_toggle=None):
    """在节点布局末尾（请求头按钮下方一行）挂一个右下角对齐的红点。"""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.addStretch()
    dot = BreakpointDot()
    if on_toggle is not None:
        dot.toggled.connect(on_toggle)
    row.addWidget(dot, 0, Qt.AlignRight | Qt.AlignBottom)
    layout.addLayout(row)
    return dot
