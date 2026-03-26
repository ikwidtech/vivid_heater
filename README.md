# ViViD Dryer — Klipper Integration

Full Klipper control for the **ViViD filament dryer** with two operating
modes, multi-instance support, AHT3X humidity sensing, and a Mainsail widget.

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [Hardware Requirements](#hardware-requirements)
3. [Quick Install](#quick-install)
4. [Manual Install](#manual-install)
5. [Config Reference](#config-reference)
6. [GCode Command Reference](#gcode-command-reference)
7. [Multiple ViViD Units](#multiple-vivid-units)
8. [Mainsail Widget Setup](#mainsail-widget-setup)
9. [idle_timeout Integration](#idle_timeout-integration)
10. [Troubleshooting](#troubleshooting)

---

## What It Does

### Mode 1 — Timed Dry Cycle

```
VIVID_DRY_START_VIVID_1 TEMP=55 HOURS=6
```

Heats the drying chamber to a fixed temperature for a fixed duration, then
turns the heater off automatically. The remaining time is exposed via
Moonraker so the Mainsail widget can display a live countdown and progress bar.

### Mode 2 — Humidity-Hold

```
VIVID_DRY_START_VIVID_1 HUMIDITY=30 TEMP_MAX=55 TEMP_MIN=35
```

Reads humidity from both AHT3X sensors (left and right), averages them, and
uses proportional control to modulate the heater temperature between TEMP_MIN
and TEMP_MAX to drive relative humidity to the target and hold it there.

**Proportional control formula:**
```
new_temp = TEMP_MIN + (TEMP_MAX - TEMP_MIN)
           * (current_humidity - target_humidity) / (target_humidity * 0.5)
```
Clamped to [TEMP_MIN, TEMP_MAX]. A deadband prevents constant setpoint
changes when the humidity is already near target.

| Condition | Action |
|---|---|
| Humidity within ±deadband of target | Hold current temp (no change) |
| Humidity above target + deadband | Raise temp toward TEMP_MAX |
| Humidity below target - deadband | Lower temp toward TEMP_MIN |

The dryer runs until you call `VIVID_DRY_STOP_VIVID_1` or until `MAX_HOURS`
elapses (if set).

---

## Hardware Requirements

| Component | Notes |
|---|---|
| ViViD filament dryer unit | With integrated heater element and MCU |
| AHT3X humidity/temp sensors (×2) | Left and right sensors on software I2C |
| Generic 3950 PTC thermistor | Ambient temperature sensor |
| Klipper-supported MCU | The unit connects as a secondary MCU (`Vivid_1`) |

Your `printer.cfg` must already contain these blocks per unit (they are **not**
included in `vivid_dryer.cfg`):

```ini
[temperature_sensor Vivid_1_PTC_temp]
[temperature_sensor Vivid_1_dryer_left]   # AHT3X
[temperature_sensor Vivid_1_dryer_right]  # AHT3X
[heater_generic Vivid_1_dryer]
[verify_heater Vivid_1_dryer]
[heater_fan Vivid_1_dryer_fan]
```

---

## Quick Install

```bash
curl -fsSL https://raw.githubusercontent.com/ikwidtech/vivid_heater/main/install.sh | bash
```

Or with options:

```bash
bash install.sh --instance Vivid_1 \
                --klipper-dir ~/klipper \
                --config-dir ~/printer_data/config \
                --mainsail-dir ~/mainsail
```

The installer will:
1. Copy `vivid_dryer.py` into `~/klipper/klippy/extras/`
2. Copy `vivid_dryer.cfg` into your config directory
3. Copy `vivid_idle_timeout_guard.cfg` into your config directory
4. Copy `vivid_dryer_panel.cfg` into your config directory
5. Copy `vivid_dryer.html` into your Mainsail directory (if found)
6. Print next-step instructions
7. Optionally restart Klipper

---

## Manual Install

### 1. Copy the Klipper extra

```bash
cp klippy/extras/vivid_dryer.py ~/klipper/klippy/extras/
```

### 2. Copy config files

```bash
cp config/vivid_dryer.cfg ~/printer_data/config/
cp config/idle_timeout_guard.cfg ~/printer_data/config/vivid_idle_timeout_guard.cfg
cp mainsail/vivid_dryer_panel.cfg ~/printer_data/config/
```

### 3. Copy the Mainsail widget (optional)

```bash
cp mainsail/vivid_dryer.html ~/mainsail/
```

### 4. Add includes to `printer.cfg`

```ini
[include vivid_dryer.cfg]

# Optional — Mainsail macro panel buttons
[include vivid_dryer_panel.cfg]

# Optional — protect dryer from idle timeout
[include vivid_idle_timeout_guard.cfg]
```

### 5. Restart Klipper

```bash
sudo systemctl restart klipper
```

---

## Config Reference

All options go in the `[vivid_dryer Vivid_1]` section in `vivid_dryer.cfg`.

| Option | Type | Default | Description |
|---|---|---|---|
| `heater` | string | `Vivid_1_dryer` | Name of the `[heater_generic]` block to control |
| `humidity_sensor` | string | *(unset)* | Comma-separated list of AHT3X sensor object names (e.g. `aht10 Vivid_1_dryer_left, aht10 Vivid_1_dryer_right`) |
| `default_temp` | float | `55.0` | Default heater temperature in °C |
| `default_duration` | int | `14400` | Default timed cycle duration in seconds (4 hours) |
| `humidity_deadband` | float | `3.0` | ±RH% band around target where no setpoint change is made |
| `humidity_poll_interval` | int | `30` | Seconds between humidity reads and setpoint adjustments |

**Important:** The `humidity_sensor` names must match the Klipper internal
object key for AHT3X sensors, which is `aht10 <section_name>`. For example,
`[temperature_sensor Vivid_1_dryer_left]` is looked up as
`aht10 Vivid_1_dryer_left`.

---

## GCode Command Reference

GCode commands are namespaced per instance. Replace `VIVID_1` with your
instance name in uppercase.

### `VIVID_DRY_START_VIVID_1`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `TEMP` | float | `default_temp` | Heater temperature for timed mode (°C, max 75) |
| `HOURS` | float | 0 | Duration hours |
| `MINUTES` | float | 0 | Duration minutes |
| `SECONDS` | float | 0 | Duration seconds |
| `HUMIDITY` | float | *(unset)* | **Triggers humidity-hold mode.** Target % RH |
| `TEMP_MAX` | float | `default_temp` | Max heater temp in humidity-hold mode (°C, max 75) |
| `TEMP_MIN` | float | `35.0` | Min heater temp in humidity-hold mode (°C) |
| `MAX_HOURS` | float | 0 (= unlimited) | Optional timeout for humidity-hold mode |

If `HUMIDITY` is supplied the dryer enters humidity-hold mode; otherwise it
uses timed mode. If no duration is given for timed mode, `default_duration`
is used.

### `VIVID_DRY_STOP_VIVID_1`

Stops any active cycle and turns the heater off.

### `VIVID_DRY_STATUS_VIVID_1`

Prints current state to the GCode console.

---

## Multiple ViViD Units

Each physical ViViD unit gets its own `[vivid_dryer <Name>]` section. Klipper
discovers all instances automatically via the `load_config_prefix` entry point.

GCode commands are automatically namespaced so they never collide:

| Config section | Start command | Stop command | Status command |
|---|---|---|---|
| `[vivid_dryer Vivid_1]` | `VIVID_DRY_START_VIVID_1` | `VIVID_DRY_STOP_VIVID_1` | `VIVID_DRY_STATUS_VIVID_1` |
| `[vivid_dryer Vivid_2]` | `VIVID_DRY_START_VIVID_2` | `VIVID_DRY_STOP_VIVID_2` | `VIVID_DRY_STATUS_VIVID_2` |

Example `vivid_dryer.cfg` for two units:

```ini
[vivid_dryer Vivid_1]
heater:          Vivid_1_dryer
humidity_sensor: aht10 Vivid_1_dryer_left, aht10 Vivid_1_dryer_right
default_temp:    55.0

[vivid_dryer Vivid_2]
heater:          Vivid_2_dryer
humidity_sensor: aht10 Vivid_2_dryer_left, aht10 Vivid_2_dryer_right
default_temp:    55.0
```

The Mainsail widget (`vivid_dryer.html`) auto-discovers all instances by
querying `/printer/objects/list` on page load and renders a separate card
for each one.

---

## Mainsail Widget Setup

1. Copy `mainsail/vivid_dryer.html` to your Mainsail web root:
   ```bash
   cp mainsail/vivid_dryer.html ~/mainsail/
   ```
2. In Mainsail, go to **Settings → Interface → Custom Panels** and add an
   iframe pointing to:
   ```
   http://<your-printer-ip>/vivid_dryer.html
   ```

The widget:
- Auto-discovers all `[vivid_dryer *]` instances from Moonraker
- Renders a card per unit with mode badge, heater temp, left/right/average
  humidity, PTC ambient temp, live countdown, progress bar (timed mode), and
  humidity sparkline (humidity-hold mode)
- Provides timed and humidity-hold start controls and a stop button
- Auto-refreshes every 5 seconds

---

## idle_timeout Integration

Without the guard, Klipper's idle timeout will turn off the dryer heater
mid-cycle. To prevent this:

1. Make sure `vivid_idle_timeout_guard.cfg` is included in `printer.cfg`:
   ```ini
   [include vivid_idle_timeout_guard.cfg]
   ```

2. Configure `[idle_timeout]` to call the guard macro:
   ```ini
   [idle_timeout]
   gcode:
     _VIVID_IDLE_TIMEOUT_CHECK
   timeout: 600
   ```

The `_VIVID_IDLE_TIMEOUT_CHECK` macro loops over **all** `vivid_dryer`
instances in Klipper's object list. If any instance is active, the heaters
stay on. If all are idle, the normal shutdown runs.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| `heater 'Vivid_1_dryer' not found` | Confirm `[heater_generic Vivid_1_dryer]` exists in `printer.cfg` and the `heater:` option in `[vivid_dryer Vivid_1]` matches exactly |
| `humidity-hold mode is unavailable` | Confirm `humidity_sensor` is set and the sensor names include the `aht10 ` prefix, e.g. `aht10 Vivid_1_dryer_left` |
| Heater turns on but idle timeout kills it | Wire `_VIVID_IDLE_TIMEOUT_CHECK` into `[idle_timeout]` as shown above |
| Widget shows "Moonraker offline" | Check that Moonraker is running and the widget URL matches your printer's IP/port |
| Humidity always reads 0% | Verify AHT3X wiring and that `[temperature_sensor Vivid_1_dryer_left]` appears in Klipper's object list |
| `VIVID_DRY_START_VIVID_1` not found | Confirm `vivid_dryer.py` is in `~/klipper/klippy/extras/` and Klipper has been restarted |
| Commands conflict between units | Each `[vivid_dryer Name]` generates its own namespaced commands — check for duplicate section names |
