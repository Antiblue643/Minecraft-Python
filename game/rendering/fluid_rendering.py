from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from game.block.material import material_for_block

FLUID_TEXTURE_BASE = {"water": 205, "lava": 237}
FLUID_TYPES = frozenset(FLUID_TEXTURE_BASE)
TILE_SIZE = 1.0 / 16.0
_SIDE_INSET = 0.004


@lru_cache(maxsize=1)
def _block_definitions():
    path = Path(__file__).resolve().parents[1] / "data" / "blocks.json"
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)["blocks"]


def percent_air(metadata: int) -> float:
    metadata = int(metadata) & 15
    if metadata >= 8:
        metadata = 0
    return (metadata + 1) / 9.0


def _is_solid_material(block_type: str | None) -> bool:
    if block_type is None:
        return False
    if block_type in ("water", "lava", "fire", "torch", "rail", "ladder",
                      "sign", "standing_sign", "wall_sign"):
        return False
    material = material_for_block(block_type)
    return material.name not in ("air", "plant", "leaves", "water", "lava")


def _is_opaque_normal_cube(block_type: str | None) -> bool:
    if block_type is None or block_type in FLUID_TYPES:
        return False
    definition = _block_definitions().get(str(block_type), {})
    return (
        definition.get("model", "cube") == "cube"
        and definition.get("nCull", "all") == "all"
        and definition.get("collision", True) is not False
        and not definition.get("transparent", False)
    )


def fluid_corner_height(world, fluid_type: str, x: int, y: int, z: int,
                        cache=None) -> float:
    key = (fluid_type, int(x), int(y), int(z))
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached
    result = _compute_fluid_corner_height(world, fluid_type, int(x), int(y), int(z))
    if cache is not None:
        cache[key] = result
    return result


def _compute_fluid_corner_height(world, fluid_type: str, x: int, y: int, z: int) -> float:
    weighted_height = 0.0
    weight = 0
    for i in range(4):
        sx = int(x) - (i & 1)
        sz = int(z) - ((i >> 1) & 1)

        if world.get_block_type(sx, int(y) + 1, sz) == fluid_type:
            return 1.0

        neighbor_type = world.get_block_type(sx, int(y), sz)
        if neighbor_type != fluid_type:
            if not _is_solid_material(neighbor_type):
                weighted_height += 1.0
                weight += 1
            continue

        metadata = int(world.get_metadata(sx, int(y), sz)) & 15
        if metadata >= 8 or metadata == 0:
            weighted_height += percent_air(metadata) * 10.0
            weight += 10
        weighted_height += percent_air(metadata)
        weight += 1

    if weight <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - weighted_height / weight))


def _atlas_uv(index: int):
    col = int(index) & 15
    row = int(index) >> 4
    return col * TILE_SIZE, 1.0 - (row + 1) * TILE_SIZE


def _quad_uvs(vertices, atlas_index: int, direction):
    u0, v0 = _atlas_uv(atlas_index)
    dx, dy, dz = direction
    values = []
    for x, y, z in vertices:
        xf, yf, zf = x + 0.5, y + 0.5, z + 0.5
        if dx == 1:
            uf, vf = 1.0 - zf, yf
        elif dx == -1:
            uf, vf = zf, yf
        elif dz == 1:
            uf, vf = xf, yf
        elif dz == -1:
            uf, vf = 1.0 - xf, yf
        elif dy == 1:
            uf, vf = xf, 1.0 - zf
        else:
            uf, vf = xf, zf
        values.append((u0 + uf * TILE_SIZE, v0 + vf * TILE_SIZE))
    return tuple(values)


