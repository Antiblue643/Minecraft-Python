from __future__ import annotations

import random
import time

import numpy as np

from game.block.material import material_for_block
from game.world.block_sampling import (
    id_lookup_table, numpy_rng, sampled_hits, supports_vector_sampling,
)

TICKABLE = frozenset({
    "grass_block",
    "farmland",
    "crops",
    "sapling",
    "reed",
    "cactus",
    "leaves",
    "yellow_flower",
    "red_flower",
    "brown_mushroom",
    "red_mushroom",
    "snow",
    "ice",
})

SAMPLES_PER_TICK = 5000
TICK_RADIUS = 64

_accum = {"samples": 0, "hits": 0, "by_type": {}, "ticks": 0}
_display = {
    "samples": 0,
    "hits": 0,
    "hits_per_tick": 0.0,
    "by_type": {},
    "ticks": 0,
    "tps": 0.0,
}


def get_tick_stats():
    return _display


def _is_water(world, x, y, z):
    t = world.get_block_type(x, y, z)
    return t in ("water", "water_still", "water_flowing", "stationary_water")


def _blocks_grass(world, x, y, z):
    block_type = world.get_block_type(x, y, z)
    if block_type is None:
        return False
    return material_for_block(block_type).can_block_grass


def tick_grass(world, x, y, z, rng):
    light_above = world.get_light(x, y + 1, z)
    if light_above < 4 and _blocks_grass(world, x, y + 1, z):
        if rng.randrange(4) != 0:
            return
        world.set_block(x, y, z, "dirt")
    elif light_above >= 9:
        nx = x + rng.randrange(3) - 1
        ny = y + rng.randrange(5) - 3
        nz = z + rng.randrange(3) - 1
        if world.get_block_type(nx, ny, nz) != "dirt":
            return
        if world.get_light(nx, ny + 1, nz) < 4:
            return
        if _blocks_grass(world, nx, ny + 1, nz):
            return
        world.set_block(nx, ny, nz, "grass_block")


def _water_nearby(world, x, y, z):
    for dx in range(-4, 5):
        for dy in range(0, 2):
            for dz in range(-4, 5):
                if _is_water(world, x + dx, y + dy, z + dz):
                    return True
    return False


def _crops_nearby(world, x, y, z):
    return world.get_block_type(x, y + 1, z) == "crops"


def tick_farmland(world, x, y, z, rng):
    if rng.randrange(5) != 0:
        return
    meta = world.get_metadata(x, y, z)
    if _water_nearby(world, x, y, z):
        if meta != 7:
            world.set_block(x, y, z, "farmland", meta=7)
    else:
        if meta > 0:
            world.set_block(x, y, z, "farmland", meta=meta - 1)
        elif not _crops_nearby(world, x, y, z):
            world.set_block(x, y, z, "dirt")


def _crop_growth_chance(world, x, y, z):
    chance = 1.0
    for dx in range(-1, 2):
        for dz in range(-1, 2):
            below = world.get_block_type(x + dx, y - 1, z + dz)
            factor = 0.0
            if below == "farmland":
                factor = 1.0
                if world.get_metadata(x + dx, y - 1, z + dz) > 0:
                    factor = 3.0
            if dx != 0 or dz != 0:
                factor /= 4.0
            chance += factor

    n = world.get_block_type(x, y, z - 1) == "crops"
    s = world.get_block_type(x, y, z + 1) == "crops"
    w = world.get_block_type(x - 1, y, z) == "crops"
    e = world.get_block_type(x + 1, y, z) == "crops"
    diag = any(
        world.get_block_type(x + dx, y, z + dz) == "crops"
        for dx, dz in ((-1, -1), (1, -1), (1, 1), (-1, 1))
    )
    if diag or ((w or e) and (n or s)):
        chance /= 2.0
    return max(chance, 0.01)


