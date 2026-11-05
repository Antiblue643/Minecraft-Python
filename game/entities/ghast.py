from __future__ import annotations

import math

from .entity import Entity, MAX_DT, TICKS_PER_SECOND
from .monster import Monster
from .mob import Mob


class Ghast(Monster):
    WIDTH = 4.0
    HEIGHT = 4.0
    HEALTH = 10
    ATTACK_STRENGTH = 0
    FIRE_IMMUNE = True
    DROP_ITEM = "gunpowder"
    LIVING_SOUND = "mob.ghast.moan"
    HURT_SOUND = "mob.ghast.scream"
    DEATH_SOUND = "mob.ghast.death"
    TARGET_RANGE = 100.0
    FIRE_RANGE = 64.0
    SHADOW_SIZE = 0.5
    MAX_SPAWN_GROUP = 1

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.render_yaw_offset = 180.0 - self.yaw
        self.prev_render_yaw_offset = self.render_yaw_offset
        self.size = (self.WIDTH, self.HEIGHT, self.WIDTH)
        self.skin_path = "mob/ghast.png"
        self.shadow_size = self.SHADOW_SIZE
        self.health = self.HEALTH
        self.prev_health = self.HEALTH
        self.attack_strength = 0
        self.field_4121_a = 0  
        self.waypoint = [float(v) for v in self.pos]
        self.target_player = None
        self.target_refresh_ticks = 0
        self.prev_charge = 0
        self.charge = 0
        self._tick_accum = 0.0
        self._living_fluid_physics = False
        self._ghast_ai_enabled = True

    @property
    def is_charging(self):
        return self.charge > 10

    def _difficulty(self) -> int:
        return super()._difficulty()

    def _nearest_player(self, max_distance: float):
        best = None
        best_distance_sq = float(max_distance) ** 2
        for player in self._world_players():
            if not self._is_alive(player):
                continue
            dx = float(player.pos[0]) - self.pos[0]
            dy = float(player.pos[1]) - self.pos[1]
            dz = float(player.pos[2]) - self.pos[2]
            distance_sq = dx * dx + dy * dy + dz * dz
            if distance_sq < best_distance_sq:
                best, best_distance_sq = player, distance_sq
        return best

    def _path_is_clear(self, target, distance: float) -> bool:
        if distance <= 1.0:
            return True
        dx = (float(target[0]) - self.pos[0]) / distance
        dy = (float(target[1]) - self.pos[1]) / distance
        dz = (float(target[2]) - self.pos[2]) / distance
        for step in range(1, int(distance)):
            px = self.pos[0] + dx * step
            py = self.pos[1] + dy * step
            pz = self.pos[2] + dz * step
            if self._collides(px, py, pz):
                return False
        return True

    def _next_float(self):
        next_float = getattr(self._rng, "next_float", None)
        return float(next_float()) if callable(next_float) else float(self._rng.random())

    def _next_int(self, bound):
        next_int = getattr(self._rng, "next_int", None)
        return int(next_int(bound)) if callable(next_int) else int(self._rng.randrange(bound))

    def _random_waypoint(self):
        return [
            self.pos[0] + (self._next_float() * 2.0 - 1.0) * 16.0,
            self.pos[1] + (self._next_float() * 2.0 - 1.0) * 16.0,
            self.pos[2] + (self._next_float() * 2.0 - 1.0) * 16.0,
        ]

    def _play_ghast_sound(self, name):
        pitch = (self._next_float() - self._next_float()) * 0.2 + 1.0
        self._play_world_sound(name, volume=10.0, pitch=pitch, position=self.pos)

    def _tick_living_sound(self) -> None:
        if self._next_int(1000) < self._living_sound_time:
            self._living_sound_time = -80
            if self.LIVING_SOUND:
                self._play_ghast_sound(self.LIVING_SOUND)
        self._living_sound_time += 1

    def _launch_fireball(self, dx: float, dy: float, dz: float, target=None):
        from .fireball import FireballEntity

        world = Entity.world
        if world is None or not hasattr(world, "spawn_entity"):
            return None
        yaw = math.radians(float(self.yaw))
        look_x, look_z = -math.sin(yaw), math.cos(yaw)
        muzzle = (
            self.pos[0] + look_x * 4.0,
            self.pos[1] + self.size[1] * 0.5 + 0.5,
            self.pos[2] + look_z * 4.0,
        )
        if target is not None:
            target_pos = getattr(target, "pos", self.pos)
            target_box = getattr(target, "get_bounding_box", None)
            if callable(target_box):
                try:
                    bounds = target_box()
                    target_min_y = float(bounds[1])
                except Exception:
                    target_min_y = float(target_pos[1])
            else:
                target_min_y = float(target_pos[1])
            target_height = float(getattr(target, "size", (0.6, 1.8, 0.6))[1])
            aim = (
                float(target_pos[0]) - muzzle[0],
                target_min_y + target_height * 0.5 - muzzle[1],
                float(target_pos[2]) - muzzle[2],
            )
        else:
            aim = (dx, dy, dz)
        projectile = FireballEntity.from_ghast(
            world, self, muzzle, aim, rng=self._rng,
        )
        world.spawn_entity(projectile)
        return projectile

    def _tick_flight_and_attack(self) -> None:
        world = Entity.world
        if world is None:
            return

        self.prev_charge = self.charge
        if self._difficulty() == 0:
            self.dead = True
            self.health = 0
            self.player_to_attack = None
            return

        dx = self.waypoint[0] - self.pos[0]
        dy = self.waypoint[1] - self.pos[1]
        dz = self.waypoint[2] - self.pos[2]
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if distance < 1.0 or distance > 60.0:
            self.waypoint = self._random_waypoint()
            dx = self.waypoint[0] - self.pos[0]
            dy = self.waypoint[1] - self.pos[1]
            dz = self.waypoint[2] - self.pos[2]
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)

        self.field_4121_a -= 1
        if self.field_4121_a <= 0:
            self.field_4121_a += self._next_int(5) + 2
            if distance > 1.0e-9 and self._path_is_clear(self.waypoint, distance):
                self.vel[0] += dx / distance * 0.1
                self.vel[1] += dy / distance * 0.1
                self.vel[2] += dz / distance * 0.1
            else:
                self.waypoint = list(self.pos)

        if self.target_player is not None and not self._is_alive(self.target_player):
            self.target_player = None
        self.target_refresh_ticks -= 1
        if self.target_player is None or self.target_refresh_ticks <= 0:
            self.target_player = self._nearest_player(self.TARGET_RANGE)
            if self.target_player is not None:
                self.target_refresh_ticks = 20

        player = self.target_player
        if player is not None:
            px, py, pz = (float(v) for v in player.pos)
            dx = px - self.pos[0]
            dy = py + float(player.size[1]) * 0.5 - (self.pos[1] + self.size[1] * 0.5)
            dz = pz - self.pos[2]
            range_dx = px - self.pos[0]
            range_dy = py - self.pos[1]
            range_dz = pz - self.pos[2]
            distance_sq = range_dx * range_dx + range_dy * range_dy + range_dz * range_dz
            if distance_sq < self.FIRE_RANGE * self.FIRE_RANGE:
                self.yaw = -math.degrees(math.atan2(dx, dz))
                if self.can_entity_be_seen(player):
                    if self.charge == 10:
                        self._play_ghast_sound("mob.ghast.charge")
                    self.charge += 1
                    if self.charge == 20:
                        self._play_ghast_sound("mob.ghast.fireball")
                        self._launch_fireball(dx, dy, dz, target=player)
                        self.charge = -40
                elif self.charge > 0:
                    self.charge -= 1
            else:
                self._face_motion()
                if self.charge > 0:
                    self.charge -= 1
        else:
            self._face_motion()
            if self.charge > 0:
                self.charge -= 1

        self.skin_path = "mob/ghast_fire.png" if self.charge > 10 else "mob/ghast.png"
        self.render_yaw_offset = 180.0 - self.yaw

    def _face_motion(self):
        if abs(self.vel[0]) + abs(self.vel[2]) > 1.0e-9:
            self.yaw = -math.degrees(math.atan2(self.vel[0], self.vel[2]))

    def _living_ai_tick(self) -> None:
        self.ticks_existed += 1
        self.hearts_life = max(0, self.hearts_life - 1)
        self.hurt_time = max(0, self.hurt_time - 1)
        self.attack_time = max(0, self.attack_time - 1)
        self._tick_living_sound()
        if self.health <= 0:
            self.player_to_attack = None
            self.death_time += 1
            if self.death_time > 20:
                if not self._death_particles_spawned:
                    self._death_particles_spawned = True
                    self.spawn_explosion_particles()
                self.dead = True
            return
        if Mob.global_ai_paused or type(self).ai_frozen:
            return
        self.entity_age += 1
        self._tick_despawn()
        if not self.dead:
            self._tick_flight_and_attack()

    def update(self, dt):
        if self.dead or Entity.world is None:
            return
        dt = min(max(float(dt), 0.0), MAX_DT)
        if dt <= 0.0:
            return
        ticks = dt * TICKS_PER_SECOND
        self._tick_accum += dt
        tick_count = min(int(self._tick_accum / (1.0 / TICKS_PER_SECOND)), 4)
        if tick_count:
            self._tick_accum -= tick_count / TICKS_PER_SECOND
            self.prev_pos = list(self.pos)
            self.prev_yaw = self.yaw
            self.prev_render_yaw_offset = self.render_yaw_offset
            for _ in range(tick_count):
                self._living_ai_tick()
                if self.dead:
                    return

        self._check_water()
        old = tuple(self.pos)
        want = (self.vel[0] * ticks, self.vel[1] * ticks, self.vel[2] * ticks)
        self.move(*want)
        moved = tuple(self.pos[i] - old[i] for i in range(3))
        if abs(moved[0] - want[0]) > 1.0e-8:
            self.vel[0] = 0.0
        if abs(moved[1] - want[1]) > 1.0e-8:
            self.vel[1] = 0.0
        if abs(moved[2] - want[2]) > 1.0e-8:
            self.vel[2] = 0.0
        drag = (0.8 if self.in_water else 0.91) ** ticks
        self.vel[0] *= drag
        self.vel[1] *= drag
        self.vel[2] *= drag
        self._tick_living_hazards(dt)
        self.render_yaw_offset = 180.0 - self.yaw

    def get_can_spawn_here(self, x: float, y: float, z: float) -> bool:
        world = Entity.world
        if world is None:
            return False
        if self._next_int(20) != 0 or self._difficulty() <= 0:
            return False
        provider = getattr(world, "world_provider", None) or getattr(world, "dimension", None)
        is_hell = bool(getattr(provider, "is_hell_world", False))
        if provider is not None:
            name = str(getattr(provider, "name", "")).lower()
            is_hell = is_hell or name in ("hell", "nether")
        dimension_id = getattr(world, "dimension_id", None)
        is_hell = is_hell or dimension_id == -1
        if not is_hell:
            return False
        self.pos = [float(x), float(y), float(z)]
        self.prev_pos = list(self.pos)
        return (
            self._aabb_clear_of_spawn_blockers(*self.pos)
            and not self._collides(*self.pos)
            and not self._overlaps_any_liquid(*self.pos)
        )

    def draw(self):
        from .mob_models import draw_living_with_model
        draw_living_with_model(self, "ghast")


EntityGhast = Ghast
