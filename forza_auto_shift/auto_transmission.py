"""Configurable automatic transmission decision logic for telemetry-driven shifting."""

from __future__ import annotations

from dataclasses import dataclass, field
import time

from .telemetry import TelemetryPacket


@dataclass(slots=True)
class AutomaticTransmissionConfig:
    upshift_rpm_low_throttle: float = 2800.0
    upshift_rpm_high_throttle: float = 7000.0
    downshift_rpm_low_throttle: float = 1100.0
    downshift_rpm_high_throttle: float = 3600.0
    min_time_between_shifts: float = 0.35
    pending_shift_timeout: float = 0.75
    min_forward_gear: int = 1
    max_forward_gear: int = 10
    min_speed_for_upshift_mps: float = 1.0
    min_speed_for_downshift_mps: float = 4.0
    min_throttle_for_upshift: float = 0.08
    min_throttle_for_downshift: float = 0.15
    brake_downshift_threshold: float = 0.08
    kickdown_throttle_threshold: float = 0.88
    kickdown_max_rpm: float = 5200.0
    max_pedal_value: int = 255
    throttle_smoothing_alpha: float = 0.30
    enable_per_gear_dwell: bool = False
    dwell_after_upshift_s: float = 0.0
    dwell_after_downshift_s: float = 0.0
    dwell_after_kickdown_s: float = 0.0
    per_gear_dwell_overrides: dict[int, float] = field(default_factory=dict)
    enable_low_speed_recovery_downshift: bool = True
    low_speed_recovery_max_speed_mps: float = 1.0
    low_speed_recovery_rpm_margin: float = 150.0
    allow_upshift_from_neutral: bool = False
    allow_reverse_while_moving: bool = False


