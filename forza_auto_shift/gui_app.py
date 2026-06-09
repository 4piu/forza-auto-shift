"""Minimal PySide6 GUI for the Forza Auto Shift core app."""

from __future__ import annotations

import sys
import time
import ctypes
import json
import os
from pathlib import Path

from pynput import keyboard

from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    QLocale,
    QPointF,
    QRectF,
    Qt,
    QThread,
    QTimer,
    QTranslator,
    Signal,
    Slot,
    QUrl,
)
from PySide6.QtGui import (
    QColor,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
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
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QStyledItemDelegate,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtMultimedia import QSoundEffect
except Exception:
    QSoundEffect = None

from .auto_transmission import (
    AutomaticTransmissionConfig,
    DEFAULT_DOWNSHIFT_CURVE,
    DEFAULT_MIN_SHIFT_RPM_GAP,
    DEFAULT_UPSHIFT_CURVE,
    ShiftCurvePoint,
    minimum_shift_curve_gap,
    minimum_shift_curve_ratio_gap,
    normalize_shift_curve,
    serialize_shift_curve,
    shift_curve_target_rpm,
)
from .input_controller import SC_E, SC_Q
from .preset_binding_service import (
    DISABLED_PRESET_NAME,
    build_car_binding_rows,
    car_storage_key,
    cars_assigned_to_preset,
    create_or_update_preset,
    default_preset_name,
    duplicate_preset,
    format_car_keys,
    normalize_car_preset_maps,
    normalize_game_code,
    reassign_cars_to_preset,
    remove_preset_entry,
    rename_preset_entry,
    rename_preset_references,
    resolve_selected_name,
    sorted_preset_names,
    split_car_key,
    upsert_detected_car,
    validate_new_preset_name,
    validate_rename_preset,
)
from .state_persistence import (
    APP_STATE_FILE_NAME,
    SCHEMA_VERSION,
    build_app_state_payload,
    load_state_file,
    normalize_preset_values,
    parse_app_state_payload,
    save_state_file,
)
from .telemetry import DEFAULT_TELEMETRY_PORT
from .workers.auto_shift_worker import AutoShiftWorker

LOG_LEVEL_ORDER = {
    "DEBUG": 10,
    "INFO": 20,
    "WARN": 30,
    "ERROR": 40,
}
CAR_TABLE_GAME_COLUMN = 0
CAR_TABLE_ID_COLUMN = 1
CAR_TABLE_ALIAS_COLUMN = 2
CAR_TABLE_PRESET_COLUMN = 3
CAR_TABLE_ACTIONS_COLUMN = 4
MAPVK_VK_TO_VSC = 0
DEFAULT_CAR_PRESET_NAME = "street"
AUTO_LANGUAGE_CODE = "auto"
SUPPORTED_UI_LANGUAGE_CODES = ("en", "es", "fr", "de", "zh_cn", "zh_tw", "ja_jp")
UI_LANGUAGE_CODES = (AUTO_LANGUAGE_CODE, *SUPPORTED_UI_LANGUAGE_CODES)
APP_STATE_DIR_NAME = "ForzaAutoShift"
PORTABLE_MARKER_FILE = "portable"


def _resolve_executable_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    if sys.argv and sys.argv[0]:
        return Path(sys.argv[0]).resolve().parent
    return Path.cwd()


def _resolve_state_file_path() -> Path:
    executable_dir = _resolve_executable_dir()
    if (executable_dir / PORTABLE_MARKER_FILE).exists():
        return executable_dir / APP_STATE_FILE_NAME

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    base_dir = (
        Path(local_app_data) if local_app_data else (Path.home() / "AppData" / "Local")
    )
    return base_dir / APP_STATE_DIR_NAME / APP_STATE_FILE_NAME


DEFAULT_AT_CONFIG_VALUES: dict[str, object] = {
    "upshift_curve": DEFAULT_UPSHIFT_CURVE,
    "downshift_curve": DEFAULT_DOWNSHIFT_CURVE,
    "min_shift_rpm_gap": DEFAULT_MIN_SHIFT_RPM_GAP,
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
                "upshift_curve": [
                    {"throttle": 0.0, "rpm_ratio": 0.21},
                    {"throttle": 0.45, "rpm_ratio": 0.58},
                    {"throttle": 1.0, "rpm_ratio": 0.92},
                ],
                "downshift_curve": [
                    {"throttle": 0.0, "rpm_ratio": 0.05},
                    {"throttle": 0.55, "rpm_ratio": 0.20},
                    {"throttle": 1.0, "rpm_ratio": 0.28},
                ],
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
                "upshift_curve": [
                    {"throttle": 0.0, "rpm_ratio": 0.33},
                    {"throttle": 0.45, "rpm_ratio": 0.70},
                    {"throttle": 1.0, "rpm_ratio": 0.95},
                ],
                "downshift_curve": [
                    {"throttle": 0.0, "rpm_ratio": 0.06},
                    {"throttle": 0.55, "rpm_ratio": 0.28},
                    {"throttle": 1.0, "rpm_ratio": 0.46},
                ],
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
                "upshift_curve": [
                    {"throttle": 0.0, "rpm_ratio": 0.40},
                    {"throttle": 0.40, "rpm_ratio": 0.78},
                    {"throttle": 1.0, "rpm_ratio": 1.00},
                ],
                "downshift_curve": [
                    {"throttle": 0.0, "rpm_ratio": 0.09},
                    {"throttle": 0.45, "rpm_ratio": 0.36},
                    {"throttle": 1.0, "rpm_ratio": 0.56},
                ],
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


UPSHIFT_CURVE_COLOR = QColor("#d94848")
DOWNSHIFT_CURVE_COLOR = QColor("#2d8cff")
UPSHIFT_CURVE_FILL_COLOR = QColor(217, 72, 72, 45)
DOWNSHIFT_CURVE_FILL_COLOR = QColor(45, 140, 255, 45)


class ShiftCurveChart(QWidget):
    """Interactive normalized throttle/RPM-ratio curve editor."""

    points_changed = Signal(list)

    def __init__(
        self,
        title: str,
        curve_color: QColor,
        fill_color: QColor,
        fill_above: bool = False,
    ) -> None:
        super().__init__()
        self._title = title
        self._curve_color = curve_color
        self._fill_color = fill_color
        self._fill_above = fill_above
        self._points: list[ShiftCurvePoint] = []
        self._selected_index = -1
        self._dragging = False
        self.setMinimumHeight(150)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    def points(self) -> list[ShiftCurvePoint]:
        return [
            ShiftCurvePoint(point.throttle, point.rpm_ratio)
            for point in self._points
        ]

    def set_points(self, points: list[ShiftCurvePoint]) -> None:
        self._points = sorted(
            [
                ShiftCurvePoint(
                    max(0.0, min(1.0, point.throttle)),
                    max(0.0, min(1.0, point.rpm_ratio)),
                )
                for point in points
            ],
            key=lambda point: point.throttle,
        )
        if self._points:
            self._selected_index = max(
                0,
                min(self._selected_index, len(self._points) - 1),
            )
        else:
            self._selected_index = -1
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._plot_rect()
        painter.fillRect(self.rect(), self.palette().window())
        painter.setPen(QPen(QColor("#666666"), 1))
        painter.drawRect(rect)
        painter.drawText(8, 18, self._title)
        painter.drawText(rect.left(), rect.bottom() + 18, "0%")
        painter.drawText(rect.right() - 28, rect.bottom() + 18, "100%")
        painter.drawText(rect.left() - 38, rect.top() + 8, "100%")
        painter.drawText(rect.left() - 28, rect.bottom(), "0%")

        if len(self._points) < 2:
            return

        path_points = [self._point_to_screen(point, rect) for point in self._points]
        fill_points: list[QPointF] = []
        if self._fill_above:
            fill_points = [
                QPointF(rect.left(), rect.top()),
                *path_points,
                QPointF(rect.right(), rect.top()),
            ]
        else:
            fill_points = [
                QPointF(rect.left(), rect.bottom()),
                *path_points,
                QPointF(rect.right(), rect.bottom()),
            ]
        painter.setBrush(self._fill_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygonF(fill_points))

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self._curve_color, 2))
        for index in range(len(path_points) - 1):
            painter.drawLine(path_points[index], path_points[index + 1])

        for index, point in enumerate(path_points):
            is_selected = index == self._selected_index
            painter.setBrush(QColor("#ffcc33") if is_selected else QColor("#ffffff"))
            painter.setPen(QPen(self._curve_color, 2))
            painter.drawEllipse(point, 5 if is_selected else 4, 5 if is_selected else 4)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            return
        index = self._nearest_point_index(event.position())
        if index >= 0:
            self._selected_index = index
            self._dragging = True
            self.setFocus()
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if not self._dragging or self._selected_index < 0:
            return
        self._move_selected_to(event.position())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        del event
        self._dragging = False

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if self._selected_index < 0 or not self._points:
            super().keyPressEvent(event)
            return
        throttle_step = 0.10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.01
        ratio_step = 0.025 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.005
        point = self._points[self._selected_index]
        key = event.key()
        if key == Qt.Key.Key_Left:
            point.throttle -= throttle_step
        elif key == Qt.Key.Key_Right:
            point.throttle += throttle_step
        elif key == Qt.Key.Key_Up:
            point.rpm_ratio += ratio_step
        elif key == Qt.Key.Key_Down:
            point.rpm_ratio -= ratio_step
        else:
            super().keyPressEvent(event)
            return
        self._replace_selected_point(point)

    def _plot_rect(self) -> QRectF:
        return QRectF(45, 28, max(80, self.width() - 60), max(60, self.height() - 55))

    def _point_to_screen(self, point: ShiftCurvePoint, rect: QRectF) -> QPointF:
        return QPointF(
            rect.left() + (point.throttle * rect.width()),
            rect.bottom() - (point.rpm_ratio * rect.height()),
        )

    def _screen_to_point(self, position: QPointF) -> ShiftCurvePoint:
        rect = self._plot_rect()
        throttle = (position.x() - rect.left()) / max(1.0, rect.width())
        ratio = (rect.bottom() - position.y()) / max(1.0, rect.height())
        return ShiftCurvePoint(
            max(0.0, min(1.0, throttle)),
            max(0.0, min(1.0, ratio)),
        )

    def _nearest_point_index(self, position: QPointF) -> int:
        rect = self._plot_rect()
        nearest_index = -1
        nearest_distance = 14.0
        for index, point in enumerate(self._points):
            screen_point = self._point_to_screen(point, rect)
            distance = (
                (screen_point.x() - position.x()) ** 2
                + (screen_point.y() - position.y()) ** 2
            ) ** 0.5
            if distance <= nearest_distance:
                nearest_distance = distance
                nearest_index = index
        return nearest_index

    def _move_selected_to(self, position: QPointF) -> None:
        self._replace_selected_point(self._screen_to_point(position))

    def _replace_selected_point(self, point: ShiftCurvePoint) -> None:
        if self._selected_index < 0:
            return
        self._points[self._selected_index] = ShiftCurvePoint(
            max(0.0, min(1.0, point.throttle)),
            max(0.0, min(1.0, point.rpm_ratio)),
        )
        selected_point = self._points[self._selected_index]
        self._points.sort(key=lambda item: item.throttle)
        self._selected_index = self._points.index(selected_point)
        self.update()
        self.points_changed.emit(self.points())


