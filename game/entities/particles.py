from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import List, Optional


from engine.lighting import brightness_from_level
from engine.textures import Texture
from game.block import Block
from .particle_renderer import ParticleRendererMixin, DiggingParticleRendererMixin, EffectRendererMixin

def _load_particle_config():
    path = Path(__file__).resolve().parents[1] / "data" / "particles.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


_PARTICLE_CONFIG = _load_particle_config()
_FLUID_BLOCK_TYPES = frozenset(_PARTICLE_CONFIG.get("fluid_block_types", ()))
_RAIN_CONFIG = _PARTICLE_CONFIG.get("rain", {})
_PARTICLE_SPAWN_DEFS = _PARTICLE_CONFIG.get("spawn_types", {})


def particle_is_inside_fluid(particle, world, partial: float = 0.0) -> bool:
    if world is None or not hasattr(world, "get_block_type"):
        return False
    try:
        t = min(1.0, max(0.0, float(partial)))
        x = particle.prev_pos[0] + (particle.pos[0] - particle.prev_pos[0]) * t
        y = particle.prev_pos[1] + (particle.pos[1] - particle.prev_pos[1]) * t
        z = particle.prev_pos[2] + (particle.pos[2] - particle.prev_pos[2]) * t
        block_type = world.get_block_type(
            math.floor(x + 0.5), math.floor(y + 0.5), math.floor(z + 0.5)
        )
        return block_type in _FLUID_BLOCK_TYPES
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def spawn_living_death_particles(world, pos, size, rng=None) -> int:
    spawn = getattr(world, "spawn_particle", None) if world is not None else None
    if not callable(spawn):
        return 0
    rng = rng or random
    x0, y0, z0 = (float(v) for v in pos)
    width = float(size[0])
    height = float(size[1])
    emitted = 0
    for _ in range(20):
        mx = rng.gauss(0.0, 0.02)
        my = rng.gauss(0.0, 0.02)
        mz = rng.gauss(0.0, 0.02)
        x = x0 + rng.random() * width * 2.0 - width
        y = y0 + rng.random() * height
        z = z0 + rng.random() * width * 2.0 - width
        spawn("explode", x, y, z, mx, my, mz)
        emitted += 1
    return emitted


def _block_texture_index(block_type: str) -> int:
    defn = Block._block_defs.get(block_type) or {}
    particle_texture = defn.get("particleTexture")
    if particle_texture is not None:
        return int(particle_texture)
    faces = defn.get("faces", 0)
    if isinstance(faces, list):
        if len(faces) > 2:
            return int(faces[2])
        return int(faces[0]) if faces else 0
    return int(faces) if faces else 0


