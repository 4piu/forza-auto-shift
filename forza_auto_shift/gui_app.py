"""Minimal PySide6 GUI for the Forza Auto Shift core app."""

from __future__ import annotations

import sys
import time
import ctypes
import json
from pathlib import Path

from pynput import keyboard

from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    QLocale,
    Qt,
    QThread,
    QTranslator,
    Signal,
    Slot,
    QUrl,
)
from PySide6.QtGui import QIcon, QPalette
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtMultimedia import QSoundEffect
except Exception:
    QSoundEffect = None

from .auto_transmission import (
    AutomaticTransmissionConfig,
)
from .input_controller import SC_E, SC_Q
from .preset_binding_service import (
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
MAPVK_VK_TO_VSC = 0
DEFAULT_CAR_PRESET_NAME = "street"
AUTO_LANGUAGE_CODE = "auto"
SUPPORTED_UI_LANGUAGE_CODES = ("en", "es")
UI_LANGUAGE_CODES = (AUTO_LANGUAGE_CODE, *SUPPORTED_UI_LANGUAGE_CODES)

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
            self._t("language.es", "Spanish"),
            "es",
        )
        current_index = self.ui_language_input.findData(self._ui_language)
        if current_index < 0:
            current_index = 0
        self.ui_language_input.setCurrentIndex(current_index)
        self.ui_language_input.blockSignals(False)

    def __init__(self) -> None:
        super().__init__()
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

        preset_editor_group = QGroupBox(self._t("group.preset_editor", "Preset Editor"))
        preset_editor_layout = QVBoxLayout(preset_editor_group)
        self.preset_list = QListWidget()
        self.preset_list.setMinimumWidth(220)
        self.preset_list.currentTextChanged.connect(self._on_preset_selected)
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

        tuning_tab_layout.addWidget(preset_editor_group)

        tuning_fields_widget = QWidget()
        tuning_layout = QVBoxLayout(tuning_fields_widget)

        # RPM Maps group
        rpm_group = CollapsibleBox(
            self._t("tuning.group.rpm", "RPM Maps"), collapsed=False
        )
        self._set_compact_form(rpm_group.content_layout)
        self.upshift_low_input = FocusWheelSpinBox()
        self.upshift_low_input.setRange(500, 12000)
        self.upshift_low_input.setValue(2800)
        self._set_compact_numeric_input(self.upshift_low_input)
        rpm_group.addRow(
            self._t("tuning.upshift_low", "Upshift RPM (low throttle):"),
            self.upshift_low_input,
        )
        self.upshift_high_input = FocusWheelSpinBox()
        self.upshift_high_input.setRange(1000, 12000)
        self.upshift_high_input.setValue(7000)
        self._set_compact_numeric_input(self.upshift_high_input)
        rpm_group.addRow(
            self._t("tuning.upshift_high", "Upshift RPM (high throttle):"),
            self.upshift_high_input,
        )
        self.downshift_low_input = FocusWheelSpinBox()
        self.downshift_low_input.setRange(500, 12000)
        self.downshift_low_input.setValue(1100)
        self._set_compact_numeric_input(self.downshift_low_input)
        rpm_group.addRow(
            self._t("tuning.downshift_low", "Downshift RPM (low throttle):"),
            self.downshift_low_input,
        )
        self.downshift_high_input = FocusWheelSpinBox()
        self.downshift_high_input.setRange(500, 12000)
        self.downshift_high_input.setValue(3600)
        self._set_compact_numeric_input(self.downshift_high_input)
        rpm_group.addRow(
            self._t("tuning.downshift_high", "Downshift RPM (high throttle):"),
            self.downshift_high_input,
        )
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
        tuning_tab_layout.addWidget(tuning_scroll, 1)
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
        tabs.addTab(options_widget, self._t("tabs.options", "Options"))

        self._settings_tabs = tabs
        main_layout.addWidget(tabs)

        # ===== CONTROLS BAR =====
        controls = QHBoxLayout()
        self.clear_log_button = QPushButton(self._t("button.clear_log", "Clear Log"))
        self.clear_log_button.clicked.connect(self._clear_log)
        controls.addWidget(self.clear_log_button)
        self.save_log_button = QPushButton(self._t("button.save_log", "Save Log"))
        self.save_log_button.clicked.connect(self._save_log_to_file)
        controls.addWidget(self.save_log_button)
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

        # ===== LOG VIEW =====
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(divider)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        main_layout.addWidget(self.log_view)
        main_layout.setStretch(0, 3)
        main_layout.setStretch(3, 2)

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

        at_config = self._build_at_config_from_editor()

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
            self.preset_delete_button.setToolTip(
                self._t("tooltip.unavailable_running", "Unavailable while running")
            )
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
        return normalize_preset_values(values, DEFAULT_AT_CONFIG_VALUES)

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
            self._default_binding_preset_name = self._default_preset_name()

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
            if self._default_binding_preset_name not in self._preset_store:
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
            self.binding_default_preset_combo.addItem(name)
        self._active_preset_name = resolve_selected_name(current, names)
        if self._active_preset_name:
            index = names.index(self._active_preset_name)
            self.preset_list.setCurrentRow(index)

        self._default_binding_preset_name = resolve_selected_name(
            self._default_binding_preset_name,
            names,
        )
        if self._default_binding_preset_name:
            index = names.index(self._default_binding_preset_name)
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
            empty_label = QLabel(self._t("car.empty.none", "No cars detected yet."))
            empty_label.setStyleSheet("color: gray;")
            self.car_binding_rows_layout.addWidget(empty_label)
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
            empty_label = QLabel(self._t("car.empty.filter", "No cars in this filter."))
            empty_label.setStyleSheet("color: gray;")
            self.car_binding_rows_layout.addWidget(empty_label)
            self._suppress_car_binding_updates = False
            return

        preset_names = sorted_preset_names(self._preset_store)
        for row in rows:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            car_label = QLabel(row.display_name)
            car_label.setFixedWidth(210)
            car_label.setToolTip(f"{row.game_code}-{row.car_id}")
            row_layout.addWidget(car_label)

            alias_button = QPushButton(self._t("button.rename", "Rename"))
            alias_button.setFixedWidth(80)
            alias_button.clicked.connect(
                lambda _checked=False, car_key=row.car_key: self._rename_car_alias(
                    car_key
                )
            )
            self._car_binding_alias_buttons.append(alias_button)
            row_layout.addWidget(alias_button)

            preset_combo = QComboBox()
            preset_combo.setFixedWidth(180)
            for preset_name in preset_names:
                preset_combo.addItem(preset_name)
            preset_combo.setCurrentText(row.preset_name)
            preset_combo.currentTextChanged.connect(
                lambda name, car_key=row.car_key: self._on_car_preset_changed(
                    car_key, name
                )
            )
            self._car_binding_preset_combos.append(preset_combo)
            row_layout.addWidget(preset_combo)

            remove_button = QPushButton(self._t("button.delete", "Delete"))
            remove_button.setFixedWidth(80)
            remove_button.clicked.connect(
                lambda _checked=False, car_key=row.car_key: self._remove_car_binding(
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
        default_preset = self._default_preset_name()
        car_key, was_new = upsert_detected_car(
            car_preset_map=self._car_preset_map,
            game_code=normalized_game_code,
            car_id=car_id,
            default_preset=default_preset,
        )
        if was_new:
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
            self._t("dialog.set_car_alias.title", "Set Car Alias"),
            self._t(
                "dialog.set_car_alias.prompt",
                "Alias for {car} (empty resets):",
            ).format(car=f"{game_code}-{car_id}"),
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

    @Slot()
    def _save_selected_preset(self) -> None:
        name = self._active_preset_name.strip()
        if not name or name not in self._preset_store:
            self.append_log("[WARN] Select a preset to save.")
            return
        if name.lower() in BUILTIN_PRESET_TEMPLATES:
            self.append_log("[WARN] Built-in presets cannot be saved/overwritten.")
            return
        create_or_update_preset(
            preset_store=self._preset_store,
            name=name,
            preset_data=self._collect_preset_payload(),
        )
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
            affected_cars = cars_assigned_to_preset(
                car_preset_map=self._car_preset_map,
                preset_name=name,
            )
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

    def _set_tuning_tooltips(self) -> None:
        self._set_tooltip_with_label(
            self.upshift_low_input,
            self._t(
                "tooltip.tuning.upshift_low",
                "Upshift RPM target at low throttle (gentle driving).",
            ),
        )
        self._set_tooltip_with_label(
            self.upshift_high_input,
            self._t(
                "tooltip.tuning.upshift_high",
                "Upshift RPM target at high throttle (aggressive driving).",
            ),
        )
        self._set_tooltip_with_label(
            self.downshift_low_input,
            self._t(
                "tooltip.tuning.downshift_low",
                "Downshift RPM target at low throttle.",
            ),
        )
        self._set_tooltip_with_label(
            self.downshift_high_input,
            self._t(
                "tooltip.tuning.downshift_high",
                "Downshift RPM target at high throttle or braking load.",
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
        base = system_name.split("_", 1)[0] if system_name else ""
        if base in SUPPORTED_UI_LANGUAGE_CODES:
            return base
        return "en"

    def _load_selected_ui_language_from_state() -> str:
        state_path = Path.cwd() / APP_STATE_FILE_NAME
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
