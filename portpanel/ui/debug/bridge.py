# -*- coding: utf-8 -*-
"""断点总线：worker 线程里的请求在 pre-send 处阻塞，主线程弹面板决策。

- `set_armed(node, on)`：红点开关（按节点对象记账）；
- `make_decider(node)`：给 `DebuggerInterceptor` 用；**在 worker 线程里调用**，
  发信号到主线程后等 `threading.Event`（默认 10 分钟超时）；
- `submit(decision)`：主线程放行 / 改包 / 丢弃时唤醒 worker。
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Signal

__all__ = ['DebugBridge', 'get_bridge']

_TIMEOUT_S = 600.0


class DebugBridge(QObject):
    pauseRequested = Signal(object, object)     # (node, HttpRequest) —— 主线程接
    resumed = Signal(object)                    # (node,)
    liveEvent = Signal(object)                  # {'phase','method','url','status'} 实时报文

    def __init__(self):
        super().__init__()
        self._armed = set()
        self._lock = threading.Lock()
        self._event = None
        self._decision = None
        self._paused_node = None
        self._journal_path = None

    # ---------- 断点开关 ----------
    def set_armed(self, node, on: bool):
        if on:
            self._armed.add(node)
        else:
            self._armed.discard(node)
        # 程序化开关时同步红点（不触发 toggled，避免递归）
        dot = getattr(node, 'breakpoint_dot', None)
        if dot is not None and getattr(dot, 'armed', None) != on:
            dot.set_armed(on)

    def is_armed(self, node) -> bool:
        return node in self._armed

    def armed_count(self) -> int:
        return len(self._armed)

    # ---------- 留痕 ----------
    def journal_path(self) -> str:
        if self._journal_path is None:
            self._journal_path = 'logs/run_%s/requests.jsonl' % time.strftime('%Y%m%d_%H%M%S')
        return self._journal_path

    # ---------- worker 侧 ----------
    def make_decider(self, node):
        def _decide(req):
            ev = threading.Event()
            with self._lock:
                self._event = ev
                self._decision = None
                self._paused_node = node
            self.pauseRequested.emit(node, req)      # 跨线程 → 主线程排队
            ev.wait(timeout=_TIMEOUT_S)
            with self._lock:
                decision = self._decision
                self._event = None
                self._paused_node = None
            return decision
        return _decide

    # ---------- 主线程侧 ----------
    def submit(self, decision):
        with self._lock:
            self._decision = decision
            ev = self._event
        if ev is not None:
            ev.set()
        node = self._paused_node
        if node is not None:
            self.resumed.emit(node)

    def is_paused(self) -> bool:
        return self._event is not None


_bridge = None


def get_bridge() -> DebugBridge:
    global _bridge
    if _bridge is None:
        _bridge = DebugBridge()
    return _bridge