class Particle(ParticleRendererMixin):
    __slots__ = (
        "pos", "prev_pos", "motion", "age", "max_age", "dead",
        "scale", "gravity", "red", "green", "blue",
        "tex_index", "jitter_x", "jitter_y", "on_ground",
        "no_clip", "collision_radius",
    )

    def __init__(self, x, y, z, mx, my, mz):
        self.pos = [float(x), float(y), float(z)]
        self.prev_pos = [float(x), float(y), float(z)]
        self.motion = [
            mx + (random.random() * 2.0 - 1.0) * 0.4,
            my + (random.random() * 2.0 - 1.0) * 0.4,
            mz + (random.random() * 2.0 - 1.0) * 0.4,
        ]
        speed = math.sqrt(self.motion[0] ** 2 + self.motion[1] ** 2 + self.motion[2] ** 2)
        spread = (random.random() + random.random() + 1.0) * 0.15
        if speed < 1e-6:
            speed = 1.0
        self.motion[0] = self.motion[0] / speed * spread * 0.4
        self.motion[1] = self.motion[1] / speed * spread * 0.4 + 0.1
        self.motion[2] = self.motion[2] / speed * spread * 0.4
        self.jitter_x = random.random() * 3.0
        self.jitter_y = random.random() * 3.0
        self.scale = (random.random() * 0.5 + 0.5) * 2.0
        self.max_age = int(4.0 / (random.random() * 0.9 + 0.1))
        self.age = 0
        self.dead = False
        self.gravity = 1.0
        self.red = self.green = self.blue = 1.0
        self.tex_index = 0
        self.on_ground = False
        self.no_clip = False
        self.collision_radius = 0.1

    def get_brightness(self, world, cache=None) -> float:
        if world is None:
            return 1.0
        key = (math.floor(self.pos[0] + 0.5), math.floor(self.pos[1] + 0.5),
               math.floor(self.pos[2] + 0.5))
        if cache is not None and key in cache:
            return cache[key]
        try:
            value = brightness_from_level(world.get_light(*key), hell=bool(getattr(world, "is_hell_world", False)))
        except Exception:
            value = 1.0
        if cache is not None:
            cache[key] = value
        return value

    def multiply_velocity(self, factor: float) -> "Particle":
        self.motion[0] *= factor
        self.motion[1] = (self.motion[1] - 0.1) * factor + 0.1
        self.motion[2] *= factor
        return self

    def multiply_scale(self, factor: float) -> "Particle":
        self.scale *= factor
        return self

    def fx_layer(self) -> int:
        return 0

    def update(self, world=None):
        self.prev_pos[0] = self.pos[0]
        self.prev_pos[1] = self.pos[1]
        self.prev_pos[2] = self.pos[2]
        self.age += 1
        if self.age >= self.max_age:
            self.dead = True
            return
        self.motion[1] -= 0.04 * self.gravity
        self._move(world)
        self.motion[0] *= 0.98
        self.motion[1] *= 0.98
        self.motion[2] *= 0.98
        if self.on_ground:
            self.motion[0] *= 0.7
            self.motion[2] *= 0.7

    def _move(self, world):
        old_x, old_y, old_z = self.pos
        dx, dy, dz = self.motion
        if world is None or self.no_clip:
            self.pos[0] = old_x + dx
            self.pos[1] = old_y + dy
            self.pos[2] = old_z + dz
            self.on_ground = False
            return

        radius = float(self.collision_radius)
        self.on_ground = False

        def overlaps_box(px, py, pz, box):
            return (
                px + radius > box[0] and px - radius < box[3]
                and py + radius > box[1] and py - radius < box[4]
                and pz + radius > box[2] and pz - radius < box[5]
            )

        def boxes_near(px, py, pz):
            x0 = math.floor(px - radius + 0.5)
            x1 = math.floor(px + radius + 0.5)
            y0 = math.floor(py - radius + 0.5)
            y1 = math.floor(py + radius + 0.5)
            z0 = math.floor(pz - radius + 0.5)
            z1 = math.floor(pz + radius + 0.5)
            for bx in range(x0, x1 + 1):
                for by in range(y0, y1 + 1):
                    for bz in range(z0, z1 + 1):
                        try:
                            block = world.get_block(bx, by, bz) if hasattr(world, "get_block") else None
                            if block is not None:
                                if getattr(block, "collision", True) is False:
                                    continue
                                local_boxes = (block.get_collision_boxes()
                                               if hasattr(block, "get_collision_boxes")
                                               else [block.get_collision_bounds()])
                                local_boxes = [box for box in local_boxes if box is not None]
                            else:
                                block_type = world.get_block_type(bx, by, bz)
                                if block_type is None:
                                    continue
                                definition = Block._block_defs.get(block_type, {})
                                collision = definition.get("collision", True)
                                if collision is False:
                                    continue
                                bounds = definition.get("bounds", (0, 0, 0, 1, 1, 1))
                                local_boxes = [tuple(float(v) - 0.5 for v in bounds)]
                            for local in local_boxes:
                                box = (bx + local[0], by + local[1], bz + local[2],
                                       bx + local[3], by + local[4], bz + local[5])
                                yield box
                        except Exception:
                            continue

        y1 = old_y + dy
        relevant_boxes = list(boxes_near(old_x, y1, old_z))
        resolved_y = y1
        for box in relevant_boxes:
            if not (old_x + radius > box[0] and old_x - radius < box[3]
                    and old_z + radius > box[2] and old_z - radius < box[5]):
                continue
            if dy < 0.0:
                top = box[4]
                old_bottom = old_y - radius
                new_bottom = resolved_y - radius
                if old_bottom >= top - 1e-7 and new_bottom < top:
                    resolved_y = top + radius
                    self.motion[1] = 0.0
                    self.on_ground = True
            elif dy > 0.0:
                bottom = box[1]
                old_top = old_y + radius
                new_top = resolved_y + radius
                if old_top <= bottom + 1e-7 and new_top > bottom:
                    resolved_y = bottom - radius
                    self.motion[1] = 0.0
        self.pos[1] = resolved_y

        x1 = old_x + dx
        if not any(overlaps_box(x1, self.pos[1], old_z, box)
                   and not overlaps_box(old_x, self.pos[1], old_z, box)
                   for box in boxes_near(x1, self.pos[1], old_z)):
            self.pos[0] = x1
        else:
            self.motion[0] = 0.0

        z1 = old_z + dz
        if not any(overlaps_box(self.pos[0], self.pos[1], z1, box)
                   and not overlaps_box(self.pos[0], self.pos[1], old_z, box)
                   for box in boxes_near(self.pos[0], self.pos[1], z1)):
            self.pos[2] = z1
        else:
            self.motion[2] = 0.0

    def _center(self, partial, cam):
        return (
            self.prev_pos[0] + (self.pos[0] - self.prev_pos[0]) * partial - cam[0],
            self.prev_pos[1] + (self.pos[1] - self.prev_pos[1]) * partial - cam[1],
            self.prev_pos[2] + (self.pos[2] - self.prev_pos[2]) * partial - cam[2],
        )



