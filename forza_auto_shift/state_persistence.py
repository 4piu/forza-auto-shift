"""App state and preset persistence helpers for the GUI layer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, cast

SCHEMA_VERSION = 5
APP_STATE_FILE_NAME = "forza_auto_shift_state.json"


@dataclass(frozen=True)
class ParsedAppState:
    listen_address: str
    udp_port: int
    dry_run: bool
    relay_enabled: bool
    relay_targets: list[str]
    focus_guard: bool
    play_worker_chime: bool
    ui_language: str
    log_level: str
    shift_down_scan_code: int
    shift_up_scan_code: int
    shift_down_key_name: str
    shift_up_key_name: str
    hotkey_tokens: list[str]
    current_tuning: dict[str, object]
    presets: dict[str, dict[str, object]]
    car_preset_map: dict[str, str]
    car_alias_map: dict[str, str]
    active_preset: str
    default_binding_preset: str


def normalize_preset_values(
    values: Mapping[str, object] | None,
    default_values: Mapping[str, object],
) -> dict[str, object]:
    normalized: dict[str, object] = dict(values) if isinstance(values, Mapping) else {}

    for key, default_value in default_values.items():
        if key not in normalized:
            normalized[key] = default_value

    raw_dwell_overrides = normalized.get("per_gear_dwell_overrides", {})
    if isinstance(raw_dwell_overrides, Mapping):
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


def build_app_state_payload(
    *,
    listen_address: str,
    udp_port: int,
    dry_run: bool,
    relay_enabled: bool,
    relay_targets: list[str],
    focus_guard: bool,
    play_worker_chime: bool,
    ui_language: str,
    log_level: str,
    shift_down_scan_code: int,
    shift_up_scan_code: int,
    shift_down_key_name: str,
    shift_up_key_name: str,
    hotkey_tokens: list[str],
    current_tuning: dict[str, object],
    active_preset: str,
    default_binding_preset: str,
    preset_store: Mapping[str, dict[str, object]],
    builtin_preset_names: set[str],
    car_preset_map: Mapping[str, str],
    car_alias_map: Mapping[str, str],
    split_car_key: Callable[[str], tuple[str, str]],
) -> dict[str, object]:
    user_presets = {
        name: preset
        for name, preset in preset_store.items()
        if name.lower() not in builtin_preset_names
    }

    cars: dict[str, dict[str, str]] = {}
    for car_key, preset_name in car_preset_map.items():
        game_code, car_id = split_car_key(car_key)
        if not game_code:
            continue
        alias = car_alias_map.get(car_key, "").strip()
        cars[car_key] = {
            "preset": preset_name,
            "alias": alias,
            "game": game_code,
            "car_id": car_id,
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "listen_address": listen_address,
        "udp_port": udp_port,
        "dry_run": dry_run,
        "relay_enabled": relay_enabled,
        "relay_targets": relay_targets,
        "focus_guard": focus_guard,
        "play_worker_chime": play_worker_chime,
        "ui_language": ui_language,
        "log_level": log_level,
        "shift_down_scan_code": shift_down_scan_code,
        "shift_up_scan_code": shift_up_scan_code,
        "shift_down_key_name": shift_down_key_name,
        "shift_up_key_name": shift_up_key_name,
        "hotkey_tokens": [token for token in hotkey_tokens if token],
        "current_tuning": current_tuning,
        "active_preset": active_preset,
        "default_binding_preset": default_binding_preset,
        "presets": user_presets,
        "cars": cars,
    }


def parse_app_state_payload(
    *,
    state: Mapping[str, object],
    valid_ui_languages: set[str],
    valid_log_levels: set[str],
    parse_relay_target: Callable[[str], tuple[str, int] | None],
    normalize_preset: Callable[[dict[str, object] | None], dict[str, object]],
    car_storage_key: Callable[[str, str], str],
) -> ParsedAppState:
    schema_version = int(cast(Any, state["schema_version"]))
    if schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported app state schema_version={schema_version}; expected {SCHEMA_VERSION}"
        )

    listen_address = str(state["listen_address"])
    udp_port = int(cast(Any, state["udp_port"]))
    dry_run = bool(state["dry_run"])

    relay_enabled = state["relay_enabled"]
    if not isinstance(relay_enabled, bool):
        raise ValueError("Invalid app state: relay_enabled must be a bool")

    raw_relay_targets = state["relay_targets"]
    if not isinstance(raw_relay_targets, list):
        raise ValueError("Invalid app state: relay_targets must be a list")

    relay_targets: list[str] = []
    for raw_target in raw_relay_targets:
        if not isinstance(raw_target, str):
            raise ValueError("Invalid app state: relay_targets entries must be strings")
        parsed = parse_relay_target(raw_target)
        if parsed is None:
            raise ValueError(
                f"Invalid app state: relay target '{raw_target}' is malformed"
            )
        relay_targets.append(f"{parsed[0]}:{parsed[1]}")

    focus_guard = bool(state["focus_guard"])

    play_worker_chime = state["play_worker_chime"]
    if not isinstance(play_worker_chime, bool):
        raise ValueError("Invalid app state: play_worker_chime must be a bool")

    ui_language_raw = state.get("ui_language", "auto")
    if not isinstance(ui_language_raw, str):
        raise ValueError("Invalid app state: ui_language must be a string")
    ui_language = ui_language_raw.strip().lower() or "auto"
    if ui_language not in valid_ui_languages:
        raise ValueError(f"Unsupported ui_language '{ui_language}' in state")

    log_level = str(state["log_level"])
    if log_level not in valid_log_levels:
        raise ValueError(f"Unsupported log_level '{log_level}' in state")

    shift_down_scan_code = int(cast(Any, state["shift_down_scan_code"]))
    shift_up_scan_code = int(cast(Any, state["shift_up_scan_code"]))
    shift_down_key_name = str(state["shift_down_key_name"])
    shift_up_key_name = str(state["shift_up_key_name"])

    raw_hotkey_tokens = state["hotkey_tokens"]
    if not isinstance(raw_hotkey_tokens, list):
        raise ValueError("Invalid app state: hotkey_tokens must be a list")
    hotkey_tokens = [token for token in raw_hotkey_tokens if isinstance(token, str)]

    raw_tuning_values = state["current_tuning"]
    if not isinstance(raw_tuning_values, Mapping):
        raise ValueError("Invalid app state: current_tuning must be an object")
    current_tuning = normalize_preset(dict(raw_tuning_values))

    raw_presets = state["presets"]
    if not isinstance(raw_presets, Mapping):
        raise ValueError("Invalid app state: presets must be an object")
    presets: dict[str, dict[str, object]] = {}
    for name, value in raw_presets.items():
        if not isinstance(name, str) or not isinstance(value, Mapping):
            raise ValueError("Invalid app state: preset entries must be objects")
        presets[name] = normalize_preset(dict(value))

    raw_cars = state["cars"]
    if not isinstance(raw_cars, Mapping):
        raise ValueError("Invalid app state: cars must be an object")

    car_preset_map: dict[str, str] = {}
    car_alias_map: dict[str, str] = {}
    for car_key, car_entry in raw_cars.items():
        if not isinstance(car_key, str) or not isinstance(car_entry, Mapping):
            raise ValueError("Invalid app state: car entries must be objects")

        preset_name = car_entry["preset"]
        alias = car_entry["alias"]
        game = car_entry["game"]
        car_id = car_entry["car_id"]

        if not isinstance(preset_name, str) or not preset_name.strip():
            raise ValueError("Invalid app state: car preset must be a non-empty string")
        if not isinstance(alias, str):
            raise ValueError("Invalid app state: car alias must be a string")
        if not isinstance(game, str):
            raise ValueError("Invalid app state: car game must be a string")
        if not isinstance(car_id, str) or not car_id.strip():
            raise ValueError("Invalid app state: car_id must be a non-empty string")

        normalized_key = car_storage_key(game, car_id.strip())
        if not normalized_key:
            continue
        car_preset_map[normalized_key] = preset_name
        if alias.strip():
            car_alias_map[normalized_key] = alias.strip()

    active_preset = state["active_preset"]
    if not isinstance(active_preset, str):
        raise ValueError("Invalid app state: active_preset must be a string")

    default_binding_preset = state["default_binding_preset"]
    if not isinstance(default_binding_preset, str):
        raise ValueError("Invalid app state: default_binding_preset must be a string")

    return ParsedAppState(
        listen_address=listen_address,
        udp_port=udp_port,
        dry_run=dry_run,
        relay_enabled=relay_enabled,
        relay_targets=relay_targets,
        focus_guard=focus_guard,
        play_worker_chime=play_worker_chime,
        ui_language=ui_language,
        log_level=log_level,
        shift_down_scan_code=shift_down_scan_code,
        shift_up_scan_code=shift_up_scan_code,
        shift_down_key_name=shift_down_key_name,
        shift_up_key_name=shift_up_key_name,
        hotkey_tokens=hotkey_tokens,
        current_tuning=current_tuning,
        presets=presets,
        car_preset_map=car_preset_map,
        car_alias_map=car_alias_map,
        active_preset=active_preset,
        default_binding_preset=default_binding_preset,
    )


def load_state_file(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("Invalid app state: root value must be an object")
    return raw


def save_state_file(path: Path, state: Mapping[str, object]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
