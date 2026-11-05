from __future__ import annotations

from .entity import Entity
from .zombie import Zombie


class ZombiePigman(Zombie):
    HEALTH = 20
    ATTACK_STRENGTH = 5
    MOVE_SPEED = 0.5
    ANGRY_MOVE_SPEED = 0.95
    ANGER_RADIUS = 32.0
    FIRE_IMMUNE = True
    LIVING_SOUND = "mob.zombiepig.zpig"
    HURT_SOUND = "mob.zombiepig.zpighurt"
    DEATH_SOUND = "mob.zombiepig.zpigdeath"
    ANGRY_SOUND = "mob.zombiepig.zpigangry"
    DROP_ITEM_ID = "cooked_porkchop"

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.skin_path = "mob/pigzombie.png"
        self.attack_strength = self.ATTACK_STRENGTH
        self.anger_level = 0
        self._random_sound_delay = 0
        from .item import ItemStack
        self._equipped_item = ItemStack("gold_sword", 1)

    @staticmethod
    def _is_player(entity) -> bool:
        if entity is None:
            return False
        return (callable(getattr(entity, "damage_player", None))
                or bool(getattr(entity, "_mp_remote_player", False)))

    def become_angry_at(self, target) -> None:
        self.player_to_attack = target
        self.anger_level = 400 + self._rand_int(400)
        self._random_sound_delay = self._rand_int(40)

    def find_player_to_attack(self):
        return None if self.anger_level == 0 else super().find_player_to_attack()

    def _anger_nearby_pigmen(self, attacker) -> None:
        if not self._is_player(attacker):
            return
        world = Entity.world
        sx, sy, sz = (float(v) for v in self.size)
        radius = self.ANGER_RADIUS
        source_bounds = (
            float(self.pos[0]) - sx * 0.5 - radius,
            float(self.pos[1]) - radius,
            float(self.pos[2]) - sz * 0.5 - radius,
            float(self.pos[0]) + sx * 0.5 + radius,
            float(self.pos[1]) + sy + radius,
            float(self.pos[2]) + sz * 0.5 + radius,
        )
        for other in list(getattr(world, "entities", ()) or ()):
            if (other is self or not isinstance(other, ZombiePigman)
                    or other.dead or other.health <= 0):
                continue
            ox, oy, oz = (float(v) for v in other.pos)
            ow, oh, od = (float(v) for v in other.size)
            other_bounds = (
                ox - ow * 0.5, oy, oz - od * 0.5,
                ox + ow * 0.5, oy + oh, oz + od * 0.5,
            )
            if (other_bounds[0] < source_bounds[3]
                    and other_bounds[3] > source_bounds[0]
                    and other_bounds[1] < source_bounds[4]
                    and other_bounds[4] > source_bounds[1]
                    and other_bounds[2] < source_bounds[5]
                    and other_bounds[5] > source_bounds[2]):
                other.become_angry_at(attacker)
        self.become_angry_at(attacker)

    def attack_entity_from(self, attacker=None, amount=1) -> bool:
        self._anger_nearby_pigmen(attacker)
        return super().attack_entity_from(attacker, amount)

    def on_fishing_hook_hit(self, attacker=None) -> bool:
        self._anger_nearby_pigmen(attacker)
        return super().on_fishing_hook_hit(attacker)

    def on_fireball_hit(self, attacker=None) -> bool:
        self._anger_nearby_pigmen(attacker)
        return super().on_fireball_hit(attacker)

    def _tick_sunburn(self) -> None:
        self._fire_ticks = min(self._fire_ticks, 0)

    def _living_ai_tick(self) -> None:
        self.MOVE_SPEED = (
            self.ANGRY_MOVE_SPEED if self.player_to_attack is not None
            else type(self).MOVE_SPEED
        )
        if self._random_sound_delay > 0:
            self._random_sound_delay -= 1
            if self._random_sound_delay == 0:
                pitch = ((self._rng.random() - self._rng.random()) * 0.2 + 1.0) * 1.8
                self._play_world_sound(
                    self.ANGRY_SOUND, volume=1.0, pitch=pitch, position=self.pos,
                )
        super()._living_ai_tick()

    def get_can_spawn_here(self, x: float, y: float, z: float) -> bool:
        from .creature import Creature
        return self._difficulty() > 0 and Creature.get_can_spawn_here(self, x, y, z)

    def _spawn_drops(self) -> None:
        if self._drops_spawned:
            return
        self._drops_spawned = True
        for _ in range(self._rng.randint(0, 2)):
            self.drop_item(self.DROP_ITEM_ID, 1, y_offset=0.2)

    def restore_anger(self, level: int) -> None:
        self.anger_level = max(0, int(level))


EntityPigZombie = ZombiePigman
