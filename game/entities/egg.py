
from __future__ import annotations

import random

from game.entities.item import ItemStack
from game.entities.snowball import SnowballEntity


class EggEntity(SnowballEntity):

    MAX_AGE_TICKS = None

    def __init__(self, world, pos, velocity=(0.0, 0.0, 0.0), shooting_entity=None):
        super().__init__(world, pos, velocity=velocity, shooting_entity=shooting_entity)
        self.item = ItemStack("egg", 1)

    def _spawn_water_particles(self):
        spawn = getattr(self.world_obj, "spawn_particle", None)
        if not callable(spawn):
            return
        x, y, z = self.pos
        for _ in range(4):
            spawn(
                "bubble",
                x - self.vel[0] * 0.25,
                y - self.vel[1] * 0.25,
                z - self.vel[2] * 0.25,
                self.vel[0], self.vel[1], self.vel[2],
            )

    def _spawn_impact_particles(self):
        world = self.world_obj
        if not bool(getattr(world, "_multiplayer_client_world", False)):
            next_int = random.randrange
            if next_int(8) == 0:
                count = 4 if next_int(32) == 0 else 1
                from game.entities.chicken import Chicken
                spawn = getattr(world, "spawn_entity", None)
                if callable(spawn):
                    for _ in range(count):
                        spawn(Chicken(tuple(self.pos), yaw=self.yaw))

        super()._spawn_impact_particles()


EntityEgg = EggEntity
