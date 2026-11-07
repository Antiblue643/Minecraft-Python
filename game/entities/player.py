import math
import random
import time
import pygame as pg
from game.entities.entity import Entity, TICKS_PER_SECOND
from game.entities.player_cosmetics import initialize_cape_motion, advance_cape_motion
from game.block import Block
from game.gameplay.inventory import Inventory
from game.entities.item import Item, ItemStack, ItemEntity
from game.entities.combat import item_hit_entity_wear, scale_incoming_player_damage
from game.block.drops import block_drops, can_harvest, wear_from_breaking, scatter_position
from game.block.placement import (can_place, placement_meta, placement_position,
                                 align_door_meta_with_neighbor, toggle_door_metadata,
                                 refresh_rail_and_neighbors)
from game.block.material import material_for_block, step_sound_for_block
from game.block.bucket_rules import bucket_result_for_source, bucket_target_is_solid


def _network_edit_action(method):
    from functools import wraps

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        world = getattr(self, "world", None)
        if world is None:
            return method(self, *args, **kwargs)
        world._network_edit_depth = getattr(world, "_network_edit_depth", 0) + 1
        try:
            return method(self, *args, **kwargs)
        finally:
            world._network_edit_depth = max(0, getattr(world, "_network_edit_depth", 1) - 1)

    return wrapped


def alpha_fall_replay(y0, vy0, y_end, max_ticks=400):
    y = float(y0)
    vy = float(vy0)
    counted = 0.0
    for _ in range(max_ticks):
        ny = y + vy
        if vy < 0.0 and ny <= y_end:
            return counted, counted + (y - y_end)
        if vy < 0.0:
            counted += -vy
        y = ny
        vy = (vy - 0.08) * 0.98
    return None


