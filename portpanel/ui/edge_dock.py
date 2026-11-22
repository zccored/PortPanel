# -*- coding: utf-8 -*-
"""贴边自动隐藏（类 QQ 的吸边）。

用法
----
    self._edge = EdgeDock(self)
    self._edge.attach()

    # 拖拽结束时（面板自己的事件过滤器里）
    self._edge.on_drag_end()

行为
----
* 把面板拖到屏幕任意一条边（距边缘 ≤ 24px）松手 → 吸边，动画滑出屏幕，
  只留 8px 的“拉手”在外面；
* 鼠标移到拉手上 → 动画滑回屏幕内；
* 鼠标离开面板 0.65 秒 → 自动滑回去；
* 拖离边缘（不贴边）松手 → 解除吸边，保持普通窗口行为。

注意：滑出后只露一条窄边，所以面板必须是无边框的（这些调试面板本来就是），
否则系统标题栏会露在外面。
"""
from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPoint,
                            QPropertyAnimation, QTimer, Qt)
from PySide6.QtWidgets import QApplication

__all__ = ['EdgeDock']

# PySide 里“C++ 对象由父对象持有、Python 包装器被回收”的情况会让 self.w 变空，
# 而事件过滤器还挂在 widget 上 → 回调里 AttributeError。这里强引用一份。
_ALIVE = set()


