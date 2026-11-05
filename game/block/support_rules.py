from __future__ import annotations

from collections import deque

from game.block import Block
from game.block.fire_rules import can_catch_fire, is_normal_cube
from game.block.material import material_for_block
from game.block.mushroom_rules import mushroom_can_stay
from game.block.plant_rules import cactus_can_stay, reed_can_stay

_DIRS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))

_PLANT_SOIL = frozenset({"grass_block", "dirt", "farmland"})
_FLOWERS = frozenset({"yellow_flower", "red_flower", "sapling"})
_MUSHROOMS = frozenset({"brown_mushroom", "red_mushroom"})
FLORA = _FLOWERS | _MUSHROOMS | {"crops"}

SUPPORT_TYPES = FLORA | {
    "torch", "redstone_torch", "unlit_redstone_torch", "lever", "stone_button", "cactus",
    "stone_pressure_plate", "wooden_pressure_plate", "ladder", "rail",
    "redstone_wire", "wooden_door", "iron_door", "fire", "sign", "wall_sign", "reed",
}

_NON_SOLID_MATERIAL_BLOCKS = frozenset({"torch", "ladder", "rail", "fire"})


def _material_is_solid(block_type):
    if block_type is None or block_type in _NON_SOLID_MATERIAL_BLOCKS:
        return False
    Block._load_defs()
    definition = Block._block_defs.get(block_type)
    return material_for_block(block_type, definition).is_solid

GRAVITY_BLOCKS = frozenset({"sand", "gravel"})

STAIR_TYPES = frozenset({"stairs", "cobblestone_stairs"})
STAIR_MODEL_BLOCK = {
    "stairs": "planks",
    "cobblestone_stairs": "cobblestone",
}

_TORCH_SUPPORT = {1: (-1, 0, 0), 2: (1, 0, 0), 3: (0, 0, -1), 4: (0, 0, 1), 5: (0, -1, 0)}
_LADDER_SUPPORT = {2: (0, 0, 1), 3: (0, 0, -1), 4: (1, 0, 0), 5: (-1, 0, 0)}
_RAIL_SLOPE_SUPPORT = {2: (1, 0, 0), 3: (-1, 0, 0), 4: (0, 0, -1), 5: (0, 0, 1)}

_MAX_CHECKS_PER_NOTIFICATION = 4096

def _loaded(world, x, z):
    return world._get_chunk_index(x, z) in world.chunks


def normal_cube(world, x, y, z):
    if y < 0:
        return True
    if y >= world.CHUNK_HEIGHT:
        return False
    if not _loaded(world, x, z):
        return True
    block_type = world.get_block_type(x, y, z)
    if block_type is None:
        return False
    Block._load_defs()
    return is_normal_cube(Block._block_defs.get(block_type))


def _flammable(world, x, y, z):
    block_type = world.get_block_type(x, y, z)
    if block_type is None:
        return False
    Block._load_defs()
    return can_catch_fire(Block._block_defs.get(block_type))


def can_see_sky(world, x, y, z):
    chunk = world.chunks.get(world._get_chunk_index(x, z))
    if chunk is None:
        return True
    lx = x - chunk.start_x
    lz = z - chunk.start_z
    if not (0 <= lx < chunk.width and 0 <= lz < chunk.depth):
        return True
    return y >= int(chunk.heightmap[lz * chunk.width + lx])


def _light_known(world, x, z):
    chunk = world.chunks.get(world._get_chunk_index(x, z))
    return chunk is not None and getattr(chunk, "light_ready", True)

def flora_can_stay(world, block_type, x, y, z):
    below = world.get_block_type(x, y - 1, z)
    light_known = _light_known(world, x, z)
    if block_type in _MUSHROOMS:
        if not normal_cube(world, x, y - 1, z):
            return False
        return mushroom_can_stay(
            world.get_light(x, y, z), True, light_known=light_known,
        )
    if block_type == "crops":
        soil_ok = below == "farmland"
    else:
        soil_ok = below in _PLANT_SOIL
    if not soil_ok:
        return False
    if not light_known:
        return True
    return world.get_light(x, y, z) >= 8 or can_see_sky(world, x, y, z)


