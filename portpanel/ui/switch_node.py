# -*- coding: utf-8 -*-
"""逻辑门节点：与门 / 非门 / 异或门。

数据约定
--------
* 左侧三个输入点：`in1`、`in2`、`data`
  - `in1` / `in2` 只认 `true` / `false`（大小写不敏感，允许带引号、换行）
  - `data` 是要放行的数据本体（任意内容）
* 右侧两个输出点：`OUT`、`data`
  - `OUT`：条件结果，字符串 `true` / `false`
  - `data`：与输入 `data` 相同的放行数据

触发规则（只有“字符串遵从 true/false”才算数据到位）
--------------------------------------------------
| 门   | 需要               | 成立条件              |
| AND  | in1、in2           | in1 且 in2 都为 true  |
| NOT  | in1                | in1 为 false          |
| XOR  | in1、in2           | in1、in2 一真一假     |

* 输入缺失或不是 true/false → `evaluate()` 返回 `None`（= 还在等数据，下游不放行）
* 条件成立 → 放行 `data`；不成立 → `OUT=false`，`data` 不往下走
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsRectItem

W, H = 210, 124
PAD = 10

MODES = ('AND', 'NOT', 'XOR')
_MODE_CN = {'AND': '与门 AND', 'NOT': '非门 NOT', 'XOR': '异或门 XOR',
            'gate': '闸门（旧）'}

_IN_PORTS = ('in1', 'in2', 'data')
_OUT_PORTS = ('OUT', 'data')


def parse_bool(value):
    """把“字符串”解析成布尔；不是 true/false 就返回 None（= 数据还没到位）。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    s = str(value).strip().strip('"').strip("'").strip()
    low = s.lower()
    if low in ('true', '1', 'yes', 'on'):
        return True
    if low in ('false', '0', 'no', 'off'):
        return False
    return None


