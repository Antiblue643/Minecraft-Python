from __future__ import annotations

import math

from .creature import Creature
from .entity import Entity


class Squid(Creature):

    WIDTH = 0.95
    HEIGHT = 0.95
    HEALTH = 10
    MOVE_SPEED = 0.0
    MAX_SPAWN_GROUP = 4
    SHADOW_SIZE = 0.7
    MANAGES_AI_ORIENTATION = True

    GROUND_CONTACT_IN_WATER = False
    USE_AIR_DRAG_IN_WATER = True
    DRAG_XZ = 1.0
    DRAG_Y = 1.0
    WATER_GRAVITY = 0.0

    LIVING_SOUND = None
    HURT_SOUND = None
    DEATH_SOUND = None
    DROP_ITEM = None

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.size = (self.WIDTH, self.HEIGHT, self.WIDTH)
        self.skin_path = "mob/squid.png"
        self.shadow_size = self.SHADOW_SIZE
        self.health = self.HEALTH
        self.prev_health = self.HEALTH
        self._living_fluid_physics = True

        self.swim_phase = 0.0
        self.prev_swim_phase = 0.0
        self.body_yaw_animation = 0.0
        self.prev_body_yaw_animation = 0.0
        self.tentacle_angle = 0.0
        self.prev_tentacle_angle = 0.0
        self.tentacle_phase_speed = 1.0 / (self._next_float() + 1.0) * 0.2
        self.swim_velocity_strength = 0.0
        self.swim_body_strength = 0.0
        self.desired_motion = [0.0, 0.0, 0.0]
        self._drops_spawned = False
        self.max_air = 300
        self.air = self.max_air

    def _next_float(self) -> float:
        next_float = getattr(self._rng, "next_float", None)
        return float(next_float()) if callable(next_float) else float(self._rng.random())

    def _next_int(self, bound: int) -> int:
        next_int = getattr(self._rng, "next_int", None)
        return int(next_int(bound)) if callable(next_int) else int(self._rng.randrange(bound))

    def can_breathe_underwater(self) -> bool:
        return True

    def _check_water(self) -> None:
        super()._check_water()
        world = Entity.world
        if world is None:
            return
        px, py, pz = (float(value) for value in self.pos)
        hw, hd = self._half_width(), self._half_depth()
        min_y, max_y = py - 0.6, py + float(self.size[1])
        self.in_water = False
        flow_x = flow_y = flow_z = 0.0
        for bx in range(math.floor(px - hw + 0.5), math.floor(px + hw + 0.5) + 1):
            if not (px - hw < bx + 0.5 and px + hw > bx - 0.5):
                continue
            for bz in range(math.floor(pz - hd + 0.5), math.floor(pz + hd + 0.5) + 1):
                if not (pz - hd < bz + 0.5 and pz + hd > bz - 0.5):
                    continue
                for by in range(math.floor(min_y + 0.5), math.floor(max_y + 0.5) + 1):
                    if world.get_block_type(bx, by, bz) not in ("water", "flowing_water"):
                        continue
                    surface_y = self._liquid_surface_y(bx, by, bz)
                    if min_y < surface_y and max_y > by - 0.5:
                        self.in_water = True
                        if self.water_surface_y is None or surface_y > self.water_surface_y:
                            self.water_surface_y = surface_y
                        fx, fy, fz = self._water_flow_at(bx, by, bz)
                        flow_x += fx
                        flow_y += fy
                        flow_z += fz
        flow_length = math.sqrt(flow_x * flow_x + flow_y * flow_y + flow_z * flow_z)
        self.water_flow = (
            (flow_x / flow_length, flow_y / flow_length, flow_z / flow_length)
            if flow_length > 1e-8 else (0.0, 0.0, 0.0)
        )

    def _play_world_sound(self, name, volume=1.0, pitch=1.0, position=None) -> None:
        if name:
            super()._play_world_sound(name, volume=volume, pitch=pitch, position=position)

    def _update_player_action_state(self) -> None:
        self.has_attacked = False
        self.player_to_attack = None
        self.path_to_entity = None
        self.move_forward = 0.0
        self.move_strafing = 0.0
        self.is_jumping = False

        if (
            self._next_int(50) == 0
            or not self.in_water
            or (self.desired_motion[0] == 0.0
                and self.desired_motion[1] == 0.0
                and self.desired_motion[2] == 0.0)
        ):
            angle = self._next_float() * math.tau
            self.desired_motion[0] = math.cos(angle) * 0.2
            self.desired_motion[1] = -0.1 + self._next_float() * 0.2
            self.desired_motion[2] = math.sin(angle) * 0.2

    def _update_heading_acceleration(self, ticks: float) -> None:
        self._heading_accel = (0.0, 0.0)

    def update(self, dt):
        self.prev_render_yaw_offset = self.render_yaw_offset
        return super().update(dt)

    def _advance_living_visual_tick(self, dx: float, dz: float, travelled: float) -> None:
        return None

    def _living_ai_tick(self) -> None:
        super()._living_ai_tick()
        if (self.dead or self.health <= 0
                or bool(getattr(type(self), "global_ai_paused", False))
                or bool(getattr(type(self), "ai_frozen", False))):
            return

        self.prev_swim_phase = self.swim_phase
        self.prev_body_yaw_animation = self.body_yaw_animation
        self.prev_tentacle_angle = self.tentacle_angle
        self.swim_phase += self.tentacle_phase_speed
        if self.swim_phase > math.tau:
            self.swim_phase -= math.tau
            if self._next_int(10) == 0:
                self.tentacle_phase_speed = 1.0 / (self._next_float() + 1.0) * 0.2

        if self.in_water:
            if self.swim_phase < math.pi:
                fraction = self.swim_phase / math.pi
                self.tentacle_angle = math.sin(fraction * fraction * math.pi) * math.pi * 0.25
                if fraction > 0.75:
                    self.swim_velocity_strength = 1.0
                    self.swim_body_strength = 1.0
                else:
                    self.swim_body_strength *= 0.8
            else:
                self.tentacle_angle = 0.0
                self.swim_velocity_strength *= 0.9
                self.swim_body_strength *= 0.99

            if not self._movement_blocked():
                self.vel[0] = self.desired_motion[0] * self.swim_velocity_strength
                self.vel[1] = self.desired_motion[1] * self.swim_velocity_strength
                self.vel[2] = self.desired_motion[2] * self.swim_velocity_strength

            horizontal_speed = math.hypot(self.vel[0], self.vel[2])
            target_yaw = -math.degrees(math.atan2(self.vel[0], self.vel[2]))
            self.render_yaw_offset += (target_yaw - self.render_yaw_offset) * 0.1
            self.yaw = self.render_yaw_offset
            self.body_yaw_animation += math.pi * self.swim_body_strength * 1.5
            target_pitch = -math.degrees(math.atan2(horizontal_speed, self.vel[1]))
            self.rotation_pitch += (target_pitch - self.rotation_pitch) * 0.1
        else:
            self.tentacle_angle = abs(math.sin(self.swim_phase)) * math.pi * 0.25
            if not self._movement_blocked():
                self.vel[0] = 0.0
                self.vel[1] = (self.vel[1] - 0.08) * 0.98
                self.vel[2] = 0.0
            self.rotation_pitch += (-90.0 - self.rotation_pitch) * 0.02

    def _movement_blocked(self) -> bool:
        return self.health <= 0 or self.dead

    def _tick_living_hazards(self, dt: float) -> None:
        self.air = self.max_air
        if self.dead or self.health <= 0:
            return
        self._survival_tick_accum += min(max(float(dt), 0.0), 0.25)
        ticks = 0
        while self._survival_tick_accum >= 0.05 and ticks < 4:
            self._survival_tick_accum -= 0.05
            ticks += 1
            if self.in_water:
                self._fire_ticks = 0
            elif self.in_lava:
                self._fire_ticks = 600
                self.attack_entity_from(None, 1)
            elif self._fire_ticks > 0:
                if self._fire_ticks % 20 == 0:
                    self.attack_entity_from(None, 1)
                self._fire_ticks -= 1
            elif self._is_touching_fire():
                self.attack_entity_from(None, 1)
                if self._fire_ticks < 0:
                    self._fire_ticks += 1
                elif self._fire_ticks == 0:
                    self._fire_ticks = 300
            else:
                self._fire_ticks = -20
            if self._inside_opaque_block():
                self.attack_entity_from(None, 1)

    def get_can_spawn_here(self, x: float, y: float, z: float) -> bool:
        if Entity.world is None:
            return False
        return self._aabb_clear_of_spawn_blockers(float(x), float(y), float(z))

    def _on_death(self, attacker=None) -> None:
        if self._drops_spawned:
            return
        self._drops_spawned = True
        count = self._next_int(3) + 1
        for _ in range(count):
            self.drop_item("dye", 1, damage=0)

    def interact(self, player) -> bool:
        if self.dead or player is None or getattr(player, "health", 1) <= 0:
            return False
        inventory = getattr(player, "inventory", None)
        get_active = getattr(inventory, "get_active_item", None)
        held = get_active() if callable(get_active) else None
        if (held is None or getattr(held, "id", None) != "bucket"
                or int(getattr(held, "count", 0)) <= 0):
            return False

        world = Entity.world
        if (bool(getattr(world, "_multiplayer_client_world", False))
                and bool(getattr(self, "_mp_remote_entity", False))):
            request = getattr(world, "_network_entity_interact_callback", None)
            return bool(request(self, "milk_squid")) if callable(request) else False

        set_stack = getattr(inventory, "set_stack", None)
        if not callable(set_stack):
            return False
        slot = getattr(inventory, "active_idx", getattr(inventory, "current_item", 0))
        from .item import ItemStack
        set_stack(slot, ItemStack("milk_bucket"))
        return True

    def draw(self):
        from .mob_models import draw_living_with_model
        draw_living_with_model(self, "squid")


EntitySquid = Squid
