# Stewart-platform-6-axis-load-cell-low-cost

 The goal of this project is to make a low cost cheap 6 axis load cell design for measuring small forces up to 3000N this will be done with a Stewart platform design and strain gauges

 BOM: https://docs.google.com/spreadsheets/d/1DH0zlGb7TFqwraOZyVDA1qlbqgXSxcA0r8sQnDxzwLU/edit?usp=sharing 

## **Goals**

| thing            |                |
| ---------------- | -------------- |
| linear load      | 30000 N (30kg) |
| linear precision | +- 1N          |
| Rotary load      | 20 Nm          |
| Rotary precision | +- 0.5nm       |
| Cost             | 200 aud        |
| assambly time    | 2.5 h          |



## Software (rewrite)

The original prototype is in `legacy/`. The rewrite is `firmware/` (PlatformIO, ESP32) and `ft6/` (Python).

### Wiring (ESP32 dev board, one HX711 per leg)

| leg | DOUT | SCK |
| --- | ---- | --- |
| 1 | 32 | 19 |
| 2 | 33 | 18 |
| 3 | 25 | 5 |
| 4 | 26 | 17 |
| 5 | 27 | 16 |
| 6 | 14 | 4 |

HX711 sample rate is hardware: RATE pin low = 10 Hz, high = 80 Hz.

### Build and flash

    pip install platformio      # or use a venv
    cd firmware && pio run -t upload

### Run

    python -m venv .venv && . .venv/bin/activate
    pip install -e .[test]
    ft6-gui                  # auto-detects CP210x/CH340; --port /dev/ttyUSB0 to override
    ft6-gui --fake           # synthetic device, no hardware
    ft6 stream | ft6 log FILE --seconds 30 | ft6 tare     (all accept --port / --fake / --calib)
    pytest

On Linux you may need `sudo usermod -aG dialout $USER` (or `chmod 666 /dev/ttyUSB0`).

Leg geometry is `ft6/default_geometry.json` (base and top joint coordinates in mm, sensor frame). Replace it with the CAD values. Calibration is stored in `~/.config/ft6/calibration.json`; until you calibrate, C is a geometry-based guess (Tᵀ times a rough per-cell sensitivity), so readings are only approximate.

### Calibration procedure

The GUI (Calibrate...) and the CLI (`ft6 calib ...`) drive the same session file (`~/.config/ft6/session.json`), with the same steps and prompts; `ft6 --help` maps each GUI button to its command.

**Weights only (mode `gains`, default)** - enough for thrust. Fits one gain per leg; the leg geometry does the rest. Fz, Mx, My are checked by the cases; Fx, Fy, Mz (torque) rely on the geometry file being right.

    ft6 calib new --mode gains
    ft6 calib plan --mass 0.5 --radius 40     # centre + 6 points on a 40 mm circle (+x first)
    ft6 calib wizard                          # tare, then prompts "put 500.0 g at x=... y=..." per case, fits, asks to save

or step by step: `ft6 tare`, `ft6 calib capture` (repeat; prints the next prompt), `ft6 calib fit --save`. Known masses: water (1 mL = 1 g), a kitchen scale, or AU coins ($1 9.00 g, $2 6.60 g, 50c 15.55 g, 20c 11.30 g). Use 2-3 different masses to check linearity. A leg misfit above 5 % in the report means that leg or the geometry needs checking.

**Full 6x6 (mode `full`)** - adds sideways string pulls over a pulley (`ft6 calib add --mass M --at X Y Z --dir DX DY DZ`), 12-20 cases covering all six axes.

### Data format

Serial, 921600 baud, little-endian 34-byte frames: `A5 5A | u16 seq | u32 micros | 6 x i32 raw | u16 CRC-16/CCITT-FALSE` (CRC over the 30 bytes after the sync). CSV logs (`logs/ft6_YYYYmmdd_HHMMSS.csv`): `t` (s, device clock), `raw0..raw5` (counts), `Fx Fy Fz` (N), `Mx My Mz` (N*m). Wrench is the load applied to the top plate, moments about the sensor origin.
