from __future__ import annotations

import contextlib
import math
import random
import time


from game.block import Block
from game.block.drops import block_drops, scatter_position
from game.entities.entity import MAX_DT, TICKS_PER_SECOND, Entity
from .tnt_model import PrimedTNTRendererMixin
from game.entities.item import ItemEntity

SIZE = 0.98
HALF = SIZE * 0.5
GRAVITY = 0.04
DRAG = 0.98
GROUND_XZ = 0.7
DEFAULT_FUSE = 80
EXPLOSION_POWER = 4.0
PICKUP_CHANCE = 0.3
PARTICLE_STRIDE = 1


def _build_explosion_ray_directions(n=16):
    last = float(n - 1)
    directions = []
    for i in range(n):
        for j in range(n):
            for k in range(n):
                if not (i == 0 or i == n - 1 or j == 0 or j == n - 1
                        or k == 0 or k == n - 1):
                    continue
                dx = i / last * 2.0 - 1.0
                dy = j / last * 2.0 - 1.0
                dz = k / last * 2.0 - 1.0
                length = math.sqrt(dx * dx + dy * dy + dz * dz)
                directions.append((dx / length, dy / length, dz / length))
    return tuple(directions)


_EXPLOSION_RAY_DIRECTIONS = _build_explosion_ray_directions()

def ignite_tnt(world, x, y, z, fuse=None, play_sound=True):
    tnt = PrimedTNT.at_block(x, y, z)
    if fuse is not None:
        tnt.fuse = float(fuse)
    world.spawn_entity(tnt)
    if play_sound:
        _play(world, "random.fuse", 1.0, 1.0, tnt.center)
    return tnt


