# Honeywell-HTRAM-Control-BLE-
Simple desktop GUI to toggle the **alarm buzzer (sound)** and **screen auto-off timer** on Honeywell HTRAM-V1 / HTRAM-V1-W air quality monitors via Bluetooth (BLE) without the official mobile app.

## Prerequisites
1. Double-press the top button on your HTRAM monitor to enable Bluetooth pairing mode.
2. Pair the `HTRAM-*` device in your OS Bluetooth settings using the 6-digit PIN shown on the monitor's screen.

## Run from Source
```bash
pip install -r requirements.txt
python htram_control.py
