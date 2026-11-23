import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_gui_smoke(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from PySide6 import QtWidgets
    from ft6.device import open_device
    from ft6.gui import CalibDialog, MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow(open_device(fake=True), tmp_path / "cal.json")
    win.show()
    t0 = time.time()
    while time.time() - t0 < 1.0:
        app.processEvents()
        time.sleep(0.01)
    win.tare()
    win.toggle_log()
    t0 = time.time()
    while time.time() - t0 < 0.3:
        app.processEvents()
    win.toggle_log()
    assert len(list((tmp_path / "logs").glob("*.csv"))) == 1
    assert "Fz" in win.readouts[2][0].text()
    CalibDialog(win)
    win.close()
