import numpy as np
from ft6.calib import Calibration, fit, torque_wrench, wrench_from_mass
from ft6.device import FakeDevice
from ft6.geometry import Geometry


def cases(rng, n=16):
    out = []
    for _ in range(n):
        m = rng.uniform(0.5, 4)
        r = rng.uniform(-60, 60, 3)
        d = rng.standard_normal(3)  # arbitrary string direction (pulleys)
        out.append(wrench_from_mass(m, r, d))
    return np.array(out)


def test_fit_recovers_true_C():
    dev = FakeDevice(noise=20, seed=3)
    rng = np.random.default_rng(1)
    W = cases(rng)
    raws = np.array([dev.synth(w, 100).mean(0) for w in W])
    cal, rep = fit(raws, W, dev.tare_true)
    err = np.linalg.norm(cal.C - dev.C_true) / np.linalg.norm(dev.C_true)
    assert err < 1e-2, err
    assert rep.rms.max() < 0.05  # N / N*m


def test_geometry_guess_is_close_but_fit_is_better():
    dev = FakeDevice(seed=3)
    guess = Calibration.from_geometry(Geometry.load())
    e0 = np.linalg.norm(guess.C - dev.C_true) / np.linalg.norm(dev.C_true)
    W = cases(np.random.default_rng(2))
    cal, _ = fit([dev.synth(w, 100).mean(0) for w in W], W, dev.tare_true)
    assert np.linalg.norm(cal.C - dev.C_true) / np.linalg.norm(dev.C_true) < e0 / 5


def test_threaded_capture_and_save_load(tmp_path):
    dev = FakeDevice(rate=1000, noise=10, seed=5)
    w = wrench_from_mass(2.0, [10, -5, 0])
    dev.set_wrench(w)
    with dev:
        raw = dev.capture_mean(0.2)
    assert dev.parser.dropped == 0 and dev.error is None
    cal = Calibration(dev.C_true, dev.tare_true)
    assert np.allclose(cal.wrench(raw), w, atol=0.05)
    cal.save(tmp_path / "c.json")
    assert np.allclose(Calibration.load(tmp_path / "c.json").C, cal.C)


def test_too_few_or_degenerate_cases_rejected():
    import pytest
    with pytest.raises(ValueError):
        fit(np.ones((5, 6)), np.ones((5, 6)), np.zeros(6))
    vert = np.array([wrench_from_mass(1 + i, [i, 2 * i, 0]) for i in range(8)])
    dev = FakeDevice()
    raws = np.array([dev.synth(w, 10).mean(0) for w in vert])
    with pytest.raises(ValueError):
        fit(raws, vert, dev.tare_true)


def test_torque_wrench():
    w = torque_wrench(1.0, 100, [0, 0, 1])
    assert np.allclose(w[:3], 0) and abs(w[5] - 9.80665 * 0.1) < 1e-9
