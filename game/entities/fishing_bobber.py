
from __future__ import annotations

import math
import random

from game.entities.entity import Entity, MAX_DT, TICKS_PER_SECOND
from game.java_random import JavaRandom


_WATER_TYPES = frozenset(("water", "flowing_water"))
_PARTICLES_TEXTURE = "game/textures/particles.png"
_FISH_BOBBER_UV = (1.0 / 16.0, 2.0 / 16.0, 13.0 / 16.0, 14.0 / 16.0)


def apply_reel_durability(rod_stack, inventory, slot: int, reel_result: int) -> bool:
    wear = max(0, int(reel_result))
    if wear == 0:
        return False
    if rod_stack.damage_item(wear):
        inventory.set_stack(slot, None)
        return True
    return False


def _wrap_degrees(value: float) -> float:
    return (float(value) + 180.0) % 360.0 - 180.0


def _lerp_angle(previous: float, current: float, partial: float) -> float:
    return float(previous) + _wrap_degrees(float(current) - float(previous)) * partial


def _new_fishing_rng():
    return JavaRandom(random.SystemRandom().getrandbits(64))


def _rng_float(rng):
    next_float = getattr(rng, "next_float", None)
    return float(next_float() if callable(next_float) else rng.random())


def _rng_int(rng, bound):
    next_int = getattr(rng, "next_int", None)
    return int(next_int(bound) if callable(next_int) else rng.randrange(bound))


def _rng_gaussian(rng):
    next_gaussian = getattr(rng, "next_gaussian", None)
    if callable(next_gaussian):
        return float(next_gaussian())
    return float(rng.gauss(0.0, 1.0))


def _camera_billboard_axes(yaw_degrees: float, pitch_degrees: float):
    yaw = math.radians(float(yaw_degrees))
    pitch = math.radians(float(pitch_degrees))
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    cos_p, sin_p = math.cos(pitch), math.sin(pitch)
    right = (cos_y, 0.0, -sin_y)
    look = (-sin_y * cos_p, sin_p, -cos_y * cos_p)
    up = (
        right[1] * look[2] - right[2] * look[1],
        right[2] * look[0] - right[0] * look[2],
        right[0] * look[1] - right[1] * look[0],
    )
    return right, up, look


def _apply_gl_transform_ops(point, operations):
    x, y, z = (float(v) for v in point)
    for kind, values in reversed(operations):
        if kind == "translate":
            x += values[0]
            y += values[1]
            z += values[2]
        elif kind == "scale":
            x *= values[0]
            y *= values[1]
            z *= values[2]
        elif kind == "rotate":
            axis, degrees = values
            angle = math.radians(float(degrees))
            c, sn = math.cos(angle), math.sin(angle)
            if axis == "x":
                y, z = y * c - z * sn, y * sn + z * c
            elif axis == "y":
                x, z = x * c + z * sn, -x * sn + z * c
            else:
                x, y = x * c - y * sn, x * sn + y * c
        else:
            raise ValueError(f"unknown GL transform operation: {kind}")
    return (x, y, z)


_STICK_SPRITE_TIP = (1.5 / 16.0, 14.5 / 16.0, 0.0)


def _sprite_tip_operations():
    return [
        ("translate", (0.0, -0.3, 0.0)),
        ("scale", (1.5, 1.5, 1.5)),
        ("rotate", ("y", 50.0)),
        ("rotate", ("z", 335.0)),
        ("translate", (-15.0 / 16.0, -1.0 / 16.0, 0.0)),
    ]