def fire_can_stay(world, x, y, z):
    if normal_cube(world, x, y - 1, z):
        return True
    for dx, dy, dz in _DIRS:
        nx, ny, nz = x + dx, y + dy, z + dz
        if not _loaded(world, nx, nz):
            return True
        if _flammable(world, nx, ny, nz):
            return True
    return False


def should_break(world, block_type, meta, x, y, z):
    if block_type in FLORA:
        return not flora_can_stay(world, block_type, x, y, z)

    if block_type == "cactus":
        return not cactus_can_stay(world, x, y, z)

    if block_type == "reed":
        return not reed_can_stay(world, x, y, z)

    if block_type == "wall_sign":
        meta = int(meta) & 15
        if meta == 2:
            return not normal_cube(world, x, y, z + 1)
        if meta == 3:
            return not normal_cube(world, x, y, z - 1)
        if meta == 4:
            return not normal_cube(world, x + 1, y, z)
        if meta == 5:
            return not normal_cube(world, x - 1, y, z)
        return not normal_cube(world, x, y - 1, z)

    if block_type == "fence":
        below = world.get_block_type(x, y - 1, z) if hasattr(world, "get_block_type") else None
        if below == "fence":
            return True
        return not _material_is_solid(below)

    if block_type in ("sign", "redstone_wire", "stone_pressure_plate", "wooden_pressure_plate"):
        return not normal_cube(world, x, y - 1, z)

    if block_type == "stone_button":
        support_offset = {
            1: (-1, 0, 0), 2: (1, 0, 0),
            3: (0, 0, -1), 4: (0, 0, 1),
        }.get(int(meta) & 7)
        return support_offset is None or not normal_cube(
            world, x + support_offset[0], y, z + support_offset[2])

    if block_type in ("torch", "redstone_torch", "unlit_redstone_torch", "lever"):
        if not (normal_cube(world, x - 1, y, z) or normal_cube(world, x + 1, y, z)
                or normal_cube(world, x, y, z - 1) or normal_cube(world, x, y, z + 1)
                or normal_cube(world, x, y - 1, z)):
            return True
        support_map = _TORCH_SUPPORT if block_type != "lever" else _TORCH_SUPPORT
        offset = support_map.get(int(meta) & 7)
        return offset is not None and not normal_cube(
            world, x + offset[0], y + offset[1], z + offset[2])

    if block_type == "ladder":
        offset = _LADDER_SUPPORT.get(int(meta) & 15)
        return offset is not None and not normal_cube(
            world, x + offset[0], y + offset[1], z + offset[2])

    if block_type == "rail":
        if not normal_cube(world, x, y - 1, z):
            return True
        offset = _RAIL_SLOPE_SUPPORT.get(int(meta) & 15)
        return offset is not None and not normal_cube(
            world, x + offset[0], y + offset[1], z + offset[2])

    if block_type == "fire":
        return not fire_can_stay(world, x, y, z)

    return False

def _remove(world, x, y, z):
    world.set_block(x, y, z, None,
                    immediate_mesh=not getattr(world, "_processing_natural_tick", False))


def _check_door(world, x, y, z, meta):
    block_type = world.get_block_type(x, y, z)
    if block_type not in ("wooden_door", "iron_door"):
        return
    if meta & 8:
        if world.get_block_type(x, y - 1, z) != block_type:
            _remove(world, x, y, z)
        return
    above_is_door = world.get_block_type(x, y + 1, z) == block_type
    if above_is_door and normal_cube(world, x, y - 1, z):
        return
    world.drop_block_as_item(block_type, meta & 7, x, y, z)
    _remove(world, x, y, z)
    if above_is_door:
        _remove(world, x, y + 1, z)


def _is_stair_block(world, x, y, z):
    btype = world.get_block_type(x, y, z)
    if btype in STAIR_TYPES:
        return True
    if btype is None:
        return False
    Block._load_defs()
    return Block._block_defs.get(btype, {}).get("model") == "stairs"


