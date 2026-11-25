"""Calibration session: the list of load cases, their captures, the fit.

Shared by the CLI (`ft6 calib ...`) and the GUI (Calibrate... window) and stored as JSON
(default ~/.config/ft6/session.json), so you can start in one and finish in the other.

Two modes:
  gains  Weights only. Put known masses on the top plate at known points, hanging straight
         down. Fits one gain per leg (N per count); the rest comes from the leg geometry.
         Fz (thrust), Mx, My are checked by the cases; Fx, Fy, Mz (torque) rely on geometry.
  full   Full 6x6 matrix. Needs sideways string pulls (over a pulley) as well, 12-20 cases.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import calib as C
from .geometry import Geometry

DEFAULT_SESSION = Path.home() / ".config" / "ft6" / "session.json"
MODES = {
    "gains": "Weights only: fits the 6 leg gains (gives thrust Fz, Mx, My; Fx, Fy, Mz from geometry)",
    "full": "Full 6x6: weights AND sideways string pulls over a pulley, 12-20 cases",
}
MIN_CASES = {"gains": 3, "full": 6}


@dataclass
class Case:
    mass_kg: float
    point_mm: list
    direction: list = field(default_factory=lambda: [0.0, 0.0, -1.0])
    label: str = ""
    raw: list | None = None
    n: int = 0

    def wrench(self) -> np.ndarray:
        return C.wrench_from_mass(self.mass_kg, self.point_mm, self.direction)

    @property
    def captured(self) -> bool:
        return self.raw is not None

    def describe(self) -> str:
        x, y, z = self.point_mm
        d = np.asarray(self.direction, float)
        how = ("hanging straight down" if np.allclose(d / np.linalg.norm(d), [0, 0, -1])
               else f"string pulling along ({d[0]:g}, {d[1]:g}, {d[2]:g}) over a pulley")
        g = f"{self.mass_kg * 1000:.1f} g" if self.mass_kg < 1 else f"{self.mass_kg:.3f} kg"
        lab = f" [{self.label}]" if self.label else ""
        return f"put {g} at x={x:+.1f} y={y:+.1f} z={z:+.1f} mm, {how}{lab}"


class Session:
    def __init__(self, mode: str = "gains", cases=None, path=DEFAULT_SESSION) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {list(MODES)}")
        self.mode, self.cases, self.path = mode, list(cases or []), Path(path)

    # -- storage
    @classmethod
    def load(cls, path=DEFAULT_SESSION) -> "Session":
        path = Path(path)
        if not path.exists():
            return cls(path=path)
        d = json.loads(path.read_text())
        return cls(d.get("mode", "gains"), [Case(**c) for c in d.get("cases", [])], path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"mode": self.mode, "cases": [asdict(c) for c in self.cases]}, indent=1))

    # -- editing
    def add(self, mass_kg, point_mm, direction=(0, 0, -1), label="") -> int:
        p = [float(v) for v in point_mm] + [0.0] * (3 - len(point_mm))
        if mass_kg <= 0:
            raise ValueError("mass must be > 0")
        if np.linalg.norm(direction) == 0:
            raise ValueError("direction must be non-zero")
        self.cases.append(Case(float(mass_kg), p[:3], [float(v) for v in direction], label))
        return len(self.cases) - 1

    def remove(self, i: int) -> None:
        del self.cases[i]

    def plan_vertical(self, mass_kg: float, radius_mm: float, n_ring: int = 6) -> list[int]:
        """Centre, then n_ring points on a circle of radius_mm (first one on +x)."""
        out = [self.add(mass_kg, [0, 0, 0], label="centre")]
        for k in range(n_ring):
            a = 2 * np.pi * k / n_ring
            out.append(self.add(mass_kg, [radius_mm * np.cos(a), radius_mm * np.sin(a), 0],
                                label=f"ring {round(np.degrees(a))} deg"))
        return out

    def set_raw(self, i: int, raw, n: int) -> None:
        self.cases[i].raw = [float(v) for v in raw]
        self.cases[i].n = int(n)

    def clear_captures(self) -> None:
        for c in self.cases:
            c.raw, c.n = None, 0

    # -- prompting
    def next_index(self) -> int | None:
        return next((i for i, c in enumerate(self.cases) if not c.captured), None)

    def prompt(self, i: int | None = None) -> str:
        if not self.cases:
            return "No load cases yet: add a plan (weights at the centre and round a circle) or single cases."
        i = self.next_index() if i is None else i
        done = sum(c.captured for c in self.cases)
        if i is None:
            need = MIN_CASES[self.mode]
            return (f"All {len(self.cases)} cases captured: Fit." if done >= need
                    else f"{done} captured, need at least {need}: add cases.")
        return f"Case {i + 1}/{len(self.cases)}: {self.cases[i].describe()}. Let it settle, then Capture."

    def table(self) -> str:
        rows = []
        for i, c in enumerate(self.cases):
            st = f"captured ({c.n} samples)" if c.captured else "-"
            rows.append(f"{i + 1:3d}  {c.describe():70s}  {st}")
        return "\n".join(rows) or "(no cases)"

    # -- capture / fit
    def capture(self, i: int, dev, seconds: float = 2.0) -> np.ndarray:
        """Average `seconds` of raw data for case i. A FakeDevice is given the case's load."""
        from .device import FakeDevice
        if isinstance(dev, FakeDevice):
            dev.set_wrench(self.cases[i].wrench())
            dev.capture_mean(0.1)  # flush samples from before the load was applied
        raw = dev.capture_mean(seconds)
        self.set_raw(i, raw, max(1, int(seconds * getattr(dev, "rate", 80))))
        return raw

    def fit(self, tare, geom: Geometry | None = None):
        done = [c for c in self.cases if c.captured]
        if len(done) < MIN_CASES[self.mode]:
            raise ValueError(f"{self.mode} mode needs at least {MIN_CASES[self.mode]} captured cases, have {len(done)}")
        if not np.any(np.asarray(tare)):
            raise ValueError("no tare: unload the sensor and Tare first")
        raws = np.array([c.raw for c in done])
        W = np.array([c.wrench() for c in done])
        if self.mode == "gains":
            return C.fit_gains(raws, W, tare, geom or Geometry.load())
        return C.fit(raws, W, tare)
