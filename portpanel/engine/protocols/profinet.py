# -*- coding: utf-8 -*-
"""PROFINET RT 帧的最小可用编解码（面向「把 IO 数据搬到另一个口」）。

真实的 PROFINET RT 是二层以太网帧（EtherType 0x8892），普通 socket 抓不到，
所以本模块定义的是**可测试的隧道形态**：

    ┌ 4B magic 'PNRT' ┬ 2B FrameID ┬ 2B 数据长度 ┬ IO 数据 ┐

这样仿真器（扮演 TIA/PLC 的 IO 控制器）和端口节点（转发到 NX 机电模拟）都能在
普通 TCP 上跑；将来接真实现场时，只需把 `read_frame` 换成 pcap/raw socket 的
以太网入口，帧体解析完全复用。

IO 数据布局按「槽位表」描述：[(slot, subslot, byte_len), ...]。
"""
from __future__ import annotations

import socket
import struct
from dataclasses import dataclass, field

__all__ = ['frame_to_json', 'frame_from_json', 'PN_MAGIC', 'RtFrame', 'SlotData', 'read_frame', 'encode', 'decode',
           'ProfinetError', 'build_slots']

PN_MAGIC = b'PNRT'
_HDR = '>4sHH'
_HDR_LEN = struct.calcsize(_HDR)          # 8


class ProfinetError(Exception):
    pass


@dataclass
class SlotData:
    slot: int
    subslot: int
    data: bytes = b''

    def as_u16(self) -> list[int]:
        return list(struct.unpack('>%dH' % (len(self.data) // 2), self.data)) \
            if len(self.data) >= 2 else []


@dataclass
class RtFrame:
    frame_id: int = 0x8000
    slots: list = field(default_factory=list)     # [SlotData]

    def payload(self) -> bytes:
        return b''.join(s.data for s in self.slots)

    def slot_map(self) -> dict:
        return {(s.slot, s.subslot): s.data for s in self.slots}


def build_slots(spec, values=None) -> list:
    """spec=[(slot, subslot, bytes)] → [SlotData]，values 可按顺序覆盖字节内容。"""
    out, idx = [], 0
    for slot, subslot, blen in spec:
        raw = b''
        if values is not None and idx < len(values):
            v = values[idx]
            idx += 1
            if isinstance(v, (bytes, bytearray)):
                raw = bytes(v)[:blen].ljust(blen, b'\x00')
            elif isinstance(v, int):
                raw = struct.pack('>I', v & 0xFFFFFFFF)[-blen:] if blen <= 4 else \
                    struct.pack('>I', v & 0xFFFFFFFF).ljust(blen, b'\x00')
        out.append(SlotData(slot, subslot, raw.ljust(blen, b'\x00')))
    return out


def encode(frame: RtFrame) -> bytes:
    body = frame.payload()
    return struct.pack(_HDR, PN_MAGIC, frame.frame_id, len(body)) + body


def decode(raw: bytes, spec=None) -> RtFrame:
    if len(raw) < _HDR_LEN:
        raise ProfinetError('RT 帧太短: %d' % len(raw))
    magic, frame_id, ln = struct.unpack(_HDR, raw[:_HDR_LEN])
    if magic != PN_MAGIC:
        raise ProfinetError('不是 PNRT 隧道帧: %r' % magic)
    body = raw[_HDR_LEN:_HDR_LEN + ln]
    slots = []
    if spec:
        off = 0
        for slot, subslot, blen in spec:
            slots.append(SlotData(slot, subslot, body[off:off + blen]))
            off += blen
    return RtFrame(frame_id=frame_id, slots=slots)


def read_frame(sock: socket.socket, spec=None) -> RtFrame:
    """从 TCP 隧道读一整帧 RT。"""
    head = _recv_exact(sock, _HDR_LEN)
    magic, frame_id, ln = struct.unpack(_HDR, head)
    if magic != PN_MAGIC:
        raise ProfinetError('不是 PNRT 隧道帧: %r' % magic)
    if ln > 65535:
        raise ProfinetError('RT 帧长度非法: %d' % ln)
    body = _recv_exact(sock, ln)
    slots = []
    if spec:
        off = 0
        for slot, subslot, blen in spec:
            slots.append(SlotData(slot, subslot, body[off:off + blen]))
            off += blen
    return RtFrame(frame_id=frame_id, slots=slots)


def _recv_exact(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('连接提前关闭')
        buf += chunk
    return buf


# ------------------------------------------------------------------ JSON 互转（WebSocket）
def frame_to_json(frame: 'RtFrame', spec=None) -> dict:
    """RT 帧 → WS 文本报文：{"frame_id":32768,"slots":[{"slot":1,"subslot":1,"u32":[...]}]}"""
    out = {'frame_id': frame.frame_id, 'slots': []}
    for s in frame.slots:
        item = {'slot': s.slot, 'subslot': s.subslot, 'hex': s.data.hex()}
        if len(s.data) % 2 == 0 and s.data:
            item['u16'] = list(struct.unpack('>%dH' % (len(s.data) // 2), s.data))
        if len(s.data) == 4:
            item['u32'] = struct.unpack('>I', s.data)[0]
        out['slots'].append(item)
    return out


def frame_from_json(obj: dict, spec=None) -> 'RtFrame':
    """WS 文本报文 → RT 帧。槽位字节优先取 hex，其次 u32/u16。"""
    fid = int(obj.get('frame_id', 0x8000))
    slots = []
    lengths = {(a, b): n for a, b, n in (spec or [])}
    for item in obj.get('slots') or []:
        slot = int(item.get('slot', 1))
        sub = int(item.get('subslot', 1))
        blen = lengths.get((slot, sub), 4)
        if item.get('hex'):
            raw = bytes.fromhex(str(item['hex']))
        elif 'u32' in item:
            raw = struct.pack('>I', int(item['u32']) & 0xFFFFFFFF)
        elif 'u16' in item:
            raw = b''.join(struct.pack('>H', int(v) & 0xFFFF) for v in item['u16'])
        else:
            raw = b''
        slots.append(SlotData(slot, sub, raw[:blen].ljust(blen, b'\x00')))
    return RtFrame(frame_id=fid, slots=slots)
