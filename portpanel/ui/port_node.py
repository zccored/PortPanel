# -*- coding: utf-8 -*-
"""端口节点（画布元素）：交换机式多输入 / 多输出。

- 输入点数量 = 用户配置的路数（`inputs`），每个口都能被连线；
- 输出点数量 = 用户配置的转发目标（`outputs`，host:port），一入口可分流到多目标；
- 纯自绘，鼠标事件由 item 处理：点按钮=动作，点其他=拖动；
- boundingRect 扩 9px，端口圆点纳入 shape()，移动不留残影。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainterPath, QPen
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QGraphicsRectItem, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

PROTO_META = {
    'modbus':    {'label': 'Modbus TCP', 'color': '#2f8f5f', 'badge': 'MB'},
    'profinet':  {'label': 'PROFINET RT', 'color': '#2f6fa8', 'badge': 'PN'},
    'websocket': {'label': 'WebSocket', 'color': '#7a4aa0', 'badge': 'WS'},
    'raw':       {'label': '原始 TCP', 'color': '#8a6a3a', 'badge': 'TCP'},
}
PAD = 10
MIN_W, MIN_H = 252, 128


class PortNode(QGraphicsRectItem):
    def __init__(self, proto: str = 'modbus', **kw):
        super().__init__()
        self.setCacheMode(QGraphicsRectItem.NoCache)     # 不做缓存，避免移动残影
        self.setFlags(QGraphicsRectItem.ItemIsMovable |
                      QGraphicsRectItem.ItemIsSelectable |
                      QGraphicsRectItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(3)

        self.proto = proto if proto in PROTO_META else 'raw'
        self.name = kw.get('name') or ('%s 端口' % PROTO_META[self.proto]['badge'])
        self.listen_port = int(kw.get('listen_port') or 0)
        self.unit = int(kw.get('unit') or 1)
        self.forward = kw.get('forward') or 'auto'
        self.map_ranges = [tuple(x) for x in (kw.get('map_ranges') or [(0, 4)])]
        self.frame_id = int(kw.get('frame_id') or 0x8000)
        self.slot_spec = [tuple(x) for x in (kw.get('slot_spec') or [(1, 1, 4), (2, 1, 4)])]
        self.ws_path = kw.get('ws_path') or '/pn'
        self.ws_direction = kw.get('ws_direction') or 'ws2pn'

        # 交换机式端口：输入若干路，输出若干目标
        self.inputs = list(kw.get('inputs') or ['in1'])
        outs = kw.get('outputs')
        if not outs:
            outs = [{'name': 'out1', 'host': kw.get('target_host') or '127.0.0.1',
                     'port': int(kw.get('target_port') or 0)}]
        self.outputs = [{'name': o.get('name') or 'out%d' % (i + 1),
                         'host': o.get('host') or '127.0.0.1',
                         'port': int(o.get('port') or 0)} for i, o in enumerate(outs)]

        self._bridge = None
        self._timer = None
        self._hover_btn = None
        self._btn_start = QRectF()
        self._btn_cfg = QRectF()
        self._sync_geometry()

    # ---------------- 端口列表 ----------------
    def input_names(self):
        return list(self.inputs)

    def output_names(self):
        return [o['name'] for o in self.outputs]

    @property
    def target_host(self):
        return self.outputs[0]['host'] if self.outputs else '127.0.0.1'

    @property
    def target_port(self):
        return self.outputs[0]['port'] if self.outputs else 0

    def _sync_geometry(self):
        rows = max(len(self.inputs), len(self.outputs), 1)
        self.prepareGeometryChange()      # 尺寸变了必须告知场景，否则端口重绘区是旧的 → 拖影
        self.setRect(0, 0, MIN_W, max(MIN_H, 108 + 18 * rows))
        self.update()

    # ---------------- 几何：端口均匀分布，绘制区包含端口 ----------------
    def boundingRect(self):
        return self.rect().adjusted(-10, -10, 10, 10)

    def shape(self):
        path = QPainterPath()
        path.addRoundedRect(self.rect(), 12, 12)
        for nm in self.input_names():
            path.addEllipse(self._port_point(nm, True), 7, 7)
        for nm in self.output_names():
            path.addEllipse(self._port_point(nm, False), 7, 7)
        return path

    def _port_point(self, name, inbound: bool) -> QPointF:
        names = self.inputs if inbound else self.output_names()
        n = max(len(names), 1)
        try:
            idx = names.index(name)
        except ValueError:
            idx = 0
        top = self.rect().top() + 46
        span = self.rect().height() - 46 - 44
        y = top + (span * (idx + 0.5) / n)
        x = self.rect().left() if inbound else self.rect().right()
        return QPointF(x, y)

    def input_port(self, name):
        return self.mapToScene(self._port_point(name, True))

    def output_port(self, name):
        return self.mapToScene(self._port_point(name, False))

    def inputPort(self):
        return self.input_port(self.inputs[0]) if self.inputs else self.mapToScene(
            self.rect().left(), self.rect().center().y())

    def outputPort(self):
        names = self.output_names()
        return self.output_port(names[0]) if names else self.mapToScene(
            self.rect().right(), self.rect().center().y())

    def itemChange(self, change, value):
        if change == QGraphicsRectItem.ItemPositionHasChanged:
            self.update()                    # 自身整块重绘（含端口）
            scene = self.scene()
            if scene is not None:
                if hasattr(scene, 'update_connections_for_node'):
                    scene.update_connections_for_node(self)
                scene.update()
        return super().itemChange(change, value)

    # ---------------- 绘制 ----------------
    def _route_text(self):
        outs = ','.join('%s:%d' % (o['host'], o['port']) for o in self.outputs[:2])
        if len(self.outputs) > 2:
            outs += ' +%d' % (len(self.outputs) - 2)
        head = ':%d → %s' % (self.listen_port, outs or '（未设目标）')
        if self.proto == 'websocket':
            head = ('WS→PN ' if self.ws_direction == 'ws2pn' else 'PN→WS ') + head
        return head

    def _stat_text(self):
        if self._bridge is None:
            return '未启动 · %d 入 / %d 出' % (len(self.inputs), len(self.outputs))
        d = self._bridge.stats.as_dict()
        txt = '收 %s · 发 %s · 错 %s' % (d.get('frames_in', 0),
                                        d.get('frames_out', 0), d.get('errors', 0))
        if d.get('last_error'):
            txt += ' · ' + str(d['last_error'])[:20]
        return txt

    def paint(self, painter, option, widget=None):
        meta = PROTO_META[self.proto]
        r = self.rect()
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        painter.setBrush(QBrush(QColor(28, 34, 44, 240)))
        painter.setPen(QPen(QColor(96, 118, 145), 2))
        painter.drawRoundedRect(r, 12, 12)
        painter.setBrush(QBrush(QColor(meta['color'])))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(r.left() + 8, r.top() + 4, r.width() - 16, 4), 2, 2)

        badge = QRectF(r.left() + PAD, r.top() + 14, 30, 17)
        painter.setBrush(QColor(meta['color']))
        painter.drawRoundedRect(badge, 3, 3)
        painter.setPen(QColor('white'))
        f = QFont('Microsoft YaHei', 7); f.setBold(True)
        painter.setFont(f)
        painter.drawText(badge, Qt.AlignCenter, meta['badge'])

        painter.setFont(QFont('Microsoft YaHei', 9, QFont.Bold))
        painter.setPen(QColor('#e8eef5'))
        title = QRectF(r.left() + 46, r.top() + 12, r.width() - 92, 20)
        painter.drawText(title, Qt.AlignLeft | Qt.AlignVCenter,
                         QFontMetrics(painter.font()).elidedText(
                             self.name, Qt.ElideRight, int(title.width())))
        painter.setFont(QFont('Microsoft YaHei', 9))
        painter.setPen(QColor('#46d07a' if self._bridge else '#6b7787'))
        painter.drawText(QRectF(r.right() - 34, r.top() + 12, 20, 18), Qt.AlignCenter, '●')

        painter.setFont(QFont('Consolas', 8))
        painter.setPen(QColor('#9fb0c3'))
        painter.drawText(QRectF(r.left() + PAD, r.top() + 34, r.width() - 2 * PAD, 14),
                         Qt.AlignLeft | Qt.AlignVCenter, self._route_text())
        painter.setPen(QColor('#7f8fa3'))
        painter.drawText(QRectF(r.left() + PAD, r.top() + 50, r.width() - 2 * PAD, 14),
                         Qt.AlignLeft | Qt.AlignVCenter, self._stat_text())

        # 端口圆点 + 标签
        painter.setFont(QFont('Consolas', 7))
        for nm in self.inputs:
            p = self._port_point(nm, True)
            painter.setPen(QPen(QColor(20, 24, 30), 1))
            painter.setBrush(QColor(255, 190, 90))
            painter.drawEllipse(p, 6, 6)
            painter.setPen(QColor('#b9a07a'))
            painter.drawText(QRectF(p.x() + 8, p.y() - 7, 40, 14),
                             Qt.AlignLeft | Qt.AlignVCenter, nm)
        for o in self.outputs:
            p = self._port_point(o['name'], False)
            painter.setPen(QPen(QColor(20, 24, 30), 1))
            painter.setBrush(QColor(120, 230, 120))
            painter.drawEllipse(p, 6, 6)
            painter.setPen(QColor('#8fc79a'))
            painter.drawText(QRectF(p.x() - 48, p.y() - 7, 40, 14),
                             Qt.AlignRight | Qt.AlignVCenter, o['name'])

        # 按钮
        running = self._bridge is not None
        self._btn_start = QRectF(r.left() + PAD, r.bottom() - 30, 78, 21)
        self._btn_cfg = QRectF(r.left() + PAD + 86, r.bottom() - 30, 56, 21)
        painter.setFont(QFont('Microsoft YaHei', 8))
        for rect, text, primary, hot in (
                (self._btn_start, '■ 停止' if running else '▶ 启动', True,
                 self._hover_btn == 'start'),
                (self._btn_cfg, '配置', False, self._hover_btn == 'cfg')):
            if primary:
                painter.setBrush(QColor('#2f5378' if hot else '#24405c'))
                painter.setPen(QPen(QColor('#3a6088'), 1))
            else:
                painter.setBrush(QColor('#33404f') if hot else QColor(0, 0, 0, 0))
                painter.setPen(QPen(QColor('#40506a'), 1))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor('#dce8f5' if primary else '#9fb0c3'))
            painter.drawText(rect, Qt.AlignCenter, text)

        if self.isSelected():
            painter.setBrush(QColor(40, 140, 255, 30))
            painter.setPen(QPen(QColor(0, 200, 255), 2))
            painter.drawRoundedRect(r.adjusted(-3, -3, 3, 3), 14, 14)

    # ---------------- 交互 ----------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._btn_start.contains(event.pos()):
                self.toggle(); event.accept(); return
            if self._btn_cfg.contains(event.pos()):
                self.open_config(); event.accept(); return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.open_config(); event.accept()

    def hoverMoveEvent(self, event):
        btn = ('start' if self._btn_start.contains(event.pos()) else
               ('cfg' if self._btn_cfg.contains(event.pos()) else None))
        if btn != self._hover_btn:
            self._hover_btn = btn
            self.setCursor(Qt.PointingHandCursor if btn else Qt.SizeAllCursor)
            self.update()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover_btn = None
        self.setCursor(Qt.SizeAllCursor)
        self.update()
        super().hoverLeaveEvent(event)

    # ---------------- 配置 ----------------
    def open_config(self):
        """非模态、不置顶、可随意拖动；改动实时写回画布（不必等「确定」）。"""
        views = self.scene().views() if self.scene() else []
        dlg = PortNodeDialog(self, parent=views[0] if views else None)
        dlg.setModal(False)
        dlg.setWindowModality(Qt.NonModal)
        dlg.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        dlg.accepted.connect(lambda: (dlg.apply_to(self), self._after_config()))
        dlg.changed.connect(lambda: (dlg.apply_to(self), self._after_config()))
        dlg.show()
        self._cfg_dlg = dlg
        return dlg

    def _after_config(self):
        self._sync_geometry()
        self.update()
        scene = self.scene()
        if scene is not None:
            if hasattr(scene, 'update_connections_for_node'):
                scene.update_connections_for_node(self)
            scene.update()

    # ---------------- 启停 ----------------
    def toggle(self):
        self.stop() if self._bridge is not None else self.start()

    def start(self):
        from portpanel.engine.ports import PortBridge
        if self._bridge is not None:
            return True
        targets = [(o['host'], o['port']) for o in self.outputs if o['port']]
        kw = dict(listen_port=self.listen_port, target_host=self.target_host,
                  target_port=self.target_port, targets=targets)
        if self.proto == 'modbus':
            kw.update(unit=self.unit, forward=self.forward, map_ranges=self.map_ranges)
        elif self.proto == 'profinet':
            kw.update(frame_id=self.frame_id, slot_spec=self.slot_spec)
        elif self.proto == 'websocket':
            kw.update(slot_spec=self.slot_spec, path=self.ws_path,
                      direction=self.ws_direction)
        try:
            self._bridge = PortBridge(proto=self.proto, **kw).start()
        except Exception as exc:
            self._bridge = None
            self.update()
            views = self.scene().views() if self.scene() else []
            if views:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(views[0], '端口启动失败', str(exc))
            return False
        self.listen_port = self._bridge.listen_port
        if self._timer is None:
            self._timer = QTimer()
            self._timer.setInterval(500)
            self._timer.timeout.connect(self.update)
        self._timer.start()
        self.update()
        return True

    def stop(self):
        if self._timer is not None:
            self._timer.stop()
        if self._bridge is not None:
            try:
                self._bridge.stop()
            except Exception:
                pass
            self._bridge = None
        self.update()

    def is_running(self):
        return self._bridge is not None

    # ---------------- 序列化 ----------------
    def to_dict(self):
        return {'type': 'PortNode', 'proto': self.proto, 'name': self.name,
                'listen_port': self.listen_port, 'unit': self.unit,
                'forward': self.forward, 'map_ranges': [list(x) for x in self.map_ranges],
                'frame_id': self.frame_id,
                'slot_spec': [list(x) for x in self.slot_spec],
                'ws_path': self.ws_path, 'ws_direction': self.ws_direction,
                'inputs': list(self.inputs),
                'outputs': [dict(o) for o in self.outputs]}

    @classmethod
    def from_dict(cls, d):
        return cls(proto=d.get('proto', 'modbus'), name=d.get('name'),
                   listen_port=d.get('listen_port'), unit=d.get('unit'),
                   forward=d.get('forward'),
                   map_ranges=[tuple(x) for x in (d.get('map_ranges') or [(0, 4)])],
                   frame_id=d.get('frame_id'),
                   slot_spec=[tuple(x) for x in (d.get('slot_spec') or [(1, 1, 4), (2, 1, 4)])],
                   ws_path=d.get('ws_path'), ws_direction=d.get('ws_direction'),
                   inputs=d.get('inputs'),
                   outputs=[dict(o) for o in (d.get('outputs') or [])] or None)


class PortNodeDialog(QDialog):
    """配置：协议 / 监听口 / 输入路数 / 输出目标表（交换机式）。非模态、实时生效。"""

    from PySide6.QtCore import Signal
    changed = Signal()

    def __init__(self, node: PortNode, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        self.setWindowTitle('端口配置 · %s' % PROTO_META[node.proto]['label'])
        self.setMinimumWidth(520)
        form = QFormLayout(self)

        self.proto = QComboBox()
        for k, m in PROTO_META.items():
            self.proto.addItem(m['label'], k)
        self.proto.setCurrentIndex(max(0, self.proto.findData(node.proto)))
        form.addRow('协议', self.proto)

        self.name = QLineEdit(node.name); form.addRow('名称', self.name)
        self.listen = QSpinBox(); self.listen.setRange(0, 65535)
        self.listen.setValue(node.listen_port); form.addRow('监听端口', self.listen)

        self.n_in = QSpinBox(); self.n_in.setRange(1, 32)
        self.n_in.setValue(len(node.inputs)); form.addRow('输入路数', self.n_in)

        bottom = QWidget()
        bl = QVBoxLayout(bottom); bl.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['名称', '目标主机', '目标端口'])
        self.table.horizontalHeader().setStretchLastSection(True)
        for o in node.outputs:
            self._add_row(o['name'], o['host'], o['port'])
        bl.addWidget(self.table)
        row = QHBoxLayout()
        btn_add = QPushButton('＋ 输出')
        btn_add.clicked.connect(lambda: self._add_row('out%d' % (self.table.rowCount() + 1),
                                                      '127.0.0.1', 0))
        btn_del = QPushButton('－ 删除选中')
        btn_del.clicked.connect(self._del_row)
        row.addWidget(btn_add); row.addWidget(btn_del); row.addStretch(1)
        bl.addLayout(row)
        form.addRow('输出目标', bottom)
        self.row = row

        self.unit = QSpinBox(); self.unit.setRange(0, 255); self.unit.setValue(node.unit)
        form.addRow('Modbus 站号', self.unit)
        self.forward = QComboBox()
        self.forward.addItem('auto（原样转发写入段）', 'auto')
        self.forward.addItem('mirror（整段镜像推送）', 'mirror')
        self.forward.setCurrentIndex(max(0, self.forward.findData(node.forward)))
        form.addRow('转发策略', self.forward)
        self.ranges = QLineEdit(';'.join('%d:%d' % (a, n) for a, n in node.map_ranges))
        form.addRow('镜像区间', self.ranges)
        self.frame_id = QLineEdit(hex(node.frame_id)); form.addRow('PN FrameID', self.frame_id)
        self.slots = QLineEdit(';'.join('%d,%d,%d' % s for s in node.slot_spec))
        form.addRow('PN 槽位表', self.slots)
        self.ws_path = QLineEdit(node.ws_path); form.addRow('WS 路径', self.ws_path)
        self.ws_dir = QComboBox()
        self.ws_dir.addItem('WebSocket → PROFINET', 'ws2pn')
        self.ws_dir.addItem('PROFINET → WebSocket', 'pn2ws')
        self.ws_dir.setCurrentIndex(max(0, self.ws_dir.findData(node.ws_direction)))
        form.addRow('WS 方向', self.ws_dir)

        for _w in (self.proto, self.forward, self.ws_dir, self.n_in,
                   self.listen, self.unit):
            try:
                _w.currentIndexChanged.connect(lambda *_: self.changed.emit())
            except Exception:
                try:
                    _w.valueChanged.connect(lambda *_: self.changed.emit())
                except Exception:
                    pass
        for _w in (self.name, self.ranges, self.frame_id, self.slots, self.ws_path):
            _w.textChanged.connect(lambda *_: self.changed.emit())
        self.table.cellChanged.connect(lambda *_: self.changed.emit())
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _add_row(self, name, host, port):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(str(name)))
        self.table.setItem(r, 1, QTableWidgetItem(str(host)))
        self.table.setItem(r, 2, QTableWidgetItem(str(port)))

    def _del_row(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def apply_to(self, node: PortNode):
        node.proto = self.proto.currentData()
        node.name = self.name.text().strip() or node.name
        node.listen_port = self.listen.value()
        node.unit = self.unit.value()
        node.forward = self.forward.currentData()
        node.ws_path = self.ws_path.text().strip() or '/pn'
        node.ws_direction = self.ws_dir.currentData()
        n = self.n_in.value()
        node.inputs = ['in%d' % (i + 1) for i in range(n)]
        outs = []
        for r in range(self.table.rowCount()):
            nm = (self.table.item(r, 0).text() if self.table.item(r, 0) else '').strip()
            host = (self.table.item(r, 1).text() if self.table.item(r, 1) else '').strip()
            port = (self.table.item(r, 2).text() if self.table.item(r, 2) else '0').strip()
            try:
                port_i = int(port or 0)
            except ValueError:
                port_i = 0
            outs.append({'name': nm or 'out%d' % (r + 1),
                         'host': host or '127.0.0.1', 'port': port_i})
        node.outputs = outs or [{'name': 'out1', 'host': '127.0.0.1', 'port': 0}]
        try:
            if self.ranges.text().strip():
                node.map_ranges = [tuple(int(x) for x in part.split(':'))
                                   for part in self.ranges.text().split(';') if part.strip()]
        except Exception:
            pass
        try:
            node.frame_id = int(self.frame_id.text().strip() or '0x8000', 0)
        except Exception:
            node.frame_id = 0x8000
        try:
            if self.slots.text().strip():
                node.slot_spec = [tuple(int(x) for x in part.split(','))
                                  for part in self.slots.text().split(';') if part.strip()]
        except Exception:
            pass
