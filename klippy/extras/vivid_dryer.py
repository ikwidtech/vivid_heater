"""
vivid_dryer.py — Klipper extra for the ViViD filament dryer.

Multi-instance support
----------------------
Both [vivid_dryer] (unnamed) and [vivid_dryer Vivid_1], [vivid_dryer Vivid_2]
etc. are supported.  GCode commands are namespaced per instance:
  [vivid_dryer Vivid_1]  →  VIVID_DRY_START_VIVID_1, VIVID_DRY_STOP_VIVID_1,
                             VIVID_DRY_STATUS_VIVID_1
  [vivid_dryer]          →  VIVID_DRY_START, VIVID_DRY_STOP, VIVID_DRY_STATUS

Two operating modes
-------------------
Mode 1 — Timed dry cycle
    VIVID_DRY_START[_NAME] TEMP=55 HOURS=6 [MINUTES=0] [SECONDS=0]
    Heats the desiccant chamber to a fixed temperature and shuts off
    automatically when the timer expires.

Mode 2 — Humidity-hold
    VIVID_DRY_START[_NAME] HUMIDITY=30 TEMP_MAX=55 TEMP_MIN=35 [MAX_HOURS=0]
    Reads humidity from all configured AHT3X sensors, averages them, and
    applies proportional control to modulate the heater temperature between
    TEMP_MIN and TEMP_MAX.  Runs indefinitely or until MAX_HOURS elapses.

    Proportional formula:
      new_temp = TEMP_MIN + (TEMP_MAX - TEMP_MIN)
                 * (current_humidity - target_humidity) / (target_humidity * 0.5)
      clamped to [TEMP_MIN, TEMP_MAX]
    Deadband: only update the setpoint when |current - target| > deadband.

Moonraker / Mainsail
--------------------
    get_status(eventtime) is called by Moonraker and drives vivid_dryer.html.
"""

import logging

# Returning this from a reactor timer callback disables the timer.
TIMER_IDLE = 99999999.0

# Maximum heater temperature — hardware limit from max_temp: 75 on the heater.
TEMP_MAX_HARD_LIMIT = 75.0


