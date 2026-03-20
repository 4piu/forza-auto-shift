"""Configurable automatic transmission decision logic for telemetry-driven shifting."""

from __future__ import annotations

from dataclasses import dataclass
import time

from .telemetry import TelemetryPacket


@dataclass(slots=True)
class AutomaticTransmissionConfig:
    upshift_rpm_low_throttle: float
    upshift_rpm_high_throttle: float
    downshift_rpm_low_throttle: float
    downshift_rpm_high_throttle: float
    min_time_between_shifts: float
    pending_shift_timeout: float
    min_forward_gear: int
    max_forward_gear: int
    min_speed_for_upshift_mps: float
    min_speed_for_downshift_mps: float
    min_throttle_for_upshift: float
    min_throttle_for_downshift: float
    brake_downshift_threshold: float
    coast_throttle_threshold: float
    coast_brake_threshold: float
    coast_downshift_idle_rpm_margin: float
    coast_downshift_max_speed_mps: float
    kickdown_throttle_threshold: float
    kickdown_tip_in_min_delta: float
    kickdown_max_rpm: float
    kickdown_lockout_after_upshift_s: float
    max_pedal_value: int
    throttle_smoothing_alpha: float
    enable_per_gear_dwell: bool
    dwell_after_upshift_s: float
    dwell_after_downshift_s: float
    dwell_after_kickdown_s: float
    per_gear_dwell_overrides: dict[int, float]
    enable_low_speed_recovery_downshift: bool
    low_speed_recovery_max_speed_mps: float
    low_speed_recovery_rpm_margin: float
    allow_upshift_from_neutral: bool
    allow_reverse_while_moving: bool
    enable_unload_upshift_guard: bool
    unload_suspension_threshold: float
    unload_guard_after_detect_s: float
    unload_min_throttle: float
    enable_slip_upshift_guard: bool
    slip_upshift_guard_threshold: float
    slip_guard_after_detect_s: float
    slip_guard_min_throttle: float


