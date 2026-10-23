# -*- coding: utf-8 -*-
"""断点 / 实时报文面板 v2（P2）。

编辑者要求（2026-10-23 晚）：
- 可拖动、不锁死、不置顶于其他应用；
- 打开时落在**画板居中**位置；
- 打开时把画板**渐变虚化**做视觉聚焦；鼠标移到框外 → 虚化渐变取消；
- 请求 / 响应**左右分栏**，中间一条竖线；
- 页眉有红点按钮表示「已断点」；
- 执行期间常驻画板前部，实时看每条支路的报文；
- 断点保留前方输入点快照；放行条件由调用方门控（上游数据到齐）。

边框画在 `rect().adjusted(0,0,-1,-1)` 上，永远在控件内部。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                               QLabel, QPushButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from portpanel.engine.transport import HttpRequest, HttpResult

__all__ = ['InspectPanel', 'FocusVeil']

_MASK_HEADERS = ('authorization', 'cookie', 'set-cookie', 'x-api-key', 'token')


def _mask(k: str, v: str) -> str:
    if k.lower() in _MASK_HEADERS and v:
        return '%s…(len=%d)' % (v[:3], len(v))
    return v


def masked_headers(headers):
    """响应头打码（cookie / set-cookie / authorization …），用于面板展示。"""
    out = {}
    for k, v in (headers or {}).items():
        out[str(k)] = _mask(str(k), v) if str(k).lower() in _MASK_HEADERS else v
    return out


class FocusVeil(QWidget):
    """画板上的半透明遮罩：面板打开时淡入，鼠标移出面板后淡出。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet('background: rgba(6, 10, 18, 120);')
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)
        self._anim = None
        self.hide()

    def fade_to(self, value: float):
        from PySide6.QtCore import QPropertyAnimation
        if self._anim is None:
            self._anim = QPropertyAnimation(self._fx, b'opacity', self)
            self._anim.setDuration(220)
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(value)
        self._anim.start()
        if value > 0:
            self.show()
            self.raise_()


