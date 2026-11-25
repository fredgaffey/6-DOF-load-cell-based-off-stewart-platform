"""Data sources: real serial device and a synthetic FakeDevice.

Both run one thread that owns the data source and appends rows
[t_seconds, raw0..raw5] to a thread-safe RingBuffer; everything else only reads
the buffer.
"""
from __future__ import annotations

import threading
import time

import numpy as np

from .calib import Calibration
from .geometry import Geometry
from .protocol import Parser, encode

BAUD = 921600
# (vid, pid) of common ESP32 dev-board USB-UART bridges: CP210x, CH340/CH341
USB_IDS = {(0x10C4, 0xEA60), (0x1A86, 0x7523), (0x1A86, 0x55D4)}


class RingBuffer:
    def __init__(self, capacity: int = 20000) -> None:
        self._a = np.zeros((capacity, 7))
        self._cap = capacity
        self._n = 0  # total rows ever appended
        self._lock = threading.Lock()

    def extend(self, rows) -> None:
        rows = np.asarray(rows, float).reshape(-1, 7)[-self._cap:]
        with self._lock:
            idx = (self._n + np.arange(len(rows))) % self._cap
            self._a[idx] = rows
            self._n += len(rows)

    @property
    def total(self) -> int:
        return self._n

    def read_since(self, idx: int) -> tuple[int, np.ndarray]:
        """Rows appended after total==idx (oldest ones lost if the ring wrapped)."""
        with self._lock:
            n = self._n
            k = min(n - idx, self._cap)
            return n, self._a[(n - k + np.arange(k)) % self._cap].copy()

    def latest(self, count: int) -> np.ndarray:
        return self.read_since(max(0, self._n - count))[1]


class _Source:
    def __init__(self) -> None:
        self.buffer = RingBuffer()
        self.parser = Parser()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t_prev: int | None = None
        self._t_wrap = 0
        self.error: str | None = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._guard, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *a) -> None:
        self.stop()

    def _guard(self) -> None:
        try:
            self._run()
        except Exception as e:  # report instead of dying silently
            self.error = repr(e)

    def _ingest(self, data: bytes) -> None:
        frames = self.parser.feed(data)
        rows = []
        for f in frames:
            if self._t_prev is not None and f.micros < self._t_prev - 2 ** 31:
                self._t_wrap += 2 ** 32  # micros() wrapped (every 71 min)
            self._t_prev = f.micros
            rows.append([(f.micros + self._t_wrap) * 1e-6, *f.raw])
        if rows:
            self.buffer.extend(rows)

    def capture_mean(self, seconds: float = 1.0, timeout: float = 30.0) -> np.ndarray:
        """Block until `seconds` of data (by device time) arrived; return mean raw (6,)."""
        idx = self.buffer.total
        chunks, t0, t_end = [], None, time.time() + timeout
        while time.time() < t_end:
            idx, rows = self.buffer.read_since(idx)
            if len(rows):
                chunks.append(rows)
                t0 = rows[0, 0] if t0 is None else t0
                if rows[-1, 0] - t0 >= seconds:
                    return np.vstack(chunks)[:, 1:].mean(0)
            time.sleep(0.01)
        raise TimeoutError(self.error or "no data from device")

    def _run(self) -> None:
        raise NotImplementedError


def find_port() -> str:
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    for p in ports:
        if (p.vid, p.pid) in USB_IDS:
            return p.device
    raise RuntimeError("no CP210x/CH340 serial port found (use --port); saw: "
                       + (", ".join(p.device for p in ports) or "none"))


class Device(_Source):
    def __init__(self, port: str | None = None, baud: int = BAUD) -> None:
        super().__init__()
        self.port = port or find_port()
        self.baud = baud

    def _run(self) -> None:
        import serial
        with serial.Serial(self.port, self.baud, timeout=0.05) as ser:
            while not self._stop.is_set():
                data = ser.read(max(1, ser.in_waiting))
                if data:
                    self._ingest(data)


class FakeDevice(_Source):
    """Synthesises frames from a known C_true: raw = C_true^-1 w + tare_true + noise."""

    def __init__(self, rate: float = 80.0, noise: float = 20.0, seed: int = 0,
                 C_true=None, tare_true=None, wrench_fn=None, crosstalk: float = 0.02) -> None:
        super().__init__()
        rng = np.random.default_rng(seed)
        self.rng = rng
        if C_true is None:  # geometry guess with +-10% gains and 2% crosstalk
            C0 = Calibration.from_geometry(Geometry.load()).C
            C_true = C0 @ np.diag(1 + 0.1 * rng.standard_normal(6)) @ (np.eye(6) + crosstalk * rng.standard_normal((6, 6)))
        self.C_true = np.asarray(C_true, float)
        self.tare_true = np.round(rng.uniform(-2e5, 2e5, 6)) if tare_true is None else np.asarray(tare_true, float)
        self.rate, self.noise = rate, noise
        self._Cinv = np.linalg.inv(self.C_true)
        self._w = np.zeros(6)
        self._fn = wrench_fn
        self._seq = 0

    def set_wrench(self, w) -> None:
        """Constant applied wrench (disables the demo signal)."""
        self._fn = None
        self._w = np.asarray(w, float)

    def _wrench_at(self, t: float) -> np.ndarray:
        if self._fn is not None:
            return np.asarray(self._fn(t), float)
        return self._w

    def synth(self, w, n: int = 1) -> np.ndarray:
        """n noisy raw samples (n,6) for applied wrench w (synchronous, no thread)."""
        raw = self._Cinv @ np.asarray(w, float) + self.tare_true
        return np.round(raw + self.noise * self.rng.standard_normal((n, 6)))

    def _run(self) -> None:
        t0 = time.perf_counter()
        sent = 0
        while not self._stop.is_set():
            t = time.perf_counter() - t0
            due = int(t * self.rate) + 1
            data = b""
            for i in range(sent, due):
                raw = self.synth(self._wrench_at(i / self.rate))[0]
                data += encode(self._seq, int(i / self.rate * 1e6), raw)
                self._seq += 1
            sent = due
            if data:
                self._ingest(data)
            time.sleep(0.002)


def demo_wrench(t: float) -> np.ndarray:
    """Default FakeDevice signal for the demo: slow sinusoids on all axes."""
    return np.array([2 * np.sin(t), 2 * np.sin(0.7 * t), 10 + 5 * np.sin(0.5 * t),
                     0.1 * np.sin(0.9 * t), 0.1 * np.sin(1.3 * t), 0.05 * np.sin(1.7 * t)])


def open_device(port: str | None = None, fake: bool = False) -> _Source:
    return FakeDevice(wrench_fn=demo_wrench) if fake else Device(port)
