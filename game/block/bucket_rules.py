from game.block.material import material_for_block

FLUID_TYPES = frozenset(("water", "lava"))
_NON_SOLID_SPECIALS = frozenset(("air", "fire", "torch", "rail", "ladder"))


def bucket_result_for_source(block_type, metadata):
    if block_type in FLUID_TYPES and (int(metadata) & 15) == 0:
        return f"{block_type}_bucket"
    return None


def bucket_target_is_solid(block_type):
    if block_type is None or block_type in _NON_SOLID_SPECIALS:
        return False
    material = material_for_block(block_type)
    return material.name not in ("air", "plant", "leaves", "water", "lava")
