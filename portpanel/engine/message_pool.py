# -*- coding: utf-8 -*-
"""消息池 + 心跳同步（工业端口的“持续监听”地基）。

问题：WebSocket / PROFINET 这类心跳流，执行一次只拿到一帧快照，后面的帧全丢了。
做法：
- ``MessagePool`` 常驻收集每一帧（带序号），随时能取「最新一条」「自某序号之后的所有条」；
- ``HeartbeatSync`` 把池里的最新消息按心跳节拍推给下游（POST）；
- 断点语义：``pause()`` 只收不发，``release_once()`` 发最后一条但**保持断点**，
  ``resume()`` 恢复心跳，``stop()`` 彻底停。

纯线程实现，不依赖 Qt；UI 侧只需要把 sender 包成跨线程信号即可。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

__all__ = ['PoolMessage', 'MessagePool', 'HeartbeatSync']


@dataclass
class PoolMessage:
    seq: int
    ts: float
    channel: str
    payload: dict = field(default_factory=dict)
    raw: bytes = b''

    def values(self) -> list:
        """把一帧摊平成值列表，供下游 POST 的参数按序填充。"""
        p = self.payload or {}
        if isinstance(p.get('values'), (list, tuple)):
            return list(p['values'])
        if isinstance(p.get('slots'), list):
            out = []
            for s in p['slots']:
                if isinstance(s, dict):
                    if 'u32' in s:
                        out.append(s['u32'])
                    elif isinstance(s.get('u16'), list):
                        out.extend(s['u16'])
            return out
        if isinstance(p.get('slots'), dict):          # {(slot,sub): [u16]}
            out = []
            for v in p['slots'].values():
                out.extend(v if isinstance(v, (list, tuple)) else [v])
            return out
        return []

    def summary(self) -> str:
        p = self.payload or {}
        if 'values' in p:
            return 'addr=%s values=%s' % (p.get('addr'), p.get('values'))
        if 'slots' in p:
            return 'frame=%s slots=%s' % (p.get('frame_id'), p.get('slots'))
        return str(p)[:120]


class MessagePool:
    """线程安全的消息池（环形，按通道隔离序号）。"""

    def __init__(self, maxlen: int = 500):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._msgs = deque(maxlen=int(maxlen))
        self._seq = 0
        self._dropped = 0

    # ---- 写 ----
    def put(self, channel: str, payload: dict, raw: bytes = b'') -> PoolMessage:
        with self._cond:
            self._seq += 1
            msg = PoolMessage(self._seq, time.time(), channel or '', dict(payload or {}),
                              raw or b'')
            if len(self._msgs) == self._msgs.maxlen:
                self._dropped += 1
            self._msgs.append(msg)
            self._cond.notify_all()
            return msg

    def clear(self):
        with self._cond:
            self._msgs.clear()

    # ---- 读 ----
    def latest(self, channel: str | None = None):
        with self._lock:
            for m in reversed(self._msgs):
                if channel is None or m.channel == channel:
                    return m
            return None

    def since(self, seq: int, channel: str | None = None) -> list:
        with self._lock:
            return [m for m in self._msgs if m.seq > seq
                    and (channel is None or m.channel == channel)]

    def snapshot(self, n: int = 20) -> list:
        with self._lock:
            return list(self._msgs)[-int(n):]

    def wait_new(self, after_seq: int, timeout: float = 1.0,
                 channel: str | None = None):
        """阻塞等待序号 > after_seq 的新消息；超时返回 None。"""
        deadline = time.time() + max(0.0, timeout)
        with self._cond:
            while True:
                for m in self._msgs:
                    if m.seq > after_seq and (channel is None or m.channel == channel):
                        return m
                left = deadline - time.time()
                if left <= 0:
                    return None
                self._cond.wait(min(left, 0.2))

    @property
    def depth(self) -> int:
        with self._lock:
            return len(self._msgs)

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def stats(self) -> dict:
        with self._lock:
            last = self._msgs[-1] if self._msgs else None
            return {'depth': len(self._msgs), 'seq': self._seq,
                    'dropped': self._dropped,
                    'last': last.summary() if last else '',
                    'last_ts': last.ts if last else 0.0}


class HeartbeatSync:
    """按心跳节拍把「池里最新一条」推给下游。

    - ``pause()``      ：断点期间只收不发（不阻塞监听线程）；
    - ``release_once()``：发最后一条，但**保持暂停**（断点不取消）；
    - ``resume()``     ：发最后一条并恢复心跳；
    - ``stop()``       ：彻底停（与断点无关，节点停止时调用）。
    """

    IDLE, RUNNING, PAUSED = 'idle', 'running', 'paused'

    def __init__(self, pool: MessagePool, sender, interval: float = 2.0,
                 channel: str | None = None, min_gap: float = 0.2, on_log=None):
        self.pool = pool
        self.sender = sender
        self.interval = max(0.05, float(interval))
        self.channel = channel
        self.min_gap = max(0.0, float(min_gap))
        self.on_log = on_log
        self.state = self.IDLE
        self.sent = 0
        self.errors = 0
        self.skipped = 0
        self.last_sent_seq = 0
        self.last_sent_ts = 0.0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    # ---- 生命周期 ----
    def start(self):
        if self._thread and self._thread.is_alive():
            self.state = self.RUNNING
            return self
        self._stop.clear()
        self.state = self.RUNNING
        self._thread = threading.Thread(target=self._loop, name='heartbeat', daemon=True)
        self._thread.start()
        self._log('心跳开始（间隔 %.1fs）' % self.interval)
        return self

    def stop(self):
        self._stop.set()
        self._wake.set()
        self.state = self.IDLE
        if self._thread:
            self._thread.join(timeout=1.0)
        self._log('心跳停止')

    # ---- 断点 ----
    def pause(self):
        with self._lock:
            if self.state != self.IDLE:
                self.state = self.PAUSED
        self._log('断点阻塞：只入池，不推送')

    def resume(self):
        with self._lock:
            was = self.state
            if self.state == self.IDLE:
                return self.start()
            self.state = self.RUNNING
        self._wake.set()
        if was == self.PAUSED:
            self._log('断点取消：恢复心跳')

    def release_once(self, note: str = '放行最后一条'):
        msg = self.pool.latest(self.channel)
        if msg is None:
            self._log('%s：池里还没有消息' % note)
            return False
        ok = self._send(msg)
        self._log('%s #%d（断点保持）' % (note, msg.seq) if ok else '%s 失败 #%d' % (note, msg.seq))
        return ok

    # ---- 内部 ----
    def _log(self, text):
        if callable(self.on_log):
            try:
                self.on_log(text)
            except Exception:
                pass

    def _send(self, msg) -> bool:
        if msg.seq == self.last_sent_seq and (time.time() - self.last_sent_ts) < self.min_gap:
            self.skipped += 1
            return False
        try:
            ok = self.sender(msg)
        except Exception as exc:
            self.errors += 1
            self._log('心跳发送异常：%s' % str(exc)[:60])
            return False
        if ok is False:
            self.errors += 1
            return False
        self.sent += 1
        self.last_sent_seq = msg.seq
        self.last_sent_ts = time.time()
        return True

    def _loop(self):
        while not self._stop.is_set():
            st = self.state
            if st == self.PAUSED:
                self._wake.wait(0.1)
                self._wake.clear()
                continue
            if st != self.RUNNING:
                time.sleep(0.05)
                continue
            msg = self.pool.latest(self.channel)
            if msg is None:
                self._wake.wait(0.2)
                self._wake.clear()
                continue
            fresh = msg.seq != self.last_sent_seq
            due = (time.time() - self.last_sent_ts) >= self.interval
            if fresh or due:
                self._send(msg)
            self._wake.wait(0.1)
            self._wake.clear()

    def stats(self) -> dict:
        return {'state': self.state, 'sent': self.sent, 'errors': self.errors,
                'skipped': self.skipped, 'interval': self.interval,
                'last_seq': self.last_sent_seq}