class InspectPanel(QWidget):
    released = Signal(object)      # HttpRequest（改后的）或 None=丢弃
    cancelled = Signal()

    MARGIN = 12
    RADIUS = 12

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle('请求断点')
        self._req: HttpRequest | None = None
        self._drag = None
        self._veil = None
        self._live_rows = {}
        self._release_ok = True
        self._armed_state = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(self.MARGIN, self.MARGIN - 2,
                                 self.MARGIN, self.MARGIN)
        outer.setSpacing(6)

        # ---- 页眉（可拖动）----
        self._header = QWidget()
        head = QHBoxLayout(self._header)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self.dot_btn = QPushButton()
        self.dot_btn.setFixedSize(14, 14)
        self.dot_btn.setToolTip('断点状态：灰色空心=未开启，实心红=已开启')
        self.dot_btn.setStyleSheet(self._dot_qss(False))
        self.dot_btn.clicked.connect(self._toggle_dot)
        head.addWidget(self.dot_btn)
        self.title = QLabel('请求断点')
        self.title.setStyleSheet('color:#ffd7d7; font-weight:bold;')
        head.addWidget(self.title)
        self.state_label = QLabel('')
        self.state_label.setStyleSheet('color:#8b98a8; font-size:11px;')
        head.addWidget(self.state_label)
        head.addStretch()
        self.close_btn = QPushButton('×')
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setStyleSheet(
            'QPushButton{border:none;color:#bbb;font-size:16px;}'
            'QPushButton:hover{color:#fff;}')
        self.close_btn.clicked.connect(self._on_cancel)
        head.addWidget(self.close_btn)
        self._header.installEventFilter(self)
        outer.addWidget(self._header)

        # ---- 主体：请求 | 竖线 | 响应 ----
        body = QHBoxLayout()
        body.setSpacing(6)
        self.req_tree = self._make_tree('支路 / 输入点')
        self.resp_tree = self._make_tree('响应 / 输出点')
        body.addWidget(self.req_tree, 1)
        self._divider = QFrame()
        self._divider.setFrameShape(QFrame.VLine)
        self._divider.setStyleSheet('color:rgba(90,170,255,120);')
        self._divider.setFixedWidth(1)
        body.addWidget(self._divider)
        body.addWidget(self.resp_tree, 1)
        outer.addLayout(body, 1)

        # 兼容旧测试/旧调用：tree 指向请求侧
        self.tree = self.req_tree

        # ---- 脚部 ----
        btns = QHBoxLayout()
        self.release_btn = QPushButton('放行')
        self.release_btn.clicked.connect(self._on_release)
        self.drop_btn = QPushButton('丢弃')
        self.drop_btn.clicked.connect(self._on_drop)
        btns.addWidget(self.release_btn)
        btns.addWidget(self.drop_btn)
        btns.addStretch()
        self.hint = QLabel('双击值可改；取消勾选=断开该支路')
        self.hint.setStyleSheet('color:#8b98a8;font-size:11px;')
        btns.addWidget(self.hint)
        outer.addLayout(btns)

        self.setStyleSheet(self.styleSheet() + 'QLabel{color:#dfe7f2;}')
        self.setMinimumSize(520, 300)
        self.resize(760, 420)
        self._rz_edge = None
        self._rz_origin = None
        self._rz_geo = None
        self.setMouseTracking(True)

    # ---------- 样式 ----------
    @staticmethod
    def _dot_qss(armed: bool) -> str:
        if armed:
            return ('QPushButton{background:#e05252;border:1px solid #e05252;'
                    'border-radius:7px;}')
        return ('QPushButton{background:transparent;border:1px solid #8b98a8;'
                'border-radius:7px;}')

    def _make_tree(self, header: str) -> QTreeWidget:
        t = QTreeWidget()
        t.setHeaderLabels([header, '值'])
        t.setColumnWidth(0, 170)
        t.setRootIsDecorated(True)
        t.setUniformRowHeights(True)
        t.setEditTriggers(QTreeWidget.DoubleClicked | QTreeWidget.EditKeyPressed)
        t.setStyleSheet(
            'QTreeWidget{background:rgba(20,26,36,220);color:#dfe7f2;'
            'border:1px solid rgba(90,170,255,90);border-radius:8px;}'
            'QHeaderView::section{background:rgba(30,42,58,230);color:#9ecbff;'
            'border:none;padding:3px 6px;}'
            'QTreeWidget::indicator{width:12px;height:12px;border:1px solid #6b7684;'
            'border-radius:2px;background:transparent;}'
            'QTreeWidget::indicator:checked{background:#2f81f7;border:1px solid #2f81f7;'
            'image:none;}')
        return t

    # ---------- 绘制 ----------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(0, 0, -1, -1)
        p.setPen(QPen(QColor(90, 170, 255, 200), 1))
        p.setBrush(QColor(16, 22, 32, 246))
        p.drawRoundedRect(rect, self.RADIUS, self.RADIUS)
        p.end()

    # ---------- 拖动 ----------
    def eventFilter(self, obj, event):
        if obj is self._header:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if event.type() == QEvent.MouseMove and self._drag is not None:
                self.move(event.globalPosition().toPoint() - self._drag)
                return True
            if event.type() == QEvent.MouseButtonRelease:
                self._drag = None
                return True
        return super().eventFilter(obj, event)

    def leaveEvent(self, event):
        if self._veil is not None:
            self._veil.fade_to(0.0)
        super().leaveEvent(event)

    def enterEvent(self, event):
        if self._veil is not None:
            self._veil.fade_to(1.0)
        super().enterEvent(event)

    # ---------- 虚化遮罩 ----------
    def attach_veil(self, view):
        """view = NodeView；遮罩盖住画板区域，面板始终在遮罩之上。"""
        try:
            win = view.window()
            if self._veil is None or self._veil.parent() is not win:
                self._veil = FocusVeil(win)
            r = view.viewport().geometry()
            tl = view.viewport().mapTo(win, QPoint(0, 0))
            self._veil.setGeometry(tl.x(), tl.y(), r.width(), r.height())
            self._veil.show()
            self._veil.raise_()
            self._veil.fade_to(1.0)
        except Exception:
            self._veil = None

    def detach_veil(self):
        if self._veil is not None:
            self._veil.fade_to(0.0)
            QTimer.singleShot(260, self._veil.hide)

    # ---------- 断点状态 ----------
    def set_armed(self, on: bool):
        self._armed_state = bool(on)
        self.dot_btn.setStyleSheet(self._dot_qss(self._armed_state))
        self.dot_btn.setToolTip('断点已开启' if on else '断点未开启')
        self.state_label.setText('已断点' if on else '')

    def _toggle_dot(self):
        self.set_armed(not self._armed_state)

    # ---------- 数据 ----------
    def set_request(self, req: HttpRequest, node_label: str = ''):
        self._req = req
        self.title.setText('请求断点' + (' · ' + node_label if node_label else ''))
        self.req_tree.clear()
        root = QTreeWidgetItem(['请求', ''])
        root.setFirstColumnSpanned(True)
        self.req_tree.addTopLevelItem(root)
        m = QTreeWidgetItem(['方法', req.method]); m.setCheckState(0, Qt.Checked)
        u = QTreeWidgetItem(['URL', req.url]); u.setCheckState(0, Qt.Checked)
        root.addChild(m); root.addChild(u)
        hroot = QTreeWidgetItem(['请求头', '%d 条' % len(req.headers or {})])
        hroot.setCheckState(0, Qt.Checked)
        root.addChild(hroot)
        for k, v in (req.headers or {}).items():
            it = QTreeWidgetItem([k, _mask(k, v)])
            it.setCheckState(0, Qt.Checked)
            hroot.addChild(it)
        broot = QTreeWidgetItem(['请求体', req.body_kind])
        broot.setCheckState(0, Qt.Checked)
        root.addChild(broot)
        body_text = (req.body or b'').decode('utf-8', 'replace')
        b = QTreeWidgetItem(['body', body_text[:600]])
        b.setCheckState(0, Qt.Checked)
        broot.addChild(b)
        self.req_tree.expandAll()

        self.resp_tree.clear()
        self._live_rows.clear()          # 树被清空，缓存的项已成悬空指针
        rr = QTreeWidgetItem(['响应', '（放行后填充）'])
        rr.setFirstColumnSpanned(True)
        self.resp_tree.addTopLevelItem(rr)

    def set_inputs(self, items):
        """断点时的**入口输入点**快照（service / creator_id / q / o …）。

        items: [{'name','value','location','source'}]；空值用 ⚠ 标出来，
        便于一眼看出「上游到底有没有把值送到这个元素框」。
        """
        for i in range(self.req_tree.topLevelItemCount() - 1, -1, -1):
            if self.req_tree.topLevelItem(i).text(0) == '输入点':
                self.req_tree.takeTopLevelItem(i)
        root = QTreeWidgetItem(['输入点', '%d 个' % len(items or [])])
        root.setFirstColumnSpanned(True)
        # 放在最上面：先看输入，再看请求
        self.req_tree.insertTopLevelItem(0, root)
        for it in items or []:
            name = str(it.get('name') or '')
            val = it.get('value', '')
            val_s = '' if val is None else str(val)
            empty = not val_s.strip()
            row = QTreeWidgetItem([name, ('⚠ 空' if empty else val_s[:200])])
            row.setToolTip(1, ('来源: %s' % it.get('source')) if it.get('source') else '')
            row.setToolTip(0, '位置: %s' % it.get('location', 'path'))
            root.addChild(row)
        root.setExpanded(True)

    def set_response_payload(self, payload: dict):
        """放行后用真实响应填右栏（状态码/头/正文）。payload 来自 HttpResult。"""
        if not payload:
            return
        self.resp_tree.clear()
        self._live_rows.clear()
        status = payload.get('status', '')
        via = payload.get('via', '')
        _bits = [str(status)] if status != '' else []
        if via:
            _bits.append(str(via))
        if payload.get('elapsed_ms') not in (None, '', 0):
            _bits.append('%s ms' % payload['elapsed_ms'])
        root = QTreeWidgetItem(['响应', ' · '.join(_bits)])
        root.setFirstColumnSpanned(True)
        self.resp_tree.addTopLevelItem(root)
        for k, v in (payload.get('headers') or {}).items():
            root.addChild(QTreeWidgetItem([str(k), _mask(str(k), str(v))]))
        body = payload.get('body')
        if isinstance(body, (bytes, bytearray)):
            body = bytes(body).decode('utf-8', 'replace')
        body_s = (body or payload.get('error') or '（空响应）')
        root.addChild(QTreeWidgetItem(['body', str(body_s)[:4000]]))
        self.resp_tree.expandAll()

    def set_response(self, res: HttpResult):
        self.resp_tree.clear()
        root = QTreeWidgetItem(['响应', '%s · %s · %d ms' % (res.status, res.via, res.elapsed_ms)])
        root.setFirstColumnSpanned(True)
        self.resp_tree.addTopLevelItem(root)
        for k, v in (res.headers or {}).items():
            if k in ('content-type', 'content-length', 'server'):
                root.addChild(QTreeWidgetItem([k, str(v)]))
        body = (res.body or b'')[:1200].decode('utf-8', 'replace')
        root.addChild(QTreeWidgetItem(['body', body or ('<%s>' % res.error or '空')]))
        self.resp_tree.expandAll()

    def set_live(self, method: str, url: str, phase: str = '', status=None):
        """执行期间实时显示每条支路的报文（不打断流程）。"""
        key = '%s %s' % (method, url.split('?')[0])
        root = self.resp_tree.topLevelItem(0)
        if root is None:
            root = QTreeWidgetItem(['实时报文', ''])
            root.setFirstColumnSpanned(True)
            self.resp_tree.addTopLevelItem(root)
        it = self._live_rows.get(key)
        if it is not None:
            try:
                it.text(0)                     # 探测 C++ 对象是否还活着
            except RuntimeError:
                self._live_rows.pop(key, None)
                it = None
        if it is None:
            it = QTreeWidgetItem([key, ''])
            root.addChild(it)
            self._live_rows[key] = it
        if phase == 'request':
            it.setText(1, '→ 已发出')
        elif phase == 'response':
            it.setText(1, '← %s' % status)

    def set_release_enabled(self, ok: bool, why: str = ''):
        self._release_ok = bool(ok)
        self.release_btn.setEnabled(ok)
        self.release_btn.setToolTip(why or ('上游数据已就绪，可放行' if ok else '等待上游数据到齐'))

    def apply_edits(self) -> HttpRequest | None:
        """把树里的编辑结果合回 HttpRequest；断开（未勾选）的支路不发送。"""
        if self._req is None:
            return None
        from dataclasses import replace
        method, url = self._req.method, self._req.url
        headers = {}
        body = None
        body_kind = self._req.body_kind
        root = self.req_tree.topLevelItem(0)
        if root is None:
            return self._req
        for i in range(root.childCount()):
            node = root.child(i)
            label = node.text(0)
            if label == '方法' and node.checkState(0) == Qt.Checked:
                method = node.text(1) or method
            elif label == 'URL' and node.checkState(0) == Qt.Checked:
                url = node.text(1) or url
            elif label == '请求头':
                for j in range(node.childCount()):
                    it = node.child(j)
                    if it.checkState(0) == Qt.Checked:
                        headers[it.text(0)] = it.text(1)
            elif label == '请求体':
                for j in range(node.childCount()):
                    it = node.child(j)
                    if it.checkState(0) == Qt.Checked:
                        body = it.text(1).encode('utf-8')
        return replace(self._req, method=method, url=url, headers=headers,
                       body=body, body_kind=body_kind)

    # ---------- 动作 ----------
    def _on_release(self):
        if not self._release_ok:
            return
        self.released.emit(self.apply_edits())

    def _on_drop(self):
        self.released.emit(None)

    def _on_cancel(self):
        self.hide()
        if self._veil is not None:
            self._veil.hide()
        self.cancelled.emit()

    # ------------------------------------------------------------------ 八向缩放
    _EDGE_PX = 7

    def _edge_at(self, pos):
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        e = self._EDGE_PX
        left, right = x <= e, x >= w - e
        top, bottom = y <= e, y >= h - e
        if top and left:
            return 'tl'
        if top and right:
            return 'tr'
        if bottom and left:
            return 'bl'
        if bottom and right:
            return 'br'
        if left:
            return 'l'
        if right:
            return 'r'
        if top:
            return 't'
        if bottom:
            return 'b'
        return None

    def _cursor_for(self, edge):
        return {'l': Qt.SizeHorCursor, 'r': Qt.SizeHorCursor,
                't': Qt.SizeVerCursor, 'b': Qt.SizeVerCursor,
                'tl': Qt.SizeFDiagCursor, 'br': Qt.SizeFDiagCursor,
                'tr': Qt.SizeBDiagCursor, 'bl': Qt.SizeBDiagCursor}.get(edge)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            edge = self._edge_at(event.pos())
            if edge:
                self._rz_edge = edge
                self._rz_origin = event.globalPosition().toPoint()
                self._rz_geo = self.geometry()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._rz_edge:
            delta = event.globalPosition().toPoint() - self._rz_origin
            g, min_w, min_h = self._rz_geo, self.minimumWidth(), self.minimumHeight()
            x, y, w, h = g.x(), g.y(), g.width(), g.height()
            if 'l' in self._rz_edge:
                nw = max(min_w, w - delta.x()); x, w = x + (w - nw), nw
            if 'r' in self._rz_edge:
                w = max(min_w, w + delta.x())
            if 't' in self._rz_edge:
                nh = max(min_h, h - delta.y()); y, h = y + (h - nh), nh
            if 'b' in self._rz_edge:
                h = max(min_h, h + delta.y())
            self.setGeometry(x, y, w, h)
            event.accept()
            return
        cur = self._cursor_for(self._edge_at(event.pos()))
        self.setCursor(cur) if cur is not None else self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._rz_edge = None
        self._rz_origin = None
        self._rz_geo = None
        super().mouseReleaseEvent(event)
