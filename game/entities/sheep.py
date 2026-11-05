from __future__ import annotations

from .animal import Animal
from .entity import Entity


class Sheep(Animal):
    WIDTH = 0.9
    HEIGHT = 1.3
    HEALTH = 10
    MOVE_SPEED = 0.7
    DROP_ITEM = None
    LIVING_SOUND = "mob.sheep"
    HURT_SOUND = "mob.sheep"
    DEATH_SOUND = "mob.sheep"

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.size = (self.WIDTH, self.HEIGHT, self.WIDTH)
        self.skin_path = "mob/sheep.png"
        self.health = self.HEALTH
        self.prev_health = self.HEALTH
        self.sheared = False
        self._drops_spawned = False

    def draw(self):
        from .mob_models import draw_living_with_model

        if self.sheared:
            draw_living_with_model(self, "sheep")
        else:
            draw_living_with_model(
                self,
                "sheep",
                extra_pass_kind="sheep_fur",
                extra_skin_path="mob/sheep_fur.png",
            )

    def _shear_for_living_attacker(self, attacker) -> None:
        is_living = bool(getattr(attacker, "is_living_entity", False)) or (
            attacker is not None and type(attacker).__name__ == "Player"
        )
        world = Entity.world
        multiplayer = bool(
            getattr(world, "multiplayerWorld", False)
            or getattr(world, "_multiplayer_world", False)
        )
        if self.sheared or attacker is None or not is_living or multiplayer:
            return

        self.sheared = True
        count = 1 + self._rand_int(3)
        for _ in range(count):
            item = self.drop_item("wool", 1, y_offset=1.0)
            if item is not None:
                try:
                    item.vel[1] += self._rng.random() * 0.05
                    item.vel[0] += (self._rng.random() - self._rng.random()) * 0.1
                    item.vel[2] += (self._rng.random() - self._rng.random()) * 0.1
                except Exception:
                    pass

    def attack_entity_from(self, attacker=None, amount=1) -> bool:
        self._shear_for_living_attacker(attacker)
        return super().attack_entity_from(attacker, amount)

    def on_fishing_hook_hit(self, attacker=None) -> bool:
        self._shear_for_living_attacker(attacker)
        return super().on_fishing_hook_hit(attacker)


EntitySheep = Sheep