def _top_uvs(world, fluid_type: str, x: int, y: int, z: int):
    base = FLUID_TEXTURE_BASE[fluid_type]
    try:
        from game.world.fluid_physics import fluid_flow_vector
        flow_x, _flow_y, flow_z = fluid_flow_vector(world, x, y, z, fluid_type)
    except Exception:
        flow_x = flow_z = 0.0

    if math.hypot(flow_x, flow_z) < 1.0e-8:
        u0, v0 = _atlas_uv(base)
        u1, v1 = u0 + TILE_SIZE, v0 + TILE_SIZE
        return ((u0, v0), (u0, v1), (u1, v1), (u1, v0))

    angle = math.atan2(flow_z, flow_x) - math.pi * 0.5
    sine = math.sin(angle) * (TILE_SIZE * 0.5)
    cosine = math.cos(angle) * (TILE_SIZE * 0.5)
    u0, v0 = _atlas_uv(base + 1)
    uc = u0 + TILE_SIZE
    return (
        (uc - cosine - sine, v0 + cosine - sine),
        (uc - cosine + sine, v0 - cosine - sine),
        (uc + cosine + sine, v0 - cosine + sine),
        (uc + cosine - sine, v0 + cosine + sine),
    )


def build_fluid_mesh(world, fluid_type: str, x: int, y: int, z: int,
                     metadata: int = 0, corner_cache=None):
    if fluid_type not in FLUID_TYPES:
        return []

    x, y, z = int(x), int(y), int(z)
    above_type = world.get_block_type(x, y + 1, z)
    below_type = world.get_block_type(x, y - 1, z)
    base = FLUID_TEXTURE_BASE[fluid_type]

    h00 = fluid_corner_height(world, fluid_type, x, y, z, corner_cache)
    h01 = fluid_corner_height(world, fluid_type, x, y, z + 1, corner_cache)
    h11 = fluid_corner_height(world, fluid_type, x + 1, y, z + 1, corner_cache)
    h10 = fluid_corner_height(world, fluid_type, x + 1, y, z, corner_cache)
    bottom = -0.5
    faces = []

    def add(direction, verts, atlas, uvs=None):
        if uvs is None:
            uvs = _quad_uvs(verts, atlas, direction)
        verts = tuple(verts)
        uvs = tuple(uvs)
        face = (direction, verts, uvs, False, True, (1.0, 1.0, 1.0))
        back_face = (direction, tuple(reversed(verts)), tuple(reversed(uvs)),
                     False, True, (1.0, 1.0, 1.0))
        faces.extend((face, back_face))

    if above_type != fluid_type:
        vertices = (
            (-0.5, bottom + h00, -0.5),
            (-0.5, bottom + h01,  0.5),
            ( 0.5, bottom + h11,  0.5),
            ( 0.5, bottom + h10, -0.5),
        )
        add((0, 1, 0), vertices, base, _top_uvs(world, fluid_type, x, y, z))

    if below_type != fluid_type and not _is_opaque_normal_cube(below_type):
        vertices = (
            (-0.5, bottom, -0.5),
            ( 0.5, bottom, -0.5),
            ( 0.5, bottom,  0.5),
            (-0.5, bottom,  0.5),
        )
        add((0, -1, 0), vertices, base)

    sides = (
        ((0, 0, -1), (0, 0, -1), h00, h10,
         (( 0.5, bottom, -0.5), (-0.5, bottom, -0.5),
          (-0.5, bottom + h00, -0.5), ( 0.5, bottom + h10, -0.5))),
        ((0, 0,  1), (0, 0,  1), h11, h01,
         ((-0.5, bottom,  0.5), ( 0.5, bottom,  0.5),
          ( 0.5, bottom + h11,  0.5), (-0.5, bottom + h01,  0.5))),
        ((-1, 0, 0), (-1, 0, 0), h01, h00,
         ((-0.5, bottom, -0.5), (-0.5, bottom,  0.5),
          (-0.5, bottom + h01,  0.5), (-0.5, bottom + h00, -0.5))),
        (( 1, 0, 0), ( 1, 0, 0), h10, h11,
         (( 0.5, bottom,  0.5), ( 0.5, bottom, -0.5),
          ( 0.5, bottom + h10, -0.5), ( 0.5, bottom + h11,  0.5))),
    )
    for (dx, dy, dz), normal, _ha, _hb, vertices in sides:
        neighbor_type = world.get_block_type(x + dx, y + dy, z + dz)
        if neighbor_type == fluid_type or _is_opaque_normal_cube(neighbor_type):
            continue
        if neighbor_type is not None:
            vertices = tuple(
                (vx - dx * _SIDE_INSET, vy, vz - dz * _SIDE_INSET)
                for vx, vy, vz in vertices
            )
        add(normal, vertices, base + 1)

    return faces