def tick_crops(world, x, y, z, rng):
    if world.get_block_type(x, y - 1, z) != "farmland":
        world.set_block(x, y, z, None)
        return
    if world.get_light(x, y + 1, z) < 9:
        return
    meta = world.get_metadata(x, y, z)
    if meta >= 7:
        return
    growth = _crop_growth_chance(world, x, y, z)
    threshold = max(1, int(100.0 / growth))
    if rng.randrange(threshold) == 0:
        world.set_block(x, y, z, "crops", meta=meta + 1)


def tick_sapling(world, x, y, z, rng):
    if world.get_light(x, y + 1, z) < 9:
        return
    if rng.randrange(5) != 0:
        return
    meta = world.get_metadata(x, y, z)
    if meta < 15:
        world.set_block(x, y, z, "sapling", meta=meta + 1)
        return
    from game.world.treegen import generate_alpha_tree, generate_alpha_big_tree
    world.set_block(x, y, z, None)
    if rng.randrange(10) == 0:
        grown = generate_alpha_big_tree(
            world, x, y, z, rng,
            world._is_opaque_tree_block, world.CHUNK_HEIGHT,
        )
        if not grown:
            grown = generate_alpha_tree(
                world, x, y, z, rng,
                world._is_opaque_tree_block, world.CHUNK_HEIGHT,
            )
    else:
        grown = generate_alpha_tree(
            world, x, y, z, rng,
            world._is_opaque_tree_block, world.CHUNK_HEIGHT,
        )
    if not grown:
        world.set_block(x, y, z, "sapling", meta=15)


def tick_reed(world, x, y, z, rng):
    if world.get_block_type(x, y + 1, z) is not None:
        return
    height = 1
    while height < 3 and world.get_block_type(x, y - height, z) == "reed":
        height += 1
    if height >= 3:
        return
    meta = int(world.get_metadata(x, y, z)) if hasattr(world, "get_metadata") else 0
    if meta >= 8:
        world.set_block(x, y + 1, z, "reed", meta=0)
        world.set_block(x, y, z, "reed", meta=0)
    else:
        world.set_block(x, y, z, "reed", meta=meta + 1)


def tick_cactus(world, x, y, z, rng):
    if world.get_block_type(x, y + 1, z) is not None:
        return

    height = 1
    while height < 3 and world.get_block_type(x, y - height, z) == "cactus":
        height += 1
    if height >= 3:
        return

    meta = int(world.get_metadata(x, y, z)) if hasattr(world, "get_metadata") else 0
    if meta >= 15:
        world.set_block(x, y + 1, z, "cactus", meta=0)
        world.set_block(x, y, z, "cactus", meta=0)
    else:
        world.set_block(x, y, z, "cactus", meta=meta + 1)


def tick_leaves(world, x, y, z, rng):
    from game.block.leaf_decay import tick_leaves as _tick_leaves
    return _tick_leaves(world, x, y, z, rng)


def _catch_fire_chance(block_type):
    from game.block import Block
    Block._load_defs()
    return int(Block._block_defs.get(block_type or "", {}).get("catchFire", 0))


def _encourage_fire_chance(block_type):
    from game.block import Block
    Block._load_defs()
    return int(Block._block_defs.get(block_type or "", {}).get("encourageFire", 0))


def _is_normal_cube(world, x, y, z):
    t = world.get_block_type(x, y, z)
    if t is None:
        return False
    from game.block import Block
    Block._load_defs()
    d = Block._block_defs.get(t, {})
    return (
        d.get("collision", True) is not False
        and d.get("model", "cube") == "cube"
        and not d.get("transparent", False)
        and d.get("nCull", "all") == "all"
        and t not in ("water", "lava", "fire")
    )


def _neighbor_encourages_fire(world, x, y, z):
    for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
        if _catch_fire_chance(world.get_block_type(x + dx, y + dy, z + dz)) > 0:
            return True
    return False


def _chance_neighbors_encourage(world, x, y, z):
    best = 0
    for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
        best = max(best, _encourage_fire_chance(world.get_block_type(x + dx, y + dy, z + dz)))
    return best