def _first_person_rod_tip_position(owner, renderer, partial: float):
    swing_now = float(getattr(owner, "swing_progress", 0.0))
    swing_prev = float(getattr(owner, "prev_swing_progress", swing_now))
    swing_delta = swing_now - swing_prev
    if swing_delta < 0.0:
        swing_delta += 1.0
    swing = swing_prev + swing_delta * partial

    equipped = float(getattr(owner, "equipped_progress", 1.0))
    prev_equipped = float(getattr(owner, "prev_equipped_progress", equipped))
    equipped = prev_equipped + (equipped - prev_equipped) * partial
    operations = []
    if swing > 0.0:
        sin_s = math.sin(swing * math.pi)
        sin_sqrt = math.sin(math.sqrt(swing) * math.pi)
        operations.append((
            "translate",
            (-sin_sqrt * 0.4,
             math.sin(math.sqrt(swing) * math.pi * 2.0) * 0.2,
             -sin_s * 0.2),
        ))
    operations.extend((
        ("translate", (0.7 * 0.8, -0.65 * 0.8 - (1.0 - equipped) * 0.6, -0.9 * 0.8)),
        ("rotate", ("y", 45.0)),
    ))
    if swing > 0.0:
        sin_sq = math.sin(swing * swing * math.pi)
        sin_sqrt = math.sin(math.sqrt(swing) * math.pi)
        operations.extend((
            ("rotate", ("y", -sin_sq * 20.0)),
            ("rotate", ("z", -sin_sqrt * 20.0)),
            ("rotate", ("x", -sin_sqrt * 80.0)),
        ))
    operations.append(("scale", (0.4, 0.4, 0.4)))
    if getattr(owner, "fishing_hook", None) is not None:
        operations.append(("rotate", ("y", 180.0)))
    operations.extend(_sprite_tip_operations())
    view_point = _apply_gl_transform_ops(_STICK_SPRITE_TIP, operations)

    yaw = math.radians(float(getattr(owner, "yaw", 0.0)))
    pitch = math.radians(float(getattr(owner, "pitch", 0.0)))
    c_y, s_y = math.cos(yaw), math.sin(yaw)
    c_p, s_p = math.cos(pitch), math.sin(pitch)
    vx, vy, vz = view_point
    vx, vy, vz = vx, vy * c_p - vz * s_p, vy * s_p + vz * c_p
    vx, vz = vx * c_y + vz * s_y, -vx * s_y + vz * c_y

    if renderer is not None:
        camera = (
            float(getattr(renderer, "camX", owner.pos[0])),
            float(getattr(renderer, "camY", owner.pos[1] + getattr(owner, "EYE_HEIGHT", 1.62))),
            float(getattr(renderer, "camZ", owner.pos[2])),
        )
    else:
        camera = (
            float(owner.pos[0]),
            float(owner.pos[1]) + float(getattr(owner, "EYE_HEIGHT", 1.62))
            - float(getattr(owner, "render_y_offset", 0.0)),
            float(owner.pos[2]),
        )
    return (camera[0] + vx, camera[1] + vy, camera[2] + vz)


def _third_person_item_tip_world(owner, partial: float, body_yaw: float, arm_pose):
    arm_pivot, arm_angles = arm_pose
    world_y = float(owner.pos[1]) - float(getattr(owner, "render_y_offset", 0.0))
    operations = [
        ("translate", (float(owner.pos[0]), world_y, float(owner.pos[2]))),
        ("rotate", ("y", body_yaw)),
        ("scale", (-1.0, -1.0, 1.0)),
        ("scale", (15.0 / 16.0, 15.0 / 16.0, 15.0 / 16.0)),
        ("translate", (0.0, -24.0 / 16.0 - 0.0078125, 0.0)),
        ("translate", tuple(float(v) / 16.0 for v in arm_pivot)),
        ("rotate", ("z", math.degrees(float(arm_angles[2])))),
        ("rotate", ("y", math.degrees(float(arm_angles[1])))),
        ("rotate", ("x", math.degrees(float(arm_angles[0])))),
        ("translate", (-1.0 / 16.0, 7.0 / 16.0, 1.0 / 16.0)),
        ("rotate", ("z", 180.0)),
        ("translate", (0.0, -2.0 / 16.0, 0.0)),
        ("translate", (0.0, 3.0 / 16.0, 0.0)),
        ("scale", (10.0 / 16.0, -10.0 / 16.0, 10.0 / 16.0)),
        ("rotate", ("x", -100.0)),
        ("rotate", ("y", 45.0)),
        *_sprite_tip_operations(),
    ]
    return _apply_gl_transform_ops(_STICK_SPRITE_TIP, operations)


