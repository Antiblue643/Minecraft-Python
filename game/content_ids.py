from __future__ import annotations

import json
from pathlib import Path

from game.content_data import load_block_definitions, load_item_definitions

_DATA_DIR = Path(__file__).resolve().parent / "data"


def _read_json(filename: str) -> dict:
    with (_DATA_DIR / filename).open(encoding="utf-8") as stream:
        return json.load(stream)


_BLOCK_DEFS = load_block_definitions()
_ITEM_DEFS = load_item_definitions()
_ENTITY_DEFS = _read_json("entities.json")["entities"]


def _numeric_id_map(definitions: dict, field: str = "id") -> dict[str, int]:
    result: dict[str, int] = {}
    for name, definition in definitions.items():
        value = definition.get(field)
        if value is None:
            continue
        try:
            identifier = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid {field} for content {name!r}: {value!r}") from exc
        if identifier < 0 or identifier > 65535:
            raise ValueError(f"Out-of-range {field} for content {name!r}: {identifier}")
        if identifier in result.values():
            previous = next(key for key, current in result.items() if current == identifier)
            raise ValueError(
                f"Duplicate legacy ID {identifier}: {previous!r} and {name!r}"
            )
        result[name] = identifier
    return result


CLASSIC_BLOCK_NAME_TO_ID = _numeric_id_map(_BLOCK_DEFS)
CLASSIC_BLOCK_ID_TO_NAME = {identifier: name for name, identifier in CLASSIC_BLOCK_NAME_TO_ID.items()}
CLASSIC_BLOCK_ID_TO_NAME.update({8: "water", 10: "lava"})
CLASSIC_ITEM_NAME_TO_ID = _numeric_id_map(_ITEM_DEFS)
CLASSIC_ITEM_ID_TO_NAME = {identifier: name for name, identifier in CLASSIC_ITEM_NAME_TO_ID.items()}
CLASSIC_BLOCK_ITEM_ID_TO_NAME = CLASSIC_BLOCK_ID_TO_NAME

ITEM_NAME_ALIASES = {
    str(alias): name
    for name, definition in _ITEM_DEFS.items()
    for alias in definition.get("aliases", ())
}
for alias, target in ITEM_NAME_ALIASES.items():
    if target not in _ITEM_DEFS:
        raise ValueError(f"Item alias {alias!r} points to unknown item {target!r}")
    if alias in _ITEM_DEFS:
        raise ValueError(f"Item alias {alias!r} conflicts with an item key")
    if alias in _BLOCK_DEFS:
        block_id = _BLOCK_DEFS[alias].get("id")
        item_id = _ITEM_DEFS[target].get("id")
        if block_id is None or item_id is None or int(block_id) == int(item_id):
            raise ValueError(f"Item alias {alias!r} conflicts with a block key")

CLASSIC_ENTITY_KEY_TO_ID = {
    key: str(definition["legacy_id"])
    for key, definition in _ENTITY_DEFS.items()
    if definition.get("legacy_id") is not None
}
if len(set(CLASSIC_ENTITY_KEY_TO_ID.values())) != len(CLASSIC_ENTITY_KEY_TO_ID):
    raise ValueError("Duplicate classic string entity ID in game/data/entities.json")
CLASSIC_ENTITY_ID_TO_KEY = {
    identifier: key for key, identifier in CLASSIC_ENTITY_KEY_TO_ID.items()
}

CLASSIC_LIVING_ENTITY_KEYS = (
    "pig", "sheep", "cow", "chicken", "giant", "zombie", "zombie_pigman", "ghast", "skeleton", "spider", "creeper", "slime", "monster", "mob",
)
CLASSIC_LIVING_ENTITY_IDS = tuple(
    CLASSIC_ENTITY_KEY_TO_ID[key] for key in CLASSIC_LIVING_ENTITY_KEYS
)


def legacy_entity_id(key: str) -> str:
    try:
        return CLASSIC_ENTITY_KEY_TO_ID[str(key)]
    except KeyError as exc:
        raise ValueError(f"No classic entity ID registered for {key!r}") from exc


def classic_block_id(name: str | None, metadata: int = 0) -> int:
    if name is None:
        return 0
    if name == "water":
        return 8 if (int(metadata) & 15) else 9
    if name == "lava":
        return 10 if (int(metadata) & 15) else 11
    try:
        return CLASSIC_BLOCK_NAME_TO_ID[name]
    except KeyError as exc:
        raise ValueError(f"Block type {name!r} has no mapping to an Alpha block ID") from exc


def classic_block_id_or_air(name: str | None, metadata: int = 0) -> int:
    try:
        return classic_block_id(name, metadata)
    except ValueError:
        return 0


def classic_item_id(item_name: str) -> int | None:
    name = ITEM_NAME_ALIASES.get(item_name, item_name)
    if name in CLASSIC_ITEM_NAME_TO_ID:
        return CLASSIC_ITEM_NAME_TO_ID[name]
    if name in CLASSIC_BLOCK_NAME_TO_ID:
        return CLASSIC_BLOCK_NAME_TO_ID[name]
    return None


def item_name_from_classic_id(numeric_id: int) -> str | None:
    numeric_id = int(numeric_id) & 0xFFFF
    if numeric_id in CLASSIC_ITEM_ID_TO_NAME:
        return CLASSIC_ITEM_ID_TO_NAME[numeric_id]
    return CLASSIC_BLOCK_ID_TO_NAME.get(numeric_id)
