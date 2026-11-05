
from __future__ import annotations

from .animal import Animal


class Cow(Animal):
    WIDTH = 0.9
    HEIGHT = 1.3
    HEALTH = 10
    MOVE_SPEED = 0.7
    DROP_ITEM = "leather"
    LIVING_SOUND = "mob.cow"
    HURT_SOUND = "mob.cowhurt"
    DEATH_SOUND = "mob.cowhurt"

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.size = (self.WIDTH, self.HEIGHT, self.WIDTH)
        self.skin_path = "mob/cow.png"
        self.health = self.HEALTH
        self.prev_health = self.HEALTH
        self._drops_spawned = False

    def draw(self):
        from .mob_models import draw_living_with_model

        draw_living_with_model(self, "cow")


EntityCow = Cow
