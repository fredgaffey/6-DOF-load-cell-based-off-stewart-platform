"""Qt GUI: live wrench + raw plots, tare, CSV logging, guided calibration."""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from . import calib as C
from . import session as S
from .device import FakeDevice, open_device

COLORS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#008080"]
WINDOW_S = 10.0


def make_plot(title, unit, names, colors):
    pw = pg.PlotWidget(title=title)
    pw.addLegend(offset=(10, 5))
    pw.setLabel("left", unit)
    pw.setLabel("bottom", "s")
    pw.showGrid(x=True, y=True)
    return pw, [pw.plot(pen=pg.mkPen(c, width=1.5), name=n) for n, c in zip(names, colors)]


class CalibDialog(QtWidgets.QDialog):
    """GUI for a calibration Session (same file and steps as `ft6 calib ...`)."""
    COLS = ["mass kg", "x mm", "y mm", "z mm", "dx", "dy", "dz", "label", "captured"]

    def __init__(self, win: "MainWindow", session_path=S.DEFAULT_SESSION) -> None:
        super().__init__(win)
        self.win, self.result = win, None
        self.ses = S.Session.load(session_path)
        self.setWindowTitle("Calibration")
        self.resize(900, 600)
        lay = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Mode:"))
        self.mode = QtWidgets.QComboBox()
        for k, v in S.MODES.items():
            self.mode.addItem(v, k)
        self.mode.setCurrentIndex(list(S.MODES).index(self.ses.mode))
        self.mode.currentIndexChanged.connect(self.set_mode)
        top.addWidget(self.mode, 1)
        lay.addLayout(top)
        plan = QtWidgets.QHBoxLayout()
        plan.addWidget(QtWidgets.QLabel("Plan: mass kg"))
        self.p_mass = QtWidgets.QDoubleSpinBox(decimals=4, minimum=0.001, maximum=50, value=0.5)
        plan.addWidget(self.p_mass)
        plan.addWidget(QtWidgets.QLabel("radius mm"))
        self.p_rad = QtWidgets.QDoubleSpinBox(decimals=1, minimum=0, maximum=1000, value=40)
        plan.addWidget(self.p_rad)
        plan.addWidget(QtWidgets.QLabel("points"))
        self.p_n = QtWidgets.QSpinBox(minimum=3, maximum=24, value=6)
        plan.addWidget(self.p_n)
        b = QtWidgets.QPushButton("Add plan (centre + circle)")
        b.clicked.connect(self.add_plan)
        plan.addWidget(b)
        plan.addStretch(1)
        lay.addLayout(plan)
        self.prompt = QtWidgets.QLabel()
        self.prompt.setWordWrap(True)
        self.prompt.setStyleSheet("font: bold 16px; padding: 8px; background: #fff3c4; color: #222")
        lay.addWidget(self.prompt)
        self.table = QtWidgets.QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.itemChanged.connect(self.edited)
        self.table.itemSelectionChanged.connect(self.refresh_prompt)
        lay.addWidget(self.table)
        row = QtWidgets.QHBoxLayout()
        for text, fn in [("Tare (sensor empty)", self.tare), ("Add case", self.add_row), ("Remove", self.remove_row),
                         ("Capture (2 s)", self.capture), ("Clear captures", self.clear),
                         ("Fit", self.do_fit), ("Save && apply", self.save)]:
            b = QtWidgets.QPushButton(text.replace("&&", "&"))
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        self.out = QtWidgets.QPlainTextEdit(readOnly=True)
        self.out.setMaximumHeight(170)
        lay.addWidget(self.out)
        self.reload()

    # -- table <-> session
    def reload(self, select=None) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for c in self.ses.cases:
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = [c.mass_kg, *c.point_mm, *c.direction, c.label, f"ok ({c.n})" if c.captured else "-"]
            for k, v in enumerate(vals):
                it = QtWidgets.QTableWidgetItem(f"{v:g}" if isinstance(v, float) else str(v))
                if k == 8:
                    it.setFlags(it.flags() & ~QtCore.Qt.ItemIsEditable)
                self.table.setItem(r, k, it)
        self.table.blockSignals(False)
        nxt = self.ses.next_index() if select is None else select
        if nxt is not None and nxt < self.table.rowCount():
            self.table.selectRow(nxt)
        self.refresh_prompt()

    def refresh_prompt(self) -> None:
        r = self.table.currentRow()
        i = r if 0 <= r < len(self.ses.cases) and not self.ses.cases[r].captured else None
        txt = self.ses.prompt(i)
        if not np.any(self.win.calib.tare):
            txt = "Step 1: take everything off the sensor and press Tare. " + txt
        self.prompt.setText(txt)

    def edited(self, item) -> None:
        r, c = item.row(), item.column()
        case = self.ses.cases[r]
        try:
            if c == 0:
                case.mass_kg = float(item.text())
            elif c <= 3:
                case.point_mm[c - 1] = float(item.text())
            elif c <= 6:
                case.direction[c - 4] = float(item.text())
            elif c == 7:
                case.label = item.text()
        except ValueError:
            self.out.setPlainText("not a number")
            return
        case.raw, case.n = None, 0  # the load changed: recapture
        self.ses.save()
        self.reload(select=r)

    def set_mode(self) -> None:
        self.ses.mode = self.mode.currentData()
        self.ses.save()
        self.refresh_prompt()

    def add_plan(self) -> None:
        self.ses.plan_vertical(self.p_mass.value(), self.p_rad.value(), self.p_n.value())
        self.ses.save()
        self.reload()

    def add_row(self) -> None:
        self.ses.add(self.p_mass.value(), [0, 0, 0])
        self.ses.save()
        self.reload(select=len(self.ses.cases) - 1)

    def remove_row(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.ses.remove(r)
            self.ses.save()
            self.reload()

    def clear(self) -> None:
        self.ses.clear_captures()
        self.ses.save()
        self.reload()

    def tare(self) -> None:
        self.win.tare()
        self.refresh_prompt()

    # -- capture / fit
    def capture(self) -> None:
        r = self.table.currentRow()
        if r < 0:
            r = self.ses.next_index()
        if r is None:
            return
        self.table.item(r, 8).setText("capturing...")
        dev = self.win.dev
        if isinstance(dev, FakeDevice):
            dev.set_wrench(self.ses.cases[r].wrench())
        idx0 = dev.buffer.total

        def done():
            _, rows = dev.buffer.read_since(idx0)
            rows = rows[len(rows) // 5:]  # drop the first 20 % (settling)
            if len(rows):
                self.ses.set_raw(r, rows[:, 1:].mean(0), len(rows))
                self.ses.save()
                self.reload()
            else:
                self.table.item(r, 8).setText("no data")
        QtCore.QTimer.singleShot(self.capture_ms, done)

    capture_ms = 2000

    def do_fit(self) -> None:
        try:
            cal, rep = self.ses.fit(self.win.calib.tare)
        except ValueError as e:
            self.out.setPlainText(f"cannot fit: {e}")
            return
        self.result = cal
        self.out.setPlainText(rep.text())

    def save(self) -> None:
        if self.result is None:
            self.do_fit()
        if self.result is not None:
            self.win.calib = self.result
            self.win.calib.save(self.win.calib_path)
            self.out.appendPlainText(f"saved -> {self.win.calib_path}")


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, dev, calib_path=C.DEFAULT_PATH) -> None:
        super().__init__()
        self.dev, self.calib_path = dev, calib_path
        self.calib = C.Calibration.load_or_default(calib_path)
        self.log_file = None
        self.log_writer = None
        self.idx = dev.buffer.total
        self.setWindowTitle("ft6 - 6-axis force/torque")
        dev.start()

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        grid = QtWidgets.QGridLayout(central)
        bar = QtWidgets.QHBoxLayout()
        self.readouts = []
        for n, u, col in zip(C.AXES, C.UNITS, COLORS):
            lab = QtWidgets.QLabel(f"{n}: --")
            lab.setStyleSheet(f"font: bold 16px monospace; color: {col}")
            bar.addWidget(lab)
            self.readouts.append((lab, n, u))
        grid.addLayout(bar, 0, 0, 1, 2)
        ctl = QtWidgets.QHBoxLayout()
        for text, fn in [("Tare", self.tare), ("Start logging", self.toggle_log), ("Calibrate...", self.calibrate)]:
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(fn)
            ctl.addWidget(b)
            if text.startswith("Start"):
                self.log_btn = b
        self.status = QtWidgets.QLabel("")
        ctl.addWidget(self.status, 1)
        grid.addLayout(ctl, 1, 0, 1, 2)
        self.pf, self.cf = make_plot("Force", "N", C.AXES[:3], COLORS[:3])
        self.pm, self.cm = make_plot("Moment", "N*m", C.AXES[3:], COLORS[3:])
        self.pr, self.cr = make_plot("Raw counts", "counts", [f"leg{i+1}" for i in range(6)], COLORS)
        grid.addWidget(self.pf, 2, 0)
        grid.addWidget(self.pm, 2, 1)
        grid.addWidget(self.pr, 3, 0, 1, 2)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(50)

    def tare(self) -> None:
        if isinstance(self.dev, FakeDevice):  # simulated sensor: unload it, tare from no-load samples
            self.dev.set_wrench(np.zeros(6))
            self.calib.tare = self.dev.synth(np.zeros(6), 40).mean(0)
            self.calib.save(self.calib_path)
            self.status.setText("tared")
            return
        rows = self.dev.buffer.latest(40)
        if len(rows):
            self.calib.tare = rows[:, 1:].mean(0)
            self.calib.save(self.calib_path)
            self.status.setText("tared")

    def toggle_log(self) -> None:
        if self.log_file:
            self.log_file.close()
            self.log_file = self.log_writer = None
            self.log_btn.setText("Start logging")
            return
        Path("logs").mkdir(exist_ok=True)
        name = time.strftime("logs/ft6_%Y%m%d_%H%M%S.csv")
        self.log_file = open(name, "w", newline="")
        self.log_writer = csv.writer(self.log_file)
        self.log_writer.writerow(["t", *[f"raw{i}" for i in range(6)], *C.AXES])
        self.log_btn.setText("Stop logging")
        self.status.setText(f"logging to {name}")

    def calibrate(self) -> None:
        CalibDialog(self).exec()

    def tick(self) -> None:
        idx, new = self.dev.buffer.read_since(self.idx)
        self.idx = idx
        if self.log_writer is not None:
            for r in new:
                self.log_writer.writerow([f"{r[0]:.6f}", *[int(x) for x in r[1:]],
                                          *[f"{x:.6g}" for x in self.calib.wrench(r[1:])]])
        rows = self.dev.buffer.latest(4000)
        if not len(rows):
            if self.dev.error:
                self.status.setText(f"device error: {self.dev.error}")
            return
        rows = rows[rows[:, 0] > rows[-1, 0] - WINDOW_S]
        t = rows[:, 0] - rows[-1, 0]
        w = self.calib.wrench(rows[:, 1:])
        for i in range(3):
            self.cf[i].setData(t, w[:, i])
            self.cm[i].setData(t, w[:, 3 + i])
        for i in range(6):
            self.cr[i].setData(t, rows[:, 1 + i])
        for (lab, n, u), v in zip(self.readouts, w[-1]):
            lab.setText(f"{n}: {v:+8.3f} {u}")
        p = self.dev.parser
        if not self.status.text().startswith(("tared", "logging")):
            self.status.setText(f"frames {p.frames}  dropped {p.dropped}  crc errors {p.crc_errors}")

    def closeEvent(self, e) -> None:
        self.timer.stop()
        if self.log_file:
            self.toggle_log()
        self.dev.stop()
        super().closeEvent(e)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="ft6-gui")
    ap.add_argument("--port")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--calib", default=str(C.DEFAULT_PATH))
    a = ap.parse_args(argv)
    app = QtWidgets.QApplication(sys.argv[:1])
    win = MainWindow(open_device(a.port, a.fake), a.calib)
    win.resize(1100, 800)
    win.show()
    sys.exit(app.exec())