class DiggingParticle(DiggingParticleRendererMixin, Particle):
    def __init__(self, x, y, z, mx, my, mz, block_type: str, color=None):
        super().__init__(x, y, z, mx, my, mz)
        self.tex_index = _block_texture_index(block_type)
        defn = Block._block_defs.get(block_type) or {}
        self.gravity = float(defn.get("particle_gravity", 1.0))
        if color is not None:
            r, g, b = color
            self.red = (r / 255.0) * 0.6
            self.green = (g / 255.0) * 0.6
            self.blue = (b / 255.0) * 0.6
        else:
            self.red = self.green = self.blue = 0.6
        self.scale *= 0.5

    def fx_layer(self) -> int:
        return 1



class SnowballPoofParticle(DiggingParticle):

    ITEM_NAME = "snowball"
    DEFAULT_TEXTURE = 14

    def __init__(self, x, y, z, mx=0.0, my=0.0, mz=0.0):
        Block._load_defs()
        super().__init__(x, y, z, mx, my, mz, "snow_block")
        from game.entities.item import Item
        item_def = Item.item_defs.get(self.ITEM_NAME) or {}
        self.tex_index = int(item_def.get("texture", self.DEFAULT_TEXTURE))
        self.red = self.green = self.blue = 1.0

    def fx_layer(self) -> int:
        return 2


class SlimeParticle(SnowballPoofParticle):

    ITEM_NAME = "slimeball"
    DEFAULT_TEXTURE = 30


