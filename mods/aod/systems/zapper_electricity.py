
from __future__ import annotations

import math
import random

from .tesla_electricity import (
    TESLA_ARC_LIFETIME,
    _living_entities,
    has_full_electrical_insulation,
    is_creative_player,
    build_arc_points,
)

ZAPPER_RADIUS = 5.5
ZAPPER_MAX_DAMAGE = 7
ZAPPER_MAX_EDGE_ARCS = 72
ZAPPER_MAX_ENTITY_ARCS = 12
ZAPPER_MAX_ACTIVE_ARCS = 160
ZAPPER_IDLE_PULSE_TICKS = 3
ZAPPER_IDLE_ARC_COUNT = 2  
ZAPPER_IDLE_EDGE_RADIUS = 2.6
ZAPPER_IDLE_EDGE_SEGMENTS = 14
ZAPPER_MAX_ACTIVE_STELMOS_EDGES = 144
ZAPPER_STELMOS_EDGE_LIFETIME = 0.36

_FACE_DELTAS = (
    (1, 0, 0), (-1, 0, 0), (0, 1, 0),
    (0, -1, 0), (0, 0, 1), (0, 0, -1),
)


def _arc_store(world):
    arcs = getattr(world, "_aod_tesla_arcs", None)
    if not isinstance(arcs, list):
        arcs = []
        world._aod_tesla_arcs = arcs
    return arcs


def _append_arcs(world, arcs):
    if not arcs:
        return 0
    store = _arc_store(world)
    store.extend(arcs)
    if len(store) > ZAPPER_MAX_ACTIVE_ARCS:
        del store[:-ZAPPER_MAX_ACTIVE_ARCS]
    return len(arcs)


def _seed(world, position, salt=0):
    now = int(getattr(world, "world_time", 0) or 0)
    x, y, z = (int(round(float(v) * 100.0)) for v in position)
    return (now * 0x45D9F3B ^ x * 73856093 ^ y * 19349663
            ^ z * 83492791 ^ int(salt)) & 0xFFFFFFFF


def _loaded(world, position):
    chunks = getattr(world, "chunks", None)
    if isinstance(chunks, dict):
        size = max(1, int(getattr(world, "CHUNK_SIZE", 16) or 16))
        return (math.floor(int(position[0]) / size),
                math.floor(int(position[2]) / size)) in chunks
    return True


def _block_definitions():
    try:
        from game.block import Block
        Block._load_defs()
        definitions = getattr(Block, "_block_defs", {})
        return definitions if isinstance(definitions, dict) else {}
    except Exception:
        return {}


def _is_solid_visible_block(block_type, definitions):
    if block_type is None or str(block_type).lower() in {
        "air", "cave_air", "water", "flowing_water", "lava", "flowing_lava",
        "fire", "portal", "nether_portal",
    }:
        return False
    definition = definitions.get(block_type, {})
    return bool(definition.get("collision", True))


def _face_corners(center, face):
    x, y, z = (float(v) for v in center)
    if face == (1, 0, 0):
        return ((x + .5, y - .5, z - .5), (x + .5, y + .5, z - .5),
                (x + .5, y + .5, z + .5), (x + .5, y - .5, z + .5))
    if face == (-1, 0, 0):
        return ((x - .5, y - .5, z + .5), (x - .5, y + .5, z + .5),
                (x - .5, y + .5, z - .5), (x - .5, y - .5, z - .5))
    if face == (0, 1, 0):
        return ((x - .5, y + .5, z - .5), (x + .5, y + .5, z - .5),
                (x + .5, y + .5, z + .5), (x - .5, y + .5, z + .5))
    if face == (0, -1, 0):
        return ((x - .5, y - .5, z + .5), (x + .5, y - .5, z + .5),
                (x + .5, y - .5, z - .5), (x - .5, y - .5, z - .5))
    if face == (0, 0, 1):
        return ((x + .5, y - .5, z + .5), (x + .5, y + .5, z + .5),
                (x - .5, y + .5, z + .5), (x - .5, y - .5, z + .5))
    return ((x - .5, y - .5, z - .5), (x - .5, y + .5, z - .5),
            (x + .5, y + .5, z - .5), (x + .5, y - .5, z - .5))


