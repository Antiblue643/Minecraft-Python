from __future__ import annotations

"""Creative-mod debug shortcuts (F4 entity spawning).

Time control moved to the /time command in the core command set.
"""


def _allowed(game) -> bool:
    from game.multiplayer.permissions import debug_tools_allowed
    return debug_tools_allowed(getattr(game, "mp_session", None))


def _spawn_debug_entity(number, world, player):
    if int(number) == 14:
        from game.entities.squid import Squid
        return Squid.spawn_near_player(world, player)
    from game.entities.debug_spawning import spawn_debug_entity
    return spawn_debug_entity(int(number), world, player)


def handle_key(game, event) -> bool:
    import pygame as pg

    world = getattr(game, "world", None)
    player = getattr(game, "player", None)
    if world is None or player is None or not _allowed(game):
        return False
    key = event.key
    mods = getattr(event, "mod", None)
    if mods is None:
        mods = pg.key.get_mods()

    if key == pg.K_F4:
        game._creative_debug_f4_held = True
        return True

    if bool(getattr(game, "_creative_debug_f4_held", False)):
        if key == pg.K_BACKQUOTE:
            from game.entities.mob import Mob
            Mob.global_ai_paused = not Mob.global_ai_paused
            return True
        if key == pg.K_0:
            _spawn_debug_entity(0, world, player)
            return True
        number = getattr(game, "num_keys", {}).get(key)
        if number is not None:
            bank = 10 if mods & pg.KMOD_CTRL else 1
            _spawn_debug_entity(number + bank, world, player)
            return True
    return False


def release_held_key(game, event) -> bool:
    import pygame as pg

    if getattr(event, "key", None) == pg.K_F4:
        game._creative_debug_f4_held = False
        return True
    return False
