"""Calibration: wrench = C @ (raw - tare), C is 6x6 (N, N*m per count)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .geometry import Geometry

G = 9.80665
AXES = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]
UNITS = ["N", "N", "N", "N*m", "N*m", "N*m"]
DEFAULT_PATH = Path.home() / ".config" / "ft6" / "calibration.json"
# Rough guess: 5 kg cell, ~1 mV/V, HX711 gain 128 -> about 1.8e-5 N/count.
DEFAULT_K = 1.8e-5


def wrench_from_force(force_n, r_mm) -> np.ndarray:
    """Force vector (N) applied at point r (mm, sensor frame) -> [F, r x F] (N, N*m)."""
    f = np.asarray(force_n, float)
    return np.concatenate([f, np.cross(np.asarray(r_mm, float) / 1000.0, f)])


def wrench_from_mass(m_kg, r_mm, direction=(0, 0, -1)) -> np.ndarray:
    """Mass hung at r (mm); gravity along -z by default. A string over a pulley
    just changes `direction` (unit vector of the string pull on the sensor)."""
    d = np.asarray(direction, float)
    return wrench_from_force(m_kg * G * d / np.linalg.norm(d), r_mm)


def torque_wrench(m_kg, arm_mm, axis) -> np.ndarray:
    """Pure torque: two equal masses on strings over pulleys forming a couple of
    arm `arm_mm` about `axis` (net force zero). Returns [0,0,0, m*g*arm * axis]."""
    a = np.asarray(axis, float)
    return np.concatenate([np.zeros(3), m_kg * G * arm_mm / 1000.0 * a / np.linalg.norm(a)])


@dataclass
class FitReport:
    rms: np.ndarray  # per axis residual RMS
    max_abs: np.ndarray
    cond: float  # condition number of the load-case matrix
    n_cases: int
    residuals: np.ndarray = field(repr=False, default=None)

    def text(self) -> str:
        lines = [f"{self.n_cases} load cases, raw-matrix cond {self.cond:.1f}"]
        for a, u, r, m in zip(AXES, UNITS, self.rms, self.max_abs):
            lines.append(f"  {a}: rms {r:.4g} {u}, max {m:.4g} {u}")
        return "\n".join(lines)


@dataclass
class Calibration:
    C: np.ndarray
    tare: np.ndarray = field(default_factory=lambda: np.zeros(6))
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.C = np.asarray(self.C, float).reshape(6, 6)
        self.tare = np.asarray(self.tare, float).reshape(6)

    def wrench(self, raw) -> np.ndarray:
        """raw (...,6) counts -> wrench (...,6)."""
        return (np.asarray(raw, float) - self.tare) @ self.C.T

    @classmethod
    def from_geometry(cls, geom: Geometry, k=DEFAULT_K) -> "Calibration":
        """Starting guess: leg force = k*(raw - tare), wrench = T^T f."""
        k = np.broadcast_to(np.asarray(k, float), (6,))
        return cls(geom.W() * k[None, :], np.zeros(6), {"source": "geometry", "k": k.tolist()})

    def save(self, path=DEFAULT_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"C": self.C.tolist(), "tare": self.tare.tolist(),
             "meta": {**self.meta, "saved": time.strftime("%Y-%m-%dT%H:%M:%S")}}, indent=1))

    @classmethod
    def load(cls, path=DEFAULT_PATH) -> "Calibration":
        d = json.loads(Path(path).read_text())
        return cls(d["C"], d["tare"], d.get("meta", {}))

    @classmethod
    def load_or_default(cls, path=DEFAULT_PATH, geom: Geometry | None = None) -> "Calibration":
        if Path(path).exists():
            return cls.load(path)
        return cls.from_geometry(geom or Geometry.load())


def fit(raws, wrenches, tare) -> tuple[Calibration, FitReport]:
    """Least-squares C from load cases. raws (N,6) mean raw counts, wrenches (N,6)
    known applied wrench, tare (6,) raw counts at no load. Needs N >= 6 cases whose
    wrenches span all six axes (vertical masses alone give only Fz, Mx, My)."""
    X = np.asarray(raws, float) - np.asarray(tare, float)
    W = np.asarray(wrenches, float)
    if X.ndim != 2 or X.shape[1] != 6 or X.shape != W.shape or len(X) < 6:
        raise ValueError("need at least 6 load cases, raw and wrench both (N,6)")
    if np.linalg.matrix_rank(X, tol=1e-3 * np.linalg.norm(X, 2)) < 6:
        raise ValueError("load cases do not excite all six channels independently")
    Ct, *_ = np.linalg.lstsq(X, W, rcond=None)
    res = X @ Ct - W
    rep = FitReport(np.sqrt((res ** 2).mean(0)), np.abs(res).max(0),
                    float(np.linalg.cond(X)), len(X), res)
    meta = {"source": "fit", "n_cases": len(X), "rms": rep.rms.tolist()}
    return Calibration(Ct.T, tare, meta), rep
