"""Command line: ft6 stream | log FILE --seconds N | tare"""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import numpy as np

from .calib import AXES, DEFAULT_PATH, Calibration
from .device import open_device


def log_rows(dev, calib: Calibration, writer, seconds: float) -> int:
    idx, n, t_end = dev.buffer.total, 0, time.time() + seconds
    while time.time() < t_end:
        idx, rows = dev.buffer.read_since(idx)
        for r in rows:
            writer.writerow([f"{r[0]:.6f}", *[int(x) for x in r[1:]],
                             *[f"{x:.6g}" for x in calib.wrench(r[1:])]])
        n += len(rows)
        time.sleep(0.02)
    return n


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="ft6")
    ap.add_argument("--port")
    ap.add_argument("--fake", action="store_true", help="synthetic device, no hardware")
    ap.add_argument("--calib", default=str(DEFAULT_PATH), help="calibration JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stream", help="print wrench until Ctrl-C")
    p = sub.add_parser("log", help="log raw + wrench to CSV")
    p.add_argument("file")
    p.add_argument("--seconds", type=float, default=10)
    p = sub.add_parser("tare", help="average raw at no load and store as tare")
    p.add_argument("--seconds", type=float, default=2)
    a = ap.parse_args(argv)

    calib = Calibration.load_or_default(a.calib)
    with open_device(a.port, a.fake) as dev:
        if a.cmd == "stream":
            idx = dev.buffer.total
            try:
                while True:
                    idx, rows = dev.buffer.read_since(idx)
                    if len(rows):
                        w = calib.wrench(rows[-1, 1:])
                        print("  ".join(f"{n}={v:+9.4f}" for n, v in zip(AXES, w)), flush=True)
                    time.sleep(0.1)
            except KeyboardInterrupt:
                pass
        elif a.cmd == "log":
            Path(a.file).parent.mkdir(parents=True, exist_ok=True)
            with open(a.file, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["t", *[f"raw{i}" for i in range(6)], *AXES])
                n = log_rows(dev, calib, w, a.seconds)
            print(f"wrote {n} rows to {a.file}")
        elif a.cmd == "tare":
            calib.tare = dev.capture_mean(a.seconds)
            calib.save(a.calib)
            print("tare", np.round(calib.tare).astype(int).tolist(), "->", a.calib)
