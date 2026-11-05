from __future__ import annotations

import math
import random

from .entity import Entity, MAX_DT, TICKS_PER_SECOND


def _segment_aabb(start, end, bounds):
    lo, hi = 0.0, 1.0
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
        if near > far:
            near, far = far, near
        lo = max(lo, near)
        hi = min(hi, far)
        if lo > hi:
            return None
    if hi < 0.0 or lo > 1.0:
        return None
    return max(0.0, lo)


def _create_fireball_explosion(world, source, position, *, power=1.0, rng=None, fire=True):
    from .tnt import create_explosion

    create_explosion(
        world, source, float(position[0]), float(position[1]),
        float(position[2]), power=power, rng=rng, fire=fire,
    )


class FireballEntity(Entity):
    SIZE = 1.0
    PROJECTILE_SCALE = 0.1
    AIR_DRAG = 0.95
    WATER_DRAG = 0.8
    SHOOTER_GRACE_TICKS = 25.0

    def __init__(self, world, pos, *, acceleration=(0.0, 0.0, 0.0),
                 shooting_entity=None, rng=None):
        super().__init__(pos, (self.SIZE, self.SIZE, self.SIZE))
        self.world_obj = world
        self.shooting_entity = shooting_entity
        self.acceleration = [float(v) for v in acceleration]
        self._rng = rng if rng is not None else random.Random()
        self.prev_pos = list(self.pos)
        self.yaw = self.prev_yaw = 0.0
        self.pitch = self.prev_pitch = 0.0
        self.ticks_in_air = 0.0
        self.been_attacked = False
        self.shake = 0
        self.in_water = False
        self._water_particle_accum = 0.0
        self.age = 0.0
        self.x_tile = self.y_tile = self.z_tile = -1
        self.in_tile = None
        self.in_ground = False

    @classmethod
    def from_ghast(cls, world, shooter, pos, direction, *, rng=None):
        rng = rng if rng is not None else getattr(shooter, "_rng", None) or random.Random()
        dx, dy, dz = (float(v) for v in direction)
        gaussian = getattr(rng, "next_gaussian", None)
        if callable(gaussian):
            noise = (float(gaussian()), float(gaussian()), float(gaussian()))
        else:
            noise = (rng.gauss(0.0, 1.0), rng.gauss(0.0, 1.0), rng.gauss(0.0, 1.0))
        dx += noise[0] * 0.4
        dy += noise[1] * 0.4
        dz += noise[2] * 0.4
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length <= 1.0e-12:
            dx, dy, dz, length = 0.0, 0.0, 1.0, 1.0
        acceleration = (
            dx / length * cls.PROJECTILE_SCALE,
            dy / length * cls.PROJECTILE_SCALE,
            dz / length * cls.PROJECTILE_SCALE,
        )
        entity = cls(world, pos, acceleration=acceleration,
                     shooting_entity=shooter, rng=rng)
        entity.yaw = entity.prev_yaw = float(getattr(shooter, "yaw", 0.0))
        entity.pitch = entity.prev_pitch = float(
            getattr(shooter, "rotation_pitch", getattr(shooter, "pitch", 0.0))
        )
        return entity

    @property
    def center(self):
        return tuple(self.pos)

    def can_be_collided_with(self):
        return not self.dead

    def get_bounding_box(self, position=None):
        return super().get_bounding_box(position)

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
                        t = _segment_aabb(start, end, bounds)
                        if t is not None and (nearest is None or t < nearest[0]):
                            nearest = (t, (bx, by, bz), block_type)
        return nearest

    @staticmethod
    def _entity_bounds(entity, expansion=0.3):
        x, y, z = (float(v) for v in entity.pos)
        size = getattr(entity, "size", (0.6, 1.8, 0.6))
        hw, height, hd = (float(v) for v in size)
        return (x - hw * 0.5 - expansion, y - expansion, z - hd * 0.5 - expansion,
                x + hw * 0.5 + expansion, y + height + expansion, z + hd * 0.5 + expansion)

    def _find_entity_hit(self, start, end):
        world = self.world_obj
        candidates = list(getattr(world, "entities", ()) or ())
        player = getattr(world, "player", None)
        if player is not None and all(player is not item for item in candidates):
            candidates.append(player)
        for remote in getattr(world, "_mp_remote_players", ()) or ():
            if all(remote is not item for item in candidates):
                candidates.append(remote)
        nearest = None
        for target in candidates:
            if target is self or getattr(target, "dead", False):
                continue
            if target is self.shooting_entity and self.ticks_in_air < self.SHOOTER_GRACE_TICKS:
                continue
            can_collide = getattr(target, "can_be_collided_with", None)
            if callable(can_collide):
                if not can_collide():
                    continue
            else:
                continue
            t = _segment_aabb(start, end, self._entity_bounds(target, 0.3))
            if t is not None and (nearest is None or t < nearest[0]):
                nearest = (t, target)
        return nearest

    def _medium_flags(self):
        x, y, z = self.pos
        h = 0.5
        in_water = False
        for bx in range(math.floor(x + 0.5 - h), math.floor(x + 0.5 + h + 1.0)):
            for by in range(math.floor(y + 0.5 - h), math.floor(y + 0.5 + h + 1.0)):
                for bz in range(math.floor(z + 0.5 - h), math.floor(z + 0.5 + h + 1.0)):
                    if self._block_type(bx, by, bz) in ("water", "flowing_water"):
                        in_water = True
        return in_water

    def _impact(self, target=None, *, explosion_position=None):
        if target is not None:
            hit = getattr(target, "on_fireball_hit", None)
            if callable(hit):
                hit(self.shooting_entity)
        self.dead = True
        try:
            position = self.pos if explosion_position is None else explosion_position
            _create_fireball_explosion(
                self.world_obj, self, tuple(float(v) for v in position),
                power=1.0, rng=self._rng, fire=True,
            )
        except Exception:
            pass

    def attack_entity(self, attacker, amount=0):
        self.been_attacked = True
        if attacker is None:
            return False

        look = getattr(attacker, "get_look_vector", None)
        if callable(look):
            direction = look(1.0)
        elif bool(getattr(attacker, "is_living_entity", False)):
            yaw = math.radians(float(getattr(attacker, "yaw", 0.0)))
            pitch = math.radians(float(getattr(attacker, "pitch", 0.0)))
            direction = (-math.sin(yaw) * math.cos(pitch), -math.sin(pitch),
                         math.cos(yaw) * math.cos(pitch))
        else:
            return True

        self.vel[:] = [float(v) for v in direction]
        self.acceleration[:] = [v * 0.1 for v in self.vel]
        return True

    def attack_entity_from(self, attacker=None, amount=1):
        return self.attack_entity(attacker, amount)

    def _update_orientation(self):
        if not any(abs(v) > 1.0e-12 for v in self.vel):
            return
        yaw = math.degrees(math.atan2(self.vel[0], self.vel[2]))
        horizontal = math.hypot(self.vel[0], self.vel[2])
        pitch = math.degrees(math.atan2(self.vel[1], horizontal))
        yaw_delta = (yaw - self.prev_yaw + 180.0) % 360.0 - 180.0
        pitch_delta = (pitch - self.prev_pitch + 180.0) % 360.0 - 180.0
        self.yaw = self.prev_yaw + yaw_delta * 0.2
        self.pitch = self.prev_pitch + pitch_delta * 0.2

    def update(self, dt):
        world = self.world_obj
        if self.dead or world is None:
            return
        ticks = min(max(float(dt), 0.0), MAX_DT) * TICKS_PER_SECOND
        if ticks <= 0.0:
            return
        self.age += ticks
        self.prev_pos[:] = self.pos
        self.prev_yaw, self.prev_pitch = self.yaw, self.pitch
        self.shake = max(0, self.shake - int(ticks))
        self.ticks_in_air += ticks

        start = tuple(self.pos)
        end = tuple(self.pos[i] + self.vel[i] * ticks for i in range(3))
        block_hit = self._find_block_hit(start, end)
        block_t = block_hit[0] if block_hit is not None else 1.0
        clipped_end = tuple(start[i] + (end[i] - start[i]) * block_t for i in range(3))
        entity_hit = self._find_entity_hit(start, clipped_end)
        if entity_hit is not None:
            _t, target = entity_hit
            self._impact(target, explosion_position=start)
        elif block_hit is not None:
            self._impact(explosion_position=start)

        self.pos[:] = end
        self._update_orientation()
        self.in_water = self._medium_flags()
        drag = self.WATER_DRAG if self.in_water else self.AIR_DRAG
        if self.in_water:
            for _ in range(4):
                if hasattr(world, "spawn_particle"):
                    world.spawn_particle(
                        "bubble", self.pos[0] - self.vel[0] * 0.25,
                        self.pos[1] - self.vel[1] * 0.25,
                        self.pos[2] - self.vel[2] * 0.25,
                        self.vel[0], self.vel[1], self.vel[2],
                    )
        for axis in range(3):
            self.vel[axis] += self.acceleration[axis] * ticks
            self.vel[axis] *= drag ** ticks
        if hasattr(world, "spawn_particle"):
            world.spawn_particle("smoke", self.pos[0], self.pos[1] + 0.5,
                                 self.pos[2], 0.0, 0.0, 0.0)

    def draw(self):
        if self.dead:
            return
        from .fireball_model import draw_fireball
        draw_fireball(self)


EntityFireball = FireballEntity