class SmokeParticle(Particle):
    def __init__(self, x, y, z, scale=1.0):
        super().__init__(x, y, z, 0.0, 0.0, 0.0)
        self.motion[0] *= 0.1
        self.motion[1] *= 0.1
        self.motion[2] *= 0.1
        grey = random.random() * 0.3
        self.red = self.green = self.blue = grey
        self.scale *= 12.0 / 16.0 * scale
        self._base_scale = self.scale
        base_max_age = int(8.0 / (random.random() * 0.8 + 0.2))
        self.max_age = int(base_max_age * scale)
        self.tex_index = 0

    def update(self, world=None):
        self.prev_pos[0] = self.pos[0]
        self.prev_pos[1] = self.pos[1]
        self.prev_pos[2] = self.pos[2]
        self.age += 1
        if self.age >= self.max_age:
            self.dead = True
            return
        self.tex_index = max(0, 7 - self.age * 8 // max(1, self.max_age))
        self.motion[1] += 0.004
        previous_y = self.pos[1]
        self._move(world)
        if self.pos[1] == previous_y:
            self.motion[0] *= 1.1
            self.motion[2] *= 1.1
        self.motion[0] *= 0.96
        self.motion[1] *= 0.96
        self.motion[2] *= 0.96
        if self.on_ground:
            self.motion[0] *= 0.7
            self.motion[2] *= 0.7
        self.scale = self._base_scale * min(1.0, self.age / max(1, self.max_age) * 32.0)


class BubbleParticle(Particle):
    def __init__(self, x, y, z, mx=0.0, my=0.0, mz=0.0):
        super().__init__(x, y, z, mx, my, mz)
        self.tex_index = 32
        self.scale *= random.random() * 0.6 + 0.2
        self.motion[0] = mx * 0.2 + (random.random() * 2.0 - 1.0) * 0.02
        self.motion[1] = my * 0.2 + (random.random() * 2.0 - 1.0) * 0.02
        self.motion[2] = mz * 0.2 + (random.random() * 2.0 - 1.0) * 0.02
        self.max_age = int(8.0 / (random.random() * 0.8 + 0.2))
        self.collision_radius = 0.01
        self.gravity = 0.0

    def update(self, world=None):
        self.prev_pos[:] = self.pos
        self.age += 1
        if self.age >= self.max_age:
            self.dead = True
            return
        self.motion[1] += 0.002
        self._move(world)
        self.motion[0] *= 0.85
        self.motion[1] *= 0.85
        self.motion[2] *= 0.85
        if world is not None:
            try:
                bx, by, bz = (math.floor(self.pos[0] + 0.5),
                              math.floor(self.pos[1] + 0.5),
                              math.floor(self.pos[2] + 0.5))
                if world.get_block_type(bx, by, bz) not in ("water", "flowing_water"):
                    self.dead = True
            except Exception:
                pass


class RainParticle(Particle):

    def __init__(self, x, y, z):
        super().__init__(x, y, z, 0.0, 0.0, 0.0)
        self.tex_index = 19 + random.randrange(4)
        self.gravity = 0.06
        self.collision_radius = 0.01
        self.max_age = int(8.0 / (random.random() * 0.8 + 0.2))
        self.motion[0] *= 0.3
        self.motion[1] = random.random() * 0.2 + 0.1
        self.motion[2] *= 0.3
        self.red = self.green = self.blue = 1.0

    def update(self, world=None):
        self.prev_pos[:] = self.pos
        self.age += 1
        if self.age >= self.max_age:
            self.dead = True
            return
        self.motion[1] -= self.gravity
        self._move(world)
        self.motion[0] *= 0.98
        self.motion[1] *= 0.98
        self.motion[2] *= 0.98
        if self.on_ground:
            self.motion[0] *= 0.7
            self.motion[2] *= 0.7
            if random.random() < 0.5:
                self.dead = True
        if world is not None:
            try:
                bx, by, bz = (math.floor(self.pos[0] + 0.5),
                              math.floor(self.pos[1] + 0.5),
                              math.floor(self.pos[2] + 0.5))
                if world.get_block_type(bx, by, bz) in ("water", "flowing_water", "lava", "flowing_lava"):
                    self.dead = True
            except Exception:
                pass


class SplashParticle(RainParticle):

    def __init__(self, x, y, z, mx=0.0, my=0.0, mz=0.0):
        super().__init__(x, y, z)
        self.gravity = 0.04
        self.tex_index += 1
        if my == 0.0 and (mx != 0.0 or mz != 0.0):
            self.motion[:] = [mx, my + 0.1, mz]


class SnowParticle(Particle):

    def __init__(self, x, y, z):
        super().__init__(x, y, z, 0.0, 0.0, 0.0)
        self.tex_index = 19
        self.gravity = 0.01
        self.collision_radius = 0.01
        self.max_age = int(8.0 / (random.random() * 0.8 + 0.2)) * 4
        self.motion[0] *= 0.1
        self.motion[1] = -0.02 - random.random() * 0.02
        self.motion[2] *= 0.1
        self.red = self.green = self.blue = 1.0
        self.no_clip = False
        self.scale *= 0.8

    def update(self, world=None):
        self.prev_pos[:] = self.pos
        self.age += 1
        if self.age >= self.max_age:
            self.dead = True
            return
        self.motion[1] -= self.gravity
        self._move(world)
        self.motion[0] *= 0.98
        self.motion[1] *= 0.98
        self.motion[2] *= 0.98
        if self.on_ground:
            self.motion[0] *= 0.7
            self.motion[2] *= 0.7
            if random.random() < 0.5:
                self.dead = True


RAIN_SURFACE_Y = int(_RAIN_CONFIG.get("surface_y", 64))
RAIN_RADIUS = max(0, int(_RAIN_CONFIG.get("radius", 4)))
RAIN_ATTEMPTS = max(0, int(_RAIN_CONFIG.get("attempts", 50)))
SNOW_RADIUS = RAIN_RADIUS
SNOW_ATTEMPTS = RAIN_ATTEMPTS


def add_rain_particles(effects, world, player, rng=random):
    if player is None or world is None:
        return 0
    if effects.count() > 600:
        return 0
    eye = float(player.pos[1]) + float(getattr(player, "EYE_HEIGHT", 1.62))
    cx = int(math.floor(player.pos[0]))
    cy = int(math.floor(eye))
    cz = int(math.floor(player.pos[2]))
    radius = 16  
    attempts = 150
    chunks = getattr(world, "chunks", None)
    size = int(getattr(world, "CHUNK_SIZE", 16))
    Block._load_defs()
    spawned = 0
    for _ in range(attempts):
        bx = cx + rng.randrange(radius) - rng.randrange(radius)
        bz = cz + rng.randrange(radius) - rng.randrange(radius)
        top = None
        if isinstance(chunks, dict):
            chunk = chunks.get((bx // size, bz // size))
            if chunk is not None:
                lx = bx - chunk.start_x
                lz = bz - chunk.start_z
                if 0 <= lx < chunk.width and 0 <= lz < chunk.depth:
                    top = int(chunk.heightmap[lz * chunk.width + lx])
        if top is None:
            continue
        if not (cy - radius <= top <= cy + radius):
            continue
        below = world.get_block_type(bx, top - 1, bz)
        if below is None:
            continue
        bounds = Block._block_defs.get(below, {}).get("bounds") or (0, 0, 0, 1, 1, 1)
        fx = rng.random()
        fz = rng.random()
        spawn_y = (top + 0.1) - float(bounds[1])
        effects.add(RainParticle(bx + fx, spawn_y, bz + fz))
        spawned += 1
    return spawned


def add_snow_particles(effects, world, player, rng=random):
    if player is None:
        return 0
    eye = float(player.pos[1]) + float(getattr(player, "EYE_HEIGHT", 1.62))
    cx = math.floor(player.pos[0] + 0.5)
    cy = math.floor(eye + 0.5)
    cz = math.floor(player.pos[2] + 0.5)
    chunks = getattr(world, "chunks", None)
    if not isinstance(chunks, dict):
        return 0
    size = int(getattr(world, "CHUNK_SIZE", 16))
    spawned = 0
    for _ in range(SNOW_ATTEMPTS):
        bx = cx + rng.randrange(SNOW_RADIUS * 2 + 1) - SNOW_RADIUS
        bz = cz + rng.randrange(SNOW_RADIUS * 2 + 1) - SNOW_RADIUS
        chunk = chunks.get((bx // size, bz // size))
        if chunk is None:
            continue
        lx = bx - chunk.start_x
        lz = bz - chunk.start_z
        if lx < 0 or lz < 0 or lx >= chunk.width or lz >= chunk.depth:
            continue
        top = int(chunk.heightmap[lz * chunk.width + lx])
        if not (cy - SNOW_RADIUS <= top <= cy + SNOW_RADIUS + 8):
            continue
        fx = rng.random()
        fz = rng.random()
        spawn_y = top + 0.1 + rng.random() * 3.0
        if spawn_y < cy - SNOW_RADIUS or spawn_y > cy + SNOW_RADIUS + 8:
            continue
        effects.add(SnowParticle(bx - 0.5 + fx, spawn_y, bz - 0.5 + fz))
        spawned += 1
    return spawned


class FlameParticle(Particle):
    def __init__(self, x, y, z, mx=0.0, my=0.0, mz=0.0):
        super().__init__(x, y, z, mx, my, mz)
        self.motion[0] = self.motion[0] * 0.01 + mx
        self.motion[1] = self.motion[1] * 0.01 + my
        self.motion[2] = self.motion[2] * 0.01 + mz
        self._base_scale = self.scale
        self.max_age = int(8.0 / (random.random() * 0.8 + 0.2)) + 4
        self.tex_index = 48
        self.red = self.green = self.blue = 1.0
        self.no_clip = True
        self.gravity = 0.0

    def get_brightness(self, world, cache=None) -> float:
        t = min(1.0, max(0.0, self.age / max(1, self.max_age)))
        return super().get_brightness(world, cache) * t + (1.0 - t)

    def update(self, world=None):
        self.prev_pos[:] = self.pos
        self.age += 1
        if self.age >= self.max_age:
            self.dead = True
            return
        self._move(world)
        self.motion[0] *= 0.96
        self.motion[1] *= 0.96
        self.motion[2] *= 0.96
        if self.on_ground:
            self.motion[0] *= 0.7
            self.motion[2] *= 0.7
        t = min(1.0, (self.age + 1) / max(1, self.max_age))
        self.scale = self._base_scale * (1.0 - t * t * 0.5)


class PortalParticle(Particle):

    def __init__(self, x, y, z, mx=0.0, my=0.0, mz=0.0):
        super().__init__(x, y, z, 0.0, 0.0, 0.0)
        self.motion = [float(mx), float(my), float(mz)]
        self._start_pos = (float(x), float(y), float(z))
        tint = random.random() * 0.6 + 0.4
        self._base_scale = random.random() * 0.2 + 0.5
        self.scale = self._base_scale
        self.red = 0.9 * tint
        self.green = 0.3 * tint
        self.blue = tint
        self.max_age = random.randrange(10) + 40
        self.tex_index = random.randrange(8)
        self.no_clip = True
        self.gravity = 0.0

    def get_brightness(self, world, cache=None) -> float:
        age = self.age / max(1, self.max_age)
        return super().get_brightness(world, cache) * (1.0 - age ** 4) + age ** 4

    def update(self, world=None):
        self.prev_pos[:] = self.pos
        age = self.age / max(1, self.max_age)
        factor = 1.0 - age + age * age * 2.0
        factor = 1.0 - factor
        sx, sy, sz = self._start_pos
        self.pos[:] = (
            sx + self.motion[0] * factor,
            sy + self.motion[1] * factor + (1.0 - age),
            sz + self.motion[2] * factor,
        )
        if self.age >= self.max_age:
            self.dead = True
            return
        self.age += 1

    def render(self, partial, cam, right, up, brightness=1.0):
        age = min(1.0, (self.age + float(partial)) / max(1, self.max_age))
        fade = 1.0 - (1.0 - age) ** 2
        self.scale = self._base_scale * fade
        super().render(partial, cam, right, up, brightness)


class RedstoneDustParticle(Particle):

    def __init__(self, x, y, z, mx=0.0, my=0.0, mz=0.0, power=1.0):
        super().__init__(x, y, z, 0.0, 0.0, 0.0)
        strength = max(0.0, min(1.0, float(power)))
        self.motion[0] *= 0.1
        self.motion[1] *= 0.1
        self.motion[2] *= 0.1
        self.red = random.random() * 0.3 + 0.7
        self.green = self.blue = random.random() * 0.1
        self.scale *= 12.0 / 16.0
        self.scale *= strength
        self._base_scale = self.scale
        self.max_age = int(8.0 / (random.random() * 0.8 + 0.2))
        self.max_age = int(self.max_age * strength)
        self.tex_index = 0
        self.no_clip = False
        self.gravity = 0.0

    def get_brightness(self, world, cache=None) -> float:
        return 1.0

    def update(self, world=None):
        self.prev_pos[:] = self.pos
        self.age += 1
        if self.age >= self.max_age:
            self.dead = True
            return
        self.tex_index = max(0, 7 - self.age * 8 // max(1, self.max_age))
        previous_y = self.pos[1]
        self._move(world)
        if self.pos[1] == previous_y:
            self.motion[0] *= 1.1
            self.motion[2] *= 1.1
        self.motion[0] *= 0.96
        self.motion[1] *= 0.96
        self.motion[2] *= 0.96
        if self.on_ground:
            self.motion[0] *= 0.7
            self.motion[2] *= 0.7
        t = min(1.0, max(0.0, self.age / max(1, self.max_age) * 32.0))
        self.scale = self._base_scale * t


class LavaParticle(Particle):
    def __init__(self, x, y, z):
        super().__init__(x, y, z, 0.0, 0.0, 0.0)
        self.motion[0] *= 0.8
        self.motion[1] = random.random() * 0.4 + 0.05
        self.motion[2] *= 0.8
        self.red = self.green = self.blue = 1.0
        self.scale *= random.random() * 2.0 + 0.2
        self._base_scale = self.scale
        self.max_age = int(16.0 / (random.random() * 0.8 + 0.2))
        self.tex_index = 49

    def get_brightness(self, world, cache=None) -> float:
        return 1.0

    def update(self, world=None):
        self.prev_pos[:] = self.pos
        self.age += 1
        if self.age >= self.max_age:
            self.dead = True
            return
        t = self.age / max(1, self.max_age)
        if world is not None and random.random() > t:
            spawn = getattr(world, "spawn_particle", None)
            if callable(spawn):
                spawn("smoke", *self.pos, *self.motion)
            else:
                effects = getattr(world, "effects", None)
                if effects is not None:
                    effects.spawn_particle("smoke", *self.pos, *self.motion, world=world)
        self.motion[1] -= 0.03
        self._move(world)
        self.motion[0] *= 0.999
        self.motion[1] *= 0.999
        self.motion[2] *= 0.999
        if self.on_ground:
            self.motion[0] *= 0.7
            self.motion[2] *= 0.7
        self.scale = self._base_scale * (1.0 - t * t)


class NoteParticle(Particle):

    def __init__(self, x, y, z, mx=0.0, my=0.0, mz=0.0):
        super().__init__(x, y, z, 0.0, 0.0, 0.0)
        self.motion = [0.0, 0.2, 0.0]
        hue = float(mx) % 1.0
        tau = math.tau
        self.red = math.sin(hue * tau) * 0.65 + 0.35
        self.green = math.sin((hue + 1.0 / 3.0) * tau) * 0.65 + 0.35
        self.blue = math.sin((hue + 2.0 / 3.0) * tau) * 0.65 + 0.35
        self.scale *= 0.75 * 2.0
        self._base_scale = self.scale
        self.max_age = 6
        self.tex_index = 64
        self.no_clip = False
        self.gravity = 0.0

    def update(self, world=None):
        self.prev_pos[:] = self.pos
        if self.age >= self.max_age:
            self.dead = True
        else:
            self.age += 1
        previous_y = self.pos[1]
        self._move(world)
        if self.pos[1] == previous_y:
            self.motion[0] *= 1.1
            self.motion[2] *= 1.1
        self.motion[0] *= 0.66
        self.motion[1] *= 0.66
        self.motion[2] *= 0.66
        if self.on_ground:
            self.motion[0] *= 0.7
            self.motion[2] *= 0.7

    def render(self, partial, cam, right, up, brightness=1.0):
        partial_scale = ((self.age + float(partial)) / max(1, self.max_age)) * 32.0
        self.scale = self._base_scale * min(1.0, max(0.0, partial_scale))
        return super().render(partial, cam, right, up, brightness)


class ExplosionParticle(Particle):
    def __init__(self, x, y, z, mx=0.0, my=0.0, mz=0.0):
        super().__init__(x, y, z, mx, my, mz)
        self.motion[0] = mx + (random.random() * 2.0 - 1.0) * 0.05
        self.motion[1] = my + (random.random() * 2.0 - 1.0) * 0.05
        self.motion[2] = mz + (random.random() * 2.0 - 1.0) * 0.05
        self.red = self.green = self.blue = random.random() * 0.3 + 0.7
        self.scale = random.random() * random.random() * 6.0 + 1.0
        self.max_age = int(16.0 / (random.random() * 0.8 + 0.2)) + 2
        self.tex_index = 7

    def update(self, world=None):
        self.prev_pos[:] = self.pos
        self.age += 1
        if self.age >= self.max_age:
            self.dead = True
            return
        self.tex_index = max(0, 7 - self.age * 8 // max(1, self.max_age))
        self.motion[1] += 0.004
        self._move(world)
        self.motion[0] *= 0.9
        self.motion[1] *= 0.9
        self.motion[2] *= 0.9
        if self.on_ground:
            self.motion[0] *= 0.7
            self.motion[2] *= 0.7


def spawn_water_entry_particles(world, pos, motion, width):
    spawn = getattr(world, "spawn_particle", None)
    if not callable(spawn):
        return 0
    count = max(1, int(math.ceil(1.0 + float(width) * 20.0)))
    x, y, z = map(float, pos)
    mx, my, mz = map(float, motion)
    surface_y = math.floor(y + 0.5) + 0.5
    for _ in range(count):
        ox = (random.random() * 2.0 - 1.0) * float(width)
        oz = (random.random() * 2.0 - 1.0) * float(width)
        bx, bz = x + ox, z + oz
        spawn("bubble", bx, surface_y - 0.015, bz,
              mx, my - random.random() * 0.2, mz)
        spawn("splash", bx, surface_y - 0.01, bz, mx, my, mz)
    return count * 2


_PARTICLE_TYPES = {
    "SnowballPoofParticle": SnowballPoofParticle,
    "SlimeParticle": SlimeParticle,
    "SmokeParticle": SmokeParticle,
    "BubbleParticle": BubbleParticle,
    "SplashParticle": SplashParticle,
    "RainParticle": RainParticle,
    "SnowParticle": SnowParticle,
    "FlameParticle": FlameParticle,
    "PortalParticle": PortalParticle,
    "RedstoneDustParticle": RedstoneDustParticle,
    "LavaParticle": LavaParticle,
    "ExplosionParticle": ExplosionParticle,
    "NoteParticle": NoteParticle,
}


class EffectRenderer(EffectRendererMixin):
    def __init__(self):
        self.layers: List[List[Particle]] = [[], [], []]
        self._particles_tex: Optional[Texture] = None
        self._terrain_tex: Optional[Texture] = None
        self._items_tex: Optional[Texture] = None
        self._tick_accum = 0.0
        self._rain_last_world_time = None


    def add(self, particle: Particle):
        layer = particle.fx_layer()
        if 0 <= layer < 3:
            self.layers[layer].append(particle)

    def count(self) -> int:
        return sum(len(layer) for layer in self.layers)

    @staticmethod
    def _oriented_block_bounds(world, x, y, z, block_type: str, meta=None):
        if meta is None and world is not None and hasattr(world, "get_metadata"):
            try:
                meta = int(world.get_metadata(x, y, z)) & 15
            except (TypeError, ValueError, OverflowError):
                meta = None
        if meta is None:
            meta = 2 if block_type == "wall_sign" else 0

        if block_type in ("sign", "wall_sign"):
            half_depth = 1.0 / 24.0
            board = (-0.5, 2.0 / 24.0, -half_depth,
                     0.5, 14.0 / 24.0, half_depth)
            boxes = [board]
            if block_type == "sign":
                boxes.append((-half_depth, -12.0 / 24.0, -half_depth,
                              half_depth, 2.0 / 24.0, half_depth))
                yaw_degrees = (int(meta) & 15) * (360.0 / 16.0)
            else:
                boxes = [(x0, y0 - 5.0 / 16.0, z0 - 7.0 / 16.0,
                          x1, y1 - 5.0 / 16.0, z1 - 7.0 / 16.0)
                         for x0, y0, z0, x1, y1, z1 in boxes]
                yaw_degrees = {2: 180.0, 3: 0.0, 4: 90.0, 5: -90.0}.get(
                    int(meta) & 15, 180.0
                )

            theta = math.radians(-yaw_degrees)
            cosine, sine = math.cos(theta), math.sin(theta)
            rotated = []
            for x0, y0, z0, x1, y1, z1 in boxes:
                for bx in (x0, x1):
                    for bz in (z0, z1):
                        rotated.append((cosine * bx + sine * bz,
                                        y0, -sine * bx + cosine * bz))
                        rotated.append((cosine * bx + sine * bz,
                                        y1, -sine * bx + cosine * bz))
            xs = [point[0] for point in rotated]
            ys = [point[1] for point in rotated]
            zs = [point[2] for point in rotated]
            return (min(xs) + 0.5, min(ys) + 0.5, min(zs) + 0.5,
                    max(xs) + 0.5, max(ys) + 0.5, max(zs) + 0.5)

        try:
            Block._load_defs()
            block = Block(block_type, (int(x), int(y), int(z)), world=world)
            block._mesh_meta = int(meta) & 15
            bounds = block._oriented_bounds()
            return tuple(float(value) for value in bounds)
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError):
            definitions = Block._block_defs or {}
            return (definitions.get(block_type) or {}).get(
                "bounds", (0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
            )

    def _digging_particle_color(self, world, x, y, z, block_type: str):
        if block_type != "leaves":
            return None
        provider = getattr(world, "biome_provider", None)
        if provider is None:
            return None
        try:
            return provider.get_foliage_tint(int(x), int(z))
        except Exception:
            return None

    def add_block_destroy_effects(self, world, x, y, z, block_type: str, meta=None):
        emit = getattr(world, "_emit_multiplayer_particle_event", None)
        if callable(emit):
            emit("block_destroy", x, y, z, block_type=str(block_type),
                 meta=int(meta if meta is not None else 0) & 15)
        n = 4
        bounds = self._oriented_block_bounds(world, x, y, z, block_type, meta=meta)
        min_x, min_y, min_z, max_x, max_y, max_z = map(float, bounds)
        color = self._digging_particle_color(world, x, y, z, block_type)
        for i in range(n):
            for j in range(n):
                for k in range(n):
                    fx, fy, fz = ((i + 0.5) / n, (j + 0.5) / n, (k + 0.5) / n)
                    px = x + min_x + fx * (max_x - min_x) - 0.5
                    py = y + min_y + fy * (max_y - min_y) - 0.5
                    pz = z + min_z + fz * (max_z - min_z) - 0.5
                    self.add(DiggingParticle(
                        px, py, pz, px - x, py - y, pz - z, block_type, color=color,
                    ))

    def add_block_hit_effects(self, world, x, y, z, face: int, block_type: str, meta=None):
        emit = getattr(world, "_emit_multiplayer_particle_event", None)
        if callable(emit):
            emit("block_hit", x, y, z, block_type=str(block_type),
                 meta=int(meta if meta is not None else 0) & 15, face=int(face))
        inset = 0.1
        bounds = self._oriented_block_bounds(world, x, y, z, block_type, meta=meta)
        color = self._digging_particle_color(world, x, y, z, block_type)
        n = 8
        for i in range(n):
            min_x, min_y, min_z, max_x, max_y, max_z = map(float, bounds)
            lo_x, hi_x = x + min_x - 0.5, x + max_x - 0.5
            lo_y, hi_y = y + min_y - 0.5, y + max_y - 0.5
            lo_z, hi_z = z + min_z - 0.5, z + max_z - 0.5
            inset_x = min(inset, max(0.0, hi_x - lo_x) * 0.25)
            inset_y = min(inset, max(0.0, hi_y - lo_y) * 0.25)
            inset_z = min(inset, max(0.0, hi_z - lo_z) * 0.25)
            px = lo_x + random.random() * max(0.0, hi_x - lo_x - inset_x * 2) + inset_x
            py = lo_y + random.random() * max(0.0, hi_y - lo_y - inset_y * 2) + inset_y
            pz = lo_z + random.random() * max(0.0, hi_z - lo_z - inset_z * 2) + inset_z
            if face == 0:
                py = lo_y - inset_y
            elif face == 1:
                py = hi_y + inset_y
            elif face == 2:
                pz = lo_z - inset_z
            elif face == 3:
                pz = hi_z + inset_z
            elif face == 4:
                px = lo_x - inset_x
            elif face == 5:
                px = hi_x + inset_x
            self.add(
                DiggingParticle(px, py, pz, 0.0, 0.0, 0.0, block_type, color=color)
                .multiply_velocity(0.2)
                .multiply_scale(0.6)
            )

    def spawn_particle(self, name: str, x, y, z, mx=0.0, my=0.0, mz=0.0, *, world=None):
        spec = _PARTICLE_SPAWN_DEFS.get(name)
        if not isinstance(spec, dict):
            return
        particle_type = _PARTICLE_TYPES.get(spec.get("class"))
        if particle_type is None:
            return
        arguments = spec.get("arguments", "position")
        if arguments == "motion":
            particle = particle_type(x, y, z, mx, my, mz, **dict(spec.get("kwargs", {})))
        elif arguments == "position":
            particle = particle_type(x, y, z, **dict(spec.get("kwargs", {})))
        else:
            return
        self.add(particle)

    def update(self, dt: float, world=None):
        self._tick_accum += min(dt, 0.25)
        ticks = 0
        while self._tick_accum >= 0.05 and ticks < 4:
            self._tick_accum -= 0.05
            ticks += 1
            if world is not None and getattr(world, "raining", False):
                world_time = int(getattr(world, "world_time", -1))
                if world_time != self._rain_last_world_time:
                    self._rain_last_world_time = world_time
                    add_rain_particles(self, world, getattr(world, "player", None))
            for layer in self.layers:
                read = write = 0
                while read < len(layer):
                    particle = layer[read]
                    read += 1
                    particle.update(world)
                    if not particle.dead:
                        if write != read - 1:
                            layer[write] = particle
                        write += 1
                if write < len(layer):
                    del layer[write:]