class VividDryer:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()

        # Derive instance suffix from section name, e.g. "vivid_dryer Vivid_1"
        # → suffix = "VIVID_1", cmd_suffix = "_VIVID_1"
        full_name = config.get_name()   # e.g. "vivid_dryer" or "vivid_dryer Vivid_1"
        parts = full_name.split(None, 1)
        if len(parts) > 1:
            self._instance_name = parts[1]           # "Vivid_1"
            cmd_suffix = "_" + parts[1].upper()      # "_VIVID_1"
        else:
            self._instance_name = ""
            cmd_suffix = ""

        self.logger = logging.getLogger(
            "vivid_dryer" + ("." + self._instance_name if self._instance_name else "")
        )

        # --- config options ---
        self.heater_name = config.get("heater", "Vivid_1_dryer")

        # humidity_sensor: comma-separated list of object names, e.g.
        #   "aht10 Vivid_1_dryer_left, aht10 Vivid_1_dryer_right"
        raw_sensors = config.get("humidity_sensor", "")
        self._humidity_sensor_names = (
            [s.strip() for s in raw_sensors.split(",") if s.strip()]
            if raw_sensors.strip()
            else []
        )

        self.default_temp = config.getfloat("default_temp", 55.0)
        self.default_duration = config.getint("default_duration", 14400)
        self.humidity_deadband = config.getfloat("humidity_deadband", 3.0)
        self.humidity_poll_interval = config.getint("humidity_poll_interval", 30)

        # --- runtime state ---
        self._pheaters = None
        self._heater = None
        self._humidity_sensors = []      # resolved sensor objects
        self._active = False
        self._mode = "idle"              # "timed" | "humidity" | "idle"
        self._target_temp = 0.0
        self._temp_min = 0.0
        self._temp_max = 0.0
        self._target_humidity = 0.0
        self._current_humidity = 0.0
        self._current_humidity_left = 0.0
        self._current_humidity_right = 0.0
        self._end_time = None            # monotonic end time for timed mode
        self._max_end_time = None        # optional MAX_HOURS deadline
        self._start_time = None
        self._timer = None

        # --- register namespaced GCode commands ---
        gcode = self.printer.lookup_object("gcode")
        gcode.register_command(
            "VIVID_DRY_START" + cmd_suffix,
            self.cmd_VIVID_DRY_START,
            desc=(
                "Start a ViViD dryer cycle. "
                "Timed: TEMP=55 HOURS=6 [MINUTES=0] [SECONDS=0]. "
                "Humidity-hold: HUMIDITY=30 TEMP_MAX=55 TEMP_MIN=35 [MAX_HOURS=0]."
            ),
        )
        gcode.register_command(
            "VIVID_DRY_STOP" + cmd_suffix,
            self.cmd_VIVID_DRY_STOP,
            desc="Stop an active ViViD dryer cycle",
        )
        gcode.register_command(
            "VIVID_DRY_STATUS" + cmd_suffix,
            self.cmd_VIVID_DRY_STATUS,
            desc="Report current ViViD dryer status",
        )

        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("klippy:shutdown", self._handle_shutdown)

    # ------------------------------------------------------------------
    # Printer event handlers
    # ------------------------------------------------------------------

    def _handle_ready(self):
        """Resolve heater and humidity sensor objects after printer init."""
        self._pheaters = self.printer.lookup_object("heaters")
        try:
            self._heater = self._pheaters.lookup_heater(self.heater_name)
        except Exception:
            self.logger.error(
                "vivid_dryer: heater '%s' not found — dryer unavailable",
                self.heater_name,
            )

        resolved = []
        for name in self._humidity_sensor_names:
            try:
                obj = self.printer.lookup_object(name)
                resolved.append(obj)
            except Exception:
                self.logger.warning(
                    "vivid_dryer: humidity sensor '%s' not found — "
                    "humidity-hold mode may be unavailable",
                    name,
                )
        self._humidity_sensors = resolved
        if self._humidity_sensor_names and not resolved:
            self.logger.warning(
                "vivid_dryer: no humidity sensors resolved — "
                "humidity-hold mode disabled"
            )

    def _handle_shutdown(self):
        self._stop_cycle()

    # ------------------------------------------------------------------
    # GCode command handlers
    # ------------------------------------------------------------------

    def cmd_VIVID_DRY_START(self, gcmd):
        if self._heater is None:
            gcmd.error("vivid_dryer: heater '%s' not available" % self.heater_name)
            return

        if self._active:
            gcmd.respond_info(
                "vivid_dryer: stopping previous cycle before starting a new one"
            )
            self._stop_cycle()

        humidity_target = gcmd.get_float("HUMIDITY", None, minval=0.0, maxval=100.0)

        if humidity_target is not None:
            # ---- humidity-hold mode ----
            if not self._humidity_sensors:
                gcmd.error(
                    "vivid_dryer: no humidity sensors available — "
                    "humidity-hold mode is unavailable"
                )
                return

            temp_max = gcmd.get_float(
                "TEMP_MAX", self.default_temp,
                minval=1.0, maxval=TEMP_MAX_HARD_LIMIT,
            )
            temp_min = gcmd.get_float(
                "TEMP_MIN", 35.0,
                minval=1.0, maxval=TEMP_MAX_HARD_LIMIT,
            )
            if temp_min >= temp_max:
                gcmd.error(
                    "vivid_dryer: TEMP_MIN (%.1f) must be less than TEMP_MAX (%.1f)"
                    % (temp_min, temp_max)
                )
                return

            max_hours = gcmd.get_float("MAX_HOURS", 0.0, minval=0.0)
            now = self.reactor.monotonic()

            self._mode = "humidity"
            self._target_humidity = humidity_target
            self._temp_min = temp_min
            self._temp_max = temp_max
            self._target_temp = temp_max   # start at max to drive humidity down fast
            self._active = True
            self._start_time = now
            self._max_end_time = (
                now + max_hours * 3600.0 if max_hours > 0.0 else None
            )

            self._set_temp(self._target_temp)
            self._timer = self.reactor.register_timer(
                self._humidity_loop, now + self.humidity_poll_interval
            )
            gcmd.respond_info(
                "vivid_dryer: humidity-hold mode started. "
                "Target: %.0f%% RH, Temp range: %.1f-%.1f C%s"
                % (
                    humidity_target,
                    temp_min,
                    temp_max,
                    (", max %.1f h" % max_hours if max_hours > 0.0 else " (no timeout)"),
                )
            )
        else:
            # ---- timed mode ----
            temp = gcmd.get_float(
                "TEMP", self.default_temp,
                minval=1.0, maxval=TEMP_MAX_HARD_LIMIT,
            )
            hours = gcmd.get_float("HOURS", 0.0, minval=0.0)
            minutes = gcmd.get_float("MINUTES", 0.0, minval=0.0)
            seconds = gcmd.get_float("SECONDS", 0.0, minval=0.0)
            duration = hours * 3600.0 + minutes * 60.0 + seconds
            if duration <= 0.0:
                duration = float(self.default_duration)

            now = self.reactor.monotonic()
            self._mode = "timed"
            self._target_temp = temp
            self._temp_min = 0.0
            self._temp_max = temp
            self._target_humidity = 0.0
            self._active = True
            self._start_time = now
            self._end_time = now + duration
            self._max_end_time = None

            self._set_temp(temp)
            self._timer = self.reactor.register_timer(self._timed_loop, now + 1.0)
            gcmd.respond_info(
                "vivid_dryer: timed mode started. Temp: %.1f C, Duration: %s"
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
                "vivid_dryer: TIMED mode — temp %.1f C, remaining %s"
                % (self._target_temp, self._format_duration(remaining))
            )
        elif self._mode == "humidity":
            remaining_str = "unlimited"
            if self._max_end_time is not None:
                remaining_str = self._format_duration(
                    max(0.0, self._max_end_time - now)
                )
            gcmd.respond_info(
                "vivid_dryer: HUMIDITY-HOLD mode — "
                "current %.1f%% RH (target %.0f%%), "
                "heater %.1f C (range %.1f-%.1f C), "
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
            self.printer.lookup_object("gcode").respond_info(
                "vivid_dryer: timed cycle complete — heater off"
            )
            return TIMER_IDLE
        return eventtime + 1.0

    def _humidity_loop(self, eventtime):
        """Called every humidity_poll_interval s during humidity-hold mode."""
        if not self._active or self._mode != "humidity":
            return TIMER_IDLE
        now = self.reactor.monotonic()

        if self._max_end_time is not None and now >= self._max_end_time:
            self._stop_cycle()
            self.printer.lookup_object("gcode").respond_info(
                "vivid_dryer: MAX_HOURS timeout reached — cycle stopped"
            )
            return TIMER_IDLE

        readings = self._read_humidity_sensors()
        if readings:
            avg = sum(readings) / len(readings)
            self._current_humidity = avg
            self._current_humidity_left = readings[0] if len(readings) >= 1 else 0.0
            self._current_humidity_right = readings[1] if len(readings) >= 2 else 0.0

            new_temp = self._compute_humidity_temp(avg)
            # Apply deadband: only update if outside ±deadband
            if abs(avg - self._target_humidity) > self.humidity_deadband:
                self._target_temp = new_temp
                self._set_temp(new_temp)

        return eventtime + self.humidity_poll_interval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_humidity_sensors(self):
        """Read humidity from all configured sensors. Returns list of floats."""
        readings = []
        now = self.reactor.monotonic()
        for sensor in self._humidity_sensors:
            try:
                status = sensor.get_status(now)
                readings.append(float(status.get("humidity", 0.0)))
            except Exception as exc:
                self.logger.warning(
                    "vivid_dryer: humidity read error from sensor: %s", exc
                )
        return readings

    def _compute_humidity_temp(self, current_humidity):
        """
        Proportional control:
          new_temp = TEMP_MIN + (TEMP_MAX - TEMP_MIN)
                     * (current - target) / (target * 0.5)
        Clamped to [TEMP_MIN, TEMP_MAX].
        """
        target = self._target_humidity
        if target <= 0.0:
            return self._temp_min
        scale = target * 0.5
        if scale <= 0.0:
            scale = 1.0
        fraction = (current_humidity - target) / scale
        temp = self._temp_min + (self._temp_max - self._temp_min) * fraction
        return max(self._temp_min, min(self._temp_max, temp))

    def _set_temp(self, temp):
        """Set the heater target temperature via the heaters manager."""
        if self._pheaters is None or self._heater is None:
            return
        try:
            self._pheaters.set_temperature(self._heater, temp, wait=False)
        except Exception as exc:
            self.logger.error(
                "vivid_dryer: failed to set heater temp to %.1f: %s", temp, exc
            )

    def _stop_cycle(self):
        """Turn off heater and reset all runtime state."""
        if self._timer is not None:
            self.reactor.update_timer(self._timer, TIMER_IDLE)
            self._timer = None
        self._set_temp(0.0)
        self._active = False
        self._mode = "idle"
        self._target_temp = 0.0
        self._end_time = None
        self._max_end_time = None
        self._target_humidity = 0.0
        self._current_humidity = 0.0
        self._current_humidity_left = 0.0
        self._current_humidity_right = 0.0
        self._temp_min = 0.0
        self._temp_max = 0.0

    @staticmethod
    def _format_duration(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return "%02d:%02d:%02d" % (h, m, s)

    # ------------------------------------------------------------------
    # Moonraker / Mainsail status
    # ------------------------------------------------------------------

    def get_status(self, eventtime):
        now = self.reactor.monotonic()
        remaining = 0
        if self._active:
            if self._mode == "timed" and self._end_time is not None:
                remaining = max(0, int(self._end_time - now))
            elif self._mode == "humidity" and self._max_end_time is not None:
                remaining = max(0, int(self._max_end_time - now))
        return {
            "active": self._active,
            "mode": self._mode,
            "target_temp": self._target_temp,
            "current_humidity": self._current_humidity,
            "current_humidity_left": self._current_humidity_left,
            "current_humidity_right": self._current_humidity_right,
            "target_humidity": self._target_humidity,
            "remaining_seconds": remaining,
            "temp_min": self._temp_min,
            "temp_max": self._temp_max,
        }


def load_config(config):
    """Entry point for [vivid_dryer] (unnamed instance)."""
    return VividDryer(config)


def load_config_prefix(config):
    """Entry point for [vivid_dryer Vivid_1], [vivid_dryer Vivid_2], etc."""
    return VividDryer(config)
