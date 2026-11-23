"""Stewart-platform leg geometry.

Config JSON: {"base_mm": [[x,y,z] x6], "top_mm": [[x,y,z] x6]} in the sensor
frame (mm). Leg i runs from base point b_i to top point p_i; n_i is the unit
vector b_i -> p_i. A leg force f_i (N, positive = compression, pushing the top
plate along +n_i) contributes wrench [n_i, p_i x n_i] f_i (force N, moment N*m),
so W = T^T f with T rows [n_i, p_i(m) x n_i].
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

DEFAULT_PATH = Path(__file__).with_name("default_geometry.json")


@dataclass
class Geometry:
    base_mm: np.ndarray  # (6,3)
    top_mm: np.ndarray  # (6,3)

    def __post_init__(self) -> None:
        self.base_mm = np.asarray(self.base_mm, float).reshape(6, 3)
        self.top_mm = np.asarray(self.top_mm, float).reshape(6, 3)

    @classmethod
    def load(cls, path=None) -> "Geometry":
        d = json.loads(Path(path or DEFAULT_PATH).read_text())
        return cls(d["base_mm"], d["top_mm"])

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(
            {"base_mm": self.base_mm.tolist(), "top_mm": self.top_mm.tolist()}, indent=1))

    def leg_vectors(self) -> np.ndarray:
        d = self.top_mm - self.base_mm
        return d / np.linalg.norm(d, axis=1, keepdims=True)

    def T(self) -> np.ndarray:
        n = self.leg_vectors()
        return np.hstack([n, np.cross(self.top_mm / 1000.0, n)])

    def W(self) -> np.ndarray:
        """Map from leg forces (N) to wrench: wrench = W @ f."""
        return self.T().T

    def condition(self) -> float:
        return condition_number(self.T())


def condition_number(M: np.ndarray) -> float:
    return float(np.linalg.cond(M))


def symmetric_hexapod(r_base=60.0, r_top=30.0, height=50.0, a_base_deg=10.0, a_top_deg=10.0) -> Geometry:
    """Base pairs at 120k +- a_base; top pairs at 120k+60 +- a_top. All legs equivalent
    by symmetry: leg (k, s) joins base angle 120k+s*a_base to top angle 120k+s*(60-a_top)."""
    b, p = [], []
    for k in range(3):
        for s in (-1, 1):
            tb = np.deg2rad(120 * k + s * a_base_deg)
            tt = np.deg2rad(120 * k + s * (60 - a_top_deg))
            b.append([r_base * np.cos(tb), r_base * np.sin(tb), 0.0])
            p.append([r_top * np.cos(tt), r_top * np.sin(tt), height])
    return Geometry(b, p)
