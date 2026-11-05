
import math
import random

from game.entities.arrow import ArrowEntity
from game.entities.entity import Entity, MAX_DT, TICKS_PER_SECOND
from game.entities.item import ItemRendererMixin, ItemStack


class SnowballEntity(ItemRendererMixin, Entity):

    MAX_AGE_TICKS = 1200.0

    _block_type = ArrowEntity._block_type
    _find_block_hit = ArrowEntity._find_block_hit
    _entity_bounds = staticmethod(ArrowEntity._entity_bounds)
    _find_entity_hit = ArrowEntity._find_entity_hit
    _play_sound = ArrowEntity._play_sound
    _medium_flags = ArrowEntity._medium_flags

    def __init__(self, world, pos, velocity=(0.0, 0.0, 0.0), shooting_entity=None):
        super().__init__(pos, (0.25, 0.25, 0.25))
        self.world_obj = world
        self.shooting_entity = shooting_entity
        self.vel = [float(velocity[0]), float(velocity[1]), float(velocity[2])]
        self.prev_pos = list(self.pos)
        self.yaw = self.prev_yaw = math.degrees(math.atan2(self.vel[0], self.vel[2])) if any(self.vel) else 0.0
        horizontal = math.hypot(self.vel[0], self.vel[2])
        self.pitch = self.prev_pitch = math.degrees(math.atan2(self.vel[1], horizontal)) if any(self.vel) else 0.0
        self.ticks_in_air = 0.0

        self.item = ItemStack("snowball", 1)
        self._is_projectile_billboard = True
        self.age = 0.0
        self.hover_start = 0.0
        self.pickup_ticks = None
        self._pickup_from = None
        self._collector = None
        self._burning_ticks = 0.0
        self._draw_brightness = 1.0
        self._is_pressure_plate = False
        self._is_button = False
        self._is_stairs = False
        self._is_block = False

    @classmethod
    def from_shooter(cls, world, shooter):
        yaw = math.radians(float(shooter.yaw))
        pitch = math.radians(float(shooter.pitch))
        direction = (
            -math.sin(yaw) * math.cos(pitch),
            math.sin(pitch),
            -math.cos(yaw) * math.cos(pitch),
        )
        pos = (
            shooter.pos[0] + math.cos(yaw) * 0.16,
            shooter.pos[1] + float(getattr(shooter, "EYE_HEIGHT", 1.62)) - 0.1,
            shooter.pos[2] - math.sin(yaw) * 0.16,
        )
        norm = math.sqrt(sum(v * v for v in direction)) or 1.0
        perturbed = [v / norm + random.gauss(0.0, 0.0075) for v in direction]
        norm = math.sqrt(sum(v * v for v in perturbed)) or 1.0
        velocity = tuple(v / norm * 1.5 for v in perturbed)
        return cls(world, pos, velocity=velocity, shooting_entity=shooter)

    def draw(self):
        ItemRendererMixin.draw(self)

    def _spawn_water_particles(self):
        return

    def _spawn_impact_particles(self):
        spawn = getattr(self.world_obj, "spawn_particle", None)
        if not callable(spawn):
            return
        x, y, z = self.pos
        for _ in range(8):
            spawn(
                "snowballpoof", x, y, z,
                random.gauss(0.0, 0.02),
                random.gauss(0.0, 0.02),
                random.gauss(0.0, 0.02),
            )

    def _hit_entity(self, target, ticks):
        attacker = self.shooting_entity
        attack = getattr(target, "attack_entity_from", None)
        if not callable(attack):
            attack = getattr(target, "attackEntityFrom", None)
        if callable(attack):
            try:
                attack(attacker, 0)
            except TypeError:
                try:
                    attack(attacker, 0, source="projectile")
                except TypeError:
                    pass
        else:
            hit_callback = getattr(target, "on_zero_damage_projectile_hit", None)
            if not callable(hit_callback):
                hit_callback = getattr(target, "on_fishing_hook_hit", None)
            if callable(hit_callback):
                hit_callback(attacker)
        self._spawn_impact_particles()
        self.dead = True

    def update(self, dt):
        if self.dead or self.world_obj is None or self.pos[1] < -64.0:
            self.dead = True
            return
        ticks = min(max(float(dt), 0.0), MAX_DT) * TICKS_PER_SECOND
        if ticks <= 0.0:
            return
        self.age += ticks
        self.prev_pos[:] = self.pos
        self.prev_yaw, self.prev_pitch = self.yaw, self.pitch
        self.ticks_in_air += ticks

        start = tuple(self.pos)
        end = tuple(self.pos[i] + self.vel[i] * ticks for i in range(3))
        block_hit = self._find_block_hit(start, end)
        block_t = block_hit[0] if block_hit is not None else 1.0
        blocked_end = tuple(start[i] + (end[i] - start[i]) * block_t for i in range(3))
        entity_hit = self._find_entity_hit(start, blocked_end, self.ticks_in_air)

        if entity_hit is not None:
            entity_t, _target = entity_hit
            fraction = block_t * entity_t
            self.pos[:] = [start[i] + (end[i] - start[i]) * fraction for i in range(3)]
            self._hit_entity(_target, 0.0)
            return
        if block_hit is not None:
            t = block_hit[0]
            self.pos[:] = [start[i] + (end[i] - start[i]) * t for i in range(3)]
            self._spawn_impact_particles()
            self.dead = True
            return

        self.pos[:] = end
        in_water, in_lava, _touching_fire = self._medium_flags()
        self.in_water = in_water
        self.in_lava = in_lava
        if in_water:
            self._spawn_water_particles()
        drag = (0.8 if in_water else 0.99) ** ticks
        self.vel[0] *= drag
        self.vel[1] = self.vel[1] * drag - 0.03 * ticks
        self.vel[2] *= drag

        if any(abs(v) > 1.0e-12 for v in self.vel):
            yaw = math.degrees(math.atan2(self.vel[0], self.vel[2]))
            horizontal = math.hypot(self.vel[0], self.vel[2])
            pitch = math.degrees(math.atan2(self.vel[1], horizontal))
            yaw_delta = (yaw - self.prev_yaw + 180.0) % 360.0 - 180.0
            pitch_delta = (pitch - self.prev_pitch + 180.0) % 360.0 - 180.0
            blend = 1.0 - (1.0 - 0.2) ** ticks
            self.yaw = self.prev_yaw + yaw_delta * blend
            self.pitch = self.prev_pitch + pitch_delta * blend

        if self.MAX_AGE_TICKS is not None and self.age >= float(self.MAX_AGE_TICKS):
            self.dead = True
