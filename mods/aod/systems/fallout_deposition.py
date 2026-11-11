
import math
import random
from contextlib import nullcontext

FALLOUT_RADIUS = 20
MAX_FALLOUT_PATCHES = 320


def _column_height(world, x, z):
    chunk_index = getattr(world, "_get_chunk_index", None)
    chunks = getattr(world, "chunks", None)
    if callable(chunk_index) and isinstance(chunks, dict):
        chunk = chunks.get(chunk_index(int(x), int(z)))
        if chunk is None:
            return None
        lx = int(x) - int(chunk.start_x)
        lz = int(z) - int(chunk.start_z)
        width = int(chunk.width)
        if not (0 <= lx < width and 0 <= lz < int(chunk.depth)):
            return None
        return int(chunk.heightmap[lz * width + lx])

    height = int(getattr(world, "CHUNK_HEIGHT", 128))
    for y in range(height - 1, -1, -1):
        if world.get_block_type(int(x), y, int(z)) is not None:
            return y + 1
    return 0


def _is_solid_surface(block_type, definitions):
    if block_type is None or block_type in ("ice", "fallout"):
        return False
    from game.block.material import material_for_block
    material = material_for_block(block_type, definitions.get(block_type, {}))
    return bool(getattr(material, "is_solid", False))


def deposit_fallout(world, x, y, z, rng=random):
    set_block = getattr(world, "set_block", None)
    get_block = getattr(world, "get_block_type", None)
    if not callable(set_block) or not callable(get_block):
        return 0

    from game.block import Block
    Block._load_defs()
    definitions = Block._block_defs or {}
    fallout_definition = definitions.get("fallout")
    if not isinstance(fallout_definition, dict):
        return 0

    center_x = int(math.floor(float(x) + 0.5))
    center_z = int(math.floor(float(z) + 0.5))
    radius = int(FALLOUT_RADIUS)
    candidates = []

    for dx in range(-radius, radius + 1):
        for dz in range(-radius, radius + 1):
            distance_sq = dx * dx + dz * dz
            if distance_sq > radius * radius:
                continue
            distance = math.sqrt(distance_sq)
            if distance < 3.0:
                continue
            wx, wz = center_x + dx, center_z + dz
            top = _column_height(world, wx, wz)
            if top is None or top <= 0:
                continue
            if get_block(wx, top, wz) is not None:
                continue
            below = get_block(wx, top - 1, wz)
            if not _is_solid_surface(below, definitions):
                continue
            edge_weight = 1.0 - distance / float(radius)
            chance = 0.10 + 0.28 * edge_weight
            if rng.random() <= chance:
                candidates.append((rng.random(), wx, int(top), wz))

    if not candidates:
        return 0

    candidates.sort(key=lambda item: item[0])
    selected = candidates[:MAX_FALLOUT_PATCHES]
    bulk_edit = getattr(world, "bulk_edit", None)
    context = bulk_edit() if callable(bulk_edit) else nullcontext()
    placed = 0
    with context:
        for _key, wx, wy, wz in selected:
            if get_block(wx, wy, wz) is not None:
                continue
            set_block(wx, wy, wz, "fallout", meta=0)
            placed += 1
    return placed
