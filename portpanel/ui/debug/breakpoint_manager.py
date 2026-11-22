# -*- coding: utf-8 -*-
"""断点管理面板（= 节点观察器）：列出画布上**所有**可设断点的元素框。

与「元素框详情（InspectPanel）」同一套样式：无边框圆角、深色底、蓝边、页眉可拖动。

和旧版的区别
------------
旧版只列“已开启断点”的节点；现在**全部列出**，没开的显示灰色空心圆点，
相当于一个观察器：一眼看到画布上有哪些 API 元素框、谁开了断点、点一下就能切换。

交互
----
* 行首圆点：实心红 = 已开断点，灰色空心 = 未开；点一下切换（立即生效）
* 「查看」：打开该元素框的详情面板
* 底部「全部关闭」：把已开启的断点全部关掉
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

__all__ = ['BreakpointManagerPanel']

RADIUS = 12
MARGIN = 10
ROW_H = 26          # 单行高度（含间距）
MAX_ROWS = 8        # 超过这个行数才出现滚动条

_DOT_ON = ('QPushButton{background:#e05252;border:1px solid #e05252;border-radius:7px;}')
_DOT_OFF = ('QPushButton{background:transparent;border:1px solid #8b98a8;border-radius:7px;}')


class BreakpointManagerPanel(QWidget):
    RADIUS = RADIUS
    MARGIN = MARGIN

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle('断点管理')
        self._drag = None
        self._rows_host = QWidget()
        self._host = parent                      # FlowEditorDialog（用它拿 scene）

        outer = QVBoxLayout(self)
        outer.setContentsMargins(self.MARGIN, self.MARGIN - 2, self.MARGIN, self.MARGIN)
        outer.setSpacing(6)

        # ---- 页眉（可拖动）----
        self._header = QWidget()
        head = QHBoxLayout(self._header)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        dot = QLabel()
        dot.setFixedSize(14, 14)
        dot.setStyleSheet('QLabel{background:#e05252;border:1px solid #e05252;'
                          'border-radius:7px;}')
        head.addWidget(dot)
        self.title = QLabel('断点管理')
        self.title.setStyleSheet('color:#ffd7d7; font-weight:bold;')
        head.addWidget(self.title)
        self.count_label = QLabel('')
        self.count_label.setStyleSheet('color:#8b98a8; font-size:11px;')
        head.addWidget(self.count_label)
        head.addStretch()
        self.close_btn = QPushButton('✕')
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.setStyleSheet(
            'QPushButton{background:transparent;color:#c8d3e0;border:none;font-size:13px;}'
            'QPushButton:hover{color:#ffffff;}')
        self.close_btn.clicked.connect(self.hide)
        head.addWidget(self.close_btn)
        outer.addWidget(self._header)
        self._header.installEventFilter(self)

        hint = QLabel('圆点＝该元素框的断点开关（实心=已开）；「查看」打开它的详情面板')
        hint.setStyleSheet('color:#8b98a8;font-size:11px;')
        outer.addWidget(hint)

        # ---- 列表 ----
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        # 透明到底：面板自己是无边框透明窗口，滚动区/视口/内容层只要有 palette 底色
        # 就会在深色背景上刷出一块白（8 条以上出现滚动条时最明显）
        self._scroll.setStyleSheet(
            'QScrollArea{background:transparent;border:none;}'
            'QScrollArea > QWidget > QWidget{background:transparent;}'
            'QScrollArea > QWidget > QWidget > QWidget{background:transparent;}')
        self._scroll.viewport().setAutoFillBackground(False)
        self._rows_host.setAutoFillBackground(False)
        self._rows_host.setAttribute(Qt.WA_TranslucentBackground, True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.verticalScrollBar().setStyleSheet(
            'QScrollBar:vertical{background:transparent;width:8px;margin:0;}'
            'QScrollBar::handle:vertical{background:#3a5a8a;border-radius:4px;min-height:24px;}'
            'QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}'
            'QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}')
        self._rows = QVBoxLayout(self._rows_host)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(2)
        self._rows.addStretch()
        self._scroll.setWidget(self._rows_host)
        outer.addWidget(self._scroll, 1)

        # ---- 底部 ----
        foot = QHBoxLayout()
        foot.addStretch()
        self.clear_btn = QPushButton('全部关闭')
        self.clear_btn.setStyleSheet(
            'QPushButton{background:#3a1e1e;color:#ffb3b3;border:1px solid #e05252;'
            'border-radius:4px;padding:4px 10px;font-size:11px;}'
            'QPushButton:hover{background:#4a2626;}')
        self.clear_btn.clicked.connect(self._clear_all)
        foot.addWidget(self.clear_btn)
        outer.addLayout(foot)

        self._win_w = 560
        self.resize(self._win_w, 300)
        # 贴边自动隐藏（与详情面板同一套行为）
        try:
            from portpanel.ui.edge_dock import EdgeDock
            self._edge = EdgeDock(self).attach()
        except Exception:
            self._edge = None
        self._refresh_pending = False
        try:
            from portpanel.ui.debug.bridge import get_bridge
            get_bridge().armedChanged.connect(lambda _n: self.refresh())
        except Exception:
            pass
        # 观察器要跟画板一致：节点增删/图纸切换也跟着刷新
        try:
            sc = getattr(parent, 'scene', None)
            if sc is not None and hasattr(sc, 'changed'):
                sc.changed.connect(self._on_scene_changed)
        except Exception:
            pass

    # ---------- 外观 ----------
    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(0, 0, -1, -1)
        p.setPen(QPen(QColor(90, 170, 255, 200), 1))
        p.setBrush(QColor(16, 22, 32, 246))
        p.drawRoundedRect(rect, self.RADIUS, self.RADIUS)
        p.end()

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._header:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if event.type() == QEvent.MouseMove and self._drag is not None:
                self.move(event.globalPosition().toPoint() - self._drag)
                return True
            if event.type() == QEvent.MouseButtonRelease:
                self._drag = None
                if getattr(self, '_edge', None) is not None:
                    self._edge.on_drag_end()
                return True
        return super().eventFilter(obj, event)

    # ---------- 数据 ----------
    def _scene(self):
        return getattr(self._host, 'scene', None)

    def _nodes(self):
        """画布上所有“可以设断点”的元素框（当前是 APINode）。"""
        try:
            from portpanel.ui.flow_editor import APINode
            sc = self._scene()
            return [n for n in getattr(sc, 'nodes', []) if isinstance(n, APINode)]
        except Exception:
            return []

    def _armed(self, node) -> bool:
        try:
            from portpanel.ui.debug.bridge import get_bridge
            return get_bridge().is_armed(node)
        except Exception:
            return False

    def _on_scene_changed(self, *_a):
        """画布变化：合并成一次刷新（拖动节点时不要每帧重建）。"""
        if not self.isVisible() or self._refresh_pending:
            return
        self._refresh_pending = True
        try:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(150, self._do_pending_refresh)
        except Exception:
            self._refresh_pending = False

    def _do_pending_refresh(self):
        self._refresh_pending = False
        self.refresh()

    def refresh(self):
        """按画布真实情况重建列表（列全部元素框，不只已开断点的）。"""
        while self._rows.count() > 1:
            item = self._rows.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        nodes = self._nodes()
        armed_n = sum(1 for n in nodes if self._armed(n))
        self.count_label.setText('共 %d 个 · 已开启 %d 个' % (len(nodes), armed_n))

        if not nodes:
            empty = QLabel('画布上还没有 API 元素框。')
            empty.setStyleSheet('color:#8b98a8;font-size:11px;')
            empty.setWordWrap(True)
            self._rows.insertWidget(0, empty)
            self._fit_height(1)
            return
        for i, node in enumerate(nodes):
            self._rows.insertWidget(i, self._make_row(node, self._armed(node)))
        self._fit_height(len(nodes))

    def _fit_height(self, n_rows: int):
        """行数 ≤ MAX_ROWS 时窗口自适应内容；超过则固定 8 行高，内部滚动。"""
        visible = max(1, min(n_rows, MAX_ROWS))
        h = visible * ROW_H + 2
        self._scroll.setMinimumHeight(h)
        self._scroll.setMaximumHeight(h)
        # 内容撑开的真实高度（>8 条时它比可视区高，滚动条才会出现）
        self._rows_host.setMinimumHeight(max(h, n_rows * ROW_H))
        # 让布局按 sizeHint 收紧/放宽窗口高度（宽度保持固定，避免抖动）
        self.resize(self._win_w, self.sizeHint().height())

    def _make_row(self, node, armed):
        row = QWidget()
        row.setFixedHeight(ROW_H - 2)      # 固定行高：总高度可预测，滚动才准
        h = QHBoxLayout(row)
        h.setContentsMargins(2, 2, 2, 2)
        h.setSpacing(6)

        dot = QPushButton()
        dot.setFixedSize(14, 14)
        dot.setStyleSheet(_DOT_ON if armed else _DOT_OFF)
        dot.setToolTip('已开启断点，点一下关闭' if armed else '未开启，点一下开启')
        dot.clicked.connect(lambda _=False, n=node: self._toggle(n))
        h.addWidget(dot)

        label = QLabel(self._node_label(node))
        label.setStyleSheet('color:#dfe7f2;font-size:12px;')
        label.setMinimumWidth(120)
        h.addWidget(label, 1)

        state = QLabel('已断点' if armed else '')
        state.setFixedWidth(48)
        state.setStyleSheet('color:#e0a0a0;font-size:11px;')
        h.addWidget(state)

        view_btn = QPushButton('查看')
        view_btn.setStyleSheet(
            'QPushButton{background:transparent;color:#9fd0ff;border:1px solid #3a5a8a;'
            'border-radius:4px;padding:2px 8px;font-size:11px;}'
            'QPushButton:hover{background:#1b2a3d;}')
        view_btn.clicked.connect(lambda _=False, n=node: self._open_detail(n))
        h.addWidget(view_btn)
        return row

    @staticmethod
    def _node_label(node) -> str:
        try:
            method = getattr(node, 'method', '') or 'GET'
            path = getattr(node, 'url_path', '') or ''
            ref = getattr(node, 'api_ref', None) or {}
            name = ref.get('name') if isinstance(ref, dict) else ''
            extra = '  · %s' % name if name else ''
            return '%s %s%s' % (method, path[:46], extra)
        except Exception:
            return '元素框'

    # ---------- 动作 ----------
    def _toggle(self, node):
        try:
            from portpanel.ui.debug.bridge import get_bridge
            b = get_bridge()
            b.set_armed(node, not b.is_armed(node))
        except Exception:
            pass
        self.refresh()

    def _clear_all(self):
        try:
            from portpanel.ui.debug.bridge import get_bridge
            b = get_bridge()
            for node in self._nodes():
                if b.is_armed(node):
                    b.set_armed(node, False)
        except Exception:
            pass
        self.refresh()

    def _open_detail(self, node):
        try:
            from portpanel.ui.debug import panel_host
            view = None
            try:
                sc = node.scene()
                view = sc.views()[0] if sc and sc.views() else None
            except Exception:
                view = None
            panel_host.open_for_node(node, view=view)
        except Exception:
            pass

    def show_near(self, global_pos: QPoint):
        """在按钮下方弹出（屏幕内收敛）。"""
        self.refresh()
        self.adjustSize()
        scr = self.screen().availableGeometry() if self.screen() else None
        x, y = global_pos.x() - self.width() + 40, global_pos.y() + 8
        if scr is not None:
            x = max(scr.left() + 8, min(x, scr.right() - self.width() - 8))
            y = max(scr.top() + 8, min(y, scr.bottom() - self.height() - 8))
        self.move(x, y)
        self.show()
        self.raise_()
