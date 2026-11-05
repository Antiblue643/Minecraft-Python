from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from game.block.chest_inventory import find_chest_pair


_CHEST_SIDE = 26
_CHEST_FRONT = 27
_CHEST_TOP_BOTTOM = 25
_VALID_FACING = (2, 3, 4, 5)

_FACE_ORDER = (5, 4, 0, 1, 3, 2)


@lru_cache(maxsize=1)
def _block_defs():
    path = Path(__file__).resolve().parent.parent / "data" / "blocks.json"
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return {}


def _block_type(world, x, y, z):
    try:
        return world.get_block_type(int(x), int(y), int(z))
    except (AttributeError, TypeError, ValueError):
        return None


def _metadata(world, x, y, z):
    try:
        return int(world.get_metadata(int(x), int(y), int(z))) & 15
    except (AttributeError, TypeError, ValueError):
        return 0


def _is_opaque_cube(world, x, y, z):
    name = _block_type(world, x, y, z)
    if name is None:
        return False
    definition = _block_defs().get(name, {})
    bounds = definition.get("bounds", (0, 0, 0, 1, 1, 1))
    try:
        full_bounds = tuple(float(v) for v in bounds) == (0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    except (TypeError, ValueError):
        full_bounds = False
    return bool(
        name != "leaves"
        and definition.get("model", "cube") == "cube"
        and definition.get("nCull", "all") == "all"
        and not definition.get("transparent", False)
        and full_bounds
    )


def _single_native_facing(world, x, y, z):
    north = _is_opaque_cube(world, x, y, z - 1)
    south = _is_opaque_cube(world, x, y, z + 1)
    west = _is_opaque_cube(world, x - 1, y, z)
    east = _is_opaque_cube(world, x + 1, y, z)
    facing = 3
    if north and not south:
        facing = 3
    if south and not north:
        facing = 2
    if west and not east:
        facing = 5
    if east and not west:
        facing = 4
    return facing


def _paired_native_facing(world, x, y, z, axis_x):
    if axis_x:
        west_chest = _block_type(world, x - 1, y, z) == "chest"
        outer_x = x - 1 if west_chest else x + 1
        north_or_outer = (
            _is_opaque_cube(world, x, y, z - 1)
            or _is_opaque_cube(world, outer_x, y, z - 1)
        )
        south_or_outer = (
            _is_opaque_cube(world, x, y, z + 1)
            or _is_opaque_cube(world, outer_x, y, z + 1)
        )
        facing = 3
        if north_or_outer and not _is_opaque_cube(world, x, y, z + 1) \
                and not _is_opaque_cube(world, outer_x, y, z + 1):
            facing = 3
        if south_or_outer and not _is_opaque_cube(world, x, y, z - 1) \
                and not _is_opaque_cube(world, outer_x, y, z - 1):
            facing = 2
        return facing

    north_chest = _block_type(world, x, y, z - 1) == "chest"
    outer_z = z - 1 if north_chest else z + 1
    west_or_outer = (
        _is_opaque_cube(world, x - 1, y, z)
        or _is_opaque_cube(world, x - 1, y, outer_z)
    )
    east_or_outer = (
        _is_opaque_cube(world, x + 1, y, z)
        or _is_opaque_cube(world, x + 1, y, outer_z)
    )
    facing = 5
    if west_or_outer and not _is_opaque_cube(world, x + 1, y, z) \
            and not _is_opaque_cube(world, x + 1, y, outer_z):
        facing = 5
    if east_or_outer and not _is_opaque_cube(world, x - 1, y, z) \
            and not _is_opaque_cube(world, x - 1, y, outer_z):
        facing = 4
    return facing


def _facing_for_pair(world, pair, axis_x):
    allowed = (2, 3) if axis_x else (4, 5)
    for position in pair:
        facing = _metadata(world, *position)
        if facing in allowed:
            return facing
    x, y, z = pair[0]
    return _paired_native_facing(world, x, y, z, axis_x)


def chest_face_textures(world, x, y, z, metadata=None):
    x, y, z = int(x), int(y), int(z)
    meta = (_metadata(world, x, y, z) if metadata is None else int(metadata)) & 15
    pair = find_chest_pair(world, x, y, z)
    side_textures = {
        0: _CHEST_TOP_BOTTOM,
        1: _CHEST_TOP_BOTTOM,
        2: _CHEST_SIDE,
        3: _CHEST_SIDE,
        4: _CHEST_SIDE,
        5: _CHEST_SIDE,
    }

    if pair is None:
        facing = meta if meta in _VALID_FACING else _single_native_facing(world, x, y, z)
        side_textures[facing] = _CHEST_FRONT
    else:
        axis_x = pair[0][0] != pair[1][0]
        facing = _facing_for_pair(world, pair, axis_x)
        if axis_x:
            west_has_chest = _block_type(world, x - 1, y, z) == "chest"
            base_offset = -1 if west_has_chest else 0
            for side in (2, 3):
                offset = (-1 - base_offset) if side == 3 else base_offset
                side_textures[side] = (_CHEST_SIDE + (16 if side == facing else 32)) + offset
        else:
            north_has_chest = _block_type(world, x, y, z - 1) == "chest"
            base_offset = -1 if north_has_chest else 0
            for side in (4, 5):
                offset = (-1 - base_offset) if side == 4 else base_offset
                side_textures[side] = (_CHEST_SIDE + (16 if side == facing else 32)) + offset

    return tuple(side_textures[side] for side in _FACE_ORDER)
