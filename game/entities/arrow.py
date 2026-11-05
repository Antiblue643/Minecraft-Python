from __future__ import annotations

import math
import random

from .arrow_model import ArrowRendererMixin, _arrow_texture_v

from game.entities.entity import Entity, MAX_DT, TICKS_PER_SECOND


_ARROW_SIZE = 0.5
_ARROW_HALF = _ARROW_SIZE * 0.5
_GRAVITY = 0.03
_AIR_DRAG = 0.99
_WATER_DRAG = 0.8
_MAX_GROUND_TICKS = 1200
_SHOOTER_GRACE_TICKS = 5


def _wrap_degrees(degrees):
    while degrees < -180.0:
        degrees += 360.0
    while degrees >= 180.0:
        degrees -= 360.0
    return degrees


def _segment_aabb(start, end, bounds):
    lo, hi = 0.0, 1.0
    hit_normal = (0, 0, 0)
    for axis, (origin, target, mn, mx) in enumerate(zip(
        start, end,
        (bounds[0], bounds[1], bounds[2]),
        (bounds[3], bounds[4], bounds[5]),
    )):
        delta = target - origin
        if abs(delta) < 1.0e-12:
            if origin < mn or origin > mx:
                return None
            continue
        near = (mn - origin) / delta
        far = (mx - origin) / delta
        near_sign = -1 if delta > 0.0 else 1
        if near > far:
            near, far = far, near
        if near > lo:
            lo = near
            n = [0, 0, 0]
            n[axis] = near_sign
            hit_normal = tuple(n)
        hi = min(hi, far)
        if lo > hi:
            return None
    if hi < 0.0 or lo > 1.0:
        return None
    return max(0.0, lo), hit_normal


