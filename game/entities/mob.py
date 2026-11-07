from __future__ import annotations

import math
import random

from .entity import Entity, MAX_DT, TICKS_PER_SECOND


def _wrap_degrees(value: float) -> float:
    return (float(value) + 180.0) % 360.0 - 180.0


class IMobs:
    pass


class Mob(Entity):
    is_living_entity = True
    GROUND_CONTACT_IN_WATER = True
    ai_frozen = True
    global_ai_paused = False

    WIDTH = 0.6
    HEIGHT = 1.8
    MOVE_SPEED = 0.7
    GROUND_ACCEL = 0.1
    AIR_ACCEL = 0.02
    AI_TICK_SECONDS = 1.0 / TICKS_PER_SECOND
    HEALTH = 10
    STEP_DISTANCE = 1
    IDLE_LOOK_RANGE = 8.0
    FACE_YAW_SPEED = 10.0
    FACE_PITCH_SPEED = 10.0
    FIRE_IMMUNE = False

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, size=(self.WIDTH, self.HEIGHT, self.WIDTH))
        self._living_fluid_physics = True
        self.prevent_entity_spawning = True
        self.skin_path = "char.png"
        self._rng = rng if rng is not None else random.Random()

        if yaw is None:
            next_float = getattr(self._rng, "next_float", None)
            if callable(next_float):
                self.yaw = float(next_float()) * 360.0 - 180.0
            else:
                self.yaw = float(self._rng.uniform(-180.0, 180.0))
        else:
            self.yaw = float(yaw)
        self.prev_yaw = self.yaw
        self.render_yaw_offset = self.yaw
        self.prev_render_yaw_offset = self.yaw
        self.prev_pos = list(self.pos)

        self.limb_swing = 0.0
        self.prev_limb_swing = 0.0
        self.limb_swing_amount = 0.0
        self.prev_limb_swing_amount = 0.0
        self.ticks_existed = 0
        self.hurt_time = 0
        self.max_hurt_time = 10
        self._last_attacked_damage = 0
        self.death_time = 0
        self.attacked_at_yaw = 0.0

        self.health = self.HEALTH
        self.prev_health = self.HEALTH
        self.hearts_life = 0
        self.hearts_halves_life = 20
        self.entity_age = 0

        self.move_strafing = 0.0
        self.move_forward = 0.0
        self.random_yaw_velocity = 0.0
        self.is_jumping = False
        self.default_pitch = 0.0
        self.rotation_pitch = 0.0
        self._heading_accel = (0.0, 0.0)
        self._look_target = None
        self._look_target_ticks = 0
        self.prev_rotation_pitch = 0.0
        self._pending_path_yaw = None

        try:
            phase = float(getattr(Entity.world, "_tick_accum", 0.0))
        except (TypeError, ValueError):
            phase = 0.0
        self._tick_accum = min(max(phase % self.AI_TICK_SECONDS, 0.0), self.AI_TICK_SECONDS - 1e-9)
        self._frame_distance_accum = 0.0
        self._frame_dx_accum = 0.0
        self._frame_dz_accum = 0.0
        self.distance_walked_modified = 0.0
        self.next_step_distance = self.STEP_DISTANCE
        self._death_particles_spawned = False
        self._drops_spawned = False
        self._living_sound_time = 0
        self.attack_time = 0

        self.air = 300
        self.max_air = 300
        self._fire_ticks = -20
        self._fall_distance = 0.0
        self._prev_on_ground_for_fall = True
        self._survival_tick_accum = 0.0

    @classmethod
    def spawn_near_player(cls, world, player):
        if world is None or player is None:
            return None

        facing = math.radians(float(getattr(player, "yaw", 0.0)))
        px, py, pz = (float(v) for v in player.pos)
        candidates = []
        for distance in (3.0, 2.5, 3.75, 2.0):
            for offset in (0.0, 35.0, -35.0, 70.0, -70.0, 140.0, -140.0, 180.0):
                angle = facing + math.radians(offset)
                x = px - math.sin(angle) * distance
                z = pz - math.cos(angle) * distance
                for y_offset in (0.0, 0.5, 1.0, -0.5):
                    candidates.append((x, py + y_offset, z))

        mob = cls(candidates[0])
        for candidate in candidates:
            if not mob._collides(*candidate):
                mob.pos = [float(v) for v in candidate]
                mob.prev_pos = list(mob.pos)
                mob.render_y_offset = 0.0
                world.spawn_entity(mob)
                return mob
        return None

    def _living_ai_tick(self) -> None:
        self.ticks_existed += 1
        self.hearts_life = max(0, self.hearts_life - 1)
        self.hurt_time = max(0, self.hurt_time - 1)
        self.attack_time = max(0, self.attack_time - 1)
        self._tick_living_sound()

        if self.health <= 0:
            self.move_strafing = 0.0
            self.move_forward = 0.0
            self.random_yaw_velocity = 0.0
            self.is_jumping = False
            self.death_time += 1
            if self.death_time > 20:
                if not self._death_particles_spawned:
                    self._death_particles_spawned = True
                    try:
                        from game.entities.particles import spawn_living_death_particles
                        spawn_living_death_particles(Entity.world, self.pos, self.size, rng=self._rng)
                    except Exception:
                        pass
                self.dead = True
            return

        permanent_mob = type(self) is Mob
        if permanent_mob or Mob.global_ai_paused or type(self).ai_frozen:
            self._idle_look()
            return

        self.entity_age += 1
        self._tick_despawn()
        if self.dead:
            return
        self._update_player_action_state()

        self.move_strafing *= 0.98
        self.move_forward *= 0.98
        self.random_yaw_velocity *= 0.9

        if self.is_jumping:
            if self.in_water or self.in_lava:
                self.vel[1] += 0.04
            elif self.on_ground:
                self.vel[1] = self.JUMP_MOTION
                self.on_ground = False
                self._jump_airborne = True

    def _rand_int(self, bound: int) -> int:
        next_int = getattr(self._rng, "next_int", None)
        if callable(next_int):
            return int(next_int(bound))
        return min(bound - 1, int(self._rng.random() * bound))

    @staticmethod
    def _wrap_degrees(value: float) -> float:
        return (float(value) + 180.0) % 360.0 - 180.0

    def _update_rotation(self, current: float, target: float, max_delta: float) -> float:
        delta = self._wrap_degrees(target - current)
        if delta > max_delta:
            delta = max_delta
        elif delta < -max_delta:
            delta = -max_delta
        return current + delta

    def _eye_height(self, entity=None) -> float:
        ent = self if entity is None else entity
        explicit = getattr(ent, "EYE_HEIGHT", None)
        if explicit is not None:
            try:
                return float(explicit)
            except (TypeError, ValueError):
                pass
        try:
            return float(ent.size[1]) * 0.85
        except Exception:
            return 1.62

    def _face_entity(self, target, yaw_speed: float, pitch_speed: float) -> None:
        dx = float(target.pos[0]) - float(self.pos[0])
        dz = float(target.pos[2]) - float(self.pos[2])
        dy = (
            float(target.pos[1]) + self._eye_height(target)
            - (float(self.pos[1]) + self._eye_height())
        )
        horiz = math.sqrt(dx * dx + dz * dz) or 1e-6
        target_yaw = math.degrees(math.atan2(-dx, -dz))
        target_pitch = math.degrees(math.atan2(dy, horiz))
        self.yaw = self._update_rotation(self.yaw, target_yaw, yaw_speed)
        self.rotation_pitch = self._update_rotation(
            self.rotation_pitch, target_pitch, pitch_speed,
        )
        self.random_yaw_velocity = 0.0

    def _nearest_player_for_look(self):
        world = Entity.world
        if world is None:
            return None
        player = getattr(world, "player", None)
        if player is None or getattr(player, "dead", False):
            return None
        try:
            if float(getattr(player, "health", 1)) <= 0.0:
                return None
        except (TypeError, ValueError):
            return None
        dx = float(player.pos[0]) - float(self.pos[0])
        dy = float(player.pos[1]) - float(self.pos[1])
        dz = float(player.pos[2]) - float(self.pos[2])
        if dx * dx + dy * dy + dz * dz > self.IDLE_LOOK_RANGE * self.IDLE_LOOK_RANGE:
            return None
        can_see = getattr(self, "can_entity_be_seen", None)
        if callable(can_see):
            try:
                if not can_see(player):
                    return None
            except Exception:
                pass
        return player

    def _tick_look_target(self) -> bool:
        if self._look_target is not None:
            target = self._look_target
            self._look_target_ticks -= 1
            still_valid = (
                self._look_target_ticks > 0
                and not getattr(target, "dead", False)
            )
            if still_valid:
                dx = float(target.pos[0]) - float(self.pos[0])
                dy = float(target.pos[1]) - float(self.pos[1])
                dz = float(target.pos[2]) - float(self.pos[2])
                if dx * dx + dy * dy + dz * dz > self.IDLE_LOOK_RANGE * self.IDLE_LOOK_RANGE:
                    still_valid = False
            if still_valid:
                return True
            self._look_target = None
            self._look_target_ticks = 0

        if self._rng.random() < 0.02:
            player = self._nearest_player_for_look()
            if player is not None:
                self._look_target = player
                self._look_target_ticks = 10 + self._rand_int(20)
                return True
            self.random_yaw_velocity = (self._rng.random() - 0.5) * 20.0
        return False

    def _apply_look_rotation(self, tick_scale: float = 1.0) -> None:
        target = self._look_target
        if target is None or getattr(target, "dead", False):
            return
        scale = max(0.0, min(1.0, float(tick_scale)))
        if scale <= 0.0:
            return
        self._face_entity(
            target,
            self.FACE_YAW_SPEED * scale,
            float(self.FACE_PITCH_SPEED) * scale,
        )

    def _idle_look(self) -> None:
        self.move_strafing = 0.0
        self.move_forward = 0.0
        self.is_jumping = False
        self._tick_look_target()

    def _update_player_action_state(self) -> None:
        if self._tick_look_target():
            self.is_jumping = self._rng.random() < 0.01
            if self.in_water or self.in_lava:
                self.is_jumping = self._rng.random() < 0.8
            return

        if self._rng.random() < 0.07:
            self.move_strafing = (self._rng.random() - 0.5) * self.MOVE_SPEED
            self.move_forward = self._rng.random() * self.MOVE_SPEED

        self.is_jumping = self._rng.random() < 0.01
        if self._rng.random() < 0.05:
            self.random_yaw_velocity = (self._rng.random() - 0.5) * 20.0
        if self.in_water or self.in_lava:
            self.is_jumping = self._rng.random() < 0.8

    def _tick_despawn(self) -> None:
        world = Entity.world
        player = getattr(world, "player", None) if world is not None else None
        if player is None:
            return
        dx = float(player.pos[0]) - self.pos[0]
        dy = float(player.pos[1]) - self.pos[1]
        dz = float(player.pos[2]) - self.pos[2]
        dist_sq = dx * dx + dy * dy + dz * dz
        if dist_sq > 16384.0:
            self.dead = True
            return
        if self.entity_age > 600 and self._rand_int(800) == 0:
            if dist_sq < 1024.0:
                self.entity_age = 0
            else:
                self.dead = True

    HURT_SOUND = "random.hurt"
    DEATH_SOUND = "random.hurt"
    LIVING_SOUND = None
    DROP_ITEM = None

    def _tick_living_sound(self) -> None:
        if self._rand_int(1000) < self._living_sound_time:
            self._living_sound_time = -80
            name = self.LIVING_SOUND
            if name:
                pitch = (self._rng.random() - self._rng.random()) * 0.2 + 1.0
                self._play_world_sound(name, volume=1.0, pitch=pitch, position=self.pos)
        else:
            pass
        self._living_sound_time += 1

    def drop_item(self, item_id, count=1, y_offset=0.0, damage=0):
        world = Entity.world
        if world is None or not hasattr(world, "spawn_entity"):
            return None
        try:
            from game.entities.item import ItemEntity, ItemStack
            entity = ItemEntity(
                ItemStack(item_id, count, damage),
                (float(self.pos[0]), float(self.pos[1]) + y_offset, float(self.pos[2])),
                pickup_delay=10,
            )
        except Exception:
            return None
        world.spawn_entity(entity)
        return entity

    def _on_death(self, attacker=None) -> None:
        if self._drops_spawned:
            return
        self._drops_spawned = True
        if self.DROP_ITEM:
            for _ in range(self._rand_int(3)):
                self.drop_item(self.DROP_ITEM, 1)

    def spawn_explosion_particles(self) -> None:
        world = Entity.world
        spawn = getattr(world, "spawn_particle", None) if world is not None else None
        if not callable(spawn):
            return
        w, h = float(self.size[0]), float(self.size[1])
        for _ in range(20):
            mx, my, mz = (self._rng.gauss(0.0, 0.02) for _ in range(3))
            spawn(
                "explode",
                self.pos[0] + self._rng.random() * w * 2.0 - w - mx * 10.0,
                self.pos[1] + self._rng.random() * h - my * 10.0,
                self.pos[2] + self._rng.random() * w * 2.0 - w - mz * 10.0,
                mx, my, mz,
            )

    def _update_heading_acceleration(self, ticks: float) -> None:
        if self.in_water or self.in_lava:
            accel = self.AIR_ACCEL
        elif self.on_ground:
            friction = self._ground_slipperiness() * self.DRAG_XZ
            friction = max(friction, 1e-4)
            accel = self.GROUND_ACCEL * (0.16277136 / (friction * friction * friction))
        else:
            accel = self.AIR_ACCEL

        strafe = float(self.move_strafing)
        forward = float(self.move_forward)
        length = math.sqrt(strafe * strafe + forward * forward)
        if length < 0.01:
            self._heading_accel = (0.0, 0.0)
            return
        if length < 1.0:
            length = 1.0
        scale = accel / length
        strafe *= scale
        forward *= scale
        yaw = math.radians(self.yaw)
        ax = strafe * math.cos(yaw) - forward * math.sin(yaw)
        az = -strafe * math.sin(yaw) - forward * math.cos(yaw)
        self._heading_accel = (ax, az)
        self.vel[0] += ax * ticks
        self.vel[2] += az * ticks

    def can_be_collided_with(self) -> bool:
        return self.health > 0 and not self.dead

    def can_be_pushed(self) -> bool:
        return self.health > 0 and not self.dead

    def _play_world_sound(self, name, volume=1.0, pitch=1.0, position=None) -> None:
        world = Entity.world
        callback = getattr(world, "sound_player", None) if world is not None else None
        if not callable(callback):
            callback = getattr(Entity, "sound_player", None)
        if callable(callback):
            callback(
                name, volume=volume, pitch=pitch,
                position=tuple(position if position is not None else self.pos),
            )

    def attack_entity_from(self, attacker=None, amount=1) -> bool:
        try:
            amount = max(0, int(amount))
        except (TypeError, ValueError):
            return False
        self.entity_age = 0
        if self.health <= 0:
            return False
        if amount <= 0:
            return self.on_zero_damage_projectile_hit(attacker)

        if self.hearts_life > self.hearts_halves_life / 2:
            if self.prev_health - amount >= self.health:
                return False
            self.health = max(0, self.prev_health - amount)
        else:
            self.prev_health = self.health
            self.hearts_life = self.hearts_halves_life
            self.health = max(0, self.health - amount)
            self.hurt_time = self.max_hurt_time = 10

        self._last_attacked_damage = amount
        self.limb_swing_amount = 1.5
        self.attacked_at_yaw = 0.0
        if attacker is not None and hasattr(attacker, "pos"):
            dx = float(attacker.pos[0]) - self.pos[0]
            dz = float(attacker.pos[2]) - self.pos[2]
            if dx * dx + dz * dz < 1.0e-4:
                dx = self._rng.uniform(-0.01, 0.01)
                dz = self._rng.uniform(-0.01, 0.01)
            self.attacked_at_yaw = math.degrees(math.atan2(dz, dx)) - self.yaw
            length = math.sqrt(dx * dx + dz * dz) or 1.0
            self.vel[0] = self.vel[0] * 0.5 - dx / length * 0.4
            self.vel[1] = min(0.4, self.vel[1] * 0.5 + 0.4)
            self.vel[2] = self.vel[2] * 0.5 - dz / length * 0.4
        else:
            self.attacked_at_yaw = 0.0 if self._rng.random() < 0.5 else 180.0

        pitch = (self._rng.random() - self._rng.random()) * 0.2 + 1.0
        if self.health <= 0:
            self._play_world_sound(self.DEATH_SOUND, volume=1.0, pitch=pitch, position=self.pos)
            self.move_forward = self.move_strafing = 0.0
            self.is_jumping = False
            self._on_death(attacker)
        else:
            self._play_world_sound(self.HURT_SOUND, volume=1.0, pitch=pitch, position=self.pos)
        return True

    def on_zero_damage_projectile_hit(self, attacker=None) -> bool:
        if self.dead or self.health <= 0:
            return False
        self.entity_age = 0
        if (self.hurt_time > self.max_hurt_time / 2
                and 0 <= self._last_attacked_damage):
            return False

        self._last_attacked_damage = 0
        self.limb_swing_amount = 1.5
        self.hurt_time = self.max_hurt_time = 10
        self.attacked_at_yaw = 0.0
        if attacker is not None and hasattr(attacker, "pos"):
            dx = float(attacker.pos[0]) - self.pos[0]
            dz = float(attacker.pos[2]) - self.pos[2]
            if dx * dx + dz * dz < 1.0e-4:
                next_float = getattr(self._rng, "next_float", None)
                if callable(next_float):
                    dx = (next_float() * 2.0 - 1.0) * 0.01
                    dz = (next_float() * 2.0 - 1.0) * 0.01
                else:
                    dx = self._rng.uniform(-0.01, 0.01)
                    dz = self._rng.uniform(-0.01, 0.01)
            self.attacked_at_yaw = math.degrees(math.atan2(dz, dx)) - self.yaw
            length = math.sqrt(dx * dx + dz * dz) or 1.0
            self.vel[0] = self.vel[0] * 0.5 - dx / length * 0.4
            self.vel[1] = min(0.4, self.vel[1] * 0.5 + 0.4)
            self.vel[2] = self.vel[2] * 0.5 - dz / length * 0.4
        else:
            next_float = getattr(self._rng, "next_float", None)
            roll = float(next_float()) if callable(next_float) else self._rng.random()
            self.attacked_at_yaw = 0.0 if roll < 0.5 else 180.0
            self.vel[0] *= 0.5
            self.vel[1] = min(0.4, self.vel[1] * 0.5 + 0.4)
            self.vel[2] *= 0.5

        next_float = getattr(self._rng, "next_float", None)
        if callable(next_float):
            pitch = (next_float() - next_float()) * 0.2 + 1.0
        else:
            pitch = (self._rng.random() - self._rng.random()) * 0.2 + 1.0
        self._play_world_sound(self.HURT_SOUND, volume=1.0, pitch=pitch, position=self.pos)
        return True

    def on_fishing_hook_hit(self, attacker=None) -> bool:
        return self.on_zero_damage_projectile_hit(attacker)

    def on_fireball_hit(self, attacker=None) -> bool:
        return self.on_zero_damage_projectile_hit(attacker)

    def _is_touching_fire(self) -> bool:
        world = Entity.world
        if world is None or not hasattr(world, "get_block_type"):
            return False
        for bx, by, bz in self._block_positions_for_aabb(*self.pos):
            if world.get_block_type(bx, by, bz) == "fire":
                return True
        return False

    def _inside_opaque_block(self) -> bool:
        world = Entity.world
        if world is None or not hasattr(world, "get_block_type"):
            return False
        eye_y = self.pos[1] + self.size[1] * 0.85
        bx = math.floor(self.pos[0] + 0.5)
        by = math.floor(eye_y + 0.5)
        bz = math.floor(self.pos[2] + 0.5)
        block_type = world.get_block_type(bx, by, bz)
        if block_type is None:
            return False
        try:
            from game.block.material import material_for_block
            mat = material_for_block(block_type)
            if mat is not None and getattr(mat, "is_solid", False) and not getattr(mat, "is_translucent", False):
                if block_type in (
                    "oak_stairs", "cobblestone_stairs", "brick_stairs",
                    "stone_slab", "oak_slab", "farmland", "snow", "ladder",
                    "torch", "fire", "rail", "redstone_ore", "door",
                    "wooden_door", "iron_door", "sign", "wall_sign",
                ):
                    return False
                return True
        except Exception:
            pass
        solidish = {
            "stone", "grass_block", "dirt", "cobblestone", "planks", "bedrock",
            "sand", "gravel", "log", "leaves", "glass", "wool", "bricks",
            "mossy_cobblestone", "obsidian", "iron_block", "gold_block",
            "diamond_block", "furnace", "lit_furnace", "crafting_table",
            "chest", "mob_spawner",
        }
        return block_type in solidish

    def can_breathe_underwater(self) -> bool:
        return False

    def _tick_living_hazards(self, dt: float) -> None:
        if self.health <= 0 or self.dead:
            return
        self._survival_tick_accum += min(max(float(dt), 0.0), 0.25)
        ticks = 0
        while self._survival_tick_accum >= 0.05 and ticks < 4:
            self._survival_tick_accum -= 0.05
            ticks += 1

            if self.submerged and not self.can_breathe_underwater():
                self.air -= 1
                if self.air <= -20:
                    self.air = 0
                    self.attack_entity_from(None, 2)
            else:
                self.air = self.max_air

            touching_fire = self._is_touching_fire()
            if self.FIRE_IMMUNE:
                self._fire_ticks = 0
            elif self.in_water:
                self._fire_ticks = 0
            elif self.in_lava:
                self._fire_ticks = 600
                self.attack_entity_from(None, 1)
            else:
                if self._fire_ticks > 0 and self._fire_ticks % 20 == 0:
                    self.attack_entity_from(None, 1)
                if touching_fire:
                    self.attack_entity_from(None, 1)
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
                self.attack_entity_from(None, 1)

    def _update_fall_distance(self, dy: float, was_on_ground: bool) -> None:
        if self.on_ladder or self.in_water:
            self._fall_distance = 0.0
            self._prev_on_ground_for_fall = self.on_ground
            return

        if self.on_ground and not was_on_ground:
            if self._fall_distance > 3.0:
                amount = int(math.ceil(self._fall_distance - 3.0))
                if amount > 0:
                    self.attack_entity_from(None, amount)
                    try:
                        world = Entity.world
                        if world is not None:
                            bx = math.floor(self.pos[0] + 0.5)
                            by = math.floor(self.pos[1] - 0.2 + 0.5)
                            bz = math.floor(self.pos[2] + 0.5)
                            block_type = world.get_block_type(bx, by, bz)
                            if block_type and block_type not in (
                                "water", "flowing_water", "lava", "flowing_lava",
                            ):
                                self._play_footstep()
                    except Exception:
                        pass
            self._fall_distance = 0.0
        elif not self.on_ground and dy < 0.0:
            self._fall_distance += -dy

        self._prev_on_ground_for_fall = self.on_ground

    def _apply_collision_to(self, other) -> None:
        self.apply_entity_collision(other)

    def _apply_living_entity_collisions(self) -> None:
        self._apply_entity_collisions()

    def _play_footstep(self) -> None:
        world = Entity.world
        if world is None:
            return
        bx = math.floor(self.pos[0] + 0.5)
        by = math.floor(self.pos[1] - 0.2 + 0.5)
        bz = math.floor(self.pos[2] + 0.5)
        block_type = world.get_block_type(bx, by, bz) if hasattr(world, "get_block_type") else None
        if block_type is None:
            return
        try:
            from game.block.material import material_for_block, step_sound_for_block
            material = material_for_block(block_type)
            if material.is_liquid:
                return
            step_sound = step_sound_for_block(block_type)
            step_name = step_sound.name
            step_volume = step_sound.volume * 0.15
            step_pitch = step_sound.pitch
        except Exception:
            if block_type in ("water", "flowing_water", "lava", "flowing_lava"):
                return
            step_name = {
                "grass_block": "grass", "yellow_flower": "grass",
                "red_flower": "grass", "sapling": "grass", "leaves": "grass",
                "dirt": "gravel", "gravel": "gravel", "sand": "sand",
                "planks": "wood", "log": "wood", "crafting_table": "wood",
                "chest": "wood", "wooden_door": "wood", "sign": "wood",
                "stone": "stone", "cobblestone": "stone", "mossy_cobblestone": "stone",
                "bricks": "stone", "bedrock": "stone", "furnace": "stone",
                "mob_spawner": "stone", "iron_block": "metal", "gold_block": "metal",
                "glass": "glass", "wool": "cloth",
            }.get(block_type, "powder")
            step_volume, step_pitch = 0.15, 1.0
        self._play_world_sound(
            f"step.{step_name}", volume=step_volume,
            pitch=step_pitch, position=self.pos,
        )

    def _advance_living_visual_tick(self, dx: float, dz: float, travelled: float) -> None:
        self.prev_limb_swing_amount = self.limb_swing_amount
        self.prev_limb_swing = self.limb_swing
        target_amount = min(1.0, max(0.0, travelled * 4.0))
        self.limb_swing_amount += (target_amount - self.limb_swing_amount) * 0.4
        self.limb_swing += self.limb_swing_amount
        self.prev_render_yaw_offset = self.render_yaw_offset
        if travelled > 0.05:
            movement_yaw = math.degrees(math.atan2(-dx, -dz))
            delta = _wrap_degrees(movement_yaw - self.render_yaw_offset)
            self.render_yaw_offset += delta * 0.1

        head_delta = _wrap_degrees(self.yaw - self.render_yaw_offset)
        head_delta = max(-75.0, min(75.0, head_delta))
        self.render_yaw_offset = self.yaw - head_delta
        self.render_yaw_offset += head_delta * 0.1

        while self.render_yaw_offset - self.prev_render_yaw_offset < -180.0:
            self.prev_render_yaw_offset -= 360.0
        while self.render_yaw_offset - self.prev_render_yaw_offset >= 180.0:
            self.prev_render_yaw_offset += 360.0

        self.distance_walked_modified += travelled * 0.6
        if self.on_ground and self.distance_walked_modified > self.next_step_distance:
            self.next_step_distance += 1
            self._play_footstep()

    def _update_while_mounted(self, mount, dt: float) -> None:
        dt = min(max(float(dt), 0.0), MAX_DT)
        if dt <= 0.0:
            position_rider = getattr(mount, "_position_rider", None)
            if callable(position_rider):
                position_rider()
            return

        last_pos = getattr(self, "_mounted_visual_pos", None)
        if last_pos is None:
            last_pos = tuple(self.pos)

        self.prev_yaw = self.yaw
        self.prev_rotation_pitch = self.rotation_pitch
        self.prev_render_yaw_offset = self.render_yaw_offset
        self.prev_limb_swing = self.limb_swing
        self.prev_limb_swing_amount = self.limb_swing_amount

        self._tick_accum += dt
        tick_count = min(int(self._tick_accum / self.AI_TICK_SECONDS), 4)
        if tick_count:
            self._tick_accum -= tick_count * self.AI_TICK_SECONDS
            for _ in range(tick_count):
                self._living_ai_tick()
                self.vel[:] = [0.0, 0.0, 0.0]
            while self.yaw - self.prev_yaw < -180.0:
                self.prev_yaw -= 360.0
            while self.yaw - self.prev_yaw >= 180.0:
                self.prev_yaw += 360.0

        position_rider = getattr(mount, "_position_rider", None)
        if callable(position_rider):
            position_rider()

        current_pos = tuple(float(value) for value in self.pos)
        dx = current_pos[0] - float(last_pos[0])
        dz = current_pos[2] - float(last_pos[2])
        travelled = math.hypot(dx, dz)
        self._mounted_visual_pos = current_pos
        self.prev_pos = list(current_pos)

        if tick_count:
            old_on_ground = self.on_ground
            self.on_ground = False
            per_tick = 1.0 / tick_count
            for _ in range(tick_count):
                self._advance_living_visual_tick(
                    dx * per_tick, dz * per_tick, travelled * per_tick,
                )
            self.on_ground = old_on_ground

        self.vel[:] = [0.0, 0.0, 0.0]
        self.on_ground = False
        self._fall_distance = 0.0
        self._fall_pending = 0.0
        self.distance_walked_modified = 0.0
        self._frame_distance_accum = 0.0
        self._frame_dx_accum = 0.0
        self._frame_dz_accum = 0.0

    def update(self, dt):
        if self.dead or Entity.world is None:
            return
        mount = getattr(self, "riding_entity", None)
        if mount is not None:
            if (getattr(mount, "dead", False)
                    or getattr(mount, "rider", None) is not self
                    or float(getattr(self, "health", 1)) <= 0.0):
                dismount = getattr(mount, "dismount", None)
                if callable(dismount) and getattr(mount, "rider", None) is self:
                    dismount()
                else:
                    self.riding_entity = None
            else:
                self._update_while_mounted(mount, dt)
                return
        dt = min(max(float(dt), 0.0), MAX_DT)
        if dt <= 0.0:
            return

        self._tick_accum += dt
        tick_count = min(int(self._tick_accum / self.AI_TICK_SECONDS), 4)
        if tick_count:
            self._tick_accum -= tick_count * self.AI_TICK_SECONDS
            self.prev_pos = list(self.pos)
            self.prev_yaw = self.yaw
            self.prev_rotation_pitch = self.rotation_pitch
            for _ in range(tick_count):
                self._living_ai_tick()
            while self.yaw - self.prev_yaw < -180.0:
                self.prev_yaw -= 360.0
            while self.yaw - self.prev_yaw >= 180.0:
                self.prev_yaw += 360.0
            if not bool(getattr(self, "MANAGES_AI_ORIENTATION", False)):
                pending = getattr(self, "_pending_path_yaw", None)
                if pending is not None:
                    turn = self._wrap_degrees(float(pending) - self.yaw)
                    if turn > 30.0:
                        turn = 30.0
                    elif turn < -30.0:
                        turn = -30.0
                    self.yaw += turn
                    self._pending_path_yaw = None
                    self.rotation_pitch = self._update_rotation(
                        self.rotation_pitch, self.default_pitch, float(self.FACE_PITCH_SPEED),
                    )
                elif self._look_target is not None:
                    self._apply_look_rotation(1.0)
                else:
                    self.yaw += self.random_yaw_velocity
                    self.random_yaw_velocity *= 0.9
                    self.rotation_pitch = self._update_rotation(
                        self.rotation_pitch, self.default_pitch, float(self.FACE_PITCH_SPEED),
                    )

        self._update_heading_acceleration(dt * TICKS_PER_SECOND)
        was_on_ground = bool(self.on_ground)
        old_pos = tuple(self.pos)
        super().update(dt)
        dy = self.pos[1] - old_pos[1]
        dx = self.pos[0] - old_pos[0]
        dz = self.pos[2] - old_pos[2]
        travelled = math.hypot(dx, dz)
        self._frame_distance_accum += travelled
        self._frame_dx_accum += dx
        self._frame_dz_accum += dz

        self._tick_living_hazards(dt)
        self._update_fall_distance(dy, was_on_ground)

        if tick_count:
            per_tick_distance = self._frame_distance_accum / tick_count
            per_tick_dx = self._frame_dx_accum / tick_count
            per_tick_dz = self._frame_dz_accum / tick_count
            self._frame_distance_accum = 0.0
            self._frame_dx_accum = 0.0
            self._frame_dz_accum = 0.0
            for _ in range(tick_count):
                self._advance_living_visual_tick(per_tick_dx, per_tick_dz, per_tick_distance)
                self._apply_living_entity_collisions()

    def draw(self):
        if self.dead or Entity.renderer is None:
            return

        world = Entity.world
        partial = 0.0
        if world is not None:
            try:
                partial = max(0.0, min(1.0, float(world.partial_ticks())))
            except Exception:
                partial = 0.0

        x, y, z = (float(v) for v in self.pos)
        body_yaw = self.prev_render_yaw_offset + _wrap_degrees(
            self.render_yaw_offset - self.prev_render_yaw_offset
        ) * partial
        head_yaw = self.prev_yaw + _wrap_degrees(self.yaw - self.prev_yaw) * partial
        prev_pitch = float(getattr(self, "prev_rotation_pitch", self.rotation_pitch))
        head_pitch = prev_pitch + (float(self.rotation_pitch) - prev_pitch) * partial

        try:
            from game.entities.entity_shadow import (
                SHADOW_SIZE_LIVING, render_entity_shadow, shadow_opacity_for_distance,
            )
            opacity = shadow_opacity_for_distance(world, x, y, z)
            if opacity > 0.0:
                render_entity_shadow(world, x, y, z, SHADOW_SIZE_LIVING, opacity=opacity)
        except Exception:
            pass

        from game.entities import player_model
        player_model.render_player_model(
            self, x, y - self.render_y_offset, z, body_yaw, partial,
            head_yaw=head_yaw, head_pitch=head_pitch,
        )
        if int(getattr(self, "_fire_ticks", 0)) > 0:
            player_model.render_player_fire_effect(
                self, x, y - self.render_y_offset, z,
            )
