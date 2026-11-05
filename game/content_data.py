
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"


def _read_json(path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def load_block_definitions(path=DATA_DIR / "blocks.json"):
    return _read_json(path).get("blocks", {})


def load_item_definitions(path=DATA_DIR / "items.json", *,
                          blocks_path=DATA_DIR / "blocks.json"):
    definitions = dict(_read_json(path).get("items", {}))
    block_defs = load_block_definitions(blocks_path)
    for block_name, block_def in block_defs.items():
        block_item = block_def.get("blockitem")
        if not isinstance(block_item, dict):
            continue
        item_def = dict(block_item)
        item_def.setdefault("block", block_name)
        definitions.setdefault(block_name, item_def)
    return definitions


def load_painting_definitions(path=DATA_DIR / "paintings.json"):
    entries = _read_json(path).get("paintings", {})
    if not isinstance(entries, dict) or not entries:
        raise ValueError(f"{path}: 'paintings' must be a non-empty object")
    motives = []
    for title, data in entries.items():
        try:
            size_x, size_y = int(data["size_x"]), int(data["size_y"])
            offset_x, offset_y = int(data["offset_x"]), int(data["offset_y"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid painting {title!r}: {data!r}") from exc
        if size_x <= 0 or size_y <= 0 or size_x % 16 or size_y % 16:
            raise ValueError(f"Painting {title!r} size must be a positive multiple of 16")
        if offset_x < 0 or offset_y < 0:
            raise ValueError(f"Painting {title!r} offset cannot be negative")
        motives.append((str(title), size_x, size_y, offset_x, offset_y))
    return motives