def _edge_corona_arcs(world, origin, *, rng, radius=ZAPPER_RADIUS,
                       max_arcs=ZAPPER_MAX_EDGE_ARCS):
    definitions = _block_definitions()
    ox, oy, oz = (int(round(float(v))) for v in origin)
    radius_i = int(math.ceil(float(radius)))
    radius_sq = float(radius) ** 2
    arcs = []
    scanned = 0
    for x in range(ox - radius_i, ox + radius_i + 1):
        for y in range(oy - radius_i, oy + radius_i + 1):
            for z in range(oz - radius_i, oz + radius_i + 1):
                if (x - ox) ** 2 + (y - oy) ** 2 + (z - oz) ** 2 > radius_sq:
                    continue
                position = (x, y, z)
                if not _loaded(world, position):
                    continue
                try:
                    block_type = world.get_block_type(x, y, z)
                except Exception:
                    continue
                if not _is_solid_visible_block(block_type, definitions):
                    continue

                exposed = []
                for face in _FACE_DELTAS:
                    neighbor = (x + face[0], y + face[1], z + face[2])
                    if not _loaded(world, neighbor):
                        continue
                    try:
                        adjacent = world.get_block_type(*neighbor)
                    except Exception:
                        continue
                    if not _is_solid_visible_block(adjacent, definitions):
                        exposed.append(face)
                if not exposed:
                    continue

                face_count = min(len(exposed), 1 + int(rng.random() < 0.28))
                for face in rng.sample(exposed, face_count):
                    corners = _face_corners(position, face)
                    edge_index = rng.randrange(4)
                    start = corners[edge_index]
                    end = corners[(edge_index + 1) % 4]
                    points = build_arc_points(
                        start, end, rng=rng, segments=4, jitter=0.075,
                        max_steps=4,
                    )
                    arcs.append({
                        "points": points,
                        "remaining": 0.42 + rng.random() * 0.16,
                        "lifetime": 0.58,
                        "kind": "zapper_edge",
                        "block": position,
                    })
                    if len(arcs) >= max(0, int(max_arcs)):
                        return arcs
                scanned += 1
                if scanned >= 256:
                    return arcs
    return arcs


def _stelmos_surface_quad(position, face, *, axis="x"):
    x, y, z = (float(v) for v in position)
    if face == (0, 1, 0):
        bottom = y + .512
        top = y + 1.512
        if axis == "x":
            return (
                (x - .5, bottom, z),
                (x + .5, bottom, z),
                (x + .5, top, z),
                (x - .5, top, z),
            )
        if axis == "z":
            return (
                (x, bottom, z - .5),
                (x, bottom, z + .5),
                (x, top, z + .5),
                (x, top, z - .5),
            )
        raise ValueError("floor corona axis must be 'x' or 'z'")

    if face in ((1, 0, 0), (-1, 0, 0)):
        outward = float(face[0])
        if outward > 0:
            a = (x + outward * .512, y + .5, z - .5)
            b = (x + outward * .512, y + .5, z + .5)
        else:
            a = (x + outward * .512, y + .5, z + .5)
            b = (x + outward * .512, y + .5, z - .5)
        lean = (outward * 0.70710678, 0.70710678, 0.0)
    elif face in ((0, 0, 1), (0, 0, -1)):
        outward = float(face[2])
        if outward > 0:
            a = (x + .5, y + .5, z + outward * .512)
            b = (x - .5, y + .5, z + outward * .512)
        else:
            a = (x - .5, y + .5, z + outward * .512)
            b = (x + .5, y + .5, z + outward * .512)
        lean = (0.0, 0.70710678, outward * 0.70710678)
    else:
        return None
    c = tuple(b[i] + lean[i] for i in range(3))
    d = tuple(a[i] + lean[i] for i in range(3))
    return (a, b, c, d)


