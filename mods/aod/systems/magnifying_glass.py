
from __future__ import annotations

import math


MAGNIFYING_GLASS_VARIANT = "magnifying_glass"
BEAM_MAX_DISTANCE = 32.0
BEAM_ENTITY_RADIUS = 0.32
MIN_SUN_ELEVATION = 0.20
PULSE_INTERVAL = 0.25
MAX_BLOCKS_PER_PULSE = 64
MAX_EFFECTS_PER_PULSE = MAX_BLOCKS_PER_PULSE
FIRE_TICKS = 100

_NEIGHBORS = (
    (0, 1, 0), (1, 0, 0), (-1, 0, 0),
    (0, 0, 1), (0, 0, -1), (0, -1, 0),
)


def sun_beam_direction(world, *, min_elevation=MIN_SUN_ELEVATION):
    if world is None or bool(getattr(world, "is_hell_world", False)):
        return None
    is_daytime = getattr(world, "is_daytime", None)
    if callable(is_daytime):
        try:
            if not is_daytime():
                return None
        except Exception:
            return None
    get_angle = getattr(world, "get_celestial_angle", None)
    if not callable(get_angle):
        return None
    try:
        angle = float(get_angle(0.0))
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(angle):
        return None

    radians = angle * (2.0 * math.pi)
    elevation = math.cos(radians)
    if elevation <= max(0.0, float(min_elevation)):
        return None
    return (0.0, -elevation, -math.sin(radians))


def _block_definitions():
    try:
        from game.block import Block
        Block._load_defs()
        definitions = getattr(Block, "_block_defs", {})
        return definitions if isinstance(definitions, dict) else {}
    except Exception:
        return {}


def _loaded(world, position):
    chunks = getattr(world, "chunks", None)
    get_index = getattr(world, "_get_chunk_index", None)
    if isinstance(chunks, dict) and callable(get_index):
        try:
            return get_index(int(position[0]), int(position[2])) in chunks
        except Exception:
            return False
    return True


def _normal_cube(definition):
    return bool(definition) and (
        definition.get("collision", True) is not False
        and definition.get("model", "cube") == "cube"
        and not definition.get("transparent", False)
        and definition.get("nCull", "all") == "all"
    )


def _fire_has_support(world, position, definitions):
    x, y, z = position
    below = world.get_block_type(x, y - 1, z)
    if _normal_cube(definitions.get(below, {})):
        return True
    for dx, dy, dz in _NEIGHBORS:
        neighbor = world.get_block_type(x + dx, y + dy, z + dz)
        if int(definitions.get(neighbor, {}).get("catchFire", 0) or 0) > 0:
            return True
    return False


