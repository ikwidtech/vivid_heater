"""
vivid_dryer.py — Klipper extra for the ViViD filament dryer.

Two operating modes
-------------------

Mode 1 — Timed dry cycle
    VIVID_DRY_START TEMP=55 HOURS=6
    Heats the desiccant chamber to a fixed temperature and shuts off
    automatically when the timer expires.

Mode 2 — Humidity-hold
    VIVID_DRY_START HUMIDITY=30 TEMP_MAX=55 TEMP_MIN=35
    Reads a humidity sensor (any Klipper temperature_sensor that exposes a
    'humidity' key in get_status()) and modulates the heater temperature
    between TEMP_MIN and TEMP_MAX to drive relative humidity down to the
    target and hold it there indefinitely (or until MAX_HOURS elapses).

    Control loop (runs every humidity_poll_interval seconds):
      - humidity > target + deadband  → raise temp (linear interp toward TEMP_MAX)
      - humidity < target - deadband  → lower temp toward TEMP_MIN (or turn off)
      - within deadband               → hold current temp

Common commands
---------------
    VIVID_DRY_STOP     — stop any running cycle
    VIVID_DRY_STATUS   — report current state via GCode response

Moonraker / Mainsail
--------------------
    get_status(eventtime) is called by Moonraker and returns a dict that
    drives the vivid_dryer.html widget.
"""

import logging

# Klipper convention: returning this value from a timer callback disables the timer.
TIMER_IDLE = 99999999.0

# How many deadband-widths above the target humidity we consider "full overshoot"
# (i.e. when the heater should run at TEMP_MAX).
HUMIDITY_SCALE_FACTOR = 3.0


