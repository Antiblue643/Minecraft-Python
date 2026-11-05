
from game.content_data import load_block_definitions

FLUID_TYPES = frozenset(("water", "lava"))
_NON_SOLID_SPECIALS = frozenset(("air", "fire", "torch", "rail", "ladder"))
_NON_SOLID_MATERIALS = frozenset(("air", "plant", "leaves", "water", "lava"))

_DEFAULT_MATERIALS = {
    "sapling": "plant",
    "cactus": "plant",
    "reed": "plant",
    "yellow_flower": "plant",
    "red_flower": "plant",
    "brown_mushroom": "plant",
    "red_mushroom": "plant",
    "crops": "plant",
    "leaves": "leaves",
    "water": "water",
    "lava": "lava",
}
_BLOCK_DEFINITIONS = None


def bucket_result_for_source(block_type, metadata):
    if block_type in FLUID_TYPES and (int(metadata) & 15) == 0:
        return f"{block_type}_bucket"
    return None


def bucket_target_is_solid(block_type):
    if block_type is None or block_type in _NON_SOLID_SPECIALS:
        return False
    global _BLOCK_DEFINITIONS
    if _BLOCK_DEFINITIONS is None:
        _BLOCK_DEFINITIONS = load_block_definitions()
    definition = _BLOCK_DEFINITIONS.get(str(block_type), {})
    material_name = definition.get("material") or _DEFAULT_MATERIALS.get(
        str(block_type), "powder"
    )
    return str(material_name).lower() not in _NON_SOLID_MATERIALS
