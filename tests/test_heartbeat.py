# -*- coding: utf-8 -*-
"""消息池 / 心跳同步：断点只收不发、放行一条、取消后恢复。"""
from __future__ import annotations

import threading
import time

import pytest

from portpanel.engine.message_pool import HeartbeatSync, MessagePool, PoolMessage


def test_pool_latest_and_since():
    pool = MessagePool()
    for i in range(3):
        pool.put('ws:in', {'values': [i]})
    assert pool.depth == 3
    assert pool.latest().values() == [2]
    assert [m.seq for m in pool.since(0)] == [1, 2, 3]
    assert [m.seq for m in pool.since(1)] == [2, 3]
    assert pool.latest('nope') is None


def test_pool_ring_evicts_oldest():
    pool = MessagePool(maxlen=2)
    for i in range(4):
        pool.put('x', {'values': [i]})
    assert pool.depth == 2
    assert pool.dropped == 2
    assert pool.latest().values() == [3]


def test_pool_wait_new_times_out_and_wakes():
    pool = MessagePool()
    t0 = time.time()
    assert pool.wait_new(0, timeout=0.2) is None
    assert time.time() - t0 >= 0.15
    threading.Timer(0.1, lambda: pool.put('x', {'values': [9]})).start()
    msg = pool.wait_new(0, timeout=2.0)
    assert msg is not None and msg.values() == [9]


def test_values_from_slots():
    p = PoolMessage(1, 0.0, 'profinet:in',
                    {'frame_id': '0x8000', 'slots': [{'u32': 1500}, {'u16': [1, 2]}]})
    assert p.values() == [1500, 1, 2]


def _wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return bool(cond())


def test_heartbeat_pause_release_resume():
    pool = MessagePool()
    sent = []
    hb = HeartbeatSync(pool, sender=lambda m: (sent.append(m.seq), True)[1], interval=0.05)
    hb.start()
    pool.put('ws:in', {'values': [1]})
    assert _wait(lambda: bool(sent)), '心跳应当推送最新一条'

    hb.pause()
    before = len(sent)
    pool.put('ws:in', {'values': [2]})
    time.sleep(0.3)
    assert len(sent) == before, '断点期间只收不发'

    assert hb.release_once() is True
    assert len(sent) == before + 1, '放行只发最后一条'
    assert sent[-1] == pool.latest().seq
    time.sleep(0.25)
    assert len(sent) == before + 1, '放行后仍保持断点'

    hb.resume()
    assert hb.state == hb.RUNNING
    pool.put('ws:in', {'values': [3]})
    assert _wait(lambda: sent and sent[-1] == pool.latest().seq), '取消断点后恢复心跳'
    hb.stop()
    assert hb.state == hb.IDLE


def test_heartbeat_stop_is_idempotent():
    hb = HeartbeatSync(MessagePool(), sender=lambda m: True, interval=0.05)
    hb.start()
    hb.stop()
    hb.stop()
    assert hb.state == hb.IDLE
