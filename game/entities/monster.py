from __future__ import annotations

from .entity import Entity
from .creature import Creature
from .mob import IMobs


class Monster(Creature, IMobs):
    is_hostile = True

    HEALTH = 20
    ATTACK_STRENGTH = 2
    TARGET_RANGE = 16.0
    ATTACK_RANGE = 2.5

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.health = self.HEALTH
        self.prev_health = self.HEALTH
        self.attack_strength = self.ATTACK_STRENGTH

    def _difficulty(self) -> int:
        player = self._world_player()
        settings = getattr(player, "settings", None) if player is not None else None
        if settings is None and player is not None:
            settings = getattr(type(player), "settings", None)
        difficulty = getattr(settings, "difficulty", 2)
        try:
            return int(difficulty) if difficulty is not None else 2
        except (TypeError, ValueError):
            return 2

    def _damage_target(self, target) -> None:
        damage_player = getattr(target, "damage_player", None)
        if callable(damage_player):
            damage_player(self.attack_strength, source="mob", attacker=self)
            return
        attack = getattr(target, "attack_entity_from", None)
        if callable(attack):
            attack(self, self.attack_strength)

    def _living_ai_tick(self) -> None:
        if self.health > 0 and not self.dead and self._difficulty() == 0:
            self.dead = True
            self.player_to_attack = None
            return
        super()._living_ai_tick()
        if self.health <= 0 or self.dead:
            self.player_to_attack = None

    def _tick_despawn(self) -> None:
        if float(self.get_entity_brightness()) > 0.5:
            self.entity_age += 2
        super()._tick_despawn()

    def find_player_to_attack(self):
        candidates = self._world_players()
        best = None
        best_distance_sq = self.TARGET_RANGE * self.TARGET_RANGE
        for player in candidates:
            if not self._is_alive(player):
                continue
            dx = float(player.pos[0]) - self.pos[0]
            dy = float(player.pos[1]) - self.pos[1]
            dz = float(player.pos[2]) - self.pos[2]
            distance_sq = dx * dx + dy * dy + dz * dz
            if distance_sq >= best_distance_sq:
                continue
            if self.can_entity_be_seen(player):
                best = player
                best_distance_sq = distance_sq
        return best

    def attack_entity(self, target, distance: float) -> None:
        target_bottom = float(target.pos[1])
        target_top = target_bottom + float(target.size[1])
        my_bottom = float(self.pos[1])
        my_top = my_bottom + float(self.size[1])
        if (distance < self.ATTACK_RANGE and target_top > my_bottom
                and target_bottom < my_top):
            self.attack_time = 20
            self._damage_target(target)

    def get_block_path_weight(self, x: int, y: int, z: int) -> float:
        return 0.5 - self._brightness_at(x, y, z)

    def get_can_spawn_here(self, x: float, y: float, z: float) -> bool:
        import math
        cell = tuple(math.floor(v + 0.5) for v in (x, y, z))
        light = self._light_level_at(*cell)
        return light <= 7 and super().get_can_spawn_here(x, y, z)

    def attack_entity_from(self, attacker=None, amount=1) -> bool:
        accepted = super().attack_entity_from(attacker, amount)
        if accepted and attacker is not None and attacker is not self \
                and self._is_alive(attacker):
            self.player_to_attack = attacker
        return accepted

EntityMonster = Monster
