"""Binary frame protocol (see firmware/src/main.cpp).

A5 5A | u16 seq | u32 micros | 6 x i32 raw | u16 CRC   (little endian, 34 bytes)
CRC-16/CCITT-FALSE over the 30 bytes after the sync word.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

SYNC = b"\xa5\x5a"
BODY = struct.Struct("<HI6i")
FRAME_LEN = len(SYNC) + BODY.size + 2  # 34


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


@dataclass
class Frame:
    seq: int
    micros: int
    raw: tuple[int, ...]


def encode(seq: int, micros: int, raw) -> bytes:
    body = BODY.pack(seq & 0xFFFF, micros & 0xFFFFFFFF, *[int(x) for x in raw])
    return SYNC + body + struct.pack("<H", crc16(body))


class Parser:
    """Incremental parser: feed() bytes, get complete valid frames back."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._last_seq: int | None = None
        self.frames = 0
        self.crc_errors = 0
        self.dropped = 0  # frames missing according to seq gaps
        self.garbage = 0  # bytes skipped while hunting for sync

    def feed(self, data: bytes) -> list[Frame]:
        buf = self._buf
        buf += data
        out: list[Frame] = []
        while True:
            i = buf.find(SYNC)
            if i < 0:
                keep = 1 if buf.endswith(SYNC[:1]) else 0
                self.garbage += len(buf) - keep
                del buf[: len(buf) - keep]
                break
            if i:
                self.garbage += i
                del buf[:i]
            if len(buf) < FRAME_LEN:
                break
            body = bytes(buf[2 : FRAME_LEN - 2])
            (crc,) = struct.unpack_from("<H", buf, FRAME_LEN - 2)
            if crc16(body) != crc:
                self.crc_errors += 1
                del buf[:1]  # false sync: rescan from next byte
                continue
            seq, micros, *raw = BODY.unpack(body)
            if self._last_seq is not None:
                self.dropped += (seq - self._last_seq - 1) & 0xFFFF
            self._last_seq = seq
            self.frames += 1
            out.append(Frame(seq, micros, tuple(raw)))
            del buf[:FRAME_LEN]
        return out
