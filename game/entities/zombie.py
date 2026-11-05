from __future__ import annotations

import math

from .entity import Entity
from .monster import Monster


class Zombie(Monster):
    HEALTH = 20
    ATTACK_STRENGTH = 5
    MOVE_SPEED = 0.5
    raised_arms = True
    LIVING_SOUND = "mob.zombie"
    HURT_SOUND = "mob.zombiehurt"
    DEATH_SOUND = "mob.zombiedeath"

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.skin_path = "mob/zombie.png"
        self.health = self.HEALTH
        self.prev_health = self.HEALTH
        self.attack_strength = self.ATTACK_STRENGTH
        self.MOVE_SPEED = type(self).MOVE_SPEED
        self._drops_spawned = False

    def _is_daytime(self) -> bool:
        world = Entity.world
        if world is None:
            return False
        lighting = getattr(world, "lighting", None)
        if lighting is not None:
            try:
                return int(getattr(lighting, "skylight_subtracted", 0)) < 4
            except (TypeError, ValueError):
                pass
        try:
            t = int(getattr(world, "world_time", 0)) % 24000
            return t < 12000
        except (TypeError, ValueError):
            return False

    def _can_see_sky(self) -> bool:
        world = Entity.world
        if world is None:
            return False
        bx = int(math.floor(float(self.pos[0]) + 0.5))
        by = int(math.floor(float(self.pos[1]) + 0.5))
        bz = int(math.floor(float(self.pos[2]) + 0.5))
        try:
            cx, cz = world._get_chunk_index(bx, bz)
            chunk = world.chunks.get((cx, cz))
            if chunk is None:
                return True
            lx = bx - chunk.start_x
            lz = bz - chunk.start_z
            if not (0 <= lx < chunk.width and 0 <= lz < chunk.depth):
                return True
            top = int(chunk.heightmap[lz * chunk.width + lx])
            return by >= top
        except Exception:
            return False

    def _tick_sunburn(self) -> None:
        if self.health <= 0 or self.dead:
            return
        if not self._is_daytime():
            return
        brightness = float(self.get_entity_brightness())
        if brightness <= 0.5:
            return
        if not self._can_see_sky():
            return
        threshold = (brightness - 0.4) * 2.0
        if self._rng.random() * 30.0 < threshold:
            self._fire_ticks = 300

    def _spawn_drops(self) -> None:
        if self._drops_spawned:
            return
        self._drops_spawned = True
        world = Entity.world
        if world is None or not hasattr(world, "spawn_entity"):
            return
        try:
            from game.entities.item import ItemEntity, ItemStack
        except Exception:
            return
        count = self._rng.randint(0, 2)
        for _ in range(count):
            try:
                world.spawn_entity(
                    ItemEntity(
                        ItemStack("feather", 1),
                        (
                            float(self.pos[0]),
                            float(self.pos[1]) + 0.2,
                            float(self.pos[2]),
                        ),
                        pickup_delay=10,
                    )
                )
            except Exception:
                break

    def _on_death(self, attacker=None) -> None:
        self._spawn_drops()
        super()._on_death(attacker)

    def attack_entity_from(self, attacker=None, amount=1) -> bool:
        accepted = super().attack_entity_from(attacker, amount)
        if accepted and self.health <= 0:
            self._spawn_drops()
        return accepted

    def _living_ai_tick(self) -> None:
        if self.health > 0 and not self.dead:
            self._tick_sunburn()
        super()._living_ai_tick()

    def update(self, dt):
        was_alive = self.health > 0 and not self.dead
        super().update(dt)
        if was_alive and self.health <= 0:
            self._spawn_drops()

EntityZombie = Zombie
