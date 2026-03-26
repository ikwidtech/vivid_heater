# ViViD Dryer — Klipper Integration

Full Klipper control for the **ViViD filament dryer** with two operating modes:
timed dry cycles and closed-loop humidity-hold.  Supports multiple dryer units
on a single printer (Vivid_1, Vivid_2, …).

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [Hardware Requirements](#hardware-requirements)
3. [Quick Install](#quick-install)
4. [Manual Install](#manual-install)
5. [Config Reference](#config-reference)
6. [GCode Command Reference](#gcode-command-reference)
7. [Multiple Units](#multiple-units)
8. [Mainsail Widget](#mainsail-widget)
9. [Idle-Timeout Guard](#idle-timeout-guard)
10. [Troubleshooting](#troubleshooting)

---

## What It Does

### Mode 1 — Timed Dry Cycle

```
VIVID_DRY_START_VIVID_1 TEMP=55 HOURS=6
```

Heats the drying chamber to a fixed temperature for a fixed duration, then
turns the heater off automatically.  The remaining time is exposed via
`printer["vivid_dryer Vivid_1"].remaining_seconds` so Moonraker / Mainsail
can display a live countdown.

### Mode 2 — Humidity-Hold

```
VIVID_DRY_START_VIVID_1 HUMIDITY=30 TEMP_MAX=55 TEMP_MIN=35
```

Reads humidity from one or more AHT3X sensors and modulates the heater
temperature between `TEMP_MIN` and `TEMP_MAX` to drive relative humidity
down to the target percentage and hold it there indefinitely (or until an
optional `MAX_HOURS` timeout is reached).

**Control loop** (runs every `humidity_poll_interval` seconds):

| Condition | Action |
|---|---|
| humidity > target + deadband | raise heater temp (linear interp toward `TEMP_MAX`) |
| humidity < target − deadband | lower heater temp toward `TEMP_MIN` |
| within deadband | hold current heater temp |

---

## Hardware Requirements

| Component | Notes |
|---|---|
| Heater element | Any `heater_generic` pin supported by your MCU |
| AHT3X sensor(s) | Register in Klipper as `[aht10 <name>]` — **not** `temperature_sensor` |
| MCU with I²C | Required for AHT3X sensors |

> **Important:** AHT3X sensors use the `aht10` Klipper module.  The object
> name in `printer.cfg` must be `[aht10 <name>]`.  The vivid_dryer extra
> looks them up as `printer.lookup_object("aht10 <name>")` and reads
> `get_status()` → `{'temperature': float, 'humidity': int}`.
>
> Heater `max_temp` is capped at **75 °C** by the vivid_dryer extra.

---

## Quick Install

```bash
curl -fsSL https://raw.githubusercontent.com/ikwidtech/vivid_heater/main/install.sh | bash
```

The installer auto-detects your Klipper and Mainsail directories and prints
the exact `[include …]` lines to add to `printer.cfg`.

---

## Manual Install

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

Then (if using the idle-timeout guard):

```ini
[idle_timeout]
gcode:
  _VIVID_IDLE_TIMEOUT_CHECK
timeout: 600
```

### 4. Verify `printer.cfg` hardware blocks

The following blocks must exist in `printer.cfg` (or another included file).
**Do not** add them to `vivid_dryer.cfg`.

```ini
[heater_generic Vivid_1_dryer]
heater_pin: PA1         # adjust to your board
sensor_type: temperature_combined
...

[aht10 Vivid_1_dryer_left]
i2c_mcu: mcu
i2c_bus: i2c1

[aht10 Vivid_1_dryer_right]
i2c_mcu: mcu
i2c_bus: i2c2
```

### 5. Firmware restart

```
FIRMWARE_RESTART
```

---

## Config Reference

All options go inside the `[vivid_dryer Vivid_1]` section in `vivid_dryer.cfg`.

| Option | Type | Default | Description |
|---|---|---|---|
| `heater` | string | `Vivid_1_dryer` | Name of the `[heater_generic]` block |
| `humidity_sensor` | string | *(unset)* | Comma-separated list of `aht10 <name>` objects |
| `default_temp` | float | `55.0` | Fallback heater temperature (°C, max 75) |
| `default_duration` | int | `14400` | Fallback duration (seconds) when no time args are given |
| `humidity_deadband` | float | `3.0` | ±%RH band around target where no temp change is made |
| `humidity_poll_interval` | float | `30.0` | Seconds between humidity reads / temp adjustments |

---

## GCode Command Reference

Commands are suffixed with the uppercased instance name.
For `[vivid_dryer Vivid_1]` the suffix is `_VIVID_1`.
For an unnamed `[vivid_dryer]` there is no suffix (backwards-compatible).

### `VIVID_DRY_START_VIVID_1`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `TEMP` | float | `default_temp` | Heater temperature for timed mode (°C, max 75) |
| `HOURS` | float | 0 | Duration hours |
| `MINUTES` | float | 0 | Duration minutes |
| `SECONDS` | float | 0 | Duration seconds |
| `HUMIDITY` | float | *(unset)* | **Triggers humidity-hold mode.** Target %RH |
| `TEMP_MAX` | float | `default_temp` | Max heater temp in humidity-hold mode (°C, max 75) |
| `TEMP_MIN` | float | `35.0` | Min heater temp in humidity-hold mode (°C) |
| `MAX_HOURS` | float | 0 (= ∞) | Optional timeout for humidity-hold mode |

If `HUMIDITY` is supplied the dryer enters humidity-hold mode; otherwise
it uses timed mode.  If no duration is given for timed mode,
`default_duration` is used.

### `VIVID_DRY_STOP_VIVID_1`

Stops any active cycle and turns the heater off.

### `VIVID_DRY_STATUS_VIVID_1`

Prints current state to the GCode console.

---

## Multiple Units

Add a second `[vivid_dryer Vivid_2]` block to `vivid_dryer.cfg` (see the
commented example in that file).  Each instance gets its own set of GCode
commands with the appropriate suffix (`_VIVID_2`).

The `_VIVID_IDLE_TIMEOUT_CHECK` macro and the Mainsail widget both handle
multiple instances automatically — no additional configuration is required.

---

## Mainsail Widget

A standalone dark-theme HTML widget (`mainsail/vivid_dryer.html`) is included.
It auto-polls Moonraker every 5 seconds and shows, **per instance**:

- Mode badge (IDLE / TIMED / HUMIDITY)
- Actual heater temperature and setpoint
- PTC ambient temperature (from AHT3X sensors)
- Left, right, and averaged humidity readings
- Live countdown timer (HH:MM:SS) with progress bar (timed mode)
- Humidity sparkline chart — last 30 readings with target-RH reference line
- Start / Stop controls for both modes

### Serving the widget

```bash
# Copy to Mainsail www directory (installed automatically by install.sh)
cp mainsail/vivid_dryer.html ~/mainsail/vivid_dryer.html
```

Then in Mainsail → **Settings → Interface → Custom Panels**, add an iframe
pointing to `http://<your-printer-ip>/vivid_dryer.html`.

---

## Idle-Timeout Guard

`idle_timeout_guard.cfg` provides `_VIVID_IDLE_TIMEOUT_CHECK`, a macro that
replaces Klipper's default idle-timeout shutdown and keeps heaters on while
**any** vivid_dryer instance is active.

```ini
[idle_timeout]
gcode:
  _VIVID_IDLE_TIMEOUT_CHECK
timeout: 600   # seconds of inactivity before the check fires
```

The macro iterates over all `printer` keys that start with `vivid_dryer` and
checks the `.active` field, so it works automatically for any number of dryer
instances without modification.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| `Error: heater 'Vivid_1_dryer' not found` | `[heater_generic Vivid_1_dryer]` must be in `printer.cfg`; the `heater:` value in `vivid_dryer.cfg` must match exactly |
| `humidity sensor 'aht10 …' not found` | AHT3X sensors must be defined as `[aht10 <name>]` in `printer.cfg`, **not** as `[temperature_sensor …]` |
| Humidity reads 0 % always | Check I²C wiring; run `QUERY_ADC` or inspect Klipper logs for AHT3X errors |
| `no humidity sensors configured` | `humidity_sensor:` is missing or blank in `[vivid_dryer Vivid_1]` |
| Heater turns on but idle timeout kills it | Wire `_VIVID_IDLE_TIMEOUT_CHECK` into `[idle_timeout]` (see [Idle-Timeout Guard](#idle-timeout-guard)) |
| Widget shows "OFFLINE" | Check that Moonraker is running and that the widget URL matches your printer IP |
| GCode command not found | Confirm the correct suffix: `VIVID_DRY_START_VIVID_1` for `[vivid_dryer Vivid_1]` |
| Heater exceeds 75 °C | The extra enforces a 75 °C cap on all setpoints; check your heater `max_temp` in `printer.cfg` |
