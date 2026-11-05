from __future__ import annotations


_MATERIAL_TIER = {
    "wood": 0,
    "gold": 0,
    "stone": 1,
    "iron": 2,
    "diamond": 3,
}
_TOOL_BASE_DAMAGE = {
    "pickaxe": 2,
    "axe": 3,
    "shovel": 1,
}
_TOOL_TYPES_WITH_ITEMTOOL_HIT_WEAR = frozenset(_TOOL_BASE_DAMAGE)


def item_damage_vs_entity(definition: dict | None) -> int:
    definition = definition if isinstance(definition, dict) else {}
    try:
        explicit = definition.get("damage")
        if explicit is not None:
            return max(1, int(explicit))
    except (TypeError, ValueError):
        pass

    material = str(definition.get("material", "wood")).lower()
    tier = _MATERIAL_TIER.get(material, 0)
    kind = definition.get("type")
    if kind == "melee_weapon":
        return 4 + tier * 2
    if kind == "tool":
        base = _TOOL_BASE_DAMAGE.get(definition.get("tooltype"))
        if base is not None:
            return base + tier
    return 1


def item_hit_entity_wear(definition: dict | None) -> int:
    definition = definition if isinstance(definition, dict) else {}
    if definition.get("type") == "melee_weapon":
        return 1
    if (definition.get("type") == "tool"
            and definition.get("tooltype") in _TOOL_TYPES_WITH_ITEMTOOL_HIT_WEAR):
        return 2
    return 0


def scale_incoming_player_damage(amount: int, difficulty: int, *, difficulty_scaled: bool) -> int:
    try:
        amount = max(0, int(amount))
    except (TypeError, ValueError):
        return 0
    if amount == 0 or not difficulty_scaled:
        return amount
    try:
        difficulty = int(difficulty)
    except (TypeError, ValueError):
        difficulty = 2
    difficulty = max(0, min(3, difficulty))
    if difficulty == 0:
        return 0
    if difficulty == 1:
        return amount // 3 + 1
    if difficulty == 3:
        return amount * 3 // 2
    return amount