def _block_relative_bounds(world, position, block_type, definition):
    collision_value = definition.get("collision")
    explicit_collision = isinstance(collision_value, (list, tuple)) and len(collision_value) == 6
    raw = collision_value
    if not explicit_collision:
        raw = definition.get("bounds", (0.0, 0.0, 0.0, 1.0, 1.0, 1.0))
    try:
        values = tuple(float(value) for value in raw)
    except (TypeError, ValueError, OverflowError):
        values = (0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    if len(values) != 6:
        values = (0.0, 0.0, 0.0, 1.0, 1.0, 1.0)

    if (definition.get("model") == "slab" and block_type == "stone_slab"
            and not explicit_collision):
        values = (0.0, 0.0, 0.0, 1.0, 0.5, 1.0)

    if definition.get("model") == "stairs" and not explicit_collision:
        try:
            metadata = int(world.get_metadata(*position)) & 7
        except (AttributeError, TypeError, ValueError, OverflowError):
            metadata = 0
        facing = metadata & 3
        upside_down = bool(metadata & 4)
        if facing == 0:
            step = (0.5, 0.0, 0.0, 1.0, 0.5, 1.0)
        elif facing == 1:
            step = (0.0, 0.0, 0.0, 0.5, 0.5, 1.0)
        elif facing == 2:
            step = (0.0, 0.0, 0.5, 1.0, 0.5, 1.0)
        else:
            step = (0.0, 0.0, 0.0, 1.0, 0.5, 0.5)
        if upside_down:
            boxes = [(0.0, 0.5, 0.0, 1.0, 1.0, 1.0), step]
        else:
            boxes = [(0.0, 0.0, 0.0, 1.0, 0.5, 1.0),
                     (step[0], 0.5, step[2], step[3], 1.0, step[5])]
        return tuple(
            tuple(box[index] - 0.5 for index in range(6))
            for box in boxes
        )

    return tuple(values[index] - 0.5 for index in range(6))


def _ray_aabb_entry_hit(origin, direction, position, relative_bounds):
    bounds = (
        position[0] + relative_bounds[0],
        position[1] + relative_bounds[1],
        position[2] + relative_bounds[2],
        position[0] + relative_bounds[3],
        position[1] + relative_bounds[4],
        position[2] + relative_bounds[5],
    )
    near, far = -math.inf, math.inf
    near_normal = (0.0, 0.0, 0.0)
    for axis in range(3):
        component = float(direction[axis])
        start = float(origin[axis])
        low, high = float(bounds[axis]), float(bounds[axis + 3])
        if abs(component) <= 1.0e-12:
            if start < low - 1.0e-9 or start > high + 1.0e-9:
                return None
            continue
        if component > 0.0:
            first = (low - start) / component
            second = (high - start) / component
            normal_sign = -1.0
        else:
            first = (high - start) / component
            second = (low - start) / component
            normal_sign = 1.0
        if first > near:
            near = first
            normal = [0.0, 0.0, 0.0]
            normal[axis] = normal_sign
            near_normal = tuple(normal)
        far = min(far, second)
        if near > far:
            return None
    if far < 0.0:
        return None
    return max(0.0, near), near_normal


def _ray_aabb_entry_distance(origin, direction, position, relative_bounds):
    hit = _ray_aabb_entry_hit(origin, direction, position, relative_bounds)
    return None if hit is None else hit[0]


def trace_sun_beam(world, source_position, direction, definitions=None,
                   max_distance=BEAM_MAX_DISTANCE):
    if definitions is None:
        definitions = _block_definitions()
    origin = tuple(float(value) for value in source_position)
    sx, sy, sz = (int(value) for value in source_position)
    cell = [sx, sy, sz]
    previous = tuple(cell)
    t_max = []
    t_delta = []
    steps = []
    for component in direction:
        component = float(component)
        if component > 1.0e-12:
            steps.append(1)
            t_max.append(0.5 / component)
            t_delta.append(1.0 / component)
        elif component < -1.0e-12:
            steps.append(-1)
            t_max.append(0.5 / -component)
            t_delta.append(1.0 / -component)
        else:
            steps.append(0)
            t_max.append(math.inf)
            t_delta.append(math.inf)

    while True:
        axis = min(range(3), key=t_max.__getitem__)
        distance = t_max[axis]
        if not math.isfinite(distance) or distance > float(max_distance):
            return None, previous, float(max_distance), None
        previous = tuple(cell)
        cell[axis] += steps[axis]
        t_max[axis] += t_delta[axis]
        position = tuple(cell)
        if not _loaded(world, position):
            return None, previous, distance, None
        block_type = world.get_block_type(*position)
        if block_type is None:
            continue
        definition = definitions.get(block_type, {})
        try:
            opacity = int(definition.get("lightOpacity", 255))
        except (AttributeError, TypeError, ValueError, OverflowError):
            opacity = 255
        if opacity >= 255:
            relative_bounds = _block_relative_bounds(
                world, position, block_type, definition,
            )
            if (isinstance(relative_bounds, (list, tuple))
                    and relative_bounds and isinstance(relative_bounds[0], (list, tuple))):
                candidate_bounds = relative_bounds
            else:
                candidate_bounds = (relative_bounds,)
            hits = []
            for bounds in candidate_bounds:
                hit = _ray_aabb_entry_hit(
                    origin, direction, position, bounds,
                )
                if hit is not None and hit[0] + 1.0e-7 >= distance:
                    hits.append(hit)
            if hits:
                hit_distance, hit_normal = min(hits, key=lambda item: item[0])
                return position, previous, hit_distance, hit_normal


def _sun_reaches_source(world, source, direction, definitions):
    target, _previous, distance, _hit_normal = trace_sun_beam(
        world, source, tuple(-float(value) for value in direction),
        definitions, max_distance=128.0,
    )
    return target is None and distance >= 127.999


def _place_fire_at_target(world, target, previous, source, direction, definitions,
                          hit_normal=None):
    candidates = []
    if hit_normal is not None:
        normal_neighbor = tuple(
            target[i] + int(round(float(hit_normal[i]))) for i in range(3)
        )
        candidates.append(normal_neighbor)
    if previous != source:
        candidates.append(tuple(previous))

    incoming = tuple(-float(component) for component in direction)
    neighbors = sorted(
        _NEIGHBORS,
        key=lambda offset: -sum(offset[i] * incoming[i] for i in range(3)),
    )
    for dx, dy, dz in neighbors:
        candidate = (target[0] + dx, target[1] + dy, target[2] + dz)
        if candidate not in candidates:
            candidates.append(candidate)

    if any(world.get_block_type(*position) == "fire" for position in candidates):
        return None

    for position in candidates:
        if position == source or not _loaded(world, position):
            continue
        if world.get_block_type(*position) is not None:
            continue
        if not _fire_has_support(world, position, definitions):
            continue
        world.set_block(*position, "fire", meta=0, immediate_mesh=False)
        return position
    return None


def _entity_center(entity):
    position = getattr(entity, "pos", None)
    if position is None or len(position) < 3:
        return None
    size = getattr(entity, "size", (0.6, 1.8, 0.6))
    try:
        height = max(0.0, float(size[1]))
    except (TypeError, ValueError, IndexError):
        height = 1.8
    return (float(position[0]), float(position[1]) + height * 0.5,
            float(position[2]))


def _candidate_entities(world, start, direction, max_distance):
    end = tuple(start[i] + direction[i] * max_distance for i in range(3))
    bounds = (
        min(start[0], end[0]) - 1.0, min(start[1], end[1]) - 1.0,
        min(start[2], end[2]) - 1.0, max(start[0], end[0]) + 1.0,
        max(start[1], end[1]) + 1.0, max(start[2], end[2]) + 1.0,
    )
    iterator = getattr(world, "iter_entities_in_bounds", None)
    if callable(iterator):
        try:
            entities = list(iterator(*bounds))
        except Exception:
            entities = list(getattr(world, "entities", ()) or ())
    else:
        entities = list(getattr(world, "entities", ()) or ())
    entities.append(getattr(world, "player", None))
    entities.extend(tuple(getattr(world, "_mp_remote_players", ()) or ()))

    seen = set()
    for entity in entities:
        if entity is None or id(entity) in seen:
            continue
        seen.add(id(entity))
        yield entity


def _burn_entities_in_beam(world, source, direction, blocker_distance):
    start = tuple(float(value) for value in source)
    burned = 0
    for entity in _candidate_entities(world, start, direction, blocker_distance):
        if (getattr(entity, "dead", False)
                or getattr(entity, "health", 1) <= 0
                or getattr(entity, "gamemode", 0) == 1
                or bool(getattr(entity, "FIRE_IMMUNE", False))):
            continue
        if (getattr(entity, "in_water", False)
                or getattr(entity, "submerged", False)
                or getattr(entity, "in_lava", False)):
            continue
        if (not callable(getattr(entity, "damage_player", None))
                and not callable(getattr(entity, "attack_entity_from", None))):
            continue
        center = _entity_center(entity)
        if center is None:
            continue
        delta = tuple(center[i] - start[i] for i in range(3))
        along = sum(delta[i] * direction[i] for i in range(3))
        if along < 0.25 or along > blocker_distance + 0.15:
            continue
        perpendicular_sq = sum(
            (delta[i] - direction[i] * along) ** 2 for i in range(3)
        )
        size = getattr(entity, "size", (0.6, 1.8, 0.6))
        try:
            radius = max(float(size[0]), float(size[2])) * 0.5 + BEAM_ENTITY_RADIUS
        except (TypeError, ValueError, IndexError):
            radius = 0.62
        if perpendicular_sq > radius * radius:
            continue
        entity._fire_ticks = max(
            int(getattr(entity, "_fire_ticks", 0) or 0), FIRE_TICKS,
        )
        burned += 1
    return burned


def _spawn_target_effect(world, source, direction, hit_distance, target, previous,
                         hit_normal=None):
    if target is None or previous is None:
        return 0
    delta = tuple(int(target[i]) - int(previous[i]) for i in range(3))
    normal = (tuple(float(value) for value in hit_normal)
              if hit_normal is not None
              else tuple(-float(value) for value in delta))
    if not any(normal):
        return 0

    center = [float(source[i]) + float(direction[i]) * float(hit_distance)
              + normal[i] * 0.004 for i in range(3)]
    spots = getattr(world, "_aod_solar_spots", None)
    if not isinstance(spots, list):
        spots = []
        world._aod_solar_spots = spots

    target_key = tuple(target)
    history_key = tuple(int(value) for value in source)
    smoke_counters = getattr(world, "_aod_solar_smoke_counters", None)
    if not isinstance(smoke_counters, dict):
        smoke_counters = {}
        world._aod_solar_smoke_counters = smoke_counters
    smoke_counter = int(smoke_counters.get(history_key, 0)) + 1
    smoke_counters[history_key] = smoke_counter
    if len(smoke_counters) > 512:
        smoke_counters.pop(next(iter(smoke_counters)))

    last_focus = getattr(world, "_aod_solar_last_focus", None)
    if not isinstance(last_focus, dict):
        last_focus = {}
        world._aod_solar_last_focus = last_focus
    previous_focus = last_focus.get(history_key)
    if previous_focus:
        old_center = tuple(float(value) for value in previous_focus.get("center", center))
        old_normal = tuple(float(value) for value in previous_focus.get("normal", normal))
        travel = math.sqrt(sum((center[i] - old_center[i]) ** 2 for i in range(3)))
        normal_dot = sum(old_normal[i] * normal[i] for i in range(3))
        if 0.006 <= travel <= 1.25 and normal_dot >= 0.45:
            trail = getattr(world, "_aod_solar_plasma_trail", None)
            if not isinstance(trail, list):
                trail = []
                world._aod_solar_plasma_trail = trail
            trail.append({
                "start": old_center,
                "end": tuple(center),
                "center": tuple((old_center[i] + center[i]) * 0.5 for i in range(3)),
                "normal": normal,
                "remaining": 2.8,
                "lifetime": 2.8,
                "radius": 0.075,
                "phase": (smoke_counter * 0.73) % (2.0 * math.pi),
            })
            if len(trail) > 480:
                del trail[:-480]
            last_focus[history_key] = {"center": tuple(center), "normal": normal}
        elif travel > 1.25 or normal_dot < 0.45:
            last_focus[history_key] = {"center": tuple(center), "normal": normal}
    else:
        last_focus[history_key] = {"center": tuple(center), "normal": normal}
    if len(last_focus) > 512:
        last_focus.pop(next(iter(last_focus)))

    if smoke_counter % 5 == 0:
        spawn_particle = getattr(world, "spawn_particle", None)
        if callable(spawn_particle):
            try:
                spawn_particle(
                    "smoke", center[0], center[1] + 0.025, center[2],
                    normal[0] * 0.006, 0.018, normal[2] * 0.006,
                )
            except Exception:
                pass

    spots[:] = [spot for spot in spots if tuple(spot.get("target", ())) != target_key]
    spots.append({
        "center": tuple(center),
        "normal": normal,
        "target": target_key,
        "remaining": 0.36,
        "lifetime": 0.36,
        "phase": (int(target[0]) * 0.173 + int(target[1]) * 0.311
                  + int(target[2]) * 0.127) % (2.0 * math.pi),
    })
    if len(spots) > 160:
        del spots[:-160]
    return 1


def age_solar_spots(world, elapsed):
    try:
        dt = max(0.0, min(0.5, float(elapsed)))
    except (TypeError, ValueError, OverflowError):
        return 0
    spots = getattr(world, "_aod_solar_spots", None)
    if not isinstance(spots, list):
        spots = []
    write = 0
    for spot in spots:
        remaining = float(spot.get("remaining", 0.0)) - dt
        if remaining > 0.0:
            spot["remaining"] = remaining
            spot["phase"] = float(spot.get("phase", 0.0)) + dt * 7.0
            spots[write] = spot
            write += 1
    if write < len(spots):
        del spots[write:]

    trail = getattr(world, "_aod_solar_plasma_trail", None)
    if isinstance(trail, list) and trail:
        write = 0
        for droplet in trail:
            remaining = float(droplet.get("remaining", 0.0)) - dt
            if remaining > 0.0:
                droplet["remaining"] = remaining
                droplet["phase"] = float(droplet.get("phase", 0.0)) + dt * 5.5
                trail[write] = droplet
                write += 1
        if write < len(trail):
            del trail[write:]
    return len(spots) + (len(trail) if isinstance(trail, list) else 0)

def focus_sunlight(world, source_position, direction, *, definitions=None,
                   max_distance=BEAM_MAX_DISTANCE, emit_effect=True):
    if world is None or bool(getattr(world, "_multiplayer_client_world", False)):
        return {"entities_burned": 0, "block_ignited": False, "particles": 0}
    definitions = _block_definitions() if definitions is None else definitions
    source = tuple(int(value) for value in source_position)
    if not _sun_reaches_source(world, source, direction, definitions):
        return {"entities_burned": 0, "block_ignited": False,
                "target": None, "particles": 0, "sun_visible": False}
    target, previous, blocker_distance, hit_normal = trace_sun_beam(
        world, source, direction, definitions, max_distance=max_distance,
    )
    burned = _burn_entities_in_beam(
        world, source, direction, blocker_distance,
    )
    fire_position = None
    particles = 0
    if target is not None:
        target_type = world.get_block_type(*target)
        target_def = definitions.get(target_type, {})
        try:
            flammable = int(target_def.get("catchFire", 0) or 0) > 0
        except (AttributeError, TypeError, ValueError, OverflowError):
            flammable = False
        if flammable:
            fire_position = _place_fire_at_target(
                world, target, previous, source, direction, definitions,
                hit_normal=hit_normal,
            )
        if emit_effect:
            particles = _spawn_target_effect(
                world, source, direction, blocker_distance, target, previous,
                hit_normal=hit_normal,
            )
    return {
        "entities_burned": burned,
        "block_ignited": fire_position is not None,
        "fire_position": fire_position,
        "target": target,
        "particles": particles,
        "sun_visible": True,
    }