class AdaptiveAutomaticTransmission:
    """AT controller using throttle-based RPM maps, hysteresis, and kickdown."""

    def __init__(self, config: AutomaticTransmissionConfig) -> None:
        self.config = config
        self._last_shift_time = 0.0
        self._last_gear: int | None = None
        self._pending_shift = False
        self._pending_shift_started = 0.0
        self._smoothed_throttle = 0.0
        self._last_smoothed_throttle = 0.0
        self._last_shift_kind: str | None = None
        self.last_decision_reason: str = ""
        self.last_upshift_block_reason: str = ""
        self.last_upshift_target_rpm: float = 0.0
        self._upshift_lockout_until = 0.0
        self._upshift_lockout_source: str = ""
        self._unload_guard_condition_active = False
        self._slip_guard_condition_active = False

    def update(self, packet: TelemetryPacket, now: float | None = None) -> str | None:
        self.last_decision_reason = ""
        self.last_upshift_block_reason = ""
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
        throttle_delta = max(0.0, throttle - self._last_smoothed_throttle)
        self._last_smoothed_throttle = throttle
        brake = self._normalize_pedal(packet.brake)
        rpm = packet.current_rpm
        idle_rpm = float(packet.values.get("EngineIdleRpm", rpm))
        upshift_target_rpm = self._target_upshift_rpm(throttle)
        self.last_upshift_target_rpm = upshift_target_rpm

        self._update_unload_upshift_guard(packet=packet, throttle=throttle, now=now)
        self._update_slip_upshift_guard(packet=packet, throttle=throttle, now=now)

        if self._should_low_speed_recovery_downshift(
            gear=gear,
            speed=speed,
            rpm=rpm,
            idle_rpm=idle_rpm,
            brake=brake,
        ):
            self._mark_shift(now, shift_kind="recovery_downshift")
            recovery_rpm_limit = idle_rpm + self.config.low_speed_recovery_rpm_margin
            self.last_decision_reason = f"recovery(speed={speed*3.6:.1f}kmh,rpm={rpm:.0f},limit={recovery_rpm_limit:.0f},brk={brake:.2f})"
            return "downshift"

        if self._should_coast_downshift(
            gear=gear,
            speed=speed,
            rpm=rpm,
            idle_rpm=idle_rpm,
            throttle=throttle,
            brake=brake,
        ):
            self._mark_shift(now, shift_kind="downshift")
            coast_limit = idle_rpm + self.config.coast_downshift_idle_rpm_margin
            self.last_decision_reason = (
                f"coast_downshift(rpm={rpm:.0f}<=limit={coast_limit:.0f},"
                f"thr={throttle:.2f},brk={brake:.2f})"
            )
            return "downshift"

        if self._is_kickdown_locked_out(now):
            pass
        elif self._should_kickdown(
            gear=gear,
            rpm=rpm,
            speed=speed,
            throttle=throttle,
            throttle_delta=throttle_delta,
        ):
            self._mark_shift(now, shift_kind="kickdown")
            self.last_decision_reason = (
                "kickdown("
                f"thr={throttle:.2f}>={self.config.kickdown_throttle_threshold:.2f},"
                f"dthr={throttle_delta:.2f}>={self.config.kickdown_tip_in_min_delta:.2f},"
                f"rpm={rpm:.0f}<={self.config.kickdown_max_rpm:.0f}"
                ")"
            )
            return "downshift"

        if not self._is_upshift_locked_out(now):
            if self._should_upshift(
                gear=gear, rpm=rpm, speed=speed, throttle=throttle, brake=brake
            ):
                self._mark_shift(now, shift_kind="upshift")
                self.last_decision_reason = f"upshift(rpm={rpm:.0f}>={upshift_target_rpm:.0f},thr={throttle:.2f})"
                return "upshift"

        if self._should_downshift(
            gear=gear,
            rpm=rpm,
            speed=speed,
            throttle=throttle,
            brake=brake,
        ):
            self._mark_shift(now, shift_kind="downshift")
            demand = max(throttle, brake)
            self.last_decision_reason = f"map_downshift(rpm={rpm:.0f}<={self._target_downshift_rpm(demand):.0f},thr={throttle:.2f},brk={brake:.2f})"
            return "downshift"

        if rpm >= upshift_target_rpm and gear >= self.config.min_forward_gear:
            self.last_upshift_block_reason = self._build_upshift_block_reason(
                now=now,
                gear=gear,
                speed=speed,
                throttle=throttle,
                brake=brake,
                rpm=rpm,
                upshift_target_rpm=upshift_target_rpm,
            )

        return None

    def _build_upshift_block_reason(
        self,
        now: float,
        gear: int,
        speed: float,
        throttle: float,
        brake: float,
        rpm: float,
        upshift_target_rpm: float,
    ) -> str:
        if gear >= self.config.max_forward_gear:
            return f"at_max_gear(gear={gear},max={self.config.max_forward_gear})"
        if speed < self.config.min_speed_for_upshift_mps:
            return f"speed_low(speed={speed*3.6:.1f}kmh,min={self.config.min_speed_for_upshift_mps*3.6:.1f}kmh)"
        if throttle < self.config.min_throttle_for_upshift:
            return f"throttle_low(thr={throttle:.2f},min={self.config.min_throttle_for_upshift:.2f})"
        if brake >= self.config.brake_downshift_threshold:
            return f"brake_active(brk={brake:.2f},th={self.config.brake_downshift_threshold:.2f})"
        if self._pending_shift:
            remaining = max(
                0.0,
                self.config.pending_shift_timeout - (now - self._pending_shift_started),
            )
            return f"pending_shift(timeout_remain={remaining:.2f}s)"

        cooldown_remaining = max(
            0.0, self.config.min_time_between_shifts - (now - self._last_shift_time)
        )
        if cooldown_remaining > 0.0:
            return f"cooldown(remain={cooldown_remaining:.2f}s)"

        dwell_seconds = self._required_dwell_seconds(gear)
        dwell_remaining = max(0.0, dwell_seconds - (now - self._last_shift_time))
        if dwell_remaining > 0.0:
            return f"dwell(remain={dwell_remaining:.2f}s,last={self._last_shift_kind or 'n/a'})"

        if self._is_upshift_locked_out(now):
            lockout_remaining = max(0.0, self._upshift_lockout_until - now)
            source = self._upshift_lockout_source or "guard"
            return f"upshift_lockout(remain={lockout_remaining:.2f}s,source={source})"

        if rpm < upshift_target_rpm:
            return f"rpm_below_target(rpm={rpm:.0f},target={upshift_target_rpm:.0f})"

        return "unknown_blocker(check_telemetry_and_config)"

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

    def _is_kickdown_locked_out(self, now: float) -> bool:
        if self._last_shift_kind != "upshift":
            return False
        return (
            now - self._last_shift_time
        ) < self.config.kickdown_lockout_after_upshift_s

    def _is_upshift_locked_out(self, now: float) -> bool:
        return now < self._upshift_lockout_until

    def _update_unload_upshift_guard(
        self,
        packet: TelemetryPacket,
        throttle: float,
        now: float,
    ) -> None:
        if not self.config.enable_unload_upshift_guard:
            self._unload_guard_condition_active = False
            return

        unload_detected = (
            throttle >= self.config.unload_min_throttle
            and self._is_unloaded_from_suspension(packet)
        )
        if unload_detected and not self._unload_guard_condition_active:
            self._upshift_lockout_until = max(
                self._upshift_lockout_until,
                now + self.config.unload_guard_after_detect_s,
            )
            self._upshift_lockout_source = "unload"

        self._unload_guard_condition_active = unload_detected

    def _update_slip_upshift_guard(
        self,
        packet: TelemetryPacket,
        throttle: float,
        now: float,
    ) -> None:
        if not self.config.enable_slip_upshift_guard:
            self._slip_guard_condition_active = False
            return

        max_slip = self._max_driven_tire_slip(packet)
        slip_detected = (
            throttle >= self.config.slip_guard_min_throttle
            and max_slip >= self.config.slip_upshift_guard_threshold
        )
        if slip_detected and not self._slip_guard_condition_active:
            self._upshift_lockout_until = max(
                self._upshift_lockout_until,
                now + self.config.slip_guard_after_detect_s,
            )
            self._upshift_lockout_source = f"slip(max={max_slip:.2f},th={self.config.slip_upshift_guard_threshold:.2f})"

        self._slip_guard_condition_active = slip_detected

    def _is_unloaded_from_suspension(self, packet: TelemetryPacket) -> bool:
        values = packet.values
        fields = (
            "NormalizedSuspensionTravelFrontLeft",
            "NormalizedSuspensionTravelFrontRight",
            "NormalizedSuspensionTravelRearLeft",
            "NormalizedSuspensionTravelRearRight",
        )
        suspension_values: list[float] = []
        for field_name in fields:
            raw = values.get(field_name)
            if raw is None:
                return False
            suspension_values.append(float(raw))

        max_travel = max(suspension_values)
        return max_travel <= self.config.unload_suspension_threshold

    def _max_driven_tire_slip(self, packet: TelemetryPacket) -> float:
        values = packet.values

        front_fields = ("TireSlipRatioFrontLeft", "TireSlipRatioFrontRight")
        rear_fields = ("TireSlipRatioRearLeft", "TireSlipRatioRearRight")

        drivetrain = int(values.get("DrivetrainType", 2))
        if drivetrain == 0:
            fields = front_fields
        elif drivetrain == 1:
            fields = rear_fields
        else:
            fields = front_fields + rear_fields

        max_abs_slip = 0.0
        for field_name in fields:
            raw = values.get(field_name)
            if raw is None:
                continue
            slip = abs(float(raw))
            if slip > max_abs_slip:
                max_abs_slip = slip

        return max_abs_slip

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
        self,
        gear: int,
        rpm: float,
        speed: float,
        throttle: float,
        throttle_delta: float,
    ) -> bool:
        return (
            gear > self.config.min_forward_gear
            and speed >= self.config.min_speed_for_downshift_mps
            and throttle >= self.config.kickdown_throttle_threshold
            and throttle_delta >= self.config.kickdown_tip_in_min_delta
            and rpm <= self.config.kickdown_max_rpm
        )

    def _should_low_speed_recovery_downshift(
        self,
        gear: int,
        speed: float,
        rpm: float,
        idle_rpm: float,
        brake: float,
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

    def _should_coast_downshift(
        self,
        gear: int,
        speed: float,
        rpm: float,
        idle_rpm: float,
        throttle: float,
        brake: float,
    ) -> bool:
        if gear <= self.config.min_forward_gear:
            return False
        if speed < self.config.min_speed_for_downshift_mps:
            return False
        if speed > self.config.coast_downshift_max_speed_mps:
            return False
        if throttle > self.config.coast_throttle_threshold:
            return False
        if brake > self.config.coast_brake_threshold:
            return False
        coast_limit = idle_rpm + self.config.coast_downshift_idle_rpm_margin
        return rpm <= coast_limit

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

        # Use the stronger of throttle or brake demand so braking raises
        # downshift target RPM and engine braking feels more natural.
        downshift_demand = max(throttle, brake)
        downshift_rpm = self._target_downshift_rpm(downshift_demand)
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