def _third_person_rod_tip_position(owner, partial: float):
    previous_body_yaw = float(getattr(
        owner, "prev_render_yaw_offset", getattr(owner, "render_yaw_offset", owner.yaw),
    ))
    current_body_yaw = float(getattr(owner, "render_yaw_offset", owner.yaw))
    body_yaw = _lerp_angle(previous_body_yaw, current_body_yaw, partial)

    try:
        from game.entities import player_model
        model = player_model._get_main_model()
        player_model._apply_player_pose(model, owner, partial)
        limb_swing, limb_amount, age = player_model._player_anim(owner, partial)
        net_head = _wrap_degrees(body_yaw - float(owner.yaw))
        model.set_rotation_angles(
            limb_swing, limb_amount, age, net_head, -float(owner.pitch),
            raised_arms=bool(getattr(owner, "raised_arms", False)),
            is_riding=getattr(owner, "riding_entity", None) is not None,
            is_sneak=bool(getattr(owner, "is_sneaking", False)),
        )
        arm = model.biped_right_arm
        arm_pose = (tuple(arm.rotation_point), (
            float(arm.rotate_angle_x), float(arm.rotate_angle_y),
            float(arm.rotate_angle_z),
        ))
        return _third_person_item_tip_world(owner, partial, body_yaw, arm_pose)
    except Exception:
        yaw = math.radians(body_yaw)
        return (
            float(owner.pos[0]) + math.cos(yaw) * 0.35 - math.sin(yaw) * 0.85,
            float(owner.pos[1]) - 0.45,
            float(owner.pos[2]) - math.sin(yaw) * 0.35 - math.cos(yaw) * 0.85,
        )


def _fishing_line_hand_position(owner, renderer, partial: float):
    if (bool(getattr(owner, "_mp_remote_player", False))
            or bool(getattr(owner, "_third_person_view", False))):
        return _third_person_rod_tip_position(owner, partial)
    return _first_person_rod_tip_position(owner, renderer, partial)


def _fishing_line_vertices(bobber_relative, hand_relative, segments=16):
    delta_x = hand_relative[0] - bobber_relative[0]
    delta_y = hand_relative[1] - (bobber_relative[1] + 0.25)
    delta_z = hand_relative[2] - bobber_relative[2]
    points = []
    for i in range(int(segments) + 1):
        t = i / float(segments)
        curve = (t * t + t) * 0.5
        points.append((
            bobber_relative[0] + delta_x * t,
            bobber_relative[1] + delta_y * curve + 0.25,
            bobber_relative[2] + delta_z * t,
        ))
    return tuple(points)


def _segment_aabb(start, end, bounds):
    lo, hi = 0.0, 1.0
    normal = (0, 0, 0)
    for axis, (origin, target, mn, mx) in enumerate(zip(
        start, end, bounds[:3], bounds[3:],
    )):
        delta = target - origin
        if abs(delta) < 1.0e-12:
            if origin < mn or origin > mx:
                return None
            continue
        near = (mn - origin) / delta
        far = (mx - origin) / delta
        sign = -1 if delta > 0.0 else 1
        if near > far:
            near, far = far, near
        if near > lo:
            lo = near
            current = [0, 0, 0]
            current[axis] = sign
            normal = tuple(current)
        hi = min(hi, far)
        if lo > hi:
            return None
    if hi < 0.0 or lo > 1.0:
        return None
    return max(0.0, lo), normal