class VividDryer:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.logger = logging.getLogger("vivid_dryer")

        # --- config options ---
        self.heater_name = config.get("heater", "Vivid_1_dryer")
        self.humidity_sensor_name = config.get("humidity_sensor", None)
        self.default_temp = config.getfloat("default_temp", 55.0)
        self.default_duration = config.getint("default_duration", 14400)
        self.humidity_deadband = config.getfloat("humidity_deadband", 3.0)
        self.humidity_poll_interval = config.getint("humidity_poll_interval", 30)

        # --- runtime state ---
        self._heater = None
        self._humidity_sensor = None
        self._active = False
        self._mode = "idle"          # "timed" | "humidity" | "idle"
        self._target_temp = 0.0
        self._temp_min = 0.0
        self._temp_max = 0.0
        self._target_humidity = None
        self._current_humidity = None
        self._end_time = None        # reactor monotonic time when timed cycle ends
        self._max_end_time = None    # optional MAX_HOURS deadline for humidity mode
        self._start_time = None
        self._timer = None
        self._humidity_history = []  # list of (eventtime, humidity) for sparkline

        # --- register GCode commands ---
        gcode = self.printer.lookup_object("gcode")
        gcode.register_command(
            "VIVID_DRY_START",
            self.cmd_VIVID_DRY_START,
            desc=self.cmd_VIVID_DRY_START_help,
        )
        gcode.register_command(
            "VIVID_DRY_STOP",
            self.cmd_VIVID_DRY_STOP,
            desc="Stop an active ViViD dryer cycle",
        )
        gcode.register_command(
            "VIVID_DRY_STATUS",
            self.cmd_VIVID_DRY_STATUS,
            desc="Report current ViViD dryer status",
        )

        # --- hook printer ready event ---
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("klippy:shutdown", self._handle_shutdown)

    cmd_VIVID_DRY_START_help = (
        "Start a ViViD dryer cycle. "
        "Timed: TEMP=55 HOURS=6. "
        "Humidity-hold: HUMIDITY=30 TEMP_MAX=55 TEMP_MIN=35 [MAX_HOURS=24]."
    )

    # ------------------------------------------------------------------
    # Printer event handlers
    # ------------------------------------------------------------------

    def _handle_ready(self):
        """Look up heater and optional humidity sensor after printer init."""
        pheaters = self.printer.lookup_object("heaters")
        try:
            self._heater = pheaters.lookup_heater(self.heater_name)
        except Exception:
            self.logger.error(
                "vivid_dryer: heater '%s' not found", self.heater_name
            )

        if self.humidity_sensor_name:
            try:
                self._humidity_sensor = self.printer.lookup_object(
                    "temperature_sensor %s" % self.humidity_sensor_name
                )
            except Exception:
                self.logger.warning(
                    "vivid_dryer: humidity sensor '%s' not found — "
                    "humidity-hold mode unavailable",
                    self.humidity_sensor_name,
                )

    def _handle_shutdown(self):
        self._stop_cycle()

    # ------------------------------------------------------------------
    # GCode command handlers
    # ------------------------------------------------------------------

    def cmd_VIVID_DRY_START(self, gcmd):
        if self._heater is None:
            gcmd.error("vivid_dryer: heater not available")
            return

        if self._active:
            gcmd.respond_info(
                "vivid_dryer: stopping previous cycle before starting a new one"
            )
            self._stop_cycle()

        humidity_target = gcmd.get_float("HUMIDITY", None, minval=0.0, maxval=100.0)

        if humidity_target is not None:
            # --- humidity-hold mode ---
            if self._humidity_sensor is None:
                gcmd.error(
                    "vivid_dryer: no humidity sensor configured; "
                    "humidity-hold mode is unavailable"
                )
                return

            temp_max = gcmd.get_float(
                "TEMP_MAX", self.default_temp, minval=1.0, maxval=70.0
            )
            temp_min = gcmd.get_float("TEMP_MIN", 35.0, minval=1.0, maxval=70.0)
            if temp_min >= temp_max:
                gcmd.error(
                    "vivid_dryer: TEMP_MIN (%.1f) must be less than TEMP_MAX (%.1f)"
                    % (temp_min, temp_max)
                )
                return

            max_hours = gcmd.get_float("MAX_HOURS", 0.0, minval=0.0)

            self._mode = "humidity"
            self._target_humidity = humidity_target
            self._temp_min = temp_min
            self._temp_max = temp_max
            self._target_temp = temp_max  # start at max to drive humidity down fast
            self._active = True
            now = self.reactor.monotonic()
            self._start_time = now
            self._max_end_time = (
                now + max_hours * 3600.0 if max_hours > 0.0 else None
            )
            self._humidity_history = []

            self._set_heater_temp(self._target_temp)
            self._timer = self.reactor.register_timer(
                self._humidity_loop, now + self.humidity_poll_interval
            )
            gcmd.respond_info(
                "vivid_dryer: humidity-hold mode started. "
                "Target: %.0f%% RH, Temp range: %.1f–%.1f°C%s"
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
            temp = gcmd.get_float("TEMP", self.default_temp, minval=1.0, maxval=70.0)
            hours = gcmd.get_float("HOURS", 0.0, minval=0.0)
            minutes = gcmd.get_float("MINUTES", 0.0, minval=0.0)
            seconds = gcmd.get_float("SECONDS", 0.0, minval=0.0)
            duration = hours * 3600.0 + minutes * 60.0 + seconds
            if duration <= 0.0:
                duration = float(self.default_duration)

            self._mode = "timed"
            self._target_temp = temp
            self._temp_min = 0.0
            self._temp_max = temp
            self._target_humidity = None
            self._active = True
            now = self.reactor.monotonic()
            self._start_time = now
            self._end_time = now + duration
            self._max_end_time = None

            self._set_heater_temp(temp)
            self._timer = self.reactor.register_timer(self._timed_loop, now + 1.0)
            gcmd.respond_info(
                "vivid_dryer: timed mode started. "
                "Temp: %.1f°C, Duration: %s"
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
                "vivid_dryer: TIMED mode — temp %.1f°C, remaining %s"
                % (self._target_temp, self._format_duration(remaining))
            )
        elif self._mode == "humidity":
            humidity_str = (
                "%.1f%%" % self._current_humidity
                if self._current_humidity is not None
                else "unknown"
            )
            remaining_str = "∞"
            if self._max_end_time is not None:
                remaining_str = self._format_duration(
                    max(0.0, self._max_end_time - now)
                )
            gcmd.respond_info(
                "vivid_dryer: HUMIDITY-HOLD mode — "
                "current %.0f%% RH (target %.0f%%), "
                "heater %.1f°C (range %.1f–%.1f°C), "
                "time remaining: %s"
                % (
                    self._current_humidity or 0.0,
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
                "vivid_dryer: timed cycle complete, heater off"
            )
            return TIMER_IDLE
        return eventtime + 1.0

    def _humidity_loop(self, eventtime):
        """Called every humidity_poll_interval s during humidity-hold mode."""
        if not self._active or self._mode != "humidity":
            return TIMER_IDLE
        now = self.reactor.monotonic()

        # check MAX_HOURS timeout
        if self._max_end_time is not None and now >= self._max_end_time:
            self._stop_cycle()
            self.printer.lookup_object("gcode").respond_info(
                "vivid_dryer: MAX_HOURS timeout reached, cycle stopped"
            )
            return TIMER_IDLE

        # read humidity
        humidity = self._read_humidity()
        if humidity is not None:
            self._current_humidity = humidity
            self._humidity_history.append((eventtime, humidity))
            # keep last 30 readings
            if len(self._humidity_history) > 30:
                self._humidity_history.pop(0)

            # proportional step logic
            new_temp = self._compute_humidity_temp(humidity)
            self._target_temp = new_temp
            self._set_heater_temp(new_temp)

        return eventtime + self.humidity_poll_interval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_humidity(self):
        """Read humidity from the configured sensor. Returns float or None."""
        if self._humidity_sensor is None:
            return None
        try:
            status = self._humidity_sensor.get_status(self.reactor.monotonic())
            return float(status.get("humidity", 0.0))
        except Exception as e:
            self.logger.warning("vivid_dryer: humidity read error: %s", e)
            return None

    def _compute_humidity_temp(self, current_humidity):
        """
        Linear interpolation between TEMP_MIN and TEMP_MAX based on how far
        current humidity is above the target.  Capped at TEMP_MAX.

        If current_humidity <= target - deadband  → TEMP_MIN (or off)
        If current_humidity >= target + deadband + range → TEMP_MAX
        """
        target = self._target_humidity
        deadband = self.humidity_deadband

        if current_humidity <= target - deadband:
            # well below target, cool down
            return self._temp_min

        if current_humidity <= target + deadband:
            # within deadband, hold current temp unchanged
            return self._target_temp

        # above target + deadband: interpolate
        # at target+deadband → TEMP_MIN; at target+deadband+(range) → TEMP_MAX
        temp_range = self._temp_max - self._temp_min
        if temp_range <= 0.0:
            return self._temp_max

        humidity_above = current_humidity - (target + deadband)
        # full range of humidity overshoot we consider (3× deadband)
        scale_range = deadband * HUMIDITY_SCALE_FACTOR if deadband > 0 else 1.0
        fraction = min(humidity_above / scale_range, 1.0)
        return self._temp_min + fraction * temp_range

    def _set_heater_temp(self, temp):
        """Set the heater target temperature."""
        if self._heater is None:
            return
        try:
            self._heater.set_temp(temp)
        except Exception as e:
            self.logger.error("vivid_dryer: failed to set heater temp: %s", e)

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
        self._target_humidity = None
        self._current_humidity = None
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
            "target_humidity": self._target_humidity,
            "remaining_seconds": remaining,
            "temp_min": self._temp_min,
            "temp_max": self._temp_max,
        }


def load_config(config):
    return VividDryer(config)
