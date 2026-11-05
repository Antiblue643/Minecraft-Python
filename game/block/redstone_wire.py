from __future__ import annotations

WEST = 1
EAST = 2
NORTH = 4
SOUTH = 8
UP_WEST = 16
UP_EAST = 32
UP_NORTH = 64
UP_SOUTH = 128
REDSTONE_WIRE_TEXTURE = 84

_POWER_PROVIDER_TYPES = frozenset({
    "redstone_wire",
    "lever",
    "stone_button",
    "stone_pressure_plate",
    "wooden_pressure_plate",
    "redstone_torch",
    "unlit_redstone_torch",
})


def redstone_wire_color(power_level: int):
    level = max(0, min(15, int(power_level)))
    strength = 0.30 + (level / 15.0) * 0.70
    return (strength, 0.035 + (level / 15.0) * 0.12, 0.015 + (level / 15.0) * 0.045)


def redstone_wire_render_flags(*, west=False, east=False, north=False, south=False,
                               up_west=False, up_east=False, up_north=False, up_south=False) -> int:
    return ((WEST if west else 0) | (EAST if east else 0) |
            (NORTH if north else 0) | (SOUTH if south else 0) |
            (UP_WEST if up_west else 0) | (UP_EAST if up_east else 0) |
            (UP_NORTH if up_north else 0) | (UP_SOUTH if up_south else 0))


def redstone_wire_render_flags_from_world(world, position) -> int:
    x, y, z = (int(v) for v in position)

    def kind(bx, by, bz):
        try:
            return world.get_block_type(bx, by, bz)
        except Exception:
            block = world.get_block(bx, by, bz)
            return block.block_type if block else None

    def solid(bx, by, bz):
        bt = kind(bx, by, bz)
        if not bt:
            return False
        try:
            from game.block import Block
            from game.block.fire_rules import is_normal_cube
            Block._load_defs()
            return is_normal_cube(Block._block_defs.get(bt, {}))
        except Exception:
            block = world.get_block(bx, by, bz)
            return bool(block and getattr(block, "collision", False)
                        and not getattr(block, "transparent", False))

    result = {}
    for name, dx, dz in (("west", -1, 0), ("east", 1, 0),
                         ("north", 0, -1), ("south", 0, 1)):
        same = kind(x + dx, y, z + dz) in _POWER_PROVIDER_TYPES
        side_solid = solid(x + dx, y, z + dz)
        lower = (
            not side_solid
            and kind(x + dx, y - 1, z + dz) in _POWER_PROVIDER_TYPES
        )
        higher = (
            side_solid
            and kind(x + dx, y + 1, z + dz) == "redstone_wire"
            and not solid(x, y + 1, z)
        )
        result[name] = same or lower or higher
        result["up_" + name] = higher
    return redstone_wire_render_flags(**result)


def build_redstone_wire_mesh_data(
    position, flags=0, texture_index=REDSTONE_WIRE_TEXTURE, color=(1.0, 1.0, 1.0),
):
    px, py, pz = (float(value) for value in position)
    flags = int(flags)
    tile = 1.0 / 16.0
    base = int(texture_index)
    west, east = bool(flags & WEST), bool(flags & EAST)
    north, south = bool(flags & NORTH), bool(flags & SOUTH)
    straight_x = (west or east) and not north and not south
    straight_z = (north or south) and not west and not east
    tile_index = base + 1 if straight_x or straight_z else base
    any_connection = west or east or north or south
    if straight_x or straight_z:
        x0, x1, z0, z1 = -0.5, 0.5, -0.5, 0.5
        u0 = (tile_index % 16) * tile
        v0 = 1.0 - (tile_index // 16 + 1) * tile
        u1, v1 = u0 + tile * 15.99 / 16.0, v0 + tile * 15.99 / 16.0
        if straight_z:
            uvs = ((u1, v0), (u0, v0), (u0, v1), (u1, v1))
        else:
            uvs = ((u1, v1), (u1, v0), (u0, v0), (u0, v1))
    elif not any_connection:
        x0, x1, z0, z1 = -0.5, 0.5, -0.5, 0.5
        u0 = (base % 16) * tile
        v0 = 1.0 - (base // 16 + 1) * tile
        u1, v1 = u0 + tile * 15.99/16, v0 + tile * 15.99/16
        uvs = ((u1, v1), (u1, v0), (u0, v0), (u0, v1))
    else:
        x0 = -0.5 if west else -0.5 + 5/16
        x1 = 0.5 if east else 0.5 - 5/16
        z0 = -0.5 if north else -0.5 + 5/16
        z1 = 0.5 if south else 0.5 - 5/16
        u0 = (base % 16) * tile + (0 if west else 5/16 * tile)
        u1 = (base % 16) * tile + tile * 15.99/16 - (0 if east else 5/16 * tile)
        v0 = 1.0 - (base // 16 + 1) * tile + (0 if north else 5/16 * tile)
        v1 = 1.0 - (base // 16 + 1) * tile + tile * 15.99/16 - (0 if south else 5/16 * tile)
        uvs = ((u1, v1), (u1, v0), (u0, v0), (u0, v1))
    y = py - 0.5 + 1/16
    vertices = ((px+x1,y,pz+z1),(px+x1,y,pz+z0),
                (px+x0,y,pz+z0),(px+x0,y,pz+z1))
    result = [(vertices, uvs, False, True, tuple(float(v) for v in color))]

    side_index = base + 1
    su0 = (side_index % 16) * tile
    sv0 = 1.0 - (side_index // 16 + 1) * tile
    su1, sv1 = su0 + tile * 15.99/16, sv0 + tile * 15.99/16
    bottom, top = py - 0.5, py + 0.5
    if flags & UP_WEST:
        quad = ((px-0.5+1/32,top,pz+0.5),(px-0.5+1/32,bottom,pz+0.5),
                (px-0.5+1/32,bottom,pz-0.5),(px-0.5+1/32,top,pz-0.5))
        result.append((quad, ((su1,sv0),(su0,sv0),(su0,sv1),(su1,sv1)), False, True, tuple(color)))
    if flags & UP_EAST:
        quad = ((px+0.5-1/32,bottom,pz+0.5),(px+0.5-1/32,top,pz+0.5),
                (px+0.5-1/32,top,pz-0.5),(px+0.5-1/32,bottom,pz-0.5))
        result.append((quad, ((su0,sv1),(su1,sv1),(su1,sv0),(su0,sv0)), False, True, tuple(color)))
    if flags & UP_NORTH:
        quad = ((px+0.5,bottom,pz-0.5+1/32),(px+0.5,top,pz-0.5+1/32),
                (px-0.5,top,pz-0.5+1/32),(px-0.5,bottom,pz-0.5+1/32))
        result.append((quad, ((su0,sv1),(su1,sv1),(su1,sv0),(su0,sv0)), False, True, tuple(color)))
    if flags & UP_SOUTH:
        quad = ((px+0.5,top,pz+0.5-1/32),(px+0.5,bottom,pz+0.5-1/32),
                (px-0.5,bottom,pz+0.5-1/32),(px-0.5,top,pz+0.5-1/32))
        result.append((quad, ((su1,sv0),(su0,sv0),(su0,sv1),(su1,sv1)), False, True, tuple(color)))
    return result
