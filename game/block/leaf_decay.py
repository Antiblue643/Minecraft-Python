from __future__ import annotations


_LEAF = "leaves"
_LOG = "log"


def _region_chunks_loaded(world, x: int, y: int, z: int, radius: int) -> bool:
    height = int(getattr(world, "CHUNK_HEIGHT", 128))
    if y - radius < 0 or y + radius >= height:
        return False

    chunks = getattr(world, "chunks", None)
    if not isinstance(chunks, dict):
        checker = getattr(world, "check_chunks_exist", None)
        if callable(checker):
            return bool(checker(
                x - radius, y - radius, z - radius,
                x + radius, y + radius, z + radius,
            ))
        return True

    chunk_size = max(1, int(getattr(world, "CHUNK_SIZE", 16)))
    min_cx = (int(x) - radius) // chunk_size
    max_cx = (int(x) + radius) // chunk_size
    min_cz = (int(z) - radius) // chunk_size
    max_cz = (int(z) + radius) // chunk_size
    return all(
        (cx, cz) in chunks
        for cx in range(min_cx, max_cx + 1)
        for cz in range(min_cz, max_cz + 1)
    )


def on_block_removed(world, block_type: str, x: int, y: int, z: int) -> int:
    if bool(getattr(world, "_multiplayer_client_world", False)):
        return 0

    block_type = str(block_type)
    if block_type == _LOG:
        scan_radius = 4
        required_radius = 5
    elif block_type == _LEAF:
        scan_radius = 1
        required_radius = 2
    else:
        return 0

    if not _region_chunks_loaded(world, x, y, z, required_radius):
        return 0

    marked = 0
    for dx in range(-scan_radius, scan_radius + 1):
        for dy in range(-scan_radius, scan_radius + 1):
            for dz in range(-scan_radius, scan_radius + 1):
                lx, ly, lz = int(x) + dx, int(y) + dy, int(z) + dz
                if world.get_block_type(lx, ly, lz) != _LEAF:
                    continue
                if int(world.get_metadata(lx, ly, lz)) == 7:
                    continue
                world.set_block(lx, ly, lz, _LEAF, meta=7, immediate_mesh=False)
                marked += 1
    return marked


def _has_log_path(world, x: int, y: int, z: int, radius: int = 4) -> bool:
    from collections import deque

    queue = deque()
    visited_leaves = set()
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                if world.get_block_type(x + dx, y + dy, z + dz) == _LOG:
                    queue.append((dx, dy, dz, 0))

    directions = (
        (1, 0, 0), (-1, 0, 0), (0, 1, 0),
        (0, -1, 0), (0, 0, 1), (0, 0, -1),
    )
    while queue:
        dx, dy, dz, distance = queue.popleft()
        if distance >= radius:
            continue
        for sx, sy, sz in directions:
            nx, ny, nz = dx + sx, dy + sy, dz + sz
            if (abs(nx) > radius or abs(ny) > radius or abs(nz) > radius
                    or (nx, ny, nz) in visited_leaves):
                continue
            if world.get_block_type(x + nx, y + ny, z + nz) != _LEAF:
                continue
            if (nx, ny, nz) == (0, 0, 0):
                return True
            visited_leaves.add((nx, ny, nz))
            queue.append((nx, ny, nz, distance + 1))
    return False


def tick_leaves(world, x: int, y: int, z: int, rng) -> None:
    if bool(getattr(world, "_multiplayer_client_world", False)):
        return
    if world.get_block_type(x, y, z) != _LEAF:
        return
    meta = int(world.get_metadata(x, y, z))
    if meta != 7:
        return
    if not _region_chunks_loaded(world, x, y, z, 5):
        return

    if _has_log_path(world, x, y, z, radius=4):
        world.set_block(x, y, z, _LEAF, meta=0, immediate_mesh=False)
        return

    dropper = getattr(world, "drop_block_as_item", None)
    if callable(dropper):
        dropper(_LEAF, meta, x, y, z, rng=rng)
    world.set_block(x, y, z, None, immediate_mesh=False)
