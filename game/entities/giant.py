from __future__ import annotations

from .monster import Monster


class Giant(Monster):

    HEALTH = 200
    ATTACK_STRENGTH = 50
    MOVE_SPEED = 0.5
    WIDTH = 3.6
    HEIGHT = 10.8
    raised_arms = True

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.skin_path = "mob/zombie.png"
        self.health = self.HEALTH
        self.prev_health = self.HEALTH
        self.attack_strength = self.ATTACK_STRENGTH
        self.MOVE_SPEED = type(self).MOVE_SPEED
        self.model_scale = 6.0

    def get_block_path_weight(self, x: int, y: int, z: int) -> float:
        return self._brightness_at(x, y, z) - 0.5

    def draw(self):
        from .mob_models import draw_living_with_model

        draw_living_with_model(self, "giant")


EntityGiant = Giant
