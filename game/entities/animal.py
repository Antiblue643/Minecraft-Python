from __future__ import annotations

import math

from .creature import Creature
from .entity import Entity


class Animal(Creature):
    is_hostile = False
    HEALTH = 10
    MOVE_SPEED = 0.7

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.health = self.HEALTH
        self.prev_health = self.HEALTH
        self.MOVE_SPEED = type(self).MOVE_SPEED

    def find_player_to_attack(self):
        return None

    def get_block_path_weight(self, x: int, y: int, z: int) -> float:
        world = Entity.world
        if world is not None:
            try:
                below = world.get_block_type(x, y - 1, z)
                if below == "grass_block":
                    return 10.0
            except Exception:
                pass
        return self._brightness_at(x, y, z) - 0.5

    def get_can_spawn_here(self, x: float, y: float, z: float) -> bool:
        cell = tuple(math.floor(v + 0.5) for v in (x, y, z))
        world = Entity.world
        below = world.get_block_type(cell[0], cell[1] - 1, cell[2]) if world is not None else None
        if below != "grass_block":
            return False
        light = self._light_level_at(*cell)
        return light > 8 and super().get_can_spawn_here(x, y, z)


EntityAnimal = Animal