def _try_catch_block_on_fire(world, x, y, z, chance, rng):
    ability = _catch_fire_chance(world.get_block_type(x, y, z))
    if ability <= 0:
        return
    if rng.randrange(chance) < ability:
        if rng.randrange(2) == 0:
            world.set_block(x, y, z, "fire", meta=0, immediate_mesh=False)
        else:
            world.set_block(x, y, z, None, immediate_mesh=False)


def tick_fire(world, x, y, z, rng):
    meta = world.get_metadata(x, y, z) if hasattr(world, "get_metadata") else 0
    meta = int(meta) & 15
    if meta < 15:
        world.set_block(x, y, z, "fire", meta=meta + 1, immediate_mesh=False)

    on_netherrack = world.get_block_type(x, y - 1, z) == "netherrack"
    if not on_netherrack and not _neighbor_encourages_fire(world, x, y, z):
        if not _is_normal_cube(world, x, y - 1, z) or meta > 3:
            world.set_block(x, y, z, None, immediate_mesh=False)
            return
    elif (not on_netherrack and not _catch_fire_chance(world.get_block_type(x, y - 1, z))
          and meta == 15 and rng.randrange(4) == 0):
        world.set_block(x, y, z, None, immediate_mesh=False)
        return

    if meta % 5 == 0 and meta > 5:
        _try_catch_block_on_fire(world, x + 1, y, z, 300, rng)
        _try_catch_block_on_fire(world, x - 1, y, z, 300, rng)
        _try_catch_block_on_fire(world, x, y - 1, z, 100, rng)
        _try_catch_block_on_fire(world, x, y + 1, z, 200, rng)
        _try_catch_block_on_fire(world, x, y, z - 1, 300, rng)
        _try_catch_block_on_fire(world, x, y, z + 1, 300, rng)

        for nx in range(x - 1, x + 2):
            for nz in range(z - 1, z + 2):
                for ny in range(y - 1, y + 5):
                    if nx == x and ny == y and nz == z:
                        continue
                    if world.get_block_type(nx, ny, nz) is not None:
                        continue
                    chance = 100
                    if ny > y + 1:
                        chance += (ny - (y + 1)) * 100
                    encourage = _chance_neighbors_encourage(world, nx, ny, nz)
                    if encourage > 0 and rng.randrange(chance) <= encourage:
                        world.set_block(nx, ny, nz, "fire", meta=0, immediate_mesh=False)


BURNABLE_BLOCKS = frozenset({
    "planks", "stairs", "log", "leaves", "wool", "tnt", "bookshelf",
    "crafting_table", "chest", "wooden_door", "sign", "wall_sign",
    "wooden_pressure_plate", "jukebox",
})


def tick_stationary_lava(world, x, y, z, rng):
    from game.block.support_rules import _material_is_solid
    if world.get_block_type(x, y, z) != "lava":
        return
    for _ in range(rng.randrange(3)):
        x += rng.randrange(3) - 1
        y += 1
        z += rng.randrange(3) - 1
        target = world.get_block_type(x, y, z)
        if target is None:
            for dx, dy, dz in ((-1, 0, 0), (1, 0, 0), (0, 0, -1), (0, 0, 1), (0, -1, 0), (0, 1, 0)):
                if world.get_block_type(x + dx, y + dy, z + dz) in BURNABLE_BLOCKS:
                    world.set_block(x, y, z, "fire", meta=0)
                    return
        elif _material_is_solid(target):
            return


def _saved_block_light(world, x, y, z):
    getter = getattr(world, "_get_saved_light", None)
    if getter is None:
        return 0
    return int(getter("block", int(x), int(y), int(z)))


def tick_snow(world, x, y, z, rng):
    if _saved_block_light(world, x, y, z) > 11:
        world.set_block(x, y, z, None)


def tick_ice(world, x, y, z, rng):
    if _saved_block_light(world, x, y, z) > 11 - 3:
        world.set_block(x, y, z, None)


