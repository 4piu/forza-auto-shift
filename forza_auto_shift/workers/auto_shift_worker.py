"""Telemetry worker runtime separated from Qt widget construction."""

from __future__ import annotations

import ctypes
import os
import queue
import socket
import threading
import time
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal, Slot

from ..auto_transmission import (
    AdaptiveAutomaticTransmission,
    AutomaticTransmissionConfig,
    minimum_shift_curve_gap,
)
from ..input_controller import GearInputConfig, GearInputController
from ..telemetry import (
    PacketDecodeError,
    TelemetryListener,
    UnknownPacketSizeError,
    decode_packet,
    format_packet_summary,
)

PRINT_EVERY = 20
TELEMETRY_STALE_SECONDS = 1.5
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
LOG_LEVEL_ORDER = {
    "DEBUG": 10,
    "INFO": 20,
    "WARN": 30,
    "ERROR": 40,
}

PROCESS_NAME_TO_GAME_CODE = {
    "forzahorizon4.exe": "FH4",
    "forzahorizon5.exe": "FH5",
    "forzahorizon6.exe": "FH6",
    "forzamotorsport.exe": "FM",
    "forza_gaming.desktop.x64_release_final.exe": "FM",
}

FORZA_PROCESS_NAMES = {
    "forzahorizon4.exe",
    "forzahorizon5.exe",
    "forzahorizon6.exe",
    "forzamotorsport.exe",
    "forza_gaming.desktop.x64_release_final.exe",
}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetForegroundWindow.argtypes = ()
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextW.argtypes = (
    wintypes.HWND,
    wintypes.LPWSTR,
    ctypes.c_int,
)
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = (
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
)
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = (
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD,
)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
)
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL


def detect_game_code_from_process_name(process_name: str | None) -> str:
    if not process_name:
        return ""
    return PROCESS_NAME_TO_GAME_CODE.get(process_name.lower(), "")


def _get_foreground_window_title_and_process() -> tuple[str, str | None]:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", None

    title_buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
    title = title_buffer.value

    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == 0:
        return title, None

    process_handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
    )
    if not process_handle:
        return title, None

    try:
        path_buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(path_buffer))
        success = kernel32.QueryFullProcessImageNameW(
            process_handle,
            0,
            path_buffer,
            ctypes.byref(size),
        )
        if not success:
            return title, None
        process_name = os.path.basename(path_buffer.value).lower()
        return title, process_name
    finally:
        kernel32.CloseHandle(process_handle)