class ShiftCurveEditor(QWidget):
    """Point table plus interactive chart for a single shift curve."""

    points_changed = Signal()

    def __init__(
        self,
        title: str,
        curve_color: QColor,
        fill_color: QColor,
        throttle_header: str,
        rpm_header: str,
        remove_tooltip: str,
        fill_above: bool = False,
    ) -> None:
        super().__init__()
        self._suppress_table_updates = False
        self._remove_tooltip = remove_tooltip
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.chart = ShiftCurveChart(
            title,
            curve_color=curve_color,
            fill_color=fill_color,
            fill_above=fill_above,
        )
        self.chart.points_changed.connect(self._on_chart_points_changed)
        layout.addWidget(self.chart)

        self.table = QTableWidget(0, 3)
        self.table.setItemDelegate(OpaqueTableEditDelegate(self.table))
        self.table.setHorizontalHeaderLabels([throttle_header, rpm_header, ""])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.itemChanged.connect(self._on_table_item_changed)
        layout.addWidget(self.table)

        actions = QHBoxLayout()
        self.add_button = QPushButton("+")
        self.add_button.clicked.connect(self._add_point)
        actions.addWidget(self.add_button)
        actions.addStretch()
        layout.addLayout(actions)

    def points(self) -> list[ShiftCurvePoint]:
        return self.chart.points()

    def set_points(self, points: list[ShiftCurvePoint]) -> None:
        self.chart.set_points(points)
        self._refresh_table(points)

    def set_edit_enabled(self, enabled: bool) -> None:
        self.chart.setEnabled(enabled)
        self.table.setEnabled(enabled)
        self.add_button.setEnabled(enabled)

    def _refresh_table(self, points: list[ShiftCurvePoint]) -> None:
        self._suppress_table_updates = True
        try:
            self.table.setRowCount(0)
            for point in points:
                row = self.table.rowCount()
                self.table.insertRow(row)
                throttle_item = QTableWidgetItem(f"{point.throttle * 100.0:.0f}")
                ratio_item = QTableWidgetItem(f"{point.rpm_ratio * 100.0:.1f}")
                self.table.setItem(row, 0, throttle_item)
                self.table.setItem(row, 1, ratio_item)
                remove_button = QPushButton("×")
                remove_button.setMaximumWidth(28)
                remove_button.setToolTip(self._remove_tooltip)
                remove_button.setEnabled(
                    not self._is_endpoint_throttle(point.throttle)
                )
                remove_button.clicked.connect(
                    lambda _checked=False, throttle=point.throttle: self._remove_point_by_throttle(
                        throttle
                    )
                )
                self.table.setCellWidget(row, 2, remove_button)
            self._update_table_height()
        finally:
            self._suppress_table_updates = False

    def _on_chart_points_changed(self, points: list[ShiftCurvePoint]) -> None:
        self._refresh_table(points)
        self.points_changed.emit()

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        del item
        if self._suppress_table_updates:
            return
        points: list[ShiftCurvePoint] = []
        has_invalid_value = False
        for row in range(self.table.rowCount()):
            try:
                throttle = float(self.table.item(row, 0).text()) / 100.0
                ratio = float(self.table.item(row, 1).text()) / 100.0
            except (AttributeError, TypeError, ValueError):
                has_invalid_value = True
                continue
            points.append(
                ShiftCurvePoint(
                    max(0.0, min(1.0, throttle)),
                    max(0.0, min(1.0, ratio)),
                )
            )
        if has_invalid_value:
            QTimer.singleShot(0, self._refresh_table_from_chart)
            return
        if len(points) >= 2:
            self.chart.set_points(points)
            QTimer.singleShot(0, self._refresh_table_from_chart)
            self.points_changed.emit()

    def _refresh_table_from_chart(self) -> None:
        self._refresh_table(self.chart.points())

    def _add_point(self) -> None:
        points = self.points()
        points.append(ShiftCurvePoint(0.5, 0.5))
        points.sort(key=lambda point: point.throttle)
        self.set_points(points)
        self.points_changed.emit()

    @staticmethod
    def _is_endpoint_throttle(throttle: float) -> bool:
        return throttle <= 0.0001 or throttle >= 0.9999

    def _remove_point_by_throttle(self, throttle: float) -> None:
        if self._is_endpoint_throttle(throttle) or len(self.points()) <= 2:
            return
        points = [
            point
            for point in self.points()
            if abs(point.throttle - throttle) > 0.0001
        ]
        if len(points) < 2:
            return
        self.set_points(points)
        self.points_changed.emit()

    def _update_table_height(self) -> None:
        row_height = self.table.verticalHeader().defaultSectionSize()
        if self.table.rowCount() > 0:
            row_height = self.table.rowHeight(0)
        visible_rows = min(float(self.table.rowCount()), 3.5)
        height = (
            self.table.horizontalHeader().height()
            + int(row_height * visible_rows)
            + (self.table.frameWidth() * 2)
            + 4
        )
        self.table.setFixedHeight(height)


class OpaqueTableEditDelegate(QStyledItemDelegate):
    """Creates opaque table editors so item text does not show behind edits."""

    def createEditor(self, parent, option, index):  # type: ignore[override]
        editor = super().createEditor(parent, option, index)
        if editor is not None:
            editor.setAutoFillBackground(True)
            palette = editor.palette()
            background = palette.color(QPalette.ColorRole.Base).name()
            foreground = palette.color(QPalette.ColorRole.Text).name()
            editor.setStyleSheet(
                f"background-color: {background}; color: {foreground};"
            )
        return editor


class ShiftCurvePreviewChart(QWidget):
    """Combined RPM preview for upshift/downshift curves."""

    def __init__(self) -> None:
        super().__init__()
        self._upshift_curve: list[ShiftCurvePoint] = []
        self._downshift_curve: list[ShiftCurvePoint] = []
        self._idle_rpm = 900.0
        self._max_rpm = 7000.0
        self._cursor_throttle: float | None = None
        self.setMinimumHeight(240)
        self.setMouseTracking(True)

    def set_preview(
        self,
        upshift_curve: list[ShiftCurvePoint],
        downshift_curve: list[ShiftCurvePoint],
        idle_rpm: float,
        max_rpm: float,
    ) -> None:
        self._upshift_curve = upshift_curve
        self._downshift_curve = downshift_curve
        self._idle_rpm = idle_rpm
        self._max_rpm = max_rpm
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(55, 22, max(100, self.width() - 75), max(100, self.height() - 55))
        painter.fillRect(self.rect(), self.palette().window())
        painter.setPen(QPen(QColor("#666666"), 1))
        painter.drawRect(rect)
        painter.drawText(8, 16, "Preview")
        painter.drawText(rect.left(), rect.bottom() + 18, "0%")
        painter.drawText(rect.right() - 30, rect.bottom() + 18, "100%")
        painter.drawText(4, rect.top() + 8, f"{self._max_rpm:.0f}")
        painter.drawText(4, rect.bottom(), f"{self._idle_rpm:.0f}")
        self._draw_curve(painter, rect, self._downshift_curve, DOWNSHIFT_CURVE_COLOR)
        self._draw_curve(painter, rect, self._upshift_curve, UPSHIFT_CURVE_COLOR)
        if self._cursor_throttle is not None:
            x = rect.left() + self._cursor_throttle * rect.width()
            painter.setPen(QPen(QColor("#ffcc33"), 1))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            return
        rect = self._plot_rect()
        self._cursor_throttle = self._throttle_at_position(event.position(), rect)
        self._show_curve_tooltip(event, self._cursor_throttle)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        rect = self._plot_rect()
        hover_throttle = self._throttle_at_position(event.position(), rect)
        tooltip_throttle = (
            self._cursor_throttle
            if self._cursor_throttle is not None
            else hover_throttle
        )
        self._show_curve_tooltip(event, tooltip_throttle)

    def _plot_rect(self) -> QRectF:
        return QRectF(55, 22, max(100, self.width() - 75), max(100, self.height() - 55))

    @staticmethod
    def _throttle_at_position(position: QPointF, rect: QRectF) -> float:
        return max(
            0.0,
            min(1.0, (position.x() - rect.left()) / max(1.0, rect.width())),
        )

    def _show_curve_tooltip(self, event: QMouseEvent, throttle: float) -> None:
        upshift_rpm = shift_curve_target_rpm(
            self._upshift_curve,
            throttle,
            self._idle_rpm,
            self._max_rpm,
        )
        downshift_rpm = shift_curve_target_rpm(
            self._downshift_curve,
            throttle,
            self._idle_rpm,
            self._max_rpm,
        )
        tooltip = (
            f"Throttle {throttle * 100.0:.0f}% | Up {upshift_rpm:.0f} RPM | Down {downshift_rpm:.0f} RPM"
        )
        self.setToolTip(tooltip)
        QToolTip.showText(event.globalPosition().toPoint(), tooltip, self)

    def _draw_curve(
        self,
        painter: QPainter,
        rect: QRectF,
        curve: list[ShiftCurvePoint],
        color: QColor,
    ) -> None:
        if len(curve) < 2 or self._max_rpm <= self._idle_rpm:
            return
        painter.setPen(QPen(color, 2))
        points: list[QPointF] = []
        for index in range(101):
            throttle = index / 100.0
            rpm = shift_curve_target_rpm(
                curve,
                throttle,
                self._idle_rpm,
                self._max_rpm,
            )
            x = rect.left() + throttle * rect.width()
            y_ratio = (rpm - self._idle_rpm) / (self._max_rpm - self._idle_rpm)
            y = rect.bottom() - y_ratio * rect.height()
            points.append(QPointF(x, y))
        for index in range(len(points) - 1):
            painter.drawLine(points[index], points[index + 1])


