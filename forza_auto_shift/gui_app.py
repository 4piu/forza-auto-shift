"""Minimal PySide6 GUI for the Forza Auto Shift core app."""

from __future__ import annotations

import socket
import sys
import threading
import time
import ctypes
import os
import json
from pathlib import Path

from pynput import keyboard

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .auto_transmission import (
    AdaptiveAutomaticTransmission,
    AutomaticTransmissionConfig,
)
from .input_controller import GearInputConfig, GearInputController, SC_E, SC_Q
from .telemetry import (
    DEFAULT_TELEMETRY_PORT,
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
MAPVK_VK_TO_VSC = 0
APP_STATE_FILE_NAME = "forza_auto_shift_state.json"
DEFAULT_CAR_PRESET_NAME = "street"

DEFAULT_AT_CONFIG_VALUES: dict[str, object] = {
    "upshift_rpm_low_throttle": 2800.0,
    "upshift_rpm_high_throttle": 7000.0,
    "downshift_rpm_low_throttle": 1100.0,
    "downshift_rpm_high_throttle": 3600.0,
    "min_time_between_shifts": 0.35,
    "pending_shift_timeout": 0.75,
    "min_forward_gear": 1,
    "max_forward_gear": 10,
    "min_speed_for_upshift_mps": 1.0,
    "min_speed_for_downshift_mps": 4.0,
    "min_throttle_for_upshift": 0.08,
    "min_throttle_for_downshift": 0.15,
    "brake_downshift_threshold": 0.08,
    "coast_throttle_threshold": 0.05,
    "coast_brake_threshold": 0.05,
    "coast_downshift_idle_rpm_margin": 320.0,
    "coast_downshift_max_speed_mps": 55.0,
    "kickdown_throttle_threshold": 0.88,
    "kickdown_tip_in_min_delta": 0.10,
    "kickdown_max_rpm": 5200.0,
    "kickdown_lockout_after_upshift_s": 1.10,
    "max_pedal_value": 255,
    "throttle_smoothing_alpha": 0.30,
    "enable_per_gear_dwell": False,
    "dwell_after_upshift_s": 0.0,
    "dwell_after_downshift_s": 0.0,
    "dwell_after_kickdown_s": 0.0,
    "per_gear_dwell_overrides": {},
    "enable_low_speed_recovery_downshift": True,
    "low_speed_recovery_max_speed_mps": 1.0,
    "low_speed_recovery_rpm_margin": 150.0,
    "allow_upshift_from_neutral": False,
    "allow_reverse_while_moving": False,
    "enable_unload_upshift_guard": True,
    "unload_suspension_threshold": 0.12,
    "unload_guard_after_detect_s": 0.35,
    "unload_min_throttle": 0.45,
    "enable_slip_upshift_guard": True,
    "slip_upshift_guard_threshold": 0.28,
    "slip_guard_after_detect_s": 0.30,
    "slip_guard_min_throttle": 0.45,
}

PROCESS_NAME_TO_GAME_CODE = {
    "forzahorizon4.exe": "FH4",
    "forzahorizon5.exe": "FH5",
    "forzamotorsport.exe": "FM",
    "forza_gaming.desktop.x64_release_final.exe": "FM",
}


def detect_game_code_from_process_name(process_name: str | None) -> str:
    if not process_name:
        return ""
    return PROCESS_NAME_TO_GAME_CODE.get(process_name.lower(), "")


SPECIAL_KEY_VK_MAP = {
    keyboard.Key.space: 0x20,
    keyboard.Key.enter: 0x0D,
    keyboard.Key.tab: 0x09,
    keyboard.Key.backspace: 0x08,
    keyboard.Key.esc: 0x1B,
    keyboard.Key.up: 0x26,
    keyboard.Key.down: 0x28,
    keyboard.Key.left: 0x25,
    keyboard.Key.right: 0x27,
    keyboard.Key.home: 0x24,
    keyboard.Key.end: 0x23,
    keyboard.Key.page_up: 0x21,
    keyboard.Key.page_down: 0x22,
    keyboard.Key.insert: 0x2D,
    keyboard.Key.delete: 0x2E,
    keyboard.Key.f1: 0x70,
    keyboard.Key.f2: 0x71,
    keyboard.Key.f3: 0x72,
    keyboard.Key.f4: 0x73,
    keyboard.Key.f5: 0x74,
    keyboard.Key.f6: 0x75,
    keyboard.Key.f7: 0x76,
    keyboard.Key.f8: 0x77,
    keyboard.Key.f9: 0x78,
    keyboard.Key.f10: 0x79,
    keyboard.Key.f11: 0x7A,
    keyboard.Key.f12: 0x7B,
    keyboard.Key.shift: 0x10,
    keyboard.Key.shift_l: 0xA0,
    keyboard.Key.shift_r: 0xA1,
    keyboard.Key.ctrl: 0x11,
    keyboard.Key.ctrl_l: 0xA2,
    keyboard.Key.ctrl_r: 0xA3,
    keyboard.Key.alt: 0x12,
    keyboard.Key.alt_l: 0xA4,
    keyboard.Key.alt_r: 0xA5,
}

# Default hotkey for toggle start/stop
DEFAULT_HOTKEY = keyboard.Key.f12


def _preset_with_defaults(overrides: dict[str, object]) -> dict[str, object]:
    merged: dict[str, object] = dict(DEFAULT_AT_CONFIG_VALUES)
    merged.update(overrides)
    return merged


BUILTIN_PRESET_TEMPLATES: dict[str, dict[str, object]] = {
    "street": {
        **_preset_with_defaults(
            {
                "upshift_rpm_low_throttle": 2200,
                "upshift_rpm_high_throttle": 6500,
                "downshift_rpm_low_throttle": 1050,
                "downshift_rpm_high_throttle": 2600,
                "min_time_between_shifts": 0.50,
                "enable_per_gear_dwell": True,
                "dwell_after_upshift_s": 0.35,
                "dwell_after_downshift_s": 0.60,
                "dwell_after_kickdown_s": 0.60,
                "kickdown_throttle_threshold": 0.96,
                "kickdown_max_rpm": 4000,
                "kickdown_lockout_after_upshift_s": 1.10,
                "enable_unload_upshift_guard": True,
                "unload_suspension_threshold": 0.12,
                "unload_guard_after_detect_s": 0.35,
                "unload_min_throttle": 0.45,
                "enable_slip_upshift_guard": True,
                "slip_upshift_guard_threshold": 0.28,
                "slip_guard_after_detect_s": 0.30,
                "slip_guard_min_throttle": 0.45,
            }
        )
    },
    "sports": {
        **_preset_with_defaults(
            {
                "upshift_rpm_low_throttle": 2900,
                "upshift_rpm_high_throttle": 7000,
                "downshift_rpm_low_throttle": 1200,
                "downshift_rpm_high_throttle": 3600,
                "min_time_between_shifts": 0.35,
                "enable_per_gear_dwell": True,
                "dwell_after_upshift_s": 0.25,
                "dwell_after_downshift_s": 0.35,
                "dwell_after_kickdown_s": 0.45,
                "kickdown_throttle_threshold": 0.88,
                "kickdown_max_rpm": 5400,
                "kickdown_lockout_after_upshift_s": 0.95,
                "enable_unload_upshift_guard": True,
                "unload_suspension_threshold": 0.10,
                "unload_guard_after_detect_s": 0.32,
                "unload_min_throttle": 0.40,
                "enable_slip_upshift_guard": True,
                "slip_upshift_guard_threshold": 0.24,
                "slip_guard_after_detect_s": 0.28,
                "slip_guard_min_throttle": 0.38,
            }
        )
    },
    "race": {
        **_preset_with_defaults(
            {
                "upshift_rpm_low_throttle": 3300,
                "upshift_rpm_high_throttle": 7600,
                "downshift_rpm_low_throttle": 1450,
                "downshift_rpm_high_throttle": 4300,
                "min_time_between_shifts": 0.28,
                "enable_per_gear_dwell": True,
                "dwell_after_upshift_s": 0.10,
                "dwell_after_downshift_s": 0.20,
                "dwell_after_kickdown_s": 0.25,
                "kickdown_throttle_threshold": 0.82,
                "kickdown_max_rpm": 6200,
                "kickdown_lockout_after_upshift_s": 0.70,
                "enable_unload_upshift_guard": True,
                "unload_suspension_threshold": 0.09,
                "unload_guard_after_detect_s": 0.28,
                "unload_min_throttle": 0.32,
                "enable_slip_upshift_guard": True,
                "slip_upshift_guard_threshold": 0.18,
                "slip_guard_after_detect_s": 0.22,
                "slip_guard_min_throttle": 0.28,
            }
        )
    },
}

FORZA_PROCESS_NAMES = {
    "forzahorizon4.exe",
    "forzahorizon5.exe",
    "forzamotorsport.exe",
    "forza_gaming.desktop.x64_release_final.exe",
}

FORZA_TITLE_KEYWORDS = (
    "forza horizon",
    "forza motorsport",
)


def _get_foreground_window_title_and_process() -> tuple[str, str | None]:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", None

    title_buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
    title = title_buffer.value

    pid = ctypes.c_ulong(0)
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
        size = ctypes.c_ulong(len(path_buffer))
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


def is_game_window_active() -> bool:
    title, process_name = _get_foreground_window_title_and_process()
    title_lower = title.lower()

    if "forza auto shift" in title_lower:
        return False

    if process_name in FORZA_PROCESS_NAMES:
        return True

    return any(keyword in title_lower for keyword in FORZA_TITLE_KEYWORDS)


class AutoShiftWorker(QObject):
    """Runs telemetry receive/decode/shift loop in a background thread."""

    log = Signal(str)
    status = Signal(str)
    car_detected = Signal(int, str)
    finished = Signal()

    def __init__(
        self,
        bind_host: str,
        port: int,
        dry_run: bool,
        require_focus_guard: bool,
        shift_down_scan_code: int,
        shift_up_scan_code: int,
        shift_down_key_name: str,
        shift_up_key_name: str,
        at_config: AutomaticTransmissionConfig,
        log_level: str,
    ) -> None:
        super().__init__()
        self.bind_host = bind_host
        self.port = port
        self.dry_run = dry_run
        self.require_focus_guard = require_focus_guard
        self.shift_down_scan_code = shift_down_scan_code
        self.shift_up_scan_code = shift_up_scan_code
        self.shift_down_key_name = shift_down_key_name
        self.shift_up_key_name = shift_up_key_name
        self.at_config = at_config
        self.log_level = log_level
        self._stop_event = threading.Event()
        self._listener: TelemetryListener | None = None
        self._at_controller: AdaptiveAutomaticTransmission | None = None
        self._run_state = "Idle"
        self._telemetry_state = "Waiting"
        self._focus_state = "N/A"
        self._game_state = "Unknown"

    def _log(self, level: str, message: str) -> None:
        current_level = LOG_LEVEL_ORDER.get(self.log_level, LOG_LEVEL_ORDER["INFO"])
        message_level = LOG_LEVEL_ORDER.get(level, LOG_LEVEL_ORDER["INFO"])
        if message_level >= current_level:
            self.log.emit(f"[{level}] {message}")

    def _log_at_config(self, context: str) -> None:
        cfg = self.at_config
        self._log(
            "INFO",
            (
                f"AT config ({context}): up_low={cfg.upshift_rpm_low_throttle:.0f}, "
                f"up_high={cfg.upshift_rpm_high_throttle:.0f}, "
                f"down_low={cfg.downshift_rpm_low_throttle:.0f}, "
                f"down_high={cfg.downshift_rpm_high_throttle:.0f}, "
                f"kick_thr={cfg.kickdown_throttle_threshold:.2f}, "
                f"kick_max={cfg.kickdown_max_rpm:.0f}, "
                f"cooldown={cfg.min_time_between_shifts:.2f}, "
                f"dwell={cfg.enable_per_gear_dwell}"
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
        self.status.emit(
            f"{self._run_state} | Telemetry: {self._telemetry_state} | Focus: {self._focus_state} | Game: {self._game_state}"
        )

    @Slot()
    def run(self) -> None:
        self._run_state = "Running"
        self._telemetry_state = "Waiting"
        self._focus_state = (
            "N/A" if self.dry_run or not self.require_focus_guard else "Unknown"
        )
        self._game_state = "Unknown"
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

        at = AdaptiveAutomaticTransmission(self.at_config)
        self._at_controller = at
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
        last_car_ordinal: int | None = None

        try:
            with TelemetryListener(
                bind_host=self.bind_host, port=self.port
            ) as listener:
                self._listener = listener
                listener.sock.settimeout(0.20)

                while not self._stop_event.is_set():
                    try:
                        raw_data, addr = listener.recv_raw()
                    except socket.timeout:
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

                    raw_car_ordinal = packet.values.get("CarOrdinal")
                    if raw_car_ordinal is not None:
                        car_ordinal = int(raw_car_ordinal)
                        if car_ordinal > 0 and car_ordinal != last_car_ordinal:
                            last_car_ordinal = car_ordinal
                            self.car_detected.emit(
                                car_ordinal,
                                detect_game_code_from_process_name(
                                    _get_foreground_window_title_and_process()[1]
                                ),
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
            self._listener = None
            self._at_controller = None
            self._run_state = "Stopped"
            self._telemetry_state = "Stopped"
            self._emit_status()
            self.finished.emit()

    @Slot(object)
    def update_at_config(self, config: object) -> None:
        if not isinstance(config, AutomaticTransmissionConfig):
            return
        self.at_config = config
        if self._at_controller is not None:
            self._at_controller.config = config
            self._log("INFO", "Applied AT config update while running.")
            self._log_at_config("runtime-update")

    def stop(self) -> None:
        self._stop_event.set()
        if self._listener is not None:
            self._listener.close()


class CollapsibleBox(QWidget):
    """A simple collapsible box with a clickable title and collapsible content."""

    def __init__(self, title: str, collapsed: bool = False) -> None:
        super().__init__()
        self.title = title
        self.is_collapsed = collapsed

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header button
        self.header_button = QPushButton(
            f"▼ {title}" if not collapsed else f"► {title}"
        )
        self.header_button.setFlat(True)
        self.header_button.setStyleSheet(
            "text-align: left; padding: 5px; font-weight: bold;"
        )
        self.header_button.clicked.connect(self.toggle)
        layout.addWidget(self.header_button)

        # Content area
        self.content_widget = QWidget()
        self.content_layout = QFormLayout(self.content_widget)
        layout.addWidget(self.content_widget)

        if collapsed:
            self.content_widget.hide()

    def toggle(self) -> None:
        self.is_collapsed = not self.is_collapsed
        if self.is_collapsed:
            self.content_widget.hide()
            self.header_button.setText(f"► {self.title}")
        else:
            self.content_widget.show()
            self.header_button.setText(f"▼ {self.title}")

    def addRow(self, label: str | QWidget, field: QWidget | None = None) -> None:
        if field is None:
            if isinstance(label, QWidget):
                self.content_layout.addRow(label)
            else:
                self.content_layout.addRow(label, QLabel(""))
        else:
            self.content_layout.addRow(label, field)


class FocusWheelSpinBox(QSpinBox):
    """Only reacts to wheel when focused to prevent accidental value changes."""

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        editor = self.lineEdit()
        if self.hasFocus() and editor is not None and editor.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class FocusWheelDoubleSpinBox(QDoubleSpinBox):
    """Only reacts to wheel when focused to prevent accidental value changes."""

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        editor = self.lineEdit()
        if self.hasFocus() and editor is not None and editor.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class MainWindow(QMainWindow):
    """Minimal GUI shell for starting/stopping the core app loop."""

    hotkey_pressed = Signal(object)
    hotkey_released = Signal(object)
    worker_config_update_requested = Signal(object)

    # Modifier keys to track
    MODIFIER_KEYS = {
        keyboard.Key.shift,
        keyboard.Key.shift_l,
        keyboard.Key.shift_r,
        keyboard.Key.ctrl,
        keyboard.Key.ctrl_l,
        keyboard.Key.ctrl_r,
        keyboard.Key.alt,
        keyboard.Key.alt_l,
        keyboard.Key.alt_r,
    }

    @staticmethod
    def _set_compact_numeric_input(widget: QSpinBox | QDoubleSpinBox) -> None:
        widget.setButtonSymbols(QAbstractSpinBox.NoButtons)
        widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        widget.setMaximumWidth(120)

    @staticmethod
    def _set_compact_form(form: QFormLayout) -> None:
        form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        form.setHorizontalSpacing(10)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Forza Auto Shift")
        self.resize(1200, 700)

        self._thread: QThread | None = None
        self._worker: AutoShiftWorker | None = None
        self._hotkey_listener: keyboard.Listener | None = None
        self._current_hotkey: frozenset | None = frozenset([DEFAULT_HOTKEY])
        self._recording_hotkey = False
        self._recording_shift_key_target: str | None = None
        self._currently_pressed_keys: set = set()
        self._hotkey_recording_pressed_keys: set = set()
        self._hotkey_trigger_latched = False
        self._shift_down_scan_code = SC_Q
        self._shift_up_scan_code = SC_E
        self._shift_down_key_name = "Q"
        self._shift_up_key_name = "E"
        self._preset_store: dict[str, dict[str, object]] = {}
        self._active_preset_name = ""
        self._default_binding_preset_name = ""
        self._car_preset_map: dict[str, str] = {}
        self._car_alias_map: dict[str, str] = {}
        self._car_binding_preset_combos: list[QComboBox] = []
        self._car_binding_alias_buttons: list[QPushButton] = []
        self._car_binding_remove_buttons: list[QPushButton] = []
        self._suppress_car_binding_updates = False
        self._suppress_preset_auto_apply = False
        self._state_file_path = Path.cwd() / APP_STATE_FILE_NAME

        root = QWidget(self)
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)

        # Tab widget for settings
        tabs = QTabWidget()

        # ===== CONNECTION TAB =====
        connection_widget = QWidget()
        connection_layout = QVBoxLayout(connection_widget)
        connection_group = QGroupBox("Connection")
        connection_form = QFormLayout(connection_group)
        self._set_compact_form(connection_form)
        self.listen_address_input = QLineEdit("0.0.0.0")
        self.listen_address_input.setMaximumWidth(180)
        connection_form.addRow("Listen address:", self.listen_address_input)
        self.port_input = FocusWheelSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(DEFAULT_TELEMETRY_PORT)
        self._set_compact_numeric_input(self.port_input)
        connection_form.addRow("UDP port:", self.port_input)
        connection_layout.addWidget(connection_group)
        connection_layout.addStretch()
        tabs.addTab(connection_widget, "Connection")

        # ===== INPUT TAB =====
        input_widget = QWidget()
        input_layout = QVBoxLayout(input_widget)
        input_group = QGroupBox("Input")
        input_form = QFormLayout(input_group)
        self._set_compact_form(input_form)
        self.dry_run_checkbox = QCheckBox("Dry Run (no key press)")
        self.dry_run_checkbox.setChecked(True)
        input_form.addRow(self.dry_run_checkbox)
        self.focus_guard_checkbox = QCheckBox("Require Forza window focus")
        self.focus_guard_checkbox.setChecked(True)
        input_form.addRow(self.focus_guard_checkbox)
        shift_down_row = QHBoxLayout()
        self.record_shift_down_button = QPushButton("Bind Down Key")
        self.record_shift_down_button.setFixedWidth(130)
        self.record_shift_down_button.clicked.connect(
            self._start_shift_down_key_recording
        )
        self.shift_down_key_label = QLabel(self._shift_down_key_name)
        self.shift_down_key_label.setMinimumWidth(70)
        shift_down_row.addWidget(self.shift_down_key_label)
        shift_down_row.addStretch(1)
        shift_down_row.addWidget(self.record_shift_down_button)
        input_form.addRow("Shift down key:", shift_down_row)
        shift_up_row = QHBoxLayout()
        self.record_shift_up_button = QPushButton("Bind Up Key")
        self.record_shift_up_button.setFixedWidth(130)
        self.record_shift_up_button.clicked.connect(self._start_shift_up_key_recording)
        self.shift_up_key_label = QLabel(self._shift_up_key_name)
        self.shift_up_key_label.setMinimumWidth(70)
        shift_up_row.addWidget(self.shift_up_key_label)
        shift_up_row.addStretch(1)
        shift_up_row.addWidget(self.record_shift_up_button)
        input_form.addRow("Shift up key:", shift_up_row)
        input_layout.addWidget(input_group)
        input_layout.addStretch()
        tabs.addTab(input_widget, "Input")

        # ===== TUNING TAB WITH PRESET EDITOR =====
        tuning_widget = QWidget()
        tuning_tab_layout = QHBoxLayout(tuning_widget)

        preset_editor_group = QGroupBox("Preset Editor")
        preset_editor_layout = QVBoxLayout(preset_editor_group)
        self.preset_list = QListWidget()
        self.preset_list.setMinimumWidth(220)
        self.preset_list.currentTextChanged.connect(self._on_preset_selected)
        preset_editor_layout.addWidget(self.preset_list)

        preset_editor_actions_top = QHBoxLayout()
        self.preset_create_button = QPushButton("Create")
        self.preset_create_button.clicked.connect(self._save_current_as_preset)
        preset_editor_actions_top.addWidget(self.preset_create_button)
        self.preset_duplicate_button = QPushButton("Duplicate")
        self.preset_duplicate_button.clicked.connect(self._duplicate_selected_preset)
        preset_editor_actions_top.addWidget(self.preset_duplicate_button)
        preset_editor_layout.addLayout(preset_editor_actions_top)

        preset_editor_actions_bottom = QHBoxLayout()
        self.preset_rename_button = QPushButton("Rename")
        self.preset_rename_button.clicked.connect(self._rename_selected_preset)
        preset_editor_actions_bottom.addWidget(self.preset_rename_button)
        self.preset_save_button = QPushButton("Save")
        self.preset_save_button.clicked.connect(self._save_selected_preset)
        preset_editor_actions_bottom.addWidget(self.preset_save_button)
        self.preset_delete_button = QPushButton("Delete")
        self.preset_delete_button.clicked.connect(self._delete_selected_preset)
        preset_editor_actions_bottom.addWidget(self.preset_delete_button)
        preset_editor_layout.addLayout(preset_editor_actions_bottom)

        tuning_tab_layout.addWidget(preset_editor_group)

        tuning_fields_widget = QWidget()
        tuning_layout = QVBoxLayout(tuning_fields_widget)

        # RPM Maps group
        rpm_group = CollapsibleBox("RPM Maps", collapsed=False)
        self._set_compact_form(rpm_group.content_layout)
        self.upshift_low_input = FocusWheelSpinBox()
        self.upshift_low_input.setRange(500, 12000)
        self.upshift_low_input.setValue(2800)
        self._set_compact_numeric_input(self.upshift_low_input)
        rpm_group.addRow("Upshift RPM (low throttle):", self.upshift_low_input)
        self.upshift_high_input = FocusWheelSpinBox()
        self.upshift_high_input.setRange(1000, 12000)
        self.upshift_high_input.setValue(7000)
        self._set_compact_numeric_input(self.upshift_high_input)
        rpm_group.addRow("Upshift RPM (high throttle):", self.upshift_high_input)
        self.downshift_low_input = FocusWheelSpinBox()
        self.downshift_low_input.setRange(500, 12000)
        self.downshift_low_input.setValue(1100)
        self._set_compact_numeric_input(self.downshift_low_input)
        rpm_group.addRow("Downshift RPM (low throttle):", self.downshift_low_input)
        self.downshift_high_input = FocusWheelSpinBox()
        self.downshift_high_input.setRange(500, 12000)
        self.downshift_high_input.setValue(3600)
        self._set_compact_numeric_input(self.downshift_high_input)
        rpm_group.addRow("Downshift RPM (high throttle):", self.downshift_high_input)
        tuning_layout.addWidget(rpm_group)

        # Cooldown & Basic Shift group
        shift_group = CollapsibleBox("Shift Cooldown & Basic Behavior", collapsed=False)
        self._set_compact_form(shift_group.content_layout)
        self.cooldown_input = FocusWheelDoubleSpinBox()
        self.cooldown_input.setRange(0.05, 2.00)
        self.cooldown_input.setSingleStep(0.05)
        self.cooldown_input.setValue(0.35)
        self._set_compact_numeric_input(self.cooldown_input)
        shift_group.addRow("Shift cooldown (s):", self.cooldown_input)
        self.enable_dwell_checkbox = QCheckBox("Enable dwell")
        self.enable_dwell_checkbox.setChecked(False)
        shift_group.addRow(self.enable_dwell_checkbox)
        tuning_layout.addWidget(shift_group)

        # Dwell Timing group
        dwell_group = CollapsibleBox("Dwell Timing", collapsed=True)
        self._set_compact_form(dwell_group.content_layout)
        self.dwell_up_input = FocusWheelDoubleSpinBox()
        self.dwell_up_input.setRange(0.0, 2.0)
        self.dwell_up_input.setSingleStep(0.05)
        self.dwell_up_input.setValue(0.0)
        self._set_compact_numeric_input(self.dwell_up_input)
        dwell_group.addRow("Dwell after upshift (s):", self.dwell_up_input)
        self.dwell_down_input = FocusWheelDoubleSpinBox()
        self.dwell_down_input.setRange(0.0, 2.0)
        self.dwell_down_input.setSingleStep(0.05)
        self.dwell_down_input.setValue(0.0)
        self._set_compact_numeric_input(self.dwell_down_input)
        dwell_group.addRow("Dwell after downshift (s):", self.dwell_down_input)
        self.dwell_kickdown_input = FocusWheelDoubleSpinBox()
        self.dwell_kickdown_input.setRange(0.0, 2.0)
        self.dwell_kickdown_input.setSingleStep(0.05)
        self.dwell_kickdown_input.setValue(0.0)
        self._set_compact_numeric_input(self.dwell_kickdown_input)
        dwell_group.addRow("Dwell after kickdown (s):", self.dwell_kickdown_input)
        tuning_layout.addWidget(dwell_group)

        # Kickdown Tuning group
        kickdown_group = CollapsibleBox("Kickdown Tuning", collapsed=True)
        self._set_compact_form(kickdown_group.content_layout)
        self.kickdown_threshold_input = FocusWheelDoubleSpinBox()
        self.kickdown_threshold_input.setRange(0.0, 1.0)
        self.kickdown_threshold_input.setSingleStep(0.01)
        self.kickdown_threshold_input.setValue(0.88)
        self._set_compact_numeric_input(self.kickdown_threshold_input)
        kickdown_group.addRow(
            "Kickdown throttle threshold:", self.kickdown_threshold_input
        )
        self.kickdown_max_rpm_input = FocusWheelSpinBox()
        self.kickdown_max_rpm_input.setRange(500, 12000)
        self.kickdown_max_rpm_input.setValue(5200)
        self._set_compact_numeric_input(self.kickdown_max_rpm_input)
        kickdown_group.addRow("Kickdown max RPM:", self.kickdown_max_rpm_input)
        self.kickdown_lockout_input = FocusWheelDoubleSpinBox()
        self.kickdown_lockout_input.setRange(0.0, 3.0)
        self.kickdown_lockout_input.setSingleStep(0.05)
        self.kickdown_lockout_input.setValue(1.10)
        self._set_compact_numeric_input(self.kickdown_lockout_input)
        kickdown_group.addRow(
            "Kickdown lockout after upshift (s):", self.kickdown_lockout_input
        )
        tuning_layout.addWidget(kickdown_group)

        # Unload Guard group
        unload_group = CollapsibleBox("Unload Upshift Guard", collapsed=True)
        self._set_compact_form(unload_group.content_layout)
        self.enable_unload_guard_checkbox = QCheckBox("Enable unload upshift guard")
        self.enable_unload_guard_checkbox.setChecked(True)
        unload_group.addRow(self.enable_unload_guard_checkbox)
        self.unload_threshold_input = FocusWheelDoubleSpinBox()
        self.unload_threshold_input.setRange(0.0, 1.0)
        self.unload_threshold_input.setSingleStep(0.01)
        self.unload_threshold_input.setValue(0.12)
        self._set_compact_numeric_input(self.unload_threshold_input)
        unload_group.addRow("Unload suspension threshold:", self.unload_threshold_input)
        self.unload_guard_duration_input = FocusWheelDoubleSpinBox()
        self.unload_guard_duration_input.setRange(0.0, 2.0)
        self.unload_guard_duration_input.setSingleStep(0.05)
        self.unload_guard_duration_input.setValue(0.35)
        self._set_compact_numeric_input(self.unload_guard_duration_input)
        unload_group.addRow(
            "Unload guard lockout duration (s):", self.unload_guard_duration_input
        )
        self.unload_min_throttle_input = FocusWheelDoubleSpinBox()
        self.unload_min_throttle_input.setRange(0.0, 1.0)
        self.unload_min_throttle_input.setSingleStep(0.01)
        self.unload_min_throttle_input.setValue(0.45)
        self._set_compact_numeric_input(self.unload_min_throttle_input)
        unload_group.addRow(
            "Unload guard min throttle:", self.unload_min_throttle_input
        )
        tuning_layout.addWidget(unload_group)

        # Slip Guard group
        slip_group = CollapsibleBox("Slip Upshift Guard", collapsed=True)
        self._set_compact_form(slip_group.content_layout)
        self.enable_slip_guard_checkbox = QCheckBox("Enable slip upshift guard")
        self.enable_slip_guard_checkbox.setChecked(True)
        slip_group.addRow(self.enable_slip_guard_checkbox)
        self.slip_threshold_input = FocusWheelDoubleSpinBox()
        self.slip_threshold_input.setRange(0.0, 2.0)
        self.slip_threshold_input.setSingleStep(0.01)
        self.slip_threshold_input.setValue(0.28)
        self._set_compact_numeric_input(self.slip_threshold_input)
        slip_group.addRow("Slip ratio threshold:", self.slip_threshold_input)
        self.slip_guard_duration_input = FocusWheelDoubleSpinBox()
        self.slip_guard_duration_input.setRange(0.0, 2.0)
        self.slip_guard_duration_input.setSingleStep(0.05)
        self.slip_guard_duration_input.setValue(0.30)
        self._set_compact_numeric_input(self.slip_guard_duration_input)
        slip_group.addRow(
            "Slip guard lockout duration (s):", self.slip_guard_duration_input
        )
        self.slip_min_throttle_input = FocusWheelDoubleSpinBox()
        self.slip_min_throttle_input.setRange(0.0, 1.0)
        self.slip_min_throttle_input.setSingleStep(0.01)
        self.slip_min_throttle_input.setValue(0.45)
        self._set_compact_numeric_input(self.slip_min_throttle_input)
        slip_group.addRow("Slip guard min throttle:", self.slip_min_throttle_input)
        tuning_layout.addWidget(slip_group)

        tuning_layout.addStretch()
        tuning_scroll = QScrollArea()
        tuning_scroll.setWidget(tuning_fields_widget)
        tuning_scroll.setWidgetResizable(True)
        tuning_tab_layout.addWidget(tuning_scroll, 1)
        tabs.addTab(tuning_widget, "Tuning")

        # ===== PRESETS TAB =====
        presets_widget = QWidget()
        presets_layout = QVBoxLayout(presets_widget)
        presets_group = QGroupBox("Preset-Car Binding")
        presets_form = QFormLayout(presets_group)
        self._set_compact_form(presets_form)
        self.binding_default_preset_combo = QComboBox()
        self.binding_default_preset_combo.setMaximumWidth(220)
        self.binding_default_preset_combo.currentTextChanged.connect(
            self._on_binding_default_preset_changed
        )
        presets_form.addRow("Default for new cars:", self.binding_default_preset_combo)
        presets_layout.addWidget(presets_group)

        car_group = QGroupBox("Car Preset Assignments")
        car_group_layout = QVBoxLayout(car_group)
        car_filter_row = QHBoxLayout()
        car_filter_row.addWidget(QLabel("Filter:"))
        self.car_filter_combo = QComboBox()
        self.car_filter_combo.addItems(["All", "FH4", "FH5", "FM"])
        self.car_filter_combo.setCurrentText("All")
        self.car_filter_combo.setMaximumWidth(120)
        self.car_filter_combo.currentTextChanged.connect(self._on_car_filter_changed)
        car_filter_row.addWidget(self.car_filter_combo)
        car_filter_row.addStretch(1)
        car_group_layout.addLayout(car_filter_row)
        self.car_binding_rows_layout = QVBoxLayout()
        car_group_layout.addLayout(self.car_binding_rows_layout)
        presets_layout.addWidget(car_group)
        presets_layout.addStretch()
        tabs.addTab(presets_widget, "Presets")

        self._settings_tabs = tabs
        main_layout.addWidget(tabs)

        # ===== CONTROLS BAR =====
        controls = QHBoxLayout()
        self.record_hotkey_button = QPushButton("Record Hotkey")
        self.record_hotkey_button.clicked.connect(self._start_hotkey_recording)
        controls.addWidget(self.record_hotkey_button)
        self.hotkey_label = QLabel(f"Hotkey: {self._get_hotkey_name()}")
        controls.addWidget(self.hotkey_label)
        controls.addWidget(QLabel("Log level:"))
        self.log_level_input = QComboBox()
        self.log_level_input.addItems(["DEBUG", "INFO", "WARN", "ERROR"])
        self.log_level_input.setCurrentText("INFO")
        self.log_level_input.setMaximumWidth(110)
        controls.addWidget(self.log_level_input)
        self.clear_log_button = QPushButton("Clear Log")
        self.clear_log_button.clicked.connect(self._clear_log)
        controls.addWidget(self.clear_log_button)
        self.save_log_button = QPushButton("Save Log")
        self.save_log_button.clicked.connect(self._save_log_to_file)
        controls.addWidget(self.save_log_button)
        controls.addStretch(1)
        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self.start_worker)
        controls.addWidget(self.start_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_worker)
        self.stop_button.setEnabled(False)
        controls.addWidget(self.stop_button)
        main_layout.addLayout(controls)

        self.statusBar().showMessage(
            "Status: Idle | Telemetry: Waiting | Focus: N/A | Game: Unknown"
        )

        # ===== LOG VIEW =====
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(divider)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(200)
        main_layout.addWidget(self.log_view)

        self.hotkey_pressed.connect(self._handle_hotkey_press)
        self.hotkey_released.connect(self._handle_hotkey_release)

        self._set_tuning_tooltips()

        self._load_app_state()
        # Set up global hotkey listener after state is loaded so startup log shows saved combo
        self._setup_hotkey_listener()
        self._refresh_preset_selector()

    @Slot()
    def start_worker(self) -> None:
        if self._thread is not None:
            return

        bind_host = self.listen_address_input.text().strip()
        port = int(self.port_input.value())
        dry_run = self.dry_run_checkbox.isChecked()
        require_focus_guard = self.focus_guard_checkbox.isChecked()
        shift_down_scan_code = self._shift_down_scan_code
        shift_up_scan_code = self._shift_up_scan_code
        log_level = self.log_level_input.currentText()

        at_config = self._build_at_config_from_editor()

        thread = QThread(self)
        worker = AutoShiftWorker(
            bind_host=bind_host,
            port=port,
            dry_run=dry_run,
            require_focus_guard=require_focus_guard,
            shift_down_scan_code=shift_down_scan_code,
            shift_up_scan_code=shift_up_scan_code,
            shift_down_key_name=self._shift_down_key_name,
            shift_up_key_name=self._shift_up_key_name,
            at_config=at_config,
            log_level=log_level,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.log.connect(self.append_log)
        worker.status.connect(self.set_status)
        worker.car_detected.connect(self._on_car_detected)
        self.worker_config_update_requested.connect(worker.update_at_config)
        worker.finished.connect(self.on_worker_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)

        self._thread = thread
        self._worker = worker

        self._set_input_controls_enabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

        thread.start()

    def _build_at_config_from_editor(self) -> AutomaticTransmissionConfig:
        values = self._collect_full_tuning_values()
        return AutomaticTransmissionConfig(
            upshift_rpm_low_throttle=float(values["upshift_rpm_low_throttle"]),
            upshift_rpm_high_throttle=float(values["upshift_rpm_high_throttle"]),
            downshift_rpm_low_throttle=float(values["downshift_rpm_low_throttle"]),
            downshift_rpm_high_throttle=float(values["downshift_rpm_high_throttle"]),
            min_time_between_shifts=float(values["min_time_between_shifts"]),
            pending_shift_timeout=float(values["pending_shift_timeout"]),
            min_forward_gear=int(values["min_forward_gear"]),
            max_forward_gear=int(values["max_forward_gear"]),
            min_speed_for_upshift_mps=float(values["min_speed_for_upshift_mps"]),
            min_speed_for_downshift_mps=float(values["min_speed_for_downshift_mps"]),
            min_throttle_for_upshift=float(values["min_throttle_for_upshift"]),
            min_throttle_for_downshift=float(values["min_throttle_for_downshift"]),
            brake_downshift_threshold=float(values["brake_downshift_threshold"]),
            coast_throttle_threshold=float(values["coast_throttle_threshold"]),
            coast_brake_threshold=float(values["coast_brake_threshold"]),
            coast_downshift_idle_rpm_margin=float(
                values["coast_downshift_idle_rpm_margin"]
            ),
            coast_downshift_max_speed_mps=float(
                values["coast_downshift_max_speed_mps"]
            ),
            kickdown_throttle_threshold=float(values["kickdown_throttle_threshold"]),
            kickdown_tip_in_min_delta=float(values["kickdown_tip_in_min_delta"]),
            kickdown_max_rpm=float(values["kickdown_max_rpm"]),
            kickdown_lockout_after_upshift_s=float(
                values["kickdown_lockout_after_upshift_s"]
            ),
            max_pedal_value=int(values["max_pedal_value"]),
            throttle_smoothing_alpha=float(values["throttle_smoothing_alpha"]),
            enable_per_gear_dwell=bool(values["enable_per_gear_dwell"]),
            dwell_after_upshift_s=float(values["dwell_after_upshift_s"]),
            dwell_after_downshift_s=float(values["dwell_after_downshift_s"]),
            dwell_after_kickdown_s=float(values["dwell_after_kickdown_s"]),
            per_gear_dwell_overrides=dict(values["per_gear_dwell_overrides"]),
            enable_low_speed_recovery_downshift=bool(
                values["enable_low_speed_recovery_downshift"]
            ),
            low_speed_recovery_max_speed_mps=float(
                values["low_speed_recovery_max_speed_mps"]
            ),
            low_speed_recovery_rpm_margin=float(
                values["low_speed_recovery_rpm_margin"]
            ),
            allow_upshift_from_neutral=bool(values["allow_upshift_from_neutral"]),
            allow_reverse_while_moving=bool(values["allow_reverse_while_moving"]),
            enable_unload_upshift_guard=bool(values["enable_unload_upshift_guard"]),
            unload_suspension_threshold=float(values["unload_suspension_threshold"]),
            unload_guard_after_detect_s=float(values["unload_guard_after_detect_s"]),
            unload_min_throttle=float(values["unload_min_throttle"]),
            enable_slip_upshift_guard=bool(values["enable_slip_upshift_guard"]),
            slip_upshift_guard_threshold=float(values["slip_upshift_guard_threshold"]),
            slip_guard_after_detect_s=float(values["slip_guard_after_detect_s"]),
            slip_guard_min_throttle=float(values["slip_guard_min_throttle"]),
        )

    def _set_input_controls_enabled(self, enabled: bool) -> None:
        """Enable/disable all input controls but keep tabs switchable."""
        # Connection tab
        self.listen_address_input.setEnabled(enabled)
        self.port_input.setEnabled(enabled)
        # Input tab
        self.dry_run_checkbox.setEnabled(enabled)
        self.focus_guard_checkbox.setEnabled(enabled)
        self.record_shift_down_button.setEnabled(enabled)
        self.record_shift_up_button.setEnabled(enabled)
        # Tuning tab controls (built-in presets are read-only in editor)
        active_is_builtin = self._active_preset_name.lower() in BUILTIN_PRESET_TEMPLATES
        self._set_tuning_fields_enabled(enabled and not active_is_builtin)
        # Preset editor + binding tab
        self.preset_list.setEnabled(enabled)
        self.preset_create_button.setEnabled(enabled)
        self.preset_duplicate_button.setEnabled(
            enabled and bool(self._active_preset_name)
        )
        self.preset_rename_button.setEnabled(enabled and bool(self._active_preset_name))
        self.preset_save_button.setEnabled(enabled and bool(self._active_preset_name))
        self.binding_default_preset_combo.setEnabled(enabled)
        for combo in self._car_binding_preset_combos:
            combo.setEnabled(enabled)
        for button in self._car_binding_alias_buttons:
            button.setEnabled(enabled)
        for button in self._car_binding_remove_buttons:
            button.setEnabled(enabled)
        if enabled:
            self._update_preset_delete_button_state()
        else:
            self.preset_delete_button.setEnabled(False)
            self.preset_delete_button.setToolTip("Unavailable while running")
        # Hotkey and log level
        self.log_level_input.setEnabled(enabled)
        self.record_hotkey_button.setEnabled(enabled)

    def _set_tuning_fields_enabled(self, enabled: bool) -> None:
        self.upshift_low_input.setEnabled(enabled)
        self.upshift_high_input.setEnabled(enabled)
        self.downshift_low_input.setEnabled(enabled)
        self.downshift_high_input.setEnabled(enabled)
        self.cooldown_input.setEnabled(enabled)
        self.enable_dwell_checkbox.setEnabled(enabled)
        self.dwell_up_input.setEnabled(enabled)
        self.dwell_down_input.setEnabled(enabled)
        self.dwell_kickdown_input.setEnabled(enabled)
        self.kickdown_threshold_input.setEnabled(enabled)
        self.kickdown_max_rpm_input.setEnabled(enabled)
        self.kickdown_lockout_input.setEnabled(enabled)
        self.enable_unload_guard_checkbox.setEnabled(enabled)
        self.unload_threshold_input.setEnabled(enabled)
        self.unload_guard_duration_input.setEnabled(enabled)
        self.unload_min_throttle_input.setEnabled(enabled)
        self.enable_slip_guard_checkbox.setEnabled(enabled)
        self.slip_threshold_input.setEnabled(enabled)
        self.slip_guard_duration_input.setEnabled(enabled)
        self.slip_min_throttle_input.setEnabled(enabled)

    @Slot()
    def stop_worker(self) -> None:
        if self._worker is None:
            return
        self._worker.stop()
        self.stop_button.setEnabled(False)

    @Slot(str)
    def append_log(self, message: str) -> None:
        selected_level = self.log_level_input.currentText()
        if message.startswith("[") and "]" in message:
            level = message[1 : message.find("]")]
            if level in LOG_LEVEL_ORDER:
                if LOG_LEVEL_ORDER[level] < LOG_LEVEL_ORDER[selected_level]:
                    return
        self.log_view.appendPlainText(message)

    @Slot()
    def _clear_log(self) -> None:
        self.log_view.clear()

    @Slot()
    def _save_log_to_file(self) -> None:
        default_name = f"forza-auto-shift-log-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Log",
            str(Path.cwd() / default_name),
            "Text Files (*.txt);;All Files (*)",
        )
        if not file_path:
            return
        try:
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write(self.log_view.toPlainText())
            self.append_log(f"[INFO] Log saved to {file_path}")
        except OSError as exc:
            self.append_log(f"[WARN] Could not save log: {exc}")

    @Slot(str)
    def set_status(self, status: str) -> None:
        self.statusBar().showMessage(f"Status: {status}")

    @Slot()
    def on_worker_finished(self) -> None:
        self._worker = None
        self._thread = None

        self._set_input_controls_enabled(True)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._save_app_state()

    def _get_hotkey_name(self) -> str:
        """Get a friendly name for the current hotkey combination (modifiers first)."""
        if self._current_hotkey is None or not self._current_hotkey:
            return "Not set"
        try:
            modifiers = []
            regular_keys = []

            for key in self._current_hotkey:
                key_name = str(key).split(".")[-1].upper()
                # Normalize modifier names
                key_name = key_name.replace("_L", "").replace("_R", "")

                if key_name in ("SHIFT", "CTRL", "ALT"):
                    if key_name not in modifiers:  # Avoid duplicates
                        modifiers.append(key_name)
                else:
                    if key_name not in regular_keys:  # Avoid duplicates
                        regular_keys.append(key_name)

            # Sort modifiers in preferred order
            modifier_order = {"SHIFT": 0, "CTRL": 1, "ALT": 2}
            modifiers.sort(key=lambda x: modifier_order.get(x, 999))

            # Combine modifiers first, then regular keys
            all_parts = modifiers + regular_keys
            return "+".join(all_parts)
        except Exception:
            return "Unknown"

    def _collect_tuning_values(self) -> dict[str, float | int | bool]:
        return {
            "upshift_rpm_low_throttle": int(self.upshift_low_input.value()),
            "upshift_rpm_high_throttle": int(self.upshift_high_input.value()),
            "downshift_rpm_low_throttle": int(self.downshift_low_input.value()),
            "downshift_rpm_high_throttle": int(self.downshift_high_input.value()),
            "min_time_between_shifts": float(self.cooldown_input.value()),
            "enable_per_gear_dwell": bool(self.enable_dwell_checkbox.isChecked()),
            "dwell_after_upshift_s": float(self.dwell_up_input.value()),
            "dwell_after_downshift_s": float(self.dwell_down_input.value()),
            "dwell_after_kickdown_s": float(self.dwell_kickdown_input.value()),
            "kickdown_throttle_threshold": float(self.kickdown_threshold_input.value()),
            "kickdown_max_rpm": int(self.kickdown_max_rpm_input.value()),
            "kickdown_lockout_after_upshift_s": float(
                self.kickdown_lockout_input.value()
            ),
            "enable_unload_upshift_guard": bool(
                self.enable_unload_guard_checkbox.isChecked()
            ),
            "unload_suspension_threshold": float(self.unload_threshold_input.value()),
            "unload_guard_after_detect_s": float(
                self.unload_guard_duration_input.value()
            ),
            "unload_min_throttle": float(self.unload_min_throttle_input.value()),
            "enable_slip_upshift_guard": bool(
                self.enable_slip_guard_checkbox.isChecked()
            ),
            "slip_upshift_guard_threshold": float(self.slip_threshold_input.value()),
            "slip_guard_after_detect_s": float(self.slip_guard_duration_input.value()),
            "slip_guard_min_throttle": float(self.slip_min_throttle_input.value()),
        }

    def _normalize_preset_values(
        self, values: dict[str, object] | None
    ) -> dict[str, object]:
        normalized: dict[str, object] = dict(values) if isinstance(values, dict) else {}
        for key, default_value in DEFAULT_AT_CONFIG_VALUES.items():
            if key not in normalized:
                normalized[key] = default_value

        raw_dwell_overrides = normalized.get("per_gear_dwell_overrides", {})
        if isinstance(raw_dwell_overrides, dict):
            dwell_overrides: dict[int, float] = {}
            for raw_gear, raw_seconds in raw_dwell_overrides.items():
                try:
                    gear = int(raw_gear)
                    seconds = float(raw_seconds)
                except (TypeError, ValueError):
                    continue
                dwell_overrides[gear] = seconds
            normalized["per_gear_dwell_overrides"] = dwell_overrides
        else:
            normalized["per_gear_dwell_overrides"] = {}

        return normalized

    def _collect_full_tuning_values(self) -> dict[str, object]:
        base = self._normalize_preset_values(
            self._preset_store.get(self._active_preset_name)
        )
        base.update(self._collect_tuning_values())
        return base

    def _collect_preset_payload(self) -> dict[str, object]:
        payload = self._collect_full_tuning_values()
        payload.update(
            {
                "shift_down_scan_code": int(self._shift_down_scan_code),
                "shift_up_scan_code": int(self._shift_up_scan_code),
                "shift_down_key_name": self._shift_down_key_name,
                "shift_up_key_name": self._shift_up_key_name,
            }
        )
        return payload

    def _apply_tuning_values(self, values: dict[str, float | int | bool]) -> None:
        self.upshift_low_input.setValue(
            int(values.get("upshift_rpm_low_throttle", self.upshift_low_input.value()))
        )
        self.upshift_high_input.setValue(
            int(
                values.get("upshift_rpm_high_throttle", self.upshift_high_input.value())
            )
        )
        self.downshift_low_input.setValue(
            int(
                values.get(
                    "downshift_rpm_low_throttle", self.downshift_low_input.value()
                )
            )
        )
        self.downshift_high_input.setValue(
            int(
                values.get(
                    "downshift_rpm_high_throttle", self.downshift_high_input.value()
                )
            )
        )
        self.cooldown_input.setValue(
            float(values.get("min_time_between_shifts", self.cooldown_input.value()))
        )
        self.enable_dwell_checkbox.setChecked(
            bool(
                values.get(
                    "enable_per_gear_dwell", self.enable_dwell_checkbox.isChecked()
                )
            )
        )
        self.dwell_up_input.setValue(
            float(values.get("dwell_after_upshift_s", self.dwell_up_input.value()))
        )
        self.dwell_down_input.setValue(
            float(values.get("dwell_after_downshift_s", self.dwell_down_input.value()))
        )
        self.dwell_kickdown_input.setValue(
            float(
                values.get("dwell_after_kickdown_s", self.dwell_kickdown_input.value())
            )
        )
        self.kickdown_threshold_input.setValue(
            float(
                values.get(
                    "kickdown_throttle_threshold", self.kickdown_threshold_input.value()
                )
            )
        )
        self.kickdown_max_rpm_input.setValue(
            int(values.get("kickdown_max_rpm", self.kickdown_max_rpm_input.value()))
        )
        self.kickdown_lockout_input.setValue(
            float(
                values.get(
                    "kickdown_lockout_after_upshift_s",
                    self.kickdown_lockout_input.value(),
                )
            )
        )
        self.enable_unload_guard_checkbox.setChecked(
            bool(
                values.get(
                    "enable_unload_upshift_guard",
                    self.enable_unload_guard_checkbox.isChecked(),
                )
            )
        )
        self.unload_threshold_input.setValue(
            float(
                values.get(
                    "unload_suspension_threshold", self.unload_threshold_input.value()
                )
            )
        )
        self.unload_guard_duration_input.setValue(
            float(
                values.get(
                    "unload_guard_after_detect_s",
                    self.unload_guard_duration_input.value(),
                )
            )
        )
        self.unload_min_throttle_input.setValue(
            float(
                values.get(
                    "unload_min_throttle", self.unload_min_throttle_input.value()
                )
            )
        )
        self.enable_slip_guard_checkbox.setChecked(
            bool(
                values.get(
                    "enable_slip_upshift_guard",
                    self.enable_slip_guard_checkbox.isChecked(),
                )
            )
        )
        self.slip_threshold_input.setValue(
            float(
                values.get(
                    "slip_upshift_guard_threshold", self.slip_threshold_input.value()
                )
            )
        )
        self.slip_guard_duration_input.setValue(
            float(
                values.get(
                    "slip_guard_after_detect_s", self.slip_guard_duration_input.value()
                )
            )
        )
        self.slip_min_throttle_input.setValue(
            float(
                values.get(
                    "slip_guard_min_throttle", self.slip_min_throttle_input.value()
                )
            )
        )

    def _canonical_hotkey_key(self, key: object | None) -> object | None:
        if key is None:
            return None
        if isinstance(key, keyboard.Key):
            if key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
                return keyboard.Key.shift
            if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                return keyboard.Key.ctrl
            if key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r):
                return keyboard.Key.alt
            return key
        if isinstance(key, keyboard.KeyCode):
            if key.char:
                return keyboard.KeyCode.from_char(key.char.lower())
            if key.vk is not None:
                return keyboard.KeyCode.from_vk(int(key.vk))
        return key

    def _key_to_token(self, key: object) -> str:
        key = self._canonical_hotkey_key(key)
        if key is None:
            return ""
        if isinstance(key, keyboard.KeyCode):
            if key.char:
                return f"char:{key.char}"
            if key.vk is not None:
                return f"vk:{key.vk}"
        if isinstance(key, keyboard.Key):
            return f"key:{str(key).split('.')[-1]}"
        return ""

    def _token_to_key(self, token: str) -> object | None:
        token = token.strip()
        if token.startswith("char:"):
            char = token[5:]
            return self._canonical_hotkey_key(keyboard.KeyCode.from_char(char))
        if token.startswith("vk:"):
            try:
                return self._canonical_hotkey_key(
                    keyboard.KeyCode.from_vk(int(token[3:]))
                )
            except ValueError:
                return None
        if token.startswith("key:"):
            name = token[4:]
            key = getattr(keyboard.Key, name, None)
            return self._canonical_hotkey_key(key)
        return None

    def _collect_app_state(self) -> dict[str, object]:
        hotkey_tokens = [
            self._key_to_token(k) for k in (self._current_hotkey or frozenset())
        ]
        current_tuning = self._collect_full_tuning_values()
        user_presets = {
            name: preset
            for name, preset in self._preset_store.items()
            if name.lower() not in BUILTIN_PRESET_TEMPLATES
        }
        cars: dict[str, dict[str, str]] = {}
        for car_key, preset_name in self._car_preset_map.items():
            game_code, car_id = self._split_car_key(car_key)
            alias = self._car_alias_map.get(car_key, "").strip()
            cars[car_key] = {
                "preset": preset_name,
                "alias": alias,
                "game": game_code,
                "car_id": car_id,
            }
        return {
            "schema_version": 3,
            "listen_address": self.listen_address_input.text().strip(),
            "udp_port": int(self.port_input.value()),
            "dry_run": bool(self.dry_run_checkbox.isChecked()),
            "focus_guard": bool(self.focus_guard_checkbox.isChecked()),
            "log_level": self.log_level_input.currentText(),
            "shift_down_scan_code": int(self._shift_down_scan_code),
            "shift_up_scan_code": int(self._shift_up_scan_code),
            "shift_down_key_name": self._shift_down_key_name,
            "shift_up_key_name": self._shift_up_key_name,
            "hotkey_tokens": [t for t in hotkey_tokens if t],
            "current_tuning": current_tuning,
            "active_preset": self._active_preset_name,
            "default_binding_preset": self._default_binding_preset_name,
            "presets": user_presets,
            "cars": cars,
        }

    def _apply_app_state(self, state: dict[str, object]) -> None:
        schema_version = int(state["schema_version"])
        if schema_version != 3:
            raise ValueError(
                f"Unsupported app state schema_version={schema_version}; expected 3"
            )

        self.listen_address_input.setText(str(state["listen_address"]))
        self.port_input.setValue(int(state["udp_port"]))
        self.dry_run_checkbox.setChecked(bool(state["dry_run"]))
        self.focus_guard_checkbox.setChecked(bool(state["focus_guard"]))
        saved_log_level = str(state["log_level"])
        if saved_log_level in LOG_LEVEL_ORDER:
            self.log_level_input.setCurrentText(saved_log_level)
        else:
            raise ValueError(f"Unsupported log_level '{saved_log_level}' in state")

        self._shift_down_scan_code = int(state["shift_down_scan_code"])
        self._shift_up_scan_code = int(state["shift_up_scan_code"])
        self._shift_down_key_name = str(state["shift_down_key_name"])
        self._shift_up_key_name = str(state["shift_up_key_name"])
        self.shift_down_key_label.setText(self._shift_down_key_name)
        self.shift_up_key_label.setText(self._shift_up_key_name)

        loaded_hotkeys: list[object] = []
        hotkey_tokens = state["hotkey_tokens"]
        if not isinstance(hotkey_tokens, list):
            raise ValueError("Invalid app state: hotkey_tokens must be a list")
        loaded_hotkeys.extend(
            self._token_to_key(token)
            for token in hotkey_tokens
            if isinstance(token, str)
        )
        filtered_keys = [
            key
            for key in (self._canonical_hotkey_key(k) for k in loaded_hotkeys)
            if key
        ]
        if filtered_keys:
            self._current_hotkey = frozenset(filtered_keys)
        self.hotkey_label.setText(f"Hotkey: {self._get_hotkey_name()}")

        tuning_values = state["current_tuning"]
        if not isinstance(tuning_values, dict):
            raise ValueError("Invalid app state: current_tuning must be an object")
        self._apply_tuning_values(self._normalize_preset_values(tuning_values))

        presets = state["presets"]
        if not isinstance(presets, dict):
            raise ValueError("Invalid app state: presets must be an object")
        normalized: dict[str, dict[str, object]] = {}
        for name, value in presets.items():
            if not isinstance(name, str) or not isinstance(value, dict):
                raise ValueError("Invalid app state: preset entries must be objects")
            normalized[name] = self._normalize_preset_values(value)
        self._preset_store = normalized

        normalized_car_map: dict[str, str] = {}
        normalized_alias_map: dict[str, str] = {}
        cars = state["cars"]
        if not isinstance(cars, dict):
            raise ValueError("Invalid app state: cars must be an object")
        for car_key, car_entry in cars.items():
            if not isinstance(car_key, str) or not isinstance(car_entry, dict):
                raise ValueError("Invalid app state: car entries must be objects")
            preset_name = car_entry["preset"]
            alias = car_entry["alias"]
            game = car_entry["game"]
            car_id = car_entry["car_id"]

            if not isinstance(preset_name, str) or not preset_name.strip():
                raise ValueError(
                    "Invalid app state: car preset must be a non-empty string"
                )
            if not isinstance(alias, str):
                raise ValueError("Invalid app state: car alias must be a string")
            if not isinstance(game, str):
                raise ValueError("Invalid app state: car game must be a string")
            if not isinstance(car_id, str) or not car_id.strip():
                raise ValueError("Invalid app state: car_id must be a non-empty string")

            normalized_key = self._car_storage_key(game, car_id.strip())
            normalized_car_map[normalized_key] = preset_name
            if alias.strip():
                normalized_alias_map[normalized_key] = alias.strip()

        self._car_preset_map = normalized_car_map
        self._car_alias_map = normalized_alias_map

        active_preset = state["active_preset"]
        if not isinstance(active_preset, str):
            raise ValueError("Invalid app state: active_preset must be a string")
        self._active_preset_name = active_preset

        default_binding_preset = state["default_binding_preset"]
        if not isinstance(default_binding_preset, str):
            raise ValueError(
                "Invalid app state: default_binding_preset must be a string"
            )
        self._default_binding_preset_name = default_binding_preset

    def _load_app_state(self) -> None:
        for name, template in BUILTIN_PRESET_TEMPLATES.items():
            self._preset_store[name] = self._normalize_preset_values(template)

        if not self._active_preset_name:
            self._active_preset_name = self._default_preset_name()
        if not self._default_binding_preset_name:
            self._default_binding_preset_name = self._default_preset_name()

        if not self._state_file_path.exists():
            return

        try:
            with self._state_file_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                self._apply_app_state(data)
                for name, template in BUILTIN_PRESET_TEMPLATES.items():
                    self._preset_store[name] = self._normalize_preset_values(template)
                self._normalize_car_preset_map()
                if self._active_preset_name not in self._preset_store:
                    self._active_preset_name = self._default_preset_name()
                if self._default_binding_preset_name not in self._preset_store:
                    self._default_binding_preset_name = self._default_preset_name()
                self.append_log(f"Loaded app state from {self._state_file_path.name}")
        except (OSError, json.JSONDecodeError) as exc:
            self.append_log(f"[WARN] Could not load app state: {exc}")
        except ValueError as exc:
            self.append_log(f"[WARN] Ignoring incompatible app state: {exc}")
            self._save_app_state()
            self.append_log(
                f"[INFO] Rewrote {self._state_file_path.name} to schema_version=3."
            )

    def _save_app_state(self) -> None:
        state = self._collect_app_state()
        try:
            with self._state_file_path.open("w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2)
        except OSError as exc:
            self.append_log(f"[WARN] Could not save app state: {exc}")

    def _refresh_preset_selector(self) -> None:
        current = self._active_preset_name
        self._suppress_preset_auto_apply = True
        self.preset_list.blockSignals(True)
        self.binding_default_preset_combo.blockSignals(True)
        self.preset_list.clear()
        self.binding_default_preset_combo.clear()
        for name in sorted(self._preset_store.keys(), key=str.lower):
            self.preset_list.addItem(name)
            self.binding_default_preset_combo.addItem(name)
        if current:
            names = [
                self.preset_list.item(i).text() for i in range(self.preset_list.count())
            ]
            if current in names:
                self.preset_list.setCurrentRow(names.index(current))
            elif self.preset_list.count() > 0:
                self.preset_list.setCurrentRow(0)
                self._active_preset_name = self.preset_list.currentItem().text()
        elif self.preset_list.count() > 0:
            self.preset_list.setCurrentRow(0)
            self._active_preset_name = self.preset_list.currentItem().text()

        binding_default = self._default_binding_preset_name
        if binding_default:
            index = self.binding_default_preset_combo.findText(binding_default)
            if index >= 0:
                self.binding_default_preset_combo.setCurrentIndex(index)
            elif self.binding_default_preset_combo.count() > 0:
                self.binding_default_preset_combo.setCurrentIndex(0)
                self._default_binding_preset_name = (
                    self.binding_default_preset_combo.currentText()
                )
        elif self.binding_default_preset_combo.count() > 0:
            self.binding_default_preset_combo.setCurrentIndex(0)
            self._default_binding_preset_name = (
                self.binding_default_preset_combo.currentText()
            )

        self.preset_list.blockSignals(False)
        self.binding_default_preset_combo.blockSignals(False)
        self._suppress_preset_auto_apply = False
        self._refresh_car_preset_list()
        self._update_preset_delete_button_state()

        if self._active_preset_name:
            self._load_preset_into_editor(self._active_preset_name)

    def _default_preset_name(self) -> str:
        if self._default_binding_preset_name in self._preset_store:
            return self._default_binding_preset_name
        if DEFAULT_CAR_PRESET_NAME in self._preset_store:
            return DEFAULT_CAR_PRESET_NAME
        if self._preset_store:
            return sorted(self._preset_store.keys(), key=str.lower)[0]
        self._preset_store[DEFAULT_CAR_PRESET_NAME] = dict(
            self._normalize_preset_values(
                BUILTIN_PRESET_TEMPLATES[DEFAULT_CAR_PRESET_NAME]
            )
        )
        return DEFAULT_CAR_PRESET_NAME

    def _normalize_game_code(self, game_code: str) -> str:
        code = game_code.strip().upper()
        if code in {"FH4", "FH5", "FM"}:
            return code
        return "FORZA"

    def _car_storage_key(self, game_code: str, car_id: str) -> str:
        return f"{self._normalize_game_code(game_code)}-{car_id.strip()}"

    def _split_car_key(self, car_key: str) -> tuple[str, str]:
        if "-" in car_key:
            raw_game, raw_car_id = car_key.split("-", 1)
            return self._normalize_game_code(raw_game), raw_car_id.strip()
        return "FORZA", car_key.strip()

    def _normalize_car_preset_map(self) -> None:
        default_preset = self._default_preset_name()
        for car_key, preset_name in list(self._car_preset_map.items()):
            if preset_name not in self._preset_store:
                self._car_preset_map[car_key] = default_preset
        for car_key in list(self._car_alias_map.keys()):
            if car_key not in self._car_preset_map:
                del self._car_alias_map[car_key]

    @Slot(str)
    def _on_car_filter_changed(self, _value: str) -> None:
        self._refresh_car_preset_list()

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            child_layout = item.layout()
            if isinstance(child_layout, QVBoxLayout):
                self._clear_layout(child_layout)

    def _refresh_car_preset_list(self) -> None:
        self._suppress_car_binding_updates = True
        self._car_binding_preset_combos.clear()
        self._car_binding_alias_buttons.clear()
        self._car_binding_remove_buttons.clear()
        self._clear_layout(self.car_binding_rows_layout)

        if not self._car_preset_map:
            empty_label = QLabel("No cars detected yet.")
            empty_label.setStyleSheet("color: gray;")
            self.car_binding_rows_layout.addWidget(empty_label)
            self._suppress_car_binding_updates = False
            return

        default_preset = self._default_preset_name()

        def _sort_car_key(value: str) -> tuple[str, int]:
            game_code, car_id = self._split_car_key(value)
            try:
                numeric_id = int(car_id)
            except ValueError:
                numeric_id = 0
            return game_code, numeric_id

        selected_filter = self.car_filter_combo.currentText().strip().upper()
        filtered_car_keys = []
        for car_key in sorted(self._car_preset_map.keys(), key=_sort_car_key):
            game_code, _car_id = self._split_car_key(car_key)
            if selected_filter == "ALL" or game_code == selected_filter:
                filtered_car_keys.append(car_key)

        if not filtered_car_keys:
            empty_label = QLabel("No cars in this filter.")
            empty_label.setStyleSheet("color: gray;")
            self.car_binding_rows_layout.addWidget(empty_label)
            self._suppress_car_binding_updates = False
            return

        for car_key in filtered_car_keys:
            game_code, car_id = self._split_car_key(car_key)
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            alias = self._car_alias_map.get(car_key, "").strip()
            display_name = alias if alias else f"Car {car_id}"
            car_label = QLabel(display_name)
            car_label.setFixedWidth(210)
            car_label.setToolTip(f"{game_code}-{car_id}")
            row_layout.addWidget(car_label)

            alias_button = QPushButton("Rename")
            alias_button.setFixedWidth(80)
            alias_button.clicked.connect(
                lambda _checked=False, car_key=car_key: self._rename_car_alias(car_key)
            )
            self._car_binding_alias_buttons.append(alias_button)
            row_layout.addWidget(alias_button)

            preset_combo = QComboBox()
            preset_combo.setFixedWidth(180)
            for preset_name in sorted(self._preset_store.keys(), key=str.lower):
                preset_combo.addItem(preset_name)
            current_preset = self._car_preset_map.get(car_key, default_preset)
            if preset_combo.findText(current_preset) >= 0:
                preset_combo.setCurrentText(current_preset)
            else:
                preset_combo.setCurrentText(default_preset)
                self._car_preset_map[car_key] = default_preset
            preset_combo.currentTextChanged.connect(
                lambda name, car_key=car_key: self._on_car_preset_changed(car_key, name)
            )
            self._car_binding_preset_combos.append(preset_combo)
            row_layout.addWidget(preset_combo)

            remove_button = QPushButton("Remove")
            remove_button.setFixedWidth(80)
            remove_button.clicked.connect(
                lambda _checked=False, car_key=car_key: self._remove_car_binding(
                    car_key
                )
            )
            self._car_binding_remove_buttons.append(remove_button)
            row_layout.addWidget(remove_button)

            row_layout.addStretch(1)
            self.car_binding_rows_layout.addWidget(row_widget)

        if self._thread is not None:
            for combo in self._car_binding_preset_combos:
                combo.setEnabled(False)
            for button in self._car_binding_alias_buttons:
                button.setEnabled(False)
            for button in self._car_binding_remove_buttons:
                button.setEnabled(False)

        self._suppress_car_binding_updates = False

    @Slot(int, str)
    def _on_car_detected(self, car_ordinal: int, game_code: str) -> None:
        car_id = str(car_ordinal)
        normalized_game_code = self._normalize_game_code(game_code)
        car_key = self._car_storage_key(normalized_game_code, car_id)

        was_new = car_key not in self._car_preset_map
        if was_new:
            default_preset = self._default_preset_name()
            self._car_preset_map[car_key] = default_preset
            self._refresh_car_preset_list()
            self._save_app_state()
            self.append_log(
                f"[INFO] New car detected: {normalized_game_code}-{car_id}. Assigned default preset '{default_preset}'."
            )

        mapped_preset = self._car_preset_map.get(car_key)
        if mapped_preset and mapped_preset in self._preset_store:
            current_name = self._active_preset_name.strip()
            if current_name != mapped_preset:
                self._apply_preset_by_name(
                    mapped_preset,
                    log_context=f"{normalized_game_code}-{car_id}",
                )
            if self._thread is not None and self._worker is not None:
                cfg = self._build_at_config_from_editor()
                self.append_log(
                    (
                        "[INFO] Emitting AT config for "
                        f"{normalized_game_code}-{car_id}: "
                        f"up_low={cfg.upshift_rpm_low_throttle:.0f}, "
                        f"up_high={cfg.upshift_rpm_high_throttle:.0f}, "
                        f"down_low={cfg.downshift_rpm_low_throttle:.0f}, "
                        f"down_high={cfg.downshift_rpm_high_throttle:.0f}, "
                        f"kick_thr={cfg.kickdown_throttle_threshold:.2f}, "
                        f"kick_max={cfg.kickdown_max_rpm:.0f}"
                    )
                )
                self.worker_config_update_requested.emit(cfg)

    @Slot(str, str)
    def _on_car_preset_changed(self, car_key: str, preset_name: str) -> None:
        if self._suppress_car_binding_updates:
            return
        if preset_name not in self._preset_store:
            return
        self._car_preset_map[car_key] = preset_name
        self._save_app_state()
        game_code, car_id = self._split_car_key(car_key)
        self.append_log(
            f"[INFO] Car {game_code}-{car_id} assigned to preset '{preset_name}'."
        )

    @Slot(str)
    def _rename_car_alias(self, car_key: str) -> None:
        if car_key not in self._car_preset_map:
            return
        game_code, car_id = self._split_car_key(car_key)
        current_alias = self._car_alias_map.get(car_key, "")
        alias, ok = QInputDialog.getText(
            self,
            "Set Car Alias",
            f"Alias for {game_code}-{car_id} (empty resets):",
            text=current_alias,
        )
        if not ok:
            return
        alias = alias.strip()
        if alias:
            self._car_alias_map[car_key] = alias
            self.append_log(f"[INFO] Car {game_code}-{car_id} alias set to '{alias}'.")
        else:
            self._car_alias_map.pop(car_key, None)
            self.append_log(f"[INFO] Car {game_code}-{car_id} alias reset to default.")
        self._refresh_car_preset_list()
        self._save_app_state()

    @Slot(str)
    def _remove_car_binding(self, car_key: str) -> None:
        if car_key not in self._car_preset_map:
            return
        game_code, car_id = self._split_car_key(car_key)
        del self._car_preset_map[car_key]
        self._car_alias_map.pop(car_key, None)
        self._refresh_car_preset_list()
        self._save_app_state()
        self.append_log(f"[INFO] Removed car assignment for {game_code}-{car_id}.")

    def _update_preset_delete_button_state(self) -> None:
        name = self._active_preset_name.strip()
        if not self.preset_list.isEnabled():
            self.preset_delete_button.setEnabled(False)
            self.preset_delete_button.setToolTip("Unavailable while running")
            self.preset_rename_button.setEnabled(False)
            self.preset_duplicate_button.setEnabled(False)
            self.preset_save_button.setEnabled(False)
            self._set_tuning_fields_enabled(False)
            return
        if not name:
            self.preset_delete_button.setEnabled(False)
            self.preset_delete_button.setToolTip("Select a preset in the editor list")
            self.preset_rename_button.setEnabled(False)
            self.preset_duplicate_button.setEnabled(False)
            self.preset_save_button.setEnabled(False)
            self._set_tuning_fields_enabled(False)
            return
        self.preset_rename_button.setEnabled(True)
        self.preset_duplicate_button.setEnabled(True)
        self.preset_save_button.setEnabled(True)
        if name.lower() in BUILTIN_PRESET_TEMPLATES:
            self.preset_delete_button.setEnabled(False)
            self.preset_delete_button.setToolTip("Built-in presets cannot be deleted")
            self.preset_rename_button.setEnabled(False)
            self.preset_save_button.setEnabled(False)
            self._set_tuning_fields_enabled(False)
            return
        self.preset_delete_button.setEnabled(True)
        self.preset_delete_button.setToolTip("Delete selected custom preset")
        self._set_tuning_fields_enabled(True)

    @Slot(str)
    def _on_binding_default_preset_changed(self, name: str) -> None:
        if self._suppress_preset_auto_apply:
            return
        if not name or name not in self._preset_store:
            return
        self._default_binding_preset_name = name
        self._save_app_state()

    def _load_preset_into_editor(self, name: str) -> None:
        if not name or name not in self._preset_store:
            return
        preset = self._normalize_preset_values(self._preset_store[name])
        self._preset_store[name] = dict(preset)
        self._apply_tuning_values(preset)
        self._shift_down_scan_code = int(
            preset.get("shift_down_scan_code", self._shift_down_scan_code)
        )
        self._shift_up_scan_code = int(
            preset.get("shift_up_scan_code", self._shift_up_scan_code)
        )
        self._shift_down_key_name = str(
            preset.get("shift_down_key_name", self._shift_down_key_name)
        )
        self._shift_up_key_name = str(
            preset.get("shift_up_key_name", self._shift_up_key_name)
        )
        self.shift_down_key_label.setText(self._shift_down_key_name)
        self.shift_up_key_label.setText(self._shift_up_key_name)

    @Slot()
    def _save_current_as_preset(self) -> None:
        default_name = self._active_preset_name.strip() or "my-preset"
        name, ok = QInputDialog.getText(
            self,
            "Create Preset",
            "New preset name:",
            text=default_name,
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(
                self,
                "Invalid Preset Name",
                "Preset name cannot be empty.",
                QMessageBox.StandardButton.Ok,
            )
            self.append_log("[WARN] Preset name cannot be empty.")
            return
        if name.lower() in BUILTIN_PRESET_TEMPLATES:
            QMessageBox.warning(
                self,
                "Built-in Preset",
                (
                    f"'{name}' is a built-in preset and cannot be overwritten.\n"
                    "Please choose a different preset name."
                ),
                QMessageBox.StandardButton.Ok,
            )
            self.append_log(
                f"[WARN] Built-in preset '{name}' cannot be overwritten. Choose another name."
            )
            return
        if name in self._preset_store:
            answer = QMessageBox.question(
                self,
                "Preset Exists",
                f"Preset '{name}' already exists. Choose another name.",
                QMessageBox.StandardButton.Ok,
            )
            if answer == QMessageBox.StandardButton.Ok:
                self.append_log("[INFO] Preset creation canceled.")
            return
        preset_data = self._collect_preset_payload()
        self._preset_store[name] = preset_data
        self._active_preset_name = name
        self._refresh_preset_selector()
        self._save_app_state()
        self.append_log(f"[INFO] Created preset '{name}'.")

    @Slot()
    def _save_selected_preset(self) -> None:
        name = self._active_preset_name.strip()
        if not name or name not in self._preset_store:
            self.append_log("[WARN] Select a preset to save.")
            return
        if name.lower() in BUILTIN_PRESET_TEMPLATES:
            self.append_log("[WARN] Built-in presets cannot be saved/overwritten.")
            return
        self._preset_store[name] = self._collect_preset_payload()
        self._save_app_state()
        self.append_log(f"[INFO] Saved preset '{name}'.")

    @Slot()
    def _duplicate_selected_preset(self) -> None:
        source_name = self._active_preset_name.strip()
        if not source_name or source_name not in self._preset_store:
            self.append_log("[WARN] Select a preset to duplicate.")
            return
        suggested_name = f"{source_name}-copy"
        name, ok = QInputDialog.getText(
            self,
            "Duplicate Preset",
            "Duplicate preset name:",
            text=suggested_name,
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            self.append_log("[WARN] Preset name cannot be empty.")
            return
        if name in self._preset_store:
            self.append_log(f"[WARN] Preset '{name}' already exists.")
            return
        self._preset_store[name] = dict(self._preset_store[source_name])
        self._active_preset_name = name
        self._refresh_preset_selector()
        self._save_app_state()
        self.append_log(f"[INFO] Duplicated preset '{source_name}' to '{name}'.")

    @Slot()
    def _rename_selected_preset(self) -> None:
        source_name = self._active_preset_name.strip()
        if not source_name or source_name not in self._preset_store:
            self.append_log("[WARN] Select a preset to rename.")
            return
        if source_name.lower() in BUILTIN_PRESET_TEMPLATES:
            self.append_log("[WARN] Built-in presets cannot be renamed.")
            return
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Preset",
            "New preset name:",
            text=source_name,
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == source_name:
            return
        if new_name in self._preset_store:
            self.append_log(f"[WARN] Preset '{new_name}' already exists.")
            return
        self._preset_store[new_name] = self._preset_store.pop(source_name)
        if self._default_binding_preset_name == source_name:
            self._default_binding_preset_name = new_name
        for car_key, preset_name in list(self._car_preset_map.items()):
            if preset_name == source_name:
                self._car_preset_map[car_key] = new_name
        self._active_preset_name = new_name
        self._refresh_preset_selector()
        self._save_app_state()
        self.append_log(f"[INFO] Renamed preset '{source_name}' to '{new_name}'.")

    def _apply_preset_by_name(self, name: str, log_context: str | None = None) -> None:
        if not name or name not in self._preset_store:
            return
        self._load_preset_into_editor(name)
        self._active_preset_name = name
        self._refresh_preset_selector()
        self._save_app_state()
        if log_context:
            self.append_log(f"[INFO] Applied preset '{name}' from {log_context}.")
        else:
            self.append_log(f"[INFO] Applied preset '{name}'.")

    @Slot()
    def _apply_selected_preset(self) -> None:
        name = self._active_preset_name.strip()
        if not name or name not in self._preset_store:
            self.append_log("[WARN] Select a preset to apply.")
            return
        self._apply_preset_by_name(name)

    @Slot(str)
    def _on_preset_selected(self, name: str) -> None:
        self._active_preset_name = name.strip()
        self._update_preset_delete_button_state()
        if self._suppress_preset_auto_apply:
            return
        if not name or name not in self._preset_store:
            return
        self._load_preset_into_editor(name)
        self._save_app_state()

    @Slot()
    def _delete_selected_preset(self) -> None:
        name = self._active_preset_name.strip()
        if not name:
            self.append_log("[WARN] Select a preset to delete.")
            return
        if name.lower() in BUILTIN_PRESET_TEMPLATES:
            self.append_log("[WARN] Built-in presets cannot be deleted.")
            return
        if name in self._preset_store:
            affected_cars = [
                car_key
                for car_key, preset_name in self._car_preset_map.items()
                if preset_name == name
            ]
            if affected_cars:
                default_preset = self._default_preset_name()
                formatted_cars = [
                    f"{self._split_car_key(car_key)[0]}-{self._split_car_key(car_key)[1]}"
                    for car_key in affected_cars
                ]
                answer = QMessageBox.question(
                    self,
                    "Delete Preset",
                    (
                        f"Preset '{name}' is assigned to {len(affected_cars)} car(s): "
                        f"{', '.join(sorted(formatted_cars))}.\n\n"
                        f"If deleted, affected cars will be reassigned to '{default_preset}'. Continue?"
                    ),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                for car_key in affected_cars:
                    self._car_preset_map[car_key] = default_preset

            if self._active_preset_name == name:
                self._active_preset_name = self._default_preset_name()
            if self._default_binding_preset_name == name:
                self._default_binding_preset_name = self._default_preset_name()
            del self._preset_store[name]
            self._refresh_preset_selector()
            self._save_app_state()
            self.append_log(f"[INFO] Deleted preset '{name}'.")

    def _set_tuning_tooltips(self) -> None:
        self._set_tooltip_with_label(
            self.upshift_low_input,
            "Upshift RPM target at low throttle (gentle driving).",
        )
        self._set_tooltip_with_label(
            self.upshift_high_input,
            "Upshift RPM target at high throttle (aggressive driving).",
        )
        self._set_tooltip_with_label(
            self.downshift_low_input, "Downshift RPM target at low throttle."
        )
        self._set_tooltip_with_label(
            self.downshift_high_input,
            "Downshift RPM target at high throttle or braking load.",
        )
        self._set_tooltip_with_label(
            self.cooldown_input,
            "Minimum time between shifts to avoid rapid gear hunting.",
        )
        self.enable_dwell_checkbox.setToolTip(
            "Enable extra per-shift dwell hold times."
        )
        self._set_tooltip_with_label(
            self.dwell_up_input, "Extra hold time after an upshift."
        )
        self._set_tooltip_with_label(
            self.dwell_down_input, "Extra hold time after a downshift."
        )
        self._set_tooltip_with_label(
            self.dwell_kickdown_input, "Extra hold time after a kickdown downshift."
        )
        self._set_tooltip_with_label(
            self.kickdown_threshold_input,
            "Throttle threshold to allow kickdown downshifts.",
        )
        self._set_tooltip_with_label(
            self.kickdown_max_rpm_input, "Maximum RPM where kickdown is allowed."
        )
        self._set_tooltip_with_label(
            self.kickdown_lockout_input,
            "Time to block kickdown immediately after upshift.",
        )
        self.enable_unload_guard_checkbox.setToolTip(
            "Blocks upshift briefly when suspension unload indicates airborne/crest."
        )
        self._set_tooltip_with_label(
            self.unload_threshold_input,
            "Normalized suspension travel threshold for unload detection.",
        )
        self._set_tooltip_with_label(
            self.unload_guard_duration_input,
            "How long upshift stays blocked after unload is detected.",
        )
        self._set_tooltip_with_label(
            self.unload_min_throttle_input,
            "Minimum throttle required before unload guard can trigger.",
        )
        self.enable_slip_guard_checkbox.setToolTip(
            "Blocks upshift briefly when driven tire slip is high."
        )
        self._set_tooltip_with_label(
            self.slip_threshold_input,
            "Slip ratio threshold used to trigger slip upshift guard.",
        )
        self._set_tooltip_with_label(
            self.slip_guard_duration_input,
            "How long upshift stays blocked after slip trigger.",
        )
        self._set_tooltip_with_label(
            self.slip_min_throttle_input,
            "Minimum throttle required before slip guard can trigger.",
        )

    def _set_tooltip_with_label(self, field: QWidget, text: str) -> None:
        field.setToolTip(text)
        parent = field.parentWidget()
        if parent is None:
            return
        form = parent.layout()
        if not isinstance(form, QFormLayout):
            return
        for row in range(form.rowCount()):
            field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
            if field_item is None or field_item.widget() is not field:
                continue
            label_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if label_item is not None:
                label_widget = label_item.widget()
                if label_widget is not None:
                    label_widget.setToolTip(text)
            break

    @Slot()
    def _start_hotkey_recording(self) -> None:
        """Start listening for next key press to record as hotkey."""
        if self._recording_shift_key_target is not None:
            return
        self._currently_pressed_keys.clear()
        self._hotkey_recording_pressed_keys.clear()
        self._hotkey_trigger_latched = False
        self._recording_hotkey = True
        self.record_hotkey_button.setText("Recording... (ESC to cancel)")
        self.record_hotkey_button.setEnabled(False)
        self.append_log(
            "Hotkey recording started. Press combo to set, or ESC to cancel."
        )

    @Slot()
    def _start_shift_down_key_recording(self) -> None:
        if self._recording_hotkey:
            return
        self._currently_pressed_keys.clear()
        self._recording_shift_key_target = "down"
        self.record_shift_down_button.setText("Press key... (ESC cancel)")
        self.record_shift_down_button.setEnabled(False)
        self.record_shift_up_button.setEnabled(False)
        self.append_log("Recording shift down key (single key, ESC to cancel)...")

    @Slot()
    def _start_shift_up_key_recording(self) -> None:
        if self._recording_hotkey:
            return
        self._currently_pressed_keys.clear()
        self._recording_shift_key_target = "up"
        self.record_shift_up_button.setText("Press key... (ESC cancel)")
        self.record_shift_up_button.setEnabled(False)
        self.record_shift_down_button.setEnabled(False)
        self.append_log("Recording shift up key (single key, ESC to cancel)...")

    def _cancel_hotkey_recording(self) -> None:
        if not self._recording_hotkey:
            return
        self._recording_hotkey = False
        self._hotkey_recording_pressed_keys.clear()
        self.record_hotkey_button.setText("Record Hotkey")
        self.record_hotkey_button.setEnabled(True)
        self.append_log("Hotkey recording canceled.")

    def _cancel_shift_key_recording(self) -> None:
        if self._recording_shift_key_target is None:
            return
        target = self._recording_shift_key_target
        self._finish_shift_key_recording()
        self.append_log(f"Shift {target} key recording canceled.")

    def _key_to_scan_code(self, key: object) -> int | None:
        user32 = ctypes.windll.user32
        vk: int | None = None

        if isinstance(key, keyboard.KeyCode):
            if key.vk is not None:
                vk = int(key.vk)
            elif key.char:
                vk = ord(key.char.upper())
        elif isinstance(key, keyboard.Key):
            vk = SPECIAL_KEY_VK_MAP.get(key)

        if vk is None:
            return None

        scan_code = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
        if scan_code <= 0:
            return None
        return scan_code & 0xFF

    def _single_key_name(self, key: object) -> str:
        if isinstance(key, keyboard.KeyCode):
            if key.char:
                return key.char.upper()
            if key.vk is not None:
                return f"VK_{key.vk}"
        if isinstance(key, keyboard.Key):
            name = str(key).split(".")[-1].upper()
            return name.replace("_L", "").replace("_R", "")
        return "UNKNOWN"

    def _finish_shift_key_recording(self) -> None:
        self._recording_shift_key_target = None
        self.record_shift_down_button.setText("Bind Down Key")
        self.record_shift_up_button.setText("Bind Up Key")
        self.record_shift_down_button.setEnabled(True)
        self.record_shift_up_button.setEnabled(True)

    def _on_hotkey_press(self, key: keyboard.Key | keyboard.KeyCode | None) -> bool:
        """pynput callback thread: forward key press to Qt main thread."""
        if key is None:
            return True
        self.hotkey_pressed.emit(key)
        return True

    def _on_hotkey_release(self, key: keyboard.Key | keyboard.KeyCode | None) -> bool:
        """pynput callback thread: forward key release to Qt main thread."""
        if key is None:
            return True
        self.hotkey_released.emit(key)
        return True

    @Slot(object)
    def _handle_hotkey_press(self, key: object) -> None:
        """Qt main thread: update hotkey state and toggle start/stop safely."""
        canonical_key = self._canonical_hotkey_key(key)
        if canonical_key is None:
            return
        self._currently_pressed_keys.add(canonical_key)

        if self._recording_shift_key_target is not None:
            if key == keyboard.Key.esc:
                self._cancel_shift_key_recording()
                return
            scan_code = self._key_to_scan_code(key)
            if scan_code is None:
                self.append_log("Unsupported key for shift binding. Try another key.")
                self._finish_shift_key_recording()
                return

            key_name = self._single_key_name(key)
            if self._recording_shift_key_target == "down":
                self._shift_down_scan_code = scan_code
                self._shift_down_key_name = key_name
                self.shift_down_key_label.setText(key_name)
                self.append_log(f"Shift down key set to {key_name}")
            else:
                self._shift_up_scan_code = scan_code
                self._shift_up_key_name = key_name
                self.shift_up_key_label.setText(key_name)
                self.append_log(f"Shift up key set to {key_name}")

            self._finish_shift_key_recording()
            return

        if self._recording_hotkey:
            if key == keyboard.Key.esc:
                self._cancel_hotkey_recording()
                return

            self._hotkey_recording_pressed_keys.add(canonical_key)

            # If non-modifier key pressed, use only keys pressed during recording.
            if canonical_key not in self.MODIFIER_KEYS:
                self._current_hotkey = frozenset(self._hotkey_recording_pressed_keys)
                self._recording_hotkey = False
                self._hotkey_recording_pressed_keys.clear()
                self.record_hotkey_button.setText("Record Hotkey")
                self.record_hotkey_button.setEnabled(True)
                self.hotkey_label.setText(f"Hotkey: {self._get_hotkey_name()}")
                self.append_log(f"Hotkey set to {self._get_hotkey_name()}")
            return

        # Check if current pressed keys match hotkey
        if (
            self._current_hotkey
            and self._currently_pressed_keys == self._current_hotkey
            and not self._hotkey_trigger_latched
        ):
            self._hotkey_trigger_latched = True
            if self._thread is None:
                self.start_worker()
            else:
                self.stop_worker()

    @Slot(object)
    def _handle_hotkey_release(self, key: object) -> None:
        """Qt main thread: clear released key and re-arm combo trigger."""
        canonical_key = self._canonical_hotkey_key(key)
        if canonical_key is None:
            return
        self._currently_pressed_keys.discard(canonical_key)
        if self._recording_hotkey:
            self._hotkey_recording_pressed_keys.discard(canonical_key)
        if (
            self._current_hotkey is None
            or self._currently_pressed_keys != self._current_hotkey
        ):
            self._hotkey_trigger_latched = False

    def _setup_hotkey_listener(self) -> None:
        """Set up global hotkey listener for toggle start/stop."""
        try:
            self._hotkey_listener = keyboard.Listener(
                on_press=self._on_hotkey_press, on_release=self._on_hotkey_release
            )
            self._hotkey_listener.start()
            self.append_log(
                f"Global hotkey listener started. Hotkey: {self._get_hotkey_name()}"
            )
        except Exception as e:
            self.append_log(f"Warning: Could not set up hotkey listener: {e}")

    def _cleanup_hotkey_listener(self) -> None:
        """Clean up hotkey listener."""
        self._currently_pressed_keys.clear()
        self._hotkey_recording_pressed_keys.clear()
        self._hotkey_trigger_latched = False
        self._cancel_hotkey_recording()
        self._finish_shift_key_recording()
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
            self._hotkey_listener = None

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._cleanup_hotkey_listener()
        self._save_app_state()
        if self._worker is not None:
            self._worker.stop()

        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(1000)

        event.accept()


def run_gui() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


def main() -> None:
    raise SystemExit(run_gui())
