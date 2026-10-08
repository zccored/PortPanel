# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# 端口画板（PortPanel）· 新手引导层
# Copyright (C) 2026 zccored  ·  AGPL-3.0-only（见 LICENSE）
#
# 「新手引导」= 一层半透明遮罩盖住窗口，只在**要讲的那个控件**上开一个洞（聚光灯），
# 旁边浮一个说明气泡，用户按「下一步」走，或者**真的动手做对了**才自动进下一步。
#
# 为什么不用现成的库：考察过 `qt-tour`（BSD-3，14 KB，最接近需求）与
# `QtGuidedUI`（MIT，需 Qt.py），两者的遮罩都是整块 `WA_TransparentForMouseEvents`
# —— 也就是**聚光灯里点不进去**，只能"看"，不能"做"。本项目的引导要的是**可交互**：
# 用户得能真的点那个按钮，做完这一步才继续。那条路必须自己走。
#
# 关键技法（`_Spotlight._apply_mask`）：
#     可视区 = 整窗 − 聚光灯矩形
#     setMask() 同时裁掉"绘制"和"鼠标命中"，于是洞里既不挡视线、也不吃点击事件，
#     点击直接落到下面的真实控件上。这是 Qt 原生的做法，本项目 main.py 早就用它
#     做过透明窗口的间隙遮罩，属于已验证可行的路子。
# ---------------------------------------------------------------------------
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPen, QRegion
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

# ==================== 外观常量（固定深色，与 dark_theme 同一套色） ====================
DIM_ALPHA = 165                    # 遮罩不透明度（0-255）
HOLE_PAD = 6                       # 聚光灯比控件外扩多少像素
BUBBLE_W = 380                     # 气泡基准宽度
BUBBLE_GAP = 14                    # 气泡与聚光灯的间距
POLL_MS = 250                      # 交互式步骤的轮询间隔

C_BG = '#20242c'                   # 气泡底
C_BORDER = '#3a4658'
C_TITLE = '#e8f0fb'
C_BODY = '#aab6c8'
C_ACCENT = '#2a82da'
C_OK = '#4ade80'
C_WAIT = '#fbbf24'

# 锚点：气泡放在聚光灯的哪一侧
ANCHORS = ('left', 'right', 'above', 'below', 'center')


# ==================== 步骤模型（低代码：写一个列表就是一套引导） ====================
@dataclass
class TourStep:
    """引导的一步。

    **最小写法**（只讲、不动手）::

        TourStep(target=lambda: dlg.btn_run, title="执行", body="点这里开始跑流程。")

    **可交互写法**（做对了才自动进下一步）::

        TourStep(
            target=lambda: dlg.btn_new_drawing,
            title="先新建一张图纸",
            body="点它，给这张图纸起个名字。",
            done=lambda: dlg.tab_bar.count() > self._n0,   # 真的多了一张图纸才算过
            prepare=lambda: dlg._set_left_collapsed(False),# 侧栏收着就先展开
        )

    字段说明：

    target
        **返回控件的函数**（不是控件本身）。必须是惰性的 —— 引导开始时要讲的控件
        可能还没建出来、或者会被重建，写成 lambda 才能每次都取到当下的那个。
        返回 None 表示这步现在做不了，会按 `optional` 决定跳过还是等待。
    title / body
        气泡的标题与正文；正文支持 Qt 富文本。
    anchor
        气泡位置，见 `ANCHORS`。
    done
        交互式推进的判据：**每 POLL_MS 毫秒问一次**，返回 True 就自动进下一步。
        传 None 表示这步只是讲解，用户点「下一步」才走。
        用轮询而不是去 hook 控件的 clicked 信号，是因为同一个引导要能挂到
        QPushButton / QToolButton / QTabBar / 甚至"表格多了一行"这类**非点击**动作上，
        轮询一个谓词是唯一能统一处理的写法，而且不会干扰控件本身的信号连接。
    prepare
        进这一步**之前**先执行的动作（展开折叠面板、切到某一页…）。
        返回 True 表示"刚改了布局，几何要下一个事件循环 tick 才准"，届时会重新取一次。
    optional
        目标找不到时：True=跳过这步，False=停下来等（适合"必须先打开某个东西"的场景）。
    hint
        交互式步骤等待时显示在气泡里的提示语。
    """

    target: Callable[[], Optional[QWidget]]
    title: str
    body: str = ''
    anchor: str = 'right'
    done: Optional[Callable[[], bool]] = None
    prepare: Optional[Callable[[], bool]] = None
    optional: bool = True
    hint: str = '在上面那个高亮区域里操作，做完会自动继续'
    key: str = ''

    def __post_init__(self):
        if self.anchor not in ANCHORS:
            raise ValueError(f'anchor 必须是 {ANCHORS} 之一，收到 {self.anchor!r}')


