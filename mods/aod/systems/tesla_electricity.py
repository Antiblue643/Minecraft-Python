
from __future__ import annotations

from collections import deque
import math
import random

TESLA_VARIANT = "tesla"
METAL_NEIGHBORS = (
    (1, 0, 0), (-1, 0, 0), (0, 1, 0),
    (0, -1, 0), (0, 0, 1), (0, 0, -1),
)
REDSTONE_COMPONENT_TYPES = frozenset({
    "redstone_wire", "redstone_torch", "unlit_redstone_torch",
    "redstone_repeater", "redstone_repeater_on", "lever",
    "stone_button", "stone_pressure_plate", "wooden_pressure_plate",
})

TESLA_PULSE_INTERVAL = 0.12
TESLA_DAMAGE_INTERVAL = TESLA_PULSE_INTERVAL
TESLA_MAX_DAMAGE = 4
TESLA_EFFECTIVE_RADIUS = 7.0
TESLA_MAX_STEPS = 6
TESLA_CONDUCTION_STEPS = TESLA_MAX_STEPS
TESLA_MAX_CONDUCTORS = 24
TESLA_REDSTONE_RADIUS = 3.5
TESLA_REDSTONE_JAM_TICKS = 10
TESLA_ARC_LIFETIME = 0.20
TESLA_REDSTONE_GRID_CELL = 8
TESLA_NETWORK_CACHE_LIMIT = 256
TESLA_EMITTER_CACHE_LIMIT = 4096
TESLA_MAX_ARCS_PER_PULSE = 20
TESLA_MAX_ARCS_PER_FRAME = 64
TESLA_MAX_ACTIVE_ARCS = 160
_METAL_BLOCK_CACHE = {}


def _is_metal_block_type(block_type):
    if block_type == TESLA_VARIANT:
        return True
    if not block_type or str(block_type).lower() in {"air", "cave_air"}:
        return False
    try:
        from game.block import Block
        Block._load_defs()
        definitions = Block._block_defs or {}
        cache_key = (id(definitions), str(block_type))
        cached = _METAL_BLOCK_CACHE.get(cache_key)
        if cached is not None:
            return bool(cached)
        definition = definitions.get(str(block_type), {})
        metal = str(definition.get("material", "")).lower() == "metal"
        if not metal:
            try:
                from game.block.material import material_for_block
                metal = material_for_block(block_type, definition).name == "metal"
            except Exception:
                metal = False
        if len(_METAL_BLOCK_CACHE) > 1024:
            _METAL_BLOCK_CACHE.clear()
        _METAL_BLOCK_CACHE[cache_key] = bool(metal)
        return bool(metal)
    except Exception:
        return False


def is_metal_block(world, position):
    try:
        return _is_metal_block_type(world.get_block_type(*position))
    except Exception:
        return False


def _block_type_conduction_class(block_type):
    if block_type is None or str(block_type).lower() in {"air", "cave_air"}:
        return "air"
    return "metal" if _is_metal_block_type(block_type) else "solid"


class _ConductorParents(dict):

    def __init__(self):
        super().__init__()
        self.air_paths = {}


