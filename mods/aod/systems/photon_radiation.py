
import math

MIN_IONIZING_SIGNAL = 9
MAX_SIGNAL = 15
MAX_DAMAGE_PER_SECOND_AT_ONE_BLOCK = 4.0
MAX_DISTANCE = 12.0
LAMP_GRID_CELL_SIZE = 12
_BLOCK_OPACITY_CACHE = {}


def clamp_signal(signal):
    try:
        value = int(signal)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(MAX_SIGNAL, value))


def _block_opacity(block_type, block_defs):
    key = (id(block_defs), str(block_type))
    cached = _BLOCK_OPACITY_CACHE.get(key)
    if cached is not None:
        return cached
    definition = block_defs.get(str(block_type), {})
    try:
        opacity = int(definition.get("lightOpacity", 255))
    except (AttributeError, TypeError, ValueError):
        opacity = 255
    if len(_BLOCK_OPACITY_CACHE) > 1024:
        _BLOCK_OPACITY_CACHE.clear()
    _BLOCK_OPACITY_CACHE[key] = opacity
    return opacity


def _ionizing_lamp_grid(world, lamp_positions):
    key = (
        id(lamp_positions),
        len(lamp_positions),
        int(getattr(world, "_aod_photon_index_revision", 0)),
        int(getattr(world, "_aod_photon_radiation_revision", 0)),
    )
    cache = getattr(world, "_aod_photon_ionizing_grid_cache", None)
    if isinstance(cache, tuple) and len(cache) == 2 and cache[0] == key:
        return cache[1]

    grid = {}
    cell_size = max(1, int(LAMP_GRID_CELL_SIZE))
    for raw_position in tuple(lamp_positions):
        try:
            x, y, z = (int(value) for value in raw_position)
            if world.get_block_type(x, y, z) != "photon_lamp_lit":
                continue
            signal = clamp_signal(world.get_metadata(x, y, z))
        except Exception:
            continue
        if signal < MIN_IONIZING_SIGNAL:
            continue
        cell = (x // cell_size, y // cell_size, z // cell_size)
        grid.setdefault(cell, []).append(((x, y, z), signal))

    frozen = {cell: tuple(entries) for cell, entries in grid.items()}
    world._aod_photon_ionizing_grid_cache = (key, frozen)
    return frozen


def _nearby_ionizing_lamps(grid, target, *, cell_size=LAMP_GRID_CELL_SIZE):
    if not grid:
        return ()
    size = max(1, int(cell_size))
    span = int(math.ceil(MAX_DISTANCE / size))
    base = tuple(int(math.floor(float(target[i]) / size)) for i in range(3))
    found = []
    for cx in range(base[0] - span, base[0] + span + 1):
        for cy in range(base[1] - span, base[1] + span + 1):
            for cz in range(base[2] - span, base[2] + span + 1):
                found.extend(grid.get((cx, cy, cz), ()))
    return tuple(found)


def ionizing_fraction(signal):
    signal = clamp_signal(signal)
    if signal < MIN_IONIZING_SIGNAL:
        return 0.0
    return (signal - (MIN_IONIZING_SIGNAL - 1)) / float(MAX_SIGNAL - MIN_IONIZING_SIGNAL + 1)


def damage_rate(signal, distance_squared):
    ionizing = ionizing_fraction(signal)
    if ionizing <= 0.0:
        return 0.0
    try:
        distance_squared = max(0.25, float(distance_squared))
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return MAX_DAMAGE_PER_SECOND_AT_ONE_BLOCK * ionizing * ionizing / distance_squared


def line_of_sight_transmission(world, start, end, source_block=None):
    sx, sy, sz = (float(value) for value in start)
    ex, ey, ez = (float(value) for value in end)
    origin = (sx, sy, sz)
    delta = (ex - sx, ey - sy, ez - sz)
    current = [int(math.floor(value + 0.5)) for value in origin]
    target_cell = tuple(int(math.floor(value + 0.5)) for value in (ex, ey, ez))
    steps = []
    t_max = []
    t_delta = []
    for axis in range(3):
        direction = delta[axis]
        if direction > 0.0:
            step = 1
            boundary = current[axis] + 0.5
            t_max.append((boundary - origin[axis]) / direction)
            t_delta.append(1.0 / direction)
        elif direction < 0.0:
            step = -1
            boundary = current[axis] - 0.5
            t_max.append((boundary - origin[axis]) / direction)
            t_delta.append(-1.0 / direction)
        else:
            step = 0
            t_max.append(math.inf)
            t_delta.append(math.inf)
        steps.append(step)

    transmission = 1.0
    chunks = getattr(world, "chunks", None)
    get_chunk_index = getattr(world, "_get_chunk_index", None)
    try:
        from game.block import Block
        block_defs = Block._block_defs or {}
    except Exception:
        block_defs = {}

    max_steps = sum(abs(target_cell[axis] - current[axis]) for axis in range(3)) + 3
    for _ in range(max_steps):
        next_t = min(t_max)
        if next_t > 1.0:
            break
        for axis in range(3):
            if abs(t_max[axis] - next_t) <= 1.0e-10:
                current[axis] += steps[axis]
                t_max[axis] += t_delta[axis]
        cell = tuple(current)
        if cell == target_cell:
            break
        if cell == source_block:
            continue

        if isinstance(chunks, dict) and callable(get_chunk_index):
            try:
                if get_chunk_index(cell[0], cell[2]) not in chunks:
                    return 0.0
            except Exception:
                return 0.0

        try:
            block_type = world.get_block_type(*cell)
        except Exception:
            return 0.0
        if block_type is None or block_type == "air":
            continue
        opacity = _block_opacity(block_type, block_defs)
        if opacity >= 15:
            return 0.0
        if opacity > 0:
            transmission *= max(0.0, 1.0 - opacity / 15.0)
            if transmission <= 0.01:
                return 0.0
    return max(0.0, min(1.0, transmission))


def _entity_exposure_position(entity):
    pos = getattr(entity, "pos", None)
    if pos is None or len(pos) < 3:
        return None
    size = getattr(entity, "size", (0.6, 1.8, 0.6))
    try:
        height = float(size[1])
    except (TypeError, ValueError, IndexError):
        height = 1.8
    return float(pos[0]), float(pos[1]) + height * 0.5, float(pos[2])


def _apply_entity_damage(entity, amount):
    player_damage = getattr(entity, "damage_player", None)
    if callable(player_damage):
        try:
            return bool(player_damage(
                int(amount), source="photon", bypass_armor=True,
            ))
        except TypeError:
            return bool(player_damage(int(amount)))

    attack = getattr(entity, "attack_entity_from", None)
    if callable(attack):
        try:
            return bool(attack(None, int(amount)))
        except TypeError:
            try:
                return bool(attack(int(amount)))
            except Exception:
                return False
    return False


def apply_photon_exposure(world, lamp_positions, elapsed_seconds):
    try:
        elapsed = max(0.0, min(0.5, float(elapsed_seconds)))
    except (TypeError, ValueError, OverflowError):
        return 0
    if elapsed <= 0.0:
        return 0

    entities = list(getattr(world, "entities", ()) or ())
    player = getattr(world, "player", None)
    if player is not None and not any(player is entity for entity in entities):
        entities.append(player)
    entities = [
        entity for entity in entities
        if not getattr(entity, "dead", False)
        and getattr(entity, "health", 0) > 0
        and (callable(getattr(entity, "damage_player", None))
             or callable(getattr(entity, "attack_entity_from", None)))
    ]
    if not entities or not lamp_positions:
        return 0

    lamp_grid = _ionizing_lamp_grid(world, lamp_positions)
    if not lamp_grid:
        return 0

    doses = {}
    max_distance_sq = MAX_DISTANCE * MAX_DISTANCE
    for entity in entities:
        target = _entity_exposure_position(entity)
        if target is None:
            continue
        for source_cell, signal in _nearby_ionizing_lamps(lamp_grid, target):
            x, y, z = source_cell
            dx = target[0] - float(x)
            dy = target[1] - float(y)
            dz = target[2] - float(z)
            distance_sq = dx * dx + dy * dy + dz * dz
            if distance_sq > max_distance_sq:
                continue
            transmission = line_of_sight_transmission(
                world, (float(x), float(y), float(z)), target,
                source_block=source_cell,
            )
            if transmission <= 0.0:
                continue
            dose = damage_rate(signal, distance_sq) * transmission * elapsed
            if dose > 0.0:
                key = id(entity)
                previous = doses.get(key)
                if previous is None:
                    doses[key] = [entity, dose]
                else:
                    previous[1] += dose

    damaged = 0
    for entity, dose in doses.values():
        if getattr(entity, "gamemode", 0) == 1:
            entity._aod_photon_dose = 0.0
            continue

        accumulated = max(0.0, float(getattr(entity, "_aod_photon_dose", 0.0))) + dose
        amount = int(math.floor(accumulated + 1.0e-9))
        if amount <= 0:
            entity._aod_photon_dose = accumulated
            continue

        if _apply_entity_damage(entity, amount):
            entity._aod_photon_dose = accumulated - amount
            damaged += amount
        else:
            entity._aod_photon_dose = accumulated
    return damaged