class AutoShiftWorker(QObject):
    """Runs telemetry receive/decode/shift loop in a background thread."""

    log = Signal(str)
    status = Signal(str)
    car_detected = Signal(int, str)
    car_config_requested = Signal(str, int)
    finished = Signal()

    def __init__(
        self,
        bind_host: str,
        port: int,
        dry_run: bool,
        relay_enabled: bool,
        relay_targets: list[tuple[str, int]],
        require_focus_guard: bool,
        shift_down_scan_code: int,
        shift_up_scan_code: int,
        shift_down_key_name: str,
        shift_up_key_name: str,
        log_level: str = "INFO",
    ) -> None:
        super().__init__()
        self.bind_host = bind_host
        self.port = port
        self.dry_run = dry_run
        self.relay_enabled = relay_enabled
        self.relay_targets = relay_targets
        self.require_focus_guard = require_focus_guard
        self.shift_down_scan_code = shift_down_scan_code
        self.shift_up_scan_code = shift_up_scan_code
        self.shift_down_key_name = shift_down_key_name
        self.shift_up_key_name = shift_up_key_name
        self.at_config: AutomaticTransmissionConfig | None = None
        self._active_car_key = ""
        self._active_preset_name = ""
        self._active_car_autoshift_disabled = False
        self.log_level = log_level
        self._stop_event = threading.Event()
        self._listener: TelemetryListener | None = None
        self._at_controller: AdaptiveAutomaticTransmission | None = None
        self._run_state = "Idle"
        self._telemetry_state = "Waiting"
        self._focus_state = "N/A"
        self._game_state = "Unknown"
        self._last_shift_latency_ms: float | None = None
        self._relay_queue: queue.Queue[bytes | None] | None = None
        self._relay_thread: threading.Thread | None = None
        self._relay_socket: socket.socket | None = None
        self._relay_dropped_packets = 0
        self._config_update_queue: queue.Queue[
            tuple[AutomaticTransmissionConfig | None, str, str]
        ] = queue.Queue()
        self._latest_idle_rpm: float | None = None
        self._latest_max_rpm: float | None = None
        self._last_curve_warning_key = ""

    def _log(self, level: str, message: str) -> None:
        current_level = LOG_LEVEL_ORDER.get(self.log_level, LOG_LEVEL_ORDER["INFO"])
        message_level = LOG_LEVEL_ORDER.get(level, LOG_LEVEL_ORDER["INFO"])
        if message_level >= current_level:
            self.log.emit(f"[{level}] {message}")

    def _log_at_config(self, context: str) -> None:
        cfg = self.at_config
        if cfg is None:
            self._log("INFO", f"AT config ({context}): pending car preset")
            return
        self._log(
            "INFO",
            (
                f"AT config ({context}): up_points={len(cfg.upshift_curve)}, "
                f"down_points={len(cfg.downshift_curve)}, "
                f"min_gap={cfg.min_shift_rpm_gap:.0f}, "
                f"kick_thr={cfg.kickdown_throttle_threshold:.2f}, "
                f"kick_max={cfg.kickdown_max_rpm:.0f}, "
                f"cooldown={cfg.min_time_between_shifts:.2f}, "
                f"dwell={cfg.enable_per_gear_dwell}"
            ),
        )

    def _warn_if_curve_incompatible(self, preset_name: str, car_key: str) -> None:
        cfg = self.at_config
        if cfg is None:
            return
        idle_rpm = self._latest_idle_rpm
        max_rpm = self._latest_max_rpm
        if idle_rpm is None or max_rpm is None or max_rpm <= idle_rpm:
            return
        minimum_gap = minimum_shift_curve_gap(
            cfg.upshift_curve,
            cfg.downshift_curve,
            idle_rpm,
            max_rpm,
        )
        if minimum_gap >= cfg.min_shift_rpm_gap:
            self._last_curve_warning_key = ""
            return
        warning_key = (
            f"{car_key}:{preset_name}:{idle_rpm:.0f}:{max_rpm:.0f}:{minimum_gap:.0f}"
        )
        if warning_key == self._last_curve_warning_key:
            return
        self._last_curve_warning_key = warning_key
        self._log(
            "WARN",
            (
                f"Preset '{preset_name}' may be incompatible with {car_key}: "
                f"minimum curve gap is {minimum_gap:.0f} RPM "
                f"for idle={idle_rpm:.0f}, max={max_rpm:.0f}."
            ),
        )

    def _refresh_focus_state(self) -> None:
        _title, process_name = _get_foreground_window_title_and_process()
        detected_game = detect_game_code_from_process_name(process_name)
        next_game_state = detected_game or "Unknown"
        if self.dry_run or not self.require_focus_guard:
            next_state = "N/A"
        else:
            next_state = "Active" if process_name in FORZA_PROCESS_NAMES else "Blocked"

        if next_state != self._focus_state or next_game_state != self._game_state:
            self._focus_state = next_state
            self._game_state = next_game_state
            self._emit_status()

    def _emit_status(self) -> None:
        latency_text = (
            f"{self._last_shift_latency_ms:.1f} ms"
            if self._last_shift_latency_ms is not None
            else "N/A"
        )
        self.status.emit(
            f"{self._run_state} | Telemetry: {self._telemetry_state} | Focus: {self._focus_state} | Game: {self._game_state} | Latency: {latency_text}"
        )

    def _record_shift_latency(self, packet_received_at: float) -> None:
        if self.dry_run:
            return
        self._last_shift_latency_ms = max(
            0.0, (time.perf_counter() - packet_received_at) * 1000.0
        )
        self._emit_status()

    def _drain_config_updates(self) -> AdaptiveAutomaticTransmission | None:
        latest_update: tuple[AutomaticTransmissionConfig | None, str, str] | None = None
        while True:
            try:
                latest_update = self._config_update_queue.get_nowait()
            except queue.Empty:
                break

        if latest_update is None:
            return self._at_controller

        config, preset_name, car_key = latest_update
        if car_key and car_key != self._active_car_key:
            return self._at_controller

        if config is None:
            self.at_config = None
            self._active_preset_name = preset_name
            self._active_car_autoshift_disabled = True
            active_car_key = car_key or self._active_car_key
            self._log("INFO", f"Autoshift disabled for {active_car_key}.")
            return self._at_controller

        self.at_config = config
        self._active_preset_name = preset_name
        self._active_car_autoshift_disabled = False
        if self._at_controller is None:
            self._at_controller = AdaptiveAutomaticTransmission(config)
        else:
            self._at_controller.config = config
        if preset_name and (car_key or self._active_car_key):
            active_car_key = car_key or self._active_car_key
            self._log("INFO", f"Applied preset '{preset_name}' for {active_car_key}.")
            self._log_at_config(f"car-preset {active_car_key}")
            self._warn_if_curve_incompatible(preset_name, active_car_key)
        else:
            self._log("INFO", "Applied AT config update while running.")
            self._log_at_config("runtime-update")
        return self._at_controller

    def _start_relay_dispatcher(self) -> None:
        if not self.relay_enabled or not self.relay_targets:
            return

        self._relay_queue = queue.Queue(maxsize=4096)
        self._relay_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._relay_thread = threading.Thread(
            target=self._relay_dispatch_loop,
            name="udp-relay-dispatcher",
            daemon=True,
        )
        self._relay_thread.start()
        self._log(
            "INFO",
            f"UDP relay enabled for {len(self.relay_targets)} target(s).",
        )

    def _stop_relay_dispatcher(self) -> None:
        relay_queue = self._relay_queue
        relay_thread = self._relay_thread

        if relay_queue is not None:
            try:
                relay_queue.put_nowait(None)
            except queue.Full:
                pass

        if relay_thread is not None:
            relay_thread.join(timeout=0.25)

        if self._relay_socket is not None:
            self._relay_socket.close()

        if self._relay_dropped_packets > 0:
            self._log(
                "WARN",
                f"UDP relay dropped {self._relay_dropped_packets} packet(s) due to full relay queue.",
            )

        self._relay_queue = None
        self._relay_thread = None
        self._relay_socket = None
        self._relay_dropped_packets = 0

    def _enqueue_relay_packet(self, raw_data: bytes) -> None:
        relay_queue = self._relay_queue
        if relay_queue is None:
            return
        try:
            relay_queue.put_nowait(raw_data)
        except queue.Full:
            self._relay_dropped_packets += 1

    def _relay_dispatch_loop(self) -> None:
        relay_queue = self._relay_queue
        relay_socket = self._relay_socket
        if relay_queue is None or relay_socket is None:
            return

        while not self._stop_event.is_set():
            try:
                payload = relay_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            if payload is None:
                break

            for target in self.relay_targets:
                try:
                    relay_socket.sendto(payload, target)
                except OSError:
                    continue

    @Slot()
    def run(self) -> None:
        self._run_state = "Running"
        self._telemetry_state = "Waiting"
        self._focus_state = (
            "N/A" if self.dry_run or not self.require_focus_guard else "Unknown"
        )
        self._game_state = "Unknown"
        self._last_shift_latency_ms = None
        self._emit_status()
        self._refresh_focus_state()
        bind_text = self.bind_host if self.bind_host else "0.0.0.0"
        self._log("INFO", f"Listening for telemetry on UDP {bind_text}:{self.port}...")
        self._log("INFO", f"Input mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
        if not self.dry_run:
            self._log(
                "INFO",
                f"Window focus guard: {'ON' if self.require_focus_guard else 'OFF'}",
            )
        self._log(
            "INFO",
            f"Controls: {self.shift_down_key_name}=gear down, {self.shift_up_key_name}=gear up",
        )
        self._log_at_config("worker-start")
        self._log("INFO", "-" * 80)
        self._start_relay_dispatcher()

        self._at_controller = None
        input_controller = GearInputController(
            GearInputConfig(
                dry_run=self.dry_run,
                shift_down_scan_code=self.shift_down_scan_code,
                shift_up_scan_code=self.shift_up_scan_code,
            )
        )

        packet_count = 0
        last_packet_type = ""
        last_packet_time = time.monotonic()
        last_upshift_block_log_time = 0.0
        last_unknown_game_log_time = 0.0
        last_car_key = ""

        try:
            with TelemetryListener(
                bind_host=self.bind_host, port=self.port
            ) as listener:
                self._listener = listener
                listener.sock.settimeout(0.20)

                while not self._stop_event.is_set():
                    try:
                        raw_data, addr = listener.recv_raw()
                        packet_received_at = time.perf_counter()
                    except socket.timeout:
                        self._drain_config_updates()
                        self._refresh_focus_state()
                        now = time.monotonic()
                        if (
                            self._telemetry_state == "Connected"
                            and (now - last_packet_time) >= TELEMETRY_STALE_SECONDS
                        ):
                            self._telemetry_state = "Stale"
                            self._emit_status()
                        continue
                    except OSError:
                        break

                    self._refresh_focus_state()
                    self._enqueue_relay_packet(raw_data)

                    last_packet_time = time.monotonic()
                    if self._telemetry_state != "Connected":
                        self._telemetry_state = "Connected"
                        self._emit_status()

                    packet_count += 1

                    try:
                        packet = decode_packet(raw_data)
                        packet.source = addr
                    except UnknownPacketSizeError:
                        self._log(
                            "WARN",
                            f"[{packet_count}] Unknown packet size={len(raw_data)} from {addr}",
                        )
                        continue
                    except PacketDecodeError as exc:
                        self._log(
                            "WARN",
                            f"[{packet_count}] Decode error for size={len(raw_data)} from {addr}: {exc}",
                        )
                        continue

                    if packet.packet_type != last_packet_type:
                        self._log(
                            "INFO",
                            f"Detected packet format: {packet.packet_type} ({len(raw_data)} bytes)",
                        )
                        last_packet_type = packet.packet_type

                    if packet_count <= 5 or packet_count % PRINT_EVERY == 0:
                        self._log(
                            "DEBUG", f"[{packet_count}] {format_packet_summary(packet)}"
                        )

                    if not packet.is_race_on:
                        continue

                    self._latest_idle_rpm = float(
                        packet.values.get("EngineIdleRpm", packet.current_rpm)
                    )
                    self._latest_max_rpm = float(
                        packet.values.get("EngineMaxRpm", packet.current_rpm)
                    )

                    at = self._drain_config_updates()

                    raw_car_ordinal = packet.values.get("CarOrdinal")
                    if raw_car_ordinal is not None:
                        car_ordinal = int(raw_car_ordinal)
                        if car_ordinal > 0:
                            detected_game = detect_game_code_from_process_name(
                                _get_foreground_window_title_and_process()[1]
                            )
                            if not detected_game:
                                now = time.monotonic()
                                if now - last_unknown_game_log_time >= 2.0:
                                    self._log(
                                        "INFO",
                                        "Telemetry received, waiting for supported Forza game focus.",
                                    )
                                    last_unknown_game_log_time = now
                                last_car_key = ""
                                self._active_car_key = ""
                                self._active_preset_name = ""
                                self.at_config = None
                                continue

                            car_key = f"{detected_game}-{car_ordinal}"
                            if car_key != last_car_key:
                                last_car_key = car_key
                                self._active_car_key = car_key
                                self._active_preset_name = ""
                                self._active_car_autoshift_disabled = False
                                self.at_config = None
                                self._log(
                                    "INFO",
                                    f"Requesting AT config for {car_key}.",
                                )
                                self.car_config_requested.emit(
                                    detected_game,
                                    car_ordinal,
                                )
                                self.car_detected.emit(
                                    car_ordinal,
                                    detected_game,
                                )

                    if self.at_config is None:
                        at = self._drain_config_updates()
                        if self._active_car_autoshift_disabled:
                            continue
                        if at is None:
                            continue

                    if self._active_car_autoshift_disabled:
                        continue

                    if at is None:
                        continue

                    if self._active_preset_name and self._active_car_key:
                        self._warn_if_curve_incompatible(
                            self._active_preset_name,
                            self._active_car_key,
                        )

                    action = at.update(packet)
                    reason = at.last_decision_reason or "n/a"
                    if action == "upshift":
                        if self.require_focus_guard and not self.dry_run:
                            if self._focus_state != "Active":
                                self._log(
                                    "WARN",
                                    f"[{packet_count}] Shift blocked: Forza window not active",
                                )
                                continue
                        self._record_shift_latency(packet_received_at)
                        input_controller.shift_up()
                        self._log(
                            "INFO",
                            f"[{packet_count}] SHIFT UP | gear={packet.gear} rpm={packet.current_rpm:.0f} speed={packet.speed_kmh or 0.0:.1f} km/h | reason={reason}",
                        )
                    elif action == "downshift":
                        if self.require_focus_guard and not self.dry_run:
                            if self._focus_state != "Active":
                                self._log(
                                    "WARN",
                                    f"[{packet_count}] Shift blocked: Forza window not active",
                                )
                                continue
                        self._record_shift_latency(packet_received_at)
                        input_controller.shift_down()
                        self._log(
                            "INFO",
                            f"[{packet_count}] SHIFT DOWN | gear={packet.gear} rpm={packet.current_rpm:.0f} speed={packet.speed_kmh or 0.0:.1f} km/h | reason={reason}",
                        )
                    elif at.last_upshift_block_reason:
                        now = time.monotonic()
                        if now - last_upshift_block_log_time >= 0.80:
                            target = at.last_upshift_target_rpm
                            rpm = packet.current_rpm
                            if target > 0.0 and rpm >= (target + 100.0):
                                self._log(
                                    "WARN",
                                    f"[{packet_count}] UPSHIFT BLOCKED | gear={packet.gear} rpm={rpm:.0f} target={target:.0f} speed={packet.speed_kmh or 0.0:.1f} km/h | reason={at.last_upshift_block_reason}",
                                )
                                last_upshift_block_log_time = now
        except OSError as exc:
            self._log("ERROR", f"Listener error: {exc}")
        finally:
            self._stop_relay_dispatcher()
            self._listener = None
            self._at_controller = None
            self._run_state = "Stopped"
            self._telemetry_state = "Stopped"
            self._emit_status()
            self.finished.emit()

    @Slot(object, str, str)
    def update_at_config(
        self, config: object, preset_name: str = "", car_key: str = ""
    ) -> None:
        if config is not None and not isinstance(config, AutomaticTransmissionConfig):
            return
        self._config_update_queue.put((config, preset_name, car_key))

    def stop(self) -> None:
        self._stop_event.set()
        if self._listener is not None:
            self._listener.close()
