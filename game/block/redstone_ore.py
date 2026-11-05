from __future__ import annotations

import random


def activate_redstone_ore(world, x: int, y: int, z: int) -> bool:
    block_type = world.get_block_type(x, y, z)
    if block_type not in ("redstone_ore", "redstone_ore_lit"):
        return False
    try:
        from game.entities.particle_events import _redstone_ore_sparkle
        _redstone_ore_sparkle(world, x, y, z, random)
    except Exception:
        pass
    if block_type == "redstone_ore":
        world.set_block(
            x, y, z, "redstone_ore_lit", meta=world.get_metadata(x, y, z),
            immediate_mesh=True,
        )
        world.schedule_block_update(x, y, z, "redstone_ore_lit", delay=30)
    return True