class MainWindow(QMainWindow):
    """Minimal GUI shell for starting/stopping the core app loop."""

    hotkey_pressed = Signal(object)
    hotkey_released = Signal(object)
    worker_config_update_requested = Signal(object, str, str)

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

    @staticmethod
    def _normalize_ui_language_code(language_code: str) -> str:
        code = language_code.strip().lower()
        if not code:
            return AUTO_LANGUAGE_CODE
        if code in UI_LANGUAGE_CODES:
            return code
        return AUTO_LANGUAGE_CODE

    def _load_initial_ui_language_preference(self) -> str:
        if not self._state_file_path.exists():
            return AUTO_LANGUAGE_CODE
        try:
            state = load_state_file(self._state_file_path)
        except (OSError, json.JSONDecodeError, ValueError):
            return AUTO_LANGUAGE_CODE
        raw_code = state.get("ui_language", AUTO_LANGUAGE_CODE)
        if not isinstance(raw_code, str):
            return AUTO_LANGUAGE_CODE
        return self._normalize_ui_language_code(raw_code)

    def _t(self, key: str, default: str) -> str:
        _ = key
        translated = QCoreApplication.translate(self.__class__.__name__, key, default)
        if translated == key:
            return default
        return translated

    def _refresh_language_selector_labels(self) -> None:
        self.ui_language_input.blockSignals(True)
        self.ui_language_input.clear()
        self.ui_language_input.addItem(
            self._t("language.auto", "Auto (System)"),
            AUTO_LANGUAGE_CODE,
        )
        self.ui_language_input.addItem(
            self._t("language.en", "English"),
            "en",
        )
        self.ui_language_input.addItem(
            self._t("language.es", "Español"),
            "es",
        )
        self.ui_language_input.addItem(
            self._t("language.fr", "Français"),
            "fr",
        )
        self.ui_language_input.addItem(
            self._t("language.de", "Deutsch"),
            "de",
        )
        self.ui_language_input.addItem(
            self._t("language.zh_cn", "简体中文"),
            "zh_cn",
        )
        self.ui_language_input.addItem(
            self._t("language.zh_tw", "繁體中文"),
            "zh_tw",
        )
        self.ui_language_input.addItem(
            self._t("language.ja_jp", "日本語"),
            "ja_jp",
        )
        current_index = self.ui_language_input.findData(self._ui_language)
        if current_index < 0:
            current_index = 0
        self.ui_language_input.setCurrentIndex(current_index)
        self.ui_language_input.blockSignals(False)

    def __init__(self) -> None:
        super().__init__()
        self.resize(1100, 650)
        self.setMinimumSize(760, 480)

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
        self._car_binding_remove_buttons: list[QPushButton] = []
        self._suppress_car_binding_updates = False
        self._suppress_preset_auto_apply = False
        self._last_detected_car_key = ""
        self._state_file_path = _resolve_state_file_path()
        self._ui_language = self._load_initial_ui_language_preference()
        self._play_worker_chime_enabled = True
        self._chime_start_effect = None
        self._chime_stop_effect = None

        self.setWindowTitle(self._t("app.window_title", "Forza Auto Shift"))

        root = QWidget(self)
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)

        # Tab widget for settings
        tabs = QTabWidget()

        # ===== CONNECTION GROUP (in Options tab) =====
        connection_group = QGroupBox(self._t("group.connection", "Connection"))
        connection_form = QFormLayout(connection_group)
        self._set_compact_form(connection_form)
        self.listen_address_input = QLineEdit("0.0.0.0")
        self.listen_address_input.setMaximumWidth(180)
        connection_form.addRow(
            self._t("label.listen_address", "Listen address:"),
            self.listen_address_input,
        )
        self.port_input = FocusWheelSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(DEFAULT_TELEMETRY_PORT)
        self._set_compact_numeric_input(self.port_input)
        connection_form.addRow(self._t("label.udp_port", "UDP port:"), self.port_input)

        relay_group = QGroupBox(self._t("group.udp_relay", "UDP Relay"))
        relay_layout = QVBoxLayout(relay_group)
        self.relay_enabled_checkbox = QCheckBox(
            self._t(
                "checkbox.relay_enable",
                "Enable relay of telemetry UDP packets",
            )
        )
        self.relay_enabled_checkbox.setChecked(False)
        self.relay_enabled_checkbox.toggled.connect(self._on_relay_enabled_toggled)
        relay_layout.addWidget(self.relay_enabled_checkbox)

        relay_hint = QLabel(self._t("label.relay_targets", "Relay targets (ip:port):"))
        relay_layout.addWidget(relay_hint)

        self.relay_targets_list = QListWidget()
        self.relay_targets_list.setMinimumHeight(100)
        relay_layout.addWidget(self.relay_targets_list)

        relay_buttons_row = QHBoxLayout()
        self.relay_add_target_button = QPushButton(
            self._t("button.add_target", "Add Target")
        )
        self.relay_add_target_button.clicked.connect(self._add_relay_target)
        relay_buttons_row.addWidget(self.relay_add_target_button)
        self.relay_remove_target_button = QPushButton(
            self._t("button.remove_selected", "Remove Selected")
        )
        self.relay_remove_target_button.clicked.connect(
            self._remove_selected_relay_target
        )
        relay_buttons_row.addWidget(self.relay_remove_target_button)
        relay_buttons_row.addStretch(1)
        relay_layout.addLayout(relay_buttons_row)
        connection_form.addRow(relay_group)

        # ===== INPUT GROUP (in Options tab) =====
        input_group = QGroupBox(self._t("group.input", "Input"))
        input_form = QFormLayout(input_group)
        self._set_compact_form(input_form)
        self.dry_run_checkbox = QCheckBox(
            self._t("checkbox.dry_run", "Dry Run (no key press)")
        )
        self.dry_run_checkbox.setChecked(True)
        input_form.addRow(self.dry_run_checkbox)
        self.focus_guard_checkbox = QCheckBox(
            self._t("checkbox.require_focus", "Require Forza window focus")
        )
        self.focus_guard_checkbox.setChecked(True)
        input_form.addRow(self.focus_guard_checkbox)
        shift_down_row = QHBoxLayout()
        self.record_shift_down_button = QPushButton(
            self._t("button.bind_down_key", "Bind Down Key")
        )
        self.record_shift_down_button.setFixedWidth(130)
        self.record_shift_down_button.clicked.connect(
            self._start_shift_down_key_recording
        )
        self.shift_down_key_label = QLabel(self._shift_down_key_name)
        self.shift_down_key_label.setMinimumWidth(70)
        shift_down_row.addWidget(self.shift_down_key_label)
        shift_down_row.addStretch(1)
        shift_down_row.addWidget(self.record_shift_down_button)
        input_form.addRow(
            self._t("label.shift_down_key", "Shift down key:"), shift_down_row
        )
        shift_up_row = QHBoxLayout()
        self.record_shift_up_button = QPushButton(
            self._t("button.bind_up_key", "Bind Up Key")
        )
        self.record_shift_up_button.setFixedWidth(130)
        self.record_shift_up_button.clicked.connect(self._start_shift_up_key_recording)
        self.shift_up_key_label = QLabel(self._shift_up_key_name)
        self.shift_up_key_label.setMinimumWidth(70)
        shift_up_row.addWidget(self.shift_up_key_label)
        shift_up_row.addStretch(1)
        shift_up_row.addWidget(self.record_shift_up_button)
        input_form.addRow(self._t("label.shift_up_key", "Shift up key:"), shift_up_row)

        # ===== TUNING TAB WITH PRESET EDITOR =====
        tuning_widget = QWidget()
        tuning_tab_layout = QHBoxLayout(tuning_widget)
        tuning_splitter = QSplitter(Qt.Orientation.Horizontal)

        preset_editor_group = QGroupBox(self._t("group.preset_editor", "Preset Editor"))
        preset_editor_layout = QVBoxLayout(preset_editor_group)
        self.preset_list = QListWidget()
        self.preset_list.setMinimumWidth(140)
        self.preset_list.currentItemChanged.connect(self._on_preset_selected)
        preset_editor_layout.addWidget(self.preset_list)

        preset_editor_actions_top = QHBoxLayout()
        self.preset_create_button = QPushButton(self._t("button.create", "Create"))
        self.preset_create_button.clicked.connect(self._save_current_as_preset)
        preset_editor_actions_top.addWidget(self.preset_create_button)
        self.preset_duplicate_button = QPushButton(
            self._t("button.duplicate", "Duplicate")
        )
        self.preset_duplicate_button.clicked.connect(self._duplicate_selected_preset)
        preset_editor_actions_top.addWidget(self.preset_duplicate_button)
        preset_editor_layout.addLayout(preset_editor_actions_top)

        preset_editor_actions_bottom = QHBoxLayout()
        self.preset_rename_button = QPushButton(self._t("button.rename", "Rename"))
        self.preset_rename_button.clicked.connect(self._rename_selected_preset)
        preset_editor_actions_bottom.addWidget(self.preset_rename_button)
        self.preset_save_button = QPushButton(self._t("button.save", "Save"))
        self.preset_save_button.clicked.connect(self._save_selected_preset)
        preset_editor_actions_bottom.addWidget(self.preset_save_button)
        self.preset_delete_button = QPushButton(self._t("button.delete", "Delete"))
        self.preset_delete_button.clicked.connect(self._delete_selected_preset)
        preset_editor_actions_bottom.addWidget(self.preset_delete_button)
        preset_editor_layout.addLayout(preset_editor_actions_bottom)

        preset_editor_group.setMinimumWidth(180)
        tuning_splitter.addWidget(preset_editor_group)

        tuning_fields_widget = QWidget()
        tuning_layout = QVBoxLayout(tuning_fields_widget)

        # RPM Maps group
        rpm_group = CollapsibleBox(
            self._t("tuning.group.rpm", "Shift Curves"), collapsed=False
        )
        curve_layout = QHBoxLayout()
        curve_editor_layout = QVBoxLayout()
        self.upshift_curve_editor = ShiftCurveEditor(
            self._t("tuning.upshift_curve", "Upshift curve"),
            curve_color=UPSHIFT_CURVE_COLOR,
            fill_color=UPSHIFT_CURVE_FILL_COLOR,
            throttle_header=self._t("tuning.curve_table.throttle", "Throttle %"),
            rpm_header=self._t("tuning.curve_table.rpm", "RPM %"),
            remove_tooltip=self._t("tuning.curve_table.remove", "Remove point"),
            fill_above=True,
        )
        self.upshift_curve_editor.points_changed.connect(self._refresh_curve_preview)
        curve_editor_layout.addWidget(self.upshift_curve_editor)
        self.downshift_curve_editor = ShiftCurveEditor(
            self._t("tuning.downshift_curve", "Downshift curve"),
            curve_color=DOWNSHIFT_CURVE_COLOR,
            fill_color=DOWNSHIFT_CURVE_FILL_COLOR,
            throttle_header=self._t("tuning.curve_table.throttle", "Throttle %"),
            rpm_header=self._t("tuning.curve_table.rpm", "RPM %"),
            remove_tooltip=self._t("tuning.curve_table.remove", "Remove point"),
            fill_above=False,
        )
        self.downshift_curve_editor.points_changed.connect(self._refresh_curve_preview)
        curve_editor_layout.addWidget(self.downshift_curve_editor)
        curve_layout.addLayout(curve_editor_layout, 3)

        preview_panel = QGroupBox(self._t("tuning.curve_preview", "Preview"))
        preview_layout = QVBoxLayout(preview_panel)
        preview_form = QFormLayout()
        self._set_compact_form(preview_form)
        self.preview_idle_rpm_input = FocusWheelSpinBox()
        self.preview_idle_rpm_input.setRange(300, 4000)
        self.preview_idle_rpm_input.setValue(900)
        self.preview_idle_rpm_input.valueChanged.connect(self._refresh_curve_preview)
        self._set_compact_numeric_input(self.preview_idle_rpm_input)
        preview_form.addRow(
            self._t("tuning.preview_idle_rpm", "Idle RPM:"),
            self.preview_idle_rpm_input,
        )
        self.preview_max_rpm_input = FocusWheelSpinBox()
        self.preview_max_rpm_input.setRange(1000, 20000)
        self.preview_max_rpm_input.setValue(7000)
        self.preview_max_rpm_input.valueChanged.connect(self._refresh_curve_preview)
        self._set_compact_numeric_input(self.preview_max_rpm_input)
        preview_form.addRow(
            self._t("tuning.preview_max_rpm", "Max RPM:"),
            self.preview_max_rpm_input,
        )
        self.min_shift_gap_input = FocusWheelSpinBox()
        self.min_shift_gap_input.setRange(0, 3000)
        self.min_shift_gap_input.setValue(int(DEFAULT_MIN_SHIFT_RPM_GAP))
        self.min_shift_gap_input.valueChanged.connect(self._refresh_curve_preview)
        self._set_compact_numeric_input(self.min_shift_gap_input)
        preview_form.addRow(
            self._t("tuning.min_shift_gap", "Minimum gap (RPM):"),
            self.min_shift_gap_input,
        )
        preview_layout.addLayout(preview_form)
        self.preview_gap_label = QLabel("")
        preview_layout.addWidget(self.preview_gap_label)
        self.shift_curve_preview_chart = ShiftCurvePreviewChart()
        preview_layout.addWidget(self.shift_curve_preview_chart, 1)
        curve_layout.addWidget(preview_panel, 2)
        rpm_group.content_layout.addRow(curve_layout)
        tuning_layout.addWidget(rpm_group)

        # Cooldown & Basic Shift group
        shift_group = CollapsibleBox(
            self._t("tuning.group.shift", "Shift Cooldown & Basic Behavior"),
            collapsed=False,
        )
        self._set_compact_form(shift_group.content_layout)
        self.cooldown_input = FocusWheelDoubleSpinBox()
        self.cooldown_input.setRange(0.05, 2.00)
        self.cooldown_input.setSingleStep(0.05)
        self.cooldown_input.setValue(0.35)
        self._set_compact_numeric_input(self.cooldown_input)
        shift_group.addRow(
            self._t("tuning.shift_cooldown", "Shift cooldown (s):"),
            self.cooldown_input,
        )
        self.enable_dwell_checkbox = QCheckBox(
            self._t("tuning.enable_dwell", "Enable dwell")
        )
        self.enable_dwell_checkbox.setChecked(False)
        shift_group.addRow(self.enable_dwell_checkbox)
        tuning_layout.addWidget(shift_group)

        # Dwell Timing group
        dwell_group = CollapsibleBox(
            self._t("tuning.group.dwell", "Dwell Timing"), collapsed=True
        )
        self._set_compact_form(dwell_group.content_layout)
        self.dwell_up_input = FocusWheelDoubleSpinBox()
        self.dwell_up_input.setRange(0.0, 2.0)
        self.dwell_up_input.setSingleStep(0.05)
        self.dwell_up_input.setValue(0.0)
        self._set_compact_numeric_input(self.dwell_up_input)
        dwell_group.addRow(
            self._t("tuning.dwell_up", "Dwell after upshift (s):"),
            self.dwell_up_input,
        )
        self.dwell_down_input = FocusWheelDoubleSpinBox()
        self.dwell_down_input.setRange(0.0, 2.0)
        self.dwell_down_input.setSingleStep(0.05)
        self.dwell_down_input.setValue(0.0)
        self._set_compact_numeric_input(self.dwell_down_input)
        dwell_group.addRow(
            self._t("tuning.dwell_down", "Dwell after downshift (s):"),
            self.dwell_down_input,
        )
        self.dwell_kickdown_input = FocusWheelDoubleSpinBox()
        self.dwell_kickdown_input.setRange(0.0, 2.0)
        self.dwell_kickdown_input.setSingleStep(0.05)
        self.dwell_kickdown_input.setValue(0.0)
        self._set_compact_numeric_input(self.dwell_kickdown_input)
        dwell_group.addRow(
            self._t("tuning.dwell_kickdown", "Dwell after kickdown (s):"),
            self.dwell_kickdown_input,
        )
        tuning_layout.addWidget(dwell_group)

        # Kickdown Tuning group
        kickdown_group = CollapsibleBox(
            self._t("tuning.group.kickdown", "Kickdown Tuning"), collapsed=True
        )
        self._set_compact_form(kickdown_group.content_layout)
        self.kickdown_threshold_input = FocusWheelDoubleSpinBox()
        self.kickdown_threshold_input.setRange(0.0, 1.0)
        self.kickdown_threshold_input.setSingleStep(0.01)
        self.kickdown_threshold_input.setValue(0.88)
        self._set_compact_numeric_input(self.kickdown_threshold_input)
        kickdown_group.addRow(
            self._t("tuning.kickdown_threshold", "Kickdown throttle threshold:"),
            self.kickdown_threshold_input,
        )
        self.kickdown_max_rpm_input = FocusWheelSpinBox()
        self.kickdown_max_rpm_input.setRange(500, 12000)
        self.kickdown_max_rpm_input.setValue(5200)
        self._set_compact_numeric_input(self.kickdown_max_rpm_input)
        kickdown_group.addRow(
            self._t("tuning.kickdown_max_rpm", "Kickdown max RPM:"),
            self.kickdown_max_rpm_input,
        )
        self.kickdown_lockout_input = FocusWheelDoubleSpinBox()
        self.kickdown_lockout_input.setRange(0.0, 3.0)
        self.kickdown_lockout_input.setSingleStep(0.05)
        self.kickdown_lockout_input.setValue(1.10)
        self._set_compact_numeric_input(self.kickdown_lockout_input)
        kickdown_group.addRow(
            self._t(
                "tuning.kickdown_lockout",
                "Kickdown lockout after upshift (s):",
            ),
            self.kickdown_lockout_input,
        )
        tuning_layout.addWidget(kickdown_group)

        # Unload Guard group
        unload_group = CollapsibleBox(
            self._t("tuning.group.unload", "Unload Upshift Guard"),
            collapsed=True,
        )
        self._set_compact_form(unload_group.content_layout)
        self.enable_unload_guard_checkbox = QCheckBox(
            self._t("tuning.enable_unload_guard", "Enable unload upshift guard")
        )
        self.enable_unload_guard_checkbox.setChecked(True)
        unload_group.addRow(self.enable_unload_guard_checkbox)
        self.unload_threshold_input = FocusWheelDoubleSpinBox()
        self.unload_threshold_input.setRange(0.0, 1.0)
        self.unload_threshold_input.setSingleStep(0.01)
        self.unload_threshold_input.setValue(0.12)
        self._set_compact_numeric_input(self.unload_threshold_input)
        unload_group.addRow(
            self._t("tuning.unload_threshold", "Unload suspension threshold:"),
            self.unload_threshold_input,
        )
        self.unload_guard_duration_input = FocusWheelDoubleSpinBox()
        self.unload_guard_duration_input.setRange(0.0, 2.0)
        self.unload_guard_duration_input.setSingleStep(0.05)
        self.unload_guard_duration_input.setValue(0.35)
        self._set_compact_numeric_input(self.unload_guard_duration_input)
        unload_group.addRow(
            self._t(
                "tuning.unload_guard_duration",
                "Unload guard lockout duration (s):",
            ),
            self.unload_guard_duration_input,
        )
        self.unload_min_throttle_input = FocusWheelDoubleSpinBox()
        self.unload_min_throttle_input.setRange(0.0, 1.0)
        self.unload_min_throttle_input.setSingleStep(0.01)
        self.unload_min_throttle_input.setValue(0.45)
        self._set_compact_numeric_input(self.unload_min_throttle_input)
        unload_group.addRow(
            self._t("tuning.unload_min_throttle", "Unload guard min throttle:"),
            self.unload_min_throttle_input,
        )
        tuning_layout.addWidget(unload_group)

        # Slip Guard group
        slip_group = CollapsibleBox(
            self._t("tuning.group.slip", "Slip Upshift Guard"), collapsed=True
        )
        self._set_compact_form(slip_group.content_layout)
        self.enable_slip_guard_checkbox = QCheckBox(
            self._t("tuning.enable_slip_guard", "Enable slip upshift guard")
        )
        self.enable_slip_guard_checkbox.setChecked(True)
        slip_group.addRow(self.enable_slip_guard_checkbox)
        self.slip_threshold_input = FocusWheelDoubleSpinBox()
        self.slip_threshold_input.setRange(0.0, 2.0)
        self.slip_threshold_input.setSingleStep(0.01)
        self.slip_threshold_input.setValue(0.28)
        self._set_compact_numeric_input(self.slip_threshold_input)
        slip_group.addRow(
            self._t("tuning.slip_threshold", "Slip ratio threshold:"),
            self.slip_threshold_input,
        )
        self.slip_guard_duration_input = FocusWheelDoubleSpinBox()
        self.slip_guard_duration_input.setRange(0.0, 2.0)
        self.slip_guard_duration_input.setSingleStep(0.05)
        self.slip_guard_duration_input.setValue(0.30)
        self._set_compact_numeric_input(self.slip_guard_duration_input)
        slip_group.addRow(
            self._t(
                "tuning.slip_guard_duration",
                "Slip guard lockout duration (s):",
            ),
            self.slip_guard_duration_input,
        )
        self.slip_min_throttle_input = FocusWheelDoubleSpinBox()
        self.slip_min_throttle_input.setRange(0.0, 1.0)
        self.slip_min_throttle_input.setSingleStep(0.01)
        self.slip_min_throttle_input.setValue(0.45)
        self._set_compact_numeric_input(self.slip_min_throttle_input)
        slip_group.addRow(
            self._t("tuning.slip_min_throttle", "Slip guard min throttle:"),
            self.slip_min_throttle_input,
        )
        tuning_layout.addWidget(slip_group)

        tuning_layout.addStretch()
        tuning_scroll = QScrollArea()
        tuning_scroll.setWidget(tuning_fields_widget)
        tuning_scroll.setWidgetResizable(True)
        tuning_scroll.setMinimumWidth(320)
        tuning_splitter.addWidget(tuning_scroll)
        tuning_splitter.setStretchFactor(0, 0)
        tuning_splitter.setStretchFactor(1, 1)
        tuning_splitter.setSizes([260, 820])
        tuning_tab_layout.addWidget(tuning_splitter)
        tabs.addTab(tuning_widget, self._t("tabs.tuning", "Tuning"))

        # ===== PRESETS TAB =====
        presets_widget = QWidget()
        presets_layout = QVBoxLayout(presets_widget)
        presets_group = QGroupBox(
            self._t("group.preset_car_binding", "Preset-Car Binding")
        )
        presets_form = QFormLayout(presets_group)
        self._set_compact_form(presets_form)
        self.binding_default_preset_combo = QComboBox()
        self.binding_default_preset_combo.setMaximumWidth(220)
        self.binding_default_preset_combo.currentTextChanged.connect(
            self._on_binding_default_preset_changed
        )
        presets_form.addRow(
            self._t("label.default_for_new_cars", "Default for new cars:"),
            self.binding_default_preset_combo,
        )
        presets_layout.addWidget(presets_group)

        car_group = QGroupBox(
            self._t("group.car_preset_assignments", "Car Preset Assignments")
        )
        car_group_layout = QVBoxLayout(car_group)
        car_filter_row = QHBoxLayout()
        car_filter_row.addWidget(QLabel(self._t("label.filter", "Filter:")))
        self.car_filter_combo = QComboBox()
        self.car_filter_combo.addItems(["All", "FH4", "FH5", "FH6", "FM"])
        self.car_filter_combo.setCurrentText("All")
        self.car_filter_combo.setMaximumWidth(120)
        self.car_filter_combo.currentTextChanged.connect(self._on_car_filter_changed)
        car_filter_row.addWidget(self.car_filter_combo)
        car_filter_row.addStretch(1)
        car_group_layout.addLayout(car_filter_row)
        self.car_binding_table = QTableWidget(0, 5)
        self.car_binding_table.setHorizontalHeaderLabels(
            [
                self._t("car.table.game", "Game"),
                self._t("car.table.id", "ID"),
                self._t("car.table.alias", "Alias"),
                self._t("car.table.preset", "Preset"),
                self._t("car.table.actions", "Actions"),
            ]
        )
        self.car_binding_table.verticalHeader().setVisible(False)
        self.car_binding_table.setAlternatingRowColors(True)
        self.car_binding_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.car_binding_table.setMinimumHeight(160)
        self.car_binding_table.itemChanged.connect(self._on_car_table_item_changed)
        car_table_header = self.car_binding_table.horizontalHeader()
        car_table_header.setSectionResizeMode(
            CAR_TABLE_GAME_COLUMN, QHeaderView.ResizeMode.ResizeToContents
        )
        car_table_header.setSectionResizeMode(
            CAR_TABLE_ID_COLUMN, QHeaderView.ResizeMode.ResizeToContents
        )
        car_table_header.setSectionResizeMode(
            CAR_TABLE_ALIAS_COLUMN, QHeaderView.ResizeMode.Stretch
        )
        car_table_header.setSectionResizeMode(
            CAR_TABLE_PRESET_COLUMN, QHeaderView.ResizeMode.ResizeToContents
        )
        car_table_header.setSectionResizeMode(
            CAR_TABLE_ACTIONS_COLUMN, QHeaderView.ResizeMode.ResizeToContents
        )
        car_group_layout.addWidget(self.car_binding_table)
        presets_layout.addWidget(car_group, 1)
        tabs.addTab(presets_widget, self._t("tabs.presets", "Presets"))

        # ===== OPTIONS TAB =====
        options_widget = QWidget()
        options_layout = QVBoxLayout(options_widget)
        options_group = QGroupBox(self._t("group.general", "General"))
        options_form = QFormLayout(options_group)
        self._set_compact_form(options_form)
        self.record_hotkey_button = QPushButton(
            self._t("button.record_hotkey", "Record Hotkey")
        )
        self.record_hotkey_button.clicked.connect(self._start_hotkey_recording)
        options_form.addRow(
            self._t("label.global_hotkey", "Global hotkey:"),
            self.record_hotkey_button,
        )
        self.log_level_input = QComboBox()
        self.log_level_input.addItems(["DEBUG", "INFO", "WARN", "ERROR"])
        self.log_level_input.setCurrentText("INFO")
        self.log_level_input.setMaximumWidth(110)
        options_form.addRow(
            self._t("label.log_level", "Log level:"), self.log_level_input
        )
        self.ui_language_input = QComboBox()
        self.ui_language_input.setMaximumWidth(180)
        self._refresh_language_selector_labels()
        self.ui_language_input.currentIndexChanged.connect(self._on_ui_language_changed)
        options_form.addRow(
            self._t("label.ui_language", "UI language:"),
            self.ui_language_input,
        )
        options_layout.addWidget(connection_group)
        options_layout.addWidget(input_group)
        options_layout.addWidget(options_group)

        audio_group = QGroupBox(self._t("group.audio", "Audio"))
        audio_form = QFormLayout(audio_group)
        self._set_compact_form(audio_form)
        self.play_worker_chime_checkbox = QCheckBox(
            self._t(
                "checkbox.play_worker_chime",
                "Play chime when worker starts/stops",
            )
        )
        self.play_worker_chime_checkbox.setChecked(self._play_worker_chime_enabled)
        self.play_worker_chime_checkbox.toggled.connect(
            self._on_play_worker_chime_toggled
        )
        audio_form.addRow(self.play_worker_chime_checkbox)
        options_layout.addWidget(audio_group)

        self._set_relay_ui_enabled(False)
        options_layout.addStretch()
        options_scroll = QScrollArea()
        options_scroll.setWidget(options_widget)
        options_scroll.setWidgetResizable(True)
        tabs.addTab(options_scroll, self._t("tabs.options", "Options"))

        # ===== LOG TAB =====
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_actions = QHBoxLayout()
        self.clear_log_button = QPushButton(self._t("button.clear_log", "Clear Log"))
        self.clear_log_button.clicked.connect(self._clear_log)
        log_actions.addWidget(self.clear_log_button)
        self.save_log_button = QPushButton(self._t("button.save_log", "Save Log"))
        self.save_log_button.clicked.connect(self._save_log_to_file)
        log_actions.addWidget(self.save_log_button)
        log_actions.addStretch(1)
        log_layout.addLayout(log_actions)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        log_layout.addWidget(self.log_view)
        tabs.addTab(log_widget, self._t("tabs.log", "Log"))

        self._settings_tabs = tabs
        main_layout.addWidget(tabs)

        # ===== CONTROLS BAR =====
        controls = QHBoxLayout()
        controls.addStretch(1)
        self.hotkey_label = QLabel(
            f"{self._t('hotkey.prefix', 'Hotkey')}: {self._get_hotkey_name()}"
        )
        controls.addWidget(self.hotkey_label)
        self.start_button = QPushButton(self._t("button.start", "Start"))
        self.start_button.clicked.connect(self.start_worker)
        controls.addWidget(self.start_button)
        self.stop_button = QPushButton(self._t("button.stop", "Stop"))
        self.stop_button.clicked.connect(self.stop_worker)
        self.stop_button.setEnabled(False)
        controls.addWidget(self.stop_button)
        main_layout.addLayout(controls)

        self.statusBar().showMessage(
            self._t(
                "status.idle",
                "Status: Idle | Telemetry: Waiting | Focus: N/A | Game: Unknown | Latency: N/A",
            )
        )

        main_layout.setStretch(0, 1)

        self.hotkey_pressed.connect(self._handle_hotkey_press)
        self.hotkey_released.connect(self._handle_hotkey_release)
        self._setup_chime_effects()

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
        relay_enabled = self.relay_enabled_checkbox.isChecked()
        relay_targets = self._relay_targets_from_ui()
        require_focus_guard = self.focus_guard_checkbox.isChecked()
        shift_down_scan_code = self._shift_down_scan_code
        shift_up_scan_code = self._shift_up_scan_code
        log_level = self.log_level_input.currentText()

        thread = QThread(self)
        worker = AutoShiftWorker(
            bind_host=bind_host,
            port=port,
            dry_run=dry_run,
            relay_enabled=relay_enabled,
            relay_targets=relay_targets,
            require_focus_guard=require_focus_guard,
            shift_down_scan_code=shift_down_scan_code,
            shift_up_scan_code=shift_up_scan_code,
            shift_down_key_name=self._shift_down_key_name,
            shift_up_key_name=self._shift_up_key_name,
            log_level=log_level,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.log.connect(self.append_log)
        worker.status.connect(self.set_status)
        worker.car_detected.connect(self._on_car_detected)
        worker.car_config_requested.connect(self._on_car_config_requested)
        self.worker_config_update_requested.connect(
            worker.update_at_config,
            Qt.ConnectionType.DirectConnection,
        )
        worker.finished.connect(self.on_worker_finished)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)

        self._thread = thread
        self._worker = worker

        self._set_input_controls_enabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

        thread.start()
        self._play_worker_chime("start")

    def _relay_targets_from_ui(self) -> list[tuple[str, int]]:
        targets: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()
        for i in range(self.relay_targets_list.count()):
            item = self.relay_targets_list.item(i)
            if item is None:
                continue
            parsed = self._parse_relay_target(item.text())
            if parsed is None:
                continue
            if parsed in seen:
                continue
            seen.add(parsed)
            targets.append(parsed)
        return targets

    @staticmethod
    def _parse_relay_target(target: str) -> tuple[str, int] | None:
        text = target.strip()
        if not text or ":" not in text:
            return None
        host, port_text = text.rsplit(":", 1)
        host = host.strip()
        if not host:
            return None
        try:
            port = int(port_text)
        except ValueError:
            return None
        if port < 1 or port > 65535:
            return None
        return host, port

    def _build_at_config_from_values(
        self, values: dict[str, object]
    ) -> AutomaticTransmissionConfig:
        return AutomaticTransmissionConfig(
            upshift_curve=normalize_shift_curve(
                values.get("upshift_curve"),
                DEFAULT_UPSHIFT_CURVE,
            ),
            downshift_curve=normalize_shift_curve(
                values.get("downshift_curve"),
                DEFAULT_DOWNSHIFT_CURVE,
            ),
            min_shift_rpm_gap=float(values["min_shift_rpm_gap"]),
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

    def _build_at_config_from_editor(self) -> AutomaticTransmissionConfig:
        return self._build_at_config_from_values(self._collect_full_tuning_values())

    def _resolve_car_at_config(
        self, car_key: str
    ) -> tuple[AutomaticTransmissionConfig | None, str]:
        if not car_key:
            raise ValueError("Cannot resolve AT config for an empty car key.")
        default_preset = self._default_preset_name()
        if car_key not in self._car_preset_map:
            self._car_preset_map[car_key] = default_preset
        preset_name = self._car_preset_map.get(car_key, default_preset)
        if preset_name == DISABLED_PRESET_NAME:
            return None, DISABLED_PRESET_NAME
        if preset_name not in self._preset_store:
            preset_name = default_preset
            self._car_preset_map[car_key] = preset_name

        preset_values = self._normalize_preset_values(
            self._preset_store.get(preset_name)
        )
        return self._build_at_config_from_values(preset_values), preset_name

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
        self._set_tuning_fields_enabled(not active_is_builtin)
        # Preset editor + binding tab
        self.preset_list.setEnabled(True)
        self.preset_create_button.setEnabled(True)
        self.preset_duplicate_button.setEnabled(
            bool(self._active_preset_name)
        )
        self.preset_rename_button.setEnabled(bool(self._active_preset_name))
        self.preset_save_button.setEnabled(bool(self._active_preset_name))
        self.binding_default_preset_combo.setEnabled(True)
        self.car_binding_table.setEnabled(True)
        for combo in self._car_binding_preset_combos:
            combo.setEnabled(True)
        for button in self._car_binding_remove_buttons:
            button.setEnabled(True)
        self._update_preset_delete_button_state()
        # Hotkey and log level
        self.log_level_input.setEnabled(enabled)
        self.ui_language_input.setEnabled(enabled)
        self.record_hotkey_button.setEnabled(enabled)
        relay_enabled = enabled and self.relay_enabled_checkbox.isChecked()
        self.relay_enabled_checkbox.setEnabled(enabled)
        self._set_relay_ui_enabled(relay_enabled)

    def _setup_chime_effects(self) -> None:
        if QSoundEffect is None:
            return

        assets_dir = Path(__file__).resolve().parent / "assets"
        start_path = assets_dir / "on.wav"
        stop_path = assets_dir / "off.wav"

        if start_path.exists():
            self._chime_start_effect = QSoundEffect(self)
            self._chime_start_effect.setSource(
                QUrl.fromLocalFile(str(start_path.resolve()))
            )
            # self._chime_start_effect.setVolume(0.60)

        if stop_path.exists():
            self._chime_stop_effect = QSoundEffect(self)
            self._chime_stop_effect.setSource(
                QUrl.fromLocalFile(str(stop_path.resolve()))
            )
            # self._chime_stop_effect.setVolume(0.60)

    def _play_worker_chime(self, event: str) -> None:
        if not self._play_worker_chime_enabled:
            return

        effect = (
            self._chime_start_effect if event == "start" else self._chime_stop_effect
        )
        if effect is not None:
            effect.play()

    @Slot(bool)
    def _on_play_worker_chime_toggled(self, checked: bool) -> None:
        self._play_worker_chime_enabled = bool(checked)
        self._save_app_state()

    @Slot(int)
    def _on_ui_language_changed(self, _index: int) -> None:
        selected = self.ui_language_input.currentData()
        if not isinstance(selected, str):
            return
        normalized = self._normalize_ui_language_code(selected)
        if normalized == self._ui_language:
            return
        self._ui_language = normalized
        self._refresh_language_selector_labels()
        self.append_log(
            self._t(
                "log.language_changed_restart",
                "[INFO] UI language updated. Restart the app to apply all text changes.",
            )
        )
        self._save_app_state()

    def _set_relay_ui_enabled(self, enabled: bool) -> None:
        self.relay_targets_list.setEnabled(enabled)
        self.relay_add_target_button.setEnabled(enabled)
        self.relay_remove_target_button.setEnabled(enabled)

    @Slot(bool)
    def _on_relay_enabled_toggled(self, checked: bool) -> None:
        self._set_relay_ui_enabled(bool(checked))
        self._save_app_state()

    @Slot()
    def _add_relay_target(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(
            self._t("dialog.add_relay_target.title", "Add Relay Target")
        )
        layout = QFormLayout(dialog)

        ip_input = QLineEdit(dialog)
        ip_input.setPlaceholderText("127.0.0.1")
        layout.addRow(self._t("dialog.add_relay_target.ip", "IP:"), ip_input)

        port_input = FocusWheelSpinBox(dialog)
        port_input.setRange(1, 65535)
        port_input.setValue(DEFAULT_TELEMETRY_PORT)
        self._set_compact_numeric_input(port_input)
        layout.addRow(self._t("dialog.add_relay_target.port", "Port:"), port_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        ip = ip_input.text().strip()
        if not ip:
            return

        target = f"{ip}:{int(port_input.value())}"
        if ":" not in target:
            QMessageBox.warning(
                self,
                self._t("dialog.invalid_target.title", "Invalid Target"),
                self._t(
                    "dialog.invalid_target.message",
                    "Target must be in the form ip:port.",
                ),
                QMessageBox.StandardButton.Ok,
            )
            return
        if self.relay_targets_list.findItems(target, Qt.MatchFlag.MatchExactly):
            return
        self.relay_targets_list.addItem(target)
        self._save_app_state()

    @Slot()
    def _remove_selected_relay_target(self) -> None:
        current_item = self.relay_targets_list.currentItem()
        if current_item is None:
            return
        row = self.relay_targets_list.row(current_item)
        self.relay_targets_list.takeItem(row)
        self._save_app_state()

    def _set_tuning_fields_enabled(self, enabled: bool) -> None:
        self.upshift_curve_editor.set_edit_enabled(enabled)
        self.downshift_curve_editor.set_edit_enabled(enabled)
        self.min_shift_gap_input.setEnabled(enabled)
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
            self._t("dialog.save_log.title", "Save Log"),
            str(Path.cwd() / default_name),
            self._t(
                "dialog.save_log.filter",
                "Text Files (*.txt);;All Files (*)",
            ),
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
        self.statusBar().showMessage(f"{self._t('status.prefix', 'Status')}: {status}")

    @Slot()
    def on_worker_finished(self) -> None:
        self._worker = None
        self._thread = None

        self._set_input_controls_enabled(True)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._play_worker_chime("stop")
        self._save_app_state()

    def _get_hotkey_name(self) -> str:
        """Get a friendly name for the current hotkey combination (modifiers first)."""
        if self._current_hotkey is None or not self._current_hotkey:
            return self._t("hotkey.not_set", "Not set")
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

    def _refresh_curve_preview(self) -> None:
        idle_rpm = float(self.preview_idle_rpm_input.value())
        max_rpm = float(self.preview_max_rpm_input.value())
        if max_rpm <= idle_rpm:
            max_rpm = idle_rpm + 1000.0
        upshift_curve = self.upshift_curve_editor.points()
        downshift_curve = self.downshift_curve_editor.points()
        minimum_gap = minimum_shift_curve_gap(
            upshift_curve,
            downshift_curve,
            idle_rpm,
            max_rpm,
        )
        minimum_ratio_gap = minimum_shift_curve_ratio_gap(
            upshift_curve,
            downshift_curve,
        )
        required_gap = float(self.min_shift_gap_input.value())
        self.shift_curve_preview_chart.set_preview(
            upshift_curve,
            downshift_curve,
            idle_rpm,
            max_rpm,
        )
        if minimum_ratio_gap <= 0.0:
            self.preview_gap_label.setText(
                self._t(
                    "tuning.curve_intersection_warning",
                    "Invalid: upshift curve must stay above downshift curve.",
                )
            )
        elif minimum_gap >= required_gap:
            self.preview_gap_label.setText(
                self._t(
                    "tuning.curve_gap_ok",
                    "Minimum supported gap: {gap:.0f} RPM",
                ).format(gap=minimum_gap)
            )
        else:
            self.preview_gap_label.setText(
                self._t(
                    "tuning.curve_gap_warning",
                    "Warning: minimum gap is {gap:.0f} RPM, below {required:.0f} RPM.",
                ).format(gap=minimum_gap, required=required_gap)
            )

    def _collect_tuning_values(self) -> dict[str, object]:
        return {
            "upshift_curve": serialize_shift_curve(
                self.upshift_curve_editor.points()
            ),
            "downshift_curve": serialize_shift_curve(
                self.downshift_curve_editor.points()
            ),
            "min_shift_rpm_gap": int(self.min_shift_gap_input.value()),
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
        normalized = normalize_preset_values(values, DEFAULT_AT_CONFIG_VALUES)
        normalized["upshift_curve"] = serialize_shift_curve(
            normalize_shift_curve(
                normalized.get("upshift_curve"),
                DEFAULT_UPSHIFT_CURVE,
            )
        )
        normalized["downshift_curve"] = serialize_shift_curve(
            normalize_shift_curve(
                normalized.get("downshift_curve"),
                DEFAULT_DOWNSHIFT_CURVE,
            )
        )
        normalized["min_shift_rpm_gap"] = float(
            normalized.get("min_shift_rpm_gap", DEFAULT_MIN_SHIFT_RPM_GAP)
        )
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

    def _apply_tuning_values(self, values: dict[str, object]) -> None:
        self.upshift_curve_editor.set_points(
            normalize_shift_curve(values.get("upshift_curve"), DEFAULT_UPSHIFT_CURVE)
        )
        self.downshift_curve_editor.set_points(
            normalize_shift_curve(
                values.get("downshift_curve"),
                DEFAULT_DOWNSHIFT_CURVE,
            )
        )
        self.min_shift_gap_input.setValue(
            int(values.get("min_shift_rpm_gap", self.min_shift_gap_input.value()))
        )
        self._refresh_curve_preview()
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
        relay_targets = [
            self.relay_targets_list.item(i).text().strip()
            for i in range(self.relay_targets_list.count())
            if self.relay_targets_list.item(i) is not None
            and self.relay_targets_list.item(i).text().strip()
        ]
        return build_app_state_payload(
            listen_address=self.listen_address_input.text().strip(),
            udp_port=int(self.port_input.value()),
            dry_run=bool(self.dry_run_checkbox.isChecked()),
            relay_enabled=bool(self.relay_enabled_checkbox.isChecked()),
            relay_targets=relay_targets,
            focus_guard=bool(self.focus_guard_checkbox.isChecked()),
            play_worker_chime=bool(self.play_worker_chime_checkbox.isChecked()),
            ui_language=self._ui_language,
            log_level=self.log_level_input.currentText(),
            shift_down_scan_code=int(self._shift_down_scan_code),
            shift_up_scan_code=int(self._shift_up_scan_code),
            shift_down_key_name=self._shift_down_key_name,
            shift_up_key_name=self._shift_up_key_name,
            hotkey_tokens=hotkey_tokens,
            current_tuning=current_tuning,
            active_preset=self._active_preset_name,
            default_binding_preset=self._default_binding_preset_name,
            preset_store=self._preset_store,
            builtin_preset_names=set(BUILTIN_PRESET_TEMPLATES.keys()),
            car_preset_map=self._car_preset_map,
            car_alias_map=self._car_alias_map,
            split_car_key=self._split_car_key,
        )

    def _apply_app_state(self, state: dict[str, object]) -> None:
        parsed = parse_app_state_payload(
            state=state,
            valid_ui_languages=set(UI_LANGUAGE_CODES),
            valid_log_levels=set(LOG_LEVEL_ORDER.keys()),
            parse_relay_target=self._parse_relay_target,
            normalize_preset=self._normalize_preset_values,
            car_storage_key=self._car_storage_key,
        )

        self.listen_address_input.setText(parsed.listen_address)
        self.port_input.setValue(parsed.udp_port)
        self.dry_run_checkbox.setChecked(parsed.dry_run)
        self.relay_targets_list.clear()
        for target in parsed.relay_targets:
            self.relay_targets_list.addItem(target)
        self.relay_enabled_checkbox.blockSignals(True)
        self.relay_enabled_checkbox.setChecked(parsed.relay_enabled)
        self.relay_enabled_checkbox.blockSignals(False)
        self._set_relay_ui_enabled(parsed.relay_enabled)
        self.focus_guard_checkbox.setChecked(parsed.focus_guard)
        self._play_worker_chime_enabled = parsed.play_worker_chime
        self.play_worker_chime_checkbox.blockSignals(True)
        self.play_worker_chime_checkbox.setChecked(parsed.play_worker_chime)
        self.play_worker_chime_checkbox.blockSignals(False)
        self._ui_language = parsed.ui_language
        self._refresh_language_selector_labels()
        self.log_level_input.setCurrentText(parsed.log_level)

        self._shift_down_scan_code = parsed.shift_down_scan_code
        self._shift_up_scan_code = parsed.shift_up_scan_code
        self._shift_down_key_name = parsed.shift_down_key_name
        self._shift_up_key_name = parsed.shift_up_key_name
        self.shift_down_key_label.setText(self._shift_down_key_name)
        self.shift_up_key_label.setText(self._shift_up_key_name)

        loaded_hotkeys: list[object] = []
        loaded_hotkeys.extend(
            self._token_to_key(token) for token in parsed.hotkey_tokens
        )
        filtered_keys = [
            key
            for key in (self._canonical_hotkey_key(k) for k in loaded_hotkeys)
            if key
        ]
        if filtered_keys:
            self._current_hotkey = frozenset(filtered_keys)
        self.hotkey_label.setText(
            f"{self._t('hotkey.prefix', 'Hotkey')}: {self._get_hotkey_name()}"
        )

        self._apply_tuning_values(parsed.current_tuning)
        self._preset_store = parsed.presets
        self._car_preset_map = parsed.car_preset_map
        self._car_alias_map = parsed.car_alias_map
        self._active_preset_name = parsed.active_preset
        self._default_binding_preset_name = parsed.default_binding_preset

    def _load_app_state(self) -> None:
        for name, template in BUILTIN_PRESET_TEMPLATES.items():
            self._preset_store[name] = self._normalize_preset_values(template)

        if not self._active_preset_name:
            self._active_preset_name = self._default_preset_name()
        if not self._default_binding_preset_name:
            self._default_binding_preset_name = DISABLED_PRESET_NAME

        if not self._state_file_path.exists():
            return

        try:
            data = load_state_file(self._state_file_path)
            self._apply_app_state(data)
            for name, template in BUILTIN_PRESET_TEMPLATES.items():
                self._preset_store[name] = self._normalize_preset_values(template)
            self._normalize_car_preset_map()
            if self._active_preset_name not in self._preset_store:
                self._active_preset_name = self._default_preset_name()
            if (
                self._default_binding_preset_name != DISABLED_PRESET_NAME
                and self._default_binding_preset_name not in self._preset_store
            ):
                self._default_binding_preset_name = self._default_preset_name()
            self.append_log(f"Loaded app state from {self._state_file_path.name}")
        except (OSError, json.JSONDecodeError) as exc:
            self.append_log(f"[WARN] Could not load app state: {exc}")
        except ValueError as exc:
            self.append_log(f"[WARN] Ignoring incompatible app state: {exc}")
            self._save_app_state()
            self.append_log(
                f"[INFO] Rewrote {self._state_file_path.name} to schema_version={SCHEMA_VERSION}."
            )

    def _save_app_state(self) -> None:
        state = self._collect_app_state()
        try:
            self._state_file_path.parent.mkdir(parents=True, exist_ok=True)
            save_state_file(self._state_file_path, state)
        except OSError as exc:
            self.append_log(f"[WARN] Could not save app state: {exc}")

    def _refresh_preset_selector(self) -> None:
        current = self._active_preset_name
        self._suppress_preset_auto_apply = True
        self.preset_list.blockSignals(True)
        self.binding_default_preset_combo.blockSignals(True)
        self.preset_list.clear()
        self.binding_default_preset_combo.clear()
        names = sorted_preset_names(self._preset_store)
        for name in names:
            self.preset_list.addItem(name)
        default_binding_names = [DISABLED_PRESET_NAME, *names]
        for name in default_binding_names:
            self.binding_default_preset_combo.addItem(name)
        self._active_preset_name = resolve_selected_name(current, names)
        if self._active_preset_name:
            index = names.index(self._active_preset_name)
            self.preset_list.setCurrentRow(index)

        self._default_binding_preset_name = resolve_selected_name(
            self._default_binding_preset_name,
            default_binding_names,
        )
        if self._default_binding_preset_name:
            index = default_binding_names.index(self._default_binding_preset_name)
            self.binding_default_preset_combo.setCurrentIndex(index)

        self.preset_list.blockSignals(False)
        self.binding_default_preset_combo.blockSignals(False)
        self._suppress_preset_auto_apply = False
        self._refresh_car_preset_list()
        self._update_preset_delete_button_state()

        if self._active_preset_name:
            self._load_preset_into_editor(self._active_preset_name)

    def _default_preset_name(self) -> str:
        return default_preset_name(
            preset_store=self._preset_store,
            default_binding_preset_name=self._default_binding_preset_name,
            default_car_preset_name=DEFAULT_CAR_PRESET_NAME,
            builtin_templates=BUILTIN_PRESET_TEMPLATES,
            normalize_preset_values=self._normalize_preset_values,
        )

    def _normalize_game_code(self, game_code: str) -> str:
        return normalize_game_code(game_code)

    def _car_storage_key(self, game_code: str, car_id: str) -> str:
        return car_storage_key(game_code, car_id)

    def _split_car_key(self, car_key: str) -> tuple[str, str]:
        return split_car_key(car_key)

    def _normalize_car_preset_map(self) -> None:
        normalize_car_preset_maps(
            car_preset_map=self._car_preset_map,
            car_alias_map=self._car_alias_map,
            preset_store=self._preset_store,
            fallback_preset=self._default_preset_name(),
        )

    @Slot(str)
    def _on_car_filter_changed(self, _value: str) -> None:
        self._refresh_car_preset_list()

    def _make_readonly_table_item(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def _refresh_car_preset_list(self) -> None:
        self._suppress_car_binding_updates = True
        self._car_binding_preset_combos.clear()
        self._car_binding_remove_buttons.clear()
        self.car_binding_table.clearContents()
        self.car_binding_table.setRowCount(0)

        if not self._car_preset_map:
            self._suppress_car_binding_updates = False
            return

        default_preset = self._default_preset_name()
        rows, normalized_updates = build_car_binding_rows(
            car_preset_map=self._car_preset_map,
            car_alias_map=self._car_alias_map,
            preset_store=self._preset_store,
            default_preset=default_preset,
            selected_filter=self.car_filter_combo.currentText(),
        )
        for car_key, preset_name in normalized_updates.items():
            self._car_preset_map[car_key] = preset_name

        if not rows:
            self._suppress_car_binding_updates = False
            return

        preset_names = [
            DISABLED_PRESET_NAME,
            *[
                preset_name
                for preset_name in sorted_preset_names(self._preset_store)
                if preset_name != DISABLED_PRESET_NAME
            ],
        ]
        self.car_binding_table.setRowCount(len(rows))
        for table_row, row in enumerate(rows):
            game_item = self._make_readonly_table_item(row.game_code)
            id_item = self._make_readonly_table_item(row.car_id)
            alias_item = QTableWidgetItem(self._car_alias_map.get(row.car_key, ""))
            alias_item.setData(Qt.ItemDataRole.UserRole, row.car_key)
            alias_item.setToolTip(
                self._t("car.table.alias_tooltip", "Edit alias; leave empty to reset")
            )

            self.car_binding_table.setItem(table_row, CAR_TABLE_GAME_COLUMN, game_item)
            self.car_binding_table.setItem(table_row, CAR_TABLE_ID_COLUMN, id_item)
            self.car_binding_table.setItem(table_row, CAR_TABLE_ALIAS_COLUMN, alias_item)

            preset_combo = QComboBox()
            preset_combo.setMinimumWidth(140)
            for preset_name in preset_names:
                preset_combo.addItem(preset_name)
            preset_combo.setCurrentText(row.preset_name)
            preset_combo.currentTextChanged.connect(
                lambda name, car_key=row.car_key: self._on_car_preset_changed(
                    car_key, name
                )
            )
            self._car_binding_preset_combos.append(preset_combo)
            self.car_binding_table.setCellWidget(
                table_row,
                CAR_TABLE_PRESET_COLUMN,
                preset_combo,
            )

            remove_button = QPushButton(self._t("button.delete", "Delete"))
            remove_button.setMinimumWidth(70)
            remove_button.clicked.connect(
                lambda _checked=False, car_key=row.car_key: self._remove_car_binding(
                    car_key
                )
            )
            self._car_binding_remove_buttons.append(remove_button)
            self.car_binding_table.setCellWidget(
                table_row,
                CAR_TABLE_ACTIONS_COLUMN,
                remove_button,
            )

        self._suppress_car_binding_updates = False

    @Slot(QTableWidgetItem)
    def _on_car_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._suppress_car_binding_updates:
            return
        if item.column() != CAR_TABLE_ALIAS_COLUMN:
            return

        car_key = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(car_key, str) or car_key not in self._car_preset_map:
            return

        alias = item.text().strip()
        game_code, car_id = self._split_car_key(car_key)
        if alias:
            self._car_alias_map[car_key] = alias
            self.append_log(f"[INFO] Car {game_code}-{car_id} alias set to '{alias}'.")
        else:
            self._car_alias_map.pop(car_key, None)
            self.append_log(f"[INFO] Car {game_code}-{car_id} alias reset to default.")
        self._save_app_state()

    def _emit_current_car_config_if_running(self) -> None:
        if (
            self._thread is None
            or self._worker is None
            or not self._last_detected_car_key
        ):
            return
        config, preset_name = self._resolve_car_at_config(self._last_detected_car_key)
        self.worker_config_update_requested.emit(
            config,
            preset_name,
            self._last_detected_car_key,
        )

    def _emit_current_car_config_if_using_preset(self, preset_name: str) -> None:
        if not self._last_detected_car_key:
            return
        if self._car_preset_map.get(self._last_detected_car_key) != preset_name:
            return
        self._emit_current_car_config_if_running()

    @Slot(str, int)
    def _on_car_config_requested(self, game_code: str, car_ordinal: int) -> None:
        car_id = str(car_ordinal)
        normalized_game_code = self._normalize_game_code(game_code)
        if not normalized_game_code:
            return
        default_preset = self._default_preset_name()
        car_key, was_new = upsert_detected_car(
            car_preset_map=self._car_preset_map,
            game_code=normalized_game_code,
            car_id=car_id,
            default_preset=default_preset,
        )
        if not car_key:
            return
        self._last_detected_car_key = car_key
        config, preset_name = self._resolve_car_at_config(car_key)
        if was_new:
            self._refresh_car_preset_list()
            self._save_app_state()
            self.append_log(
                f"[INFO] New car detected: {normalized_game_code}-{car_id}. Assigned default preset '{default_preset}'."
            )
        self.worker_config_update_requested.emit(config, preset_name, car_key)

    @Slot(int, str)
    def _on_car_detected(self, car_ordinal: int, game_code: str) -> None:
        car_id = str(car_ordinal)
        normalized_game_code = self._normalize_game_code(game_code)
        car_key = self._car_storage_key(
            normalized_game_code,
            car_id,
        )
        if car_key:
            self._last_detected_car_key = car_key

    @Slot(str, str)
    def _on_car_preset_changed(self, car_key: str, preset_name: str) -> None:
        if self._suppress_car_binding_updates:
            return
        if preset_name != DISABLED_PRESET_NAME and preset_name not in self._preset_store:
            return
        game_code, car_id = self._split_car_key(car_key)
        if not game_code:
            return
        self._car_preset_map[car_key] = preset_name
        self._save_app_state()
        self.append_log(
            f"[INFO] Car {game_code}-{car_id} assigned to preset '{preset_name}'."
        )
        if car_key == self._last_detected_car_key:
            self._emit_current_car_config_if_running()

    @Slot(str)
    def _remove_car_binding(self, car_key: str) -> None:
        if car_key not in self._car_preset_map:
            return
        game_code, car_id = self._split_car_key(car_key)
        if not game_code:
            return
        is_current_running_car = (
            car_key == self._last_detected_car_key
            and self._thread is not None
            and self._worker is not None
        )
        del self._car_preset_map[car_key]
        self._car_alias_map.pop(car_key, None)
        if is_current_running_car:
            self._emit_current_car_config_if_running()
        elif car_key == self._last_detected_car_key:
            self._last_detected_car_key = ""
        self._refresh_car_preset_list()
        self._save_app_state()
        self.append_log(f"[INFO] Removed car assignment for {game_code}-{car_id}.")

    def _update_preset_delete_button_state(self) -> None:
        name = self._active_preset_name.strip()
        if not self.preset_list.isEnabled():
            self.preset_delete_button.setEnabled(False)
            self.preset_delete_button.setToolTip(
                self._t("tooltip.unavailable_running", "Unavailable while running")
            )
            self.preset_rename_button.setEnabled(False)
            self.preset_duplicate_button.setEnabled(False)
            self.preset_save_button.setEnabled(False)
            self._set_tuning_fields_enabled(False)
            return
        if not name:
            self.preset_delete_button.setEnabled(False)
            self.preset_delete_button.setToolTip(
                self._t(
                    "tooltip.select_preset_editor",
                    "Select a preset in the editor list",
                )
            )
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
            self.preset_delete_button.setToolTip(
                self._t(
                    "tooltip.builtin_cannot_delete",
                    "Built-in presets cannot be deleted",
                )
            )
            self.preset_rename_button.setEnabled(False)
            self.preset_save_button.setEnabled(False)
            self._set_tuning_fields_enabled(False)
            return
        self.preset_delete_button.setEnabled(True)
        self.preset_delete_button.setToolTip(
            self._t("tooltip.delete_selected_custom", "Delete selected custom preset")
        )
        self._set_tuning_fields_enabled(True)

    @Slot(str)
    def _on_binding_default_preset_changed(self, name: str) -> None:
        if self._suppress_preset_auto_apply:
            return
        if not name:
            return
        if name != DISABLED_PRESET_NAME and name not in self._preset_store:
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

    def _reserved_preset_name_message(self, name: str) -> str:
        return self._t(
            "dialog.reserved_preset.message",
            "'{name}' is reserved and cannot be used as a custom preset name.",
        ).format(name=name)

    def _curve_intersection_error_message(self) -> str:
        return self._t(
            "dialog.curve_intersection.message",
            "Invalid shift curves: the upshift curve must stay above the downshift curve for every throttle value.",
        )

    def _validate_current_shift_curves_for_save(self) -> bool:
        upshift_curve = self.upshift_curve_editor.points()
        downshift_curve = self.downshift_curve_editor.points()
        if minimum_shift_curve_ratio_gap(upshift_curve, downshift_curve) > 0.0:
            return True
        message = self._curve_intersection_error_message()
        QMessageBox.warning(
            self,
            self._t("dialog.curve_intersection.title", "Invalid Shift Curves"),
            message,
            QMessageBox.StandardButton.Ok,
        )
        self.append_log(f"[WARN] {message}")
        return False

    def _warn_if_saved_preset_may_be_incompatible(self, preset_name: str) -> None:
        affected_cars = cars_assigned_to_preset(
            car_preset_map=self._car_preset_map,
            preset_name=preset_name,
        )
        if not affected_cars:
            return
        idle_rpm = float(self.preview_idle_rpm_input.value())
        max_rpm = float(self.preview_max_rpm_input.value())
        if max_rpm <= idle_rpm:
            return
        values = self._collect_full_tuning_values()
        upshift_curve = normalize_shift_curve(
            values.get("upshift_curve"),
            DEFAULT_UPSHIFT_CURVE,
        )
        downshift_curve = normalize_shift_curve(
            values.get("downshift_curve"),
            DEFAULT_DOWNSHIFT_CURVE,
        )
        minimum_gap = minimum_shift_curve_gap(
            upshift_curve,
            downshift_curve,
            idle_rpm,
            max_rpm,
        )
        required_gap = float(values.get("min_shift_rpm_gap", DEFAULT_MIN_SHIFT_RPM_GAP))
        if minimum_gap >= required_gap:
            return
        formatted_cars = ", ".join(sorted(format_car_keys(affected_cars)))
        message = self._t(
            "dialog.curve_incompatible.message",
            "Preset '{name}' is assigned to {cars}. With the preview RPM range, the minimum shift-curve gap is {gap:.0f} RPM, below {required:.0f} RPM.",
        ).format(
            name=preset_name,
            cars=formatted_cars,
            gap=minimum_gap,
            required=required_gap,
        )
        QMessageBox.warning(
            self,
            self._t("dialog.curve_incompatible.title", "Shift Curve Warning"),
            message,
            QMessageBox.StandardButton.Ok,
        )
        self.append_log(f"[WARN] {message}")

    @Slot()
    def _save_current_as_preset(self) -> None:
        default_name = self._active_preset_name.strip() or "my-preset"
        name, ok = QInputDialog.getText(
            self,
            self._t("dialog.create_preset.title", "Create Preset"),
            self._t("dialog.create_preset.prompt", "New preset name:"),
            text=default_name,
        )
        if not ok:
            return
        name = name.strip()
        validation_error = validate_new_preset_name(
            name=name,
            preset_store=self._preset_store,
            builtin_templates=BUILTIN_PRESET_TEMPLATES,
        )
        if validation_error == "Preset name cannot be empty.":
            QMessageBox.warning(
                self,
                self._t("dialog.invalid_preset_name.title", "Invalid Preset Name"),
                self._t(
                    "dialog.invalid_preset_name.message",
                    "Preset name cannot be empty.",
                ),
                QMessageBox.StandardButton.Ok,
            )
            self.append_log("[WARN] Preset name cannot be empty.")
            return
        if validation_error and "reserved" in validation_error.lower():
            message = self._reserved_preset_name_message(name)
            QMessageBox.warning(
                self,
                self._t("dialog.reserved_preset.title", "Reserved Preset Name"),
                message,
                QMessageBox.StandardButton.Ok,
            )
            self.append_log(f"[WARN] {message}")
            return
        if validation_error and "built-in preset" in validation_error.lower():
            QMessageBox.warning(
                self,
                self._t("dialog.builtin_preset.title", "Built-in Preset"),
                self._t(
                    "dialog.builtin_preset.message",
                    "'{name}' is a built-in preset and cannot be overwritten.\nPlease choose a different preset name.",
                ).format(name=name),
                QMessageBox.StandardButton.Ok,
            )
            self.append_log(
                f"[WARN] Built-in preset '{name}' cannot be overwritten. Choose another name."
            )
            return
        if validation_error and "already exists" in validation_error:
            answer = QMessageBox.question(
                self,
                self._t("dialog.preset_exists.title", "Preset Exists"),
                self._t(
                    "dialog.preset_exists.message",
                    "Preset '{name}' already exists. Choose another name.",
                ).format(name=name),
                QMessageBox.StandardButton.Ok,
            )
            if answer == QMessageBox.StandardButton.Ok:
                self.append_log("[INFO] Preset creation canceled.")
            return
        if not self._validate_current_shift_curves_for_save():
            return
        preset_data = self._collect_preset_payload()
        create_or_update_preset(
            preset_store=self._preset_store,
            name=name,
            preset_data=preset_data,
        )
        self._active_preset_name = name
        self._refresh_preset_selector()
        self._save_app_state()
        self.append_log(f"[INFO] Created preset '{name}'.")
        self._emit_current_car_config_if_using_preset(name)

    @Slot()
    def _save_selected_preset(self) -> None:
        name = self._active_preset_name.strip()
        if not name or name not in self._preset_store:
            self.append_log("[WARN] Select a preset to save.")
            return
        if name.lower() in BUILTIN_PRESET_TEMPLATES:
            self.append_log("[WARN] Built-in presets cannot be saved/overwritten.")
            return
        if not self._validate_current_shift_curves_for_save():
            return
        create_or_update_preset(
            preset_store=self._preset_store,
            name=name,
            preset_data=self._collect_preset_payload(),
        )
        self._save_app_state()
        self.append_log(f"[INFO] Saved preset '{name}'.")
        self._warn_if_saved_preset_may_be_incompatible(name)
        self._emit_current_car_config_if_using_preset(name)

    @Slot()
    def _duplicate_selected_preset(self) -> None:
        source_name = self._active_preset_name.strip()
        if not source_name or source_name not in self._preset_store:
            self.append_log("[WARN] Select a preset to duplicate.")
            return
        suggested_name = f"{source_name}-copy"
        name, ok = QInputDialog.getText(
            self,
            self._t("dialog.duplicate_preset.title", "Duplicate Preset"),
            self._t("dialog.duplicate_preset.prompt", "Duplicate preset name:"),
            text=suggested_name,
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            self.append_log("[WARN] Preset name cannot be empty.")
            return
        if name.casefold() == DISABLED_PRESET_NAME.casefold():
            self.append_log(f"[WARN] {self._reserved_preset_name_message(name)}")
            return
        if name in self._preset_store:
            self.append_log(f"[WARN] Preset '{name}' already exists.")
            return
        duplicate_preset(
            preset_store=self._preset_store,
            source_name=source_name,
            target_name=name,
        )
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
            self._t("dialog.rename_preset.title", "Rename Preset"),
            self._t("dialog.rename_preset.prompt", "New preset name:"),
            text=source_name,
        )
        if not ok:
            return
        new_name = new_name.strip()
        validation_error = validate_rename_preset(
            source_name=source_name,
            new_name=new_name,
            preset_store=self._preset_store,
            builtin_templates=BUILTIN_PRESET_TEMPLATES,
        )
        if validation_error == "":
            return
        if validation_error:
            if "reserved" in validation_error.lower():
                self.append_log(f"[WARN] {self._reserved_preset_name_message(new_name)}")
            else:
                self.append_log(f"[WARN] {validation_error}")
            return
        rename_preset_entry(
            preset_store=self._preset_store,
            source_name=source_name,
            new_name=new_name,
        )
        self._default_binding_preset_name = rename_preset_references(
            car_preset_map=self._car_preset_map,
            default_binding_preset_name=self._default_binding_preset_name,
            source_name=source_name,
            new_name=new_name,
        )
        self._active_preset_name = new_name
        self._refresh_preset_selector()
        self._save_app_state()
        self.append_log(f"[INFO] Renamed preset '{source_name}' to '{new_name}'.")
        self._emit_current_car_config_if_using_preset(new_name)

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

    def _current_editor_has_unsaved_preset_changes(self, preset_name: str) -> bool:
        if not preset_name or preset_name not in self._preset_store:
            return False
        saved_values = self._normalize_preset_values(self._preset_store[preset_name])
        current_values = self._normalize_preset_values(self._collect_full_tuning_values())
        return current_values != saved_values

    def _confirm_discard_unsaved_preset_changes(self, preset_name: str) -> bool:
        if not self._current_editor_has_unsaved_preset_changes(preset_name):
            return True
        answer = QMessageBox.question(
            self,
            self._t("dialog.unsaved_preset.title", "Unsaved Preset Changes"),
            self._t(
                "dialog.unsaved_preset.message",
                "Preset '{name}' has unsaved changes. Discard them and switch presets?",
            ).format(name=preset_name),
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def _restore_selected_preset_item(self, preset_name: str) -> None:
        self.preset_list.blockSignals(True)
        try:
            matches = self.preset_list.findItems(
                preset_name,
                Qt.MatchFlag.MatchExactly,
            )
            if matches:
                self.preset_list.setCurrentItem(matches[0])
            else:
                self.preset_list.clearSelection()
        finally:
            self.preset_list.blockSignals(False)

    @Slot(object, object)
    def _on_preset_selected(self, current: object, previous: object) -> None:
        name = current.text().strip() if current is not None else ""
        previous_name = (
            previous.text().strip()
            if previous is not None
            else self._active_preset_name.strip()
        )
        if (
            not self._suppress_preset_auto_apply
            and previous_name
            and name != previous_name
            and not self._confirm_discard_unsaved_preset_changes(previous_name)
        ):
            self._restore_selected_preset_item(previous_name)
            return

        self._active_preset_name = name
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
            affected_cars = cars_assigned_to_preset(
                car_preset_map=self._car_preset_map,
                preset_name=name,
            )
            current_car_affected = self._last_detected_car_key in affected_cars
            if affected_cars:
                default_preset = self._default_preset_name()
                formatted_cars = format_car_keys(affected_cars)
                answer = QMessageBox.question(
                    self,
                    self._t("dialog.delete_preset.title", "Delete Preset"),
                    self._t(
                        "dialog.delete_preset.message",
                        "Preset '{name}' is assigned to {count} car(s): {cars}.\n\nIf deleted, affected cars will be reassigned to '{default_preset}'. Continue?",
                    ).format(
                        name=name,
                        count=len(affected_cars),
                        cars=", ".join(sorted(formatted_cars)),
                        default_preset=default_preset,
                    ),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                reassign_cars_to_preset(
                    car_preset_map=self._car_preset_map,
                    car_keys=affected_cars,
                    preset_name=default_preset,
                )

            if self._active_preset_name == name:
                self._active_preset_name = self._default_preset_name()
            if self._default_binding_preset_name == name:
                self._default_binding_preset_name = self._default_preset_name()
            remove_preset_entry(preset_store=self._preset_store, name=name)
            self._refresh_preset_selector()
            self._save_app_state()
            self.append_log(f"[INFO] Deleted preset '{name}'.")
            if current_car_affected:
                self._emit_current_car_config_if_running()

    def _set_tuning_tooltips(self) -> None:
        self.upshift_curve_editor.setToolTip(
            self._t(
                "tooltip.tuning.upshift_curve",
                "Normalized upshift curve. X is throttle, Y is target RPM ratio between idle and max RPM.",
            )
        )
        self.downshift_curve_editor.setToolTip(
            self._t(
                "tooltip.tuning.downshift_curve",
                "Normalized downshift curve. X is throttle/brake demand, Y is target RPM ratio between idle and max RPM.",
            )
        )
        self._set_tooltip_with_label(
            self.min_shift_gap_input,
            self._t(
                "tooltip.tuning.min_shift_gap",
                "Minimum RPM gap required between upshift and downshift curves.",
            ),
        )
        self._set_tooltip_with_label(
            self.cooldown_input,
            self._t(
                "tooltip.tuning.cooldown",
                "Minimum time between shifts to avoid rapid gear hunting.",
            ),
        )
        self.enable_dwell_checkbox.setToolTip(
            self._t(
                "tooltip.tuning.enable_dwell",
                "Enable extra per-shift dwell hold times.",
            )
        )
        self._set_tooltip_with_label(
            self.dwell_up_input,
            self._t("tooltip.tuning.dwell_up", "Extra hold time after an upshift."),
        )
        self._set_tooltip_with_label(
            self.dwell_down_input,
            self._t(
                "tooltip.tuning.dwell_down",
                "Extra hold time after a downshift.",
            ),
        )
        self._set_tooltip_with_label(
            self.dwell_kickdown_input,
            self._t(
                "tooltip.tuning.dwell_kickdown",
                "Extra hold time after a kickdown downshift.",
            ),
        )
        self._set_tooltip_with_label(
            self.kickdown_threshold_input,
            self._t(
                "tooltip.tuning.kickdown_threshold",
                "Throttle threshold to allow kickdown downshifts.",
            ),
        )
        self._set_tooltip_with_label(
            self.kickdown_max_rpm_input,
            self._t(
                "tooltip.tuning.kickdown_max_rpm",
                "Maximum RPM where kickdown is allowed.",
            ),
        )
        self._set_tooltip_with_label(
            self.kickdown_lockout_input,
            self._t(
                "tooltip.tuning.kickdown_lockout",
                "Time to block kickdown immediately after upshift.",
            ),
        )
        self.enable_unload_guard_checkbox.setToolTip(
            self._t(
                "tooltip.tuning.enable_unload_guard",
                "Blocks upshift briefly when suspension unload indicates airborne/crest.",
            )
        )
        self._set_tooltip_with_label(
            self.unload_threshold_input,
            self._t(
                "tooltip.tuning.unload_threshold",
                "Normalized suspension travel threshold for unload detection.",
            ),
        )
        self._set_tooltip_with_label(
            self.unload_guard_duration_input,
            self._t(
                "tooltip.tuning.unload_guard_duration",
                "How long upshift stays blocked after unload is detected.",
            ),
        )
        self._set_tooltip_with_label(
            self.unload_min_throttle_input,
            self._t(
                "tooltip.tuning.unload_min_throttle",
                "Minimum throttle required before unload guard can trigger.",
            ),
        )
        self.enable_slip_guard_checkbox.setToolTip(
            self._t(
                "tooltip.tuning.enable_slip_guard",
                "Blocks upshift briefly when driven tire slip is high.",
            )
        )
        self._set_tooltip_with_label(
            self.slip_threshold_input,
            self._t(
                "tooltip.tuning.slip_threshold",
                "Slip ratio threshold used to trigger slip upshift guard.",
            ),
        )
        self._set_tooltip_with_label(
            self.slip_guard_duration_input,
            self._t(
                "tooltip.tuning.slip_guard_duration",
                "How long upshift stays blocked after slip trigger.",
            ),
        )
        self._set_tooltip_with_label(
            self.slip_min_throttle_input,
            self._t(
                "tooltip.tuning.slip_min_throttle",
                "Minimum throttle required before slip guard can trigger.",
            ),
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
        self.record_hotkey_button.setText(
            self._t("button.recording_hotkey", "Recording... (ESC to cancel)")
        )
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
        self.record_hotkey_button.setText(
            self._t("button.record_hotkey", "Record Hotkey")
        )
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
                self.record_hotkey_button.setText(
                    self._t("button.record_hotkey", "Record Hotkey")
                )
                self.record_hotkey_button.setEnabled(True)
                self.hotkey_label.setText(
                    f"{self._t('hotkey.prefix', 'Hotkey')}: {self._get_hotkey_name()}"
                )
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
    def _normalize_ui_language_code(language_code: str) -> str:
        code = language_code.strip().lower()
        if not code:
            return AUTO_LANGUAGE_CODE
        if code in UI_LANGUAGE_CODES:
            return code
        return AUTO_LANGUAGE_CODE

    def _resolve_effective_ui_language(selected_language: str) -> str:
        selected = _normalize_ui_language_code(selected_language)
        if selected != AUTO_LANGUAGE_CODE:
            return selected
        system_name = QLocale.system().name().strip().lower().replace("-", "_")
        if system_name in SUPPORTED_UI_LANGUAGE_CODES:
            return system_name
        base = system_name.split("_", 1)[0] if system_name else ""
        if base == "zh":
            return "zh_cn"
        if base == "ja":
            return "ja_jp"
        if base in SUPPORTED_UI_LANGUAGE_CODES:
            return base
        return "en"

    def _load_selected_ui_language_from_state() -> str:
        state_path = _resolve_state_file_path()
        if not state_path.exists():
            return AUTO_LANGUAGE_CODE
        try:
            state = load_state_file(state_path)
        except (OSError, json.JSONDecodeError, ValueError):
            return AUTO_LANGUAGE_CODE
        raw = state.get("ui_language", AUTO_LANGUAGE_CODE)
        if not isinstance(raw, str):
            return AUTO_LANGUAGE_CODE
        return _normalize_ui_language_code(raw)

    def _install_ui_translator(app: QApplication, selected_language: str) -> None:
        effective_language = _resolve_effective_ui_language(selected_language)
        if effective_language == "en":
            return
        qm_dir = Path(__file__).resolve().parent / "i18n"
        qm_path = qm_dir / f"forza_auto_shift_{effective_language}.qm"
        if not qm_path.exists():
            return
        translator = QTranslator(app)
        if not translator.load(str(qm_path.resolve())):
            return
        app.installTranslator(translator)
        # Keep a strong reference so translator is not garbage collected.
        setattr(app, "_ui_translator", translator)

    def _resolve_app_icon_path(app: QApplication) -> Path | None:
        assets_dir = Path(__file__).resolve().parent / "assets"
        light_icon = assets_dir / "icon_light.png"
        dark_icon = assets_dir / "icon_dark.png"

        # Use dark icon on light themes and light icon on dark themes.
        window_lightness = app.palette().color(QPalette.ColorRole.Window).lightness()
        preferred = dark_icon if window_lightness >= 128 else light_icon
        fallback = light_icon if preferred == dark_icon else dark_icon

        if preferred.exists():
            return preferred
        if fallback.exists():
            return fallback
        return None

    app = QApplication(sys.argv)
    _install_ui_translator(app, _load_selected_ui_language_from_state())
    icon_path = _resolve_app_icon_path(app)
    app_icon: QIcon | None = None
    if icon_path is not None:
        app_icon = QIcon(str(icon_path))
        app.setWindowIcon(app_icon)

    window = MainWindow()
    if app_icon is not None:
        window.setWindowIcon(app_icon)
    window.show()
    return app.exec()


def main() -> None:
    raise SystemExit(run_gui())
