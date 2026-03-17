"""Simple test harness for the reusable Forza telemetry component."""

from forza_auto_shift.telemetry import (
    DEFAULT_TELEMETRY_PORT,
    PacketDecodeError,
    TelemetryListener,
    UnknownPacketSizeError,
    decode_packet,
    format_packet_summary,
)

PRINT_EVERY = 20


def listen_for_telemetry() -> None:
    print(f"Listening for telemetry on UDP {DEFAULT_TELEMETRY_PORT}...")
    print("Enable Data Out in Forza and choose Sled or Dash format.")
    print(
        "Known packet sizes: Sled-FH=224, Sled-FM=232, Dash-Classic=311, Dash-FH4(raw)=324"
    )
    print("-" * 80)

    packet_count = 0
    last_packet_type = ""

    with TelemetryListener() as listener:
        try:
            while True:
                raw_data, addr = listener.recv_raw()
                packet_count += 1

                try:
                    packet = decode_packet(raw_data)
                    packet.source = addr
                except UnknownPacketSizeError:
                    print(
                        f"[{packet_count}] Unknown packet size={len(raw_data)} from {addr}; "
                        f"raw(32B)={raw_data[:32].hex()}"
                    )
                    continue
                except PacketDecodeError as exc:
                    print(
                        f"[{packet_count}] Decode error for size={len(raw_data)} from {addr}: {exc}; "
                        f"raw(32B)={raw_data[:32].hex()}"
                    )
                    continue

                if packet.packet_type != last_packet_type:
                    print(
                        f"Detected packet format: {packet.packet_type} ({len(raw_data)} bytes)"
                    )
                    last_packet_type = packet.packet_type

                if packet_count <= 5 or packet_count % PRINT_EVERY == 0:
                    print(f"[{packet_count}] {format_packet_summary(packet)}")
        except KeyboardInterrupt:
            print("\nStopping telemetry listener.")


if __name__ == "__main__":
    listen_for_telemetry()
