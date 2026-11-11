
from __future__ import annotations

BED_FOOT_TO_HEAD = (
    (0, 1),   
    (-1, 0),  
    (0, -1),  
    (1, 0),   
)


def bed_direction(meta: int) -> int:
    return int(meta) & 3


def bed_is_head(meta: int) -> bool:
    return (int(meta) & 8) != 0


def bed_is_occupied(meta: int) -> bool:
    return (int(meta) & 4) != 0


def bed_set_occupied(world, x: int, y: int, z: int, occupied: bool) -> None:
    if world is None or world.get_block_type(x, y, z) != "bed":
        return
    meta = int(world.get_metadata(x, y, z)) & 15
    hx, hy, hz = bed_head_position(x, y, z, meta)
    if world.get_block_type(hx, hy, hz) != "bed":
        return
    head_meta = int(world.get_metadata(hx, hy, hz)) & 15
    updated = (head_meta | 4) if occupied else (head_meta & ~4)
    if updated != head_meta:
        world.set_block(hx, hy, hz, "bed", meta=updated, immediate_mesh=True)


def clear_loaded_bed_occupancy(world) -> int:
    if world is None:
        return 0
    bed_id = getattr(world, "block_type_to_id", {}).get("bed")
    if bed_id is None:
        return 0
    changed = 0
    for chunk in tuple(getattr(world, "chunks", {}).values()):
        blocks = getattr(chunk, "blocks", None)
        metadata = getattr(chunk, "metadata", None)
        height = int(getattr(chunk, "height", getattr(world, "CHUNK_HEIGHT", 128)))
        depth = int(getattr(chunk, "depth", 16))
        if blocks is None or metadata is None or height <= 0 or depth <= 0:
            continue
        for index, block_id_value in enumerate(blocks):
            if int(block_id_value) != int(bed_id) or not (int(metadata[index]) & 4):
                continue
            column, y = divmod(index, height)
            local_x, local_z = divmod(column, depth)
            x = int(getattr(chunk, "start_x", chunk.cx * 16)) + local_x
            z = int(getattr(chunk, "start_z", chunk.cz * 16)) + local_z
            meta = int(metadata[index]) & ~4
            world.set_block(x, y, z, "bed", meta=meta, immediate_mesh=True)
            changed += 1
    return changed


def bed_orientation_from_yaw(yaw_degrees: float) -> int:
    import math
    quadrant = int(math.floor(float(yaw_degrees) * 4.0 / 360.0 + 0.5)) & 3
    return (2, 1, 0, 3)[quadrant]


def bed_head_offset(direction: int) -> tuple[int, int]:
    return BED_FOOT_TO_HEAD[int(direction) & 3]


def bed_head_position(x: int, y: int, z: int, meta: int) -> tuple[int, int, int]:
    direction = bed_direction(meta)
    dx, dz = bed_head_offset(direction)
    if bed_is_head(meta):
        return int(x), int(y), int(z)
    return int(x) + dx, int(y), int(z) + dz


def bed_foot_position(x: int, y: int, z: int, meta: int) -> tuple[int, int, int]:
    direction = bed_direction(meta)
    dx, dz = bed_head_offset(direction)
    if bed_is_head(meta):
        return int(x) - dx, int(y), int(z) - dz
    return int(x), int(y), int(z)


def bed_sleep_position(x: int, y: int, z: int, meta: int) -> tuple[float, float, float]:
    hx, hy, hz = bed_head_position(x, y, z, meta)
    direction = bed_direction(meta)
    horizontal_offsets = {
        0: (0.0, 0.4),
        1: (-0.4, 0.0),
        2: (0.0, -0.4),
        3: (0.4, 0.0),
    }
    ox, oz = horizontal_offsets[direction]
    oy = 15.0 / 16.0 - 0.2 - 0.5
    return float(hx) + ox, float(hy) + oy, float(hz) + oz


