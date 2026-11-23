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
from .device import open_device

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
    """One row per load case: mass, application point, direction. Capture each,
    then Fit. Needs a tare taken at no load first."""
    COLS = ["mass kg", "x mm", "y mm", "z mm", "dx", "dy", "dz", "captured"]

    def __init__(self, win: "MainWindow") -> None:
        super().__init__(win)
        self.win, self.raws = win, {}
        self.setWindowTitle("Calibration")
        self.resize(760, 480)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel(
            "1) Unload the sensor and press Tare in the main window.\n"
            "2) Per row: hang the mass at the point (sensor frame, mm) pulling along the\n"
            "   direction (default gravity, 0 0 -1; use a pulley for horizontal / torque cases),\n"
            "   let it settle, select the row, Capture. Use 12-20 varied cases covering all 6 axes."))
        self.table = QtWidgets.QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        lay.addWidget(self.table)
        row = QtWidgets.QHBoxLayout()
        for text, fn in [("Add case", self.add_row), ("Remove", self.remove_row),
                         ("Capture selected (2 s)", self.capture), ("Fit", self.do_fit),
                         ("Save && apply", self.save)]:
            b = QtWidgets.QPushButton(text.replace("&&", "&"))
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        self.out = QtWidgets.QPlainTextEdit(readOnly=True)
        self.out.setMaximumHeight(150)
        lay.addWidget(self.out)
        self.result = None
        self.add_row()

    def add_row(self, vals=None) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, v in enumerate(vals or ["1.0", "0", "0", "0", "0", "0", "-1"]):
            self.table.setItem(r, c, QtWidgets.QTableWidgetItem(str(v)))
        item = QtWidgets.QTableWidgetItem("-")
        item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
        self.table.setItem(r, 7, item)

    def remove_row(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self.raws = {k - (k > r): v for k, v in self.raws.items() if k != r}

    def wrench_of(self, r: int) -> np.ndarray:
        v = [float(self.table.item(r, c).text()) for c in range(7)]
        return C.wrench_from_mass(v[0], v[1:4], v[4:7])

    def capture(self) -> None:
        r = self.table.currentRow()
        if r < 0:
            return
        try:
            self.wrench_of(r)
        except ValueError:
            self.out.setPlainText("row has a non-numeric value")
            return
        self.table.item(r, 7).setText("capturing...")
        idx = self.win.dev.buffer.total

        def done():
            _, rows = self.win.dev.buffer.read_since(idx)
            if len(rows):
                self.raws[r] = rows[:, 1:].mean(0)
                self.table.item(r, 7).setText(f"ok ({len(rows)} samples)")
            else:
                self.table.item(r, 7).setText("no data")
        QtCore.QTimer.singleShot(2000, done)

    def do_fit(self) -> None:
        rows = sorted(self.raws)
        try:
            cal, rep = C.fit([self.raws[r] for r in rows], [self.wrench_of(r) for r in rows],
                             self.win.calib.tare)
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
