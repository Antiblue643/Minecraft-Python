
from game.block import Block
from game.block.material import material_for_block

_WATER_TYPES = frozenset({"water", "flowing_water", "stationary_water", "water_still", "water_flowing"})
_CACTUS_NON_SOLID = frozenset({
    "water", "flowing_water", "stationary_water", "water_still", "water_flowing",
    "lava", "flowing_lava", "stationary_lava", "lava_still", "lava_flowing",
    "fire", "snow", "sapling", "yellow_flower", "red_flower",
    "brown_mushroom", "red_mushroom", "reed", "torch", "redstone_torch",
    "unlit_redstone_torch", "ladder", "rail", "redstone_wire",
    "stone_button", "stone_pressure_plate", "wooden_pressure_plate",
})

def _type_at(world, x, y, z):
    getter = getattr(world, "get_block_type", None)
    if callable(getter):
        return getter(x, y, z)
    block = world.get_block(x, y, z)
    return getattr(block, "block_type", None) if block is not None else None

def reed_can_stay(world, x: int, y: int, z: int) -> bool:
    below = _type_at(world, x, y - 1, z)
    if below == "reed":
        return True
    if below not in ("grass_block", "dirt"):
        return False
    return any(
        _type_at(world, x + dx, y - 1, z + dz) in _WATER_TYPES
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
    )

def cactus_can_stay(world, x: int, y: int, z: int) -> bool:
    if _type_at(world, x, y - 1, z) not in ("sand", "cactus"):
        return False
    Block._load_defs()
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        neighbor = _type_at(world, x + dx, y, z + dz)
        if neighbor is None or neighbor in _CACTUS_NON_SOLID:
            continue
        definition = Block._block_defs.get(neighbor, {})
        if material_for_block(neighbor, definition).is_solid:
            return False
    return True
