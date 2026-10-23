# -*- coding: utf-8 -*-
"""端口查看工具：把 Modbus / PROFINET / WebSocket 三个端口的信息源合并到一屏。

左边按端口列出来源，右边是该端口最近的帧；选中一帧可以看到原始报文、改完直接发回去。
协议随时切换（端口节点本身支持换协议重启）。
"""
from __future__ import annotations

import json
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from portpanel.ui.port_node import PROTO_META


class PortConsole(QDialog):
    def __init__(self, nodes, parent=None, create_cb=None):
        super().__init__(parent)
        self._create_cb = create_cb
        self.setWindowTitle('端口查看 · Modbus / PROFINET / WebSocket')
        self.resize(980, 560)
        self._nodes = list(nodes)
        self._rows = []          # 与表格行一一对应的记录

        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel('新建：'))
        for _p, _t in (('modbus', '＋ Modbus'), ('profinet', '＋ PROFINET'),
                       ('websocket', '＋ WebSocket')):
            _b = QPushButton(_t)
            _b.setToolTip('在画布上新建 %s 端口节点' % _p)
            _b.clicked.connect(lambda _c=False, proto=_p: self._create(proto))
            bar.addWidget(_b)
        bar.addSpacing(12)
        bar.addWidget(QLabel('协议切换：'))
        self.proto_box = QComboBox()
        for key, meta in PROTO_META.items():
            self.proto_box.addItem(meta['label'], key)
        bar.addWidget(self.proto_box)
        self.btn_apply = QPushButton('应用到选中端口')
        self.btn_apply.clicked.connect(self._apply_proto)
        bar.addWidget(self.btn_apply)
        self.btn_toggle = QPushButton('启动 / 停止')
        self.btn_toggle.clicked.connect(self._toggle)
        bar.addWidget(self.btn_toggle)
        self.btn_clear = QPushButton('清空')
        self.btn_clear.clicked.connect(lambda: (self.table.setRowCount(0), self._rows.clear()))
        bar.addWidget(self.btn_clear)
        bar.addStretch(1)
        self.status = QLabel('')
        self.status.setStyleSheet('color:#8fa3ba;')
        bar.addWidget(self.status)
        root.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setMinimumWidth(240)
        self.list.currentRowChanged.connect(self._on_port_changed)
        split.addWidget(self.list)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['时间', '方向', '端口', '摘要'])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellClicked.connect(self._on_row)
        rl.addWidget(self.table, 3)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText('选中一帧后在这里查看 / 编辑原始报文（JSON），'
                                       '点「发送」写回该端口')
        rl.addWidget(self.editor, 2)
        send_bar = QHBoxLayout()
        self.btn_send = QPushButton('发送到该端口')
        self.btn_send.clicked.connect(self._send)
        send_bar.addWidget(self.btn_send)
        send_bar.addStretch(1)
        rl.addLayout(send_bar)
        split.addWidget(right)
        split.setSizes([260, 700])
        root.addWidget(split, 1)

        self._reload_ports()
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    def _create(self, proto):
        if callable(self._create_cb):
            node = self._create_cb(proto)
            if node is not None:
                self._nodes.append(node)
                self._reload_ports()
                self.list.setCurrentRow(self.list.count() - 1)

    # ---------------- 端口列表 ----------------
    def _reload_ports(self):
        cur = self.list.currentRow()
        self.list.clear()
        for n in self._scan_nodes():
            run = '●' if n.is_running() else '○'
            it = QListWidgetItem('%s %s  :%d' % (run, n.name, n.listen_port))
            it.setToolTip('%s → %s:%d' % (n.proto, n.target_host, n.target_port))
            self.list.addItem(it)
        if cur >= 0 and cur < self.list.count():
            self.list.setCurrentRow(cur)

    def _scan_nodes(self):
        nodes = list(self._nodes)
        for n in nodes:
            if n not in self._nodes:
                continue
        return [n for n in nodes if n.is_running()] or nodes

    def _current_node(self):
        row = self.list.currentRow()
        nodes = self._scan_nodes()
        if 0 <= row < len(nodes):
            return nodes[row]
        return None

    def _on_port_changed(self, _row):
        self._reload_table()
        n = self._current_node()
        if n is not None:
            self.proto_box.setCurrentIndex(max(0, self.proto_box.findData(n.proto)))

    # ---------------- 帧表 ----------------
    def _reload_table(self):
        self.table.setRowCount(0)
        self._rows.clear()
        n = self._current_node()
        if n is None or n._bridge is None:
            return
        for rec in list(n._bridge.stats.recent):
            self._append(rec, n.listen_port)

    def _append(self, rec, port):
        r = self.table.rowCount()
        self.table.insertRow(r)
        ts = time.strftime('%H:%M:%S', time.localtime(rec.get('ts', time.time())))
        for c, v in enumerate([ts, rec.get('dir', ''), str(port), rec.get('summary', '')]):
            self.table.setItem(r, c, QTableWidgetItem(str(v)))
        self._rows.append(rec)

    def _refresh(self):
        n = self._current_node()
        if n is None:
            return
        if self.table.rowCount() == 0 and n._bridge is not None:
            self._reload_table()
            return
        if n._bridge is None:
            return
        seen = len(self._rows)
        allrec = list(n._bridge.stats.recent)
        if len(allrec) > seen:
            for rec in allrec[seen:]:
                self._append(rec, n.listen_port)
        self.status.setText('端口 %d · 收 %d 发 %d 错 %d' % (
            n.listen_port, n._bridge.stats.frames_in,
            n._bridge.stats.frames_out, n._bridge.stats.errors))

    def _on_row(self, row, _col):
        if 0 <= row < len(self._rows):
            raw = self._rows[row].get('raw')
            self.editor.setPlainText(
                json.dumps(raw, ensure_ascii=False, indent=2)
                if raw is not None else self._rows[row].get('summary', ''))

    # ---------------- 动作 ----------------
    def _apply_proto(self):
        n = self._current_node()
        if n is None:
            return
        was = n.is_running()
        n.stop()
        n.proto = self.proto_box.currentData()
        if was:
            n.start()
        self._reload_ports()

    def _toggle(self):
        n = self._current_node()
        if n is None:
            return
        n.stop() if n.is_running() else n.start()
        self._reload_ports()

    def _send(self):
        n = self._current_node()
        if n is None or n._bridge is None:
            self.status.setText('先启动该端口')
            return
        from portpanel.engine.ports import WsPort
        text = self.editor.toPlainText().strip()
        if not text:
            return
        impl = n._bridge.impl
        try:
            if isinstance(impl, WsPort) and impl.server is not None:
                impl.server.broadcast(text)
                self.status.setText('已广播到 %d 个 WS 客户端' % len(impl.server.clients))
            else:
                self.status.setText('该协议暂不支持直接发送（Modbus 请用读/写指令）')
        except Exception as exc:
            self.status.setText('发送失败：%s' % exc)
