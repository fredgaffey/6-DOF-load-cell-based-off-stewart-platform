import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def pump(app, s):
    t0 = time.time()
    while time.time() - t0 < s:
        app.processEvents()
        time.sleep(0.01)


def test_gui_smoke(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from PySide6 import QtWidgets
    from ft6.device import open_device
    from ft6.gui import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow(open_device(fake=True), tmp_path / "cal.json")
    win.show()
    pump(app, 1.0)
    win.tare()
    win.toggle_log()
    pump(app, 0.3)
    win.toggle_log()
    assert len(list((tmp_path / "logs").glob("*.csv"))) == 1
    assert "Fz" in win.readouts[2][0].text()
    win.close()


def test_gui_weights_only_calibration(tmp_path, monkeypatch):
    """Same steps as `ft6 calib` in the GUI: tare, plan, capture each prompted case, fit, save."""
    monkeypatch.chdir(tmp_path)
    from PySide6 import QtWidgets
    from ft6.calib import Calibration
    from ft6.device import open_device
    from ft6.gui import CalibDialog, MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow(open_device(fake=True), tmp_path / "cal.json")
    dlg = CalibDialog(win, tmp_path / "ses.json")
    dlg.capture_ms = 400
    assert "Tare" in dlg.prompt.text()
    dlg.tare()
    assert "Tare" not in dlg.prompt.text()
    dlg.p_mass.setValue(0.5)
    dlg.p_rad.setValue(40)
    dlg.add_plan()
    assert dlg.prompt.text().startswith("Case 1/7: put 500.0 g at x=+0.0")
    for i in range(7):
        assert f"Case {i + 1}/7" in dlg.prompt.text()
        dlg.capture()
        pump(app, 0.6)
    assert "All 7 cases captured" in dlg.prompt.text()
    dlg.do_fit()
    assert "leg gains" in dlg.out.toPlainText()
    dlg.save()
    assert Calibration.load(tmp_path / "cal.json").meta["source"] == "fit_gains"
    win.close()
