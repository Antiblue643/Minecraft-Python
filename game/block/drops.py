import json
import random
from pathlib import Path

from game.block import Block
from game.entities.item import Item, ItemStack

def _load_loot_config():
    path = Path(__file__).resolve().parents[1] / "data" / "loot.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


_LOOT_CONFIG = _load_loot_config()
_DROPS_AS = dict(_LOOT_CONFIG.get("block_drop_replacements", {}))
_DROPS_NOTHING = frozenset(_LOOT_CONFIG.get("blocks_that_drop_nothing", ()))
_CONDITIONAL_DROPS = dict(_LOOT_CONFIG.get("conditional_drops", {}))
_LEVELS = {str(key): int(value) for key, value in _LOOT_CONFIG.get("harvest_levels", {}).items()}
_BREAK_WEAR = {str(key): int(value) for key, value in _LOOT_CONFIG.get("tool_break_wear", {}).items()}


def _conditional_drop(block_type, meta, rng):
    rule = _CONDITIONAL_DROPS.get(block_type)
    if not isinstance(rule, dict):
        return False, None, 1
    rule_type = rule.get("rule")
    if rule_type == "one_in_n":
        denominator = max(1, int(rule.get("denominator", 1)))
        item_id = rule.get("success_item") if rng.randrange(denominator) == 0 else rule.get("fallback_item")
        return True, item_id, 1
    if rule_type == "minimum_metadata":
        item_id = rule.get("success_item") if int(meta) >= int(rule.get("minimum", 0)) else rule.get("fallback_item")
        return True, item_id, 1
    if rule_type == "bit_clear":
        bit = int(rule.get("bit", 0))
        item_id = rule.get("success_item") if not (int(meta) & bit) else rule.get("fallback_item")
        return True, item_id, 1
    if rule_type == "random_count":
        item_id = rule.get("item")
        minimum = max(1, int(rule.get("minimum", 1)))
        maximum = max(minimum, int(rule.get("maximum", minimum)))
        count = rng.randrange(minimum, maximum + 1)
        return True, item_id, count
    return False, None, 1


def _has_required_drop_tool(block_type, held):
    Block._load_defs()
    required = Block._block_defs.get(block_type, {}).get("dropTool")
    if not required:
        return True
    if held is None:
        return False
    tool = Item.item_defs.get(held.id, {})
    return tool.get("type") == "tool" and tool.get("tooltype") == required


def block_drops(block_type, meta=0, rng=random, held=None):
    if block_type in _DROPS_NOTHING or not _has_required_drop_tool(block_type, held):
        return []

    if block_type == "double_stone_slab":
        return [ItemStack("stone_slab", 2, int(meta) & 3)]

    handled, item_id, count = _conditional_drop(block_type, meta, rng)
    if not handled:
        item_id = _DROPS_AS.get(block_type, block_type)
        count = 1

    if item_id is None or count <= 0:
        return []
    item_id, damage = Item.parse_subtype_key(item_id)
    if not handled and item_id == block_type and Item.has_subtypes(block_type):
        damage = int(meta) & 15
    if not Item.exists(item_id):
        return []
    return [ItemStack(item_id, count, damage)]


def can_harvest(block_type, held):
    Block._load_defs()
    definition = Block._block_defs.get(block_type, {})
    if not _has_required_drop_tool(block_type, held):
        return False
    needed = _LEVELS.get(str(definition.get("minLevel", "none")), 0)
    if needed == 0:
        return True
    if held is None:
        return False
    tool = Item.item_defs.get(held.id)
    if not tool or tool.get("type") != "tool":
        return False
    if tool.get("tooltype") != definition.get("tool"):
        return False
    return float(tool.get("level", 0)) >= needed


def wear_from_breaking(held):
    if held is None:
        return 0
    return _BREAK_WEAR.get(Item.item_defs.get(held.id, {}).get("type"), 0)


def scatter_position(block_pos, rng=random):
    bx, by, bz = block_pos
    return (
        bx - 0.5 + rng.random() * 0.7 + 0.15,
        by - 0.5 + rng.random() * 0.7 + 0.15 - 0.125,
        bz - 0.5 + rng.random() * 0.7 + 0.15,
    )