class FishingBobberEntity(Entity):

    def __init__(self, world, pos, velocity=(0.0, 0.0, 0.0), angler=None, *, rng=None):
        super().__init__(pos, (0.25, 0.25, 0.25))
        self.world_obj = world
        self.angler = angler
        self.shooting_entity = angler
        self.vel = [float(v) for v in velocity]
        self.prev_pos = list(self.pos)
        self.yaw = self.prev_yaw = (
            math.degrees(math.atan2(self.vel[0], self.vel[2])) if any(self.vel) else 0.0
        )
        horizontal = math.hypot(self.vel[0], self.vel[2])
        self.pitch = self.prev_pitch = (
            math.degrees(math.atan2(self.vel[1], horizontal)) if any(self.vel) else 0.0
        )
        self.ticks_in_air = 0
        self.ticks_in_ground = 0
        self.in_ground = False
        self.x_tile = self.y_tile = self.z_tile = -1
        self.in_tile = None
        self.shake = 0
        self.bite_ticks = 0
        self.hooked_entity = None
        self._tick_accum = 0.0
        self.rng = rng if rng is not None else _new_fishing_rng()

    @classmethod
    def from_player(cls, world, player, *, rng=None):
        yaw = math.radians(float(player.yaw))
        pitch = math.radians(float(player.pitch))
        eye_height = float(getattr(player, "EYE_HEIGHT", 1.62))
        pos = [
            float(player.pos[0]) + math.cos(yaw) * 0.16,
            float(player.pos[1]) + eye_height - 0.1,
            float(player.pos[2]) - math.sin(yaw) * 0.16,
        ]
        direction = (
            -math.sin(yaw) * math.cos(pitch),
            math.sin(pitch),
            -math.cos(yaw) * math.cos(pitch),
        )
        norm = math.sqrt(sum(v * v for v in direction)) or 1.0
        source_rng = rng if rng is not None else _new_fishing_rng()
        spread = [
            direction[i] / norm + _rng_gaussian(source_rng) * 0.0075
            for i in range(3)
        ]
        spread_norm = math.sqrt(sum(v * v for v in spread)) or 1.0
        velocity = tuple(v / spread_norm * 1.5 for v in spread)
        bobber = cls(world, pos, velocity=velocity, angler=player, rng=source_rng)
        bobber.yaw = bobber.prev_yaw = float(player.yaw)
        bobber.pitch = bobber.prev_pitch = float(player.pitch)
        return bobber

    @property
    def center(self):
        return tuple(self.pos)

    def _block_type(self, x, y, z):
        try:
            return self.world_obj.get_block_type(int(x), int(y), int(z))
        except Exception:
            return None

    def _play_sound(self, name, volume=1.0, pitch=1.0):
        callback = getattr(self.world_obj, "sound_player", None)
        if callable(callback):
            try:
                callback(name, volume=volume, pitch=pitch, position=tuple(self.pos))
            except TypeError:
                callback(name, volume=volume, pitch=pitch)

    def _water_fraction(self):
        half_x = self.size[0] * 0.5
        half_z = self.size[2] * 0.5
        x0 = math.floor(self.pos[0] - half_x + 0.5)
        x1 = math.floor(self.pos[0] + half_x + 0.5)
        z0 = math.floor(self.pos[2] - half_z + 0.5)
        z1 = math.floor(self.pos[2] + half_z + 0.5)
        wet = 0
        for i in range(5):
            sample_y = self.pos[1] + self.size[1] * ((i + 0.5) / 5.0)
            by = math.floor(sample_y + 0.5)
            if any(
                self._block_type(bx, by, bz) in _WATER_TYPES
                for bx in range(x0, x1 + 1)
                for bz in range(z0, z1 + 1)
            ):
                wet += 1
        return wet / 5.0

    def _spawn_bite_effects(self):
        world = self.world_obj
        spawn_particle = getattr(world, "spawn_particle", None)
        if not callable(spawn_particle):
            return
        base_y = math.floor(self.pos[1] + 0.5) + 1.0
        self._play_sound(
            "random.splash", volume=0.25,
            pitch=1.0 + (_rng_float(self.rng) - _rng_float(self.rng)) * 0.4,
        )
        for _ in range(6):
            ox = (_rng_float(self.rng) * 2.0 - 1.0) * self.size[0]
            oz = (_rng_float(self.rng) * 2.0 - 1.0) * self.size[2]
            spawn_particle(
                "bubble", self.pos[0] + ox, base_y, self.pos[2] + oz,
                self.vel[0], self.vel[1] - _rng_float(self.rng) * 0.2, self.vel[2],
            )
        for _ in range(6):
            ox = (_rng_float(self.rng) * 2.0 - 1.0) * self.size[0]
            oz = (_rng_float(self.rng) * 2.0 - 1.0) * self.size[2]
            spawn_particle(
                "splash", self.pos[0] + ox, base_y, self.pos[2] + oz,
                self.vel[0], self.vel[1], self.vel[2],
            )

    def _owner_is_valid(self):
        owner = self.angler
        if owner is None or getattr(owner, "dead", False):
            return False
        if float(getattr(owner, "health", 20.0)) <= 0.0:
            return False
        inventory = getattr(owner, "inventory", None)
        held = inventory.get_active_item() if inventory is not None else None
        if held is None or getattr(held, "id", None) != "fishing_rod":
            return False
        dx = float(owner.pos[0]) - self.pos[0]
        dy = float(owner.pos[1]) - self.pos[1]
        dz = float(owner.pos[2]) - self.pos[2]
        return dx * dx + dy * dy + dz * dz <= 1024.0

    def _detach_from_owner(self):
        if self.angler is not None and getattr(self.angler, "fishing_hook", None) is self:
            self.angler.fishing_hook = None

    def _update_hooked_entity(self):
        target = self.hooked_entity
        if target is None:
            return False
        if getattr(target, "dead", False):
            self.hooked_entity = None
            return False
        target_pos = getattr(target, "pos", None)
        if target_pos is None:
            self.hooked_entity = None
            return False
        self.prev_pos[:] = self.pos
        height = float(getattr(target, "size", (0.6, 1.8, 0.6))[1])
        self.pos[:] = [float(target_pos[0]), float(target_pos[1]) + height * 0.8,
                       float(target_pos[2])]
        return True

    def _find_block_hit(self, start, end):
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
                        block = self.world_obj.get_block(bx, by, bz)
                    except Exception:
                        block = None
                    if block is None:
                        continue
                    block_type = getattr(block, "block_type", None)
                    if block_type in (*_WATER_TYPES, "lava", "flowing_lava", "fire"):
                        continue
                    if getattr(block, "collision", None) is False:
                        continue
                    try:
                        if hasattr(block, "get_collision_boxes"):
                            boxes = block.get_collision_boxes()
                        elif hasattr(block, "get_selection_boxes"):
                            boxes = block.get_selection_boxes()
                        else:
                            boxes = [block.get_selection_bounds()]
                    except Exception:
                        boxes = [(-0.5, -0.5, -0.5, 0.5, 0.5, 0.5)]
                    for local in boxes:
                        if local is None:
                            continue
                        bounds = (
                            bx + float(local[0]), by + float(local[1]), bz + float(local[2]),
                            bx + float(local[3]), by + float(local[4]), bz + float(local[5]),
                        )
                        hit = _segment_aabb(start, end, bounds)
                        if hit is not None and (nearest is None or hit[0] < nearest[0]):
                            nearest = (hit[0], (bx, by, bz), block_type, hit[1])
        return nearest

    def _find_entity_hit(self, start, end):
        candidates = list(getattr(self.world_obj, "entities", ()) or ())
        owner = self.angler
        if owner is not None and all(candidate is not owner for candidate in candidates):
            candidates.append(owner)
        nearest = None
        for target in candidates:
            if target is self or getattr(target, "dead", False):
                continue
            if target is owner and self.ticks_in_air < 5:
                continue
            if bool(getattr(target, "is_living_entity", False)):
                if getattr(target, "dead", False):
                    continue
            else:
                can_collide = getattr(target, "can_be_collided_with", None)
                if not callable(can_collide) or not can_collide():
                    continue
            pos = getattr(target, "pos", None)
            if pos is None:
                continue
            size = getattr(target, "size", (0.6, 1.8, 0.6))
            half_x, height, half_z = float(size[0]) * 0.5, float(size[1]), float(size[2]) * 0.5
            bounds = (
                float(pos[0]) - half_x - 0.3, float(pos[1]) - 0.3,
                float(pos[2]) - half_z - 0.3,
                float(pos[0]) + half_x + 0.3, float(pos[1]) + height + 0.3,
                float(pos[2]) + half_z + 0.3,
            )
            hit = _segment_aabb(start, end, bounds)
            if hit is not None and (nearest is None or hit[0] < nearest[0]):
                nearest = (hit[0], target)
        return nearest

    def _tick(self):
        if self.dead:
            return
        if self.angler is not None and not self._owner_is_valid():
            self.dead = True
            self._detach_from_owner()
            return

        self.prev_pos[:] = self.pos
        self.prev_yaw, self.prev_pitch = self.yaw, self.pitch

        if self._update_hooked_entity():
            return

        if self.in_ground:
            if self._block_type(self.x_tile, self.y_tile, self.z_tile) == self.in_tile:
                self.ticks_in_ground += 1
                if self.ticks_in_ground >= 1200:
                    self.dead = True
                    self._detach_from_owner()
                return
            self.in_ground = False
            self.vel[0] *= _rng_float(self.rng) * 0.2
            self.vel[1] *= _rng_float(self.rng) * 0.2
            self.vel[2] *= _rng_float(self.rng) * 0.2
            self.ticks_in_ground = 0
            self.ticks_in_air = 0

        self.ticks_in_air += 1

        start = tuple(self.pos)
        end = tuple(self.pos[i] + self.vel[i] for i in range(3))
        block_hit = self._find_block_hit(start, end)
        block_t = block_hit[0] if block_hit is not None else 1.0
        blocked_end = tuple(start[i] + (end[i] - start[i]) * block_t for i in range(3))
        entity_hit = self._find_entity_hit(start, blocked_end)

        if entity_hit is not None:
            _entity_t, target = entity_hit
            hit_callback = getattr(target, "on_fishing_hook_hit", None)
            accepted = bool(callable(hit_callback) and hit_callback(self.angler))
            if accepted:
                self.hooked_entity = target
        elif block_hit is not None:
            hit_t, cell, block_type, normal = block_hit
            self.pos[:] = [start[i] + (end[i] - start[i]) * hit_t
                           for i in range(3)]
            if normal and any(normal):
                self.pos[:] = [self.pos[i] - normal[i] * 0.02 for i in range(3)]
            self.x_tile, self.y_tile, self.z_tile = cell
            self.in_tile = block_type
            self.in_ground = True
            self.ticks_in_ground = 0
            return

        self.pos[:] = end
        horizontal = math.hypot(self.vel[0], self.vel[2])
        if any(abs(v) > 1.0e-12 for v in self.vel):
            yaw = math.degrees(math.atan2(self.vel[0], self.vel[2]))
            pitch = math.degrees(math.atan2(self.vel[1], horizontal))
            self.yaw = self.prev_yaw + _wrap_degrees(yaw - self.prev_yaw) * 0.2
            self.pitch = self.prev_pitch + _wrap_degrees(pitch - self.prev_pitch) * 0.2

        drag = 0.92
        water_fraction = self._water_fraction()
        if water_fraction > 0.0:
            if self.bite_ticks > 0:
                self.bite_ticks -= 1
            elif _rng_int(self.rng, 500) == 0:
                self.bite_ticks = _rng_int(self.rng, 30) + 10
                self.vel[1] -= 0.2
                self._spawn_bite_effects()
            if self.bite_ticks > 0:
                self.vel[1] -= (
                    _rng_float(self.rng) * _rng_float(self.rng)
                    * _rng_float(self.rng) * 0.2
                )

        self.vel[1] += 0.04 * (water_fraction * 2.0 - 1.0)
        if water_fraction > 0.0:
            drag *= 0.9
            self.vel[1] *= 0.8
        self.vel[0] *= drag
        self.vel[1] *= drag
        self.vel[2] *= drag

    def update(self, dt):
        if self.dead or self.world_obj is None:
            return
        self._tick_accum += min(max(float(dt), 0.0), MAX_DT) * TICKS_PER_SECOND
        ticks = min(int(self._tick_accum + 1.0e-9), 4)
        if ticks <= 0:
            return
        self._tick_accum -= ticks
        for _ in range(ticks):
            self._tick()
            if self.dead:
                break

    def reel_in(self):
        result = 0
        owner = self.angler
        if owner is not None and self.hooked_entity is not None:
            target = self.hooked_entity
            dx = float(owner.pos[0]) - self.pos[0]
            dy = float(owner.pos[1]) - self.pos[1]
            dz = float(owner.pos[2]) - self.pos[2]
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            target_vel = getattr(target, "vel", None)
            if target_vel is not None:
                target_vel[0] += dx * 0.1
                target_vel[1] += dy * 0.1 + math.sqrt(distance) * 0.08
                target_vel[2] += dz * 0.1
            result = 3
        elif owner is not None and self.bite_ticks > 0:
            dx = float(owner.pos[0]) - self.pos[0]
            dy = float(owner.pos[1]) - self.pos[1]
            dz = float(owner.pos[2]) - self.pos[2]
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            velocity = (dx * 0.1, dy * 0.1 + math.sqrt(distance) * 0.08, dz * 0.1)
            from game.entities.item import ItemEntity, ItemStack
            fish = ItemEntity(
                ItemStack("raw_fish", 1), tuple(self.pos),
                velocity=velocity,
            )
            self.world_obj.spawn_entity(fish)
            result = 1

        if self.in_ground:
            result = 2

        self.dead = True
        self._detach_from_owner()
        return result

    def _render_line(self, renderer, partial, bobber_render):
        owner = self.angler
        if owner is None:
            return

        hand_x, hand_y, hand_z = _fishing_line_hand_position(
            owner, renderer, partial,
        )
        if renderer is not None:
            camera = (
                float(getattr(renderer, "camX", 0.0)),
                float(getattr(renderer, "camY", 0.0)),
                float(getattr(renderer, "camZ", 0.0)),
            )
        else:
            camera = (0.0, 0.0, 0.0)
        hand_relative = (
            hand_x - camera[0], hand_y - camera[1], hand_z - camera[2],
        )

        line_points = _fishing_line_vertices(bobber_render, hand_relative)

        from OpenGL.GL import (
            GL_LINE_STRIP, GL_LINE_WIDTH, glBegin, glEnd, glGetFloatv,
            glLineWidth, glVertex3d,
        )
        try:
            old_line_width = float(glGetFloatv(GL_LINE_WIDTH))
        except Exception:
            old_line_width = 1.0
        try:
            glLineWidth(2.0)
            glBegin(GL_LINE_STRIP)
            for point in line_points:
                glVertex3d(*point)
            glEnd()
        finally:
            glLineWidth(old_line_width)

    def draw(self):
        if self.dead:
            return
        try:
            from OpenGL.GL import (
                GL_ALPHA_TEST, GL_BLEND, GL_CULL_FACE, GL_LIGHTING,
                GL_RESCALE_NORMAL, GL_TEXTURE_2D, glBegin, glColor3f,
                glDisable, glEnable, glEnd, glIsEnabled, glPopMatrix,
                glPushMatrix, glTexCoord2f, glTranslatef, glVertex3f,
            )
            from engine.textures import Texture
        except Exception:
            return

        if not hasattr(self, "_bobber_texture"):
            self._bobber_texture = Texture(_PARTICLES_TEXTURE)
        texture = self._bobber_texture
        if texture is None or texture.texture_id is None:
            return

        partial = 0.0
        try:
            partial = min(max(float(self.world_obj.partial_ticks()), 0.0), 1.0)
        except Exception:
            pass
        x = self.prev_pos[0] + (self.pos[0] - self.prev_pos[0]) * partial
        y = self.prev_pos[1] + (self.pos[1] - self.prev_pos[1]) * partial
        z = self.prev_pos[2] + (self.pos[2] - self.prev_pos[2]) * partial
        renderer = getattr(self.world_obj, "renderer", None) or Entity.renderer
        if renderer is not None and hasattr(renderer, "camera_relative_offset"):
            ox, oy, oz = renderer.camera_relative_offset(x, y, z)
            view_yaw = float(getattr(renderer, "yaw", 0.0))
            view_pitch = float(getattr(renderer, "pitch", 0.0))
        else:
            ox, oy, oz = x, y, z
            view_yaw = view_pitch = 0.0
        right, up, look = _camera_billboard_axes(view_yaw, view_pitch)

        old = {
            GL_ALPHA_TEST: bool(glIsEnabled(GL_ALPHA_TEST)),
            GL_BLEND: bool(glIsEnabled(GL_BLEND)),
            GL_CULL_FACE: bool(glIsEnabled(GL_CULL_FACE)),
            GL_LIGHTING: bool(glIsEnabled(GL_LIGHTING)),
            GL_TEXTURE_2D: bool(glIsEnabled(GL_TEXTURE_2D)),
            GL_RESCALE_NORMAL: bool(glIsEnabled(GL_RESCALE_NORMAL)),
        }
        matrix_pushed = False
        texture_bound = False
        try:
            glPushMatrix()
            matrix_pushed = True
            glEnable(GL_RESCALE_NORMAL)
            glEnable(GL_TEXTURE_2D)
            glEnable(GL_ALPHA_TEST)
            glDisable(GL_CULL_FACE)
            texture.bind()
            texture_bound = True
            brightness = self.get_entity_brightness()
            glColor3f(brightness, brightness, brightness)
            u0, u1 = _FISH_BOBBER_UV[0], _FISH_BOBBER_UV[1]
            v0, v1 = _FISH_BOBBER_UV[2], _FISH_BOBBER_UV[3]
            from OpenGL.GL import GL_QUADS
            half = 0.25
            rx, ry, rz = right
            ux, uy, uz = up
            cx, cy, cz = float(ox), float(oy), float(oz)
            normal = (-look[0], -look[1], -look[2])
            from OpenGL.GL import glNormal3f
            glNormal3f(*normal)
            glBegin(GL_QUADS)
            glTexCoord2f(u0, v0); glVertex3f(cx-rx*half-ux*half, cy-ry*half-uy*half, cz-rz*half-uz*half)
            glTexCoord2f(u1, v0); glVertex3f(cx+rx*half-ux*half, cy+ry*half-uy*half, cz+rz*half-uz*half)
            glTexCoord2f(u1, v1); glVertex3f(cx+rx*half+ux*half, cy+ry*half+uy*half, cz+rz*half+uz*half)
            glTexCoord2f(u0, v1); glVertex3f(cx-rx*half+ux*half, cy-ry*half+uy*half, cz-rz*half+uz*half)
            glEnd()
            texture.unbind()
            texture_bound = False
            glPopMatrix()
            matrix_pushed = False

            glDisable(GL_TEXTURE_2D)
            glDisable(GL_LIGHTING)
            glDisable(GL_BLEND)
            glColor3f(0.0, 0.0, 0.0)
            self._render_line(renderer, partial, (float(ox), float(oy), float(oz)))
        finally:
            if texture_bound:
                try:
                    texture.unbind()
                except Exception:
                    pass
            glColor3f(1.0, 1.0, 1.0)
            (glEnable if old[GL_ALPHA_TEST] else glDisable)(GL_ALPHA_TEST)
            (glEnable if old[GL_BLEND] else glDisable)(GL_BLEND)
            (glEnable if old[GL_CULL_FACE] else glDisable)(GL_CULL_FACE)
            (glEnable if old[GL_LIGHTING] else glDisable)(GL_LIGHTING)
            (glEnable if old[GL_TEXTURE_2D] else glDisable)(GL_TEXTURE_2D)
            (glEnable if old[GL_RESCALE_NORMAL] else glDisable)(GL_RESCALE_NORMAL)
            if matrix_pushed:
                glPopMatrix()
