"""
vivid_dryer.py — Klipper extra for the ViViD filament dryer.

Supports named instances ([vivid_dryer Vivid_1]) and an unnamed instance
([vivid_dryer]) for backwards compatibility.  GCode commands are namespaced
per instance (VIVID_DRY_START_VIVID_1, etc.) when a name is present.

AHT3X humidity/temperature sensors are looked up as "aht10 <name>" objects
in Klipper (NOT as "temperature_sensor <name>").  Their get_status() method
returns {'temperature': float, 'humidity': int}.

Two operating modes
-------------------
Mode 1 — Timed dry cycle
    VIVID_DRY_START[_SUFFIX] TEMP=55 HOURS=6
    Heats to a fixed temperature for a fixed duration, then shuts off.

Mode 2 — Humidity-hold
    VIVID_DRY_START[_SUFFIX] HUMIDITY=30 TEMP_MAX=55 TEMP_MIN=35
    Averages humidity from all configured AHT3X sensors and modulates the
    heater temperature between TEMP_MIN and TEMP_MAX to reach and hold a
    target % RH.  Stops automatically when MAX_HOURS elapses (optional).

Moonraker / Mainsail
--------------------
    get_status(eventtime) is called by Moonraker and returns a dict that
    drives the vivid_dryer.html widget.
"""

import logging

# Returning this value from a timer callback disables (idles) the timer.
TIMER_IDLE = 99999999.0

# Maximum heater temperature enforced by this extra (hardware cap).
MAX_HEATER_TEMP = 75.0

# Number of deadband-widths above target humidity that corresponds to TEMP_MAX.
HUMIDITY_SCALE_FACTOR = 3.0


