"""Offline simulation harness for adaptive AT logic (no game required)."""

from __future__ import annotations

from dataclasses import dataclass

from forza_auto_shift.auto_transmission import (
    AdaptiveAutomaticTransmission,
    AutomaticTransmissionConfig,
)
from forza_auto_shift.telemetry import TelemetryPacket


@dataclass(slots=True)
class SimConfig:
    duration_s: float = 85.0
    step_s: float = 0.1
    max_gear: int = 8


def throttle_profile(t: float) -> float:
    if t < 12.0:
        return 0.30
    if t < 24.0:
        return 0.55
    if t < 31.0:
        return 0.95
    if t < 44.0:
        return 0.45
    if t < 53.0:
        return 0.10
    if t < 66.0:
        return 0.00
    if t < 72.0:
        return 1.00
    return 0.40


def brake_profile(t: float) -> float:
    if 53.0 <= t < 66.0:
        return 0.90
    return 0.0


def make_packet(
    speed_mps: float, rpm: float, gear: int, throttle: float, brake: float
) -> TelemetryPacket:
    values: dict[str, int | float] = {
        "IsRaceOn": 1,
        "CurrentEngineRpm": rpm,
        "Gear": gear,
        "Accel": int(max(0.0, min(1.0, throttle)) * 255),
        "Brake": int(max(0.0, min(1.0, brake)) * 255),
        "Speed": speed_mps,
    }
    return TelemetryPacket(packet_type="Dash-Sim", values=values, raw_data=b"")


def rpm_from_speed_and_gear(speed_mps: float, gear: int, throttle: float) -> float:
    ratios = [3.8, 2.2, 1.5, 1.15, 0.95, 0.80, 0.68, 0.58]
    ratio = ratios[max(1, min(len(ratios), gear)) - 1]
    rpm = 850.0 + (speed_mps * ratio * 62.0) + (throttle * 900.0)
    return max(750.0, min(8200.0, rpm))


def run_simulation() -> None:
    sim = SimConfig()

    at = AdaptiveAutomaticTransmission(
        AutomaticTransmissionConfig(
            max_forward_gear=sim.max_gear,
            upshift_rpm_low_throttle=2800.0,
            upshift_rpm_high_throttle=7100.0,
            downshift_rpm_low_throttle=1150.0,
            downshift_rpm_high_throttle=3700.0,
            min_throttle_for_upshift=0.06,
            min_throttle_for_downshift=0.14,
            brake_downshift_threshold=0.08,
            kickdown_throttle_threshold=0.90,
            kickdown_max_rpm=5400.0,
            min_time_between_shifts=0.30,
            pending_shift_timeout=0.65,
            enable_per_gear_dwell=True,
            dwell_after_upshift_s=0.45,
            dwell_after_downshift_s=0.60,
            dwell_after_kickdown_s=0.80,
            per_gear_dwell_overrides={1: 1.00, 2: 0.85, 3: 0.65},
        )
    )

    speed_mps = 0.0
    gear = 1
    upshift_count = 0
    downshift_count = 0

    print("Offline AT simulation started")
    print("time  speed  rpm   gear  thr  brk  action")
    print("-" * 64)

    steps = int(sim.duration_s / sim.step_s)
    for step in range(steps + 1):
        now = step * sim.step_s
        throttle = throttle_profile(now)
        brake = brake_profile(now)

        longitudinal_accel = (throttle * 4.8) - (brake * 8.5) - (0.02 * speed_mps)
        speed_mps = max(0.0, speed_mps + (longitudinal_accel * sim.step_s))

        rpm = rpm_from_speed_and_gear(speed_mps, gear, throttle)
        packet = make_packet(speed_mps, rpm, gear, throttle, brake)

        action = at.update(packet, now=now)
        if action == "upshift" and gear < sim.max_gear:
            gear += 1
            upshift_count += 1
        elif action == "downshift" and gear > 1:
            gear -= 1
            downshift_count += 1

        if action or step % int(1.0 / sim.step_s) == 0:
            kmh = speed_mps * 3.6
            print(
                f"{now:4.1f}  {kmh:5.1f}  {rpm:4.0f}   {gear:>2}   {throttle:0.2f}  {brake:0.2f}  {action or '-'}"
            )

    print("-" * 64)
    print(
        f"Finished: upshifts={upshift_count}, downshifts={downshift_count}, final_gear={gear}"
    )


if __name__ == "__main__":
    run_simulation()
