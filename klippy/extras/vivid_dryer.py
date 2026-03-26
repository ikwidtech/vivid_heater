# vivid_dryer.py — Klipper extra for the ViViD filament dryer.
#
# Two operating modes
# -------------------
# Mode 1 — Timed dry cycle
#   VIVID_DRY_START[_NAME] TEMP=55 HOURS=6 [MINUTES=0] [SECONDS=0]
#   Heats the desiccant chamber to a fixed temperature and shuts off
#   automatically when the timer expires.
#
# Mode 2 — Humidity-hold
#   VIVID_DRY_START[_NAME] HUMIDITY=30 TEMP_MAX=55 TEMP_MIN=35 [MAX_HOURS=0]
#   Reads the average humidity from all configured AHT3X sensors and modulates
#   the heater temperature between TEMP_MIN and TEMP_MAX to drive relative
#   humidity to the target and hold it there.
#
#   Control formula:
#     new_temp = TEMP_MIN + (TEMP_MAX - TEMP_MIN)
#                * (current_humidity - target_humidity) / (target_humidity * 0.5)
#     clamped to [TEMP_MIN, TEMP_MAX]
#   Deadband: only update temp when |current - target| > humidity_deadband
#
# Multi-instance support
# ----------------------
#   [vivid_dryer]         → VIVID_DRY_START / VIVID_DRY_STOP / VIVID_DRY_STATUS
#   [vivid_dryer Vivid_1] → VIVID_DRY_START_VIVID_1 / VIVID_DRY_STOP_VIVID_1 / ...
#   [vivid_dryer Vivid_2] → VIVID_DRY_START_VIVID_2 / ...

import logging

TIMER_IDLE = 99999999.0