class AdaptiveAutomaticTransmission:
    """AT controller using throttle-based RPM maps, hysteresis, and kickdown."""

    def __init__(self, config: AutomaticTransmissionConfig | None = None) -> None:
        self.config = config or AutomaticTransmissionConfig()
        self._last_shift_time = 0.0
        self._last_gear: int | None = None
        self._pending_shift = False
        self._pending_shift_started = 0.0
        self._smoothed_throttle = 0.0
        self._last_shift_kind: str | None = None

    def update(self, packet: TelemetryPacket, now: float | None = None) -> str | None:
        if now is None:
            now = time.monotonic()

        gear = packet.gear

        if gear is None:
            return None

        if self._last_gear is not None and gear != self._last_gear:
            self._pending_shift = False

        self._last_gear = gear

        if not packet.packet_type.startswith("Dash"):
            return None

        if not packet.is_race_on:
            self._pending_shift = False
            return None

        if self._pending_shift:
            if now - self._pending_shift_started < self.config.pending_shift_timeout:
                return None
            self._pending_shift = False

        if now - self._last_shift_time < self.config.min_time_between_shifts:
            return None

        dwell_seconds = self._required_dwell_seconds(gear)
        if dwell_seconds > 0.0 and now - self._last_shift_time < dwell_seconds:
            return None

        speed = packet.speed_mps or 0.0
        throttle = self._update_smoothed_throttle(packet.accel)
        brake = self._normalize_pedal(packet.brake)
        rpm = packet.current_rpm
        idle_rpm = float(packet.values.get("EngineIdleRpm", rpm))

        if self._should_low_speed_recovery_downshift(
            gear=gear,
            speed=speed,
            rpm=rpm,
            idle_rpm=idle_rpm,
        ):
            self._mark_shift(now, shift_kind="recovery_downshift")
            return "downshift"

        if self._should_kickdown(gear=gear, rpm=rpm, speed=speed, throttle=throttle):
            self._mark_shift(now, shift_kind="kickdown")
            return "downshift"

        if self._should_upshift(
            gear=gear, rpm=rpm, speed=speed, throttle=throttle, brake=brake
        ):
            self._mark_shift(now, shift_kind="upshift")
            return "upshift"

        if self._should_downshift(
            gear=gear,
            rpm=rpm,
            speed=speed,
            throttle=throttle,
            brake=brake,
        ):
            self._mark_shift(now, shift_kind="downshift")
            return "downshift"

        return None

    def _mark_shift(self, now: float, shift_kind: str) -> None:
        self._last_shift_time = now
        self._pending_shift = True
        self._pending_shift_started = now
        self._last_shift_kind = shift_kind

    def _required_dwell_seconds(self, current_gear: int) -> float:
        if not self.config.enable_per_gear_dwell:
            return 0.0

        if self._last_shift_kind == "upshift":
            base_dwell = self.config.dwell_after_upshift_s
        elif self._last_shift_kind == "kickdown":
            base_dwell = self.config.dwell_after_kickdown_s
        elif self._last_shift_kind == "downshift":
            base_dwell = self.config.dwell_after_downshift_s
        elif self._last_shift_kind == "recovery_downshift":
            base_dwell = self.config.dwell_after_downshift_s
        else:
            base_dwell = 0.0

        gear_override = self.config.per_gear_dwell_overrides.get(current_gear, 0.0)
        return max(base_dwell, gear_override)

    def _normalize_pedal(self, raw: int | None) -> float:
        if raw is None:
            return 0.0
        max_value = max(1, self.config.max_pedal_value)
        pedal = float(raw) / float(max_value)
        return max(0.0, min(1.0, pedal))

    def _update_smoothed_throttle(self, accel_raw: int | None) -> float:
        throttle = self._normalize_pedal(accel_raw)
        alpha = max(0.0, min(1.0, self.config.throttle_smoothing_alpha))
        self._smoothed_throttle = (alpha * throttle) + (
            (1.0 - alpha) * self._smoothed_throttle
        )
        return self._smoothed_throttle

    @staticmethod
    def _lerp(low: float, high: float, t: float) -> float:
        clamped_t = max(0.0, min(1.0, t))
        return low + (high - low) * clamped_t

    def _target_upshift_rpm(self, throttle: float) -> float:
        return self._lerp(
            self.config.upshift_rpm_low_throttle,
            self.config.upshift_rpm_high_throttle,
            throttle,
        )

    def _target_downshift_rpm(self, throttle: float) -> float:
        return self._lerp(
            self.config.downshift_rpm_low_throttle,
            self.config.downshift_rpm_high_throttle,
            throttle,
        )

    def _should_kickdown(
        self, gear: int, rpm: float, speed: float, throttle: float
    ) -> bool:
        return (
            gear > self.config.min_forward_gear
            and speed >= self.config.min_speed_for_downshift_mps
            and throttle >= self.config.kickdown_throttle_threshold
            and rpm <= self.config.kickdown_max_rpm
        )

    def _should_low_speed_recovery_downshift(
        self,
        gear: int,
        speed: float,
        rpm: float,
        idle_rpm: float,
    ) -> bool:
        if not self.config.enable_low_speed_recovery_downshift:
            return False

        recovery_rpm_limit = idle_rpm + self.config.low_speed_recovery_rpm_margin
        return (
            gear > self.config.min_forward_gear
            and speed <= self.config.low_speed_recovery_max_speed_mps
            and rpm <= recovery_rpm_limit
        )

    def _should_upshift(
        self,
        gear: int,
        rpm: float,
        speed: float,
        throttle: float,
        brake: float,
    ) -> bool:
        if gear <= 0:
            return False

        upshift_rpm = self._target_upshift_rpm(throttle)
        return (
            gear >= self.config.min_forward_gear
            and gear < self.config.max_forward_gear
            and speed >= self.config.min_speed_for_upshift_mps
            and throttle >= self.config.min_throttle_for_upshift
            and brake < self.config.brake_downshift_threshold
            and rpm >= upshift_rpm
        )

    def _should_downshift(
        self,
        gear: int,
        rpm: float,
        speed: float,
        throttle: float,
        brake: float,
    ) -> bool:
        if gear <= 1:
            return False

        downshift_rpm = self._target_downshift_rpm(throttle)
        return (
            gear > self.config.min_forward_gear
            and speed >= self.config.min_speed_for_downshift_mps
            and rpm <= downshift_rpm
            and (
                throttle >= self.config.min_throttle_for_downshift
                or brake >= self.config.brake_downshift_threshold
            )
        )


SimpleAutomaticTransmission = AdaptiveAutomaticTransmission
