from __future__ import annotations

from .entity import Entity
from .monster import Monster


class Creeper(Monster):
    HEALTH = 20
    ATTACK_STRENGTH = 0
    MOVE_SPEED = 0.7
    FUSE_TIME = 30
    DROP_ITEM = "gunpowder"
    HURT_SOUND = "mob.creeper"
    DEATH_SOUND = "mob.creeperdeath"

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.skin_path = "mob/creeper.png"
        self.health = self.HEALTH
        self.prev_health = self.HEALTH
        self.attack_strength = self.ATTACK_STRENGTH
        self.MOVE_SPEED = type(self).MOVE_SPEED
        self._drops_spawned = False
        self.time_since_ignited = 0
        self.last_active_time = 0
        self.creeper_state = -1
        self.has_attacked = False

    def draw(self):
        from .mob_models import draw_living_with_model

        draw_living_with_model(self, "creeper")

    def _on_death(self, attacker=None) -> None:
        super()._on_death(attacker)
        try:
            from .skeleton import Skeleton
            if isinstance(attacker, Skeleton):
                record_id = ("record_13", "record_cat")[self._rand_int(2)]
                self.drop_item(record_id, 1)
        except Exception:
            return

    def _update_player_action_state(self) -> None:
        self.last_active_time = self.time_since_ignited
        if self.time_since_ignited > 0 and self.creeper_state < 0:
            self.time_since_ignited -= 1

        if self.creeper_state >= 0:
            self.creeper_state = 2

        super()._update_player_action_state()

        if self.creeper_state != 1:
            self.creeper_state = -1

    def attack_entity(self, target, distance: float) -> None:
        in_range = (
            (self.creeper_state <= 0 and distance < 3.0)
            or (self.creeper_state > 0 and distance < 7.0)
        )
        if not in_range:
            return

        if self.time_since_ignited == 0:
            self._play_world_sound(
                "random.fuse", volume=1.0, pitch=0.5, position=self.pos
            )

        self.creeper_state = 1
        self.time_since_ignited += 1
        if self.time_since_ignited >= self.FUSE_TIME:
            self._explode()
            return
        self.has_attacked = True

    def _explode(self) -> None:
        world = Entity.world
        if world is not None:
            try:
                from game.entities.tnt import create_explosion

                create_explosion(
                    world,
                    self,
                    float(self.pos[0]),
                    float(self.pos[1]),
                    float(self.pos[2]),
                    power=3.0,
                    rng=self._rng,
                )
            except Exception:
                pass
        self.dead = True
        self.health = 0
        self.player_to_attack = None

    def set_creeper_flash_time(self, partial: float) -> float:
        return (
            (self.last_active_time + (self.time_since_ignited - self.last_active_time) * partial)
            / max(1, self.FUSE_TIME - 2)
        )


EntityCreeper = Creeper
