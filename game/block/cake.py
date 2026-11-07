

def eat_cake_slice(world, player, x, y, z) -> bool:
    if world is None or player is None:
        return False
    x, y, z = int(x), int(y), int(z)
    if world.get_block_type(x, y, z) != "cake":
        return False
    health = int(getattr(player, "health", 0))
    if health <= 0 or health >= 20:
        return False

    player.prev_health = health
    player.health = min(20, health + 3)
    metadata = int(world.get_metadata(x, y, z)) & 15
    if metadata >= 5:
        world.remove_block(x, y, z, immediate_mesh=True)
    else:
        world.set_block(x, y, z, "cake", meta=metadata + 1, immediate_mesh=True)
    return True