def _position_is_loaded(world, position):
    x, y, z = (int(value) for value in position)
    height = getattr(world, "CHUNK_HEIGHT", None)
    if height is not None and not (0 <= y < int(height)):
        return False
    chunks = getattr(world, "chunks", None)
    if isinstance(chunks, dict):
        size = max(1, int(getattr(world, "CHUNK_SIZE", 16) or 16))
        return (x // size, z // size) in chunks
    return True


def collect_conductor_network(world, origin, *, max_steps=TESLA_CONDUCTION_STEPS,
                               max_nodes=TESLA_MAX_CONDUCTORS):
    root = tuple(int(value) for value in origin)
    try:
        if world.get_block_type(*root) != TESLA_VARIANT:
            return [], _ConductorParents()
    except Exception:
        return [], _ConductorParents()

    conductors = [(root, 0)]
    parents = _ConductorParents()
    parents[root] = None
    energized = {root}
    max_air_steps = max(0, int(max_steps))
    node_limit = max(1, int(max_nodes))

    queue = deque([(root, 0, root, (), 0)])
    visited_air = set()
    while queue and len(conductors) < node_limit:
        position, air_steps, emitter, air_path, total_path_length = queue.popleft()
        if air_steps >= max_air_steps:
            continue

        for dx, dy, dz in METAL_NEIGHBORS:
            neighbor = (position[0] + dx, position[1] + dy, position[2] + dz)
            if not _position_is_loaded(world, neighbor):
                continue

            try:
                block_type = world.get_block_type(*neighbor)
            except Exception:
                continue

            next_air_steps = air_steps + 1
            next_total_length = total_path_length + 1
            if _is_metal_block_type(block_type):
                if neighbor in energized:
                    continue
                energized.add(neighbor)
                conductors.append((neighbor, next_total_length))
                parents[neighbor] = emitter
                parents.air_paths[neighbor] = tuple(air_path)
                if len(conductors) >= node_limit:
                    break
                queue.append((neighbor, 0, neighbor, (), next_total_length))
                continue

            if block_type is None or str(block_type).lower() in {"air", "cave_air"}:
                state = (neighbor, emitter)
                if state in visited_air:
                    continue
                visited_air.add(state)
                queue.append((
                    neighbor, next_air_steps, emitter,
                    air_path + (neighbor,), next_total_length,
                ))

    return conductors, parents



def _find_emitter_reach(world, emitter, max_steps=TESLA_CONDUCTION_STEPS):
    emitter = tuple(int(value) for value in emitter)
    max_air_steps = max(0, int(max_steps))
    queue = deque([(emitter, 0, ())])
    visited_air = set()
    found = []
    found_set = set()
    while queue:
        position, air_steps, air_path = queue.popleft()
        if air_steps >= max_air_steps:
            continue
        for dx, dy, dz in METAL_NEIGHBORS:
            neighbor = (position[0] + dx, position[1] + dy, position[2] + dz)
            if not _position_is_loaded(world, neighbor):
                continue
            try:
                block_type = world.get_block_type(*neighbor)
            except Exception:
                continue
            if _is_metal_block_type(block_type):
                if neighbor not in found_set:
                    found_set.add(neighbor)
                    found.append((neighbor, air_path, air_steps + 1))
            elif block_type is None or str(block_type).lower() in {"air", "cave_air"}:
                if neighbor in visited_air:
                    continue
                visited_air.add(neighbor)
                queue.append((neighbor, air_steps + 1, air_path + (neighbor,)))
    return tuple(found)


def _cached_emitter_reach(world, emitter, max_steps=TESLA_CONDUCTION_STEPS):
    revision = int(getattr(world, "_aod_tesla_topology_revision", 0))
    cache = getattr(world, "_aod_tesla_emitter_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        world._aod_tesla_emitter_cache = cache
        world._aod_tesla_emitter_cache_revision = revision
    elif getattr(world, "_aod_tesla_emitter_cache_revision", None) != revision:
        cache.clear()
        world._aod_tesla_emitter_cache_revision = revision
    key = (tuple(int(value) for value in emitter), int(max_steps))
    result = cache.get(key)
    if result is None:
        result = _find_emitter_reach(world, key[0], key[1])
        cache[key] = result
        if len(cache) > TESLA_EMITTER_CACHE_LIMIT:
            for stale in tuple(cache)[:len(cache) - TESLA_EMITTER_CACHE_LIMIT]:
                cache.pop(stale, None)
    return result


def _cached_conductor_network(world, origin):
    origin = tuple(int(value) for value in origin)
    revision = int(getattr(world, "_aod_tesla_topology_revision", 0))
    cache = getattr(world, "_aod_tesla_network_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        world._aod_tesla_network_cache = cache
    entry = cache.get(origin)
    if entry is not None and entry[0] == revision:
        return entry[1], entry[2]

    try:
        if world.get_block_type(*origin) != TESLA_VARIANT:
            return [], _ConductorParents()
    except Exception:
        return [], _ConductorParents()

    conductors = [(origin, 0)]
    parents = _ConductorParents()
    parents[origin] = None
    energized = {origin}
    queue = deque([(origin, 0)])
    while queue and len(conductors) < TESLA_MAX_CONDUCTORS:
        emitter, emitter_distance = queue.popleft()
        for neighbor, air_path, link_length in _cached_emitter_reach(world, emitter):
            if neighbor in energized:
                continue
            energized.add(neighbor)
            distance = emitter_distance + int(link_length)
            conductors.append((neighbor, distance))
            parents[neighbor] = emitter
            parents.air_paths[neighbor] = air_path
            if len(conductors) >= TESLA_MAX_CONDUCTORS:
                break
            queue.append((neighbor, distance))

    cache[origin] = (revision, conductors, parents)
    if len(cache) > TESLA_NETWORK_CACHE_LIMIT:
        for old_origin in tuple(cache)[:len(cache) - TESLA_NETWORK_CACHE_LIMIT]:
            cache.pop(old_origin, None)
    return conductors, parents


def collect_conductors(world, origin, *, max_steps=TESLA_CONDUCTION_STEPS,
                       max_nodes=TESLA_MAX_CONDUCTORS):
    conductors, _parents = collect_conductor_network(
        world, origin, max_steps=max_steps, max_nodes=max_nodes,
    )
    return conductors


def effective_distance_to_conductors(entity, conductors):
    pos = getattr(entity, "pos", None)
    if pos is None or len(pos) < 3:
        return math.inf, None
    size = getattr(entity, "size", (0.6, 1.8, 0.6))
    try:
        target = (
            float(pos[0]),
            float(pos[1]) + max(0.0, float(size[1])) * 0.5,
            float(pos[2]),
        )
    except (TypeError, ValueError, IndexError):
        target = tuple(float(value) for value in pos[:3])

    best_sq = math.inf
    best_path_length = math.inf
    best_node = None
    for (x, y, z), path_length in conductors:
        dx = target[0] - float(x)
        dy = target[1] - float(y)
        dz = target[2] - float(z)
        distance_sq = dx * dx + dy * dy + dz * dz
        if (distance_sq < best_sq
                or (math.isclose(distance_sq, best_sq)
                    and float(path_length) < best_path_length)):
            best_sq = distance_sq
            best_path_length = float(path_length)
            best_node = (x, y, z)
    return math.sqrt(best_sq) if best_node is not None else math.inf, best_node


def damage_for_distance(distance):
    try:
        distance = max(0.0, float(distance))
    except (TypeError, ValueError, OverflowError):
        return 0
    if distance >= TESLA_EFFECTIVE_RADIUS:
        return 0
    falloff = 1.0 - distance / TESLA_EFFECTIVE_RADIUS
    raw_damage = TESLA_MAX_DAMAGE * falloff
    return max(1, min(TESLA_MAX_DAMAGE, int(round(raw_damage))))


def _stack_material(stack):
    if stack is None:
        return None
    item_id = str(getattr(stack, "id", "")).lower()
    if item_id.startswith("iron_"):
        return "iron"
    if item_id.startswith("chainmail_") or item_id.startswith("chain_"):
        return "chain"
    try:
        from game.entities.item import Item
        definition = Item.definition(item_id)
        return str(definition.get("material", "")).lower() or None
    except Exception:
        return None


def has_full_electrical_insulation(entity):
    inventory = getattr(entity, "inventory", None)
    armor = getattr(inventory, "armor", None)
    if armor is None:
        armor = getattr(entity, "armor", None)
    if not isinstance(armor, (list, tuple)) or len(armor) < 4:
        return False
    for stack in armor[:4]:
        if stack is None or int(getattr(stack, "count", 1) or 0) <= 0:
            return False
        try:
            max_damage = int(getattr(stack, "max_damage", 0) or 0)
            damage = int(getattr(stack, "damage", 0) or 0)
        except (TypeError, ValueError):
            max_damage, damage = 0, 0
        if max_damage > 0 and damage >= max_damage:
            return False
        if _stack_material(stack) not in {"iron", "chain"}:
            return False
    return True


def _apply_damage(entity, amount):
    player_damage = getattr(entity, "damage_player", None)
    if callable(player_damage):
        try:
            return bool(player_damage(int(amount), source="tesla", bypass_armor=False))
        except TypeError:
            return bool(player_damage(int(amount)))
        except Exception:
            return False
    attack = getattr(entity, "attack_entity_from", None)
    if callable(attack):
        try:
            return bool(attack(None, int(amount)))
        except TypeError:
            try:
                return bool(attack(int(amount)))
            except Exception:
                return False
        except Exception:
            return False
    return False


def _living_entities(world):
    entities = list(getattr(world, "entities", ()) or ())
    player = getattr(world, "player", None)
    if player is not None and all(player is not entity for entity in entities):
        entities.append(player)
    output = []
    seen = set()
    for entity in entities:
        if id(entity) in seen:
            continue
        seen.add(id(entity))
        if getattr(entity, "dead", False) or getattr(entity, "health", 0) <= 0:
            continue
        if not (getattr(entity, "is_living_entity", False)
                or callable(getattr(entity, "damage_player", None))):
            continue
        if not hasattr(entity, "pos"):
            continue
        output.append(entity)
    return output



def _build_living_entity_grid(entities, cell_size=8):
    size = max(1, int(cell_size))
    grid = {}
    for entity in entities:
        position = getattr(entity, "pos", None)
        if position is None or len(position) < 3:
            continue
        try:
            cell = tuple(int(math.floor(float(position[i]) / size)) for i in range(3))
        except (TypeError, ValueError, OverflowError):
            continue
        grid.setdefault(cell, []).append(entity)
    return grid


def _entities_near_conductors(entity_grid, conductors, radius=TESLA_EFFECTIVE_RADIUS,
                              cell_size=8):
    if not entity_grid or not conductors:
        return ()
    size = max(1, int(cell_size))
    span = int(math.ceil(float(radius) / size))
    selected = {}
    for (x, y, z), _path_length in conductors:
        base = (
            int(math.floor(float(x) / size)),
            int(math.floor(float(y) / size)),
            int(math.floor(float(z) / size)),
        )
        for cx in range(base[0] - span, base[0] + span + 1):
            for cy in range(base[1] - span, base[1] + span + 1):
                for cz in range(base[2] - span, base[2] + span + 1):
                    for entity in entity_grid.get((cx, cy, cz), ()):
                        selected[id(entity)] = entity
    return tuple(selected.values())

def is_creative_player(entity):
    for attribute in ("is_creative", "creative_mode", "creative"):
        value = getattr(entity, attribute, False)
        try:
            value = value() if callable(value) else value
        except Exception:
            value = False
        if bool(value):
            return True
    mode = getattr(entity, "gamemode", None)
    if isinstance(mode, str):
        return mode.strip().lower() in {"creative", "1", "gamemode.creative"}
    try:
        return int(mode) == 1
    except (TypeError, ValueError, OverflowError):
        return False


def _redstone_component_grid(world):
    redstone = getattr(world, "_redstone_system", None)
    if redstone is None:
        return {}
    components = getattr(redstone, "components", {}) or {}
    revision = int(getattr(redstone, "_aod_tesla_component_revision", 0))
    cache = getattr(world, "_aod_tesla_redstone_grid_cache", None)
    if (isinstance(cache, tuple) and len(cache) == 4 and cache[0] is redstone
            and cache[1] == revision and cache[2] == len(components)):
        return cache[3]

    cell_size = TESLA_REDSTONE_GRID_CELL
    grid = {}
    for raw_position, block_type in components.items():
        if block_type not in REDSTONE_COMPONENT_TYPES:
            continue
        position = tuple(int(value) for value in raw_position)
        cell = tuple(value // cell_size for value in position)
        grid.setdefault(cell, []).append(position)
    world._aod_tesla_redstone_grid_cache = (
        redstone, revision, len(components), grid,
    )
    return grid


def _world_redstone_components(world, conductor_positions):
    redstone = getattr(world, "_redstone_system", None)
    positions = set()
    if redstone is not None:
        grid = _redstone_component_grid(world)
        cell_size = TESLA_REDSTONE_GRID_CELL
        cell_radius = int(math.ceil(TESLA_REDSTONE_RADIUS / cell_size))
        radius_sq = TESLA_REDSTONE_RADIUS * TESLA_REDSTONE_RADIUS
        for cx, cy, cz in conductor_positions:
            base = (cx // cell_size, cy // cell_size, cz // cell_size)
            for gx in range(base[0] - cell_radius, base[0] + cell_radius + 1):
                for gy in range(base[1] - cell_radius, base[1] + cell_radius + 1):
                    for gz in range(base[2] - cell_radius, base[2] + cell_radius + 1):
                        for position in grid.get((gx, gy, gz), ()):
                            x, y, z = position
                            if ((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2 <= radius_sq):
                                positions.add(position)

    for x, y, z in conductor_positions:
        for dx, dy, dz in METAL_NEIGHBORS:
            neighbor = (x + dx, y + dy, z + dz)
            try:
                if world.get_block_type(*neighbor) in REDSTONE_COMPONENT_TYPES:
                    positions.add(neighbor)
            except Exception:
                continue
    return positions


def jam_nearby_redstone(world, conductors, *, duration_ticks=TESLA_REDSTONE_JAM_TICKS, processed_positions=None):
    now = int(getattr(world, "world_time", 0) or 0)
    expires = now + max(1, int(duration_ticks))
    jammed = getattr(world, "_aod_tesla_jammed_redstone", None)
    if not isinstance(jammed, dict):
        jammed = {}
        world._aod_tesla_jammed_redstone = jammed

    conductor_positions = [position for position, _path in conductors]
    added = set()
    for position in _world_redstone_components(world, conductor_positions):
        if processed_positions is not None and position in processed_positions:
            continue
        px, py, pz = position
        if any(
            (px - x) ** 2 + (py - y) ** 2 + (pz - z) ** 2
            <= TESLA_REDSTONE_RADIUS ** 2
            for x, y, z in conductor_positions
        ):
            jammed[position] = max(int(jammed.get(position, 0)), expires)
            added.add(position)
            if processed_positions is not None:
                processed_positions.add(position)

    redstone = getattr(world, "_redstone_system", None)
    if added and redstone is not None:
        redstone.dirty = True
    return added


def _is_jammed(world, position):
    jammed = getattr(world, "_aod_tesla_jammed_redstone", {}) or {}
    try:
        return int(jammed.get(tuple(position), 0)) > int(getattr(world, "world_time", 0) or 0)
    except (TypeError, ValueError):
        return False


def install_redstone_interference_hooks():
    try:
        from game.block.redstone import RedstoneSystem
    except Exception:
        return False

    original_solve = RedstoneSystem._solve_wire_power
    if not getattr(original_solve, "_aod_tesla_patched", False):
        def solve_wire_power(redstone, wire_positions):
            positions = {tuple(position) for position in wire_positions}
            blocked = {
                position for position in positions
                if _is_jammed(redstone.world, position)
            }
            if not blocked:
                return original_solve(redstone, positions)
            result = original_solve(redstone, positions - blocked)
            return {
                position: (0 if position in blocked else int(result.get(position, 0)))
                for position in positions
            }
        solve_wire_power._aod_tesla_patched = True
        RedstoneSystem._solve_wire_power = solve_wire_power

    original_source = RedstoneSystem._source_can_power_wire
    if not getattr(original_source, "_aod_tesla_patched", False):
        def source_can_power_wire(redstone, source_pos, wire_pos):
            if (_is_jammed(redstone.world, source_pos)
                    or _is_jammed(redstone.world, wire_pos)):
                return False
            return original_source(redstone, source_pos, wire_pos)
        source_can_power_wire._aod_tesla_patched = True
        RedstoneSystem._source_can_power_wire = source_can_power_wire

    original_receives = RedstoneSystem._block_receives_power
    if not getattr(original_receives, "_aod_tesla_patched", False):
        def block_receives_power(redstone, pos, wire_power, *, ignore_source=None):
            if _is_jammed(redstone.world, pos):
                return False
            return original_receives(
                redstone, pos, wire_power, ignore_source=ignore_source,
            )
        block_receives_power._aod_tesla_patched = True
        RedstoneSystem._block_receives_power = block_receives_power

    for method_name in ("on_block_changed", "on_chunk_loaded", "on_chunk_unloaded"):
        original_method = getattr(RedstoneSystem, method_name, None)
        if not callable(original_method) or getattr(original_method, "_aod_tesla_revision_patched", False):
            continue

        def make_revision_wrapper(method, name):
            def wrapped(redstone, *args, **kwargs):
                result = method(redstone, *args, **kwargs)
                redstone._aod_tesla_component_revision = int(
                    getattr(redstone, "_aod_tesla_component_revision", 0)
                ) + 1
                return result
            wrapped._aod_tesla_revision_patched = True
            wrapped.__name__ = f"aod_tesla_{name}"
            return wrapped

        setattr(RedstoneSystem, method_name, make_revision_wrapper(original_method, method_name))
    return True


def expire_redstone_interference(world):
    jammed = getattr(world, "_aod_tesla_jammed_redstone", None)
    if not isinstance(jammed, dict) or not jammed:
        return 0
    now = int(getattr(world, "world_time", 0) or 0)
    expired = [position for position, expiry in jammed.items() if int(expiry) <= now]
    for position in expired:
        jammed.pop(position, None)
    if expired:
        redstone = getattr(world, "_redstone_system", None)
        if redstone is not None:
            redstone.dirty = True
    return len(expired)


def _block_center(position):
    return tuple(float(value) for value in position)


def _block_surface_point(position, toward, *, clearance=0.06):
    center = _block_center(position)
    target = tuple(float(value) for value in toward)
    delta = tuple(target[i] - center[i] for i in range(3))
    length = math.sqrt(sum(value * value for value in delta))
    if length <= 1.0e-9:
        return (center[0], center[1] + 0.5 + clearance, center[2])
    direction = tuple(value / length for value in delta)
    major = max(abs(value) for value in direction)
    travel = 0.5 / max(major, 1.0e-9) + float(clearance)
    return tuple(center[i] + direction[i] * travel for i in range(3))


def _inside_unit_block(point, center, *, margin=1.0e-4):
    return all(abs(float(point[i]) - float(center[i])) < 0.5 - margin for i in range(3))


def _conductor_surface_endpoints(source, target):
    source_center = _block_center(source)
    target_center = _block_center(target)
    start = _block_surface_point(source, target_center)
    end = _block_surface_point(target, source_center)
    if not (_inside_unit_block(start, target_center)
            or _inside_unit_block(end, source_center)):
        return start, end

    delta = tuple(target_center[i] - source_center[i] for i in range(3))
    length = math.sqrt(sum(value * value for value in delta)) or 1.0
    direction = tuple(value / length for value in delta)
    perpendicular = (
        -direction[0] * direction[1],
        1.0 - direction[1] * direction[1],
        -direction[2] * direction[1],
    )
    perp_length = math.sqrt(sum(value * value for value in perpendicular))
    if perp_length < 1.0e-6:
        perpendicular = (1.0, 0.0, 0.0)
        perp_length = 1.0
    perpendicular = tuple(value / perp_length for value in perpendicular)
    travel = 0.5 / max(abs(value) for value in perpendicular) + 0.15
    offset = tuple(value * travel for value in perpendicular)
    return (
        tuple(source_center[i] + offset[i] for i in range(3)),
        tuple(target_center[i] + offset[i] for i in range(3)),
    )


def _conductor_arc_points(start, end, air_path, *, rng, max_steps):
    if not air_path:
        return build_arc_points(
            start, end, rng=rng, segments=5, jitter=0.10,
            max_steps=max_steps,
        )
    points = [tuple(float(value) for value in start)]
    max_jitter = 0.17
    for cell in air_path:
        center = _block_center(cell)
        points.append(tuple(
            center[axis] + rng.uniform(-max_jitter, max_jitter)
            for axis in range(3)
        ))
    points.append(tuple(float(value) for value in end))
    point_limit = max(2, int(max_steps)) + 1
    if len(points) > point_limit:
        points = points[:point_limit - 1] + [points[-1]]
    return tuple(points)


def build_arc_points(start, end, *, rng=None, segments=7, jitter=0.32,
                     max_steps=TESLA_MAX_STEPS):
    rng = rng or random.Random()
    start = tuple(float(value) for value in start)
    end = tuple(float(value) for value in end)
    step_limit = max(2, int(max_steps))
    count = min(step_limit, max(2, int(segments)))
    dx, dy, dz = (end[i] - start[i] for i in range(3))
    length = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
    direction = (dx / length, dy / length, dz / length)
    reference = (0.0, 1.0, 0.0)
    if abs(direction[1]) > 0.90:
        reference = (1.0, 0.0, 0.0)
    bx = direction[1] * reference[2] - direction[2] * reference[1]
    by = direction[2] * reference[0] - direction[0] * reference[2]
    bz = direction[0] * reference[1] - direction[1] * reference[0]
    basis_length = math.sqrt(bx * bx + by * by + bz * bz) or 1.0
    basis_a = (bx / basis_length, by / basis_length, bz / basis_length)
    cx = direction[1] * basis_a[2] - direction[2] * basis_a[1]
    cy = direction[2] * basis_a[0] - direction[0] * basis_a[2]
    cz = direction[0] * basis_a[1] - direction[1] * basis_a[0]
    cross_length = math.sqrt(cx * cx + cy * cy + cz * cz) or 1.0
    basis_b = (cx / cross_length, cy / cross_length, cz / cross_length)
    points = [start]
    for index in range(1, count):
        t = index / float(count)
        envelope = math.sin(math.pi * t) * float(jitter)
        offset_a = rng.uniform(-1.0, 1.0) * envelope
        offset_b = rng.uniform(-1.0, 1.0) * envelope * 0.7
        points.append(tuple(
            start[axis] + (end[axis] - start[axis]) * t
            + basis_a[axis] * offset_a + basis_b[axis] * offset_b
            for axis in range(3)
        ))
    points.append(end)
    return tuple(points)


def _emit_arcs(world, origin, conductors, parents, hit_entities, *, rng,
               max_steps=TESLA_MAX_STEPS, max_arcs=TESLA_MAX_ARCS_PER_PULSE):
    world_arcs = getattr(world, "_aod_tesla_arcs", None)
    if not isinstance(world_arcs, list):
        world_arcs = []
        world._aod_tesla_arcs = world_arcs
    try:
        budget = max(0, int(max_arcs))
    except (TypeError, ValueError, OverflowError):
        budget = TESLA_MAX_ARCS_PER_PULSE
    if budget <= 0:
        return 0

    air_paths = getattr(parents, "air_paths", {}) or {}

    def conductor_arcs():
        for (x, y, z), _path_length in conductors[1:]:
            position = (x, y, z)
            parent = parents.get(position)
            if parent is None:
                continue
            start, end = _conductor_surface_endpoints(parent, position)
            yield {
                "points": _conductor_arc_points(
                    start, end, air_paths.get(position, ()),
                    rng=rng, max_steps=max_steps,
                ),
                "remaining": TESLA_ARC_LIFETIME,
                "lifetime": TESLA_ARC_LIFETIME,
                "kind": "conductor",
                "source": parent,
                "target": position,
            }

    def entity_arcs():
        for entity, _damage, _distance, node in hit_entities:
            pos = getattr(entity, "pos", None)
            if pos is None:
                continue
            size = getattr(entity, "size", (0.6, 1.8, 0.6))
            try:
                end = (
                    float(pos[0]),
                    float(pos[1]) + float(size[1]) * 0.55,
                    float(pos[2]),
                )
            except (TypeError, ValueError, IndexError):
                end = tuple(float(value) for value in pos[:3])
            source_block = node if node is not None else origin
            start = _block_surface_point(source_block, end)
            if math.dist(start, end) > 9.0:
                continue
            yield {
                "points": build_arc_points(
                    start, end, rng=rng, segments=7, jitter=0.48,
                    max_steps=max_steps,
                ),
                "remaining": TESLA_ARC_LIFETIME,
                "lifetime": TESLA_ARC_LIFETIME,
                "kind": "entity",
            }

    def spark_arcs():
        center = _block_center(origin)
        spark_start = (center[0], center[1] + 0.56, center[2])
        for _ in range(3):
            end = (
                center[0] + rng.uniform(-0.75, 0.75),
                center[1] + rng.uniform(0.65, 1.0),
                center[2] + rng.uniform(-0.75, 0.75),
            )
            yield {
                "points": build_arc_points(
                    spark_start, end, rng=rng, segments=4, jitter=0.22,
                    max_steps=max_steps,
                ),
                "remaining": TESLA_ARC_LIFETIME,
                "lifetime": TESLA_ARC_LIFETIME,
                "kind": "spark",
            }

    iterators = [iter(conductor_arcs()), iter(entity_arcs()), iter(spark_arcs())]
    exhausted = [False, False, False]
    arcs = []
    category = 0
    idle = 0
    while len(arcs) < budget and not all(exhausted):
        index = category % len(iterators)
        category += 1
        if exhausted[index]:
            idle += 1
            if idle >= len(iterators):
                break
            continue
        try:
            arcs.append(next(iterators[index]))
            idle = 0
        except StopIteration:
            exhausted[index] = True
            idle += 1
    world_arcs.extend(arcs)
    if len(world_arcs) > TESLA_MAX_ACTIVE_ARCS:
        del world_arcs[:-TESLA_MAX_ACTIVE_ARCS]
    return len(arcs)


def age_tesla_arcs(world, elapsed):
    try:
        dt = max(0.0, min(0.5, float(elapsed)))
    except (TypeError, ValueError, OverflowError):
        return 0
    arcs = getattr(world, "_aod_tesla_arcs", None)
    if not isinstance(arcs, list) or not arcs:
        return 0
    write = 0
    for arc in arcs:
        try:
            remaining = float(arc.get("remaining", 0.0)) - dt
        except (AttributeError, TypeError, ValueError):
            continue
        if remaining > 0.0:
            arc["remaining"] = remaining
            arcs[write] = arc
            write += 1
    if write < len(arcs):
        del arcs[write:]
    return write


def discharge_tesla(world, origin, *, living_entities=None, living_entity_grid=None,
                    max_visual_arcs=TESLA_MAX_ARCS_PER_PULSE,
                    processed_redstone=None):
    conductors, parents = _cached_conductor_network(world, origin)
    if not conductors:
        return {"conductors": 0, "damaged": 0, "jammed": 0, "arcs": 0}

    try:
        visual_budget = max(0, int(max_visual_arcs))
    except (TypeError, ValueError, OverflowError):
        visual_budget = TESLA_MAX_ARCS_PER_PULSE
    arc_targets = [] if visual_budget > 0 else None
    total_damage = 0
    if living_entities is None:
        living_entities = _living_entities(world)
    if living_entity_grid is not None:
        living_entities = _entities_near_conductors(
            living_entity_grid, conductors,
        )
    for entity in living_entities:
        distance, node = effective_distance_to_conductors(entity, conductors)
        amount = damage_for_distance(distance)
        if amount <= 0 or has_full_electrical_insulation(entity):
            continue
        if arc_targets is not None:
            arc_targets.append((entity, amount, distance, node))
        if not is_creative_player(entity) and _apply_damage(entity, amount):
            total_damage += amount

    jammed = jam_nearby_redstone(
        world, conductors, processed_positions=processed_redstone,
    )
    arc_count = 0
    if visual_budget > 0:
        seed = (
            (int(getattr(world, "world_time", 0) or 0) * 0x45D9F3B)
            ^ (int(origin[0]) * 73856093)
            ^ (int(origin[1]) * 19349663)
            ^ (int(origin[2]) * 83492791)
        ) & 0xFFFFFFFF
        arc_count = _emit_arcs(
            world, origin, conductors, parents, arc_targets,
            rng=random.Random(seed), max_steps=TESLA_MAX_STEPS,
            max_arcs=visual_budget,
        )
    return {
        "conductors": len(conductors),
        "damaged": total_damage,
        "jammed": len(jammed),
        "arcs": arc_count,
    }