class SwitchNode(QGraphicsRectItem):
    def __init__(self, label: str = '逻辑门', enabled: bool = True, **kw):
        super().__init__()
        self.setFlags(QGraphicsRectItem.ItemIsMovable |
                      QGraphicsRectItem.ItemIsSelectable |
                      QGraphicsRectItem.ItemSendsGeometryChanges)
        self.setZValue(3)
        self.setRect(0, 0, W, H)
        self.label = kw.get('name') or label
        self.enabled = bool(enabled)
        self.mode = kw.get('mode') or 'AND'      # AND / NOT / XOR（'gate' 兼容旧图纸）
        self.inputs = {k: None for k in _IN_PORTS}
        self.outputs = {k: None for k in _OUT_PORTS}
        self._refresh_tooltip()

    # ---------------- 端口 ----------------
    TOP_BAR = 26      # 标题栏高度：端口从头栏下方开始排，避免和标题/门类型重叠

    def _in_y(self, name):
        idx = _IN_PORTS.index(name)
        top = self.rect().top() + self.TOP_BAR
        h = self.rect().height() - self.TOP_BAR - 18      # 底部留给“结果”行
        return top + h * (idx + 1) / (len(_IN_PORTS) + 1)

    def _out_y(self, name):
        idx = _OUT_PORTS.index(name)
        top = self.rect().top() + self.TOP_BAR
        h = self.rect().height() - self.TOP_BAR - 18
        return top + h * (idx + 1) / (len(_OUT_PORTS) + 1)

    def input_names(self):
        """画布连线检测用：左侧三个输入点名。"""
        return list(_IN_PORTS)

    def output_names(self):
        """画布连线检测用：右侧两个输出点名。"""
        return list(_OUT_PORTS)

    def inputPortItem(self, name='data'):
        """默认返回 data 输入点（旧代码/旧图纸的单一入口）。"""
        if name not in _IN_PORTS:
            name = 'data'
        return QPointF(self.rect().left(), self._in_y(name))

    def outputPortItem(self, name='data'):
        if name not in _OUT_PORTS:
            name = 'data'
        return QPointF(self.rect().right(), self._out_y(name))

    def inputPort(self, name='data'):
        return self.mapToScene(self.inputPortItem(name))

    def outputPort(self, name='data'):
        return self.mapToScene(self.outputPortItem(name))

    # flow_editor._find_port_at() 用 snake_case 名字（PortNode 就是这套），
    # SwitchNode 原来只有 camelCase，点到端口就 AttributeError。
    def input_port(self, name='data'):
        return self.inputPort(name)

    def output_port(self, name='data'):
        return self.outputPort(name)

    def input_ports(self):
        return {n: self.inputPort(n) for n in _IN_PORTS}

    def output_ports(self):
        return {n: self.outputPort(n) for n in _OUT_PORTS}

    def boundingRect(self):
        # 端口圆点半径 6 + 选中描边 → 外扩 16，避免拖动时留下端口拖痕
        return self.rect().adjusted(-16, -16, 16, 16)

    def shape(self):
        path = QPainterPath()
        path.addRoundedRect(self.rect(), 10, 10)
        for n in _IN_PORTS:
            path.addEllipse(self.inputPortItem(n), 7, 7)
        for n in _OUT_PORTS:
            path.addEllipse(self.outputPortItem(n), 7, 7)
        return path

    def itemChange(self, change, value):
        if change == QGraphicsRectItem.ItemPositionHasChanged:
            scene = self.scene()
            if scene is not None:
                if hasattr(scene, 'update_connections_for_node'):
                    scene.update_connections_for_node(self)
                scene.update()
        return super().itemChange(change, value)

    # ---------------- 逻辑 ----------------
    def set_input(self, name, value):
        if name in self.inputs:
            self.inputs[name] = value
            self.update()

    def reset_inputs(self):
        self.inputs = {k: None for k in _IN_PORTS}
        self.outputs = {k: None for k in _OUT_PORTS}
        self.update()

    def gather_inputs(self, connections):
        """按连线的**端口名**取上游输出。

        * 上游是逻辑门：看它这条线从哪个口出来（OUT → true/false，data → 放行数据）
        * 上游是普通元素框：取它最近一次响应的正文
        """
        for c in list(connections or []):
            if getattr(c, 'end_obj', None) is not self:
                continue
            up = getattr(c, 'start_obj', None)
            if up is None:
                continue
            param = getattr(c, 'end_param', '') or 'data'
            if param not in self.inputs:
                param = 'data'
            start_port = (getattr(c, 'start_param', '') or '').strip()
            if isinstance(up, SwitchNode):
                if start_port in ('OUT', 'data'):
                    val = up.outputs.get(start_port)
                else:
                    val = up.outputs.get('OUT')
            else:
                val = getattr(up, '_last_body_text', None)
            self.inputs[param] = val
        return self.inputs

    def evaluate(self):
        """返回 True / False / None（None = 输入还没到位，继续等）。"""
        if self.mode == 'gate':          # 旧闸门：只看 enabled，不看输入
            return bool(self.enabled)
        if self.mode == 'NOT':
            a = parse_bool(self.inputs.get('in1'))
            return None if a is None else (not a)
        a = parse_bool(self.inputs.get('in1'))
        b = parse_bool(self.inputs.get('in2'))
        if a is None or b is None:
            return None
        if self.mode == 'XOR':
            return bool(a) != bool(b)
        if self.mode == 'AND':
            return bool(a) and bool(b)
        # 旧闸门模式：enabled 决定放行
        return bool(self.enabled)

    def run_once(self, connections=None):
        """执行一次门：成立 → {'OUT':'true','data':...}；不成立 → False；等待 → None。"""
        if connections is not None:
            self.gather_inputs(connections)
        res = self.evaluate()
        if res is None:
            self.outputs['OUT'] = None
            return None
        self.outputs['OUT'] = 'true' if res else 'false'
        self.outputs['data'] = self.inputs.get('data') if res else None
        self._last_body_text = self.outputs['OUT']   # 下游按“最近结果”取值时拿到 true/false
        self.update()
        return {'OUT': self.outputs['OUT'], 'data': self.outputs['data']} if res else False

    def is_running(self) -> bool:
        """兼容画布代码（PortNode 也实现同名方法）。"""
        return False

    # ---------------- 外观 ----------------
    def _refresh_tooltip(self):
        self.setToolTip('逻辑门[%s]：左侧 in1/in2 接 true/false，data 接要放行的数据；'
                        '右侧 OUT 输出条件结果，data 输出放行数据。双击切换门类型。'
                        % _MODE_CN.get(self.mode, self.mode))

    def mouseDoubleClickEvent(self, event):
        """双击切换 AND → NOT → XOR。"""
        order = MODES
        try:
            self.mode = order[(order.index(self.mode) + 1) % len(order)]
        except ValueError:
            self.mode = 'AND'
        self._refresh_tooltip()
        self.update()
        sc = self.scene()
        if sc is not None:
            sc.update()
        event.accept()

    def paint(self, painter, option, widget=None):
        """画法和其它元素框一致：蓝色圆角框 + 黑边（选中变青色）+ 端口圆点。"""
        r = self.rect()
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        if self.isSelected():
            painter.setPen(QPen(QColor(0, 200, 255), 3, Qt.SolidLine))
            painter.setBrush(QColor(70, 130, 180, 200))
        else:
            painter.setPen(QPen(Qt.black, 2))
            painter.setBrush(QColor(70, 130, 180))
        painter.drawRoundedRect(r, 6, 6)

        # 标题（白字加粗，和 APINode 一致）
        painter.setPen(QColor('#ffffff'))
        painter.setFont(QFont('Microsoft YaHei', 8, QFont.Bold))
        painter.drawText(QRectF(r.left() + PAD, r.top() + 4, r.width() - 2 * PAD, 16),
                         Qt.AlignLeft | Qt.AlignVCenter, self.label)

        # 门类型徽标：放标题同一行右侧，别压住第一个输入点
        painter.setFont(QFont('Microsoft YaHei', 7))
        painter.setPen(QColor('#d6ecff'))
        painter.drawText(QRectF(r.left() + 6, r.top() + 4, r.width() - 12, 16),
                         Qt.AlignRight | Qt.AlignVCenter,
                         _MODE_CN.get(self.mode, self.mode))

        # 输入端口：绿色圆点（同 APINode 的参数输入口），值用浅色小字
        for name in _IN_PORTS:
            y = self._in_y(name)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(100, 200, 100))
            painter.drawEllipse(self.inputPortItem(name), 6, 6)
            val = self.inputs.get(name)
            txt = '-' if val is None else str(val).strip()[:14]
            painter.setPen(QColor('#eaf4ff'))
            painter.drawText(QRectF(r.left() + 14, y - 8, r.width() / 2, 16),
                             Qt.AlignLeft | Qt.AlignVCenter, '%s %s' % (name, txt))

        # 输出端口：红色圆点（同 APINode 的输出口）
        for name in _OUT_PORTS:
            y = self._out_y(name)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(200, 100, 100))
            painter.drawEllipse(self.outputPortItem(name), 6, 6)
            val = self.outputs.get(name)
            txt = '' if val is None else '=' + str(val)[:12]
            painter.setPen(QColor('#ffe6e6'))
            painter.drawText(QRectF(r.right() - r.width() / 2 - 16, y - 8,
                                    r.width() / 2, 16),
                             Qt.AlignRight | Qt.AlignVCenter, '%s%s' % (name, txt))

        # 结果
        painter.setPen(QColor('#ffffff'))
        painter.setFont(QFont('Microsoft YaHei', 7))
        painter.drawText(QRectF(r.left() + PAD, r.bottom() - 16, r.width() - 2 * PAD, 14),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         '结果 %s' % (self.outputs.get('OUT') or '等待数据'))

    # ---------------- 序列化 ----------------
    def to_dict(self):
        return {'type': 'SwitchNode', 'name': self.label,
                'enabled': bool(self.enabled), 'mode': self.mode}

    @classmethod
    def from_dict(cls, d):
        return cls(label=d.get('name') or '逻辑门',
                   enabled=d.get('enabled', True), mode=d.get('mode') or 'AND')


def blocked_by_switch(node, connections):
    """沿上游找逻辑门：任何一个门“条件不成立/还在等数据”就阻断。"""
    seen = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        for c in list(connections or []):
            if getattr(c, 'end_obj', None) is not cur:
                continue
            up = getattr(c, 'start_obj', None)
            if up is None:
                continue
            if isinstance(up, SwitchNode):
                # 旧闸门模式且没有输入 → 保留“开关”语义
                if up.mode == 'gate' and not up.enabled:
                    return True, '上游开关[%s]已关闭' % up.label
                up.gather_inputs(connections)
                res = up.evaluate()
                if res is None:
                    return True, ('门[%s·%s]还在等 in1/in2 的 true/false 数据'
                                  % (up.label, up.mode))
                if not res:
                    up.outputs['OUT'] = 'false'
                    return True, ('门[%s·%s]条件不成立（OUT=false，data 不放行）'
                                  % (up.label, up.mode))
                up.outputs['OUT'] = 'true'
                up.outputs['data'] = up.inputs.get('data')
                # 放行的数据挂到门节点上，下游按“上游最近一次结果”取值时就能拿到
                if up.outputs['data'] is not None:
                    up._last_body_text = up.outputs['data']
            stack.append(up)
    return False, ''
