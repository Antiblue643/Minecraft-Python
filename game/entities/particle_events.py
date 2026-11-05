from __future__ import annotations

import random
import time

import numpy as np

from game.world.block_sampling import (
    id_lookup_table, numpy_rng, sampled_hits, supports_vector_sampling,
)

_DISPLAY_TICK_BLOCKS = frozenset({
    "fire", "torch", "redstone_torch", "furnace_lit", "lava", "flowing_lava", "stationary_lava",
    "redstone_wire", "redstone_ore", "redstone_ore_lit", "portal",
})

def _emit(world, name, x, y, z, mx=0.0, my=0.0, mz=0.0):
    spawn = getattr(world, "spawn_particle", None)
    if callable(spawn):
        spawn(name, x - 0.5, y - 0.5, z - 0.5, mx, my, mz)
        return
    effects = getattr(world, "effects", None)
    if effects is not None:
        effects.spawn_particle(name, x - 0.5, y - 0.5, z - 0.5, mx, my, mz, world=world)


def _block_definition(world, block_type):
    try:
        from game.block import Block
        return Block._block_defs.get(block_type, {})
    except Exception:
        return {}


def _catches_fire(world, x, y, z):
    block_type = world.get_block_type(x, y, z)
    return bool(_block_definition(world, block_type).get("catchFire", 0)) if block_type else False


def _is_normal_cube(world, x, y, z):
    block_type = world.get_block_type(x, y, z)
    if block_type is None:
        return False
    definition = _block_definition(world, block_type)
    return (
        definition.get("collision", True) is not False
        and definition.get("model", "cube") == "cube"
        and not definition.get("transparent", False)
        and definition.get("nCull", "all") == "all"
        and block_type not in ("water", "lava", "fire", "flowing_water", "flowing_lava")
    )


def _fire_display_tick(world, x, y, z, rng):
    below_is_normal = _is_normal_cube(world, x, y - 1, z)
    below_catches = _catches_fire(world, x, y - 1, z)
    if not below_is_normal and not below_catches:
        if _catches_fire(world, x - 1, y, z):
            for _ in range(2):
                _emit(world, "largesmoke", x + rng.random() * 0.1,
                      y + rng.random(), z + rng.random())
        if _catches_fire(world, x + 1, y, z):
            for _ in range(2):
                _emit(world, "largesmoke", x + 1 - rng.random() * 0.1,
                      y + rng.random(), z + rng.random())
        if _catches_fire(world, x, y, z - 1):
            for _ in range(2):
                _emit(world, "largesmoke", x + rng.random(), y + rng.random(),
                      z + rng.random() * 0.1)
        if _catches_fire(world, x, y, z + 1):
            for _ in range(2):
                _emit(world, "largesmoke", x + rng.random(), y + rng.random(),
                      z + 1 - rng.random() * 0.1)
        if _catches_fire(world, x, y + 1, z):
            for _ in range(2):
                _emit(world, "largesmoke", x + rng.random(),
                      y + 1 - rng.random() * 0.1, z + rng.random())
    else:
        for _ in range(3):
            _emit(world, "largesmoke", x + rng.random(),
                  y + rng.random() * 0.5 + 0.5, z + rng.random())


def _torch_offset(meta):
    px, py, pz = 0.5, 0.7, 0.5
    if meta == 1:
        return px - 0.27, py + 0.22, pz
    if meta == 2:
        return px + 0.27, py + 0.22, pz
    if meta == 3:
        return px, py + 0.22, pz - 0.27
    if meta == 4:
        return px, py + 0.22, pz + 0.27
    return px, py, pz


def _torch_display_tick(world, x, y, z, rng):
    meta = int(world.get_metadata(x, y, z)) & 15
    ox, oy, oz = _torch_offset(meta)
    px, py, pz = x + ox, y + oy, z + oz
    _emit(world, "smoke", px, py, pz)
    _emit(world, "flame", px, py, pz)


def _redstone_torch_display_tick(world, x, y, z, rng):
    meta = int(world.get_metadata(x, y, z)) & 15
    jitter = lambda: (rng.random() - 0.5) * 0.2
    px = x + 0.5 + jitter()
    py = y + 0.7 + jitter()
    pz = z + 0.5 + jitter()
    side, up = 0.27, 0.22
    if meta == 1:
        px, py = px - side, py + up
    elif meta == 2:
        px, py = px + side, py + up
    elif meta == 3:
        pz, py = pz - side, py + up
    elif meta == 4:
        pz, py = pz + side, py + up
    _emit(world, "reddust", px, py, pz)


def _redstone_wire_display_tick(world, x, y, z, rng):
    power = int(world.get_metadata(x, y, z)) & 15
    if power <= 0:
        return
    jitter = lambda: (rng.random() - 0.5) * 0.2
    px = x + 0.5 + jitter()
    py = y + 1.0 / 16.0
    pz = z + 0.5 + jitter()
    _emit(world, "reddust", px, py, pz)


def _redstone_ore_sparkle(world, x, y, z, rng):
    edge = 1.0 / 16.0

    def normal_cube(bx, by, bz):
        return _is_normal_cube(world, bx, by, bz)

    for face in range(6):
        px = (x - 0.5) + rng.random()
        py = (y - 0.5) + rng.random()
        pz = (z - 0.5) + rng.random()
        if face == 0 and not normal_cube(x, y + 1, z):
            py = (y + 0.5) + edge
        elif face == 1 and not normal_cube(x, y - 1, z):
            py = (y - 0.5) - edge
        elif face == 2 and not normal_cube(x, y, z + 1):
            pz = (z + 0.5) + edge
        elif face == 3 and not normal_cube(x, y, z - 1):
            pz = (z - 0.5) - edge
        elif face == 4 and not normal_cube(x + 1, y, z):
            px = (x + 0.5) + edge
        elif face == 5 and not normal_cube(x - 1, y, z):
            px = (x - 0.5) - edge
        else:
            continue
        if (px < x - 0.5 or px > x + 0.5
                or py < y - 0.5 or py > y + 0.5
                or pz < z - 0.5 or pz > z + 0.5):
            _emit(world, "reddust", px + 0.5, py + 0.5, pz + 0.5)


