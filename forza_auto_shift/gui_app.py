"""Minimal PySide6 GUI for the Forza Auto Shift core app."""

from __future__ import annotations

import socket
import sys
import threading
import time
import ctypes
import os

from pynput import keyboard

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
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

FORZA_PROCESS_NAMES = {
    "forzahorizon4.exe",
    "forzahorizon5.exe",
    "forzamotorsport.exe",
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
        self._run_state = "Idle"
        self._telemetry_state = "Waiting"
        self._focus_state = "N/A"

    def _log(self, level: str, message: str) -> None:
        current_level = LOG_LEVEL_ORDER.get(self.log_level, LOG_LEVEL_ORDER["INFO"])
        message_level = LOG_LEVEL_ORDER.get(level, LOG_LEVEL_ORDER["INFO"])
        if message_level >= current_level:
            self.log.emit(f"[{level}] {message}")

    def _refresh_focus_state(self) -> None:
        if self.dry_run or not self.require_focus_guard:
            next_state = "N/A"
        else:
            next_state = "Active" if is_game_window_active() else "Blocked"

        if next_state != self._focus_state:
            self._focus_state = next_state
            self._emit_status()

    def _emit_status(self) -> None:
        self.status.emit(
            f"{self._run_state} | Telemetry: {self._telemetry_state} | Focus: {self._focus_state}"
        )

    @Slot()
    def run(self) -> None:
        self._run_state = "Running"
        self._telemetry_state = "Waiting"
        self._focus_state = (
            "N/A" if self.dry_run or not self.require_focus_guard else "Unknown"
        )
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
        self._log("INFO", "-" * 80)

        at = AdaptiveAutomaticTransmission(self.at_config)
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

                    action = at.update(packet)
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
                            f"[{packet_count}] SHIFT UP | gear={packet.gear} rpm={packet.current_rpm:.0f} speed={packet.speed_kmh or 0.0:.1f} km/h",
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
                            f"[{packet_count}] SHIFT DOWN | gear={packet.gear} rpm={packet.current_rpm:.0f} speed={packet.speed_kmh or 0.0:.1f} km/h",
                        )
        except OSError as exc:
            self._log("ERROR", f"Listener error: {exc}")
        finally:
            self._listener = None
            self._run_state = "Stopped"
            self._telemetry_state = "Stopped"
            self._emit_status()
            self.finished.emit()

    def stop(self) -> None:
        self._stop_event.set()
        if self._listener is not None:
            self._listener.close()