def tick_snowfall(world, player_x, player_z, rng=None):
    if not getattr(world, "snow_covered", False):
        return 0
    if rng is None:
        rng = random
    chunks = getattr(world, "chunks", None)
    if not isinstance(chunks, dict):
        return 0
    from game.block.support_rules import _material_is_solid
    size = int(getattr(world, "CHUNK_SIZE", 16))
    height = int(getattr(world, "CHUNK_HEIGHT", 128))
    px, pz = int(player_x), int(player_z)
    changed = 0
    for chunk in tuple(chunks.values()):
        if (abs(chunk.start_x + size // 2 - px) > TICK_RADIUS
                or abs(chunk.start_z + size // 2 - pz) > TICK_RADIUS):
            continue
        if rng.randrange(4) != 0:
            continue
        lx = rng.randrange(size)
        lz = rng.randrange(size)
        y = int(chunk.heightmap[lz * chunk.width + lx])
        if y <= 0 or y >= height:
            continue
        x = chunk.start_x + lx
        z = chunk.start_z + lz
        if _saved_block_light(world, x, y, z) >= 10:
            continue
        if world.get_block_type(x, y, z) is not None:
            continue
        below = world.get_block_type(x, y - 1, z)
        if below is None:
            continue
        if below != "ice" and _material_is_solid(below):
            world.set_block(x, y, z, "snow")
            changed += 1
        if below == "water" and world.get_metadata(x, y - 1, z) == 0:
            world.set_block(x, y - 1, z, "ice")
            changed += 1
    return changed


def _checked(handler=None):
    def tick(world, x, y, z, rng):
        from game.block.support_rules import check_support
        if check_support(world, x, y, z):
            return
        if handler is not None:
            handler(world, x, y, z, rng)
    return tick


HANDLERS = {
    "grass_block": tick_grass,
    "farmland": tick_farmland,
    "crops": _checked(tick_crops),
    "sapling": _checked(tick_sapling),
    "reed": _checked(tick_reed),
    "cactus": _checked(tick_cactus),
    "leaves": tick_leaves,
    "fire": tick_fire,
    "yellow_flower": _checked(),
    "red_flower": _checked(),
    "brown_mushroom": _checked(),
    "red_mushroom": _checked(),
    "snow": tick_snow,
    "ice": tick_ice,
}


def run_random_ticks(world, player_x, player_z, rng=None, samples=SAMPLES_PER_TICK):
    global _accum, _display

    if rng is None:
        rng = random
    fire_started = time.perf_counter()
    tick_snowfall(world, player_x, player_z, rng=rng)
    tick_nearby_fire(world, player_x, player_z, rng=rng)
    nearby_fire_ms = (time.perf_counter() - fire_started) * 1000.0
    handler_ms = 0.0
    max_handler_ms = 0.0
    slow_handlers = []
    px = int(player_x)
    pz = int(player_z)
    r = TICK_RADIUS
    hits = 0
    by_type = {}
    chunks = world.chunks
    height = world.CHUNK_HEIGHT
    get_index = world._get_chunk_index

    vector_hits = None
    if supports_vector_sampling(world):
        gen = numpy_rng(world, rng)
        bits = gen.integers(0, 1 << 21, size=int(samples), dtype=np.int64)
        lut = id_lookup_table(world, TICKABLE | {"water", "lava"})
        vector_hits = sampled_hits(
            world,
            px + (bits & 127) - r,
            (bits >> 14) & 127,
            pz + ((bits >> 7) & 127) - r,
            lut,
        )
        id_to_type = world.id_to_block_type

    if vector_hits is not None:
        sample_iter = ()
        for x, y, z, bid in vector_hits:
            btype = id_to_type[bid]
            if world.get_block_type(x, y, z) != btype:
                continue
            if btype not in TICKABLE:
                scheduler = getattr(world, "_fluid_scheduler", None)
                if scheduler is not None and scheduler.is_moving(x, y, z):
                    from game.world.fluid_physics import tick_fluid
                    handler_started = time.perf_counter()
                    tick_fluid(world, x, y, z, rng=rng, scheduler=scheduler)
                    elapsed_ms = (time.perf_counter() - handler_started) * 1000.0
                    handler_ms += elapsed_ms
                    max_handler_ms = max(max_handler_ms, elapsed_ms)
                    if elapsed_ms >= 1.0 and len(slow_handlers) < 8:
                        slow_handlers.append({
                            "type": f"{btype}_flowing", "x": int(x), "y": int(y),
                            "z": int(z), "duration_ms": round(elapsed_ms, 3),
                        })
                    hits += 1
                    tick_type = f"{btype}_flowing"
                    by_type[tick_type] = by_type.get(tick_type, 0) + 1
                elif btype == "lava":
                    tick_stationary_lava(world, x, y, z, rng)
                continue
            handler = HANDLERS.get(btype)
            if handler is None:
                continue
            handler_started = time.perf_counter()
            handler(world, x, y, z, rng)
            elapsed_ms = (time.perf_counter() - handler_started) * 1000.0
            handler_ms += elapsed_ms
            max_handler_ms = max(max_handler_ms, elapsed_ms)
            if elapsed_ms >= 1.0 and len(slow_handlers) < 8:
                slow_handlers.append({
                    "type": str(btype), "x": int(x), "y": int(y),
                    "z": int(z), "duration_ms": round(elapsed_ms, 3),
                })
            hits += 1
            by_type[btype] = by_type.get(btype, 0) + 1
    else:
        sample_iter = range(samples)

    for _ in sample_iter:
        bits = rng.getrandbits(21)
        x = px + (bits & 127) - r
        z = pz + ((bits >> 7) & 127) - r
        y = (bits >> 14) & 127
        if y >= height:
            continue
        cx, cz = get_index(x, z)
        chunk = chunks.get((cx, cz))
        if chunk is None:
            continue
        lx = x - chunk.start_x
        lz = z - chunk.start_z
        if not (0 <= lx < chunk.width and 0 <= lz < chunk.depth):
            continue
        bid = chunk.blocks[(lx * chunk.depth + lz) * chunk.height + y]
        if bid == 0:
            continue
        id_to = chunk._id_to_block_type
        if bid >= len(id_to):
            continue
        btype = id_to[bid]
        if btype not in TICKABLE:
            if btype in ("water", "lava"):
                scheduler = getattr(world, "_fluid_scheduler", None)
                if scheduler is not None and scheduler.is_moving(x, y, z):
                    from game.world.fluid_physics import tick_fluid
                    handler_started = time.perf_counter()
                    tick_fluid(world, x, y, z, rng=rng, scheduler=scheduler)
                    elapsed_ms = (time.perf_counter() - handler_started) * 1000.0
                    handler_ms += elapsed_ms
                    max_handler_ms = max(max_handler_ms, elapsed_ms)
                    if elapsed_ms >= 1.0 and len(slow_handlers) < 8:
                        slow_handlers.append({
                            "type": f"{btype}_flowing", "x": int(x), "y": int(y),
                            "z": int(z), "duration_ms": round(elapsed_ms, 3),
                        })
                    hits += 1
                    tick_type = f"{btype}_flowing"
                    by_type[tick_type] = by_type.get(tick_type, 0) + 1
                elif btype == "lava":
                    tick_stationary_lava(world, x, y, z, rng)
            continue
        handler = HANDLERS.get(btype)
        if handler is None:
            continue
        handler_started = time.perf_counter()
        handler(world, x, y, z, rng)
        elapsed_ms = (time.perf_counter() - handler_started) * 1000.0
        handler_ms += elapsed_ms
        max_handler_ms = max(max_handler_ms, elapsed_ms)
        if elapsed_ms >= 1.0 and len(slow_handlers) < 8:
            slow_handlers.append({
                "type": str(btype), "x": int(x), "y": int(y),
                "z": int(z), "duration_ms": round(elapsed_ms, 3),
            })
        hits += 1
        by_type[btype] = by_type.get(btype, 0) + 1

    _accum["samples"] += samples
    _accum["hits"] += hits
    _accum["ticks"] += 1
    for k, v in by_type.items():
        _accum["by_type"][k] = _accum["by_type"].get(k, 0) + v

    if _accum["ticks"] >= 20:
        t = max(1, _accum["ticks"])
        _display = {
            "samples": _accum["samples"] // t,
            "hits": _accum["hits"],
            "hits_per_tick": _accum["hits"] / t,
            "by_type": dict(_accum["by_type"]),
            "ticks": _accum["ticks"],
            "tps": float(_accum["ticks"]),
        }
        _accum = {"samples": 0, "hits": 0, "by_type": {}, "ticks": 0}

    return {
        "samples": int(samples), "hits": int(hits), "by_type": dict(by_type),
        "nearby_fire_ms": round(nearby_fire_ms, 3),
        "handler_ms": round(handler_ms, 3),
        "max_handler_ms": round(max_handler_ms, 3),
        "slow_handlers": slow_handlers,
    }


def tick_nearby_fire(world, player_x, player_z, rng=None, radius=None, samples=64):
    if rng is None:
        rng = random
    try:
        tick_count = int(getattr(world, "_fire_update_counter", 0)) + 1
        world._fire_update_counter = tick_count
    except Exception:
        tick_count = 1
    if tick_count % 20:
        return 0

    chunks = getattr(world, "chunks", None)
    id_map = getattr(world, "block_type_to_id", None)
    if isinstance(chunks, dict) and isinstance(id_map, dict):
        fire_id = id_map.get("fire")
        if fire_id is None and hasattr(world, "_ensure_block_type_id"):
            fire_id = world._ensure_block_type_id("fire")
        if fire_id is None or not (0 < int(fire_id) < 256):
            return 0
        marker = bytes((int(fire_id),))
        px, pz = int(player_x), int(player_z)
        height = int(getattr(world, "CHUNK_HEIGHT", 128))
        chunk_size = int(getattr(world, "CHUNK_SIZE", 16))
        if radius is None:
            radius = (int(getattr(world, "view_distance", 5)) + 1) * chunk_size
        radius = max(0, int(radius))
        fire_index = getattr(world, "_fire_index", None)
        nearby = getattr(fire_index, "nearby", None)
        if callable(nearby):
            fire_cells = nearby(px, pz, radius)
        else:
            fire_cells = []
            min_cx, max_cx = (px - radius) // chunk_size, (px + radius) // chunk_size
            min_cz, max_cz = (pz - radius) // chunk_size, (pz + radius) // chunk_size
            for (cx, cz), chunk in chunks.items():
                if not (min_cx <= cx <= max_cx and min_cz <= cz <= max_cz):
                    continue
                data = chunk.blocks
                depth = int(chunk.depth)
                chunk_height = int(chunk.height)
                search_from = 0
                while True:
                    index = data.find(marker, search_from)
                    if index < 0:
                        break
                    y = index % chunk_height
                    lz = (index // chunk_height) % depth
                    lx = index // (chunk_height * depth)
                    x, z = int(chunk.start_x + lx), int(chunk.start_z + lz)
                    if (y < height and abs(x - px) <= radius and abs(z - pz) <= radius):
                        fire_cells.append((x, y, z))
                    search_from = index + 1
        live_fire_cells = [
            (x, y, z) for x, y, z in fire_cells
            if world.get_block_type(x, y, z) == "fire"
        ]
        for x, y, z in live_fire_cells:
            tick_fire(world, x, y, z, rng)
        return len(live_fire_cells)

    block_map = getattr(world, "blocks", None)
    if isinstance(block_map, dict):
        px, pz = int(player_x), int(player_z)
        radius = 24 if radius is None else max(0, int(radius))
        fire_cells = [
            (int(x), int(y), int(z)) for (x, y, z), block in tuple(block_map.items())
            if block == "fire" and abs(int(x) - px) <= radius and abs(int(z) - pz) <= radius
        ]
        for x, y, z in fire_cells:
            tick_fire(world, x, y, z, rng)
        return len(fire_cells)
    return 0
