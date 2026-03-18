"""Minimal PySide6 GUI for the Forza Auto Shift core app."""

from __future__ import annotations

import socket
import sys
import threading

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


class AutoShiftWorker(QObject):
    """Runs telemetry receive/decode/shift loop in a background thread."""

    log = Signal(str)
    status = Signal(str)
    finished = Signal()

    def __init__(self, port: int, dry_run: bool) -> None:
        super().__init__()
        self.port = port
        self.dry_run = dry_run
        self._stop_event = threading.Event()
        self._listener: TelemetryListener | None = None

    @Slot()
    def run(self) -> None:
        self.status.emit("Running")
        self.log.emit(f"Listening for telemetry on UDP {self.port}...")
        self.log.emit(f"Input mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
        self.log.emit("Controls: Q=gear down, E=gear up")
        self.log.emit("-" * 80)

        at = AdaptiveAutomaticTransmission(AutomaticTransmissionConfig())
        input_controller = GearInputController(GearInputConfig(dry_run=self.dry_run))

        packet_count = 0
        last_packet_type = ""

        try:
            with TelemetryListener(port=self.port) as listener:
                self._listener = listener
                listener.sock.settimeout(0.20)

                while not self._stop_event.is_set():
                    try:
                        raw_data, addr = listener.recv_raw()
                    except socket.timeout:
                        continue
                    except OSError:
                        break

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
                        input_controller.shift_up()
                        self.log.emit(
                            f"[{packet_count}] SHIFT UP | gear={packet.gear} rpm={packet.current_rpm:.0f} speed={packet.speed_kmh or 0.0:.1f} km/h"
                        )
                    elif action == "downshift":
                        input_controller.shift_down()
                        self.log.emit(
                            f"[{packet_count}] SHIFT DOWN | gear={packet.gear} rpm={packet.current_rpm:.0f} speed={packet.speed_kmh or 0.0:.1f} km/h"
                        )
        except OSError as exc:
            self.log.emit(f"Listener error: {exc}")
        finally:
            self._listener = None
            self.status.emit("Stopped")
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

        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self.start_worker)
        controls.addWidget(self.start_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_worker)
        self.stop_button.setEnabled(False)
        controls.addWidget(self.stop_button)

        controls.addStretch(1)

        self.status_label = QLabel("Status: Idle")
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

        thread = QThread(self)
        worker = AutoShiftWorker(port=port, dry_run=dry_run)
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
