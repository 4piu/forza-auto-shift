"""Reusable Forza telemetry decoding and UDP listening utilities."""

from __future__ import annotations

from dataclasses import dataclass
import socket
import struct
from typing import Iterator

DEFAULT_TELEMETRY_PORT = 36792
DEFAULT_BUFFER_SIZE = 1024

SLED_SIZE_FH = 224
SLED_SIZE_FM = 232
DASH_SIZE_CLASSIC = 311
DASH_SIZE_FH4_RAW = 324
DASH_SIZE_FM_RAW = 331

# (field_name, struct_format)
SLED_FIELDS_FM = [
    ("IsRaceOn", "i"),
    ("TimestampMS", "I"),
    ("EngineMaxRpm", "f"),
    ("EngineIdleRpm", "f"),
    ("CurrentEngineRpm", "f"),
    ("AccelerationX", "f"),
    ("AccelerationY", "f"),
    ("AccelerationZ", "f"),
    ("VelocityX", "f"),
    ("VelocityY", "f"),
    ("VelocityZ", "f"),
    ("AngularVelocityX", "f"),
    ("AngularVelocityY", "f"),
    ("AngularVelocityZ", "f"),
    ("Yaw", "f"),
    ("Pitch", "f"),
    ("Roll", "f"),
    ("NormalizedSuspensionTravelFrontLeft", "f"),
    ("NormalizedSuspensionTravelFrontRight", "f"),
    ("NormalizedSuspensionTravelRearLeft", "f"),
    ("NormalizedSuspensionTravelRearRight", "f"),
    ("TireSlipRatioFrontLeft", "f"),
    ("TireSlipRatioFrontRight", "f"),
    ("TireSlipRatioRearLeft", "f"),
    ("TireSlipRatioRearRight", "f"),
    ("WheelRotationSpeedFrontLeft", "f"),
    ("WheelRotationSpeedFrontRight", "f"),
    ("WheelRotationSpeedRearLeft", "f"),
    ("WheelRotationSpeedRearRight", "f"),
    ("WheelOnRumbleStripFrontLeft", "i"),
    ("WheelOnRumbleStripFrontRight", "i"),
    ("WheelOnRumbleStripRearLeft", "i"),
    ("WheelOnRumbleStripRearRight", "i"),
    ("WheelInPuddleDepthFrontLeft", "f"),
    ("WheelInPuddleDepthFrontRight", "f"),
    ("WheelInPuddleDepthRearLeft", "f"),
    ("WheelInPuddleDepthRearRight", "f"),
    ("SurfaceRumbleFrontLeft", "f"),
    ("SurfaceRumbleFrontRight", "f"),
    ("SurfaceRumbleRearLeft", "f"),
    ("SurfaceRumbleRearRight", "f"),
    ("TireSlipAngleFrontLeft", "f"),
    ("TireSlipAngleFrontRight", "f"),
    ("TireSlipAngleRearLeft", "f"),
    ("TireSlipAngleRearRight", "f"),
    ("TireCombinedSlipFrontLeft", "f"),
    ("TireCombinedSlipFrontRight", "f"),
    ("TireCombinedSlipRearLeft", "f"),
    ("TireCombinedSlipRearRight", "f"),
    ("SuspensionTravelMetersFrontLeft", "f"),
    ("SuspensionTravelMetersFrontRight", "f"),
    ("SuspensionTravelMetersRearLeft", "f"),
    ("SuspensionTravelMetersRearRight", "f"),
    ("CarOrdinal", "i"),
    ("CarClass", "i"),
    ("CarPerformanceIndex", "i"),
    ("DrivetrainType", "i"),
    ("NumCylinders", "i"),
]

DASH_FIELDS_CLASSIC_EXTRA = [
    ("PositionX", "f"),
    ("PositionY", "f"),
    ("PositionZ", "f"),
    ("Speed", "f"),
    ("Power", "f"),
    ("Torque", "f"),
    ("TireTempFrontLeft", "f"),
    ("TireTempFrontRight", "f"),
    ("TireTempRearLeft", "f"),
    ("TireTempRearRight", "f"),
    ("Boost", "f"),
    ("Fuel", "f"),
    ("DistanceTraveled", "f"),
    ("BestLap", "f"),
    ("LastLap", "f"),
    ("CurrentLap", "f"),
    ("CurrentRaceTime", "f"),
    ("LapNumber", "H"),
    ("RacePosition", "B"),
    ("Accel", "B"),
    ("Brake", "B"),
    ("Clutch", "B"),
    ("HandBrake", "B"),
    ("Gear", "B"),
    ("Steer", "b"),
    ("NormalizedDrivingLine", "b"),
    ("NormalizedAIBrakeDifference", "b"),
]

DASH_FIELDS_CLASSIC = SLED_FIELDS_FM + DASH_FIELDS_CLASSIC_EXTRA


class UnknownPacketSizeError(ValueError):
    """Packet size does not match any known format variant."""


class PacketDecodeError(ValueError):
    """Packet size matches a known format but field decoding failed."""


