"""Forza Auto Shift package."""

from .auto_transmission import (
    AdaptiveAutomaticTransmission,
    AutomaticTransmissionConfig,
    SimpleAutomaticTransmission,
)
from .input_controller import GearInputConfig, GearInputController
from .telemetry import (
    DEFAULT_BUFFER_SIZE,
    DEFAULT_TELEMETRY_PORT,
    PacketDecodeError,
    TelemetryListener,
    TelemetryPacket,
    UnknownPacketSizeError,
    decode_packet,
    format_packet_summary,
)

__all__ = [
    "AdaptiveAutomaticTransmission",
    "AutomaticTransmissionConfig",
    "DEFAULT_BUFFER_SIZE",
    "DEFAULT_TELEMETRY_PORT",
    "GearInputConfig",
    "GearInputController",
    "PacketDecodeError",
    "SimpleAutomaticTransmission",
    "TelemetryListener",
    "TelemetryPacket",
    "UnknownPacketSizeError",
    "decode_packet",
    "format_packet_summary",
]
