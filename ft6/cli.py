"""Command line. Everything the GUI does is here too:

  ft6 stream [--raw] [--count N]        live wrench (or raw counts)        GUI: readouts / plots
  ft6 log FILE --seconds N              CSV of raw + wrench                GUI: Start logging
  ft6 tare                              no-load average -> tare            GUI: Tare
  ft6 calib new [--mode gains|full]     start a calibration session        GUI: Calibrate... > mode
  ft6 calib plan --mass KG --radius MM  weights at centre + round a circle GUI: Add plan
  ft6 calib add --mass KG --at X Y [Z] [--dir DX DY DZ]                    GUI: Add case
  ft6 calib list                        cases, status, next prompt         GUI: table + prompt
  ft6 calib capture [N]                 average case N (default: next)     GUI: Capture
  ft6 calib remove N                    drop case N                        GUI: Remove
  ft6 calib fit [--save]                fit, report, optionally apply      GUI: Fit / Save & apply
  ft6 calib wizard                      tare + every case + fit, prompted step by step
Case numbers are 1-based. --fake runs against a simulated sensor (captures get the case's load).
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np

from .calib import AXES, DEFAULT_PATH, Calibration
from .device import FakeDevice, open_device
from .session import DEFAULT_SESSION, MODES, Session


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


def do_tare(dev, calib: Calibration, path, seconds: float) -> None:
    if isinstance(dev, FakeDevice):
        dev.set_wrench(np.zeros(6))
        dev.capture_mean(0.1)
    calib.tare = dev.capture_mean(seconds)
    calib.save(path)
    print("tare", np.round(calib.tare).astype(int).tolist(), "->", path)


def ask(text: str) -> str:
    try:
        return input(text).strip().lower()
    except EOFError:
        return "q"


def wizard(a, dev, calib: Calibration, ses: Session) -> int:
    print(f"Calibration wizard, mode '{ses.mode}': {MODES[ses.mode]}")
    if not ses.cases:
        print("No cases: run `ft6 calib plan --mass KG --radius MM` first (or `calib add`).")
        return 1
    if ask("Step 1: take EVERYTHING off the sensor, then Enter (s = keep the current tare): ") != "s":
        do_tare(dev, calib, a.calib, a.seconds)
    while (i := ses.next_index()) is not None:
        r = ask(ses.prompt(i) + "\n  Enter = capture, s = skip (remove), q = quit: ")
        if r == "q":
            ses.save()
            print("stopped; captures so far are saved, rerun `ft6 calib wizard` to continue")
            return 1
        if r == "s":
            ses.remove(i)
        else:
            ses.capture(i, dev, a.seconds)
            print(f"  captured case {i + 1}")
        ses.save()
    cal, rep = ses.fit(calib.tare)
    print(rep.text())
    if ask("Save and apply this calibration? [y/N] ") == "y":
        cal.save(a.calib)
        print("saved ->", a.calib)
    return 0


def calib_cmd(a) -> int:
    calib = Calibration.load_or_default(a.calib)
    if a.ccmd == "new":
        ses = Session(a.mode, path=a.session)
        ses.save()
        print(f"new session ({a.mode}): {MODES[a.mode]} -> {a.session}")
        return 0
    ses = Session.load(a.session)
    if a.ccmd == "plan":
        ids = ses.plan_vertical(a.mass, a.radius, a.n)
        ses.save()
        print(f"added cases {ids[0] + 1}-{ids[-1] + 1}\n{ses.table()}\nnext: {ses.prompt()}")
    elif a.ccmd == "add":
        i = ses.add(a.mass, a.at, a.dir, a.label)
        ses.save()
        print(f"added case {i + 1}: {ses.cases[i].describe()}")
    elif a.ccmd == "remove":
        ses.remove(a.n - 1)
        ses.save()
        print(ses.table())
    elif a.ccmd == "list":
        print(f"mode {ses.mode}: {MODES[ses.mode]}\n{ses.table()}\nnext: {ses.prompt()}")
        if not np.any(calib.tare):
            print("(no tare yet: `ft6 tare` with the sensor unloaded)")
    elif a.ccmd == "fit":
        try:
            cal, rep = ses.fit(calib.tare)
        except ValueError as e:
            print(f"cannot fit: {e}")
            return 1
        print(rep.text())
        if a.save:
            cal.save(a.calib)
            print("saved ->", a.calib)
    elif a.ccmd in ("capture", "wizard"):
        with open_device(a.port, a.fake) as dev:
            if a.ccmd == "wizard":
                return wizard(a, dev, calib, ses)
            i = ses.next_index() if a.n is None else a.n - 1
            if i is None:
                print("all cases captured; `ft6 calib fit`")
                return 0
            print(ses.prompt(i))
            ses.capture(i, dev, a.seconds)
            ses.save()
            print(f"captured case {i + 1}; next: {ses.prompt()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ft6", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("--fake", action="store_true", help="synthetic device, no hardware")
    ap.add_argument("--calib", default=str(DEFAULT_PATH), help="calibration JSON")
    ap.add_argument("--session", default=str(DEFAULT_SESSION), help="calibration session JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("stream", help="print wrench until Ctrl-C")
    p.add_argument("--raw", action="store_true", help="raw counts instead of wrench")
    p.add_argument("--count", type=int, default=0, help="stop after N lines (0 = until Ctrl-C)")
    p = sub.add_parser("log", help="log raw + wrench to CSV")
    p.add_argument("file")
    p.add_argument("--seconds", type=float, default=10)
    p = sub.add_parser("tare", help="average raw at no load and store as tare")
    p.add_argument("--seconds", type=float, default=2)

    c = sub.add_parser("calib", help="calibration session (see top)").add_subparsers(dest="ccmd", required=True)
    p = c.add_parser("new")
    p.add_argument("--mode", choices=list(MODES), default="gains")
    p = c.add_parser("plan", help="weights at the centre and round a circle")
    p.add_argument("--mass", type=float, required=True, help="kg")
    p.add_argument("--radius", type=float, required=True, help="mm from the sensor centre")
    p.add_argument("--n", type=int, default=6, help="points on the circle")
    p = c.add_parser("add")
    p.add_argument("--mass", type=float, required=True, help="kg")
    p.add_argument("--at", type=float, nargs="+", required=True, metavar="MM", help="x y [z] in mm")
    p.add_argument("--dir", type=float, nargs=3, default=[0, 0, -1], metavar=("DX", "DY", "DZ"))
    p.add_argument("--label", default="")
    p = c.add_parser("remove")
    p.add_argument("n", type=int)
    c.add_parser("list")
    p = c.add_parser("capture")
    p.add_argument("n", type=int, nargs="?")
    p.add_argument("--seconds", type=float, default=2)
    p = c.add_parser("fit")
    p.add_argument("--save", action="store_true")
    p = c.add_parser("wizard")
    p.add_argument("--seconds", type=float, default=2)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "calib":
        return calib_cmd(a)
    calib = Calibration.load_or_default(a.calib)
    with open_device(a.port, a.fake) as dev:
        if a.cmd == "stream":
            idx, n = dev.buffer.total, 0
            try:
                while not a.count or n < a.count:
                    idx, rows = dev.buffer.read_since(idx)
                    if len(rows):
                        if a.raw:
                            print("  ".join(f"leg{i + 1}={int(v):+9d}" for i, v in enumerate(rows[-1, 1:])), flush=True)
                        else:
                            w = calib.wrench(rows[-1, 1:])
                            print("  ".join(f"{nm}={v:+9.4f}" for nm, v in zip(AXES, w)), flush=True)
                        n += 1
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
            do_tare(dev, calib, a.calib, a.seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
