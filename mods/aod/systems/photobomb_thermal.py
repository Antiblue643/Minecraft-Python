
import math
import random

THERMAL_RADIUS = 12.0
FIRE_RADIUS = 8.0
MAX_NEW_FIRES = 32


_NEIGHBORS = (
    (0, 1, 0), (1, 0, 0), (-1, 0, 0),
    (0, 0, 1), (0, 0, -1), (0, -1, 0),
)


def _block_definitions():
    try:
        from game.block import Block
        Block._load_defs()
        return Block._block_defs or {}
    except Exception:
        return {}


def _loaded(world, position):
    chunks = getattr(world, "chunks", None)
    chunk_index = getattr(world, "_get_chunk_index", None)
    if isinstance(chunks, dict) and callable(chunk_index):
        try:
            return chunk_index(int(position[0]), int(position[2])) in chunks
        except Exception:
            return False
    return True


def _entity_center(entity):
    position = getattr(entity, "pos", None)
    if position is None or len(position) < 3:
        return None
    size = getattr(entity, "size", (0.6, 1.8, 0.6))
    try:
        height = max(0.0, float(size[1]))
    except (TypeError, ValueError, IndexError):
        height = 1.8
    return float(position[0]), float(position[1]) + height * 0.5, float(position[2])


def _thermal_transmission(world, origin, target, source_block):
    from .photon_radiation import line_of_sight_transmission
    return line_of_sight_transmission(
        world, origin, target, source_block=source_block,
    )


def _hurt_entity(entity, source, amount):
    player_damage = getattr(entity, "damage_player", None)
    if callable(player_damage):
        try:
            return bool(player_damage(int(amount), source="fire"))
        except TypeError:
            try:
                return bool(player_damage(int(amount)))
            except Exception:
                return False
        except Exception:
            return False

    attack = getattr(entity, "attack_entity_from", None)
    if callable(attack):
        try:
            return bool(attack(source, int(amount)))
        except Exception:
            return False
    return False


def _apply_entity_heat(world, source, origin, block_origin):
    radius = float(THERMAL_RADIUS)
    bounds = (
        origin[0] - radius, origin[1] - radius - 2.0, origin[2] - radius,
        origin[0] + radius, origin[1] + radius, origin[2] + radius,
    )
    iterator = getattr(world, "iter_entities_in_bounds", None)
    if callable(iterator):
        candidates = list(iterator(*bounds))
    else:
        candidates = list(getattr(world, "entities", ()) or ())

    for extra in (getattr(world, "player", None),):
        if extra is not None:
            candidates.append(extra)
    candidates.extend(tuple(getattr(world, "_mp_remote_players", ()) or ()))

    seen = set()
    burned = 0
    for entity in candidates:
        entity_id = id(entity)
        if entity_id in seen:
            continue
        seen.add(entity_id)
        if (entity is source or getattr(entity, "dead", False)
                or getattr(entity, "health", 1) <= 0):
            continue
        if getattr(entity, "gamemode", 0) == 1:
            continue
        if (not callable(getattr(entity, "damage_player", None))
                and not callable(getattr(entity, "attack_entity_from", None))):
            continue
        if bool(getattr(entity, "FIRE_IMMUNE", False)):
            continue

        target = _entity_center(entity)
        if target is None:
            continue
        dx = target[0] - origin[0]
        dy = target[1] - origin[1]
        dz = target[2] - origin[2]
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if distance > radius:
            continue

        transmission = _thermal_transmission(
            world, origin, target, block_origin,
        )
        if transmission <= 0.0:
            continue
        falloff = max(0.0, 1.0 - distance / radius)
        intensity = (falloff ** 1.5) * transmission
        if intensity < 0.035:
            continue

        damage = max(1, min(6, int(math.ceil(6.0 * intensity))))
        _hurt_entity(entity, source, damage)

        if not (getattr(entity, "in_water", False)
                or getattr(entity, "submerged", False)
                or getattr(entity, "in_lava", False)):
            burn_ticks = max(40, int(600.0 * intensity))
            entity._fire_ticks = max(
                int(getattr(entity, "_fire_ticks", 0) or 0), burn_ticks,
            )
        burned += 1
    return burned


def _normal_cube(definition):
    if not definition:
        return False
    return (
        definition.get("collision", True) is not False
        and definition.get("model", "cube") == "cube"
        and not definition.get("transparent", False)
        and definition.get("nCull", "all") == "all"
    )


