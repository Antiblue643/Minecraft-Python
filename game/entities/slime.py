
from __future__ import annotations

import math

from .creature import Creature
from .entity import Entity
from .mob import IMobs
from .monster import Monster


class Slime(Creature, IMobs):
    SPAWN_MAX_Y = 16.0
    SOUND_VOLUME = 0.6
    ATTACK_SOUND = "mob.slimeattack"
    HURT_SOUND = "mob.slime"
    DEATH_SOUND = "mob.slime"
    LIVING_SOUND = None
    MOVE_SPEED = 1.0
    HEALTH = 1
    shadow_size = 0.25

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.skin_path = "mob/slime.png"
        self.squish_factor = 0.0
        self.prev_squish_factor = 0.0
        self.slime_size = 1
        self._was_on_ground = False
        self._ground_accel_left = 0.0
        self._exact_kill = False
        self._split_done = False
        self.render_y_offset = 0.0
        self.set_slime_size(1 << self._rand_int(3))
        self.slime_jump_delay = self._rand_int(20) + 10

    def set_slime_size(self, size: int) -> None:
        size = max(1, int(size))
        self.slime_size = size
        side = 0.6 * size
        self.size = (side, side, side)
        self.health = size * size
        self.prev_health = self.health

    @property
    def DROP_ITEM(self):  
        return "slimeball" if self.slime_size == 1 else None

    def _play_world_sound(self, name, volume=1.0, pitch=1.0, position=None) -> None:
        if name == "mob.slime":
            volume = min(float(volume), self.SOUND_VOLUME)
        super()._play_world_sound(name, volume=volume, pitch=pitch, position=position)

    def _random_pitch(self) -> float:
        return (self._rng.random() - self._rng.random()) * 0.2 + 1.0

    def get_can_spawn_here(self, x: float, y: float, z: float) -> bool:
        self.pos = [float(x), float(y), float(z)]
        self.prev_pos = list(self.pos)
        if self.slime_size != 1:
            if Monster._difficulty(self) <= 0 or self._rand_int(50) != 0:
                return False
        return (self.pos[1] + 0.5) < self.SPAWN_MAX_Y

    def attack_entity_from(self, attacker=None, amount=1) -> bool:
        hearts = self.hearts_life
        prev = self.prev_health
        health = self.health
        accepted = super().attack_entity_from(attacker, amount)
        if accepted and self.health <= 0:
            try:
                damage = max(0, int(amount))
            except (TypeError, ValueError):
                damage = 0
            base = prev if hearts > self.hearts_halves_life / 2 else health
            self._exact_kill = (base - damage) == 0
        return accepted

    def _split_into_children(self) -> None:
        world = Entity.world
        if world is None or not hasattr(world, "spawn_entity"):
            return
        size = self.slime_size
        for index in range(4):
            ox = ((index % 2) - 0.5) * size / 4.0
            oz = ((index // 2) - 0.5) * size / 4.0
            child = Slime(
                (self.pos[0] + ox, self.pos[1] + 0.5, self.pos[2] + oz),
                yaw=self._rng.random() * 360.0, rng=self._rng,
            )
            child.set_slime_size(size // 2)
            world.spawn_entity(child)

    def _collide_with_player(self) -> None:
        if self.slime_size <= 1:
            return
        mine = Entity._entity_collision_aabb(self)
        if mine is None:
            return
        for player in self._world_players():
            if not self._is_alive(player):
                continue
            theirs = Entity._entity_collision_aabb(player)
            if theirs is None:
                continue
            if not (mine[0] < theirs[3] + 1.0 and mine[3] > theirs[0] - 1.0
                    and mine[2] < theirs[5] + 1.0 and mine[5] > theirs[2] - 1.0
                    and mine[1] < theirs[4] and mine[4] > theirs[1]):
                continue
            eye_y = float(player.pos[1]) + float(getattr(player, "EYE_HEIGHT", 1.62))
            dx = float(player.pos[0]) - self.pos[0]
            dy = eye_y - self.pos[1]
            dz = float(player.pos[2]) - self.pos[2]
            if math.sqrt(dx * dx + dy * dy + dz * dz) >= 0.6 * self.slime_size:
                continue
            if not self.can_entity_be_seen(player):
                continue
            damage = getattr(player, "damage_player", None)
            if callable(damage):
                applied = damage(self.slime_size, source="mob", attacker=self)
            else:
                attack = getattr(player, "attack_entity_from", None)
                applied = attack(self, self.slime_size) if callable(attack) else False
            if applied:
                self._play_world_sound(
                    self.ATTACK_SOUND, volume=1.0, pitch=self._random_pitch(),
                    position=self.pos,
                )

    def _closest_player(self, max_distance: float):
        best = None
        best_distance_sq = float(max_distance) * float(max_distance)
        for player in self._world_players():
            if not self._is_alive(player):
                continue
            dx = float(player.pos[0]) - self.pos[0]
            dy = float(player.pos[1]) - self.pos[1]
            dz = float(player.pos[2]) - self.pos[2]
            distance_sq = dx * dx + dy * dy + dz * dz
            if distance_sq < best_distance_sq:
                best = player
                best_distance_sq = distance_sq
        return best

    def _update_player_action_state(self) -> None:
        player = self._closest_player(16.0)
        if player is not None:
            self._face_entity(player, 10.0, 10.0)

        jump = False
        if self.on_ground:
            jump = self.slime_jump_delay <= 0
            self.slime_jump_delay -= 1
        if jump:
            self.slime_jump_delay = self._rand_int(20) + 10
            if player is not None:
                self.slime_jump_delay //= 3
            self.is_jumping = True
            if self.slime_size > 1:
                self._play_world_sound(
                    "mob.slime", volume=self.SOUND_VOLUME,
                    pitch=self._random_pitch() * 0.8, position=self.pos,
                )
            self.squish_factor = 1.0
            self.move_strafing = 1.0 - self._rng.random() * 2.0
            self.move_forward = float(self.slime_size)
            self._ground_accel_left = 1.0
        else:
            self.is_jumping = False
            if self.on_ground:
                self.move_strafing = self.move_forward = 0.0

    def _update_heading_acceleration(self, ticks: float) -> None:
        left = self._ground_accel_left
        if left > 0.0 and not (self.in_water or self.in_lava):
            portion = min(float(ticks), left)
            self._ground_accel_left = left - portion
            rest = float(ticks) - portion
            was_on_ground = self.on_ground
            self.on_ground = True
            try:
                super()._update_heading_acceleration(portion)
            finally:
                self.on_ground = was_on_ground
            if rest > 1e-9:
                super()._update_heading_acceleration(rest)
            return
        super()._update_heading_acceleration(ticks)

    def _living_ai_tick(self) -> None:
        self.prev_squish_factor = self.squish_factor
        landed = bool(self.on_ground) and not self._was_on_ground
        self._was_on_ground = bool(self.on_ground)
        super()._living_ai_tick()

        if landed:
            self._landing_effects()
        self.squish_factor *= 0.6

        if self.health > 0 and not self.dead:
            self._collide_with_player()
        elif self.dead and self.health <= 0 and not self._split_done:
            self._split_done = True
            if self.slime_size > 1 and self._exact_kill:
                self._split_into_children()

    def _landing_effects(self) -> None:
        world = Entity.world
        spawn = getattr(world, "spawn_particle", None) if world is not None else None
        size = self.slime_size
        if callable(spawn):
            for _ in range(size * 8):
                angle = self._rng.random() * math.pi * 2.0
                spread = self._rng.random() * 0.5 + 0.5
                ox = math.sin(angle) * size * 0.5 * spread
                oz = math.cos(angle) * size * 0.5 * spread
                spawn("slime", self.pos[0] + ox, self.pos[1], self.pos[2] + oz,
                      0.0, 0.0, 0.0)
        if size > 2:
            self._play_world_sound(
                "mob.slime", volume=self.SOUND_VOLUME,
                pitch=self._random_pitch() / 0.8, position=self.pos,
            )
        self.squish_factor = -0.5

    def draw(self):
        from .mob_models import draw_living_with_model

        draw_living_with_model(self, "slime")


EntitySlime = Slime
