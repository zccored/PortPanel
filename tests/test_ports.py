# -*- coding: utf-8 -*-
"""工业端口：Modbus 编解码/从站/转发，PROFINET RT 隧道帧收发。"""
from __future__ import annotations

import struct

import pytest

from portpanel.engine.ports import ModbusPort, RtPort, wait_for
from portpanel.engine.protocols import modbus as mb
from portpanel.engine.protocols import profinet as pn


# ------------------------------------------------------------------ Modbus 编解码
def test_mbap_roundtrip():
    frame = mb.MBAP(transaction=0x1234, unit=3, pdu=b'\x03\x00\x00\x00\x02')
    raw = frame.to_bytes()
    back = mb.MBAP.from_bytes(raw)
    assert back.transaction == 0x1234 and back.unit == 3
    assert raw[:2] == b'\x12\x34' and raw[2:4] == b'\x00\x00'
    assert back.pdu == b'\x03\x00\x00\x00\x02'


def test_register_helpers():
    assert mb.u32_to_regs(0x0001_0002) == [1, 2]
    assert mb.regs_to_u32(1, 2) == 0x0001_0002
    assert mb.i16(0xFFFF) == -1
    regs = mb.f32_to_regs(3.5)
    assert abs(mb.regs_to_f32(*regs) - 3.5) < 1e-6
    assert mb.decode_registers(b'\x00\x0a\x00\x0b') == [10, 11]


# ------------------------------------------------------------------ 从站 / 客户端
def test_slave_read_and_write():
    writes = []
    with mb.ModbusSlave(on_write=lambda a, v, u: writes.append((a, v))) as slave:
        c = mb.ModbusClient('127.0.0.1', slave.port)
        c.write_registers(10, [111, 222])
        assert c.read_holding(10, 2) == [111, 222]
        c.write_register(12, 333)
        assert c.read_input(10, 3) == [111, 222, 333]
        c.write_coil(0, True)
        assert c.read_coils(0, 2) == [True, False]
    assert writes and writes[0] == (10, [111, 222])


def test_slave_rejects_bad_function():
    with mb.ModbusSlave() as slave:
        c = mb.ModbusClient('127.0.0.1', slave.port)
        with pytest.raises(mb.ModbusError):
            c.request(b'\x63\x00\x00\x00\x01')          # 未实现的功能码


# ------------------------------------------------------------------ 端口转发
def test_modbus_port_forwards_to_target():
    target = mb.ModbusSlave().start()
    port = ModbusPort(listen_port=0, target_host='127.0.0.1',
                      target_port=target.port, unit=1).start()
    try:
        mb.ModbusClient('127.0.0.1', port.listen_port).write_registers(100, [7, 8, 9])
        ok = wait_for(lambda: target.registers.get(100) == 7, timeout=3)
        assert ok, '转发没到达目标从站: %r' % target.registers
        assert target.registers[101] == 8 and target.registers[102] == 9
        assert port.stats.frames_in == 1 and port.stats.frames_out == 1
    finally:
        port.stop()
        target.stop()


def test_modbus_port_mirror_mode():
    """mirror：任何一次写入都把整段寄存器镜像批量推给目标（NX 只认整包）。"""
    nx = mb.ModbusSlave().start()
    port = ModbusPort(listen_port=0, target_host='127.0.0.1', target_port=nx.port,
                      forward='mirror', map_ranges=[(0, 4)]).start()
    try:
        c = mb.ModbusClient('127.0.0.1', port.listen_port)
        c.write_registers(0, [1, 2, 3, 4])
        assert wait_for(lambda: nx.registers.get(0) == 1, timeout=3), nx.registers
        c.write_register(2, 99)
        ok = wait_for(lambda: nx.registers.get(2) == 99 and nx.registers.get(3) == 4,
                      timeout=3)
        assert ok, '镜像转发失败: %r' % nx.registers
    finally:
        port.stop()
        nx.stop()


# ------------------------------------------------------------------ PROFINET RT
def test_profinet_frame_roundtrip():
    spec = [(0x1, 0x1, 4), (0x2, 0x1, 2)]
    slots = pn.build_slots(spec, [0x11223344, 0x5566])
    frame = pn.RtFrame(frame_id=0x8001, slots=slots)
    raw = pn.encode(frame)
    back = pn.decode(raw, spec)
    assert back.frame_id == 0x8001
    assert back.slots[0].data == b'\x11\x22\x33\x44'
    assert back.slots[1].as_u16() == [0x5566]


def test_rt_port_forwards_frame():
    import socket
    spec = [(0x1, 0x1, 4)]
    sink = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sink.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sink.bind(('127.0.0.1', 0))
    sink.listen(1)
    sink_port = sink.getsockname()[1]

    port = RtPort(listen_port=0, target_host='127.0.0.1', target_port=sink_port,
                  slot_spec=spec).start()
    try:
        s = socket.create_connection(('127.0.0.1', port.listen_port), timeout=3)
        s.sendall(pn.encode(pn.RtFrame(0x8000, pn.build_slots(spec, [0xDEADBEEF]))))
        sink.settimeout(3)
        conn, _ = sink.accept()
        with conn:
            got = pn.read_frame(conn, spec)
        assert got.frame_id == 0x8000
        assert got.slots[0].data == b'\xde\xad\xbe\xef'
        s.close()
        assert wait_for(lambda: port.stats.frames_out == 1, timeout=3)
        assert port.stats.frames_in == 1
    finally:
        port.stop()
        sink.close()
