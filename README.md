# ViViD Dryer — Klipper Integration

Full Klipper control for the **ViViD filament dryer** with two operating modes:
timed dry cycles and closed-loop humidity-hold.

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [Hardware Requirements](#hardware-requirements)
3. [Installation](#installation)
4. [Config Reference](#config-reference)
5. [GCode Command Reference](#gcode-command-reference)
6. [Humidity Sensor Notes](#humidity-sensor-notes)
7. [Mainsail Widget](#mainsail-widget)
8. [Troubleshooting](#troubleshooting)

---

## What It Does

### Mode 1 — Timed Dry Cycle

```
VIVID_DRY_START TEMP=55 HOURS=6
```

Heats the drying chamber to a fixed temperature for a fixed duration, then
turns the heater off automatically.  The remaining time is exposed via
`printer.vivid_dryer.remaining_seconds` so Moonraker / Mainsail can display
a live countdown.

### Mode 2 — Humidity-Hold

```
VIVID_DRY_START HUMIDITY=30 TEMP_MAX=55 TEMP_MIN=35
```

Reads a humidity sensor (see [Humidity Sensor Notes](#humidity-sensor-notes))
and modulates the heater temperature between `TEMP_MIN` and `TEMP_MAX` to
drive relative humidity down to the target percentage and hold it there
indefinitely (or until an optional `MAX_HOURS` timeout is hit).

**Control loop** (runs every `humidity_poll_interval` seconds):

| Condition | Action |
|---|---|
| humidity > target + deadband | raise temp (linear interpolation toward `TEMP_MAX`) |
| humidity < target − deadband | lower temp toward `TEMP_MIN` |
| within deadband | hold current temp |

There is no fixed end time — the dryer runs until you call `VIVID_DRY_STOP`
or `MAX_HOURS` elapses.

---

## Hardware Requirements

| Component | Notes |
|---|---|
| Heater element | Any heater pin supported by your MCU |
| Temperature sensor | NTC 3950, AHT2x, or any Klipper-supported type |
| Humidity sensor *(optional)* | SHT31 or similar I²C sensor — only needed for humidity-hold mode |

---

## Installation

### 1. Copy the Klipper extra

```bash
cp klippy/extras/vivid_dryer.py ~/klipper/klippy/extras/
```

### 2. Restart Klipper

```bash
sudo systemctl restart klipper
```

### 3. Add config includes to `printer.cfg`

```ini
[include vivid_dryer.cfg]

# Optional — Mainsail macro buttons
[include mainsail/vivid_dryer_panel.cfg]

# Optional — protect dryer from idle timeout
[include idle_timeout_guard.cfg]
```

Then in `[idle_timeout]` (if using the guard):

```ini
[idle_timeout]
gcode:
  _IDLE_TIMEOUT_GUARD
timeout: 600
```

### 4. Edit `vivid_dryer.cfg`

Set the correct `heater_pin` and `sensor_pin` for your hardware.  Optionally
uncomment and configure the `[temperature_sensor vivid_humidity]` block if you
have a humidity sensor wired.

### 5. Firmware restart

```
FIRMWARE_RESTART
```

---

## Config Reference

All options go inside the `[vivid_dryer]` section in `vivid_dryer.cfg`.

| Option | Type | Default | Description |
|---|---|---|---|
| `heater` | string | `Vivid_1_dryer` | Name of the `[heater_generic]` block |
| `humidity_sensor` | string | *(unset)* | Name of the `temperature_sensor` that exposes `humidity` — omit if no sensor |
| `default_temp` | float | `55.0` | Fallback heater temperature (°C) |
| `default_duration` | int | `14400` | Fallback duration (seconds) when no time args are given |
| `humidity_deadband` | float | `3.0` | ±RH% band around target where no temp change is made |
| `humidity_poll_interval` | int | `30` | Seconds between humidity reads / temp adjustments |

---

## GCode Command Reference

### `VIVID_DRY_START`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `TEMP` | float | `default_temp` | Heater temperature for timed mode (°C) |
| `HOURS` | float | 0 | Duration hours |
| `MINUTES` | float | 0 | Duration minutes |
| `SECONDS` | float | 0 | Duration seconds |
| `HUMIDITY` | float | *(unset)* | **Triggers humidity-hold mode.** Target % RH |
| `TEMP_MAX` | float | `default_temp` | Max heater temp in humidity-hold mode (°C) |
| `TEMP_MIN` | float | `35.0` | Min heater temp in humidity-hold mode (°C) |
| `MAX_HOURS` | float | 0 (= ∞) | Optional timeout for humidity-hold mode |

If `HUMIDITY` is supplied the dryer enters humidity-hold mode; otherwise it
uses timed mode.  If no duration is given for timed mode, `default_duration`
is used.

### `VIVID_DRY_STOP`

Stops any active cycle and turns the heater off.

```
VIVID_DRY_STOP
```

### `VIVID_DRY_STATUS`

Prints current state to the GCode console.

```
VIVID_DRY_STATUS
```

---

## Humidity Sensor Notes

The `humidity_sensor` config option must name a Klipper object whose
`get_status(eventtime)` method returns a dict containing a `humidity` key
(float, % RH).

**Example — SHT31 sensor** (requires a Klipper SHT31 extra such as
`temperature_sensor_sht31`):

```ini
[temperature_sensor vivid_humidity]
sensor_type: SHT31
i2c_address: 68
i2c_mcu: mcu
```

Then in `[vivid_dryer]`:

```ini
humidity_sensor: vivid_humidity
```

The `vivid_dryer` extra reads `humidity` from `get_status()` defensively —
if the sensor is unavailable, humidity-hold mode is disabled and timed mode
still works normally.

---

## Mainsail Widget

A standalone dark-theme HTML widget (`mainsail/vivid_dryer.html`) is included.
It auto-polls Moonraker every 5 seconds and shows:

- Mode badge (IDLE / TIMED / HUMIDITY)
- Current and target heater temperature
- Current and target humidity (when sensor is available)
- Live countdown timer (HH:MM:SS) with progress bar (timed mode)
- Humidity sparkline chart (last 30 readings, humidity-hold mode)
- Start / Stop controls for both modes

### Serving the widget

Copy `vivid_dryer.html` into a directory served by your Mainsail web server,
or serve it with a simple HTTP server on the Pi:

```bash
# Quick approach — serve from Moonraker's www directory
cp mainsail/vivid_dryer.html ~/mainsail/vivid_dryer.html
```

Then in Mainsail → **Settings → Interface → Custom Panels**, add an iframe
pointing to `http://<your-printer-ip>/vivid_dryer.html`.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| `Error: heater 'Vivid_1_dryer' not found` | Make sure `[heater_generic Vivid_1_dryer]` is in your config and `heater:` in `[vivid_dryer]` matches exactly |
| `humidity-hold mode is unavailable` | `humidity_sensor` is not set or the sensor object wasn't found — check Klipper logs |
| Heater turns on but idle timeout kills it | Make sure `_IDLE_TIMEOUT_GUARD` is wired into `[idle_timeout]` |
| Widget shows "OFFLINE" | Check that Moonraker is running and the widget URL matches your printer's IP/port |
| Humidity reads 0% always | Verify the sensor wiring and that `get_status()` returns a `humidity` key |