def stair_facing_from_neighbors(world, x, y, z):
    facing = -1
    if _is_stair_block(world, x + 1, y + 1, z):
        facing = 0
    if _is_stair_block(world, x - 1, y + 1, z):
        facing = 1
    if _is_stair_block(world, x, y + 1, z + 1):
        facing = 2
    if _is_stair_block(world, x, y + 1, z - 1):
        facing = 3

    if facing < 0:
        solid_pos_x = _material_is_solid(world.get_block_type(x + 1, y, z))
        solid_neg_x = _material_is_solid(world.get_block_type(x - 1, y, z))
        solid_pos_z = _material_is_solid(world.get_block_type(x, y, z + 1))
        solid_neg_z = _material_is_solid(world.get_block_type(x, y, z - 1))
        if solid_pos_x and not solid_neg_x:
            facing = 0
        if solid_neg_x and not solid_pos_x:
            facing = 1
        if solid_pos_z and not solid_neg_z:
            facing = 2
        if solid_neg_z and not solid_pos_z:
            facing = 3

    if facing < 0:
        if _is_stair_block(world, x - 1, y - 1, z):
            facing = 0
        if _is_stair_block(world, x + 1, y - 1, z):
            facing = 1
        if _is_stair_block(world, x, y - 1, z - 1):
            facing = 2
        if _is_stair_block(world, x, y - 1, z + 1):
            facing = 3

    return facing if facing >= 0 else None


def _set_stair_metadata(world, x, y, z, meta):
    meta = int(meta) & 15
    if world.get_metadata(x, y, z) == meta:
        return
    cx, cz = world._get_chunk_index(x, z)
    chunk = world.chunks.get((cx, cz))
    if chunk is not None and hasattr(chunk, "set_metadata"):
        chunk.set_metadata(x, y, z, meta)
        if hasattr(chunk, "mark_dirty_at"):
            chunk.mark_dirty_at(y)
        return
    world.set_block(
        x, y, z, world.get_block_type(x, y, z), meta=meta,
        immediate_mesh=not getattr(world, "_processing_natural_tick", False),
    )


def update_stair_state(world, x, y, z):
    return False


def update_stair_block(world, x, y, z):
    block_type = world.get_block_type(x, y, z)
    if block_type not in STAIR_TYPES:
        return False
    above = world.get_block_type(x, y + 1, z)
    if _material_is_solid(above):
        model = STAIR_MODEL_BLOCK.get(block_type, "planks")
        world.set_block(
            x, y, z, model,
            immediate_mesh=not getattr(world, "_processing_natural_tick", False),
        )
        return True
    return False


def check_support(world, x, y, z):
    block_type = world.get_block_type(x, y, z)
    if block_type in GRAVITY_BLOCKS:
        schedule = getattr(world, "schedule_block_update", None)
        if schedule is not None:
            schedule(x, y, z, block_type)
        return False
    if block_type in STAIR_TYPES:
        return update_stair_block(world, x, y, z)
    if block_type == "farmland":
        above = world.get_block_type(x, y + 1, z)
        if _material_is_solid(above):
            world.set_block(
                x, y, z, "dirt",
                immediate_mesh=not getattr(world, "_processing_natural_tick", False),
            )
            return True
        return False
    if block_type == "snow":
        if _material_is_solid(world.get_block_type(x, y - 1, z)):
            return False
        _remove(world, x, y, z)
        return True
    if block_type not in SUPPORT_TYPES:
        return False

    meta = world.get_metadata(x, y, z)
    if block_type in ("wooden_door", "iron_door"):
        _check_door(world, x, y, z, int(meta))
        return world.get_block_type(x, y, z) != block_type
    if not should_break(world, block_type, meta, x, y, z):
        return False
    if block_type != "fire":
        world.drop_block_as_item(block_type, meta, x, y, z)
    _remove(world, x, y, z)
    return True


def notify_neighbors(world, x, y, z):
    queue = getattr(world, "_support_queue", None)
    if queue is None:
        queue = world._support_queue = deque()
    queue.append((x, y, z))
    if getattr(world, "_support_active", False):
        return
    world._support_active = True
    try:
        budget = _MAX_CHECKS_PER_NOTIFICATION
        while queue and budget > 0:
            cx, cy, cz = queue.popleft()
            for dx, dy, dz in _DIRS:
                budget -= 1
                check_support(world, cx + dx, cy + dy, cz + dz)
    finally:
        queue.clear()
        world._support_active = False