class Player(Entity):
    EYE_HEIGHT = 1.62
    WATER_SUBMERSION_HEIGHT = EYE_HEIGHT + 0.12
    GROUND_CONTACT_IN_WATER = True
    REACH = 4.0
    GROUND_ACCEL = 0.1
    AIR_ACCEL = 0.02
    is_living_entity = True
    WATER_ACCEL = 0.02

    settings = None

    def _key(self, name, default):
        binding = getattr(self.settings, 'key_' + name, None)
        return binding.key if binding is not None else default

    def get_look_vector(self, partial_ticks: float = 1.0):
        partial = max(0.0, min(1.0, float(partial_ticks)))
        prev_yaw = float(getattr(self, "prev_yaw", self.yaw))
        yaw_delta = (float(self.yaw) - prev_yaw + 180.0) % 360.0 - 180.0
        yaw = math.radians(prev_yaw + yaw_delta * partial)
        prev_pitch = float(getattr(self, "prev_pitch", self.pitch))
        pitch_delta = (float(self.pitch) - prev_pitch + 180.0) % 360.0 - 180.0
        pitch = math.radians(prev_pitch + pitch_delta * partial)
        cos_pitch = math.cos(pitch)
        return (
            -math.sin(yaw) * cos_pitch,
            math.sin(pitch),
            -math.cos(yaw) * cos_pitch,
        )

    THROW_PICKUP_DELAY = 40
    BREAK_PICKUP_DELAY = 10

    def __init__(self, pos, gamemode=0, username="Player"):
        super().__init__(pos, size=(0.6, 1.8, 0.6))
        self.skin_username = str(username).strip() or "Player"
        self.cape_path = None
        self.cape_texture = None
        if Entity.world is not None:
            Entity.world.player = self
        self._living_fluid_physics = True
        self.yaw = 0.0
        self.pitch = 0.0
        self.prev_yaw = 0.0
        self.prev_pitch = 0.0
        self.prev_pos = list(self.pos)
        initialize_cape_motion(self)
        self.state = "stand"
        self.is_sneaking = False
        self.inventory = Inventory(self)
        self.fishing_hook = None
        self.health = 20
        self.prev_health = 20
        self.hearts_life = 0
        self.hurt_time = 0
        self.max_hurt_time = 10
        self.attacked_at_yaw = 0.0
        self.death_time = 0
        self._death_tick_accum = 0.0
        self._death_pose_active = False
        self.air = 300
        self.max_air = 300
        self._survival_tick_accum = 0.0
        self._fire_ticks = 0
        self._damage_remainder = 0
        self._death_handled = False
        self._death_particles_spawned = False
        self.on_death_callback = None
        self.gamemode = gamemode
        self.riding_entity = None
        self.mouselocked = True
        self.menuopen = ""
        self.sound_player = None
        self.workbench_opener = None
        self.chest_opener = None
        self.furnace_opener = None
        self.dispenser_opener = None
        self.minecart_chest_opener = None
        self.sign_opener = None
        self._distance_walked = 0.0
        self._fall_distance = 0.0
        self._fall_pending = 0.0
        self._fall_tick_clock = 0.0
        self._splash_initialized = False
        self._splash_cooldown = 0.0
        self._water_ambient_timer = random.uniform(3.0, 6.0)
        self._fire_ambient_timer = random.uniform(2.0, 5.0)
        self._anim_accum = 0.0
        self._digging = False
        self._dig_target = None
        self._destroy_progress = 0.0
        self._dig_sound_counter = 0
        self._dig_tick_accum = 0.0
        self._block_hit_wait = 0
        self._held_attack_click_ticks = 0
        self._left_click_counter = 0
        self._using_item = False
        self._held_use_tick_accum = 0.0
        self._held_use_cooldown = 0
        self._heading_accel = (0.0, 0.0)
        self.limb_swing = 0.0
        self.limb_swing_amount = 0.0
        self.prev_limb_swing_amount = 0.0
        self.distance_walked_modified = 0.0
        self.prev_distance_walked_modified = 0.0
        self.camera_yaw = 0.0
        self.prev_camera_yaw = 0.0
        self.camera_pitch = 0.0
        self.prev_camera_pitch = 0.0
        self.render_yaw_offset = 0.0
        self.prev_render_yaw_offset = 0.0
        self.ticks_existed = 0
        self.swing_progress = 0.0
        self.prev_swing_progress = 0.0
        self._swing_progress_int = 0
        self._is_swing_in_progress = False
        self._anim_tick_accum = 0.0
        self._limb_distance_accum = 0.0
        self.equipped_progress = 0.0
        self.prev_equipped_progress = 0.0
        self._equipped_item = None
        self._actionbar_message = ""
        self._actionbar_started_at = 0.0
        self._actionbar_expires_at = 0.0

    @property
    def animation_partial(self):
        world = Entity.world
        if world is not None:
            try:
                return min(max(float(world.partial_ticks()), 0.0), 1.0)
            except (AttributeError, TypeError, ValueError):
                try:
                    return min(max(float(getattr(world, "_tick_accum", 0.0)) / 0.05, 0.0), 1.0)
                except (TypeError, ValueError):
                    pass
        if self.health <= 0 and self._death_pose_active:
            accum = self._death_tick_accum
        else:
            accum = self._anim_tick_accum
        return min(max(float(accum) / 0.05, 0.0), 1.0)

    @property
    def inventory_animation_partial(self):
        return min(max(float(self._anim_accum) / 0.05, 0.0), 1.0)

    @property
    def hurt_animation_partial(self):
        accum = self._death_tick_accum if self.health <= 0 else self._survival_tick_accum
        return min(max(float(accum) / 0.05, 0.0), 1.0)

    @property
    def death_animation_partial(self):
        return min(max(float(self._death_tick_accum) / 0.05, 0.0), 1.0)

    @property
    def armor_value(self):
        return self.inventory.get_total_armor_value()

    def on_zero_damage_projectile_hit(self, attacker=None) -> bool:
        if self.health <= 0:
            return False
        if self.hurt_time > self.max_hurt_time / 2:
            return False

        self.hurt_time = self.max_hurt_time = 10
        self.limb_swing_amount = 1.5
        if attacker is not None and hasattr(attacker, "pos"):
            dx = float(attacker.pos[0]) - float(self.pos[0])
            dz = float(attacker.pos[2]) - float(self.pos[2])
            if dx * dx + dz * dz < 1.0e-4:
                dx = random.uniform(-0.01, 0.01)
                dz = random.uniform(-0.01, 0.01)
            self.attacked_at_yaw = math.degrees(math.atan2(dz, dx)) - self.yaw
            length = math.sqrt(dx * dx + dz * dz) or 1.0
            self.vel[0] = self.vel[0] * 0.5 - dx / length * 0.4
            self.vel[1] = min(0.4, self.vel[1] * 0.5 + 0.4)
            self.vel[2] = self.vel[2] * 0.5 - dz / length * 0.4
        else:
            self.attacked_at_yaw = random.choice((0.0, 180.0))
            self.vel[0] *= 0.5
            self.vel[1] = min(0.4, self.vel[1] * 0.5 + 0.4)
            self.vel[2] *= 0.5
        self._play_world_sound(
            "random.hurt", volume=1.0,
            pitch=random.uniform(0.8, 1.2), position=self.pos,
        )
        return True

    def on_fishing_hook_hit(self, attacker=None) -> bool:
        return self.on_zero_damage_projectile_hit(attacker)

    def damage_player(self, amount, source="generic", *, bypass_armor=False,
                      force=False, attacker=None, bypass_gamemode=False):
        amount = max(0, int(amount))
        if amount <= 0 or self.health <= 0:
            return False
        if self.gamemode == 1 and not bypass_gamemode:
            return False
        if self.hearts_life > 10 and not force:
            return False

        difficulty_scaled = (
            source == "arrow"
            or bool(getattr(attacker, "is_hostile", False))
            or source == "monster"
        )
        settings = getattr(self, "settings", None)
        difficulty = getattr(settings, "difficulty", 2)
        amount = scale_incoming_player_damage(
            amount, difficulty, difficulty_scaled=difficulty_scaled,
        )
        if amount <= 0:
            return False

        damage = amount
        if not bypass_armor:
            armor_points = self.inventory.get_total_armor_value()
            reduced = amount * (25 - armor_points) + self._damage_remainder
            damage = reduced // 25
            self._damage_remainder = reduced % 25
            self.inventory.damage_armor(amount)

        self.prev_health = self.health
        self.health = max(0, self.health - damage)
        self.hearts_life = 20
        self.hurt_time = self.max_hurt_time = 10
        self.limb_swing_amount = 1.5
        if attacker is not None and hasattr(attacker, "pos"):
            ax = float(attacker.pos[0]) - float(self.pos[0])
            az = float(attacker.pos[2]) - float(self.pos[2])
            if ax * ax + az * az < 1.0e-4:
                ax = random.uniform(-0.01, 0.01)
                az = random.uniform(-0.01, 0.01)
            self.attacked_at_yaw = math.degrees(math.atan2(az, ax)) - self.yaw
            length = math.sqrt(ax * ax + az * az) or 1.0
            self.vel[0] = self.vel[0] * 0.5 - ax / length * 0.4
            self.vel[1] = min(0.4, self.vel[1] * 0.5 + 0.4)
            self.vel[2] = self.vel[2] * 0.5 - az / length * 0.4
        else:
            self.attacked_at_yaw = random.choice((0.0, 180.0))
        self._play_world_sound(
            "random.hurt", volume=1.0,
            pitch=random.uniform(0.8, 1.2), position=self.pos,
        )
        if self.health <= 0 and not self._death_handled:
            self._death_handled = True
            self._begin_death_pose()
            self.stop_digging()
            self.inventory.drop_all_items()
            if self.skin_username.casefold() == "notch":
                self.drop_player_item(ItemStack("apple", 1))
            callback = self.on_death_callback
            if callback is not None:
                callback()
        return True

    def _spawn_death_particles(self):
        from game.entities.particles import spawn_living_death_particles
        return spawn_living_death_particles(Entity.world, self.pos, self.size)

    def _advance_death_time(self):
        self.death_time += 1
        if self.death_time > 20 and not self._death_particles_spawned:
            self._death_particles_spawned = True
            self._spawn_death_particles()

    def _begin_death_pose(self):
        if self._death_pose_active:
            return
        old_eye_height = float(self.EYE_HEIGHT)
        death_eye_height = 0.12
        self.size = (0.2, 0.2, 0.2)
        self.pos[1] += old_eye_height - death_eye_height
        self.vel = [0.0, 0.1, 0.0]
        self.y_size = self.render_y_offset = 0.0
        self.on_ground = False
        self.horizontal_collision = self.vertical_collision = False
        self._blocked_down = False
        self._death_pose_active = True
        self.death_time = 0
        self._death_tick_accum = self._world_tick_phase()

    @staticmethod
    def _world_tick_phase():
        try:
            phase = float(getattr(Entity.world, "_tick_accum", 0.0))
        except (TypeError, ValueError):
            phase = 0.0
        return min(max(phase, 0.0), 0.25)

    def respawn_at(self, pos):
        mount = getattr(self, "riding_entity", None)
        if mount is not None:
            mount.dismount()
        self.riding_entity = None
        self.pos = [float(pos[0]), float(pos[1]), float(pos[2])]
        self.vel = [0.0, 0.0, 0.0]
        self.size = (0.6, 1.8, 0.6)
        self.yaw = self.pitch = 0.0
        self.health = self.prev_health = 20
        self.hearts_life = self.hurt_time = 0
        self.max_hurt_time = 10
        self.attacked_at_yaw = 0.0
        self.death_time = 0
        self._death_tick_accum = 0.0
        self._death_pose_active = False
        self.air = self.max_air
        self._survival_tick_accum = 0.0
        self._fire_ticks = -20
        self._damage_remainder = 0
        self._death_handled = False
        self._death_particles_spawned = False
        self._fall_distance = 0.0
        self._fall_pending = 0.0
        self._fall_tick_clock = 0.0
        self._distance_walked = 0.0
        self.on_ground = False
        self.horizontal_collision = self.vertical_collision = False
        self._blocked_down = False
        self.on_ladder = False
        self.ladder_contact_normal = (0.0, 0.0, 0.0)
        self.water_flow = (0.0, 0.0, 0.0)
        self.in_water = self.body_in_water = self.in_lava = self.submerged = False
        self.water_surface_y = None
        self.y_size = self.render_y_offset = 0.0
        self.is_sneaking = False
        world_phase = self._world_tick_phase()
        self._anim_accum = world_phase
        self._anim_tick_accum = world_phase
        self._survival_tick_accum = world_phase
        self._limb_distance_accum = 0.0
        self._jump_airborne = False
        self._splash_cooldown = 0.0
        self.distance_walked_modified = self.prev_distance_walked_modified = 0.0
        self.camera_yaw = self.prev_camera_yaw = 0.0
        self.camera_pitch = self.prev_camera_pitch = 0.0
        self.render_yaw_offset = self.prev_render_yaw_offset = 0.0
        self.limb_swing = self.prev_limb_swing = 0.0
        self.limb_swing_amount = self.prev_limb_swing_amount = 0.0
        self.equipped_progress = self.prev_equipped_progress = 0.0
        self._equipped_item = None
        self._digging = False
        self._dig_target = None
        self._destroy_progress = 0.0
        self._dig_sound_counter = self._dig_tick_accum = 0
        self._block_hit_wait = self._held_attack_click_ticks = self._left_click_counter = 0
        self._is_swing_in_progress = False
        self._swing_progress_int = 0
        self.swing_progress = self.prev_swing_progress = 0.0
        self.jump_requested = self.climb_requested = False
        self._splash_initialized = False
        self._water_ambient_timer = random.uniform(3.0, 6.0)
        self._fire_ambient_timer = random.uniform(2.0, 5.0)
        self._distance_walked = 0.0
        self.sync_camera()

    def _is_touching_fire(self):
        world = Entity.world
        if world is None:
            return False
        for bx, by, bz in self._block_positions_for_aabb(*self.pos):
            if world.get_block_type(bx, by, bz) == "fire":
                return True
        return False

    def _inside_opaque_block(self):
        world = Entity.world
        if world is None:
            return False
        from game.entities import player_model
        return player_model.inside_opaque_block_texture(self, world) is not None

    def _tick_survival(self, dt):
        self._survival_tick_accum += min(max(float(dt), 0.0), 0.25)
        ticks = 0
        while self._survival_tick_accum >= 0.05 and ticks < 4:
            self._survival_tick_accum -= 0.05
            ticks += 1

            if self.gamemode != 1 and self.health > 0 and self.pos[1] < -64.0:
                self.damage_player(4, source="void")

            self.hearts_life = max(0, self.hearts_life - 1)
            self.hurt_time = max(0, self.hurt_time - 1)
            if self.gamemode == 1:
                self.air = self.max_air
                continue
            if self.health <= 0:
                continue

            if self.submerged:
                self.air -= 1
                if self.air <= -20:
                    self.air = 0
                    self.damage_player(2, source="drowning")
            else:
                self.air = self.max_air

            touching_fire = self._is_touching_fire()
            if self.in_water:
                self._fire_ticks = 0
            elif self.in_lava:
                self._fire_ticks = 600
                self.damage_player(1, source="lava")
            else:
                if self._fire_ticks > 0 and self._fire_ticks % 20 == 0:
                    self.damage_player(1, source="fire")
                if touching_fire:
                    self.damage_player(1, source="fire")
                    if self._fire_ticks < 0:
                        self._fire_ticks += 1
                        if self._fire_ticks == 0:
                            self._fire_ticks = 300
                    elif self._fire_ticks == 0:
                        self._fire_ticks = 300
                elif self._fire_ticks > 0:
                    self._fire_ticks -= 1
                else:
                    self._fire_ticks = -20

            if self._inside_opaque_block():
                self.damage_player(1, source="suffocation")

    def sync_camera(self):
        self.renderer.camX = self.pos[0]
        eye_height = 0.12 if self.health <= 0 and self._death_pose_active else self.EYE_HEIGHT
        self.renderer.camY = self.pos[1] + eye_height - self.render_y_offset
        self.renderer.camZ = self.pos[2]
        self.renderer.yaw = self.yaw
        self.renderer.pitch = self.pitch

    def handle_keys(self, keys, dt):
        ticks = min(dt, 0.1) * TICKS_PER_SECOND
        yaw_rad = math.radians(self.yaw)

        forward = (-math.sin(yaw_rad), 0.0, -math.cos(yaw_rad))
        right = (math.cos(yaw_rad), 0.0, -math.sin(yaw_rad))

        forward_amount = (
            float(bool(keys[self._key('forward', pg.K_w)]))
            - float(bool(keys[self._key('back', pg.K_s)]))
        )
        strafe_amount = (
            float(bool(keys[self._key('right', pg.K_d)]))
            - float(bool(keys[self._key('left', pg.K_a)]))
        )
        analog_axes = getattr(keys, "movement_axes", None)
        if analog_axes is not None:
            try:
                analog_strafe, analog_forward = analog_axes
                strafe_amount += max(-1.0, min(1.0, float(analog_strafe)))
                forward_amount += max(-1.0, min(1.0, float(analog_forward)))
            except (TypeError, ValueError):
                pass
        forward_amount = max(-1.0, min(1.0, forward_amount))
        strafe_amount = max(-1.0, min(1.0, strafe_amount))
        mx = forward[0] * forward_amount + right[0] * strafe_amount
        mz = forward[2] * forward_amount + right[2] * strafe_amount
        jump_down = bool(keys[self._key('jump', pg.K_SPACE)])
        sneak_down = bool(keys[self._key('sneak', pg.K_LSHIFT)])
        self.is_sneaking = sneak_down

        length = math.sqrt(mx * mx + mz * mz)
        if length > 1.0:
            mx /= length
            mz /= length

        if sneak_down:
            mx *= 0.3
            mz *= 0.3

        self.jump_requested = bool(jump_down)

        self._check_water()
        self._apply_water_current(ticks)

        if self.in_water or self.in_lava:
            accel = self.WATER_ACCEL
            if jump_down:
                self.vel[1] += self.WATER_SWIM_ACCEL * ticks
        elif self.on_ladder:
            accel = self.GROUND_ACCEL if self.on_ground else self.AIR_ACCEL
            if jump_down and self.on_ground:
                self.vel[1] = self.JUMP_MOTION
                self._jump_airborne = True
                self.on_ground = False
        elif self.on_ground:
            friction = self._ground_slipperiness() * self.DRAG_XZ
            friction = max(friction, 1e-4)
            accel = self.GROUND_ACCEL * (0.16277136 / (friction * friction * friction))
            if jump_down:
                self.vel[1] = self.JUMP_MOTION
                self._jump_airborne = True
                self.on_ground = False
        else:
            accel = self.AIR_ACCEL

        self._heading_accel = (mx * accel, mz * accel)
        self.vel[0] += self._heading_accel[0] * ticks
        self.vel[2] += self._heading_accel[1] * ticks

    def start_using_item(self):
        self._using_item = True
        self._held_use_tick_accum = 0.0
        self._held_use_cooldown = 8

    def stop_using_item(self):
        self._using_item = False
        self._held_use_tick_accum = 0.0
        self._held_use_cooldown = 0

    def update_held_use(self, dt):
        if not self._using_item or self.health <= 0:
            return
        self._held_use_tick_accum += min(max(float(dt), 0.0), 0.25)
        ticks = 0
        while self._held_use_tick_accum >= 0.05 and ticks < 4:
            self._held_use_tick_accum -= 0.05
            ticks += 1
            if self._held_use_cooldown > 0:
                self._held_use_cooldown -= 1
            if self._held_use_cooldown > 0:
                continue
            selected = self.inventory.get_active_item()
            if selected is None:
                self.stop_using_item()
                return
            item_id = str(getattr(selected, "id", ""))
            is_placeable = Item.placed_block(item_id) is not None
            is_repeatable_item = item_id in {"snowball", "egg", "fishing_rod"}
            if not (is_placeable or is_repeatable_item):
                self.stop_using_item()
                return

            self._held_use_cooldown = 8
            if not self.place_block():
                self.stop_using_item()
                return
            if item_id != "fishing_rod":
                self.swing_item()
            selected = self.inventory.get_active_item()
            if selected is None:
                self.stop_using_item()
                return
            selected_id = str(getattr(selected, "id", ""))
            if (Item.placed_block(selected_id) is None
                    and selected_id not in {"snowball", "egg", "fishing_rod"}):
                self.stop_using_item()
                return

    def handle_mouse(self, dx, dy, sensitivity=0.15):
        self.prev_yaw = self.yaw
        self.prev_pitch = self.pitch
        self.yaw -= dx * sensitivity
        self.pitch = max(-90.0, min(90.0, self.pitch - dy * sensitivity))

    def handle_menus(self):
        pass

    @staticmethod
    def _ray_aabb(ox, oy, oz, rdx, rdy, rdz, minx, miny, minz, maxx, maxy, maxz):
        tmin, tmax = -1e30, 1e30
        normal = (0, 0, 0)

        for origin, dir_, mn, mx, axis in (
            (ox, rdx, minx, maxx, 0),
            (oy, rdy, miny, maxy, 1),
            (oz, rdz, minz, maxz, 2),
        ):
            if abs(dir_) < 1e-12:
                if origin < mn or origin > mx:
                    return None
                continue
            inv = 1.0 / dir_
            if inv >= 0.0:
                t_near = (mn - origin) * inv
                t_far = (mx - origin) * inv
                n_near = -1
            else:
                t_near = (mx - origin) * inv
                t_far = (mn - origin) * inv
                n_near = 1
            if t_near > tmin:
                tmin = t_near
                n = [0, 0, 0]
                n[axis] = n_near
                normal = tuple(n)
            if t_far < tmax:
                tmax = t_far
            if tmin > tmax:
                return None
        if tmax < 0:
            return None
        t_hit = tmin if tmin >= 0 else 0.0
        if tmin < 0:
            normal = (0, 0, 0)
        return t_hit, normal

    def _raycast(
        self, *, include_intangible=False, return_distance=False,
        stop_on_source_liquid=False, yaw=None, pitch=None,
        source_liquid_only=False,
    ):
        hit, normal, dist = self._raycast_blocks(
            include_intangible=include_intangible, return_distance=True,
            stop_on_source_liquid=stop_on_source_liquid, yaw=yaw, pitch=pitch,
            source_liquid_only=source_liquid_only,
        )
        if not stop_on_source_liquid:
            limit = dist if hit is not None else self.REACH
            if self._entity_intercepts_ray(limit, yaw, pitch):
                hit, normal, dist = None, None, None
        return (hit, normal, dist) if return_distance else (hit, normal)

    def _entity_intercepts_ray(self, max_distance, yaw=None, pitch=None):
        return self._raycast_entity(max_distance, yaw=yaw, pitch=pitch) is not None

    def _raycast_attack_entity(self, max_distance=None, yaw=None, pitch=None):
        reach = self.REACH if max_distance is None else min(float(max_distance), self.REACH)
        block_hit, _normal, block_distance = self._raycast_blocks(
            include_intangible=True, return_distance=True, yaw=yaw, pitch=pitch,
        )
        limit = min(reach, block_distance) if block_hit is not None else reach
        return self._raycast_entity(limit, yaw=yaw, pitch=pitch)

    def _raycast_entity(self, max_distance, yaw=None, pitch=None):
        world = self.world
        entities = list(getattr(world, "entities", ()) or ())
        remote_players = list(getattr(world, "_mp_remote_players", ()) or ())
        if not entities and not remote_players:
            return None
        yaw_rad = math.radians(float(self.yaw if yaw is None else yaw))
        pitch_rad = math.radians(float(self.pitch if pitch is None else pitch))
        rdx = -math.sin(yaw_rad) * math.cos(pitch_rad)
        rdy = math.sin(pitch_rad)
        rdz = -math.cos(yaw_rad) * math.cos(pitch_rad)
        ox, oy, oz = self.pos[0], self.pos[1] + self.EYE_HEIGHT, self.pos[2]
        best_t = max_distance
        best = None
        candidates = list(entities)
        for remote_player in getattr(world, "_mp_remote_players", ()) or ():
            if not any(candidate is remote_player for candidate in candidates):
                candidates.append(remote_player)
        for entity in candidates:
            can_collide = getattr(entity, "can_be_collided_with", None)
            if entity is self or getattr(entity, "dead", False) or not callable(can_collide):
                continue
            if not can_collide():
                continue
            ex, ey, ez = entity.pos
            border = float(getattr(entity, "collision_border_size", 0.1))
            get_bounds = getattr(entity, "get_interaction_bounds", None)
            if callable(get_bounds):
                minx, miny, minz, maxx, maxy, maxz = get_bounds()
            else:
                w, h, d = entity.size
                minx, miny, minz = ex - w * 0.5, ey, ez - d * 0.5
                maxx, maxy, maxz = ex + w * 0.5, ey + h, ez + d * 0.5
            hit = self._ray_aabb(
                ox, oy, oz, rdx, rdy, rdz,
                minx - border, miny - border, minz - border,
                maxx + border, maxy + border, maxz + border,
            )
            if hit is not None and 0.0 <= hit[0] < best_t:
                best_t = hit[0]
                best = entity
        return best

    def _raycast_blocks(
        self, *, include_intangible=False, return_distance=False,
        stop_on_source_liquid=False, yaw=None, pitch=None,
        source_liquid_only=False,
    ):
        yaw_rad = math.radians(float(self.yaw if yaw is None else yaw))
        pitch_rad = math.radians(float(self.pitch if pitch is None else pitch))

        rdx = -math.sin(yaw_rad) * math.cos(pitch_rad)
        rdy = math.sin(pitch_rad)
        rdz = -math.cos(yaw_rad) * math.cos(pitch_rad)

        ox = self.pos[0]
        oy = self.pos[1] + self.EYE_HEIGHT
        oz = self.pos[2]

        bx = math.floor(ox + 0.5)
        by = math.floor(oy + 0.5)
        bz = math.floor(oz + 0.5)

        sx = 1 if rdx > 0 else -1
        sy = 1 if rdy > 0 else -1
        sz = 1 if rdz > 0 else -1

        inv_dx = abs(1.0 / rdx) if rdx != 0 else float("inf")
        inv_dy = abs(1.0 / rdy) if rdy != 0 else float("inf")
        inv_dz = abs(1.0 / rdz) if rdz != 0 else float("inf")

        tx = ((bx + (0.5 if sx > 0 else -0.5)) - ox) / rdx if rdx != 0 else float("inf")
        ty = ((by + (0.5 if sy > 0 else -0.5)) - oy) / rdy if rdy != 0 else float("inf")
        tz = ((bz + (0.5 if sz > 0 else -0.5)) - oz) / rdz if rdz != 0 else float("inf")

        cell_normal = (0, 0, 0)
        max_steps = int(self.REACH / 0.05) + 1

        for _ in range(max_steps):
            best = None
            for dy in (0, -1, 1):
                cx, cy, cz = bx, by + dy, bz
                block = self.world.get_block(cx, cy, cz)
                if block is None:
                    continue
                if block.block_type in ("water", "lava", "flowing_water", "flowing_lava"):
                    if dy != 0:
                        continue
                    liquid_meta = self.world.get_metadata(cx, cy, cz)
                    if stop_on_source_liquid and (
                        liquid_meta == 0
                        or (not source_liquid_only
                            and block.block_type in ("water", "flowing_water"))
                    ):
                        liquid_hit = self._ray_aabb(
                            ox, oy, oz, rdx, rdy, rdz,
                            cx - 0.5, cy - 0.5, cz - 0.5,
                            cx + 0.5, cy + 0.5, cz + 0.5,
                        )
                        if liquid_hit is not None and liquid_hit[0] <= self.REACH:
                            liquid_normal = liquid_hit[1] if liquid_hit[1] != (0, 0, 0) else cell_normal
                            if return_distance:
                                return (cx, cy, cz), liquid_normal, liquid_hit[0]
                            return (cx, cy, cz), liquid_normal
                    continue
                if not (include_intangible or not getattr(block, "intangible", False)):
                    continue
                selection_boxes = (
                    block.get_selection_boxes()
                    if hasattr(block, "get_selection_boxes")
                    else [block.get_selection_bounds()]
                )
                for sb in selection_boxes:
                    if sb is None:
                        continue
                    hit = self._ray_aabb(
                        ox, oy, oz, rdx, rdy, rdz,
                        cx + sb[0], cy + sb[1], cz + sb[2],
                        cx + sb[3], cy + sb[4], cz + sb[5],
                    )
                    if hit is not None and (best is None or hit[0] < best[0]):
                        t_hit, aabb_normal = hit
                        normal = aabb_normal if aabb_normal != (0, 0, 0) else cell_normal
                        best = (t_hit, normal, cx, cy, cz)
            if best is not None and best[0] <= self.REACH:
                t_hit, normal, cx, cy, cz = best
                if return_distance:
                    return (cx, cy, cz), normal, t_hit
                return (cx, cy, cz), normal

            if tx < ty and tx < tz:
                bx += sx
                cell_normal = (-sx, 0, 0)
                if tx > self.REACH:
                    break
                tx += inv_dx
            elif ty < tz:
                by += sy
                cell_normal = (0, -sy, 0)
                if ty > self.REACH:
                    break
                ty += inv_dy
            else:
                bz += sz
                cell_normal = (0, 0, -sz)
                if tz > self.REACH:
                    break
                tz += inv_dz

        return (None, None, None) if return_distance else (None, None)

    def get_targeted_block(self):
        hit, _ = self._raycast(include_intangible=True)
        return hit

    def _play_block_sound(self, block_type, position, *, breaking=False, digging=False):
        if not self.sound_player:
            return
        if block_type == "fire":
            if not breaking:
                return
            self.sound_player(
                "random.fizz", volume=0.5,
                pitch=2.6 + (random.random() - random.random()) * 0.8,
                position=position,
            )
            return
        material = material_for_block(block_type)
        step_sound = step_sound_for_block(block_type)
        if breaking and block_type in ("glass", "ice"):
            sound_name = "random.glass"
        else:
            step_name = "gravel" if breaking and block_type == "sand" else step_sound.name
            sound_name = f"step.{step_name}"
        if digging:
            volume = (step_sound.volume + 1.0) / 8.0
            pitch = step_sound.pitch * 0.5
        else:
            volume = (step_sound.volume + 1.0) / 2.0
            pitch = step_sound.pitch * 0.8
        self.sound_player(sound_name, volume=volume, pitch=pitch, position=position)


    def swing_item(self):
        self._swing_progress_int = -1
        self._is_swing_in_progress = True
        world = getattr(self, "world", None)
        callback = getattr(world, "_network_animation_callback", None)
        if callable(callback):
            try:
                callback("swing")
            except Exception:
                pass

    def _finish_interactable_action(self, selected):
        if getattr(selected, "id", None) in {"bow", "fishing_rod"}:
            self.swing_item()
        return True

    def _update_arm_swing(self):
        self.prev_swing_progress = self.swing_progress
        if self._is_swing_in_progress:
            self._swing_progress_int += 1
            if self._swing_progress_int >= 8:
                self._swing_progress_int = 0
                self._is_swing_in_progress = False
        else:
            self._swing_progress_int = 0
        self.swing_progress = self._swing_progress_int / 8.0

    def _update_limb_swing(self, dx, dz, dt):
        self._anim_tick_accum = getattr(self, "_anim_tick_accum", 0.0) + min(max(dt, 0.0), 0.25)
        self._limb_distance_accum += math.hypot(float(dx), float(dz))
        tick_dt = 1.0 / 20.0
        ticks = min(int(self._anim_tick_accum / tick_dt), 4)
        if ticks <= 0:
            return

        distance_per_tick = self._limb_distance_accum / ticks
        self._limb_distance_accum = 0.0
        for _ in range(ticks):
            self._anim_tick_accum -= tick_dt
            self._limb_swing_tick(distance_per_tick, 0.0)

    def _limb_swing_tick(self, dx, dz):
        if self._left_click_counter > 0:
            self._left_click_counter -= 1
        self.prev_limb_swing_amount = self.limb_swing_amount
        self.prev_limb_swing = self.limb_swing
        self.prev_render_yaw_offset = self.render_yaw_offset
        self.prev_distance_walked_modified = self.distance_walked_modified
        self.prev_camera_yaw = self.camera_yaw
        self.prev_camera_pitch = self.camera_pitch

        vx = float(self.vel[0])
        vy = float(self.vel[1])
        vz = float(self.vel[2])
        horizontal_speed = math.sqrt(vx * vx + vz * vz)
        travelled = math.hypot(float(dx), float(dz))
        self.distance_walked_modified += travelled * 0.6
        target_camera_yaw = min(0.1, horizontal_speed) if self.on_ground else 0.0
        target_camera_pitch = math.atan(-vy * 0.2) * 15.0 if not self.on_ground else 0.0
        self.camera_yaw += (target_camera_yaw - self.camera_yaw) * 0.4
        self.camera_pitch += (target_camera_pitch - self.camera_pitch) * 0.8
        if getattr(self, "riding_entity", None) is not None:
            self.prev_camera_yaw = self.camera_yaw
            self.camera_yaw = 0.0
            dist = 0.0
        else:
            dist = travelled * 4.0
            if dist > 1.0:
                dist = 1.0
        self.limb_swing_amount += (dist - self.limb_swing_amount) * 0.4
        self.limb_swing += self.limb_swing_amount

        speed = math.sqrt(vx * vx + vz * vz)
        move_yaw = self.render_yaw_offset
        if speed > 0.05:
            move_yaw = math.degrees(math.atan2(-vx, -vz))

        move_delta = (move_yaw - self.render_yaw_offset + 180.0) % 360.0 - 180.0
        self.render_yaw_offset += move_delta * 0.1

        look_delta = (self.yaw - self.render_yaw_offset + 180.0) % 360.0 - 180.0
        look_delta = max(-75.0, min(75.0, look_delta))
        if speed > 0.05:
            self.render_yaw_offset = self.yaw - look_delta
            self.render_yaw_offset += look_delta * 0.1
        else:
            self.render_yaw_offset = self.yaw - look_delta

        while self.render_yaw_offset - self.prev_render_yaw_offset < -180.0:
            self.prev_render_yaw_offset -= 360.0
        while self.render_yaw_offset - self.prev_render_yaw_offset >= 180.0:
            self.prev_render_yaw_offset += 360.0

        self.ticks_existed += 1
        self._update_arm_swing()
        self._apply_entity_collisions()

        self.prev_equipped_progress = self.equipped_progress
        selected_item = self.inventory.get_active_item()
        target_progress = 1.0 if selected_item is self._equipped_item else 0.0
        delta = max(-0.4, min(0.4, target_progress - self.equipped_progress))
        self.equipped_progress += delta
        if self.equipped_progress < 0.1:
            self._equipped_item = selected_item

    def _attack_entity_with_current_item(self, entity):
        attack = getattr(entity, "attack_entity_from", None)
        if not callable(attack):
            attack = getattr(entity, "attackEntityFrom", None)
        if not callable(attack):
            return False

        held = self.inventory.get_active_item()
        damage = self.inventory.get_damage_vs_entity(entity)
        if damage <= 0:
            return False

        if (bool(getattr(self.world, "_multiplayer_client_world", False))
                and bool(getattr(entity, "_mp_remote_player", False))):
            request_attack = getattr(self.world, "_network_player_attack_callback", None)
            if not callable(request_attack) or not request_attack(entity, damage):
                return False
        elif (bool(getattr(self.world, "_multiplayer_client_world", False))
                and bool(getattr(entity, "_mp_remote_entity", False))):
            request_attack = getattr(self.world, "_network_attack_callback", None)
            if not callable(request_attack) or not request_attack(entity, damage):
                return False
        else:
            attack(self, damage)

        if held is not None and bool(getattr(entity, "is_living_entity", False)):
            wear = item_hit_entity_wear(Item.definition(held.id))
            if wear > 0:
                held.damage_item(wear)
                self.inventory.clear_if_empty(self.inventory.current_item)
        return True

    def _eat_cake_at(self, x, y, z):
        if self.health <= 0 or self.health >= 20:
            return False
        if bool(getattr(self.world, "_multiplayer_client_world", False)):
            callback = getattr(self.world, "_network_item_use_callback", None)
            if not callable(callback) or not callback("cake_eat", int(x), int(y), int(z)):
                return False
            self.prev_health = self.health
            self.health = min(20, self.health + 3)
            return True
        from game.block.cake import eat_cake_slice
        return eat_cake_slice(self.world, self, x, y, z)

    @_network_edit_action
    def start_digging(self):
        self._digging = True
        self._dig_target = None
        self._destroy_progress = 0.0
        self._dig_sound_counter = 0
        self._dig_tick_accum = 0.0
        self._block_hit_wait = 0
        self._held_attack_click_ticks = 0
        if self._left_click_counter > 0:
            return
        self.swing_item()
        entity = self._raycast_attack_entity()
        if entity is not None:
            self._attack_entity_with_current_item(entity)
            return
        hit, _ = self._raycast(include_intangible=True)
        if hit and self.world.get_block_type(*hit) == "note_block":
            network_note = getattr(self.world, "_network_note_block_callback", None)
            if (bool(getattr(self.world, "_multiplayer_client_world", False))
                    and callable(network_note)):
                network_note("play", *hit)
            else:
                tile_entity = self.world.get_tile_entity(*hit)
                if tile_entity is not None:
                    tile_entity.trigger_note()

        if (hit and self.world.get_block_type(*hit) == "cake"
                and self.gamemode != 1):
            if self._eat_cake_at(*hit):
                self._digging = False
                self._dig_target = None
                return
        if not hit and self.gamemode != 1:
            self._left_click_counter = 10

        if hit and self.world.get_block_type(*hit) == "lever":
            self._dig_target = hit
            redstone = getattr(self.world, "_redstone_system", None)
            if redstone is not None:
                redstone.toggle_lever(*hit)
        if hit and self.world.get_block_type(*hit) == "stone_button":
            self._dig_target = hit
            redstone = getattr(self.world, "_redstone_system", None)
            if redstone is not None:
                redstone.press_button(*hit)

        if hit and self.world.get_block_type(*hit) == "wooden_door":
            self._toggle_door_at(*hit)
        elif hit and self.world.get_block_type(*hit) in ("redstone_ore", "redstone_ore_lit"):
            from game.block.redstone_ore import activate_redstone_ore
            activate_redstone_ore(self.world, *hit)

        if self.gamemode == 1:
            if hit:
                self.break_block(hit)
            return
        if hit:
            block_type = self.world.get_block_type(*hit)
            if block_type and self._block_strength(block_type) >= 1.0:
                self.break_block()

    def stop_digging(self):
        self._digging = False
        self._dig_target = None
        self._destroy_progress = 0.0
        self._dig_sound_counter = 0
        self._dig_tick_accum = 0.0
        self._block_hit_wait = 0
        self._held_attack_click_ticks = 0

    def _str_vs_block(self, block_type, held):
        strength = 1.0
        if held is None:
            return strength
        tool = Item.item_defs.get(held.id)
        if not tool:
            return strength
        if tool.get("type") == "melee_weapon":
            return 1.5
        if tool.get("type") != "tool":
            return strength
        definition = Block._block_defs.get(block_type, {})
        if tool.get("tooltype") != definition.get("tool"):
            return strength
        if not definition.get("toolSpeed", True):
            return strength
        material = tool.get("material", "wood")
        tiers = {"wood": 0, "stone": 1, "iron": 2, "diamond": 3, "gold": 0}
        return float((tiers.get(material, 0) + 1) * 2)

    def _block_strength(self, block_type):
        Block._load_defs()
        definition = Block._block_defs.get(block_type, {})
        if not definition.get("player_breakable", True):
            return 0.0
        hardness = float(definition.get("hardness", 1.0))
        if hardness < 0:
            return 0.0
        if hardness == 0:
            return 1.0
        held = self.inventory.get_active_item()
        if block_type == "snow" and held is not None:
            tool = Item.item_defs.get(held.id, {})
            if tool.get("type") == "tool" and tool.get("tooltype") == "shovel" and tool.get("material") != "wood":
                return 1.0
        if not can_harvest(block_type, held):
            return 1.0 / hardness / 100.0
        strength = self._str_vs_block(block_type, held)
        if self.submerged:
            strength /= 5.0
        if not self._blocked_down:
            strength /= 5.0
        return strength / hardness / 30.0

    def update_digging(self, dt):
        if not self._digging:
            return

        self._dig_tick_accum += min(max(dt, 0.0), 0.25)
        tick_dt = 1.0 / TICKS_PER_SECOND
        ticks = 0
        while self._dig_tick_accum >= tick_dt and ticks < 4:
            self._dig_tick_accum -= tick_dt
            self._dig_tick()
            ticks += 1

    def _tick_held_attack_click(self):
        self._held_attack_click_ticks += 1
        if self._held_attack_click_ticks < 5:
            return
        self._held_attack_click_ticks -= 5

        if self._left_click_counter > 0:
            return

        self.swing_item()
        entity = self._raycast_attack_entity()
        if entity is not None:
            self._attack_entity_with_current_item(entity)
            return

        hit, _ = self._raycast(include_intangible=True)
        if not hit:
            if self.gamemode != 1:
                self._left_click_counter = 10
            return

        if self.gamemode == 1:
            self.break_block()

    def _dig_tick(self):
        self._tick_held_attack_click()
        if self._left_click_counter > 0 or self.gamemode == 1:
            return

        hit, _ = self._raycast(include_intangible=True)
        if not hit:
            candidate = self._dig_target
            if (candidate is not None
                    and self.world.get_block_type(*candidate) in ("stone_button", "lever")
                    and (self.world.get_block_type(*candidate) == "lever"
                         or (int(self.world.get_metadata(*candidate)) & 8))):
                hit = candidate
            else:
                self._dig_target = None
                self._destroy_progress = 0.0
                self._dig_sound_counter = 0
                if self._block_hit_wait > 0:
                    self._block_hit_wait -= 1
                return

        if self._block_hit_wait > 0:
            self._block_hit_wait -= 1
            return

        block_type = self.world.get_block_type(*hit)
        if block_type is None:
            self._dig_target = None
            self._destroy_progress = 0.0
            self._dig_sound_counter = 0
            return

        if self._dig_target != hit:
            self._dig_target = hit
            self._destroy_progress = 0.0
            self._dig_sound_counter = 0

        strength = self._block_strength(block_type)
        if strength <= 0:
            self._destroy_progress = 0.0
            self._dig_sound_counter = 0
            return

        self._destroy_progress += strength
        if self._dig_sound_counter % 4 == 0:
            if block_type != "fire":
                self._play_block_sound(
                    block_type, tuple(float(v) for v in hit), digging=True
                )
                _, normal = self._raycast(include_intangible=True)
                face = self._normal_to_face(normal)
                if getattr(self.world, "effects", None) is not None:
                    hit_meta = self.world.get_metadata(*hit)
                    self.world.effects.add_block_hit_effects(
                        self.world, hit[0], hit[1], hit[2], face, block_type,
                        meta=hit_meta,
                    )
        self._dig_sound_counter += 1

        if self._destroy_progress >= 1.0:
            self.break_block(hit)
            self._destroy_progress = 0.0
            self._dig_target = None
            self._dig_sound_counter = 0
            self._block_hit_wait = 5

    @staticmethod
    def _normal_to_face(normal):
        if not normal:
            return 1
        nx, ny, nz = normal
        if abs(ny) >= abs(nx) and abs(ny) >= abs(nz):
            return 0 if ny < 0 else 1
        if abs(nz) >= abs(nx):
            return 2 if nz < 0 else 3
        return 4 if nx < 0 else 5

    def _record_performance_event(self, event, started_at=None, **fields):
        profiler = getattr(getattr(self, "world", None), "profiler", None)
        if profiler is None or not hasattr(profiler, "record_event"):
            return
        if started_at is not None:
            fields["duration_ms"] = round((time.perf_counter() - started_at) * 1000.0, 3)
        profiler.record_event(event, **fields)

    @_network_edit_action
    def break_block(self, target=None):
        operation_started = time.perf_counter()
        if target is None:
            hit, _ = self._raycast(include_intangible=True)
        else:
            hit = tuple(target)
        if not hit:
            return
        block_type = self.world.get_block_type(*hit)
        if block_type is None:
            return
        creative = self.gamemode == 1
        definition = Block._block_defs.get(block_type, {})
        if not definition.get("player_breakable", True):
            return
        if not creative and definition.get("hardness", 0) < 0:
            return

        meta = self.world.get_metadata(*hit)
        drop_pos, drop_meta = hit, meta
        if block_type == "tnt":
            from game.entities.tnt import ignite_tnt
            ignite_tnt(self.world, hit[0], hit[1], hit[2])
        if block_type != "fire" and getattr(self.world, "effects", None) is not None:
            self.world.effects.add_block_destroy_effects(
                self.world, hit[0], hit[1], hit[2], block_type, meta=meta
            )
        if block_type in ("wooden_door", "iron_door"):
            base_y = hit[1] - 1 if (meta & 8) else hit[1]
            top_y = base_y + 1
            targets = [
                (hit[0], by, hit[2])
                for by in (base_y, top_y)
                if self.world.get_block_type(hit[0], by, hit[2]) == block_type
            ]
            drop_pos = (hit[0], base_y, hit[2])
            drop_meta = self.world.get_metadata(*drop_pos) & 7
            for index, target in enumerate(targets):
                self.world.remove_block(*target, immediate_mesh=index == len(targets) - 1)
        else:
            self.world.remove_block(*hit, immediate_mesh=True)
        self._play_block_sound(block_type, tuple(float(v) for v in hit), breaking=True)

        if not creative:
            held = self.inventory.get_active_item()
            if can_harvest(block_type, held):
                self._spawn_block_drops(block_type, drop_meta, drop_pos, held=held)
            self._wear_held_item(held)
        self._record_performance_event(
            "block.break", started_at=operation_started,
            block_type=block_type, x=int(hit[0]), y=int(hit[1]), z=int(hit[2]),
            meta=int(meta), creative=bool(creative),
        )

    def _spawn_block_drops(self, block_type, meta, block_pos, held=None):
        for stack in block_drops(block_type, meta, random, held=held):
            self.world.spawn_entity(ItemEntity(
                stack, scatter_position(block_pos, random),
                pickup_delay=self.BREAK_PICKUP_DELAY,
            ))

    def _wear_held_item(self, held):
        amount = wear_from_breaking(held)
        if amount:
            held.damage_item(amount)
            self.inventory.clear_if_empty(self.inventory.current_item)

    def drop_current_item(self):
        slot = int(self.inventory.current_item)
        self.drop_player_item(
            self.inventory.decr_stack_size(slot, 1),
            inventory_slot=slot,
        )

    def drop_player_item(self, stack, random_choice=False, inventory_slot=None):
        if stack is None or self.world is None:
            return
        pos = (self.pos[0], self.pos[1] + self.EYE_HEIGHT - 0.3 - 0.125, self.pos[2])
        if random_choice:
            speed = random.random() * 0.5
            angle = random.random() * math.pi * 2.0
            vel = (-math.sin(angle) * speed, 0.2, math.cos(angle) * speed)
        else:
            yaw, pitch = math.radians(self.yaw), math.radians(self.pitch)
            power = 0.3
            vx = -math.sin(yaw) * math.cos(pitch) * power
            vz = -math.cos(yaw) * math.cos(pitch) * power
            vy = math.sin(pitch) * power + 0.1
            scatter = random.random() * math.pi * 2.0
            jitter = 0.02 * random.random()
            vx += math.cos(scatter) * jitter
            vy += (random.random() - random.random()) * 0.1
            vz += math.sin(scatter) * jitter
            vel = (vx, vy, vz)
        entity = ItemEntity(
            stack, pos, velocity=vel, pickup_delay=self.THROW_PICKUP_DELAY,
        )
        if inventory_slot is not None:
            try:
                slot = int(inventory_slot)
                if 0 <= slot < 40:
                    entity._mp_inventory_drop = {"slot": slot, "count": int(stack.count)}
            except (AttributeError, TypeError, ValueError, OverflowError):
                pass
        self.world.spawn_entity(entity)

    def collect_items(self):
        if self.world is None or self.gamemode == 1 or self.health <= 0:
            return
        px, py, pz = self.pos
        reach_x = self._half_width() + 1.0
        reach_z = self._half_depth() + 1.0
        iter_nearby = getattr(self.world, "iter_entities_in_bounds", None)
        if callable(iter_nearby):
            candidates = iter_nearby(
                px - reach_x - 0.5, py - 1.0, pz - reach_z - 0.5,
                px + reach_x + 0.5, py + self.size[1], pz + reach_z + 0.5,
            )
        else:
            candidates = self.world.entities
        for entity in candidates:
            if not isinstance(entity, ItemEntity) or not entity.can_be_picked_up():
                continue
            ex, ey, ez = entity.pos
            half = entity._half_width()
            if (abs(ex - px) >= reach_x + half or abs(ez - pz) >= reach_z + half
                    or ey >= py + self.size[1] or ey + entity.size[1] <= py):
                continue
            pickup_callback = getattr(entity, "_mp_pickup_callback", None)
            if callable(pickup_callback):
                try:
                    pickup_callback(entity)
                except Exception:
                    pass
                continue
            if self.inventory.add_stack(entity.item):
                self._play_world_sound(
                    "random.pop", volume=0.2,
                    pitch=((random.random() - random.random()) * 0.7 + 1.0) * 2.0,
                    position=entity.pos,
                )
                entity.begin_pickup(self)


    def _player_overlaps_placement(self, bx, by, bz, block_type):
        Block._load_defs()
        defn = Block._block_defs.get(block_type, {})
        if defn.get("collision", not defn.get("intangible", False)) is False:
            return False
        bounds = defn.get("bounds", [0.0, 0.0, 0.0, 1.0, 1.0, 1.0])
        local = Block.classic_to_center(bounds)
        aabb = (
            bx + local[0], by + local[1], bz + local[2],
            bx + local[3], by + local[4], bz + local[5],
        )
        hw = self._half_width()
        hd = self._half_depth()
        h = self.size[1]
        px, py, pz = self.pos
        if (px - hw < aabb[3] and px + hw > aabb[0]
                and py < aabb[4] and py + h > aabb[1]
                and pz - hd < aabb[5] and pz + hd > aabb[2]):
            return True
        for entity in getattr(self.world, "entities", ()):
            if (entity is self or getattr(entity, "dead", False)
                    or not getattr(entity, "prevent_entity_spawning", False)):
                continue
            ex, ey, ez = entity.pos
            es = getattr(entity, "size", (0.6, 1.8, 0.6))
            ehw = float(es[0]) * 0.5
            ehd = float(es[2]) * 0.5
            if (ex - ehw < aabb[3] and ex + ehw > aabb[0]
                    and ey < aabb[4] and ey + float(es[1]) > aabb[1]
                    and ez - ehd < aabb[5] and ez + ehd > aabb[2]):
                return True
        return False

    def _use_fishing_rod(self, selected):
        world = self.world
        if world is None or selected is None or selected.id != "fishing_rod":
            return False
        if bool(getattr(world, "_multiplayer_client_world", False)):
            callback = getattr(world, "_network_fishing_callback", None)
            if not callable(callback):
                return False
            hook = getattr(self, "fishing_hook", None)
            if hook is not None and not getattr(hook, "dead", True):
                try:
                    entity_id = int(getattr(hook, "_mp_net_id", 0) or 0)
                except (TypeError, ValueError, OverflowError):
                    entity_id = 0
                if entity_id <= 0:
                    return False
                callback("reel", entity_id)
            else:
                callback("cast", 0)
                self._play_world_sound(
                    "random.bow", volume=0.5,
                    pitch=0.4 / (random.random() * 0.4 + 0.8),
                    position=tuple(self.pos),
                )
            self.swing_item()
            return True

        hook = getattr(self, "fishing_hook", None)
        if hook is not None and not getattr(hook, "dead", True):
            result = hook.reel_in()
            from game.entities.fishing_bobber import apply_reel_durability
            apply_reel_durability(
                selected, self.inventory, self.inventory.active_idx, result,
            )
            self.swing_item()
            return True

        from game.entities.fishing_bobber import FishingBobberEntity
        hook = FishingBobberEntity.from_player(world, self)
        self.fishing_hook = hook
        world.spawn_entity(hook)
        self._play_world_sound(
            "random.bow", volume=0.5,
            pitch=0.4 / (random.random() * 0.4 + 0.8),
            position=tuple(self.pos),
        )
        self.swing_item()
        return True

    def _use_snowball(self, selected):
        if Entity.world is None:
            return False
        if self.gamemode != 1 and not self.inventory.consume_current(1):
            return False
        from game.entities.snowball import SnowballEntity
        snowball = SnowballEntity.from_shooter(Entity.world, self)
        Entity.world.spawn_entity(snowball)
        self._play_world_sound(
            "random.bow", volume=0.5,
            pitch=0.4 / (random.random() * 0.4 + 0.8),
            position=tuple(self.pos),
        )
        return True

    def _use_egg(self, selected):
        if selected is None or selected.id != "egg" or Entity.world is None:
            return False
        if self.gamemode != 1 and not self.inventory.consume_current(1):
            return False
        from game.entities.egg import EggEntity
        egg = EggEntity.from_shooter(Entity.world, self)
        Entity.world.spawn_entity(egg)
        self._play_world_sound(
            "random.bow", volume=0.5,
            pitch=0.4 / (random.random() * 0.4 + 0.8),
            position=tuple(self.pos),
        )
        return True

    def _use_bow(self, selected):
        if Entity.world is None:
            return False
        if not self.inventory.consume_inventory_item("arrow", 1):
            return False
        from game.entities.arrow import ArrowEntity
        self._play_world_sound(
            "random.bow", volume=1.0,
            pitch=1.0 / (random.random() * 0.4 + 0.8),
            position=tuple(self.pos),
        )
        arrow = ArrowEntity.from_shooter(Entity.world, self)
        Entity.world.spawn_entity(arrow)
        return True

    def _use_milk_bucket(self):
        inventory = getattr(self, "inventory", None)
        if inventory is None:
            return False
        held = inventory.get_active_item()
        if held is None or getattr(held, "id", None) != "milk_bucket":
            return False

        world = getattr(self, "world", None)
        network_bucket = getattr(world, "_network_bucket_callback", None)
        if (bool(getattr(world, "_multiplayer_client_world", False))
                and callable(network_bucket)):
            return bool(network_bucket("drink", 0, 0, 0))

        inventory.set_stack(inventory.active_idx, ItemStack("bucket"))
        return True

    def _use_bucket(self, selected):
        operation_started = time.perf_counter()
        item_def = Item.item_defs.get(selected.id, {})
        fluid_type = item_def.get("block")
        active_slot = self.inventory.active_idx

        if fluid_type not in ("water", "lava"):
            hit, _normal = self._raycast(
                include_intangible=True, stop_on_source_liquid=True,
                source_liquid_only=True,
            )
            if hit is None:
                return False
            hit_type = self.world.get_block_type(*hit)
            filled_item = bucket_result_for_source(hit_type, self.world.get_metadata(*hit))
            if filled_item is None:
                return False
            network_bucket = getattr(self.world, "_network_bucket_callback", None)
            if bool(getattr(self.world, "_multiplayer_client_world", False)) and callable(network_bucket):
                return bool(network_bucket("pickup", *hit))
            self.world.set_block(*hit, None, immediate_mesh=True)
            self.inventory.set_stack(active_slot, ItemStack(filled_item))
            self._record_performance_event(
                "bucket.pickup", started_at=operation_started,
                fluid=hit_type, x=int(hit[0]), y=int(hit[1]), z=int(hit[2]),
                result_item=filled_item,
            )
            return True

        hit, normal = self._raycast(include_intangible=True)
        if hit is None or normal is None or normal == (0, 0, 0):
            return False
        target = (hit[0] + normal[0], hit[1] + normal[1], hit[2] + normal[2])
        target_type = self.world.get_block_type(*target)
        if bucket_target_is_solid(target_type):
            return False
        network_bucket = getattr(self.world, "_network_bucket_callback", None)
        if bool(getattr(self.world, "_multiplayer_client_world", False)) and callable(network_bucket):
            return bool(network_bucket("pour", *target))
        if fluid_type == "water" and bool(getattr(self.world, "is_hell_world", False)):
            self._play_world_sound(
                "random.fizz", volume=0.5,
                pitch=2.6 + (random.random() - random.random()) * 0.8,
                position=(float(target[0]), float(target[1]), float(target[2])),
            )
            for _ in range(8):
                self.world.spawn_particle(
                    "largesmoke", target[0] - 0.5 + random.random(),
                    target[1] + random.random(),
                    target[2] - 0.5 + random.random(), 0.0, 0.0, 0.0,
                )
            self.inventory.set_stack(active_slot, ItemStack("bucket"))
            return True
        self.world.set_block(*target, fluid_type, meta=0, immediate_mesh=True)
        self.inventory.set_stack(active_slot, ItemStack("bucket"))
        self._record_performance_event(
            "block.place", started_at=operation_started,
            block_type=fluid_type, x=int(target[0]), y=int(target[1]), z=int(target[2]),
            method="bucket",
        )
        return True

    def _use_hoe(self, selected, hit):
        hx, hy, hz = int(hit[0]), int(hit[1]), int(hit[2])
        target = self.world.get_block_type(hx, hy, hz)
        if target not in ("grass_block", "dirt"):
            return False
        above = self.world.get_block_type(hx, hy + 1, hz)
        if above is not None:
            above_def = Block._block_defs.get(above, {})
            if above_def.get("collision", not above_def.get("intangible", False)):
                return False
        self.world.set_block(hx, hy, hz, "farmland", meta=0, immediate_mesh=True)
        self._play_block_sound("farmland", (float(hx), float(hy), float(hz)))
        if self.gamemode != 1:
            if selected.damage_item(1):
                self.inventory.set_stack(self.inventory.active_idx, None)
        if target == "grass_block" and random.randrange(8) == 0:
            scatter = 0.7
            sx = hx + (random.random() * scatter + (1.0 - scatter) * 0.5) - 0.5
            sy = hy + 1.2 - 0.5
            sz = hz + (random.random() * scatter + (1.0 - scatter) * 0.5) - 0.5
            entity = ItemEntity(
                ItemStack("seeds"),
                (sx, sy, sz),
                velocity=(0.0, 0.0, 0.0),
                pickup_delay=10,
            )
            self.world.entities.append(entity)
        return True

    def _toggle_door_at(self, x, y, z):
        door_x, door_y, door_z = int(x), int(y), int(z)
        hit_meta = self.world.get_metadata(door_x, door_y, door_z)
        if hit_meta & 8:
            door_y -= 1
        if self.world.get_block_type(door_x, door_y, door_z) != "wooden_door":
            return
        was_open, new_meta = toggle_door_metadata(self.world, door_x, door_y, door_z)
        if new_meta is None:
            return
        self.world.set_block(door_x, door_y, door_z, "wooden_door", meta=new_meta)
        self.world.set_block(
            door_x, door_y + 1, door_z, "wooden_door",
            meta=new_meta | 8, immediate_mesh=True,
        )
        self._play_world_sound(
            "random.door_close" if was_open else "random.door_open",
            volume=1.0,
            pitch=1.0,
            position=(float(door_x), float(door_y), float(door_z)),
        )

    def pick_block(self):
        hit, _normal = self._raycast(include_intangible=True)
        if hit is None:
            return False
        block_type = self.world.get_block_type(*hit)
        if not block_type:
            return False
        item_id = block_type
        try:
            from game.entities.item import Item
            for iid, defn in Item.item_defs.items():
                if defn.get("block") == block_type:
                    item_id = iid
                    break
            if item_id not in Item.item_defs and item_id not in Item.block_defs:
                return False
        except Exception:
            pass

        inv = self.inventory
        if self.gamemode == 1:
            inv.set_stack(
                inv.current_item,
                ItemStack(item_id, Item.max_stack_size(item_id)),
            )
            return True

        for slot in range(inv.HOTBAR_SIZE):
            stack = inv.main[slot]
            if stack is not None and stack.id == item_id:
                inv.select_hotbar(slot)
                return True
        return False

    _JUKEBOX_META_TO_RECORD = {1: "record_13", 2: "record_cat"}
    _JUKEBOX_RECORD_TO_META = {value: key for key, value in _JUKEBOX_META_TO_RECORD.items()}

    def _show_actionbar_message(self, message, duration=3.0):
        now = time.monotonic()
        self._actionbar_message = str(message)
        self._actionbar_started_at = now
        self._actionbar_expires_at = now + max(0.0, float(duration))

    def _eject_jukebox_record(self, x, y, z, *, clear_metadata=True):
        if self.world is None or self.world.get_block_type(x, y, z) != "jukebox":
            return False
        meta = int(self.world.get_metadata(x, y, z)) & 15
        record_id = self._JUKEBOX_META_TO_RECORD.get(meta)
        if record_id is None:
            return False

        position = (float(x) + 0.5, float(y) + 0.5, float(z) + 0.5)
        music_player = getattr(self.world, "record_music_player", None)
        stop_record = getattr(music_player, "stop_record", None)
        if callable(stop_record):
            stop_record(position)

        if clear_metadata:
            self.world.set_block(x, y, z, "jukebox", meta=0, immediate_mesh=True)

        item_pos = (
            float(x) + random.random() * 0.7 + 0.15,
            float(y) + random.random() * 0.7 + 0.66,
            float(z) + random.random() * 0.7 + 0.15,
        )
        self.world.spawn_entity(ItemEntity(
            ItemStack(record_id, 1), item_pos, pickup_delay=10,
        ))
        return True

    @_network_edit_action
    def place_block(self):
        operation_started = time.perf_counter()
        selected = self.inventory.get_active_item()

        if selected is not None and selected.id == "milk_bucket":
            return self._use_milk_bucket()

        from game.entities.minecart import MinecartEntity
        from game.entities.boat import BoatEntity

        target_entity = self._raycast_attack_entity()
        if target_entity is not None:
            if isinstance(target_entity, (MinecartEntity, BoatEntity)):
                if target_entity.interact(self):
                    return self._finish_interactable_action(selected)
                return False
            if selected is not None and (
                    MinecartEntity.type_for_item(selected.id) is not None
                    or selected.id == "boat"):
                return False
            interact = getattr(target_entity, "interact", None)
            if callable(interact) and interact(self):
                return self._finish_interactable_action(selected)

        if selected is not None and selected.id == "fishing_rod":
            return self._use_fishing_rod(selected)

        if MinecartEntity.try_place_from_item(self, selected):
            return True

        if selected is not None and selected.id == "boat":
            hit, _normal = self._raycast_blocks(
                include_intangible=True, stop_on_source_liquid=True,
            )
            if hit is None:
                hit, _normal = self._raycast_blocks(include_intangible=True)
            if hit is None:
                return False
            hx, hy, hz = int(hit[0]), int(hit[1]), int(hit[2])
            if self.world.get_block_type(hx, hy, hz) not in ("water", "flowing_water"):
                above = self.world.get_block_type(hx, hy + 1, hz)
                if above in ("water", "flowing_water"):
                    hy = hy + 1
            self.world.spawn_entity(BoatEntity(
                self.world, (float(hx), float(hy) + 0.5, float(hz)),
            ))
            if self.gamemode != 1:
                self.inventory.remove_item(self.inventory.active_idx)
            return True

        hit, normal = self._raycast(include_intangible=True)
        if hit and self.world.get_block_type(*hit) == "cake":
            self.stop_digging()
            return self._finish_interactable_action(selected) if self._eat_cake_at(*hit) else False
        if hit and self.world.get_block_type(*hit) == "note_block":
            self.stop_digging()
            network_note = getattr(self.world, "_network_note_block_callback", None)
            if (bool(getattr(self.world, "_multiplayer_client_world", False))
                    and callable(network_note)):
                if not network_note("pitch", *hit):
                    return False
            else:
                tile_entity = self.world.get_tile_entity(*hit)
                if tile_entity is None:
                    return False
                tile_entity.change_pitch()
                tile_entity.trigger_note()
            return self._finish_interactable_action(selected)
        if hit and self.world.get_block_type(*hit) == "jukebox":
            hx, hy, hz = (int(value) for value in hit)
            jukebox_meta = int(self.world.get_metadata(hx, hy, hz)) & 15
            network_jukebox = getattr(self.world, "_network_jukebox_callback", None)
            if bool(getattr(self.world, "_multiplayer_client_world", False)) and callable(network_jukebox):
                if jukebox_meta > 0:
                    if network_jukebox("eject", hx, hy, hz):
                        return self._finish_interactable_action(selected)
                    return False
                if getattr(selected, "id", None) in self._JUKEBOX_RECORD_TO_META:
                    return bool(network_jukebox("insert", hx, hy, hz))
                return False
            if jukebox_meta > 0:
                if self._eject_jukebox_record(hx, hy, hz):
                    return self._finish_interactable_action(selected)
                return False
            record_meta = self._JUKEBOX_RECORD_TO_META.get(
                getattr(selected, "id", None),
            )
            if record_meta is not None:
                self.world.set_block(
                    hx, hy, hz, "jukebox", meta=record_meta, immediate_mesh=True,
                )
                record_name = "13" if record_meta == 1 else "cat"
                music_player = getattr(self.world, "record_music_player", None)
                play_record = getattr(music_player, "play_record", None)
                if callable(play_record):
                    play_record(
                        record_name,
                        (float(hx) + 0.5, float(hy) + 0.5, float(hz) + 0.5),
                    )
                from game.localization import translate
                self._show_actionbar_message(translate(
                    "mcpy.chat.nowPlaying", record_name,
                ))
                if self.gamemode != 1:
                    self.inventory.remove_item(self.inventory.active_idx)
                return self._finish_interactable_action(selected)
            return False

        if hit and self.world.get_block_type(*hit) == "wooden_door":
            self._toggle_door_at(*hit)
            return self._finish_interactable_action(selected)
        if hit and self.world.get_block_type(*hit) in ("lever", "stone_button"):
            redstone = getattr(self.world, "_redstone_system", None)
            if redstone is not None:
                if self.world.get_block_type(*hit) == "lever":
                    redstone.toggle_lever(*hit)
                else:
                    redstone.press_button(*hit)
            return self._finish_interactable_action(selected)
        if hit and self.world.get_block_type(*hit) in ("redstone_ore", "redstone_ore_lit"):
            from game.block.redstone_ore import activate_redstone_ore
            activate_redstone_ore(self.world, *hit)
            return self._finish_interactable_action(selected)

        if hit and self.world.get_block_type(*hit) == "crafting_table":
            if self.workbench_opener is not None:
                self.stop_digging()
                self.workbench_opener()
                return self._finish_interactable_action(selected)
            return False

        if hit:
            hit_type = self.world.get_block_type(*hit)
            if hit_type == "chest":
                if self.chest_opener is not None:
                    self.stop_digging()
                    self.chest_opener(*hit)
                    return self._finish_interactable_action(selected)
                return False
            if hit_type in ("furnace", "furnace_lit"):
                if self.furnace_opener is not None:
                    self.stop_digging()
                    self.furnace_opener(*hit)
                    return self._finish_interactable_action(selected)
                return False
            if hit_type == "dispenser":
                if self.dispenser_opener is not None:
                    self.stop_digging()
                    self.dispenser_opener(*hit)
                    return self._finish_interactable_action(selected)
                return False

        if (selected is not None and selected.id == "dye"
                and (int(getattr(selected, "damage", 0)) & 15) == 15
                and hit is not None):
            target_type = self.world.get_block_type(*hit)
            if target_type in ("crops", "sapling"):
                if bool(getattr(self.world, "_multiplayer_client_world", False)):
                    use_item = getattr(self.world, "_network_item_use_callback", None)
                    if callable(use_item) and use_item("bonemeal", *hit):
                        return self._finish_interactable_action(selected)
                    return False
                from game.block.bonemeal import apply_bonemeal
                if apply_bonemeal(self.world, *hit):
                    if self.gamemode != 1:
                        self.inventory.remove_item(self.inventory.active_idx)
                    return self._finish_interactable_action(selected)
                return False

        if selected is not None and Item.item_defs.get(selected.id, {}).get("type") == "bucket":
            return self._use_bucket(selected)

        if selected is not None and selected.id == "snowball":
            return self._use_snowball(selected)

        if selected is not None and selected.id == "egg":
            return self._use_egg(selected)

        if selected is not None and Item.item_defs.get(selected.id, {}).get("type") == "ranged_weapon":
            if selected.id == "bow":
                return self._use_bow(selected)

        if not selected:
            return False

        item_def = Item.item_defs.get(selected.id, {})
        if item_def.get("type") == "food":
            if self.gamemode == 1:
                return False
            heal_amount = max(0, int(item_def.get("foodpoints", 0)))
            self.prev_health = self.health
            self.health = min(20, self.health + heal_amount)
            self.inventory.consume_current(1)
            returned = item_def.get("returns")
            if returned and self.inventory.main[self.inventory.current_item] is None:
                self.inventory.set_stack(self.inventory.active_idx, ItemStack(returned))
            return True

        if selected.id == "seeds":
            if not hit or self.world.get_block_type(*hit) != "farmland":
                return False
            hx, hy, hz = (int(value) for value in hit)
            crop_pos = (hx, hy + 1, hz)
            if not can_place(self.world, "crops", *crop_pos):
                return False
            self.world.set_block(*crop_pos, "crops", meta=0, immediate_mesh=True)
            self._play_block_sound("crops", tuple(float(value) for value in crop_pos))
            if self.gamemode != 1:
                self.inventory.remove_item(self.inventory.active_idx)
            self._record_performance_event(
                "block.place", started_at=operation_started,
                block_type="crops", x=hx, y=hy + 1, z=hz, meta=0, method="seeds",
            )
            return True

        if not hit or not normal or normal == (0, 0, 0):
            return False

        item_def = Item.item_defs.get(selected.id, {})
        if item_def.get("tooltype") == "hoe":
            return self._use_hoe(selected, hit)

        nx, ny, nz = placement_position(self.world, hit, normal)
        if selected.id == "flint_and_steel":
            if self.world.get_block_type(nx, ny, nz) is not None:
                return False
            if not can_place(self.world, "fire", nx, ny, nz, face=normal, player_yaw=self.yaw):
                return False
            self.world.set_block(nx, ny, nz, "fire", meta=0, immediate_mesh=True)
            self._play_world_sound(
                "fire.ignite", volume=1.0,
                pitch=random.uniform(0.8, 1.2),
                position=(float(nx) + 0.5, float(ny) + 0.5, float(nz) + 0.5),
            )
            if self.gamemode != 1:
                if selected.damage_item(1):
                    self.inventory.set_stack(self.inventory.active_idx, None)
            self._record_performance_event(
                "block.place", started_at=operation_started,
                block_type="fire", x=int(nx), y=int(ny), z=int(nz), method="flint_and_steel",
            )
            return True

        if selected.id == "painting":
            from game.entities.painting import try_place_painting
            if not try_place_painting(self.world, hit[0], hit[1], hit[2], normal):
                return False
            if self.gamemode != 1:
                self.inventory.remove_item(self.inventory.active_idx)
            return True

        place_id = Item.placed_block(selected.id)
        if place_id is None:
            return False

        if place_id == "sign" and normal[1] == 0 and abs(normal[0]) + abs(normal[2]) == 1:
            place_id = "wall_sign"

        if not can_place(self.world, place_id, nx, ny, nz, face=normal, player_yaw=self.yaw):
            return False
        if place_id in ("wooden_door", "iron_door"):
            if tuple(normal) != (0, 1, 0):
                return False
        elif self._player_overlaps_placement(nx, ny, nz, place_id):
            return False

        meta = placement_meta(self.world, place_id, nx, ny, nz, face=normal, player_yaw=self.yaw)
        if place_id == "wool":
            meta = int(getattr(selected, "damage", 0)) & 15
        elif Item.has_subtypes(place_id):
            meta = int(selected.damage) & 15
        if place_id in ("wooden_door", "iron_door"):
            meta = align_door_meta_with_neighbor(
                self.world, nx, ny, nz, meta, door_type=place_id,
            )
            self.world.set_block(nx, ny, nz, place_id, meta=meta)
            self.world.register_door_hinges(nx, ny, nz, meta)
            self.world.set_block(
                nx, ny + 1, nz, place_id, meta=meta | 8,
                immediate_mesh=True,
            )
        elif place_id == "stone_slab" and self.world.get_block_type(nx, ny - 1, nz) == "stone_slab":

            self.world.set_block(nx, ny - 1, nz, "double_stone_slab", meta=0, immediate_mesh=True)
        else:
            self.world.set_block(nx, ny, nz, place_id, meta=meta, immediate_mesh=True)
        if place_id == "rail":
            refresh_rail_and_neighbors(self.world, nx, ny, nz)
        if place_id in ("sign", "wall_sign") and self.sign_opener is not None:
            self.sign_opener(int(nx), int(ny), int(nz))
        self._play_block_sound(place_id, (float(nx), float(ny), float(nz)))
        if self.gamemode != 1:
            self.inventory.remove_item(self.inventory.active_idx)
        self._record_performance_event(
            "block.place", started_at=operation_started,
            block_type=place_id, x=int(nx), y=int(ny), z=int(nz),
            meta=int(meta), method="item",
        )
        return True


    def _tick_water_ambience(self, dt):
        self._water_ambient_timer -= min(max(float(dt), 0.0), 0.25)
        if self._water_ambient_timer > 0.0:
            return

        sound_enabled = bool(getattr(self.settings, "sound", True))
        if (self.in_water or self.submerged or self.in_lava
                or self.sound_player is None or not sound_enabled):
            self._water_ambient_timer = random.uniform(3.0, 6.0)
            return

        world = getattr(self, "world", None)
        if world is None:
            self._water_ambient_timer = random.uniform(2.0, 4.0)
            return

        px, py, pz = self.pos
        bx, by, bz = math.floor(px + 0.5), math.floor(py + 0.5), math.floor(pz + 0.5)
        nearest = None
        nearest_sq = 11.0 * 11.0
        for dy in (-3, 0, 3):
            yy = by + dy
            if yy < 0 or yy >= getattr(world, "CHUNK_HEIGHT", 128):
                continue
            for dx in range(-6, 7):
                for dz in range(-6, 7):
                    if world.get_block_type(bx + dx, yy, bz + dz) != "water":
                        continue
                    dist_sq = (dx - (px - bx)) ** 2 + (dy - (py - by)) ** 2 + (dz - (pz - bz)) ** 2
                    if dist_sq < nearest_sq:
                        nearest_sq = dist_sq
                        nearest = (bx + dx, yy, bz + dz)

        if nearest is None:
            self._water_ambient_timer = random.uniform(3.0, 5.0)
            return

        self._play_world_sound(
            "liquid.water", volume=0.36,
            pitch=random.uniform(0.85, 1.15),
            position=(float(nearest[0]), float(nearest[1]), float(nearest[2])),
        )
        self._water_ambient_timer = random.uniform(3.5, 7.0)

    def _tick_fire_ambience(self, dt):
        self._fire_ambient_timer -= min(max(float(dt), 0.0), 0.25)
        if self._fire_ambient_timer > 0.0:
            return

        if (self.sound_player is None
                or not bool(getattr(self.settings, "sound", True))
                or self.in_water or self.submerged or self.in_lava):
            self._fire_ambient_timer = random.uniform(3.0, 5.0)
            return

        world = getattr(self, "world", None)
        if world is None:
            self._fire_ambient_timer = random.uniform(2.0, 4.0)
            return

        px, py, pz = self.pos
        bx, by, bz = math.floor(px + 0.5), math.floor(py + 0.5), math.floor(pz + 0.5)
        nearest = None
        nearest_sq = 9.0 * 9.0
        for dy in (-2, 0, 2):
            yy = by + dy
            if yy < 0 or yy >= getattr(world, "CHUNK_HEIGHT", 128):
                continue
            for dx in range(-5, 6):
                for dz in range(-5, 6):
                    if world.get_block_type(bx + dx, yy, bz + dz) != "fire":
                        continue
                    dist_sq = ((dx - (px - bx)) ** 2 + (dy - (py - by)) ** 2
                               + (dz - (pz - bz)) ** 2)
                    if dist_sq < nearest_sq:
                        nearest_sq = dist_sq
                        nearest = (bx + dx, yy, bz + dz)

        if nearest is None:
            self._fire_ambient_timer = random.uniform(2.0, 4.0)
            return

        self._play_world_sound(
            "fire.fire", volume=0.22,
            pitch=random.uniform(0.85, 1.15),
            position=tuple(float(v) for v in nearest),
        )
        self._fire_ambient_timer = random.uniform(4.0, 8.0)

    def _play_world_sound(self, name, volume=1.0, pitch=1.0, position=None):
        if self.sound_player:
            self.sound_player(
                name,
                volume=volume,
                pitch=pitch,
                position=tuple(position if position is not None else self.pos),
            )

    def _touching_water_material(self):
        self._check_water()
        return self.body_in_water

    def can_be_collided_with(self):
        return self.health > 0 and not self.dead

    def can_be_pushed(self):
        return self.health > 0 and not self.dead

    def _supporting_block_type(self):
        x, y, z = self.pos
        bx = math.floor(x + 0.5)
        by = math.floor(y - 0.04 + 0.5)
        bz = math.floor(z + 0.5)
        return self.world.get_block_type(bx, by, bz)

    def _has_deep_water_for_fall_protection(self, px=None, py=None, pz=None):
        world = getattr(self, "world", None) or Entity.world
        if world is None:
            return False
        if px is None or py is None or pz is None:
            px, py, pz = self.pos
        if not hasattr(world, "get_block_type"):
            return False

        hw = self._half_width()
        hd = self._half_depth()
        x0 = math.floor(px + 0.5 - hw + 1e-7)
        x1 = math.floor(px + 0.5 + hw - 1e-7)
        z0 = math.floor(pz + 0.5 - hd + 1e-7)
        z1 = math.floor(pz + 0.5 + hd - 1e-7)
        feet_cell_y = math.floor(py + 0.5)

        for bx in range(x0, x1 + 1):
            for bz in range(z0, z1 + 1):
                if world.get_block_type(bx, feet_cell_y, bz) not in ("water", "flowing_water"):
                    continue
                lowest_y = highest_y = feet_cell_y
                while world.get_block_type(bx, lowest_y - 1, bz) in ("water", "flowing_water"):
                    lowest_y -= 1
                while world.get_block_type(bx, highest_y + 1, bz) in ("water", "flowing_water"):
                    highest_y += 1
                if highest_y - lowest_y + 1 >= 3:
                    return True
        return False

    def _update_fall_damage(self, dy, was_on_ground, was_on_ladder, ticks=1.0,
                            was_in_water=None):

        if was_in_water is None:
            was_in_water = self.in_water
        if was_in_water:
            was_in_water = self._has_deep_water_for_fall_protection()
        if self.on_ladder or was_on_ladder or was_in_water:
            self._fall_distance = 0.0
            self._fall_pending = 0.0
            self._fall_tick_clock = 0.0
            self._fall_airborne = False
            return

        if self.on_ground or self._blocked_down:
            counted = self._fall_distance
            if getattr(self, "_fall_airborne", False):
                replay = alpha_fall_replay(
                    self._fall_y0, self._fall_vy0, self.pos[1],
                )
                measured_total = self._fall_distance + self._fall_pending
                if replay is not None and abs(replay[1] - measured_total) < 1.0:
                    counted = replay[0]
            if counted > 3.0:
                fall_volume = min(1.0, 0.35 + (counted - 3.0) * 0.1)
                self._play_world_sound(
                    "damage.fallbig", volume=fall_volume, pitch=1.0, position=self.pos
                )
                self.damage_player(math.ceil(counted - 3.0), source="fall")
            self._fall_distance = 0.0
            self._fall_pending = 0.0
            self._fall_tick_clock = 0.0
            self._fall_airborne = False
            return

        if was_on_ground:
            self._fall_pending = 0.0
            self._fall_tick_clock = 0.0
            self._fall_airborne = True
            self._fall_y0 = self.pos[1] - dy
            frame_ticks = max(float(ticks), 1e-6)
            rate = dy / frame_ticks
            self._fall_vy0 = rate if rate > 0.05 else -0.0784
        if dy < 0:
            self._fall_pending = getattr(self, "_fall_pending", 0.0) + -dy
        clock = getattr(self, "_fall_tick_clock", 0.0) + max(0.0, float(ticks))
        if clock >= 1.0:
            clock -= math.floor(clock)
            self._fall_distance += self._fall_pending
            self._fall_pending = 0.0
        self._fall_tick_clock = clock

    def _tick_inventory_animations(self, dt):
        self._anim_accum += min(max(float(dt), 0.0), 0.25)
        inventory_ticks = 0
        while self._anim_accum >= 0.05 and inventory_ticks < 4:
            self._anim_accum -= 0.05
            inventory_ticks += 1
            self.inventory.decrement_animations()

    def _update_riding(self, mount, dt):
        self._heading_accel = (0.0, 0.0)
        self.y_size = self.render_y_offset = 0.0

        last = getattr(self, "_ride_last_pos", None)
        if last is None:
            last = tuple(self.pos)
            self._rider_yaw_delta = 0.0
        dx = self.pos[0] - last[0]
        dz = self.pos[2] - last[2]
        self._ride_last_pos = tuple(self.pos)

        cart_yaw = getattr(mount, "yaw", None)
        cart_prev = getattr(mount, "prev_yaw", cart_yaw)
        if cart_yaw is not None:
            consume = getattr(mount, "consume_turn", None)
            if callable(consume):
                turn = float(consume())
            else:
                turn = (float(cart_yaw) - float(cart_prev) + 180.0) % 360.0 - 180.0
            self._rider_yaw_delta = getattr(self, "_rider_yaw_delta", 0.0) - turn
            ticks = min(max(float(dt), 0.0), 0.1) * TICKS_PER_SECOND
            step = self._rider_yaw_delta * (1.0 - 0.5 ** ticks)
            limit = 10.0 * ticks
            step = max(-limit, min(limit, step))
            self._rider_yaw_delta -= step
            self.yaw += step

        self._update_limb_swing(dx, dz, dt)
        self._tick_survival(dt)
        self._tick_inventory_animations(dt)
        self.sync_camera()

    def _damage_cactus(self):
        return self.damage_player(1, source="cactus", bypass_armor=True)

    def _supporting_block_is_cactus(self):
        return self._supporting_block_type() == "cactus"

    def _touching_cactus(self):
        world = getattr(self, "world", None) or Entity.world
        if world is None:
            return False
        left, bottom, front, right, top, back = self.get_bounding_box()
        for bx, by, bz in self._block_positions_for_aabb(*self.pos):
            if world.get_block_type(bx, by, bz) != "cactus":
                continue
            x0, x1 = bx - 0.5, bx + 0.5
            y0, y1 = by - 0.5, by + 0.5
            z0, z1 = bz - 0.5, bz + 0.5
            if left < x1 and right > x0 and bottom < y1 and top > y0 and front < z1 and back > z0:
                return True
        return False

    def update(self, keys, dt):
        self._current_keys = keys
        mount = getattr(self, "riding_entity", None)
        if mount is not None:
            if mount.dead or mount.rider is not self:
                self.riding_entity = None
                self._ride_last_pos = None
            else:
                self._update_riding(mount, dt)
                advance_cape_motion(self, dt)
                return
        self._ride_last_pos = None

        if self.health <= 0:
            self.stop_using_item()
            self._heading_accel = (0.0, 0.0)
            self._death_tick_accum += min(max(float(dt), 0.0), 0.25)
            super().update(dt)
            death_ticks = 0
            while self._death_tick_accum >= 0.05 and death_ticks < 4:
                self._death_tick_accum -= 0.05
                death_ticks += 1
                self._advance_death_time()
                self.hearts_life = max(0, self.hearts_life - 1)
                self.hurt_time = max(0, self.hurt_time - 1)
                self.prev_camera_yaw = self.camera_yaw
                self.prev_camera_pitch = self.camera_pitch
                self.camera_yaw += (0.0 - self.camera_yaw) * 0.4
                self.camera_pitch += (0.0 - self.camera_pitch) * 0.8
            advance_cape_motion(self, dt)
            self.sync_camera()
            return
        self._check_water()
        self._apply_water_current(min(max(float(dt), 0.0), 0.1) * TICKS_PER_SECOND)
        self._check_ladder()
        old_pos = tuple(self.pos)
        self.prev_pos[:] = self.pos
        was_on_ground = self.on_ground
        was_on_ladder = self.on_ladder
        was_touching_water = self.body_in_water
        was_in_water_java = self.touches_water_java(*old_pos)
        old_velocity = tuple(self.vel)

        self.handle_keys(keys, dt)
        super().update(dt)
        if self.is_sneaking:
            target = 0.2
            ticks = min(max(float(dt), 0.0), 0.1) * TICKS_PER_SECOND
            blend = 1.0 - (0.4 ** max(ticks, 0.0))
            self.render_y_offset += (target - self.render_y_offset) * blend
            self.y_size = self.render_y_offset
        if self.gamemode != 1 and self._touching_cactus():
            self._damage_cactus()
        self._splash_cooldown = max(0.0, self._splash_cooldown - min(dt, 0.1))

        if not self._splash_initialized:
            self._splash_initialized = True
        elif not was_touching_water and self._touching_water_material():
            from game.entities.particles import spawn_water_entry_particles
            spawn_water_entry_particles(self.world, self.pos, old_velocity, self.size[0])
            horizontal_speed = math.hypot(old_velocity[0], old_velocity[2])
            genuine_entry = old_velocity[1] < -0.12 or (
                old_velocity[1] <= 0.02 and horizontal_speed > 0.12
            )
            if genuine_entry and self._splash_cooldown <= 0.0:
                entry_speed = math.sqrt(
                    old_velocity[0] * old_velocity[0] * 0.2
                    + old_velocity[1] * old_velocity[1]
                    + old_velocity[2] * old_velocity[2] * 0.2
                )
                splash_volume = min(1.0, max(0.05, entry_speed * 1.5))
                self._play_world_sound(
                    "liquid.splash",
                    volume=splash_volume,
                    pitch=random.uniform(0.8, 1.2),
                    position=self.pos,
                )
                self._splash_cooldown = 0.28

        self._tick_water_ambience(dt)
        self._tick_fire_ambience(dt)

        dx = self.pos[0] - old_pos[0]
        dy = self.pos[1] - old_pos[1]
        dz = self.pos[2] - old_pos[2]
        self._update_limb_swing(dx, dz, dt)

        self._update_fall_damage(
            dy, was_on_ground, was_on_ladder,
            ticks=min(max(float(dt), 0.0), 0.1) * TICKS_PER_SECOND,
            was_in_water=was_in_water_java,
        )

        if self.on_ground and not self.in_water:
            walked = math.sqrt(dx * dx + dz * dz) * 0.6
            self._distance_walked += walked
            while self._distance_walked >= 1.0:
                self._distance_walked -= 1.0
                block_type = self._supporting_block_type()
                if block_type is None or block_type == "air":
                    continue
                material = material_for_block(block_type)
                if material.is_liquid:
                    continue
                step_sound = step_sound_for_block(block_type)
                self._play_world_sound(
                    f"step.{step_sound.name}",
                    volume=0.15 * step_sound.volume,
                    pitch=step_sound.pitch,
                    position=self.pos,
                )

        self._tick_survival(dt)
        if self.health > 0:
            self.collect_items()
            self.update_digging(dt)
            self.update_held_use(dt)

        self._anim_accum += min(max(float(dt), 0.0), 0.25)
        inventory_ticks = 0
        while self._anim_accum >= 0.05 and inventory_ticks < 4:
            self._anim_accum -= 0.05
            inventory_ticks += 1
            self.inventory.decrement_animations()

        advance_cape_motion(self, dt)
        self.sync_camera()
