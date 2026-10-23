# -*- coding: utf-8 -*-
"""Modbus TCP —— MBAP 头 + PDU 编解码 + 客户端 + 可编程从站（仿真用）。

只做工业互联网这一层真正需要的：
- FC3 读保持寄存器 / FC4 读输入寄存器 / FC6 写单寄存器 / FC16 写多寄存器
- FC1 读线圈 / FC5 写单线圈 / FC15 写多线圈（给「启停/报警」这类位信号）

约定：地址 0 基（PLC 里常写 40001，偏移 0 = 40001）。
"""
from __future__ import annotations

import socket
import struct
import threading
from dataclasses import dataclass, field

__all__ = ['MBAP', 'ModbusError', 'ModbusClient', 'ModbusSlave', 'decode_registers',
           'encode_registers', 'u32_to_regs', 'regs_to_u32', 'i16', 'f32_to_regs',
           'regs_to_f32']

MBAP_FMT = '>HHHB'
MBAP_LEN = struct.calcsize(MBAP_FMT)          # 7

# 功能码
FC_READ_COILS = 0x01
FC_READ_DISCRETE = 0x02
FC_READ_HOLDING = 0x03
FC_READ_INPUT = 0x04
FC_WRITE_COIL = 0x05
FC_WRITE_REG = 0x06
FC_WRITE_COILS = 0x0F
FC_WRITE_REGS = 0x10

_EXC_NAMES = {1: '非法功能', 2: '非法数据地址', 3: '非法数据值', 4: '从站故障'}


class ModbusError(Exception):
    def __init__(self, code: int, msg: str = ''):
        self.code = code
        super().__init__('Modbus 异常 %s: %s' % (code, msg or _EXC_NAMES.get(code, '')))


@dataclass
class MBAP:
    transaction: int
    unit: int
    pdu: bytes
    protocol: int = 0
    length: int = 0

    def to_bytes(self) -> bytes:
        ln = len(self.pdu) + 1                     # unit + pdu
        return struct.pack(MBAP_FMT, self.transaction, self.protocol, ln,
                           self.unit) + self.pdu

    @classmethod
    def from_bytes(cls, raw: bytes) -> 'MBAP':
        if len(raw) < MBAP_LEN + 1:
            raise ValueError('MBAP 帧太短: %d' % len(raw))
        tid, proto, ln, unit = struct.unpack(MBAP_FMT, raw[:MBAP_LEN])
        pdu = raw[MBAP_LEN:MBAP_LEN - 1 + ln]
        if len(pdu) < len(raw) - MBAP_LEN:
            pdu = raw[MBAP_LEN:]
        return cls(transaction=tid, unit=unit, pdu=pdu, protocol=proto, length=ln)

    @classmethod
    def read_frame(cls, sock: socket.socket) -> 'MBAP':
        """从 socket 读一整帧（阻塞，读到 length 为止）。"""
        head = _recv_exact(sock, MBAP_LEN)
        tid, proto, ln, unit = struct.unpack(MBAP_FMT, head)
        if ln < 2 or ln > 260:
            raise ValueError('MBAP length 非法: %d' % ln)
        pdu = _recv_exact(sock, ln - 1)
        return cls(transaction=tid, unit=unit, pdu=pdu, protocol=proto, length=ln)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('连接提前关闭（还差 %d 字节）' % (n - len(buf)))
        buf += chunk
    return buf


# ------------------------------------------------------------------ 编解码助手
def decode_registers(raw: bytes, signed: bool = False) -> list[int]:
    out = []
    for i in range(0, len(raw) - 1, 2):
        v = struct.unpack('>H', raw[i:i + 2])[0]
        if signed and v >= 0x8000:
            v -= 0x10000
        out.append(v)
    return out


def encode_registers(values) -> bytes:
    return b''.join(struct.pack('>H', int(v) & 0xFFFF) for v in values)


def u32_to_regs(value: int) -> list[int]:
    value = int(value) & 0xFFFFFFFF
    return [(value >> 16) & 0xFFFF, value & 0xFFFF]


def regs_to_u32(hi: int, lo: int) -> int:
    return ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)


def i16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def f32_to_regs(value: float) -> list[int]:
    return list(struct.unpack('>HH', struct.pack('>f', float(value))))


def regs_to_f32(hi: int, lo: int) -> float:
    return struct.unpack('>f', struct.pack('>HH', hi & 0xFFFF, lo & 0xFFFF))[0]


