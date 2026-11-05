

def debug_hitbox_entities(world):
    if world is None:
        return []
    entities = list(getattr(world, "entities", ()) or ())
    player = getattr(world, "player", None)
    if (player is not None and not any(player is item for item in entities)
            and not getattr(player, "dead", False)):
        entities.append(player)
    for remote in getattr(world, "_mp_remote_players", ()) or ():
        if (remote is not None and getattr(remote, "should_render", True)
                and not any(remote is item for item in entities)):
            entities.append(remote)
    return entities
