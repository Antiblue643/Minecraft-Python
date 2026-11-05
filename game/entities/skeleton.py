from __future__ import annotations

import math

from .entity import Entity
from .monster import Monster
from .zombie import Zombie


class Skeleton(Monster):
    HEALTH = 20
    ATTACK_STRENGTH = 2
    MOVE_SPEED = 0.7
    ATTACK_RANGE = 10.0
    DROP_ITEM = "arrow"
    LIVING_SOUND = "mob.skeleton"
    HURT_SOUND = "mob.skeletonhurt"
    DEATH_SOUND = "mob.skeletondeath"

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.skin_path = "mob/skeleton.png"
        self.health = self.HEALTH
        self.prev_health = self.HEALTH
        self.attack_strength = self.ATTACK_STRENGTH
        self.MOVE_SPEED = type(self).MOVE_SPEED
        self._drops_spawned = False
        from .item import ItemStack
        self._equipped_item = ItemStack("bow", 1)
        self.raised_arms = True

    def draw(self):
        from .mob_models import draw_living_with_model

        draw_living_with_model(self, "skeleton")

    _is_daytime = Zombie._is_daytime
    _can_see_sky = Zombie._can_see_sky
    _tick_sunburn = Zombie._tick_sunburn

    def attack_entity(self, target, distance: float) -> None:
        if distance >= self.ATTACK_RANGE:
            return
        dx = float(target.pos[0]) - self.pos[0]
        dz = float(target.pos[2]) - self.pos[2]
        if self.attack_time == 0:
            try:
                from game.entities.arrow import ArrowEntity
            except Exception:
                return
            world = Entity.world
            if world is None or not hasattr(world, "spawn_entity"):
                return
            pos = (
                float(self.pos[0]),
                float(self.pos[1]) + 1.4,
                float(self.pos[2]),
            )
            target_y = float(target.pos[1]) - 0.2
            dy = target_y - pos[1]
            horiz = math.sqrt(dx * dx + dz * dz)
            aim_y = dy + horiz * 0.2
            pitch = (self._rng.random() - self._rng.random()) * 0.2 + 1.0
            self._play_world_sound(
                "random.bow",
                volume=1.0,
                pitch=1.0 / (pitch * 0.4 + 0.8),
                position=self.pos,
            )
            direction = (dx, aim_y, dz)
            arrow = ArrowEntity.from_heading(
                world,
                pos,
                direction,
                shooting_entity=self,
                speed=0.6,
                inaccuracy=12.0,
                rng=self._rng,
            )
            world.spawn_entity(arrow)
            self.attack_time = 30
        if abs(dx) > 1e-6 or abs(dz) > 1e-6:
            from .creature import port_yaw_towards

            self.yaw = port_yaw_towards(dx, dz)
        self.has_attacked = True

    def _living_ai_tick(self) -> None:
        if self.health > 0 and not self.dead:
            self._tick_sunburn()
        super()._living_ai_tick()


EntitySkeleton = Skeleton