def _fire_has_support(world, position, block_defs):
    x, y, z = position
    below = world.get_block_type(x, y - 1, z)
    if _normal_cube(block_defs.get(below, {})):
        return True
    for dx, dy, dz in _NEIGHBORS:
        neighbor = world.get_block_type(x + dx, y + dy, z + dz)
        if int(block_defs.get(neighbor, {}).get("catchFire", 0) or 0) > 0:
            return True
    return False


def _ignite_nearby_fuel(world, origin, block_origin, block_defs, rng):
    radius = int(math.ceil(FIRE_RADIUS))
    ox, oy, oz = origin
    center = (int(block_origin[0]), int(block_origin[1]), int(block_origin[2]))
    fire_cells = set()
    for x in range(int(math.floor(ox)) - radius, int(math.floor(ox)) + radius + 1):
        for y in range(int(math.floor(oy)) - radius, int(math.floor(oy)) + radius + 1):
            for z in range(int(math.floor(oz)) - radius, int(math.floor(oz)) + radius + 1):
                dx, dy, dz = x - ox, y - oy, z - oz
                distance = math.sqrt(dx * dx + dy * dy + dz * dz)
                if distance > FIRE_RADIUS:
                    continue
                fuel_type = world.get_block_type(x, y, z)
                if fuel_type is None:
                    continue
                fuel = block_defs.get(fuel_type, {})
                catch_fire = int(fuel.get("catchFire", 0) or 0)
                if catch_fire <= 0:
                    continue

                intensity = max(0.0, 1.0 - distance / FIRE_RADIUS)
                if intensity <= 0.0:
                    continue
                transmission = _thermal_transmission(
                    world, origin, (float(x), float(y), float(z)), center,
                )
                if transmission <= 0.0:
                    continue
                chance = min(0.95, (catch_fire / 30.0) * intensity * transmission)
                if rng.random() >= chance:
                    continue

                for dxn, dyn, dzn in _NEIGHBORS:
                    position = (x + dxn, y + dyn, z + dzn)
                    if position in fire_cells or not _loaded(world, position):
                        continue
                    if world.get_block_type(*position) is not None:
                        continue
                    if not _fire_has_support(world, position, block_defs):
                        continue
                    world.set_block(*position, "fire", meta=0, immediate_mesh=False)
                    fire_cells.add(position)
                    break
                if len(fire_cells) >= MAX_NEW_FIRES:
                    return len(fire_cells)
    return len(fire_cells)


def _spawn_thermal_particles(world, origin, rng):
    spawn = getattr(world, "spawn_particle", None)
    if not callable(spawn):
        return 0
    emitted = 0
    for index in range(128):
        dx = rng.uniform(-1.0, 1.0)
        dy = rng.uniform(-1.0, 1.0)
        dz = rng.uniform(-1.0, 1.0)
        length = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        dx, dy, dz = dx / length, dy / length, dz / length
        distance = rng.uniform(0.15, 3.5)
        speed = rng.uniform(0.08, 0.5)
        px = origin[0] + dx * distance
        py = origin[1] + dy * distance
        pz = origin[2] + dz * distance
        spawn("flame", px, py, pz, dx * speed, dy * speed, dz * speed)
        emitted += 1
        if index % 4 == 0:
            spawn("explode", px, py, pz, dx * speed * 1.8,
                  dy * speed * 1.8, dz * speed * 1.8)
            emitted += 1
    for _ in range(24):
        spawn(
            "smoke", origin[0] + rng.uniform(-1.5, 1.5),
            origin[1] + rng.uniform(-0.3, 2.0),
            origin[2] + rng.uniform(-1.5, 1.5),
        )
        emitted += 1
    return emitted


def photobomb_thermal_pulse(world, source, x, y, z, rng=random):
    if world is None:
        return {"entities_burned": 0, "fires_started": 0, "particles": 0}

    block_origin = tuple(int(math.floor(float(v) + 0.5)) for v in (x, y, z))
    origin = (float(x), float(y), float(z))

    play = None
    try:
        from game.entities.tnt import _play as play
    except Exception:
        play = None
    if callable(play):
        pitch = (1.0 + (rng.random() - rng.random()) * 0.2) * 0.7
        play(world, "random.explode", 4.0, pitch, origin)
        play(world, "fire.ignite", 1.0, 1.0, origin)

    entities_burned = _apply_entity_heat(
        world, source, origin, block_origin,
    )
    fires_started = _ignite_nearby_fuel(
        world, origin, block_origin, _block_definitions(), rng,
    )
    particles = _spawn_thermal_particles(world, origin, rng)
    return {
        "entities_burned": entities_burned,
        "fires_started": fires_started,
        "particles": particles,
    }
