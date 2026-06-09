"""Preset and car-binding domain logic shared by the GUI layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

SUPPORTED_GAME_CODES = {"FH4", "FH5", "FH6", "FM"}
DISABLED_PRESET_NAME = "Disabled"
RESERVED_PRESET_NAMES = {DISABLED_PRESET_NAME}
RESERVED_PRESET_NAMES_NORMALIZED = {
    preset_name.casefold() for preset_name in RESERVED_PRESET_NAMES
}


@dataclass(frozen=True)
class CarBindingRow:
    car_key: str
    game_code: str
    car_id: str
    display_name: str
    preset_name: str


def sorted_preset_names(
    preset_store: Mapping[str, dict[str, object]],
    builtin_preset_names: set[str] | None = None,
) -> list[str]:
    normalized_builtin_names = {
        name.casefold() for name in (builtin_preset_names or set())
    }
    return sorted(
        preset_store.keys(),
        key=lambda name: (name.casefold() in normalized_builtin_names, name.casefold()),
    )


def resolve_selected_name(current: str, names: list[str]) -> str:
    if current and current in names:
        return current
    if names:
        return names[0]
    return ""


def build_car_binding_rows(
    *,
    car_preset_map: Mapping[str, str],
    car_alias_map: Mapping[str, str],
    preset_store: Mapping[str, dict[str, object]],
    default_preset: str,
    selected_filter: str,
) -> tuple[list[CarBindingRow], dict[str, str]]:
    normalized_filter = selected_filter.strip().upper() or "ALL"
    preset_names = set(preset_store.keys()) | RESERVED_PRESET_NAMES

    def sort_key(value: str) -> tuple[str, int]:
        game_code, car_id = split_car_key(value)
        try:
            numeric_id = int(car_id)
        except ValueError:
            numeric_id = 0
        return game_code, numeric_id

    normalized_updates: dict[str, str] = {}
    rows: list[CarBindingRow] = []
    for car_key in sorted(car_preset_map.keys(), key=sort_key):
        game_code, car_id = split_car_key(car_key)
        if not game_code:
            continue
        if normalized_filter != "ALL" and game_code != normalized_filter:
            continue

        alias = car_alias_map.get(car_key, "").strip()
        display_name = alias if alias else f"Car {car_id}"

        current_preset = car_preset_map.get(car_key, default_preset)
        if current_preset not in preset_names:
            current_preset = default_preset
            normalized_updates[car_key] = default_preset

        rows.append(
            CarBindingRow(
                car_key=car_key,
                game_code=game_code,
                car_id=car_id,
                display_name=display_name,
                preset_name=current_preset,
            )
        )

    return rows, normalized_updates


def normalize_game_code(game_code: str) -> str:
    code = game_code.strip().upper()
    if code in SUPPORTED_GAME_CODES:
        return code
    return ""


def car_storage_key(game_code: str, car_id: str) -> str:
    normalized_game = normalize_game_code(game_code)
    normalized_car_id = car_id.strip()
    if not normalized_game or not normalized_car_id:
        return ""
    return f"{normalized_game}-{normalized_car_id}"


def split_car_key(car_key: str) -> tuple[str, str]:
    if "-" in car_key:
        raw_game, raw_car_id = car_key.split("-", 1)
        return normalize_game_code(raw_game), raw_car_id.strip()
    return "", car_key.strip()


def default_preset_name(
    *,
    preset_store: dict[str, dict[str, object]],
    default_binding_preset_name: str,
    default_car_preset_name: str,
    builtin_templates: Mapping[str, dict[str, object]],
    normalize_preset_values: Callable[[dict[str, object] | None], dict[str, object]],
) -> str:
    if default_binding_preset_name in RESERVED_PRESET_NAMES:
        return default_binding_preset_name
    if default_binding_preset_name in preset_store:
        return default_binding_preset_name
    if default_car_preset_name in preset_store:
        return default_car_preset_name
    if preset_store:
        return sorted(preset_store.keys(), key=str.lower)[0]

    preset_store[default_car_preset_name] = dict(
        normalize_preset_values(builtin_templates[default_car_preset_name])
    )
    return default_car_preset_name


def normalize_car_preset_maps(
    *,
    car_preset_map: dict[str, str],
    car_alias_map: dict[str, str],
    preset_store: Mapping[str, dict[str, object]],
    fallback_preset: str,
) -> None:
    for car_key, preset_name in list(car_preset_map.items()):
        game_code, _car_id = split_car_key(car_key)
        if not game_code:
            del car_preset_map[car_key]
            continue
        if preset_name not in preset_store and preset_name not in RESERVED_PRESET_NAMES:
            car_preset_map[car_key] = fallback_preset

    for car_key in list(car_alias_map.keys()):
        if car_key not in car_preset_map:
            del car_alias_map[car_key]


def upsert_detected_car(
    *,
    car_preset_map: dict[str, str],
    game_code: str,
    car_id: str,
    default_preset: str,
) -> tuple[str, bool]:
    key = car_storage_key(game_code, car_id)
    if not key:
        return "", False
    if key not in car_preset_map:
        car_preset_map[key] = default_preset
        return key, True
    return key, False


def rename_preset_references(
    *,
    car_preset_map: dict[str, str],
    default_binding_preset_name: str,
    source_name: str,
    new_name: str,
) -> str:
    if default_binding_preset_name == source_name:
        default_binding_preset_name = new_name

    for car_key, preset_name in list(car_preset_map.items()):
        if preset_name == source_name:
            car_preset_map[car_key] = new_name

    return default_binding_preset_name


def cars_assigned_to_preset(
    *,
    car_preset_map: Mapping[str, str],
    preset_name: str,
) -> list[str]:
    return [
        car_key
        for car_key, assigned_name in car_preset_map.items()
        if assigned_name == preset_name
    ]


def reassign_cars_to_preset(
    *,
    car_preset_map: dict[str, str],
    car_keys: list[str],
    preset_name: str,
) -> None:
    for car_key in car_keys:
        car_preset_map[car_key] = preset_name


def validate_new_preset_name(
    *,
    name: str,
    preset_store: Mapping[str, dict[str, object]],
    builtin_templates: Mapping[str, dict[str, object]],
) -> str | None:
    if not name:
        return "Preset name cannot be empty."
    if name.casefold() in RESERVED_PRESET_NAMES_NORMALIZED:
        return f"'{name}' is reserved and cannot be used as a custom preset name."
    if name.lower() in builtin_templates:
        return f"'{name}' is a built-in preset and cannot be overwritten."
    if name in preset_store:
        return f"Preset '{name}' already exists."
    return None


def validate_rename_preset(
    *,
    source_name: str,
    new_name: str,
    preset_store: Mapping[str, dict[str, object]],
    builtin_templates: Mapping[str, dict[str, object]],
) -> str | None:
    if not source_name or source_name not in preset_store:
        return "Select a preset to rename."
    if source_name.lower() in builtin_templates:
        return "Built-in presets cannot be renamed."
    if not new_name or new_name == source_name:
        return ""
    if new_name.casefold() in RESERVED_PRESET_NAMES_NORMALIZED:
        return f"'{new_name}' is reserved and cannot be used as a custom preset name."
    if new_name in preset_store:
        return f"Preset '{new_name}' already exists."
    return None


def create_or_update_preset(
    *,
    preset_store: dict[str, dict[str, object]],
    name: str,
    preset_data: Mapping[str, object],
) -> None:
    preset_store[name] = dict(preset_data)


def duplicate_preset(
    *,
    preset_store: dict[str, dict[str, object]],
    source_name: str,
    target_name: str,
) -> None:
    preset_store[target_name] = dict(preset_store[source_name])


def rename_preset_entry(
    *,
    preset_store: dict[str, dict[str, object]],
    source_name: str,
    new_name: str,
) -> None:
    preset_store[new_name] = preset_store.pop(source_name)


def remove_preset_entry(
    *,
    preset_store: dict[str, dict[str, object]],
    name: str,
) -> None:
    del preset_store[name]


def format_car_keys(car_keys: list[str]) -> list[str]:
    return [
        f"{game_code}-{car_id}"
        for car_key in car_keys
        for game_code, car_id in [split_car_key(car_key)]
        if game_code
    ]
