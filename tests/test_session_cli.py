"""Weights-only calibration through the CLI (same session file the GUI uses)."""
import json

import numpy as np
import pytest

from ft6 import cli
from ft6.calib import Calibration, fit_gains, wrench_from_mass
from ft6.device import FakeDevice
from ft6.geometry import Geometry
from ft6.session import Session


def run(capsys, tmp_path, *args):
    rc = cli.main(["--fake", "--calib", str(tmp_path / "cal.json"), "--session", str(tmp_path / "ses.json"), *args])
    return rc, capsys.readouterr().out


def test_fit_gains_exact_without_crosstalk():
    dev = FakeDevice(noise=5, seed=4, crosstalk=0)
    geom = Geometry.load()
    W = [wrench_from_mass(0.5, [r * np.cos(a), r * np.sin(a), 0])
         for r, a in [(0, 0)] + [(40, k * np.pi / 3) for k in range(6)]]
    raws = [dev.synth(w, 200).mean(0) for w in W]
    cal, rep = fit_gains(raws, W, dev.tare_true, geom)
    assert np.allclose(cal.C, dev.C_true, rtol=0.02, atol=1e-3 * np.abs(dev.C_true).max())
    assert rep.leg_err.max() < 0.01
    # thrust from a load never used in the fit
    w = wrench_from_mass(1.3, [15, -25, 0])
    assert abs(cal.wrench(dev.synth(w, 200).mean(0))[2] - w[2]) < 0.01 * abs(w[2])


def test_fit_gains_rejects_unloaded_leg():
    geom = Geometry.load()
    with pytest.raises(ValueError):
        fit_gains(np.ones((3, 6)), np.zeros((3, 6)), np.zeros(6), geom)


def test_cli_weights_only_end_to_end(capsys, tmp_path):
    assert run(capsys, tmp_path, "calib", "new", "--mode", "gains")[0] == 0
    rc, out = run(capsys, tmp_path, "calib", "plan", "--mass", "0.5", "--radius", "40")
    assert rc == 0 and "Case 1/7: put 500.0 g at x=+0.0 y=+0.0" in out
    rc, out = run(capsys, tmp_path, "calib", "fit")
    assert rc == 1 and "needs at least 3" in out
    rc, out = run(capsys, tmp_path, "tare", "--seconds", "0.3")
    assert rc == 0
    for i in range(7):
        rc, out = run(capsys, tmp_path, "calib", "capture", "--seconds", "0.5")
        assert rc == 0 and f"captured case {i + 1}" in out
    rc, out = run(capsys, tmp_path, "calib", "list")
    assert "All 7 cases captured" in out
    rc, out = run(capsys, tmp_path, "calib", "fit", "--save")
    assert rc == 0 and "leg gains" in out and "saved" in out
    cal = Calibration.load(tmp_path / "cal.json")
    assert cal.meta["source"] == "fit_gains"
    # the fake sensor has 2 % crosstalk the gains model can't represent: thrust within 3 %
    dev = FakeDevice()  # same seed as the CLI's fake -> same sensor
    w = wrench_from_mass(1.0, [10, 20, 0])
    assert abs(cal.wrench(dev.synth(w, 400).mean(0))[2] - w[2]) < 0.03 * abs(w[2])


def test_cli_wizard(capsys, tmp_path, monkeypatch):
    run(capsys, tmp_path, "calib", "new")
    run(capsys, tmp_path, "calib", "plan", "--mass", "1", "--radius", "30", "--n", "3")
    answers = iter(["", "", "", "", "", "y"])  # tare, 4 captures, save
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    rc, out = run(capsys, tmp_path, "calib", "wizard", "--seconds", "0.3")
    assert rc == 0 and "Step 1" not in out and "saved" in out
    assert len(json.loads((tmp_path / "ses.json").read_text())["cases"]) == 4


def test_cli_add_remove_stream(capsys, tmp_path):
    run(capsys, tmp_path, "calib", "new", "--mode", "full")
    rc, out = run(capsys, tmp_path, "calib", "add", "--mass", "0.2", "--at", "10", "0", "--dir", "1", "0", "0")
    assert "string pulling along (1, 0, 0)" in out
    rc, out = run(capsys, tmp_path, "calib", "remove", "1")
    assert "(no cases)" in out
    rc, out = run(capsys, tmp_path, "stream", "--raw", "--count", "2")
    assert rc == 0 and out.count("leg6=") == 2


def test_session_round_trip(tmp_path):
    s = Session("gains", path=tmp_path / "s.json")
    s.plan_vertical(0.25, 50, 4)
    s.set_raw(0, np.arange(6), 10)
    s.save()
    t = Session.load(tmp_path / "s.json")
    assert len(t.cases) == 5 and t.cases[0].captured and t.next_index() == 1