def _redstone_ore_display_tick(world, x, y, z, rng):
    block_type = world.get_block_type(x, y, z)
    if block_type == "redstone_ore_lit":
        _redstone_ore_sparkle(world, x, y, z, rng)


def _furnace_display_tick(world, x, y, z, rng):
    meta = int(world.get_metadata(x, y, z)) & 15
    if meta not in (2, 3, 4, 5):
        return
    px, py, pz = x + 0.5, y + rng.random() * 6.0 / 16.0, z + 0.5
    side_noise = rng.random() * 0.6 - 0.3
    if meta == 4:
        px, pz = px - 0.52, pz + side_noise
    elif meta == 5:
        px, pz = px + 0.52, pz + side_noise
    elif meta == 2:
        px, pz = px + side_noise, pz - 0.52
    else:
        px, pz = px + side_noise, pz + 0.52
    _emit(world, "smoke", px, py, pz)
    _emit(world, "flame", px, py, pz)


def _lava_display_tick(world, x, y, z, rng):
    if world.get_block_type(x, y + 1, z) is not None:
        return
    if rng.randrange(100) != 0:
        return
    _emit(world, "lava", x + rng.random(), y + 1.0, z + rng.random())


def _portal_display_tick(world, x, y, z, rng):
    from game.block.portal_rules import portal_component
    component = portal_component(world, (int(x), int(y), int(z)))
    if rng.randrange(100) == 0:
        sound_player = getattr(world, "sound_player", None)
        now = time.monotonic()
        component_key = frozenset(component)
        last_played = getattr(world, "_portal_ambient_last_played", {})
        if (callable(sound_player)
                and now - last_played.get(component_key, float("-inf")) >= 2.0):
            sound_player(
                "portal.portal", volume=1.0,
                pitch=rng.random() * 0.4 + 0.8,
                position=tuple(
                    (min(pos[axis] for pos in component)
                     + max(pos[axis] for pos in component)) / 2.0 + 0.5
                    for axis in range(3)
                ),
            )
            last_played[component_key] = now
            world._portal_ambient_last_played = last_played
    for _ in range(4):
        px = x + rng.random()
        py = y + rng.random()
        pz = z + rng.random()
        mx = (rng.random() - 0.5) * 0.5
        my = (rng.random() - 0.5) * 0.5
        mz = (rng.random() - 0.5) * 0.5
        side = rng.randrange(2) * 2 - 1
        if (world.get_block_type(x - 1, y, z) != "portal"
                and world.get_block_type(x + 1, y, z) != "portal"):
            px = x + 0.5 + 0.25 * side
            mx = rng.random() * 2.0 * side
        else:
            pz = z + 0.5 + 0.25 * side
            mz = rng.random() * 2.0 * side
        _emit(world, "portal", px, py, pz, mx, my, mz)


def random_display_tick(world, x, y, z, rng=None):
    if rng is None:
        rng = random
    block_type = world.get_block_type(x, y, z)
    if block_type == "fire":
        _fire_display_tick(world, x, y, z, rng)
    elif block_type == "torch":
        _torch_display_tick(world, x, y, z, rng)
    elif block_type == "redstone_torch":
        _redstone_torch_display_tick(world, x, y, z, rng)
    elif block_type == "redstone_wire":
        _redstone_wire_display_tick(world, x, y, z, rng)
    elif block_type in ("redstone_ore", "redstone_ore_lit"):
        _redstone_ore_display_tick(world, x, y, z, rng)
    elif block_type == "furnace_lit":
        _furnace_display_tick(world, x, y, z, rng)
    elif block_type in ("lava", "flowing_lava", "stationary_lava"):
        _lava_display_tick(world, x, y, z, rng)
    elif block_type == "portal":
        _portal_display_tick(world, x, y, z, rng)


def run_random_display_updates(world, player_pos, rng=None, samples=1000):
    if rng is None:
        rng = random
    px, py, pz = (int(__import__("math").floor(float(player_pos[i]) + 0.5)) for i in range(3))
    height = int(getattr(world, "CHUNK_HEIGHT", 128))
    samples = max(0, int(samples))
    if supports_vector_sampling(world):
        gen = numpy_rng(world, rng)
        offsets = gen.integers(0, 16, size=(6, samples), dtype=np.int64)
        lut = id_lookup_table(world, _DISPLAY_TICK_BLOCKS)
        for x, y, z, bid in sampled_hits(
                world,
                px + offsets[0] - offsets[1],
                py + offsets[2] - offsets[3],
                pz + offsets[4] - offsets[5],
                lut):
            random_display_tick(world, x, y, z, rng)
        return
    for _ in range(samples):
        x = px + rng.randrange(16) - rng.randrange(16)
        y = py + rng.randrange(16) - rng.randrange(16)
        z = pz + rng.randrange(16) - rng.randrange(16)
        if 0 <= y < height and world.get_block_type(x, y, z) is not None:
            random_display_tick(world, x, y, z, rng)
