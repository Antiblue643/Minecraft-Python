from __future__ import annotations

import math

from .entity import Entity
from .mob import Mob
from .pathfinding import get_path_to_entity, get_path_to_xyz


def port_yaw_towards(dx: float, dz: float) -> float:
    return math.degrees(math.atan2(-dx, -dz))


class Creature(Mob):
    ai_frozen = False
    PATH_SEARCH_DISTANCE = 16.0

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.player_to_attack = None
        self.path_to_entity = None
        self.has_attacked = False

    @property
    def target(self):
        return self.player_to_attack

    @target.setter
    def target(self, value):
        self.player_to_attack = value

    def attack_entity(self, target, distance: float) -> None:
        return None

    def find_player_to_attack(self):
        return None

    def get_block_path_weight(self, x: int, y: int, z: int) -> float:
        return 0.0

    @staticmethod
    def _is_alive(entity) -> bool:
        if entity is None or getattr(entity, "dead", False):
            return False
        try:
            return float(getattr(entity, "health", 1)) > 0.0
        except (TypeError, ValueError):
            return False

    def _world_player(self):
        world = Entity.world
        return getattr(world, "player", None) if world is not None else None

    def _world_players(self):
        world = Entity.world
        if world is None:
            return ()
        players = []
        local_player = getattr(world, "player", None)
        if local_player is not None:
            players.append(local_player)
        for remote_player in getattr(world, "_mp_remote_players", ()) or ():
            if remote_player is not None and all(remote_player is not p for p in players):
                players.append(remote_player)
        return tuple(players)

    def _brightness_at(self, x: int, y: int, z: int) -> float:
        world = Entity.world
        if world is None or not hasattr(world, "get_light"):
            return 1.0
        try:
            from engine.lighting import brightness_from_level
            return float(brightness_from_level(world.get_light(x, y, z), hell=bool(getattr(world, "is_hell_world", False))))
        except Exception:
            return 1.0

    def _saved_sky_light_at(self, x: int, y: int, z: int) -> int:
        world = Entity.world
        getter = getattr(world, "_get_saved_light", None)
        if not callable(getter):
            return 0
        try:
            return int(getter("sky", x, y, z))
        except Exception:
            return 0

    def _light_level_at(self, x: int, y: int, z: int) -> int:
        world = Entity.world
        if world is None or not hasattr(world, "get_light"):
            return 15
        try:
            return int(world.get_light(x, y, z))
        except Exception:
            return 15

    def _target_eye_position(self, target):
        pos = target.pos
        height = float(getattr(target, "EYE_HEIGHT", float(target.size[1]) * 0.85))
        return float(pos[0]), float(pos[1]) + height, float(pos[2])

    def distance_to_entity(self, other) -> float:
        dx = float(other.pos[0]) - self.pos[0]
        dy = float(other.pos[1]) - self.pos[1]
        dz = float(other.pos[2]) - self.pos[2]
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def can_entity_be_seen(self, target) -> bool:
        world = Entity.world
        if world is None or target is None:
            return False
        start = (
            float(self.pos[0]),
            float(self.pos[1]) + float(self.size[1]) * 0.85,
            float(self.pos[2]),
        )
        end = self._target_eye_position(target)
        dx, dy, dz = (end[i] - start[i] for i in range(3))
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        samples = max(2, int(math.ceil(distance * 10.0)))
        for index in range(1, samples):
            t = index / samples
            bx = math.floor(start[0] + dx * t + 0.5)
            by = math.floor(start[1] + dy * t + 0.5)
            bz = math.floor(start[2] + dz * t + 0.5)
            if self._solid_at(bx, by, bz):
                return False
        return True

    def _aabb_clear_of_spawn_blockers(self, px, py, pz) -> bool:
        world = Entity.world
        hw, hd, h = self._half_width(), self._half_depth(), float(self.size[1])
        others = list(getattr(world, "entities", ()) or ())
        player = getattr(world, "player", None)
        if player is not None:
            others.append(player)
        for other in others:
            if other is self or getattr(other, "dead", False):
                continue
            if not getattr(other, "prevent_entity_spawning", False):
                continue
            opos, osize = other.pos, other.size
            ohw, ohd = float(osize[0]) * 0.5, float(osize[2]) * 0.5
            if (px - hw < opos[0] + ohw and px + hw > opos[0] - ohw
                    and py < opos[1] + float(osize[1]) and py + h > opos[1]
                    and pz - hd < opos[2] + ohd and pz + hd > opos[2] - ohd):
                return False
        return True

    def get_can_spawn_here(self, x: float, y: float, z: float) -> bool:
        self.pos = [float(x), float(y), float(z)]
        self.prev_pos = list(self.pos)
        if Entity.world is None:
            return False
        px, py, pz = self.pos
        clear = (
            self._aabb_clear_of_spawn_blockers(px, py, pz)
            and not self._collides(px, py, pz)
            and not self._overlaps_any_liquid(px, py, pz)
        )
        if not clear:
            return False
        cx, cy, cz = (math.floor(v + 0.5) for v in (x, y, z))
        return self.get_block_path_weight(cx, cy, cz) >= 0.0

    def _path_to_target(self):
        return get_path_to_entity(
            Entity.world, self, self.player_to_attack, self.PATH_SEARCH_DISTANCE,
        )

    def _choose_wander_path(self) -> None:
        found = False
        best_x = best_y = best_z = -1
        best_weight = -99999.0
        for _ in range(10):
            cx = math.floor(self.pos[0] + 0.5 + self._rand_int(13) - 6.0)
            cy = math.floor(self.pos[1] + 0.5 + self._rand_int(7) - 3.0)
            cz = math.floor(self.pos[2] + 0.5 + self._rand_int(13) - 6.0)
            weight = self.get_block_path_weight(cx, cy, cz)
            if weight > best_weight:
                best_weight = weight
                best_x, best_y, best_z = cx, cy, cz
                found = True
        if found:
            self.path_to_entity = get_path_to_xyz(
                Entity.world, self, best_x, best_y, best_z, 10.0,
            )

    def _wrap_yaw_delta(self, delta: float) -> float:
        return (float(delta) + 180.0) % 360.0 - 180.0

    def _update_player_action_state(self) -> None:
        self.has_attacked = False
        if self.player_to_attack is None:
            self.player_to_attack = self.find_player_to_attack()
            if self.player_to_attack is not None:
                self.path_to_entity = self._path_to_target()
        elif not self._is_alive(self.player_to_attack):
            self.player_to_attack = None
        else:
            distance = self.distance_to_entity(self.player_to_attack)
            if self.can_entity_be_seen(self.player_to_attack):
                self.attack_entity(self.player_to_attack, distance)

        if (
            self.has_attacked
            or self.player_to_attack is None
            or (self.path_to_entity is not None and self._rand_int(20) != 0)
        ):
            if (
                (self.path_to_entity is None and self._rand_int(80) == 0)
                or self._rand_int(80) == 0
            ):
                self._choose_wander_path()
        else:
            self.path_to_entity = self._path_to_target()

        feet_cell = math.floor(self.pos[1] + 0.5 + 1e-4)
        in_fluid = bool(self.in_water or self.in_lava)
        if self.path_to_entity is not None and self._rand_int(100) != 0:
            waypoint = self.path_to_entity.get_position(self)
            radius = float(self.size[0]) * 2.0
            radius_sq = radius * radius
            while waypoint is not None:
                wx, _wy, wz = waypoint
                horizontal_dist_sq = (
                    (wx - self.pos[0]) ** 2 + (wz - self.pos[2]) ** 2
                )
                if horizontal_dist_sq >= radius_sq:
                    break
                self.path_to_entity.increment_path_index()
                if self.path_to_entity.is_finished():
                    waypoint = None
                    self.path_to_entity = None
                else:
                    waypoint = self.path_to_entity.get_position(self)

            self.is_jumping = False
            if waypoint is not None:
                wx, wy, wz = waypoint
                dx = wx - self.pos[0]
                dz = wz - self.pos[2]
                vertical = wy - feet_cell
                path_yaw = port_yaw_towards(dx, dz)
                self._pending_path_yaw = path_yaw
                self.move_forward = self.MOVE_SPEED
                self.move_strafing = 0.0
                if self.has_attacked and self.player_to_attack is not None:
                    tdx = float(self.player_to_attack.pos[0]) - self.pos[0]
                    tdz = float(self.player_to_attack.pos[2]) - self.pos[2]
                    face_yaw = port_yaw_towards(tdx, tdz)
                    self._pending_path_yaw = face_yaw
                    angle = math.radians(
                        self._wrap_yaw_delta(self.yaw - face_yaw + 90.0)
                    )
                    forward = self.move_forward
                    self.move_strafing = -math.sin(angle) * forward
                    self.move_forward = math.cos(angle) * forward
                if vertical > 0.0:
                    self.is_jumping = True
            else:
                self.move_forward = 0.0
                self.move_strafing = 0.0

            if self.horizontal_collision:
                self.is_jumping = True
            if self._rng.random() < 0.8 and in_fluid:
                self.is_jumping = True

            target = self.player_to_attack
            if target is not None and self.on_ground:
                vertical_gap = float(target.pos[1]) - float(self.pos[1])
                if vertical_gap > 0.6:
                    self.is_jumping = True
        else:
            Mob._update_player_action_state(self)
            self.path_to_entity = None
            self._pending_path_yaw = None
