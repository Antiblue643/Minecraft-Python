from __future__ import annotations

import math

from .monster import Monster


class Spider(Monster):
    WIDTH = 1.4
    HEIGHT = 0.9
    HEALTH = 20
    ATTACK_STRENGTH = 2
    MOVE_SPEED = 0.8
    ATTACK_RANGE = 2.5
    TARGET_RANGE = 16.0
    DROP_ITEM = "string"
    LIVING_SOUND = "mob.spider"
    HURT_SOUND = "mob.spider"
    DEATH_SOUND = "mob.spiderdeath"

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.size = (self.WIDTH, self.HEIGHT, self.WIDTH)
        self.skin_path = "mob/spider.png"
        self.health = self.HEALTH
        self.prev_health = self.HEALTH
        self.attack_strength = self.ATTACK_STRENGTH
        self.MOVE_SPEED = type(self).MOVE_SPEED
        self._drops_spawned = False
        self.rider = None

    def get_mounted_y_offset(self) -> float:
        return self.size[1] * 0.75 - 0.5

    def mount_rider(self, rider) -> None:
        if rider is None or self.dead or getattr(rider, "dead", False):
            return
        old = getattr(rider, "riding_entity", None)
        if old is not None and old is not self:
            dismount = getattr(old, "dismount", None)
            if callable(dismount):
                dismount()
            else:
                rider.riding_entity = None
        self.rider = rider
        rider.riding_entity = self
        self._position_rider()

    def dismount(self) -> None:
        rider = self.rider
        if rider is None:
            return
        if getattr(rider, "riding_entity", None) is self:
            rider.riding_entity = None
        yaw = math.radians(float(getattr(self, "yaw", 0.0)))
        rider.pos[0] = self.pos[0] + 0.9 * math.sin(yaw)
        rider.pos[1] = self.pos[1] + self.size[1]
        rider.pos[2] = self.pos[2] + 0.9 * math.cos(yaw)
        if hasattr(rider, "vel"):
            rider.vel[:] = list(self.vel)
        self.rider = None

    def _position_rider(self) -> None:
        rider = getattr(self, "rider", None)
        if rider is None:
            return
        rider.pos[0] = self.pos[0]
        rider.pos[1] = self.pos[1] + self.get_mounted_y_offset()
        rider.pos[2] = self.pos[2]
        if hasattr(rider, "vel"):
            rider.vel[:] = list(self.vel)

    def update(self, dt):
        if self.dead:
            self.dismount()
            return
        rider = getattr(self, "rider", None)
        if rider is not None and (
                getattr(rider, "dead", False) or float(getattr(rider, "health", 1)) <= 0.0):
            self.dismount()
        super().update(dt)
        self._position_rider()

    def _check_ladder(self):
        self.on_ladder = bool(self.horizontal_collision)
        self.ladder_contact_normal = (0.0, 0.0, 0.0)
        return self.on_ladder

    def draw(self):
        from .mob_models import draw_living_with_model

        draw_living_with_model(self, "spider")

    def find_player_to_attack(self):
        if float(self.get_entity_brightness()) >= 0.5:
            return None

        best = None
        best_distance_sq = self.TARGET_RANGE * self.TARGET_RANGE
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

    def attack_entity(self, target, distance: float) -> None:
        if float(self.get_entity_brightness()) > 0.5 and self._rand_int(100) == 0:
            self.player_to_attack = None
            return

        if 2.0 < distance < 6.0 and self._rand_int(10) == 0:
            if self.on_ground:
                dx = float(target.pos[0]) - self.pos[0]
                dz = float(target.pos[2]) - self.pos[2]
                horiz = math.sqrt(dx * dx + dz * dz) or 1.0
                self.vel[0] = dx / horiz * 0.5 * 0.8 + self.vel[0] * 0.2
                self.vel[2] = dz / horiz * 0.5 * 0.8 + self.vel[2] * 0.2
                self.vel[1] = 0.4
                self.on_ground = False
            return

        super().attack_entity(target, distance)

    def get_can_spawn_here(self, x: float, y: float, z: float) -> bool:
        return super().get_can_spawn_here(x, y, z)


EntitySpider = Spider