def _edge_corona_segments(world, origin, *, rng, radius=ZAPPER_IDLE_EDGE_RADIUS,
                           max_segments=ZAPPER_IDLE_EDGE_SEGMENTS):
    definitions = _block_definitions()
    ox, oy, oz = (float(v) for v in origin)
    radius_i = int(math.ceil(float(radius)))
    radius_sq = float(radius) ** 2
    segments = []
    scanned = 0
    center = (ox, oy + 0.8, oz)
    for x in range(math.floor(ox - radius_i), math.ceil(ox + radius_i) + 1):
        for y in range(math.floor(oy + 0.8 - radius_i),
                       math.ceil(oy + 0.8 + radius_i) + 1):
            for z in range(math.floor(oz - radius_i), math.ceil(oz + radius_i) + 1):
                if (x - center[0]) ** 2 + (y - center[1]) ** 2 + (z - center[2]) ** 2 > radius_sq:
                    continue
                position = (x, y, z)
                if not _loaded(world, position):
                    continue
                try:
                    block_type = world.get_block_type(x, y, z)
                except Exception:
                    continue
                if not _is_solid_visible_block(block_type, definitions):
                    continue
                exposed = []
                for face in ((0, 1, 0), (1, 0, 0), (-1, 0, 0),
                             (0, 0, 1), (0, 0, -1)):
                    neighbor = (x + face[0], y + face[1], z + face[2])
                    if not _loaded(world, neighbor):
                        continue
                    try:
                        adjacent = world.get_block_type(*neighbor)
                    except Exception:
                        continue
                    if not _is_solid_visible_block(adjacent, definitions):
                        exposed.append(face)
                if not exposed:
                    continue

                horizontal_edges = [face for face in exposed if face[1] == 0]
                selected_faces = []
                if (0, 1, 0) in exposed:
                    selected_faces.append(((0, 1, 0), "x"))
                    selected_faces.append(((0, 1, 0), "z"))
                if horizontal_edges:
                    selected_faces.append((rng.choice(horizontal_edges), None))
                elif not selected_faces:
                    selected_faces.append((rng.choice(exposed), None))

                limit = max(0, int(max_segments))
                if len(segments) + len(selected_faces) > limit:
                    return segments
                for face, axis in selected_faces:
                    vertices = _stelmos_surface_quad(position, face, axis=axis or "x")
                    if vertices is None:
                        continue
                    uvs = ((0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0))
                    segments.append({
                        "vertices": vertices,
                        "uvs": uvs,
                        "surface_axis": axis,
                        "remaining": ZAPPER_STELMOS_EDGE_LIFETIME * rng.uniform(0.78, 1.0),
                        "lifetime": ZAPPER_STELMOS_EDGE_LIFETIME,
                        "kind": "zapper_stelmos_surface",
                        "block": position,
                        "face": face,
                        "width": 1.0,
                    })
                scanned += 1
                if scanned >= 192:
                    return segments
    return segments


def emit_zapper_idle_stelmos_fire(world, zapper, *, rng=None):
    if world is None or zapper is None:
        return 0
    position = tuple(float(v) for v in getattr(zapper, "pos", (0, 0, 0))[:3])
    rng = rng if rng is not None else random.Random(_seed(world, position, 0x57E1A0))

    from ..rendering.stelmosfire_texture import empty_grid, update_electricity
    grid = getattr(world, "_aod_zapper_stelmos_grid", None)
    if not isinstance(grid, list):
        grid = empty_grid()
    world._aod_zapper_stelmos_grid = update_electricity(grid, rng=rng)
    world._aod_zapper_stelmos_revision = int(
        getattr(world, "_aod_zapper_stelmos_revision", 0)
    ) + 1

    segments = _edge_corona_segments(world, position, rng=rng)
    store = getattr(world, "_aod_zapper_stelmos_edges", None)
    if not isinstance(store, list):
        store = []
        world._aod_zapper_stelmos_edges = store
    store.extend(segments)
    if len(store) > ZAPPER_MAX_ACTIVE_STELMOS_EDGES:
        del store[:-ZAPPER_MAX_ACTIVE_STELMOS_EDGES]
    return len(segments)


def age_zapper_stelmos_effects(world, elapsed):
    try:
        dt = max(0.0, min(0.5, float(elapsed)))
    except (TypeError, ValueError, OverflowError):
        return 0
    store = getattr(world, "_aod_zapper_stelmos_edges", None)
    if not isinstance(store, list) or not store:
        return 0
    write = 0
    for segment in store:
        try:
            remaining = float(segment.get("remaining", 0.0)) - dt
        except (AttributeError, TypeError, ValueError):
            continue
        if remaining > 0.0:
            segment["remaining"] = remaining
            store[write] = segment
            write += 1
    if write < len(store):
        del store[write:]
    return write


def _entity_arc(origin, entity, *, rng):
    pos = getattr(entity, "pos", None)
    size = getattr(entity, "size", (0.6, 1.8, 0.6))
    if pos is None or len(pos) < 3:
        return None
    try:
        end = (float(pos[0]), float(pos[1]) + float(size[1]) * 0.55,
               float(pos[2]))
    except (TypeError, ValueError, IndexError):
        return None
    start = (float(origin[0]), float(origin[1]) + 0.65, float(origin[2]))
    if math.dist(start, end) > ZAPPER_RADIUS + 1.0:
        return None
    return {
        "points": build_arc_points(start, end, rng=rng, segments=7,
                                   jitter=0.35, max_steps=7),
        "remaining": 0.24,
        "lifetime": 0.24,
        "kind": "zapper_entity",
    }


