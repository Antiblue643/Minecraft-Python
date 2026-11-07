from __future__ import annotations

import random


def apply_bonemeal(world, x: int, y: int, z: int, *, rng=None) -> bool:
    if world is None:
        return False

    block_type = world.get_block_type(int(x), int(y), int(z))
    if block_type == "crops":
        world.set_block(int(x), int(y), int(z), "crops", meta=7, immediate_mesh=True)
        return True

    if block_type != "sapling":
        return False

    from game.world.treegen import generate_alpha_tree, generate_alpha_big_tree

    if rng is None:
        rng = getattr(world, "_tick_rng", None) or random

    x, y, z = int(x), int(y), int(z)
    sapling_meta = int(world.get_metadata(x, y, z)) & 15
    world.set_block(x, y, z, None, immediate_mesh=True)
    if rng.randrange(10) == 0:
        grown = generate_alpha_big_tree(
            world, x, y, z, rng,
            world._is_opaque_tree_block, world.CHUNK_HEIGHT,
        )
    else:
        grown = generate_alpha_tree(
            world, x, y, z, rng,
            world._is_opaque_tree_block, world.CHUNK_HEIGHT,
        )
    if not grown:
        world.set_block(x, y, z, "sapling", meta=sapling_meta, immediate_mesh=True)
    return True
