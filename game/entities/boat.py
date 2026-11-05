
from __future__ import annotations

import math
import time
from typing import Optional

from game.entities.entity import Entity, MAX_DT, TICKS_PER_SECOND

_BOAT_TEXTURE = None
_BOAT_MODEL = None
_MAX_SPEED = 0.4
_WATER_SAMPLES = 5


def _create_boat_model(ModelPart):
    length, height, width, y_off = 24, 6, 20, 4
    sides = []

    bottom = ModelPart(0, 8, -length // 2, -width // 2 + 2, -3.0, length, width - 4, 4)
    bottom.set_rotation_point(0.0, float(0 + y_off), 0.0)
    bottom.rotate_angle_x = math.pi * 0.5
    sides.append(bottom)

    side1 = ModelPart(0, 0, -length // 2 + 2, -height - 1, -1.0, length - 4, height, 2)
    side1.set_rotation_point(float(-length // 2 + 1), float(0 + y_off), 0.0)
    side1.rotate_angle_y = math.pi * 3.0 / 2.0
    sides.append(side1)

    side2 = ModelPart(0, 0, -length // 2 + 2, -height - 1, -1.0, length - 4, height, 2)
    side2.set_rotation_point(float(length // 2 - 1), float(0 + y_off), 0.0)
    side2.rotate_angle_y = math.pi * 0.5
    sides.append(side2)

    side3 = ModelPart(0, 0, -length // 2 + 2, -height - 1, -1.0, length - 4, height, 2)
    side3.set_rotation_point(0.0, float(0 + y_off), float(-width // 2 + 1))
    side3.rotate_angle_y = math.pi
    sides.append(side3)

    side4 = ModelPart(0, 0, -length // 2 + 2, -height - 1, -1.0, length - 4, height, 2)
    side4.set_rotation_point(0.0, float(0 + y_off), float(width // 2 - 1))
    sides.append(side4)

    return sides


class BoatEntity(Entity):
    def __init__(self, world, pos, velocity=(0.0, 0.0, 0.0)):
        super().__init__(pos, size=(1.5, 0.6, 1.5))
        self.world_obj = world
        self.prev_pos = list(self.pos)
        self.vel = [float(v) for v in velocity]
        self.rider = None
        self.damage_taken = 0
        self.time_since_hit = 0
        self.forward_direction = 1
        self.yaw = 0.0
        self.pitch = 0.0
        self.prev_yaw = 0.0
        self._drop_handled = False
        self.prevent_entity_spawning = True
        self._tick_accum = 0.0
        self.turn_accum = 0.0

    def can_be_pushed(self):
        return not self.dead

    def can_be_collided_with(self):
        return not self.dead

    def get_bounding_box(self, position=None):
        px, py, pz = self.pos if position is None else position
        half_x = float(self.size[0]) * 0.5
        half_z = float(self.size[2]) * 0.5
        height = float(self.size[1])
        return (
            px - half_x, py, pz - half_z,
            px + half_x, py + height, pz + half_z,
        )

    def get_collision_box(self, other=None):
        return self.get_bounding_box()

    def get_interaction_bounds(self):
        return self.get_bounding_box()

    def get_mounted_y_offset(self):
        return -0.3

    def attack_entity_from(self, attacker, damage):
        if self.dead:
            return False
        self.forward_direction = -self.forward_direction
        self.time_since_hit = 10
        self.damage_taken += max(0, int(damage)) * 10
        if self.damage_taken > 40:
            self._break_and_drop()
        return True

    def interact(self, player) -> bool:
        if self.dead:
            return False
        if self.rider is not None and self.rider is not player:
            return False
        remote_rider = int(getattr(self, "_mp_remote_rider_id", 0) or 0)
        if remote_rider > 0 and player is not self.rider:
            return False
        if getattr(player, "riding_entity", None) is self:
            self.dismount()
            return True
        self.mount(player)
        return True

    def mount(self, player) -> None:
        if player is None:
            return
        prev = getattr(player, "riding_entity", None)
        if prev is not None and prev is not self:
            dismount = getattr(prev, "dismount", None)
            if callable(dismount):
                dismount()
        self.rider = player
        self.turn_accum = 0.0
        player.riding_entity = self
        player.vel[0] = 0.0
        player.vel[1] = 0.0
        player.vel[2] = 0.0
        self.vel[0] = 0.0
        self.vel[2] = 0.0
        self._position_rider()

    def dismount(self) -> None:
        rider = self.rider
        self.rider = None
        if rider is not None:
            if getattr(rider, "riding_entity", None) is self:
                rider.riding_entity = None
            try:
                rider.pos[0] = float(self.pos[0])
                rider.pos[1] = float(self.pos[1]) + 0.5
                rider.pos[2] = float(self.pos[2])
                rider.vel[0] = 0.0
                rider.vel[1] = 0.0
                rider.vel[2] = 0.0
                rider.sync_camera()
            except Exception:
                pass

    def consume_turn(self) -> float:
        turn = self.turn_accum
        self.turn_accum = 0.0
        return turn

    def _position_rider(self, partial: float = 1.0) -> None:
        if self.rider is None:
            return
        bx = self.prev_pos[0] + (self.pos[0] - self.prev_pos[0]) * partial
        by = self.prev_pos[1] + (self.pos[1] - self.prev_pos[1]) * partial
        bz = self.prev_pos[2] + (self.pos[2] - self.prev_pos[2]) * partial
        yaw_r = math.radians(self.yaw)
        ox = math.cos(yaw_r) * 0.4
        oz = math.sin(yaw_r) * 0.4
        self.rider.pos[0] = float(bx) + ox
        self.rider.pos[1] = float(by) + self.get_mounted_y_offset()
        self.rider.pos[2] = float(bz) + oz
        self.rider.vel[0] = self.vel[0]
        self.rider.vel[1] = self.vel[1]
        self.rider.vel[2] = self.vel[2]
        try:
            self.rider.sync_camera()
        except Exception:
            pass

    def _break_and_drop(self) -> None:
        if self._drop_handled:
            return
        self._drop_handled = True
        self.dismount()
        from game.entities.item import ItemEntity, ItemStack
        for _ in range(3):
            self.world_obj.spawn_entity(ItemEntity(
                ItemStack("planks"), tuple(self.pos), pickup_delay=10,
            ))
        for _ in range(2):
            self.world_obj.spawn_entity(ItemEntity(
                ItemStack("stick"), tuple(self.pos), pickup_delay=10,
            ))
        self.dead = True

    def set_dead(self) -> None:
        self.dismount()
        self.dead = True

    def _rider_input_velocity(self):
        remote_control = getattr(self, "_mp_remote_control", None)
        if isinstance(remote_control, dict):
            if time.monotonic() - float(remote_control.get("updated_at", 0.0)) > 0.75:
                self._mp_remote_control = None
                self._mp_remote_rider_id = 0
                remote_control = None
        if isinstance(remote_control, dict):
            forward_amount = max(-1.0, min(1.0, float(remote_control.get("forward", 0.0))))
            strafe_amount = max(-1.0, min(1.0, float(remote_control.get("strafe", 0.0))))
            yaw = float(remote_control.get("yaw", self.yaw))
        else:
            rider = self.rider
            if rider is None:
                return 0.0, 0.0
            keys = getattr(rider, "_current_keys", None)
            if keys is None:
                return 0.0, 0.0
            try:
                import pygame as pg
            except Exception:
                return 0.0, 0.0

            def pressed(name, default):
                key_fn = getattr(rider, "_key", None)
                try:
                    code = key_fn(name, default) if callable(key_fn) else default
                except Exception:
                    code = default
                try:
                    return bool(keys[code])
                except Exception:
                    return False

            yaw = float(getattr(rider, "yaw", self.yaw))
            forward_amount = float(pressed("forward", pg.K_w)) - float(pressed("back", pg.K_s))
            strafe_amount = float(pressed("right", pg.K_d)) - float(pressed("left", pg.K_a))
            analog_axes = getattr(keys, "movement_axes", None)
            if analog_axes is not None:
                try:
                    analog_strafe, analog_forward = analog_axes
                    forward_amount += max(-1.0, min(1.0, float(analog_forward)))
                    strafe_amount += max(-1.0, min(1.0, float(analog_strafe)))
                except (TypeError, ValueError):
                    pass
            forward_amount = max(-1.0, min(1.0, forward_amount))
            strafe_amount = max(-1.0, min(1.0, strafe_amount))

        yaw_rad = math.radians(yaw)
        fx, fz = -math.sin(yaw_rad), -math.cos(yaw_rad)
        rx, rz = -fz, fx
        mx = fx * forward_amount + rx * strafe_amount
        mz = fz * forward_amount + rz * strafe_amount
        length = math.hypot(mx, mz)
        if length > 1e-6:
            scale = 0.0182 / length
            mx *= scale
            mz *= scale
        return mx, mz

    def _water_fraction(self) -> float:
        world = self.world_obj
        if world is None or not hasattr(world, "get_block_type"):
            return 0.0
        box = self.get_bounding_box()
        min_x, min_y, min_z, max_x, max_y, max_z = box
        filled = 0.0
        for i in range(_WATER_SAMPLES):
            y0 = min_y + (max_y - min_y) * i / _WATER_SAMPLES - 0.125
            y1 = min_y + (max_y - min_y) * (i + 1) / _WATER_SAMPLES - 0.125
            if self._aabb_in_water(min_x, y0, min_z, max_x, y1, max_z):
                filled += 1.0 / _WATER_SAMPLES
        return filled

    def _aabb_in_water(self, min_x, min_y, min_z, max_x, max_y, max_z) -> bool:
        world = self.world_obj
        x0 = int(math.floor(min_x + 0.5))
        x1 = int(math.floor(max_x + 0.5))
        y0 = int(math.floor(min_y + 0.5))
        y1 = int(math.floor(max_y + 0.5))
        z0 = int(math.floor(min_z + 0.5))
        z1 = int(math.floor(max_z + 0.5))
        for bx in range(x0, x1 + 1):
            for by in range(y0, y1 + 1):
                for bz in range(z0, z1 + 1):
                    block = world.get_block_type(bx, by, bz)
                    if block not in ("water", "flowing_water"):
                        continue
                    surface = self._water_surface_y(bx, by, bz)
                    if min_y < surface and max_y > by - 0.5:
                        return True
        return False

    def _water_surface_y(self, bx, by, bz) -> float:
        try:
            Entity.world = self.world_obj
            return float(self._liquid_surface_y(bx, by, bz))
        except Exception:
            return by + 0.5 - 1.0 / 9.0

    def _spawn_splash_particles(self, speed: float) -> None:
        world = self.world_obj
        if world is None:
            return
        import random
        yaw_r = math.radians(self.yaw)
        cos_y = math.cos(yaw_r)
        sin_y = math.sin(yaw_r)
        count = int(1.0 + speed * 60.0)
        for _ in range(min(count, 12)):
            side = random.random() * 2.0 - 1.0
            edge = (random.randint(0, 1) * 2 - 1) * 0.7
            if random.random() < 0.5:
                sx = self.pos[0] - cos_y * side * 0.8 + sin_y * edge
                sz = self.pos[2] - sin_y * side * 0.8 - cos_y * edge
            else:
                sx = self.pos[0] + cos_y + sin_y * side * 0.7
                sz = self.pos[2] + sin_y - cos_y * side * 0.7
            world.spawn_particle(
                "splash", sx, self.pos[1] - 0.125, sz,
                self.vel[0], self.vel[1], self.vel[2],
            )

    def update(self, dt: float = 0.05) -> None:
        if self.dead:
            return
        tick_len = 1.0 / TICKS_PER_SECOND
        self._tick_accum += min(max(float(dt), 0.0), MAX_DT)
        while self._tick_accum >= tick_len and not self.dead:
            self._tick_accum -= tick_len
            self._tick(tick_len)
        if self.rider is not None and not self.dead:
            self._position_rider(self._tick_accum * TICKS_PER_SECOND)

    def _tick(self, dt: float) -> None:
        if self.time_since_hit > 0:
            self.time_since_hit -= 1
        if self.damage_taken > 0:
            self.damage_taken -= 1

        self.prev_pos = list(self.pos)
        self.prev_yaw = self.yaw

        water_frac = self._water_fraction()
        buoyancy = water_frac * 2.0 - 1.0
        self.vel[1] += 0.04 * buoyancy

        if self.rider is not None:
            if getattr(self.rider, "dead", False):
                self.dismount()
            else:
                rider_mx, rider_mz = self._rider_input_velocity()
                self.vel[0] += rider_mx * 0.2
                self.vel[2] += rider_mz * 0.2
        elif isinstance(getattr(self, "_mp_remote_control", None), dict):
            rider_mx, rider_mz = self._rider_input_velocity()
            self.vel[0] += rider_mx * 0.2
            self.vel[2] += rider_mz * 0.2

        self.vel[0] = max(-_MAX_SPEED, min(_MAX_SPEED, self.vel[0]))
        self.vel[2] = max(-_MAX_SPEED, min(_MAX_SPEED, self.vel[2]))

        if self.on_ground:
            self.vel[0] *= 0.5
            self.vel[1] *= 0.5
            self.vel[2] *= 0.5

        tick_scale = dt * TICKS_PER_SECOND
        dx = self.vel[0] * tick_scale
        dy = self.vel[1] * tick_scale
        dz = self.vel[2] * tick_scale

        Entity.world = self.world_obj
        self.move(dx, dy, dz)

        speed = math.sqrt(self.vel[0] ** 2 + self.vel[2] ** 2)
        if speed > 0.15:
            self._spawn_splash_particles(speed)

        if self.horizontal_collision and speed > 0.15:
            self._break_and_drop()
            return

        self.vel[0] *= 0.99
        self.vel[1] *= 0.95
        self.vel[2] *= 0.99

        self.pitch = 0.0
        target_yaw = self.yaw
        moved_x = self.prev_pos[0] - self.pos[0]
        moved_z = self.prev_pos[2] - self.pos[2]
        if moved_x * moved_x + moved_z * moved_z > 0.001:
            target_yaw = math.degrees(math.atan2(moved_z, moved_x))

        delta = (target_yaw - self.yaw + 180.0) % 360.0 - 180.0
        delta = max(-20.0, min(20.0, delta))
        self.yaw = (self.yaw + delta) % 360.0
        self.turn_accum += delta

        self._push_nearby_boats()
        if self.rider is not None:
            self._position_rider()

    def _push_nearby_boats(self) -> None:
        world = self.world_obj
        if world is None:
            return
        for other in list(getattr(world, "entities", ())):
            if other is self or getattr(other, "dead", False):
                continue
            if not isinstance(other, BoatEntity):
                continue
            if not other.can_be_pushed():
                continue
            dx = other.pos[0] - self.pos[0]
            dz = other.pos[2] - self.pos[2]
            if dx * dx + dz * dz < (1.7 ** 2):
                other.apply_entity_collision(self)

    def draw(self) -> None:
        if self.dead:
            return
        try:
            from OpenGL.GL import (
                GL_ALPHA_TEST, GL_COLOR_MATERIAL, GL_CULL_FACE, GL_LIGHT0,
                GL_LIGHT1, GL_LIGHTING, GL_NORMALIZE, GL_TEXTURE_2D,
                glColor3f, glDisable, glEnable, glIsEnabled, glPopMatrix,
                glPushMatrix, glRotatef, glScalef, glTranslatef,
            )
        except Exception:
            return
        from engine.textures import Texture
        from game.entities.player_model import ModelPart

        global _BOAT_TEXTURE, _BOAT_MODEL
        if _BOAT_TEXTURE is None:
            _BOAT_TEXTURE = Texture("game/textures/item/boat.png")
        if _BOAT_MODEL is None:
            _BOAT_MODEL = _create_boat_model(ModelPart)

        renderer = getattr(self.world_obj, "renderer", None) or Entity.renderer
        partial = max(0.0, min(1.0, self._tick_accum * TICKS_PER_SECOND))

        px = self.prev_pos[0] + (self.pos[0] - self.prev_pos[0]) * partial
        py = self.prev_pos[1] + (self.pos[1] - self.prev_pos[1]) * partial
        pz = self.prev_pos[2] + (self.pos[2] - self.prev_pos[2]) * partial
        py = py + float(self.size[1]) * 0.5
        yaw = self.prev_yaw + ((self.yaw - self.prev_yaw + 180.0) % 360.0 - 180.0) * partial

        if renderer is not None and hasattr(renderer, "camera_relative_offset"):
            ox, oy, oz = renderer.camera_relative_offset(px, py, pz)
        else:
            ox, oy, oz = px, py, pz

        cull_enabled = bool(glIsEnabled(GL_CULL_FACE))
        lighting_enabled = bool(glIsEnabled(GL_LIGHTING))
        light0_enabled = bool(glIsEnabled(GL_LIGHT0))
        light1_enabled = bool(glIsEnabled(GL_LIGHT1))
        color_material_enabled = bool(glIsEnabled(GL_COLOR_MATERIAL))
        normalize_enabled = bool(glIsEnabled(GL_NORMALIZE))
        texture_enabled = bool(glIsEnabled(GL_TEXTURE_2D))
        alpha_enabled = bool(glIsEnabled(GL_ALPHA_TEST))
        standard_lighting_setup = False
        glPushMatrix()
        try:
            if not lighting_enabled:
                try:
                    from engine.legacy_lighting import enable_standard_item_lighting
                    enable_standard_item_lighting()
                    standard_lighting_setup = True
                except Exception:
                    pass
            glEnable(GL_NORMALIZE)
            glDisable(GL_CULL_FACE)
            glTranslatef(float(ox), float(oy), float(oz))
            glRotatef(180.0 - float(yaw), 0.0, 1.0, 0.0)
            hit_time = max(0.0, self.time_since_hit - partial)
            damage = max(0.0, self.damage_taken - partial)
            if hit_time > 0.0:
                angle = math.sin(hit_time) * hit_time * damage / 10.0 * self.forward_direction
                glRotatef(float(angle), 1.0, 0.0, 0.0)
            glEnable(GL_TEXTURE_2D)
            brightness = max(0.15, float(self.get_entity_brightness()))
            glColor3f(brightness, brightness, brightness)
            _BOAT_TEXTURE.bind()
            glScalef(-1.0, -1.0, 1.0)
            for part in _BOAT_MODEL:
                part.render(1.0 / 16.0)
        finally:
            glColor3f(1.0, 1.0, 1.0)
            if _BOAT_TEXTURE is not None:
                _BOAT_TEXTURE.unbind()
            (glEnable if cull_enabled else glDisable)(GL_CULL_FACE)
            if standard_lighting_setup:
                try:
                    from engine.legacy_lighting import disable_standard_item_lighting
                    disable_standard_item_lighting()
                except Exception:
                    pass
            (glEnable if lighting_enabled else glDisable)(GL_LIGHTING)
            (glEnable if light0_enabled else glDisable)(GL_LIGHT0)
            (glEnable if light1_enabled else glDisable)(GL_LIGHT1)
            (glEnable if color_material_enabled else glDisable)(GL_COLOR_MATERIAL)
            (glEnable if normalize_enabled else glDisable)(GL_NORMALIZE)
            (glEnable if texture_enabled else glDisable)(GL_TEXTURE_2D)
            (glEnable if alpha_enabled else glDisable)(GL_ALPHA_TEST)
            glPopMatrix()
