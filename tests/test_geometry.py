import numpy as np
from ft6.geometry import Geometry, symmetric_hexapod


def test_vertical_load_at_centre():
    g = symmetric_hexapod()
    n = g.leg_vectors()
    assert np.allclose(np.linalg.norm(n, axis=1), 1)
    F = 10.0
    f = np.full(6, F / (6 * n[0, 2]))  # equal leg forces
    w = g.W() @ f
    assert np.allclose(w, [0, 0, F, 0, 0, 0], atol=1e-9)


def test_inverse_roundtrip_and_units():
    g = symmetric_hexapod()
    w = np.array([1.0, -2.0, 30.0, 0.5, -0.25, 0.1])
    f = np.linalg.solve(g.W(), w)
    assert np.allclose(g.W() @ f, w)
    # moment is N*m: a pure Fz leg-set at radius 30 mm gives moments << force
    assert np.isfinite(g.condition()) and g.condition() < 1e3


def test_default_config_loads():
    g = Geometry.load()
    assert g.W().shape == (6, 6)