def _bits_to_bytes(values) -> bytes:
    n = len(values)
    out = bytearray((n + 7) // 8)
    for i, v in enumerate(values):
        if v:
            out[i // 8] |= 1 << (i % 8)
    return bytes(out)


# ------------------------------------------------------------------ 客户端
class ModbusClient:
    """最小 Modbus TCP 客户端（每次调用建连，工业现场短连接更稳）。"""

    def __init__(self, host: str, port: int = 502, timeout: float = 3.0, unit: int = 1):
        self.host, self.port = host, int(port)
        self.timeout, self.unit = timeout, unit
        self._tid = 0

    def _next_tid(self) -> int:
        self._tid = (self._tid + 1) & 0xFFFF
        return self._tid

    def request(self, pdu: bytes, unit: int | None = None) -> bytes:
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
            s.settimeout(self.timeout)
            s.sendall(MBAP(self._next_tid(), unit or self.unit, pdu).to_bytes())
            resp = MBAP.read_frame(s)
        if resp.pdu and resp.pdu[0] & 0x80:
            raise ModbusError(resp.pdu[1] if len(resp.pdu) > 1 else 0)
        return resp.pdu

    def read_holding(self, addr: int, count: int = 1) -> list[int]:
        pdu = bytes([FC_READ_HOLDING]) + struct.pack('>HH', addr, count)
        r = self.request(pdu)
        return decode_registers(r[2:2 + 2 * count])

    def read_input(self, addr: int, count: int = 1) -> list[int]:
        pdu = bytes([FC_READ_INPUT]) + struct.pack('>HH', addr, count)
        r = self.request(pdu)
        return decode_registers(r[2:2 + 2 * count])

    def write_register(self, addr: int, value: int) -> None:
        self.request(bytes([FC_WRITE_REG]) + struct.pack('>HH', addr, int(value) & 0xFFFF))

    def write_registers(self, addr: int, values) -> None:
        vals = [int(v) & 0xFFFF for v in values]
        pdu = bytes([FC_WRITE_REGS]) + struct.pack('>HHB', addr, len(vals),
                                                   len(vals) * 2) + encode_registers(vals)
        self.request(pdu)

    def read_coils(self, addr: int, count: int = 1) -> list[bool]:
        r = self.request(bytes([FC_READ_COILS]) + struct.pack('>HH', addr, count))
        nbytes = r[1]
        raw = r[2:2 + nbytes]
        return [bool(raw[i // 8] & (1 << (i % 8))) for i in range(count)]

    def write_coil(self, addr: int, value: bool) -> None:
        self.request(bytes([FC_WRITE_COIL]) +
                     struct.pack('>HH', addr, 0xFF00 if value else 0x0000))


# ------------------------------------------------------------------ 从站（仿真）
@dataclass
class ModbusSlave:
    """可编程 Modbus TCP 从站：用寄存器镜像模拟 PLC 暴露的数据。

    on_write(addr, values, unit) 在客户端写入后回调，用于「转发到另一个端口」。
    """
    registers: dict = field(default_factory=dict)      # addr -> 16bit
    coils: dict = field(default_factory=dict)          # addr -> bool
    unit: int = 1
    host: str = '127.0.0.1'
    port: int = 0
    on_write: object = None

    _srv: socket.socket | None = None
    _thread: object = None
    _stop: bool = False
    _clients: list = field(default_factory=list)
    last_request: dict = field(default_factory=dict)

    # ---- 生命周期 ----
    def start(self) -> 'ModbusSlave':
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port))
        self._srv.listen(8)
        self.port = self._srv.getsockname()[1]
        self._stop = False
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop = True
        try:
            if self._srv:
                self._srv.close()
        except Exception:
            pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    @property
    def url(self) -> str:
        return '%s:%d' % (self.host, self.port)

    # ---- 服务端 ----
    def _accept_loop(self):
        while not self._stop:
            try:
                conn, _addr = self._srv.accept()
            except OSError:
                return
            t = threading.Thread(target=self._serve, args=(conn,), daemon=True)
            t.start()
            self._clients.append(t)

    def _serve(self, conn: socket.socket):
        with conn:
            conn.settimeout(10)
            try:
                while not self._stop:
                    frame = MBAP.read_frame(conn)
                    reply = self._handle(frame)
                    if reply is not None:
                        conn.sendall(reply.to_bytes())
            except (ConnectionError, OSError, ValueError):
                return

    def _handle(self, frame: MBAP) -> MBAP | None:
        pdu = frame.pdu
        if not pdu:
            return None
        fc, body = pdu[0], pdu[1:]
        try:
            if fc in (FC_READ_HOLDING, FC_READ_INPUT):
                addr, count = struct.unpack('>HH', body[:4])
                self.last_request = {'fc': fc, 'addr': addr, 'count': count}
                data = encode_registers([self.registers.get(addr + i, 0) for i in range(count)])
                return MBAP(frame.transaction, frame.unit, bytes([fc, len(data)]) + data)
            if fc == FC_WRITE_REG:
                addr, value = struct.unpack('>HH', body[:4])
                self.registers[addr] = value
                self._fire(addr, [value], frame.unit)
                return MBAP(frame.transaction, frame.unit, pdu[:4])
            if fc == FC_WRITE_REGS:
                addr, count, nbytes = struct.unpack('>HHB', body[:5])
                vals = decode_registers(body[5:5 + nbytes])
                for i, v in enumerate(vals):
                    self.registers[addr + i] = v
                self._fire(addr, vals, frame.unit)
                return MBAP(frame.transaction, frame.unit,
                            bytes([fc]) + struct.pack('>HH', addr, count))
            if fc == FC_READ_COILS:
                addr, count = struct.unpack('>HH', body[:4])
                data = _bits_to_bytes([self.coils.get(addr + i, False) for i in range(count)])
                return MBAP(frame.transaction, frame.unit, bytes([fc, len(data)]) + data)
            if fc == FC_WRITE_COIL:
                addr, value = struct.unpack('>HH', body[:4])
                self.coils[addr] = bool(value & 0xFF00)
                self._fire(addr, [self.coils[addr]], frame.unit)
                return MBAP(frame.transaction, frame.unit, pdu[:4])
            raise ModbusError(1)
        except ModbusError as exc:
            return MBAP(frame.transaction, frame.unit, bytes([fc | 0x80, exc.code]))
        except Exception:
            return MBAP(frame.transaction, frame.unit, bytes([fc | 0x80, 4]))

    def _fire(self, addr: int, values, unit: int):
        if callable(self.on_write):
            try:
                self.on_write(addr, list(values), unit)
            except Exception:
                pass
