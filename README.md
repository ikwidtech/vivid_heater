# ViViD Dryer — Klipper Extra

A Klipper extra that adds intelligent filament drying control to the ViViD filament dryer.
Supports both **timed dry cycles** and **humidity-hold mode** with proportional temperature
control. Multiple ViViD units are supported simultaneously.

---

## What it does

### Mode 1 — Timed dry cycle

Set a temperature and duration. The heater runs at the target temperature and
automatically shuts off when the timer expires.

```
VIVID_DRY_START_VIVID_1 TEMP=55 HOURS=4
VIVID_DRY_START_VIVID_1 TEMP=65 HOURS=6 MINUTES=30
```

### Mode 2 — Humidity-hold

Set a target humidity percentage. The controller reads the average of the two AHT3X
sensors and modulates the heater temperature between `TEMP_MIN` and `TEMP_MAX` to
drive the filament spool to the target humidity and hold it there indefinitely (or
until an optional `MAX_HOURS` timeout).

```
VIVID_DRY_START_VIVID_1 HUMIDITY=30 TEMP_MAX=55 TEMP_MIN=35
VIVID_DRY_START_VIVID_1 HUMIDITY=20 TEMP_MAX=60 TEMP_MIN=40 MAX_HOURS=12
```

**Control formula:**
```
new_temp = TEMP_MIN + (TEMP_MAX - TEMP_MIN)
           * (current_humidity - target_humidity) / (target_humidity * 0.5)
```
Clamped to `[TEMP_MIN, TEMP_MAX]`. A configurable deadband prevents unnecessary
temperature adjustments when humidity is already close to target.

---

## Hardware requirements

Each ViViD unit requires the following already configured in `printer.cfg`:

| Config section | Description |
|---|---|
| `[temperature_sensor <Name>_dryer_left]` | AHT3X left sensor (software I2C) |
| `[temperature_sensor <Name>_dryer_right]` | AHT3X right sensor (software I2C) |
| `[temperature_sensor <Name>_PTC_temp]` | PTC ambient temperature thermistor |
| `[heater_generic <Name>_dryer]` | Heater using `temperature_combined` of both AHT3X sensors |
| `[verify_heater <Name>_dryer]` | Heater verification |
| `[heater_fan <Name>_dryer_fan]` | Fan tied to the heater |

The `vivid_dryer.cfg` in this repo only adds the `[vivid_dryer]` controller section —
**do not duplicate** the hardware blocks above.

### AHT3X wiring notes

The AHT3X sensors are configured using **software I2C** (`i2c_software_scl_pin` /
`i2c_software_sda_pin`) on the ViViD MCU. They are registered internally by Klipper
as `aht10 <sensor_name>` (from `klippy/extras/aht10.py`). The `humidity_sensor` config
option must use the `aht10 <name>` lookup keys, e.g.:

```ini
humidity_sensor: aht10 Vivid_1_dryer_left, aht10 Vivid_1_dryer_right
```

---

## Quick install

```bash
curl -fsSL https://raw.githubusercontent.com/ikwidtech/vivid_heater/main/install.sh | bash
```

---

## Manual install

1. Copy the Python extra to your Klipper installation:
   ```bash
   cp klippy/extras/vivid_dryer.py ~/klipper/klippy/extras/
   ```

2. Copy config files to your Klipper config directory:
   ```bash
   cp config/vivid_dryer.cfg        ~/printer_data/config/
   cp config/idle_timeout_guard.cfg ~/printer_data/config/vivid_idle_timeout_guard.cfg
   cp mainsail/vivid_dryer_panel.cfg ~/printer_data/config/
   ```

3. Add includes to `printer.cfg`:
   ```ini
   [include vivid_dryer.cfg]
   [include vivid_dryer_panel.cfg]
   ```

4. (Optional) Copy the Mainsail widget:
   ```bash
   cp mainsail/vivid_dryer.html ~/mainsail/
   ```

5. Restart Klipper:
   ```bash
   sudo systemctl restart klipper
   ```

---

## Config reference

The `[vivid_dryer <Name>]` section accepts the following options:

| Option | Default | Description |
|---|---|---|
| `heater` | `Vivid_1_dryer` | Name of the `heater_generic` section to control |
| `humidity_sensor` | *(none)* | Comma-separated list of `aht10 <name>` sensor object keys |
| `default_temp` | `55.0` | Default drying temperature (°C) used when TEMP is omitted |
| `default_duration` | `14400` | Default duration in seconds (4 hours) used when HOURS/MINUTES/SECONDS are omitted |
| `humidity_deadband` | `3.0` | Humidity deadband (% RH) — only adjust temp when outside this range |
| `humidity_poll_interval` | `30` | Seconds between humidity control loop iterations |

---

## GCode command reference

### Multi-instance naming