class ArrowEntity(ArrowRendererMixin, Entity):
    def __init__(self, world, pos, velocity=(0.0, 0.0, 0.0), shooting_entity=None):
        super().__init__(pos, (0.5, 0.5, 0.5))
        self.world_obj = world
        self.shooting_entity = shooting_entity
        self.vel = [float(velocity[0]), float(velocity[1]), float(velocity[2])]
        self.prev_pos = list(self.pos)
        self.yaw = self.prev_yaw = math.degrees(math.atan2(self.vel[0], self.vel[2])) if any(self.vel) else 0.0
        horizontal = math.hypot(self.vel[0], self.vel[2])
        self.pitch = self.prev_pitch = math.degrees(math.atan2(self.vel[1], horizontal)) if any(self.vel) else 0.0
        self.x_tile = self.y_tile = self.z_tile = -1
        self.in_tile = None
        self.in_ground = False
        self.arrow_shake = 0.0
        self.ticks_in_ground = 0.0
        self.ticks_in_air = 0.0
        self.fire = 0.0
        self._water_particle_accum = 0.0

    @classmethod
    def from_shooter(cls, world, shooter):
        yaw = math.radians(float(shooter.yaw))
        pitch = math.radians(float(shooter.pitch))
        direction = [
            -math.sin(yaw) * math.cos(pitch),
            math.sin(pitch),
            -math.cos(yaw) * math.cos(pitch),
        ]
        return cls.from_heading(
            world,
            (shooter.pos[0] + math.cos(yaw) * 0.16,
             shooter.pos[1] + float(getattr(shooter, "EYE_HEIGHT", 1.62)) - 0.1,
             shooter.pos[2] - math.sin(yaw) * 0.16),
            direction,
            shooting_entity=shooter,
            speed=1.5,
            inaccuracy=1.0,
        )

    @classmethod
    def from_heading(cls, world, pos, direction, *, shooting_entity=None,
                     speed=1.5, inaccuracy=1.0, rng=None):
        rng = rng or random
        norm = math.sqrt(sum(float(v) * float(v) for v in direction))
        if norm <= 1.0e-12:
            direction = (0.0, 0.0, -1.0)
            norm = 1.0
        perturbed = [float(v) / norm + rng.gauss(0.0, 1.0) * 0.0075 * inaccuracy
                     for v in direction]
        norm = math.sqrt(sum(v * v for v in perturbed)) or 1.0
        velocity = tuple(v / norm * speed for v in perturbed)
        return cls(world, pos, velocity=velocity, shooting_entity=shooting_entity)

    @property
    def center(self):
        return tuple(self.pos)

    def _play_sound(self, name, volume=1.0, pitch=1.0):
        callback = getattr(self.world_obj, "sound_player", None)
        if callable(callback):
            callback(name, volume=volume, pitch=pitch, position=tuple(self.pos))

    def _block_type(self, x, y, z):
        try:
            return self.world_obj.get_block_type(int(x), int(y), int(z))
        except Exception:
            return None

    def _find_block_hit(self, start, end):
        world = self.world_obj
        min_x = math.floor(min(start[0], end[0]) + 0.5) - 1
        max_x = math.floor(max(start[0], end[0]) + 0.5) + 1
        min_y = math.floor(min(start[1], end[1]) + 0.5) - 1
        max_y = math.floor(max(start[1], end[1]) + 0.5) + 1
        min_z = math.floor(min(start[2], end[2]) + 0.5) - 1
        max_z = math.floor(max(start[2], end[2]) + 0.5) + 1
        nearest = None
        for bx in range(min_x, max_x + 1):
            for by in range(min_y, max_y + 1):
                for bz in range(min_z, max_z + 1):
                    try:
                        block = world.get_block(bx, by, bz)
                    except Exception:
                        block = None
                    if block is None:
                        continue
                    block_type = getattr(block, "block_type", None)
                    if block_type in ("water", "flowing_water", "lava", "flowing_lava", "fire"):
                        continue
                    if getattr(block, "collision", None) is False:
                        continue
                    is_collidable = getattr(block, "is_collidable", None)
                    if callable(is_collidable) and not is_collidable():
                        continue
                    try:
                        boxes = block.get_collision_boxes() if hasattr(block, "get_collision_boxes") else (
                            block.get_selection_boxes() if hasattr(block, "get_selection_boxes") else [block.get_selection_bounds()]
                        )
                    except Exception:
                        boxes = [(-0.5, -0.5, -0.5, 0.5, 0.5, 0.5)]
                    for local in boxes:
                        if local is None:
                            continue
                        bounds = (
                            bx + float(local[0]), by + float(local[1]), bz + float(local[2]),
                            bx + float(local[3]), by + float(local[4]), bz + float(local[5]),
                        )
                        intercept = _segment_aabb(start, end, bounds)
                        if intercept is None:
                            continue
                        t, normal = intercept
                        if nearest is None or t < nearest[0]:
                            nearest = (t, (bx, by, bz), block_type, normal)
        return nearest

    @staticmethod
    def _entity_bounds(entity, expansion=0.3):
        x, y, z = (float(v) for v in entity.pos)
        size = getattr(entity, "size", (0.6, 1.8, 0.6))
        hw, height, hd = (float(v) for v in size)
        return (
            x - hw * 0.5 - expansion, y - expansion, z - hd * 0.5 - expansion,
            x + hw * 0.5 + expansion, y + height + expansion, z + hd * 0.5 + expansion,
        )

    def _find_entity_hit(self, start, end, ticks_in_air):
        world = self.world_obj
        candidates = list(getattr(world, "entities", ()))
        current_player = getattr(world, "player", None)
        if current_player is not None and current_player not in candidates:
            candidates.append(current_player)
        for remote_player in getattr(world, "_mp_remote_players", ()) or ():
            if remote_player not in candidates:
                candidates.append(remote_player)
        if self.shooting_entity is not None and self.shooting_entity not in candidates:
            candidates.append(self.shooting_entity)

        nearest = None
        for entity in candidates:
            if entity is self or getattr(entity, "dead", False):
                continue
            can_collide = getattr(entity, "can_be_collided_with", None)
            if callable(can_collide):
                if not can_collide():
                    continue
            else:
                can_collide = getattr(entity, "canBeCollidedWith", None)
                if not callable(can_collide) or not can_collide():
                    continue
            if entity is self.shooting_entity and ticks_in_air < _SHOOTER_GRACE_TICKS:
                continue
            intercept = _segment_aabb(start, end, self._entity_bounds(entity, 0.3))
            if intercept is None:
                continue
            t, _normal = intercept
            if nearest is None or t < nearest[0]:
                nearest = (t, entity)
        return nearest

    def _try_pickup(self):
        player = self.shooting_entity
        if not self.in_ground or self.arrow_shake > 0 or player is None:
            return False
        if getattr(player, "dead", False) or getattr(player, "health", 20) <= 0:
            return False
        player_bounds = self._entity_bounds(player, 0.0)
        pickup_bounds = (
            player_bounds[0] - 1.0, player_bounds[1], player_bounds[2] - 1.0,
            player_bounds[3] + 1.0, player_bounds[4], player_bounds[5] + 1.0,
        )
        half = _ARROW_HALF
        arrow_bounds = (
            self.pos[0] - half, self.pos[1] - half, self.pos[2] - half,
            self.pos[0] + half, self.pos[1] + half, self.pos[2] + half,
        )
        if not all((arrow_bounds[0] < pickup_bounds[3],
                    arrow_bounds[3] > pickup_bounds[0],
                    arrow_bounds[1] < pickup_bounds[4],
                    arrow_bounds[4] > pickup_bounds[1],
                    arrow_bounds[2] < pickup_bounds[5],
                    arrow_bounds[5] > pickup_bounds[2])):
            return False
        try:
            from game.entities.item import ItemStack
            if not player.inventory.add_stack(ItemStack("arrow", 1)):
                return False
        except Exception:
            return False
        self._play_sound(
            "random.pop", volume=0.2,
            pitch=((random.random() - random.random()) * 0.7 + 1.0) * 2.0,
        )
        self.dead = True
        return True

    def _handle_block_hit(self, start, end, ticks, block_hit):
        t, cell, block_type, _normal = block_hit
        hit_point = tuple(start[i] + (end[i] - start[i]) * t for i in range(3))
        motion_segment = [hit_point[i] - start[i] for i in range(3)]
        seg_len = math.sqrt(sum(v * v for v in motion_segment))
        if seg_len > 1.0e-12:
            backoff = [v / seg_len * 0.05 for v in motion_segment]
            self.pos[:] = [hit_point[i] - backoff[i] for i in range(3)]
            self.vel[:] = [v / ticks for v in motion_segment]
        else:
            self.pos[:] = hit_point
            self.vel[:] = [0.0, 0.0, 0.0]
        self.x_tile, self.y_tile, self.z_tile = cell
        self.in_tile = block_type
        self.in_ground = True
        self.arrow_shake = 7.0
        self.ticks_in_ground = 0.0
        self._play_sound(
            "random.drr", volume=1.0,
            pitch=1.2 / (random.random() * 0.2 + 0.9),
        )

    def _hit_entity(self, target, ticks):
        attacker = self.shooting_entity
        damaged = False
        damage_player = getattr(target, "damage_player", None)
        if callable(damage_player):
            damaged = bool(damage_player(4, source="arrow", attacker=attacker))
        else:
            attack = getattr(target, "attack_entity_from", None)
            if callable(attack):
                damaged = bool(attack(attacker, 4))
            else:
                attack = getattr(target, "attackEntityFrom", None)
                if callable(attack):
                    damaged = bool(attack(attacker, 4))
        if damaged:
            self._play_sound(
                "random.drr", volume=1.0,
                pitch=1.2 / (random.random() * 0.2 + 0.9),
            )
            self.dead = True
            return

        self.vel[0] *= -0.1
        self.vel[1] *= -0.1
        self.vel[2] *= -0.1
        self.yaw += 180.0
        self.prev_yaw += 180.0
        self.ticks_in_air = 0.0
        self.pos[0] += self.vel[0] * ticks
        self.pos[1] += self.vel[1] * ticks
        self.pos[2] += self.vel[2] * ticks

    def _medium_flags(self):
        x, y, z = self.pos
        h = _ARROW_HALF
        ix, iy, iz = x + 0.5, y + 0.5, z + 0.5
        x_cells = range(math.floor(ix - h), math.floor(ix + h + 1.0))
        y_cells = range(math.floor(iy - h + 0.4), math.floor(iy + h - 0.4 + 1.0))
        z_cells = range(math.floor(iz - h), math.floor(iz + h + 1.0))
        in_water = in_lava = False
        for bx in x_cells:
            for by in y_cells:
                for bz in z_cells:
                    bt = self._block_type(bx, by, bz)
                    if bt in ("water", "flowing_water"):
                        in_water = True
                    elif bt in ("lava", "flowing_lava"):
                        in_lava = True

        touching_fire = False
        for bx in range(math.floor(x - h + 0.5), math.floor(x + h + 0.5) + 1):
            for by in range(math.floor(y - h + 0.5), math.floor(y + h + 0.5) + 1):
                for bz in range(math.floor(z - h + 0.5), math.floor(z + h + 0.5) + 1):
                    if self._block_type(bx, by, bz) == "fire":
                        touching_fire = True
        return in_water, in_lava, touching_fire

    def _update_orientation(self, ticks=1.0):
        if not any(abs(v) > 1e-12 for v in self.vel):
            return
        yaw = math.degrees(math.atan2(self.vel[0], self.vel[2]))
        horizontal = math.hypot(self.vel[0], self.vel[2])
        pitch = math.degrees(math.atan2(self.vel[1], horizontal))
        yaw_delta = _wrap_degrees(yaw - self.prev_yaw)
        pitch_delta = _wrap_degrees(pitch - self.prev_pitch)
        blend = 1.0 - (1.0 - 0.2) ** max(0.0, float(ticks))
        self.yaw = self.prev_yaw + yaw_delta * blend
        self.pitch = self.prev_pitch + pitch_delta * blend

    def update(self, dt):
        world = self.world_obj
        if self.dead or world is None:
            return
        if self.pos[1] < -64.0:
            self.dead = True
            return
        ticks = min(max(float(dt), 0.0), MAX_DT) * TICKS_PER_SECOND
        if ticks <= 0.0:
            return

        self.prev_pos[:] = self.pos
        self.prev_yaw = self.yaw
        self.prev_pitch = self.pitch
        self.arrow_shake = max(0.0, self.arrow_shake - ticks)

        if self.in_ground:
            self._update_fire_and_fluid(ticks)
            if self._block_type(self.x_tile, self.y_tile, self.z_tile) == self.in_tile:
                self.ticks_in_ground += ticks
                self._try_pickup()
                if self.ticks_in_ground >= _MAX_GROUND_TICKS:
                    self.dead = True
                return
            self.in_ground = False
            self.vel[0] *= random.random() * 0.2
            self.vel[1] *= random.random() * 0.2
            self.vel[2] *= random.random() * 0.2
            self.ticks_in_ground = 0.0
            self.ticks_in_air = 0.0

        self.ticks_in_air += ticks
        start = tuple(self.pos)
        end = tuple(self.pos[i] + self.vel[i] * ticks for i in range(3))
        block_hit = self._find_block_hit(start, end)
        block_t = block_hit[0] if block_hit is not None else 1.0
        blocked_end = tuple(start[i] + (end[i] - start[i]) * block_t for i in range(3))
        entity_hit = self._find_entity_hit(start, blocked_end, self.ticks_in_air)

        if entity_hit is not None:
            entity_t, target = entity_hit
            travel_ticks = ticks * block_t * entity_t
            self._hit_entity(target, travel_ticks)
            if self.dead:
                return
        elif block_hit is not None:
            self._handle_block_hit(start, end, ticks, block_hit)
            if not self.dead:
                self._update_orientation(ticks)
                self._update_fire_and_fluid(ticks)
                self._post_flight_physics(ticks)
            return
        else:
            self.pos[:] = end

        self._update_orientation(ticks)
        self._update_fire_and_fluid(ticks)
        self._post_flight_physics(ticks)

    def _post_flight_physics(self, ticks):
        drag = (_WATER_DRAG if self.in_water else _AIR_DRAG) ** ticks
        self.vel[0] *= drag
        self.vel[1] *= drag
        self.vel[2] *= drag
        self.vel[1] -= _GRAVITY * ticks
        if self.in_water:
            self._water_particle_accum += 4.0 * ticks
            while self._water_particle_accum >= 1.0:
                self._water_particle_accum -= 1.0
                if hasattr(self.world_obj, "spawn_particle"):
                    self.world_obj.spawn_particle(
                        "bubble",
                        self.pos[0] - self.vel[0] * 0.25,
                        self.pos[1] - self.vel[1] * 0.25,
                        self.pos[2] - self.vel[2] * 0.25,
                        self.vel[0], self.vel[1], self.vel[2],
                    )

    def _update_fire_and_fluid(self, ticks):
        previously_in_water = bool(self.in_water)
        in_water, in_lava, touching_fire = self._medium_flags()
        self.in_water = in_water
        self.in_lava = in_lava
        if in_water and not previously_in_water:
            entry_speed = math.sqrt(
                self.vel[0] * self.vel[0] * 0.2
                + self.vel[1] * self.vel[1]
                + self.vel[2] * self.vel[2] * 0.2
            )
            self._play_sound(
                "liquid.splash",
                volume=min(0.9, max(0.08, entry_speed * 1.5)),
                pitch=1.0,
            )
            if hasattr(self.world_obj, "spawn_particle"):
                from game.entities.particles import spawn_water_entry_particles
                spawn_water_entry_particles(
                    self.world_obj, self.pos, self.vel, self.size[0],
                )
        if in_water:
            self.fire = 0.0
        elif in_lava:
            self.fire = 600.0
        elif touching_fire:
            if self.fire <= 0.0:
                self.fire = 300.0
        else:
            self.fire = max(0.0, self.fire - ticks)

    def can_be_collided_with(self):
        return False
