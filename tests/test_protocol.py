import numpy as np
from ft6.protocol import FRAME_LEN, Parser, crc16, encode


def test_crc_check_value():
    assert crc16(b"123456789") == 0x29B1  # CRC-16/CCITT-FALSE


def test_roundtrip_and_split_feed():
    raw = [1, -2, 8388607, -8388608, 0, 42]
    data = encode(7, 123456, raw)
    assert len(data) == FRAME_LEN
    p = Parser()
    out = []
    for i in range(len(data)):  # byte at a time
        out += p.feed(data[i:i + 1])
    assert len(out) == 1 and out[0].seq == 7 and out[0].micros == 123456
    assert list(out[0].raw) == raw


def test_resync_after_garbage_and_false_sync():
    p = Parser()
    junk = b"\x00\xa5\x5a\xa5\x13\x37" + b"\xa5"
    stream = junk + encode(0, 0, [1] * 6) + b"\xff\xff" + encode(1, 10, [2] * 6)
    out = p.feed(stream)
    assert [f.seq for f in out] == [0, 1]
    assert p.dropped == 0 and p.garbage > 0


def test_crc_failure_dropped_and_seq_gap():
    p = Parser()
    bad = bytearray(encode(1, 5, [9] * 6))
    bad[10] ^= 0xFF
    out = p.feed(encode(0, 0, [0] * 6) + bytes(bad) + encode(2, 20, [3] * 6))
    assert [f.seq for f in out] == [0, 2]
    assert p.crc_errors >= 1 and p.dropped == 1


def test_seq_wraparound_no_drop():
    p = Parser()
    p.feed(encode(65535, 0, [0] * 6) + encode(0, 1, [0] * 6))
    assert p.dropped == 0
