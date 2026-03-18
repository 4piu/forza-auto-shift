"""Minimal PySide6 GUI for the Forza Auto Shift core app."""

from __future__ import annotations

import socket
import sys
import threading
import time
import ctypes
import os

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
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
from .input_controller import GearInputConfig, GearInputController
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

    def __init__(self, port: int, dry_run: bool, require_focus_guard: bool) -> None:
        super().__init__()
        self.port = port
        self.dry_run = dry_run
        self.require_focus_guard = require_focus_guard
        self._stop_event = threading.Event()
        self._listener: TelemetryListener | None = None
        self._run_state = "Idle"
        self._telemetry_state = "Waiting"
        self._focus_state = "N/A"

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
        self.log.emit(f"Listening for telemetry on UDP {self.port}...")
        self.log.emit(f"Input mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
        if not self.dry_run:
            self.log.emit(
                f"Window focus guard: {'ON' if self.require_focus_guard else 'OFF'}"
            )
        self.log.emit("Controls: Q=gear down, E=gear up")
        self.log.emit("-" * 80)

        at = AdaptiveAutomaticTransmission(AutomaticTransmissionConfig())
        input_controller = GearInputController(GearInputConfig(dry_run=self.dry_run))

        packet_count = 0
        last_packet_type = ""
        last_packet_time = time.monotonic()

        try:
            with TelemetryListener(port=self.port) as listener:
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
                        self.log.emit(
                            f"[{packet_count}] Unknown packet size={len(raw_data)} from {addr}"
                        )
                        continue
                    except PacketDecodeError as exc:
                        self.log.emit(
                            f"[{packet_count}] Decode error for size={len(raw_data)} from {addr}: {exc}"
                        )
                        continue

                    if packet.packet_type != last_packet_type:
                        self.log.emit(
                            f"Detected packet format: {packet.packet_type} ({len(raw_data)} bytes)"
                        )
                        last_packet_type = packet.packet_type

                    if packet_count <= 5 or packet_count % PRINT_EVERY == 0:
                        self.log.emit(
                            f"[{packet_count}] {format_packet_summary(packet)}"
                        )

                    if not packet.is_race_on:
                        continue

                    action = at.update(packet)
                    if action == "upshift":
                        if self.require_focus_guard and not self.dry_run:
                            if self._focus_state != "Active":
                                self.log.emit(
                                    f"[{packet_count}] Shift blocked: Forza window not active"
                                )
                                continue
                        input_controller.shift_up()
                        self.log.emit(
                            f"[{packet_count}] SHIFT UP | gear={packet.gear} rpm={packet.current_rpm:.0f} speed={packet.speed_kmh or 0.0:.1f} km/h"
                        )
                    elif action == "downshift":
                        if self.require_focus_guard and not self.dry_run:
                            if self._focus_state != "Active":
                                self.log.emit(
                                    f"[{packet_count}] Shift blocked: Forza window not active"
                                )
                                continue
                        input_controller.shift_down()
                        self.log.emit(
                            f"[{packet_count}] SHIFT DOWN | gear={packet.gear} rpm={packet.current_rpm:.0f} speed={packet.speed_kmh or 0.0:.1f} km/h"
                        )
        except OSError as exc:
            self.log.emit(f"Listener error: {exc}")
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

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Forza Auto Shift")
        self.resize(920, 560)

        self._thread: QThread | None = None
        self._worker: AutoShiftWorker | None = None

        root = QWidget(self)
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("UDP Port:"))

        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(DEFAULT_TELEMETRY_PORT)
        controls.addWidget(self.port_input)

        self.dry_run_checkbox = QCheckBox("Dry Run (no key press)")
        self.dry_run_checkbox.setChecked(True)
        controls.addWidget(self.dry_run_checkbox)

        self.focus_guard_checkbox = QCheckBox("Require Forza window focus")
        self.focus_guard_checkbox.setChecked(True)
        controls.addWidget(self.focus_guard_checkbox)

        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self.start_worker)
        controls.addWidget(self.start_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_worker)
        self.stop_button.setEnabled(False)
        controls.addWidget(self.stop_button)

        controls.addStretch(1)

        self.status_label = QLabel("Status: Idle | Telemetry: Waiting | Focus: N/A")
        controls.addWidget(self.status_label)

        layout.addLayout(controls)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)

    @Slot()
    def start_worker(self) -> None:
        if self._thread is not None:
            return

        port = int(self.port_input.value())
        dry_run = self.dry_run_checkbox.isChecked()
        require_focus_guard = self.focus_guard_checkbox.isChecked()

        thread = QThread(self)
        worker = AutoShiftWorker(
            port=port,
            dry_run=dry_run,
            require_focus_guard=require_focus_guard,
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

        self.port_input.setEnabled(False)
        self.dry_run_checkbox.setEnabled(False)
        self.focus_guard_checkbox.setEnabled(False)
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
        self.log_view.appendPlainText(message)

    @Slot(str)
    def set_status(self, status: str) -> None:
        self.status_label.setText(f"Status: {status}")

    @Slot()
    def on_worker_finished(self) -> None:
        self._worker = None
        self._thread = None

        self.port_input.setEnabled(True)
        self.dry_run_checkbox.setEnabled(True)
        self.focus_guard_checkbox.setEnabled(True)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def closeEvent(self, event) -> None:  # type: ignore[override]
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