| Config section | Command prefix |
|---|---|
| `[vivid_dryer]` | `VIVID_DRY_START`, `VIVID_DRY_STOP`, `VIVID_DRY_STATUS` |
| `[vivid_dryer Vivid_1]` | `VIVID_DRY_START_VIVID_1`, `VIVID_DRY_STOP_VIVID_1`, `VIVID_DRY_STATUS_VIVID_1` |
| `[vivid_dryer Vivid_2]` | `VIVID_DRY_START_VIVID_2`, `VIVID_DRY_STOP_VIVID_2`, `VIVID_DRY_STATUS_VIVID_2` |

### VIVID_DRY_START[_NAME]

**Timed mode parameters:**

| Parameter | Default | Description |
|---|---|---|
| `TEMP` | `default_temp` | Target temperature in °C (max 75) |
| `HOURS` | `0` | Duration hours |
| `MINUTES` | `0` | Duration minutes |
| `SECONDS` | `0` | Duration seconds |

If no duration is provided, `default_duration` is used.

**Humidity-hold mode parameters** (triggered when `HUMIDITY` is provided):

| Parameter | Default | Description |
|---|---|---|
| `HUMIDITY` | *(required)* | Target humidity in % RH |
| `TEMP_MAX` | `default_temp` | Maximum heater temperature (°C) |
| `TEMP_MIN` | `35.0` | Minimum heater temperature (°C) |
| `MAX_HOURS` | `0` | Maximum run time in hours (0 = unlimited) |

### VIVID_DRY_STOP[_NAME]

Stops any active dry cycle and turns off the heater.

### VIVID_DRY_STATUS[_NAME]

Reports current mode, temperature, humidity, and time remaining.

---

## Multiple ViViD units

Each physical ViViD unit gets its own `[vivid_dryer <Name>]` section. Klipper
discovers all instances automatically via `load_config_prefix`.

Example config for two units:

```ini
[vivid_dryer Vivid_1]
heater:                 Vivid_1_dryer
humidity_sensor:        aht10 Vivid_1_dryer_left, aht10 Vivid_1_dryer_right
default_temp:           55.0
default_duration:       14400
humidity_deadband:      3.0
humidity_poll_interval: 30

[vivid_dryer Vivid_2]
heater:                 Vivid_2_dryer
humidity_sensor:        aht10 Vivid_2_dryer_left, aht10 Vivid_2_dryer_right
default_temp:           55.0
default_duration:       14400
humidity_deadband:      3.0
humidity_poll_interval: 30
```

GCode commands for the second unit:
```
VIVID_DRY_START_VIVID_2 TEMP=55 HOURS=4
VIVID_DRY_STATUS_VIVID_2
VIVID_DRY_STOP_VIVID_2
```

---

## Mainsail widget

Copy `mainsail/vivid_dryer.html` to your Mainsail web directory, then navigate to:

```
http://<your-printer-ip>/vivid_dryer.html
```

The widget auto-discovers all `vivid_dryer *` instances from Moonraker, and renders
a card for each one showing:

- Mode badge (IDLE / TIMED / HUMIDITY)
- Heater current and target temperature
- Left and right AHT3X humidity readings
- Average humidity (large)
- PTC ambient temperature (if available)
- Time remaining countdown
- Progress bar (timed mode)
- Humidity sparkline (last 30 readings)
- Controls for both modes with a Stop button

The widget uses `window.location.origin` as the Moonraker base URL and polls every 5 seconds.

---

## idle_timeout integration

To prevent Klipper's idle timeout from turning off the dryer heater mid-cycle:

1. Include the guard file in `printer.cfg`:
   ```ini
   [include vivid_idle_timeout_guard.cfg]
   ```

2. Set your `[idle_timeout]` gcode to call the guard:
   ```ini
   [idle_timeout]
   gcode: _VIVID_IDLE_TIMEOUT_CHECK
   timeout: 600
   ```

If any ViViD dryer is active, `_VIVID_IDLE_TIMEOUT_CHECK` skips the shutdown.
Otherwise it runs the normal `TURN_OFF_HEATERS` + `M84` sequence.

---

## Troubleshooting

**"heater not available" error**
- Check that the `heater` option in `[vivid_dryer]` matches a `[heater_generic]` section name exactly.

**"no humidity sensors configured" error**
- Ensure `humidity_sensor` lists the correct `aht10 <name>` keys.
- AHT3X sensors defined as `[temperature_sensor Vivid_1_dryer_left]` are registered by Klipper as `aht10 Vivid_1_dryer_left`.

**Humidity readings show 0.0 or are missing**
- Verify the AHT3X wiring and I2C pins are correct.
- Check `~/printer_data/logs/klippy.log` for sensor errors.

**Widget shows "Cannot reach Moonraker"**
- Ensure Moonraker is running: `sudo systemctl status moonraker`
- Check that the widget is served from the same host as Moonraker (CORS).

**GCode command not found**
- Ensure `vivid_dryer.py` is in `~/klipper/klippy/extras/`
- Ensure `[include vivid_dryer.cfg]` is in `printer.cfg`
- Restart Klipper: `sudo systemctl restart klipper`