class EdgeDock(QObject):
    STRIP = 8          # 隐藏后留在屏幕内的像素
    SNAP_PX = 24       # 距边缘多少像素内算“贴边”
    HIDE_DELAY = 650   # 鼠标离开后多久自动隐藏（毫秒）
    SENSE_PX = 6       # 指针距屏幕边缘多少像素算“贴边”（触发滑出）
    POLL_MS = 70       # 指针轮询间隔（毫秒）；比动画帧率低，几乎不占 CPU
    DURATION = 240     # 动画时长（毫秒）；170ms 时每帧要跳 100px，看着是一顿一顿的
    FRAME_MS = 16      # 动画步进（≈60fps）

    def __init__(self, widget):
        super().__init__(widget)
        self.w = widget
        _ALIVE.add(self)
        self.side = None          # 'left' / 'right' / 'top' / 'bottom' / None
        self.hidden = False
        self._inside = False
        self._anim = QPropertyAnimation(widget, b'pos', self)
        self._anim.setDuration(self.DURATION)
        # 线性移动在 Windows 上看起来“一顿一顿”，用缓出曲线让起停柔和
        # 缓入缓出：起步不猛、收尾不撞，帧间位移更均匀
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._anim.finished.connect(self._on_anim_finished)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.slide_out)
        # 指针贴边感应：QQ 那种“鼠标碰到屏幕边缘，窗口自己探出来”
        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._poll_tick)
        self._poll_enabled = True

    # ---------- 安装 ----------
    def attach(self):
        self.w.installEventFilter(self)
        return self

    def eventFilter(self, obj, event):  # noqa: N802
        w = getattr(self, 'w', None)
        if w is None:            # 包装器已被回收，安静放行
            return False
        if obj is w:
            et = event.type()
            if et == QEvent.Enter:
                self._inside = True
                self._timer.stop()
                self.slide_in()
            elif et == QEvent.Leave:
                self._inside = False
                if self.side:
                    self._timer.start(self.HIDE_DELAY)
        return False

    # ---------- 指针贴边感应 ----------
    def _pointer(self):
        """当前鼠标指针的屏幕坐标（测试里可覆盖）。"""
        from PySide6.QtGui import QCursor
        return QCursor.pos()

    def _near_edge(self, pos) -> bool:
        """指针是否贴在本面板吸住的那条屏幕上。"""
        if not self.side:
            return False
        geo = self._geo()
        g = self.w.frameGeometry()
        # 沿边的方向要有重叠（面板上下范围附近），别在屏幕另一头也弹出来
        if self.side in ('left', 'right'):
            if not (g.top() - 60 <= pos.y() <= g.bottom() + 60):
                return False
            if self.side == 'left':
                return pos.x() <= geo.left() + self.SENSE_PX
            return pos.x() >= geo.right() - self.SENSE_PX
        if not (g.left() - 60 <= pos.x() <= g.right() + 60):
            return False
        if self.side == 'top':
            return pos.y() <= geo.top() + self.SENSE_PX
        return pos.y() >= geo.bottom() - self.SENSE_PX

    def _poll_tick(self):
        """每 70ms 看一次指针：贴边 → 探出；离开面板且不贴边 → 收起。"""
        if not self.side or not self.w.isVisible():
            return
        pos = self._pointer()
        try:
            inside = self.w.frameGeometry().contains(pos)
        except Exception:
            inside = False
        if self._near_edge(pos):
            self._timer.stop()
            if self.hidden:
                self.slide_in()
            return
        if inside:
            self._timer.stop()
            return
        # 既没贴边也不在面板上 → 延时收起（正在动画时不打断）
        if not self.hidden and not self._timer.isActive() and not self._anim_running():
            self._timer.start(self.HIDE_DELAY)

    def _anim_running(self) -> bool:
        try:
            return self._anim.state() == QPropertyAnimation.Running
        except Exception:
            return False

    def set_poll(self, on: bool):
        """可关闭指针感应（比如用户只想用悬停方式）。"""
        self._poll_enabled = bool(on)
        if on and self.side:
            self._poll.start()
        else:
            self._poll.stop()

    # ---------- 屏幕几何 ----------
    def _geo(self):
        scr = None
        try:
            scr = self.w.screen()
        except Exception:
            scr = None
        if scr is None:
            scr = QApplication.primaryScreen()
        return scr.availableGeometry()

    # ---------- 吸附判定 ----------
    def on_drag_end(self):
        """面板松手时调用：贴边就吸附并收起，否则解除吸附。"""
        g = self.w.frameGeometry()
        geo = self._geo()
        side = None
        if abs(g.left() - geo.left()) <= self.SNAP_PX:
            side = 'left'
        elif abs(g.right() - geo.right()) <= self.SNAP_PX:
            side = 'right'
        elif abs(g.top() - geo.top()) <= self.SNAP_PX:
            side = 'top'
        elif abs(g.bottom() - geo.bottom()) <= self.SNAP_PX:
            side = 'bottom'
        self.side = side
        self._timer.stop()
        if side:
            self.slide_out()
            if self._poll_enabled:
                self._poll.start()
        else:
            self.hidden = False
            self._poll.stop()

    # ---------- 位置计算 ----------
    def _hidden_pos(self):
        g = self.w.frameGeometry()
        geo = self._geo()
        if self.side == 'left':
            return QPoint(geo.left() - g.width() + self.STRIP, g.top())
        if self.side == 'right':
            return QPoint(geo.right() - self.STRIP + 1, g.top())
        if self.side == 'top':
            return QPoint(g.left(), geo.top() - g.height() + self.STRIP)
        if self.side == 'bottom':
            return QPoint(g.left(), geo.bottom() - self.STRIP + 1)
        return g.topLeft()

    def _shown_pos(self):
        """滑回时的目标：贴着该边、完整可见。"""
        g = self.w.frameGeometry()
        geo = self._geo()
        x, y = g.left(), g.top()
        if self.side == 'left':
            x = geo.left() + 2
        elif self.side == 'right':
            x = geo.right() - g.width() - 1
        elif self.side == 'top':
            y = geo.top() + 2
        elif self.side == 'bottom':
            y = geo.bottom() - g.height() - 1
        # 别把窗口推到屏幕外
        x = max(geo.left() - g.width() + self.STRIP, min(x, geo.right() - self.STRIP))
        y = max(geo.top(), min(y, geo.bottom() - self.STRIP))
        return QPoint(x, y)

    # ---------- 动画 ----------
    def _mark(self, on: bool):
        try:
            self.w._edge_animating = on
        except Exception:
            pass

    def _suspend_effects(self, on: bool):
        """动画期间关掉虚化/阴影之类的大面积特效，避免每帧重绘拖慢窗口移动。"""
        try:
            veil = getattr(self.w, '_veil', None)
            if veil is not None:
                veil.setUpdatesEnabled(not on)
        except Exception:
            pass

    def _animate(self, target: QPoint):
        try:
            self._suspend_effects(True)
            self._mark(True)
            # 目标没变且动画在跑：直接返回，避免 Enter/Leave 抖动导致反复重启
            if (self._anim.state() == QPropertyAnimation.Running
                    and self._anim.endValue() == target):
                return
            self._anim.stop()
            self._anim.setStartValue(self.w.pos())
            self._anim.setEndValue(target)
            self._anim.start()
        except Exception:
            self.w.move(target)

    def _on_anim_finished(self):
        self._suspend_effects(False)
        self._mark(False)

    def slide_out(self):
        """收起：只留 STRIP 像素在屏幕内。"""
        if not self.side or not self.w.isVisible():
            return
        self.hidden = True
        self._animate(self._hidden_pos())

    def slide_in(self):
        """展开：完整回到屏幕内。"""
        if not self.side:
            return
        self.hidden = False
        self._animate(self._shown_pos())

    def undock(self):
        """彻底解除吸边（比如用户把面板拖到屏幕中间）。"""
        self._timer.stop()
        try:
            self._poll.stop()
        except Exception:
            pass
        self.side = None
        self.hidden = False