@dataclass(slots=True)
class TelemetryPacket:
    """Decoded telemetry packet."""

    packet_type: str
    values: dict[str, int | float]
    raw_data: bytes
    source: tuple[str, int] | None = None

    @property
    def is_race_on(self) -> int:
        return int(self.values["IsRaceOn"])

    @property
    def current_rpm(self) -> float:
        return float(self.values["CurrentEngineRpm"])

    @property
    def idle_rpm(self) -> float:
        return float(self.values["EngineIdleRpm"])

    @property
    def max_rpm(self) -> float:
        return float(self.values["EngineMaxRpm"])

    @property
    def speed_mps(self) -> float | None:
        if "Speed" not in self.values:
            return None
        return float(self.values["Speed"])

    @property
    def speed_kmh(self) -> float | None:
        speed = self.speed_mps
        if speed is None:
            return None
        return speed * 3.6

    @property
    def gear(self) -> int | None:
        if "Gear" not in self.values:
            return None
        return int(self.values["Gear"])

    @property
    def accel(self) -> int | None:
        if "Accel" not in self.values:
            return None
        return int(self.values["Accel"])

    @property
    def brake(self) -> int | None:
        if "Brake" not in self.values:
            return None
        return int(self.values["Brake"])

    @property
    def velocity_magnitude(self) -> float:
        vx = float(self.values["VelocityX"])
        vy = float(self.values["VelocityY"])
        vz = float(self.values["VelocityZ"])
        return (vx * vx + vy * vy + vz * vz) ** 0.5

    @property
    def velocity_magnitude_kmh(self) -> float:
        return self.velocity_magnitude * 3.6


def decode_with_schema(
    data: bytes, schema: list[tuple[str, str]]
) -> dict[str, int | float]:
    values: dict[str, int | float] = {}
    offset = 0
    for field_name, field_type in schema:
        format_code = "<" + field_type
        field_size = struct.calcsize(format_code)
        try:
            value = struct.unpack_from(format_code, data, offset)[0]
        except struct.error as exc:
            raise ValueError(
                f"Failed to decode field '{field_name}' ({field_type}) at offset {offset}: {exc}"
            ) from exc
        offset += field_size
        values[field_name] = value
    return values


def decode_packet(data: bytes) -> TelemetryPacket:
    packet_size = len(data)

    if packet_size == DASH_SIZE_FH4_RAW:
        patched_data = data[:232] + data[244:323]
        try:
            values = decode_with_schema(patched_data, DASH_FIELDS_CLASSIC)
            return TelemetryPacket("Dash-FH4", values, data)
        except ValueError as exc:
            raise PacketDecodeError(f"Dash-FH4: {exc}") from exc

    candidates_by_size: dict[int, list[tuple[str, list[tuple[str, str]]]]] = {
        SLED_SIZE_FM: [("Sled-FM", SLED_FIELDS_FM)],
        DASH_SIZE_CLASSIC: [("Dash-Classic", DASH_FIELDS_CLASSIC)],
        # FM dash packet variant with additional trailing bytes.
        DASH_SIZE_FM_RAW: [("Dash-FM", DASH_FIELDS_CLASSIC)],
        SLED_SIZE_FH: [("Sled-FH", SLED_FIELDS_FM[:-2])],
    }

    candidates = candidates_by_size.get(packet_size)
    if not candidates:
        known_sizes = sorted(candidates_by_size.keys())
        raise UnknownPacketSizeError(
            f"Unknown packet size: {packet_size} (known sizes: {known_sizes})"
        )

    decode_errors: list[str] = []
    for packet_type, schema in candidates:
        try:
            values = decode_with_schema(data, schema)
            return TelemetryPacket(packet_type, values, data)
        except ValueError as exc:
            decode_errors.append(f"{packet_type}: {exc}")

    raise PacketDecodeError("; ".join(decode_errors))


class TelemetryListener:
    """UDP listener for Forza telemetry packets."""

    def __init__(
        self,
        port: int = DEFAULT_TELEMETRY_PORT,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
        bind_host: str = "",
    ) -> None:
        self.port = port
        self.buffer_size = buffer_size
        self.bind_host = bind_host
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.bind_host, self.port))

    def recv_raw(self) -> tuple[bytes, tuple[str, int]]:
        return self.sock.recvfrom(self.buffer_size)

    def recv_packet(self) -> TelemetryPacket:
        data, addr = self.recv_raw()
        packet = decode_packet(data)
        packet.source = addr
        return packet

    def iter_packets(self) -> Iterator[TelemetryPacket]:
        while True:
            yield self.recv_packet()

    def close(self) -> None:
        self.sock.close()

    def __enter__(self) -> "TelemetryListener":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def format_packet_summary(packet: TelemetryPacket) -> str:
    summary = (
        f"{packet.packet_type} | RaceOn={packet.is_race_on} "
        f"| RPM={packet.current_rpm:.0f} (idle {packet.idle_rpm:.0f} / max {packet.max_rpm:.0f})"
    )

    if packet.packet_type.startswith("Dash"):
        speed_mps = packet.speed_mps or 0.0
        speed_kmh = packet.speed_kmh or 0.0
        summary += (
            f" | Speed={speed_mps:.2f} m/s ({speed_kmh:.1f} km/h)"
            f" | VMag={packet.velocity_magnitude:.2f} m/s ({packet.velocity_magnitude_kmh:.1f} km/h)"
            f" | Gear={packet.gear} | Accel={packet.accel} | Brake={packet.brake}"
        )

    return summary
