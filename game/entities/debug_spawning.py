from __future__ import annotations

import random

from game.entities.creeper import Creeper
from game.entities.giant import Giant
from game.entities.ghast import Ghast
from game.entities.mob import Mob
from game.entities.monster import Monster
from game.entities.cow import Cow
from game.entities.chicken import Chicken
from game.entities.pig import Pig
from game.entities.sheep import Sheep
from game.entities.slime import Slime
from game.entities.skeleton import Skeleton
from game.entities.spider import Spider
from game.entities.zombie import Zombie
from game.entities.pigzombie import ZombiePigman

DEBUG_ENTITY_TYPES = {
    0: Cow,
    1: Mob,
    2: Monster,
    3: Zombie,
    4: Pig,
    5: Sheep,
    6: Skeleton,
    7: Spider,
    8: Creeper,
    9: Giant,
    10: Slime,
    11: Chicken,
    12: ZombiePigman,
    13: Ghast,
}


def spawn_debug_entity(number: int, world, player):
    entity_type = DEBUG_ENTITY_TYPES.get(int(number))
    if entity_type is None:
        return None
    entity = entity_type.spawn_near_player(world, player)
    if isinstance(entity, Spider) and world is not None:
        rng = getattr(world, "rng", None) or getattr(world, "random", None)
        roll = None
        if rng is not None:
            next_int = getattr(rng, "next_int", None)
            if callable(next_int):
                roll = next_int(100)
            elif hasattr(rng, "randrange"):
                roll = rng.randrange(100)
        if roll is None:
            roll = random.randrange(100)
        if roll == 0:
            skeleton = Skeleton(
                list(entity.pos),
                yaw=getattr(entity, "yaw", 0.0),
            )
            skeleton.yaw = float(getattr(entity, "yaw", 0.0))
            world.spawn_entity(skeleton)
            mount = getattr(entity, "mount_rider", None)
            if callable(mount):
                mount(skeleton)
            else:
                entity.rider = skeleton
                skeleton.riding_entity = entity
                position = getattr(entity, "_position_rider", None)
                if callable(position):
                    position()
    return entity