def bed_sleep_camera_yaw(meta: int) -> float:
    direction = bed_direction(meta)
    return {0: 0.0, 1: 270.0, 2: 180.0, 3: 90.0}.get(direction, 0.0)


def bed_sleep_render_offset(meta: int) -> tuple[float, float]:
    direction = bed_direction(meta)
    return {
        0: (0.0, -1.8),
        1: (1.8, 0.0),
        2: (0.0, 1.8),
        3: (-1.8, 0.0),
    }.get(direction, (0.0, 0.0))


def bed_sleep_body_yaw(meta: int) -> float:
    direction = bed_direction(meta)
    return {0: 90.0, 1: 0.0, 2: 270.0, 3: 180.0}.get(direction, 0.0)


def bed_wake_yaw(meta: int) -> float:
    return bed_sleep_camera_yaw(meta)


def bed_exit_position(
    world, x: int, y: int, z: int, meta: int, *, skip_standable: int = 0,
) -> tuple[float, float, float]:
    hx, hy, hz = bed_head_position(x, y, z, meta)
    dx, dz = bed_head_offset(bed_direction(meta))

    for step in (0, 1):
        base_x = hx - dx * step - 1
        base_z = hz - dz * step - 1
        for px in range(base_x, base_x + 3):
            for pz in range(base_z, base_z + 3):
                if _standable(world, px, hy, pz):
                    if skip_standable > 0:
                        skip_standable -= 1
                        continue
                    return float(px), float(hy) - 0.4, float(pz)

    return float(hx), float(hy) + 0.6, float(hz)


def _standable(world, x: int, y: int, z: int) -> bool:
    if world is None:
        return False
    below = world.get_block_type(x, y - 1, z)
    if below is None:
        return False
    from game.block.fire_rules import is_normal_cube
    from game.block import Block

    defn = Block._block_defs.get(below, {})
    if not is_normal_cube(defn):
        return False
    if world.get_block_type(x, y, z) is not None:
        return False
    if world.get_block_type(x, y + 1, z) is not None:
        return False
    return True


def can_place_bed(world, x: int, y: int, z: int, direction: int) -> bool:
    from game.block.fire_rules import is_normal_cube
    from game.block import Block

    dx, dz = bed_head_offset(direction)
    hx, hz = x + dx, z + dz
    if world.get_block_type(x, y, z) is not None:
        return False
    if world.get_block_type(hx, y, hz) is not None:
        return False
    foot_floor = Block._block_defs.get(world.get_block_type(x, y - 1, z), {})
    head_floor = Block._block_defs.get(world.get_block_type(hx, y - 1, hz), {})
    return is_normal_cube(foot_floor) and is_normal_cube(head_floor)


def place_bed(world, x: int, y: int, z: int, direction: int) -> bool:
    direction = int(direction) & 3
    if not can_place_bed(world, x, y, z, direction):
        return False
    dx, dz = bed_head_offset(direction)
    world.set_block(x, y, z, "bed", meta=direction, immediate_mesh=True)
    world.set_block(x + dx, y, z + dz, "bed", meta=direction | 8, immediate_mesh=True)
    return True


def break_bed_pair(world, x: int, y: int, z: int) -> None:
    if world is None or world.get_block_type(x, y, z) != "bed":
        return
    meta = int(world.get_metadata(x, y, z)) & 15
    direction = bed_direction(meta)
    dx, dz = bed_head_offset(direction)
    if bed_is_head(meta):
        fx, fz = x - dx, z - dz
        world.set_block(x, y, z, None, immediate_mesh=True)
        if world.get_block_type(fx, y, fz) == "bed":
            world.set_block(fx, y, fz, None, immediate_mesh=True)
    else:
        hx, hz = x + dx, z + dz
        world.set_block(x, y, z, None, immediate_mesh=True)
        if world.get_block_type(hx, y, hz) == "bed":
            world.set_block(hx, y, hz, None, immediate_mesh=True)