def on_tnt_destroyed_by_explosion(world, x, y, z):
    tnt = PrimedTNT.at_block(x, y, z)
    tnt.fuse = float(random.randrange(DEFAULT_FUSE // 4) + DEFAULT_FUSE // 8)
    world.spawn_entity(tnt)
    return tnt


def _play(world, name, volume, pitch, position):
    callback = getattr(world, "sound_player", None)
    if callable(callback):
        callback(name, volume=volume, pitch=pitch, position=tuple(position))

def _block_defs():
    Block._load_defs()
    return Block._block_defs


def _explosion_resistance(defs, block_type):
    d = defs.get(block_type, {})
    resistance = float(d.get("resistance", 0.0))
    hardness = float(d.get("hardness", 0.0))
    if resistance == hardness:
        return resistance
    return resistance * 0.6


def _blocks_ray(defs, block_type):
    if block_type is None:
        return False
    return defs.get(block_type, {}).get("model") != "liquid"


def _segment_clear(world, defs, a, b):
    dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length < 1.0e-6:
        return True
    steps = max(1, int(length / 0.2))
    for i in range(steps + 1):
        t = i / steps
        cell = (math.floor(a[0] + dx * t), math.floor(a[1] + dy * t), math.floor(a[2] + dz * t))
        if _blocks_ray(defs, world.get_block_type(*cell)):
            return False
    return True


def block_density(world, defs, origin, box):
    minx, maxx, miny, maxy, minz, maxz = box
    sx = 1.0 / ((maxx - minx) * 2.0 + 1.0)
    sy = 1.0 / ((maxy - miny) * 2.0 + 1.0)
    sz = 1.0 / ((maxz - minz) * 2.0 + 1.0)
    clear = total = 0
    fx = 0.0
    while fx <= 1.0:
        fy = 0.0
        while fy <= 1.0:
            fz = 0.0
            while fz <= 1.0:
                point = (minx + (maxx - minx) * fx,
                         miny + (maxy - miny) * fy,
                         minz + (maxz - minz) * fz)
                if _segment_clear(world, defs, point, origin):
                    clear += 1
                total += 1
                fz += sz
            fy += sy
        fx += sx
    return clear / total if total else 0.0


def _entity_anchor_height(entity):
    eye = getattr(entity, "EYE_HEIGHT", None)
    if eye is not None:
        return float(eye)
    return float(entity.size[1]) * 0.5


def _hurt(entity, amount, source_entity, knockback=None):
    damage_player = getattr(entity, "damage_player", None)
    if callable(damage_player):
        damage_player(amount, source="explosion", attacker=None)
        return
    attack = getattr(entity, "attack_entity_from", None) or getattr(entity, "attackEntityFrom", None)
    if callable(attack):
        if getattr(entity, "_mp_remote_player", False):
            attack(source_entity, amount, knockback=knockback, source="explosion")
        else:
            attack(source_entity, amount)
        return
    if isinstance(entity, ItemEntity):
        entity.health -= amount
        if entity.health <= 0:
            entity.dead = True


def spawn_explosion_particles(world, x, y, z, power=EXPLOSION_POWER, rng=random):
    count = max(16, min(64, int(24 * max(0.5, float(power) / 4.0))))
    radius = max(1.0, float(power) * 1.5)
    for _ in range(count):
        dx = rng.uniform(-1.0, 1.0)
        dy = rng.uniform(-1.0, 1.0)
        dz = rng.uniform(-1.0, 1.0)
        length = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        dx, dy, dz = dx / length, dy / length, dz / length
        distance = rng.random() * radius
        px, py, pz = x + 0.5 + dx * distance, y + 0.5 + dy * distance, z + 0.5 + dz * distance
        speed = rng.uniform(0.05, 0.35)
        world.spawn_particle("explode", px, py, pz, dx * speed, dy * speed, dz * speed)
        if rng.random() < 0.7:
            world.spawn_particle("smoke", px, py, pz, dx * speed * 0.5,
                                 max(0.02, dy * speed * 0.5), dz * speed * 0.5)


def _ignite_exploded_cells(world, defs, cells, rng, light_changes=None):
    from game.block.fire_rules import is_normal_cube
    for bx, by, bz in reversed(cells):
        if world.get_block_type(bx, by, bz) is not None:
            continue
        below = defs.get(world.get_block_type(bx, by - 1, bz))
        if is_normal_cube(below) and rng.randrange(3) == 0:
            old_signature_fn = getattr(world, "_light_signature", None)
            old_signature = old_signature_fn(None) if callable(old_signature_fn) else None
            world.set_block(bx, by, bz, "fire", meta=0)
            if light_changes is not None:
                if callable(old_signature_fn):
                    new_signature = old_signature_fn(world.get_block_type(bx, by, bz))
                    if old_signature != new_signature:
                        light_changes.setdefault("block", []).append((bx, by, bz))
                        if old_signature[0] != new_signature[0]:
                            light_changes.setdefault("sky", []).append((bx, by, bz))
                else:
                    light_changes.setdefault("block", []).append((bx, by, bz))
                    light_changes.setdefault("sky", []).append((bx, by, bz))


def create_explosion(world, source, x, y, z, power=EXPLOSION_POWER, rng=random,
                     fire=False):
    profiler = getattr(world, "profiler", None)
    profile_enabled = (
        profiler is not None
        and callable(getattr(profiler, "record_event", None))
        and getattr(profiler, "_event_sink", None) is not None
    )
    explosion_started = time.perf_counter() if profile_enabled else None
    defs = _block_defs()
    jx, jy, jz = x + 0.5, y + 0.5, z + 0.5   

    _play(world, "random.explode", 4.0,
          (1.0 + (rng.random() - rng.random()) * 0.2) * 0.7, (x, y, z))

    destroyed = set()
    resist_cache = {}
    ray_started = time.perf_counter() if profile_enabled else None
    step = 0.3
    for dx, dy, dz in _EXPLOSION_RAY_DIRECTIONS:
        strength = power * (0.7 + rng.random() * 0.6)
        px, py, pz = jx, jy, jz
        while strength > 0.0:
            cell = (math.floor(px), math.floor(py), math.floor(pz))
            if cell in resist_cache:
                resistance = resist_cache[cell]
            else:
                block_type = world.get_block_type(*cell)
                resistance = (None if block_type is None
                              else _explosion_resistance(defs, block_type))
                resist_cache[cell] = resistance
            if resistance is not None:
                strength -= (resistance + 0.3) * step
            if strength > 0.0:
                destroyed.add(cell)
            px += dx * step
            py += dy * step
            pz += dz * step
            strength -= step * 0.75
    ray_ms = ((time.perf_counter() - ray_started) * 1000.0
              if ray_started is not None else None)

    diameter = power * 2.0
    query_radius = diameter + 1.0
    query_bounds = (
        x - query_radius, y - query_radius - 6.0, z - query_radius,
        x + query_radius, y + query_radius, z + query_radius,
    )
    iter_nearby = getattr(world, "iter_entities_in_bounds", None)
    if callable(iter_nearby):
        candidates = iter_nearby(*query_bounds)
    else:
        candidates = getattr(world, "entities", ())
    entity_started = time.perf_counter() if profile_enabled else None
    seen_entity_ids = set()
    entity_candidates = 0
    entity_hits = 0

    def apply_blast(entity):
        nonlocal entity_candidates, entity_hits
        if entity is source or getattr(entity, "dead", False):
            return
        entity_id = id(entity)
        if entity_id in seen_entity_ids:
            return
        seen_entity_ids.add(entity_id)
        entity_candidates += 1
        blast = _blast_impulse(world, defs, entity, x, y, z, diameter)
        if blast is None:
            return
        entity_hits += 1
        impact, direction = blast
        amount = int((impact * impact + impact) / 2.0 * 8.0 * diameter + 1.0)
        impulse = tuple(component * impact for component in direction)
        _hurt(entity, amount, source, knockback=impulse)
        if not getattr(entity, "_mp_remote_player", False):
            _push(entity, direction, impact)

    for entity in candidates:
        apply_blast(entity)

    player = getattr(world, "player", None)
    if player is not None:
        apply_blast(player)
    for remote in tuple(getattr(world, "_mp_remote_players", ())):
        apply_blast(remote)
    entity_ms = ((time.perf_counter() - entity_started) * 1000.0
                 if entity_started is not None else None)

    destroyed_list = list(destroyed)
    bulk = getattr(world, "bulk_edit", None)
    had_network_callback = callable(getattr(world, "_network_block_callback", None))
    previous_suppress = bool(getattr(world, "_mp_suppress_world_events", False))
    if had_network_callback:
        world._network_edit_depth = int(getattr(world, "_network_edit_depth", 0)) + 1
    world._mp_suppress_world_events = True
    block_started = time.perf_counter() if profile_enabled else None
    mutation_stats = {} if profile_enabled else None
    light_changes = {"sky": [], "block": []}
    relight_info = None
    try:
        with (bulk() if bulk is not None else contextlib.nullcontext()):
            chained = _destroy_cells(
                world, destroyed_list, x, y, z, power, rng,
                stats=mutation_stats, light_changes=light_changes,
            )
            if fire:
                _ignite_exploded_cells(world, defs, destroyed_list, rng, light_changes=light_changes)
    finally:
        world._mp_suppress_world_events = previous_suppress
        if had_network_callback:
            world._network_edit_depth = max(0, int(getattr(world, "_network_edit_depth", 1)) - 1)
    block_apply_ms = ((time.perf_counter() - block_started) * 1000.0
                      if block_started is not None else None)
    event_callback = getattr(world, "_network_world_event_callback", None)
    if callable(event_callback) and not previous_suppress:
        try:
            event_seed = int(rng.randrange(0, 2**31))
        except Exception:
            event_seed = 0
        event_callback({
            "kind": "explosion", "x": float(x), "y": float(y),
            "z": float(z), "power": float(power), "seed": event_seed,
        })
    relight_started = time.perf_counter() if profile_enabled else None
    relight = getattr(world, "relight_after_explosion", None)
    if callable(relight) and light_changes["block"]:
        relight_info = relight(light_changes)
    relight_ms = ((time.perf_counter() - relight_started) * 1000.0
                  if relight_started is not None else None)

    if profile_enabled:
        total_ms = (time.perf_counter() - explosion_started) * 1000.0
        try:
            profiler.record_event(
                "explosion.summary",
                duration_ms=round(total_ms, 3),
                ray_ms=round(ray_ms, 3),
                entity_ms=round(entity_ms, 3),
                block_apply_ms=round(block_apply_ms, 3),
                relight_ms=round(relight_ms, 3),
                affected_cells=len(destroyed_list),
                rays=len(_EXPLOSION_RAY_DIRECTIONS),
                cached_cells=len(resist_cache),
                blocks_destroyed=int(mutation_stats.get("blocks_destroyed", 0)) if mutation_stats else 0,
                drop_entities_spawned=int(mutation_stats.get("drop_entities_spawned", 0)) if mutation_stats else 0,
                particle_requests=int(mutation_stats.get("particle_requests", 0)) if mutation_stats else 0,
                lighting_cells=len(light_changes["block"]),
                sky_changed_cells=len(light_changes["sky"]),
                sky_columns=int(relight_info.get("sky_columns", 0)) if isinstance(relight_info, dict) else 0,
                sky_region_columns=int(relight_info.get("sky_region_columns", 0)) if isinstance(relight_info, dict) else 0,
                blocklight_radius=int(relight_info.get("blocklight_radius", 0)) if isinstance(relight_info, dict) else 0,
                chained_tnt=len(chained),
                entity_total=len(getattr(world, "entities", ())),
                entity_candidates=entity_candidates,
                entity_hits=entity_hits,
                spatial_index=bool(getattr(world, "_entity_spatial_index", None) is not None),
            )
        except Exception:
            pass



def _blast_impulse(world, defs, entity, x, y, z, diameter):
    ex, ey, ez = entity.pos
    ey += _entity_anchor_height(entity)
    ratio = math.sqrt((ex - x) ** 2 + (ey - y) ** 2 + (ez - z) ** 2) / diameter
    if ratio > 1.0:
        return None
    vx, vy, vz = ex - x, ey - y, ez - z
    length = math.sqrt(vx * vx + vy * vy + vz * vz)
    if length < 1.0e-6:
        return None
    half_w = float(entity.size[0]) * 0.5
    half_d = float(entity.size[2]) * 0.5
    box = (entity.pos[0] + 0.5 - half_w, entity.pos[0] + 0.5 + half_w,
           entity.pos[1] + 0.5, entity.pos[1] + 0.5 + float(entity.size[1]),
           entity.pos[2] + 0.5 - half_d, entity.pos[2] + 0.5 + half_d)
    density = block_density(world, defs, (x + 0.5, y + 0.5, z + 0.5), box)
    return (1.0 - ratio) * density, (vx / length, vy / length, vz / length)


def _push(entity, direction, impact):
    entity.vel[0] += direction[0] * impact
    entity.vel[1] += direction[1] * impact
    entity.vel[2] += direction[2] * impact


def _destroy_cells(world, cells, x, y, z, power, rng, stats=None, light_changes=None):
    chained = []
    for index in range(len(cells) - 1, -1, -1):
        bx, by, bz = cells[index]
        block_type = world.get_block_type(bx, by, bz)

        qx = bx - 0.5 + rng.random()
        qy = by - 0.5 + rng.random()
        qz = bz - 0.5 + rng.random()
        ox, oy, oz = qx - x, qy - y, qz - z
        olen = math.sqrt(ox * ox + oy * oy + oz * oz) or 1.0
        ox /= olen
        oy /= olen
        oz /= olen
        speed = 0.5 / (olen / power + 0.1)
        speed *= rng.random() * rng.random() + 0.3
        ox *= speed
        oy *= speed
        oz *= speed
        if index % PARTICLE_STRIDE == 0:
            world.spawn_particle("explode", (qx + x) / 2.0, (qy + y) / 2.0, (qz + z) / 2.0, ox, oy, oz)
            world.spawn_particle("smoke", qx, qy, qz, ox, oy, oz)
            if stats is not None:
                stats["particle_requests"] = stats.get("particle_requests", 0) + 2

        if block_type is None:
            continue
        if stats is not None:
            stats["blocks_destroyed"] = stats.get("blocks_destroyed", 0) + 1
        meta = world.get_metadata(bx, by, bz)
        for stack in block_drops(block_type, meta, rng):
            if rng.random() <= PICKUP_CHANCE:
                world.spawn_entity(ItemEntity(
                    stack, scatter_position((bx, by, bz), rng), pickup_delay=10,
                ))
                if stats is not None:
                    stats["drop_entities_spawned"] = stats.get("drop_entities_spawned", 0) + 1
        old_signature_fn = getattr(world, "_light_signature", None)
        if callable(old_signature_fn):
            old_light_signature = old_signature_fn(block_type)
        else:
            old_light_signature = None
        world.set_block(bx, by, bz, None, immediate_mesh=index == 0)
        if light_changes is not None:
            if callable(old_signature_fn):
                new_type = world.get_block_type(bx, by, bz)
                new_light_signature = old_signature_fn(new_type)
                if old_light_signature != new_light_signature:
                    light_changes.setdefault("block", []).append((bx, by, bz))
                    if old_light_signature[0] != new_light_signature[0]:
                        light_changes.setdefault("sky", []).append((bx, by, bz))
            else:
                light_changes.setdefault("block", []).append((bx, by, bz))
                light_changes.setdefault("sky", []).append((bx, by, bz))
        if block_type == "tnt":
            chained.append(on_tnt_destroyed_by_explosion(world, bx, by, bz))
    return chained

class PrimedTNT(PrimedTNTRendererMixin, Entity):
    STEP_HEIGHT = 0.0

    def __init__(self, pos, fuse=DEFAULT_FUSE):
        super().__init__(pos, (SIZE, SIZE, SIZE))
        self.prevent_entity_spawning = True
        self.fuse = float(fuse)
        self._smoke_accum = 0.0

    @classmethod
    def at_block(cls, bx, by, bz):
        entity = cls((float(bx), by - HALF, float(bz)))
        angle = random.random() * math.pi * 2.0
        entity.vel = [-math.sin(angle * math.pi / 180.0) * 0.02, 0.2,
                      -math.cos(angle * math.pi / 180.0) * 0.02]
        return entity

    @property
    def center(self):
        return self.pos[0], self.pos[1] + HALF, self.pos[2]

    def can_be_collided_with(self):
        return not self.dead

    def update(self, dt):
        world = Entity.world
        if self.dead or world is None:
            return
        ticks = min(dt, MAX_DT) * TICKS_PER_SECOND

        self.vel[1] -= GRAVITY * ticks
        before = list(self.pos)
        want = (self.vel[0] * ticks, self.vel[1] * ticks, self.vel[2] * ticks)
        self.move(*want)
        if abs((self.pos[0] - before[0]) - want[0]) > 1e-8:
            self.vel[0] = 0.0
        if abs((self.pos[2] - before[2]) - want[2]) > 1e-8:
            self.vel[2] = 0.0

        drag = DRAG ** ticks
        self.vel[0] *= drag
        self.vel[1] *= drag
        self.vel[2] *= drag
        if self._blocked_down:
            ground = GROUND_XZ ** ticks
            self.vel[0] *= ground
            self.vel[2] *= ground

        if self.fuse <= 0.0:
            self.dead = True
            cx, cy, cz = self.center
            create_explosion(world, self, cx, cy, cz, EXPLOSION_POWER)
            return
        self.fuse -= ticks
        self._smoke_accum += ticks
        while self._smoke_accum >= 1.0:
            self._smoke_accum -= 1.0
            cx, cy, cz = self.center
            world.spawn_particle("smoke", cx, cy + 0.5, cz, 0.0, 0.0, 0.0)
