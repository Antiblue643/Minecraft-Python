import math

from ..common import DEFAULT_RADIUS, CORRUPTION_EXCLUDED_BLOCKS

def all_block_names():
    try:
        from game.content_data import load_block_definitions
        return list(load_block_definitions().keys())
    except Exception:
        return ["stone", "dirt", "cobblestone", "sand", "gravel", "grass_block"]

def candidate_block_types(world):
    names = all_block_names()
    world_names = list((getattr(world, "block_type_to_id", None) or {}).keys())
    pool = world_names or names
    types = []
    for name in pool:
        if not name or name in CORRUPTION_EXCLUDED_BLOCKS:
            continue
        types.append(name)
    if len(types) < 3:
        for name in names:
            if (name and name not in CORRUPTION_EXCLUDED_BLOCKS
                    and name not in types):
                types.append(name)
    return types or ["stone", "dirt", "cobblestone", "sand", "gravel"]

def corrupt_around(world, cx, cy, cz, radius, rng):
    if world is None:
        return
    candidates = candidate_block_types(world)
    if not candidates:
        return
    try:
        r = max(0.0, min(16.0, float(radius)))
    except (TypeError, ValueError):
        r = DEFAULT_RADIUS
    changed = 0
    extent = int(math.ceil(r))
    for dx in range(-extent, extent + 1):
        for dy in range(-extent, extent + 1):
            for dz in range(-extent, extent + 1):
                if dx * dx + dy * dy + dz * dz > r * r + 0.01:
                    continue
                x, y, z = int(cx + dx), int(cy + dy), int(cz + dz)
                current = world.get_block_type(x, y, z)
                if current is None:
                    continue
                new_type = rng.choice(candidates)
                if new_type == current:
                    new_type = rng.choice(candidates)
                    if new_type == current:
                        continue
                try:
                    world.set_block(x, y, z, new_type, meta=rng.randrange(16))
                    changed += 1
                except Exception:
                    continue
    return changed