# ==================== 外观：遮罩（打洞） ====================
class _Spotlight(QWidget):
    """盖住整个窗口的半透明遮罩，在聚光灯处**开洞**。

    洞有两个作用：视觉上让目标控件保持原样（不被压暗），交互上让点击**穿透**到它。
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._hole: Optional[QRect] = None
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_hole(self, rect: Optional[QRect]) -> None:
        self._hole = QRect(rect) if rect is not None else None
        self._apply_mask()
        self.update()

    def _apply_mask(self) -> None:
        """可视区 = 整窗 − 洞。setMask 同时裁绘制与鼠标命中，所以洞里点击会穿透。"""
        full = QRegion(self.rect())
        if self._hole is not None and not self._hole.isEmpty():
            full = full.subtracted(QRegion(self._hole))
        self.setMask(full)

    def resizeEvent(self, event):                       # noqa: N802 (Qt 命名)
        self._apply_mask()
        super().resizeEvent(event)

    def paintEvent(self, event):                        # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(0, 0, 0, DIM_ALPHA))
        # 给洞描一圈高亮边，让人一眼看出"就是这里"
        if self._hole is not None and not self._hole.isEmpty():
            painter.setPen(QPen(QColor(C_ACCENT), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(self._hole.adjusted(1, 1, -1, -1), 7, 7)


# ==================== 外观：说明气泡 ====================
class _Bubble(QFrame):
    """说明气泡：标题 + 正文 + 进度 + 导航按钮。

    刻意**自带样式表**（不依赖全局 QSS）—— 这样它被塞进任何窗口都是同一副样子，
    也方便单独截图/测试。objectName 仍然留给外层做细调。
    """

    next_clicked = Signal()
    back_clicked = Signal()
    skip_clicked = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName('tour_bubble')
        self.setStyleSheet(f"""
            QFrame#tour_bubble {{
                background-color: {C_BG};
                border: 1px solid {C_BORDER};
                border-radius: 10px;
            }}
            QLabel#tour_title {{ color: {C_TITLE}; font-weight: bold; }}
            QLabel#tour_body  {{ color: {C_BODY}; }}
            QLabel#tour_hint  {{ color: {C_WAIT}; }}
            QLabel#tour_counter {{ color: #7f8ea6; }}
            QPushButton {{
                background-color: #38445a; color: #e8f0fb;
                border: 1px solid #4a586e; border-radius: 5px;
                padding: 5px 14px;
            }}
            QPushButton:hover {{ background-color: #46546e; }}
            QPushButton:disabled {{ background-color: #2a3140; color: #6b7789; }}
            QPushButton#tour_next {{ background-color: {C_ACCENT}; border-color: {C_ACCENT}; font-weight: bold; }}
            QPushButton#tour_next:hover {{ background-color: #3a92ea; }}
            QPushButton#tour_skip {{ background-color: transparent; border: none; color: #8a97a8; }}
            QPushButton#tour_skip:hover {{ color: #c8d4e4; }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)

        self.lb_title = QLabel()
        self.lb_title.setObjectName('tour_title')
        self.lb_title.setWordWrap(True)
        f = QFont(self.font())
        f.setPointSize(f.pointSize() + 2)
        self.lb_title.setFont(f)
        lay.addWidget(self.lb_title)

        self.lb_body = QLabel()
        self.lb_body.setObjectName('tour_body')
        self.lb_body.setWordWrap(True)
        self.lb_body.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.lb_body)

        self.lb_hint = QLabel()
        self.lb_hint.setObjectName('tour_hint')
        self.lb_hint.setWordWrap(True)
        self.lb_hint.setVisible(False)
        lay.addWidget(self.lb_hint)

        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.lb_counter = QLabel()
        self.lb_counter.setObjectName('tour_counter')
        nav.addWidget(self.lb_counter)
        nav.addStretch()

        self.btn_skip = QPushButton('跳过引导')
        self.btn_skip.setObjectName('tour_skip')
        self.btn_skip.clicked.connect(self.skip_clicked)
        nav.addWidget(self.btn_skip)

        self.btn_back = QPushButton('上一步')
        self.btn_back.clicked.connect(self.back_clicked)
        nav.addWidget(self.btn_back)

        self.btn_next = QPushButton('下一步')
        self.btn_next.setObjectName('tour_next')
        self.btn_next.setDefault(True)
        self.btn_next.clicked.connect(self.next_clicked)
        nav.addWidget(self.btn_next)
        lay.addLayout(nav)

        self._nav = nav
        self._width = BUBBLE_W

    def set_content(self, step: TourStep, index: int, total: int,
                    waiting: bool, last: bool, already: bool = False) -> None:
        """填内容。

        `waiting` 与 `already` **互斥**，表达三种状态：

        * ``already=True`` —— 这步的事**用户已经做过了**（典型是点「上一步」退回来）。
          此时**不轮询**，主按钮是「下一步」，提示换成绿色的「已完成」。
        * ``waiting=True`` —— 正在等他动手：显示 ⏳ 提示，主按钮是「跳过这步」。
        * 两者都 False —— 纯讲解：主按钮「下一步 / 完成」。
        """
        self.lb_title.setText(step.title)
        self.lb_body.setText(step.body or '')
        self.lb_body.setVisible(bool(step.body))
        if already:
            self.lb_hint.setText('✅ 这步已经完成了')
            self.lb_hint.setStyleSheet(f'color: {C_OK};')
            self.lb_hint.setVisible(True)
        elif waiting:
            self.lb_hint.setText('⏳ ' + step.hint)
            self.lb_hint.setStyleSheet(f'color: {C_WAIT};')
            self.lb_hint.setVisible(True)
        else:
            self.lb_hint.setText('')
            self.lb_hint.setVisible(False)
        self.lb_counter.setText(f'第 {index} / {total} 步')
        self.btn_back.setVisible(index > 1)
        self.btn_back.setEnabled(index > 1)
        if waiting:
            self.btn_next.setText('跳过这步')
        else:
            self.btn_next.setText('完成' if last else '下一步')
        self._relayout()

    def _relayout(self) -> None:
        lay = self.layout()
        if lay is None:
            return
        self._nav.invalidate()
        m = lay.contentsMargins()
        need = self._nav.sizeHint().width() + m.left() + m.right()
        self._width = max(BUBBLE_W, need)
        self.setFixedWidth(self._width)
        self.setFixedHeight(lay.heightForWidth(self._width))
        lay.activate()

    def place(self, hole: QRect, anchor: str, bounds: QRect) -> None:
        w, h = self.width(), self.height()
        if anchor == 'left':
            x, y = hole.left() - w - BUBBLE_GAP, hole.top()
        elif anchor == 'above':
            x, y = hole.center().x() - w // 2, hole.top() - h - BUBBLE_GAP
        elif anchor == 'below':
            x, y = hole.center().x() - w // 2, hole.bottom() + BUBBLE_GAP
        elif anchor == 'center':
            x, y = hole.center().x() - w // 2, hole.center().y() - h // 2
        else:
            x, y = hole.right() + BUBBLE_GAP, hole.top()
        # 贴边时钳回窗口内，别让气泡跑出去
        x = max(bounds.left() + 10, min(x, bounds.right() - w - 10))
        y = max(bounds.top() + 10, min(y, bounds.bottom() - h - 10))
        self.move(x, y)


# ==================== 引擎 ====================
class GuidedTour(QObject):
    """一次引导的运行实例。

    一次只该有一个实例；跑完（或被跳过）会自己清理干净，**不要复用实例**，重看请新建。
    典型用法::

        self._tour = GuidedTour(TOUR_STEPS, self)
        self._tour.finished.connect(self._on_tour_done)
        self._tour.start()
    """

    finished = Signal(bool)          # True=走完，False=中途跳过
    step_changed = Signal(int)

    def __init__(self, steps: Sequence[TourStep], window: QWidget):
        if not isinstance(window, QWidget):
            raise TypeError('window 必须是 QWidget')
        super().__init__(window)
        self._steps: List[TourStep] = list(steps)
        self._win: Optional[QWidget] = window
        self._idx = 0
        self._active = False
        self._completed = False
        self._spot = _Spotlight(window)
        self._bubble = _Bubble(window)
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._check_done)
        self._bubble.next_clicked.connect(self._on_next)
        self._bubble.back_clicked.connect(self._on_back)
        self._bubble.skip_clicked.connect(lambda: self.close(completed=False))
        self._spot.hide()
        self._bubble.hide()

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._active or self._win is None:
            return
        self._active = True
        self._completed = False
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        idx = self._seek(0, 1)
        if idx is None:
            self.close(completed=False)
            return
        QTimer.singleShot(0, lambda: self._show_step(idx))

    def close(self, completed: bool = False) -> None:
        """收工。可重复调用；会把遮罩/气泡/事件过滤器全部撤干净。"""
        if not self._active:
            return
        self._active = False
        self._completed = bool(completed)
        try:
            self._poll.stop()
        except Exception:
            pass
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:
                pass
        for sig, slot in ((self._bubble.next_clicked, self._on_next),
                          (self._bubble.back_clicked, self._on_back)):
            try:
                sig.disconnect(slot)
            except Exception:
                pass
        for w in (self._spot, self._bubble):
            try:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            except RuntimeError:
                pass                     # C++ 对象已被连带销毁（关窗口时很常见）
        self._win = None
        self.finished.emit(self._completed)
        self.deleteLater()

    # ---------- 事件 ----------
    def eventFilter(self, obj, event):                    # noqa: N802
        if event is None:
            return False
        et = event.type()
        if et == QEvent.Type.KeyPress:
            if isinstance(event, QKeyEvent) and event.key() == Qt.Key.Key_Escape:
                self.close(completed=False)
                return True
        elif et in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show) \
                and obj is self._win:
            self._refresh_geometry()
        return False

    # ---------- 步骤推进 ----------
    def _resolve(self, step: TourStep):
        """取这一步的目标控件与它的窗口内矩形；取不到返回 (None, None)。"""
        try:
            w = step.target()
        except Exception:
            w = None
        if w is None or not w.isVisible():
            return None, None
        try:
            tl = w.mapTo(self._win, QPoint(0, 0))
        except RuntimeError:
            return None, None
        return w, QRect(tl, w.size())

    def _available(self, step: TourStep) -> bool:
        """这一步现在能不能讲。

        ⚠ 有 `prepare` 的步骤**一律算可尝试** —— 它的目标可能正收着/藏着，
        而 `prepare` 的职责恰恰就是把它弄出来（比如"先把折叠的侧栏展开"）。
        早期版本在这里直接按"目标不可见"跳过，结果 prepare 永远没机会执行 ——
        「展开侧栏再讲它」这类步骤会整条被静默吃掉。真正的可用性等 prepare 跑完再判。
        """
        if step.prepare is not None:
            return True
        return self._resolve(step)[0] is not None

    def _seek(self, index: int, direction: int) -> Optional[int]:
        while 0 <= index < len(self._steps):
            step = self._steps[index]
            if step.optional and not self._available(step):
                index += direction
                continue
            return index
        return None

    def _done_now(self, step: TourStep) -> bool:
        """这一步的交互判据**当前**是否已经满足。

        为什么要在"进入步骤时"就问一次：进入时如果它**已经为真**，说明用户之前就做完了
        这件事（典型场景：做完 → 自动进下一步 → 用户点「上一步」退回来）。
        这时若照常启动轮询，250ms 后就会判定为真、把用户又弹回下一步 ——
        「上一步」看起来就像失灵了一样。
        """
        if step.done is None:
            return False
        try:
            return bool(step.done())
        except Exception:
            return False

    def _show_step(self, index: int) -> None:
        if not self._active or self._win is None:
            return
        step = self._steps[index]

        # prepare：先把该展开的展开、该切的页切好
        if step.prepare is not None:
            try:
                changed = bool(step.prepare())
            except Exception:
                changed = False
            if changed:
                self._idx = index
                QTimer.singleShot(0, lambda i=index: self._show_step(i))
                return

        widget, rect = self._resolve(step)
        if widget is None:
            if step.optional:
                nxt = self._seek(index + 1, 1)
                if nxt is None:
                    self.close(completed=True)
                else:
                    self._show_step(nxt)
            return

        self._idx = index
        hole = rect.adjusted(-HOLE_PAD, -HOLE_PAD, HOLE_PAD, HOLE_PAD)
        self._hole_rect = hole
        self._spot.setGeometry(self._win.rect())
        self._spot.set_hole(hole)
        self._spot.show()
        self._spot.raise_()

        visible = [i for i, s in enumerate(self._steps)
                   if not (s.optional and not self._available(s))]
        # 先问一次判据：为真说明这步用户早做过了（多半是点「上一步」退回来的），
        # 那就按"讲解"呈现、**不启动轮询**，否则会立刻被弹回下一步。
        already = self._done_now(step)
        waiting = (step.done is not None) and not already
        self._bubble.set_content(step, visible.index(index) + 1, len(visible),
                                 waiting, last=(index == visible[-1]),
                                 already=already)
        self._bubble.place(hole, step.anchor, self._win.rect())
        self._bubble.show()
        self._bubble.raise_()

        if waiting:
            self._poll.start()
        else:
            self._poll.stop()
        self.step_changed.emit(index)

    def _refresh_geometry(self) -> None:
        """窗口大小/位置变了：跟着挪一次（不推进步骤）。"""
        if not self._active or self._win is None:
            return
        step = self._steps[self._idx]
        _w, rect = self._resolve(step)
        if rect is None:
            return
        hole = rect.adjusted(-HOLE_PAD, -HOLE_PAD, HOLE_PAD, HOLE_PAD)
        self._hole_rect = hole
        self._spot.setGeometry(self._win.rect())
        self._spot.set_hole(hole)
        self._bubble.place(hole, step.anchor, self._win.rect())

    def _check_done(self) -> None:
        """交互式步骤：轮询判据，做对了就自动往下走。"""
        if not self._active or self._win is None:
            return
        step = self._steps[self._idx]
        if step.done is None:
            self._poll.stop()
            return
        try:
            if step.done():
                self._poll.stop()
                self._on_next()
        except Exception:
            pass

    def _on_next(self) -> None:
        nxt = self._seek(self._idx + 1, 1)
        if nxt is None:
            self.close(completed=True)
            return
        self._show_step(nxt)

    def _on_back(self) -> None:
        prev = self._seek(self._idx - 1, -1)
        if prev is not None:
            self._show_step(prev)

    # ---------- 给测试/外部看的状态 ----------
    @property
    def active(self) -> bool:
        return self._active

    @property
    def index(self) -> int:
        return self._idx

    @property
    def completed(self) -> bool:
        return self._completed

    @property
    def spotlight_rect(self) -> Optional[QRect]:
        return getattr(self, '_hole_rect', None)

    @property
    def bubble(self) -> _Bubble:
        return self._bubble


# ==================== 首次运行标记（看过就不再自动弹） ====================
def _state_path() -> str:
    root = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, 'data', 'ui_state.json')


def tour_seen(key: str) -> bool:
    try:
        with open(_state_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return bool((data.get('tours_seen') or {}).get(key))
    except Exception:
        return False                     # 读不到就当没看过（宁可多弹一次）


def mark_tour_seen(key: str, seen: bool = True) -> None:
    p = _state_path()
    data = {}
    try:
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f) or {}
    except Exception:
        data = {}
    seen_map = data.get('tours_seen')
    if not isinstance(seen_map, dict):
        seen_map = {}
    seen_map[key] = bool(seen)
    data['tours_seen'] = seen_map
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception:
        pass
