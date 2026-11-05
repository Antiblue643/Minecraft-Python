
from __future__ import annotations

import math

from .animal import Animal


class Chicken(Animal):

    WIDTH = 0.3
    HEIGHT = 0.4
    HEALTH = 4  
    MOVE_SPEED = 0.7
    DROP_ITEM = "feather"
    LIVING_SOUND = "mob.chicken"
    HURT_SOUND = "mob.chickenhurt"
    DEATH_SOUND = "mob.chickenhurt"

    EGG_MIN_TICKS = 6000
    EGG_RANDOM_TICKS = 6000

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.size = (self.WIDTH, self.HEIGHT, self.WIDTH)
        self.skin_path = "mob/chicken.png"
        self.health = self.HEALTH
        self.prev_health = self.HEALTH

        self.wing_rotation = 0.0
        self.prev_wing_rotation = 0.0
        self.dest_pos = 0.0
        self.prev_dest_pos = 0.0
        self.move_wings = 1.0

        self.time_until_next_egg = self.EGG_MIN_TICKS + self._rand_int(self.EGG_RANDOM_TICKS)

    def _living_ai_tick(self) -> None:
        super()._living_ai_tick()
        if self.dead or self.health <= 0:
            return

        self.prev_wing_rotation = self.wing_rotation
        self.prev_dest_pos = self.dest_pos
        self.dest_pos += (-1.0 if self.on_ground else 4.0) * 0.3
        self.dest_pos = max(0.0, min(1.0, self.dest_pos))

        if not self.on_ground and self.move_wings < 1.0:
            self.move_wings = 1.0
        self.move_wings *= 0.9

        if not self.on_ground and self.vel[1] < 0.0:
            self.vel[1] *= 0.6

        self.wing_rotation += self.move_wings * 2.0

        self.time_until_next_egg -= 1
        if self.time_until_next_egg <= 0:
            self.drop_item("egg", 1)
            self.time_until_next_egg = self.EGG_MIN_TICKS + self._rand_int(self.EGG_RANDOM_TICKS)

    def get_wing_rotation(self, partial_tick: float = 0.0) -> float:
        partial = max(0.0, min(1.0, float(partial_tick)))
        wing = self.prev_wing_rotation + (self.wing_rotation - self.prev_wing_rotation) * partial
        dest = self.prev_dest_pos + (self.dest_pos - self.prev_dest_pos) * partial
        return (math.sin(wing) + 1.0) * dest

    def _update_fall_distance(self, dy: float, was_on_ground: bool) -> None:
        self._fall_distance = 0.0
        self._prev_on_ground_for_fall = self.on_ground

    def draw(self):
        from .mob_models import draw_living_with_model

        draw_living_with_model(self, "chicken")


EntityChicken = Chicken