class VividDryer:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.logger = logging.getLogger("vivid_dryer")

        # Determine instance name from config section.
        # Section is "vivid_dryer" (unnamed) or "vivid_dryer Vivid_1" (named).
        section = config.get_name()
        parts = section.split(None, 1)
        if len(parts) > 1:
            self.name = parts[1]                              # e.g. "Vivid_1"
            self._suffix = "_" + self.name.upper().replace(" ", "_")  # "_VIVID_1"
        else:
            self.name = ""
            self._suffix = ""

        # --- config options ---
        self.heater_name = config.get("heater", "Vivid_1_dryer")
        humidity_str = config.get("humidity_sensor", "")
        # humidity_sensor is a comma-separated list of "aht10 <name>" entries.
        self.humidity_sensor_names = [
            s.strip() for s in humidity_str.split(",") if s.strip()
        ]
        self.default_temp = config.getfloat(
            "default_temp", 55.0, minval=20.0, maxval=MAX_HEATER_TEMP
        )
        self.default_duration = config.getint(
            "default_duration", 14400, minval=60
        )
        self.humidity_deadband = config.getfloat(
            "humidity_deadband", 3.0, minval=0.5
        )
        self.humidity_poll_interval = config.getfloat(
            "humidity_poll_interval", 30.0, minval=5.0
        )

        # --- runtime state ---
        self._heater = None
        self._humidity_sensors = []   # list of (object_name, sensor_obj)
        self._active = False
        self._mode = "idle"           # "timed" | "humidity" | "idle"
        self._target_temp = 0.0
        self._temp_min = 0.0
        self._temp_max = 0.0
        self._target_humidity = 0.0
        self._current_humidity = 0.0
        self._current_humidity_left = 0.0
        self._current_humidity_right = 0.0
        self._ambient_temp = 0.0
        self._end_time = None
        self._max_end_time = None
        self._start_time = None
        self._timer = None
        self._humidity_history = []   # list of (eventtime, humidity) for sparkline

        # --- register GCode commands (namespaced by instance suffix) ---
        gcode = self.printer.lookup_object("gcode")
        gcode.register_command(
            "VIVID_DRY_START" + self._suffix,
            self.cmd_VIVID_DRY_START,
            desc=self.cmd_VIVID_DRY_START_help,
        )
        gcode.register_command(
            "VIVID_DRY_STOP" + self._suffix,
            self.cmd_VIVID_DRY_STOP,
            desc="Stop the active ViViD dryer cycle",
        )
        gcode.register_command(
            "VIVID_DRY_STATUS" + self._suffix,
            self.cmd_VIVID_DRY_STATUS,
            desc="Report current ViViD dryer status",
        )

        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("klippy:shutdown", self._handle_shutdown)

    cmd_VIVID_DRY_START_help = (
        "Start a ViViD dryer cycle. "
        "Timed: TEMP=55 HOURS=6.  "
        "Humidity-hold: HUMIDITY=30 TEMP_MAX=55 TEMP_MIN=35 [MAX_HOURS=24]."
    )

    # ------------------------------------------------------------------
    # Printer event handlers
    # ------------------------------------------------------------------

    def _handle_ready(self):
        """Look up heater and AHT3X humidity sensors after printer init."""
        pheaters = self.printer.lookup_object("heaters")
        try:
            self._heater = pheaters.lookup_heater(self.heater_name)
        except Exception:
            self.logger.error(
                "vivid_dryer: heater '%s' not found", self.heater_name
            )

        # AHT3X sensors register as "aht10 <name>" objects in Klipper.
        for obj_name in self.humidity_sensor_names:
            try:
                sensor = self.printer.lookup_object(obj_name)
                self._humidity_sensors.append((obj_name, sensor))
            except Exception:
                self.logger.warning(
                    "vivid_dryer: humidity sensor '%s' not found — "
                    "check that aht10 section exists in printer.cfg",
                    obj_name,
                )

    def _handle_shutdown(self):
        self._stop_cycle()

    # ------------------------------------------------------------------
    # GCode command handlers
    # ------------------------------------------------------------------

    def cmd_VIVID_DRY_START(self, gcmd):
        if self._heater is None:
            gcmd.error(
                "vivid_dryer: heater '%s' not available" % self.heater_name
            )
            return

        if self._active:
            gcmd.respond_info(
                "vivid_dryer: stopping previous cycle before starting a new one"
            )
            self._stop_cycle()

        humidity_target = gcmd.get_float("HUMIDITY", None, minval=0.0, maxval=100.0)

        if humidity_target is not None:
            # --- humidity-hold mode ---
            if not self._humidity_sensors:
                gcmd.error(
                    "vivid_dryer: no humidity sensors configured; "
                    "humidity-hold mode is unavailable"
                )
                return
            temp_max = gcmd.get_float(
                "TEMP_MAX", self.default_temp, minval=1.0, maxval=MAX_HEATER_TEMP
            )
            temp_min = gcmd.get_float(
                "TEMP_MIN", 35.0, minval=1.0, maxval=MAX_HEATER_TEMP
            )
            if temp_min >= temp_max:
                gcmd.error(
                    "vivid_dryer: TEMP_MIN (%.1f) must be less than TEMP_MAX (%.1f)"
                    % (temp_min, temp_max)
                )
                return
            max_hours = gcmd.get_float("MAX_HOURS", 0.0, minval=0.0)

            now = self.reactor.monotonic()
            self._active = True
            self._mode = "humidity"
            self._target_humidity = humidity_target
            self._temp_min = temp_min
            self._temp_max = min(temp_max, MAX_HEATER_TEMP)
            self._target_temp = self._temp_max   # start at max to drive humidity down
            self._start_time = now
            self._max_end_time = (
                now + max_hours * 3600.0 if max_hours > 0.0 else None
            )
            self._humidity_history = []
            self._current_humidity = 0.0
            self._current_humidity_left = 0.0
            self._current_humidity_right = 0.0

            self._set_heater_temp(self._target_temp)
            self._timer = self.reactor.register_timer(
                self._humidity_loop, now + self.humidity_poll_interval
            )
            gcmd.respond_info(
                "vivid_dryer: humidity-hold mode started. "
                "Target: %.0f%% RH, Temp range: %.1f\u2013%.1f\u00b0C%s"
                % (
                    humidity_target,
                    temp_min,
                    temp_max,
                    (
                        ", max %.1f h" % max_hours
                        if max_hours > 0.0
                        else " (no timeout)"
                    ),
                )
            )
        else:
            # --- timed mode ---
            temp = gcmd.get_float(
                "TEMP", self.default_temp, minval=1.0, maxval=MAX_HEATER_TEMP
            )
            hours = gcmd.get_float("HOURS", 0.0, minval=0.0)
            minutes = gcmd.get_float("MINUTES", 0.0, minval=0.0)
            seconds_val = gcmd.get_float("SECONDS", 0.0, minval=0.0)
            duration = hours * 3600.0 + minutes * 60.0 + seconds_val
            if duration <= 0.0:
                duration = float(self.default_duration)

            temp = min(temp, MAX_HEATER_TEMP)
            now = self.reactor.monotonic()
            self._active = True
            self._mode = "timed"
            self._target_temp = temp
            self._temp_min = 0.0
            self._temp_max = temp
            self._target_humidity = 0.0
            self._start_time = now
            self._end_time = now + duration
            self._max_end_time = None

            self._set_heater_temp(temp)
            self._timer = self.reactor.register_timer(
                self._timed_loop, now + 1.0
            )
            gcmd.respond_info(
                "vivid_dryer: timed mode started. "
                "Temp: %.1f\u00b0C, Duration: %s"
                % (temp, self._format_duration(duration))
            )

    def cmd_VIVID_DRY_STOP(self, gcmd):
        if not self._active:
            gcmd.respond_info("vivid_dryer: no active cycle")
            return
        self._stop_cycle()
        gcmd.respond_info("vivid_dryer: cycle stopped")

    def cmd_VIVID_DRY_STATUS(self, gcmd):
        if not self._active:
            gcmd.respond_info("vivid_dryer: idle")
            return
        now = self.reactor.monotonic()
        if self._mode == "timed":
            remaining = max(0.0, self._end_time - now)
            gcmd.respond_info(
                "vivid_dryer: TIMED mode \u2014 temp %.1f\u00b0C, remaining %s"
                % (self._target_temp, self._format_duration(remaining))
            )
        elif self._mode == "humidity":
            remaining_str = "\u221e"
            if self._max_end_time is not None:
                remaining_str = self._format_duration(
                    max(0.0, self._max_end_time - now)
                )
            gcmd.respond_info(
                "vivid_dryer: HUMIDITY-HOLD mode \u2014 "
                "current %.1f%% RH (target %.0f%%), "
                "heater %.1f\u00b0C (range %.1f\u2013%.1f\u00b0C), "
                "time remaining: %s"
                % (
                    self._current_humidity,
                    self._target_humidity,
                    self._target_temp,
                    self._temp_min,
                    self._temp_max,
                    remaining_str,
                )
            )

    # ------------------------------------------------------------------
    # Timer callbacks
    # ------------------------------------------------------------------

    def _timed_loop(self, eventtime):
        """Called every ~1 s during a timed cycle."""
        if not self._active or self._mode != "timed":
            return TIMER_IDLE
        now = self.reactor.monotonic()
        if now >= self._end_time:
            self._stop_cycle()
            try:
                self.printer.lookup_object("gcode").respond_info(
                    "vivid_dryer: timed cycle complete, heater off"
                )
            except Exception:
                pass
            return TIMER_IDLE
        return eventtime + 1.0

    def _humidity_loop(self, eventtime):
        """Called every humidity_poll_interval s during humidity-hold mode."""
        if not self._active or self._mode != "humidity":
            return TIMER_IDLE
        now = self.reactor.monotonic()

        # Check MAX_HOURS timeout
        if self._max_end_time is not None and now >= self._max_end_time:
            self._stop_cycle()
            try:
                self.printer.lookup_object("gcode").respond_info(
                    "vivid_dryer: MAX_HOURS timeout reached, cycle stopped"
                )
            except Exception:
                pass
            return TIMER_IDLE

        avg, left, right, ambient = self._read_sensors()
        if avg is not None:
            self._current_humidity = avg
            if left is not None:
                self._current_humidity_left = left
            if right is not None:
                self._current_humidity_right = right
            if ambient is not None:
                self._ambient_temp = ambient
            self._humidity_history.append((eventtime, avg))
            if len(self._humidity_history) > 30:
                self._humidity_history.pop(0)

            new_temp = self._compute_humidity_temp(avg)
            self._target_temp = new_temp
            self._set_heater_temp(new_temp)

        return eventtime + self.humidity_poll_interval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_sensors(self):
        """Read humidity and temperature from all AHT3X sensors.

        Returns (avg_humidity, left_humidity, right_humidity, avg_ambient_temp).
        Any value may be None if no valid readings are available.
        AHT3X sensors expose get_status() -> {'temperature': float, 'humidity': int}.
        """
        humidities = []
        temps = []
        left_val = None
        right_val = None

        for obj_name, sensor in self._humidity_sensors:
            try:
                status = sensor.get_status(self.reactor.monotonic())
                humidity = float(status.get("humidity", 0.0))
                temp = float(status.get("temperature", 0.0))
                humidities.append(humidity)
                temps.append(temp)
                lname = obj_name.lower()
                if "left" in lname:
                    left_val = humidity
                elif "right" in lname:
                    right_val = humidity
            except Exception as exc:
                self.logger.warning(
                    "vivid_dryer: sensor read error from '%s': %s", obj_name, exc
                )

        avg_hum = sum(humidities) / len(humidities) if humidities else None
        avg_temp = sum(temps) / len(temps) if temps else None
        return avg_hum, left_val, right_val, avg_temp

    def _compute_humidity_temp(self, current_humidity):
        """Compute heater setpoint from current humidity using proportional control.

        - current <= target - deadband  →  TEMP_MIN  (humidity too low, cool)
        - current in (target ± deadband) →  hold current setpoint
        - current > target + deadband   →  interpolate toward TEMP_MAX
        """
        target = self._target_humidity
        deadband = self.humidity_deadband

        if current_humidity <= target - deadband:
            return self._temp_min

        if current_humidity <= target + deadband:
            return self._target_temp   # within deadband — hold

        # Above target + deadband: linearly ramp from TEMP_MIN to TEMP_MAX.
        temp_range = self._temp_max - self._temp_min
        if temp_range <= 0.0:
            return self._temp_max
        humidity_above = current_humidity - (target + deadband)
        scale_range = deadband * HUMIDITY_SCALE_FACTOR if deadband > 0 else 1.0
        fraction = min(humidity_above / scale_range, 1.0)
        return self._temp_min + fraction * temp_range

    def _set_heater_temp(self, temp):
        """Set the heater target temperature, enforcing the 75 °C cap."""
        if self._heater is None:
            return
        temp = max(0.0, min(float(temp), MAX_HEATER_TEMP))
        try:
            self._heater.set_temp(temp)
        except Exception as exc:
            self.logger.error(
                "vivid_dryer: failed to set heater temp: %s", exc
            )

    def _stop_cycle(self):
        """Turn off heater and reset all state."""
        if self._timer is not None:
            self.reactor.update_timer(self._timer, TIMER_IDLE)
            self._timer = None
        self._set_heater_temp(0.0)
        self._active = False
        self._mode = "idle"
        self._target_temp = 0.0
        self._end_time = None
        self._max_end_time = None
        self._target_humidity = 0.0
        self._temp_min = 0.0
        self._temp_max = 0.0

    @staticmethod
    def _format_duration(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return "%02d:%02d:%02d" % (h, m, s)

    # ------------------------------------------------------------------
    # Moonraker status
    # ------------------------------------------------------------------

    def get_status(self, eventtime):
        now = self.reactor.monotonic()
        remaining = 0
        total_secs = 0
        if self._active:
            if self._mode == "timed" and self._end_time is not None:
                remaining = max(0, int(self._end_time - now))
                if self._start_time is not None:
                    total_secs = int(self._end_time - self._start_time)
            elif self._mode == "humidity" and self._max_end_time is not None:
                remaining = max(0, int(self._max_end_time - now))

        # Read live heater temperature from the heater object.
        heater_temp = 0.0
        heater_target = 0.0
        if self._heater is not None:
            try:
                hs = self._heater.get_status(eventtime)
                heater_temp = hs.get("temperature", 0.0)
                heater_target = hs.get("target", 0.0)
            except Exception:
                pass

        # Read live ambient temperature from AHT3X sensors.
        ambient_temp = self._ambient_temp
        if self._humidity_sensors:
            temps = []
            for _, sensor in self._humidity_sensors:
                try:
                    s = sensor.get_status(eventtime)
                    temps.append(float(s.get("temperature", 0.0)))
                except Exception:
                    pass
            if temps:
                ambient_temp = sum(temps) / len(temps)

        return {
            "active": self._active,
            "mode": self._mode,
            "target_temp": self._target_temp,
            "heater_temp": heater_temp,
            "heater_target": heater_target,
            "ambient_temp": ambient_temp,
            "current_humidity": self._current_humidity,
            "current_humidity_left": self._current_humidity_left,
            "current_humidity_right": self._current_humidity_right,
            "target_humidity": self._target_humidity,
            "remaining_seconds": remaining,
            "total_seconds": total_secs,
            "temp_min": self._temp_min,
            "temp_max": self._temp_max,
        }


def load_config(config):
    return VividDryer(config)


def load_config_prefix(config):
    return VividDryer(config)