def _distance_to_entity(origin, entity):
    pos = getattr(entity, "pos", None)
    size = getattr(entity, "size", (0.6, 1.8, 0.6))
    if pos is None or len(pos) < 3:
        return math.inf
    try:
        px, py, pz = (float(pos[i]) for i in range(3))
        height = max(0.0, float(size[1]))
    except (TypeError, ValueError, IndexError):
        return math.inf
    dx = px - float(origin[0])
    dy = max(float(origin[1]) - (py + height), py - float(origin[1]), 0.0)
    dz = pz - float(origin[2])
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def damage_for_distance(distance, *, radius=ZAPPER_RADIUS,
                        maximum=ZAPPER_MAX_DAMAGE):
    try:
        distance = max(0.0, float(distance))
        radius = max(0.01, float(radius))
        maximum = max(0, int(maximum))
    except (TypeError, ValueError, OverflowError):
        return 0
    if distance >= radius or maximum <= 0:
        return 0
    return max(1, min(maximum, int(round(maximum * (1.0 - distance / radius)))))


def _damage_zapper_target(entity, amount, attacker):
    damage_player = getattr(entity, "damage_player", None)
    if callable(damage_player):
        try:
            return bool(damage_player(int(amount), source="zapper", attacker=attacker,
                                      bypass_armor=False))
        except TypeError:
            try:
                return bool(damage_player(int(amount), source="zapper",
                                          bypass_armor=False))
            except TypeError:
                return bool(damage_player(int(amount)))
        except Exception:
            return False
    attack = getattr(entity, "attack_entity_from", None)
    if callable(attack):
        try:
            return bool(attack(attacker, int(amount)))
        except TypeError:
            try:
                return bool(attack(int(amount)))
            except Exception:
                return False
        except Exception:
            return False
    return False


def detonate_zapper(world, zapper, *, radius=ZAPPER_RADIUS,
                    maximum_damage=ZAPPER_MAX_DAMAGE, rng=None):
    if world is None or zapper is None:
        return {"damaged": 0, "arcs": 0, "edge_arcs": 0}
    origin = tuple(float(v) for v in getattr(zapper, "pos", (0, 0, 0))[:3])
    rng = rng if rng is not None else random.Random(_seed(world, origin, 0x5A77E2))
    damaged = 0
    entity_arcs = []
    for entity in _living_entities(world):
        if entity is zapper or has_full_electrical_insulation(entity):
            continue
        distance = _distance_to_entity(origin, entity)
        amount = damage_for_distance(distance, radius=radius, maximum=maximum_damage)
        if amount <= 0:
            continue
        can_see = getattr(zapper, "can_entity_be_seen", None)
        if callable(can_see):
            try:
                if not can_see(entity):
                    continue
            except Exception:
                continue
        arc = _entity_arc(origin, entity, rng=rng)
        if arc is not None:
            entity_arcs.append(arc)
        if not is_creative_player(entity) and _damage_zapper_target(entity, amount, zapper):
            damaged += amount

    edge_arcs = _edge_corona_arcs(world, origin, rng=rng, radius=radius)
    arcs = entity_arcs[:ZAPPER_MAX_ENTITY_ARCS] + edge_arcs
    visual_count = _append_arcs(world, arcs)
    sound_player = getattr(world, "sound_player", None)
    if callable(sound_player):
        try:
            sound_player("aod.tesla_spark", volume=1.0, pitch=0.7,
                         position=origin)
        except Exception:
            pass
    return {"damaged": damaged, "arcs": visual_count,
            "edge_arcs": len(edge_arcs)}


def emit_zapper_idle_arcs(world, zapper, *, rng=None):
    if world is None or zapper is None:
        return 0
    pos = tuple(float(v) for v in getattr(zapper, "pos", (0, 0, 0))[:3])
    rng = rng if rng is not None else random.Random(_seed(world, pos, 0x1D1E))
    center = (pos[0], pos[1] + 0.78, pos[2])
    arcs = []
    for _ in range(max(1, ZAPPER_IDLE_ARC_COUNT)):
        end = (center[0] + rng.uniform(-0.42, 0.42),
               center[1] + rng.uniform(-0.45, 0.45),
               center[2] + rng.uniform(-0.42, 0.42))
        arcs.append({
            "points": build_arc_points(center, end, rng=rng,
                                       segments=3, jitter=0.11, max_steps=3),
            "remaining": 0.14,
            "lifetime": 0.14,
            "kind": "zapper_entity",
        })
    return _append_arcs(world, arcs)