class VividDryer:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()
        self.logger = logging.getLogger('vivid_dryer')

        # Instance name from section name, e.g. "vivid_dryer Vivid_1" -> "Vivid_1"
        parts = config.get_name().split(None, 1)
        self.instance_name = parts[1] if len(parts) > 1 else ''
        suffix = ('_' + self.instance_name.upper().replace(' ', '_')
                  if self.instance_name else '')

        # Config options
        self.heater_name = config.get('heater', 'Vivid_1_dryer')
        sensor_str = config.get('humidity_sensor', '')
        self.sensor_names = (
            [s.strip() for s in sensor_str.split(',') if s.strip()]
            if sensor_str else []
        )
        self.default_temp = config.getfloat('default_temp', 55.0,
                                             minval=0., maxval=75.)
        self.default_duration = config.getint('default_duration', 14400,
                                               minval=1)
        self.humidity_deadband = config.getfloat('humidity_deadband', 3.0,
                                                  minval=0.)
        self.humidity_poll_interval = config.getint('humidity_poll_interval',
                                                     30, minval=5)

        # Runtime state
        self.heater = None
        self.sensors = []
        self.active = False
        self.mode = 'idle'
        self.target_temp = 0.
        self.target_humidity = 0.
        self.temp_min = 0.
        self.temp_max = 75.
        self.end_time = 0.
        self.max_end_time = 0.
        self.start_time = 0.
        self._last_humidities = []

        self.timer = self.reactor.register_timer(self._timer_cb,
                                                 self.reactor.NEVER)

        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('klippy:shutdown',
                                            self._handle_shutdown)

        # Register namespaced GCode commands
        self.gcode.register_command(
            'VIVID_DRY_START' + suffix, self.cmd_START,
            desc='Start a ViViD dryer cycle (timed or humidity-hold)')
        self.gcode.register_command(
            'VIVID_DRY_STOP' + suffix, self.cmd_STOP,
            desc='Stop an active ViViD dryer cycle')
        self.gcode.register_command(
            'VIVID_DRY_STATUS' + suffix, self.cmd_STATUS,
            desc='Report current ViViD dryer status')

    # ------------------------------------------------------------------
    # Printer event handlers
    # ------------------------------------------------------------------

    def _handle_ready(self):
        pheaters = self.printer.lookup_object('heaters')
        try:
            self.heater = pheaters.lookup_heater(self.heater_name)
        except Exception:
            self.logger.error("vivid_dryer: heater '%s' not found",
                              self.heater_name)

        self.sensors = []
        for name in self.sensor_names:
            try:
                obj = self.printer.lookup_object(name)
                self.sensors.append(obj)
            except Exception:
                self.logger.warning(
                    "vivid_dryer: humidity sensor '%s' not found — "
                    "humidity-hold mode may be unavailable", name)

        if self.sensor_names and not self.sensors:
            self.logger.warning(
                "vivid_dryer: no humidity sensors available — "
                "humidity-hold mode disabled")

    def _handle_shutdown(self):
        self._stop_cycle()

    # ------------------------------------------------------------------
    # GCode command handlers
    # ------------------------------------------------------------------

    def cmd_START(self, gcmd):
        if self.heater is None:
            gcmd.error('vivid_dryer: heater not available')
            return

        if self.active:
            gcmd.respond_info(
                'vivid_dryer: stopping previous cycle before starting a new one')
            self._stop_cycle()

        humidity_target = gcmd.get_float('HUMIDITY', None,
                                          minval=0.0, maxval=100.0)

        if humidity_target is not None:
            # Humidity-hold mode
            if not self.sensors:
                gcmd.error(
                    'vivid_dryer: no humidity sensors configured or available; '
                    'humidity-hold mode is unavailable')
                return

            temp_max = gcmd.get_float('TEMP_MAX', self.default_temp,
                                       minval=1.0, maxval=75.0)
            temp_min = gcmd.get_float('TEMP_MIN', 35.0,
                                       minval=1.0, maxval=75.0)
            if temp_min >= temp_max:
                gcmd.error(
                    'vivid_dryer: TEMP_MIN (%.1f) must be less than '
                    'TEMP_MAX (%.1f)' % (temp_min, temp_max))
                return

            max_hours = gcmd.get_float('MAX_HOURS', 0.0, minval=0.0)

            self.mode = 'humidity'
            self.target_humidity = humidity_target
            self.temp_min = temp_min
            self.temp_max = temp_max
            self.target_temp = temp_max
            self.active = True
            now = self.reactor.monotonic()
            self.start_time = now
            self.end_time = 0.
            self.max_end_time = (now + max_hours * 3600.0
                                 if max_hours > 0.0 else 0.)
            self._last_humidities = []

            self._set_temp(self.target_temp)
            self.reactor.update_timer(
                self.timer, now + self.humidity_poll_interval)
            gcmd.respond_info(
                'vivid_dryer: humidity-hold started. '
                'Target: %.0f%% RH, Temp range: %.1f–%.1f°C%s'
                % (humidity_target, temp_min, temp_max,
                   (', max %.1f h' % max_hours
                    if max_hours > 0.0 else ' (no timeout)')))
        else:
            # Timed mode
            temp = gcmd.get_float('TEMP', self.default_temp,
                                   minval=1.0, maxval=75.0)
            hours = gcmd.get_float('HOURS', 0.0, minval=0.0)
            minutes = gcmd.get_float('MINUTES', 0.0, minval=0.0)
            seconds = gcmd.get_float('SECONDS', 0.0, minval=0.0)
            duration = hours * 3600.0 + minutes * 60.0 + seconds
            if duration <= 0.0:
                duration = float(self.default_duration)

            self.mode = 'timed'
            self.target_temp = temp
            self.temp_min = 0.
            self.temp_max = temp
            self.target_humidity = 0.
            self.active = True
            now = self.reactor.monotonic()
            self.start_time = now
            self.end_time = now + duration
            self.max_end_time = 0.

            self._set_temp(temp)
            self.reactor.update_timer(self.timer, now + 1.0)
            gcmd.respond_info(
                'vivid_dryer: timed mode started. '
                'Temp: %.1f°C, Duration: %s'
                % (temp, self._fmt_duration(duration)))

    def cmd_STOP(self, gcmd):
        if not self.active:
            gcmd.respond_info('vivid_dryer: no active cycle')
            return
        self._stop_cycle()
        gcmd.respond_info('vivid_dryer: cycle stopped')

    def cmd_STATUS(self, gcmd):
        if not self.active:
            gcmd.respond_info('vivid_dryer: idle')
            return
        now = self.reactor.monotonic()
        if self.mode == 'timed':
            remaining = max(0.0, self.end_time - now)
            gcmd.respond_info(
                'vivid_dryer: TIMED — temp %.1f°C, remaining %s'
                % (self.target_temp, self._fmt_duration(remaining)))
        elif self.mode == 'humidity':
            humidity_str = ('%.1f%%' % self._avg_humidity()
                            if self.sensors else 'n/a')
            remaining_str = ('∞' if self.max_end_time <= 0.
                             else self._fmt_duration(
                                 max(0., self.max_end_time - now)))
            gcmd.respond_info(
                'vivid_dryer: HUMIDITY-HOLD — '
                'current %s (target %.0f%% RH), '
                'heater %.1f°C (range %.1f–%.1f°C), '
                'remaining: %s'
                % (humidity_str, self.target_humidity,
                   self.target_temp, self.temp_min, self.temp_max,
                   remaining_str))

    # ------------------------------------------------------------------
    # Timer callback (shared for both modes)
    # ------------------------------------------------------------------

    def _timer_cb(self, eventtime):
        if not self.active:
            return TIMER_IDLE

        now = self.reactor.monotonic()

        if self.mode == 'timed':
            if now >= self.end_time:
                self._stop_cycle()
                self.gcode.respond_info(
                    'vivid_dryer: timed cycle complete, heater off')
                return TIMER_IDLE
            return eventtime + 1.0

        if self.mode == 'humidity':
            # Check optional MAX_HOURS timeout
            if self.max_end_time > 0. and now >= self.max_end_time:
                self._stop_cycle()
                self.gcode.respond_info(
                    'vivid_dryer: MAX_HOURS timeout reached, cycle stopped')
                return TIMER_IDLE

            avg = self._avg_humidity()
            if avg is not None:
                # Record for sparkline (keep last 30)
                self._last_humidities.append(avg)
                if len(self._last_humidities) > 30:
                    self._last_humidities.pop(0)

                new_temp = self._compute_temp(avg)
                # Apply deadband — only update if outside deadband
                if abs(avg - self.target_humidity) > self.humidity_deadband:
                    self.target_temp = new_temp
                    self._set_temp(new_temp)

            return eventtime + self.humidity_poll_interval

        return TIMER_IDLE

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _avg_humidity(self):
        """Return average humidity from all sensors, or None if unavailable."""
        readings = []
        for i, sensor in enumerate(self.sensors):
            name = self.sensor_names[i] if i < len(self.sensor_names) else str(i)
            try:
                status = sensor.get_status(self.reactor.monotonic())
                readings.append(float(status.get('humidity', 0.0)))
            except Exception as e:
                self.logger.warning(
                    'vivid_dryer: humidity read error on sensor %s: %s', name, e)
        return (sum(readings) / len(readings)) if readings else None

    def _sensor_humidity(self, index):
        """Return humidity from a specific sensor by index, or 0.0."""
        if index >= len(self.sensors):
            return 0.0
        try:
            status = self.sensors[index].get_status(self.reactor.monotonic())
            return float(status.get('humidity', 0.0))
        except Exception:
            return 0.0

    def _compute_temp(self, current_humidity):
        """
        Proportional control formula (from spec):
          new_temp = TEMP_MIN + (TEMP_MAX - TEMP_MIN)
                     * (current_humidity - target_humidity)
                     / (target_humidity * 0.5)
        Clamped to [TEMP_MIN, TEMP_MAX].
        """
        target = self.target_humidity
        if target <= 0.:
            return self.temp_min
        scale = target * 0.5
        if scale <= 0.:
            return self.temp_max
        fraction = (current_humidity - target) / scale
        temp = self.temp_min + (self.temp_max - self.temp_min) * fraction
        return max(self.temp_min, min(self.temp_max, temp))

    def _set_temp(self, temp):
        """Set the heater target temperature via pheaters API."""
        if self.heater is None:
            return
        try:
            pheaters = self.printer.lookup_object('heaters')
            pheaters.set_temperature(self.heater, temp, wait=False)
        except Exception as e:
            self.logger.error('vivid_dryer: failed to set heater temp: %s', e)

    def _stop_cycle(self):
        self.reactor.update_timer(self.timer, self.reactor.NEVER)
        self._set_temp(0.)
        self.active = False
        self.mode = 'idle'
        self.target_temp = 0.
        self.target_humidity = 0.
        self.temp_min = 0.
        self.temp_max = 75.
        self.end_time = 0.
        self.max_end_time = 0.

    @staticmethod
    def _fmt_duration(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return '%02d:%02d:%02d' % (h, m, s)

    # ------------------------------------------------------------------
    # Moonraker / Mainsail status
    # ------------------------------------------------------------------

    def get_status(self, eventtime):
        now = self.reactor.monotonic()
        remaining = 0
        if self.active:
            if self.mode == 'timed' and self.end_time > 0.:
                remaining = max(0, int(self.end_time - now))
            elif self.mode == 'humidity' and self.max_end_time > 0.:
                remaining = max(0, int(self.max_end_time - now))

        avg = self._avg_humidity() or 0.0
        n = len(self.sensors)
        if n >= 2:
            left  = self._sensor_humidity(0)
            right = self._sensor_humidity(1)
        elif n == 1:
            left  = avg
            right = avg
        else:
            left  = 0.0
            right = 0.0

        return {
            'active': self.active,
            'mode': self.mode,
            'target_temp': self.target_temp,
            'current_humidity': avg,
            'current_humidity_left': left,
            'current_humidity_right': right,
            'target_humidity': self.target_humidity,
            'remaining_seconds': remaining,
            'temp_min': self.temp_min,
            'temp_max': self.temp_max,
        }


def load_config(config):
    return VividDryer(config)


def load_config_prefix(config):
    return VividDryer(config)