class MainWindow(QMainWindow):
    """Minimal GUI shell for starting/stopping the core app loop."""

    hotkey_pressed = Signal(object)
    hotkey_released = Signal(object)

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

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Forza Auto Shift")
        self.resize(920, 560)

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

        root = QWidget(self)
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)

        connection_group = QGroupBox("Connection")
        connection_form = QFormLayout(connection_group)
        self.listen_address_input = QLineEdit("0.0.0.0")
        connection_form.addRow("Listen address:", self.listen_address_input)
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(DEFAULT_TELEMETRY_PORT)
        connection_form.addRow("UDP port:", self.port_input)
        layout.addWidget(connection_group)

        input_group = QGroupBox("Input")
        input_form = QFormLayout(input_group)
        self.dry_run_checkbox = QCheckBox("Dry Run (no key press)")
        self.dry_run_checkbox.setChecked(True)
        input_form.addRow(self.dry_run_checkbox)
        self.focus_guard_checkbox = QCheckBox("Require Forza window focus")
        self.focus_guard_checkbox.setChecked(True)
        input_form.addRow(self.focus_guard_checkbox)
        shift_down_row = QHBoxLayout()
        self.record_shift_down_button = QPushButton("Bind Down Key")
        self.record_shift_down_button.clicked.connect(
            self._start_shift_down_key_recording
        )
        shift_down_row.addWidget(self.record_shift_down_button)
        self.shift_down_key_label = QLabel(self._shift_down_key_name)
        shift_down_row.addWidget(self.shift_down_key_label)
        input_form.addRow("Shift down key:", shift_down_row)
        shift_up_row = QHBoxLayout()
        self.record_shift_up_button = QPushButton("Bind Up Key")
        self.record_shift_up_button.clicked.connect(self._start_shift_up_key_recording)
        shift_up_row.addWidget(self.record_shift_up_button)
        self.shift_up_key_label = QLabel(self._shift_up_key_name)
        shift_up_row.addWidget(self.shift_up_key_label)
        input_form.addRow("Shift up key:", shift_up_row)
        layout.addWidget(input_group)

        self.tuning_group = QGroupBox("AT Tuning")
        tuning_form = QFormLayout(self.tuning_group)
        self.upshift_low_input = QSpinBox()
        self.upshift_low_input.setRange(500, 12000)
        self.upshift_low_input.setValue(2800)
        tuning_form.addRow("Upshift RPM (low throttle):", self.upshift_low_input)
        self.upshift_high_input = QSpinBox()
        self.upshift_high_input.setRange(1000, 12000)
        self.upshift_high_input.setValue(7000)
        tuning_form.addRow("Upshift RPM (high throttle):", self.upshift_high_input)
        self.downshift_low_input = QSpinBox()
        self.downshift_low_input.setRange(500, 12000)
        self.downshift_low_input.setValue(1100)
        tuning_form.addRow("Downshift RPM (low throttle):", self.downshift_low_input)
        self.downshift_high_input = QSpinBox()
        self.downshift_high_input.setRange(500, 12000)
        self.downshift_high_input.setValue(3600)
        tuning_form.addRow("Downshift RPM (high throttle):", self.downshift_high_input)
        self.cooldown_input = QDoubleSpinBox()
        self.cooldown_input.setRange(0.05, 2.00)
        self.cooldown_input.setSingleStep(0.05)
        self.cooldown_input.setValue(0.35)
        tuning_form.addRow("Shift cooldown (s):", self.cooldown_input)
        self.enable_dwell_checkbox = QCheckBox("Enable dwell")
        self.enable_dwell_checkbox.setChecked(False)
        tuning_form.addRow(self.enable_dwell_checkbox)
        self.dwell_up_input = QDoubleSpinBox()
        self.dwell_up_input.setRange(0.0, 2.0)
        self.dwell_up_input.setSingleStep(0.05)
        self.dwell_up_input.setValue(0.0)
        tuning_form.addRow("Dwell after upshift (s):", self.dwell_up_input)
        self.dwell_down_input = QDoubleSpinBox()
        self.dwell_down_input.setRange(0.0, 2.0)
        self.dwell_down_input.setSingleStep(0.05)
        self.dwell_down_input.setValue(0.0)
        tuning_form.addRow("Dwell after downshift (s):", self.dwell_down_input)
        self.dwell_kickdown_input = QDoubleSpinBox()
        self.dwell_kickdown_input.setRange(0.0, 2.0)
        self.dwell_kickdown_input.setSingleStep(0.05)
        self.dwell_kickdown_input.setValue(0.0)
        tuning_form.addRow("Dwell after kickdown (s):", self.dwell_kickdown_input)
        self.kickdown_threshold_input = QDoubleSpinBox()
        self.kickdown_threshold_input.setRange(0.0, 1.0)
        self.kickdown_threshold_input.setSingleStep(0.01)
        self.kickdown_threshold_input.setValue(0.88)
        tuning_form.addRow(
            "Kickdown throttle threshold:", self.kickdown_threshold_input
        )
        self.kickdown_max_rpm_input = QSpinBox()
        self.kickdown_max_rpm_input.setRange(500, 12000)
        self.kickdown_max_rpm_input.setValue(5200)
        tuning_form.addRow("Kickdown max RPM:", self.kickdown_max_rpm_input)
        self.tuning_group.setCheckable(True)
        self.tuning_group.setChecked(False)
        layout.addWidget(self.tuning_group)

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
        controls.addWidget(self.log_level_input)
        controls.addStretch(1)
        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self.start_worker)
        controls.addWidget(self.start_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_worker)
        self.stop_button.setEnabled(False)
        controls.addWidget(self.stop_button)
        layout.addLayout(controls)

        self.status_label = QLabel("Status: Idle | Telemetry: Waiting | Focus: N/A")
        layout.addWidget(self.status_label)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        layout.addWidget(divider)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)

        self.hotkey_pressed.connect(self._handle_hotkey_press)
        self.hotkey_released.connect(self._handle_hotkey_release)

        # Set up global hotkey listener
        self._setup_hotkey_listener()

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

        at_config = AutomaticTransmissionConfig(
            upshift_rpm_low_throttle=float(self.upshift_low_input.value()),
            upshift_rpm_high_throttle=float(self.upshift_high_input.value()),
            downshift_rpm_low_throttle=float(self.downshift_low_input.value()),
            downshift_rpm_high_throttle=float(self.downshift_high_input.value()),
            min_time_between_shifts=float(self.cooldown_input.value()),
            enable_per_gear_dwell=self.enable_dwell_checkbox.isChecked(),
            dwell_after_upshift_s=float(self.dwell_up_input.value()),
            dwell_after_downshift_s=float(self.dwell_down_input.value()),
            dwell_after_kickdown_s=float(self.dwell_kickdown_input.value()),
            kickdown_throttle_threshold=float(self.kickdown_threshold_input.value()),
            kickdown_max_rpm=float(self.kickdown_max_rpm_input.value()),
        )

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
        worker.finished.connect(self.on_worker_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)

        self._thread = thread
        self._worker = worker

        self.listen_address_input.setEnabled(False)
        self.port_input.setEnabled(False)
        self.dry_run_checkbox.setEnabled(False)
        self.focus_guard_checkbox.setEnabled(False)
        self.record_shift_down_button.setEnabled(False)
        self.record_shift_up_button.setEnabled(False)
        self.tuning_group.setEnabled(False)
        self.log_level_input.setEnabled(False)
        self.record_hotkey_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

        thread.start()

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

    @Slot(str)
    def set_status(self, status: str) -> None:
        self.status_label.setText(f"Status: {status}")

    @Slot()
    def on_worker_finished(self) -> None:
        self._worker = None
        self._thread = None

        self.listen_address_input.setEnabled(True)
        self.port_input.setEnabled(True)
        self.dry_run_checkbox.setEnabled(True)
        self.focus_guard_checkbox.setEnabled(True)
        self.record_shift_down_button.setEnabled(True)
        self.record_shift_up_button.setEnabled(True)
        self.tuning_group.setEnabled(True)
        self.log_level_input.setEnabled(True)
        self.record_hotkey_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

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
        self._currently_pressed_keys.add(key)

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

            self._hotkey_recording_pressed_keys.add(key)

            # If non-modifier key pressed, use only keys pressed during recording.
            if key not in self.MODIFIER_KEYS:
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
        self._currently_pressed_keys.discard(key)
        if self._recording_hotkey:
            self._hotkey_recording_pressed_keys.discard(key)
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
