from __future__ import annotations

from game.block.fire_rules import can_catch_fire, is_normal_cube
from game.block.door_hinges import DOOR_TYPES
from game.block.plant_rules import cactus_can_stay, reed_can_stay

_PLANT_SOIL = frozenset({"grass_block", "dirt", "farmland"})
_MUSHROOM_SOIL = frozenset({"grass_block", "dirt", "farmland", "stone", "cobblestone", "mossy_cobblestone"})
_CROP_SOIL = frozenset({"farmland"})
_RAIL_SUPPORT = None


def _is_solid(world, x, y, z):
    block = world.get_block(x, y, z)
    if block is None:
        return False
    if getattr(block, "collision", None) is False:
        return False
    if getattr(block, "intangible", False) and getattr(block, "collision", None) is not True:
        return False
    n_cull = getattr(block, "nCull", "all")
    if n_cull == "none" and block.block_type not in ("glass", "mob_spawner", "ice"):
        return False
    return True


def _is_opaque_cube(world, x, y, z):
    return _normal_cube(world, x, y, z)


def _definition_at(world, x, y, z):
    block = world.get_block(x, y, z)
    return getattr(block, "definition", None) if block is not None else None


def _normal_cube(world, x, y, z):
    return is_normal_cube(_definition_at(world, x, y, z))


def _can_catch_fire(world, x, y, z):
    return can_catch_fire(_definition_at(world, x, y, z))


_NEIGHBORS_6 = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def can_neighbor_catch_fire(world, x, y, z):
    return any(_can_catch_fire(world, x + dx, y + dy, z + dz) for dx, dy, dz in _NEIGHBORS_6)


def fire_is_supported(world, x, y, z):
    return _normal_cube(world, x, y - 1, z) or can_neighbor_catch_fire(world, x, y, z)


def extinguish_unsupported_fire(world, x, y, z):
    removed = 0
    for dx, dy, dz in _NEIGHBORS_6:
        fx, fy, fz = x + dx, y + dy, z + dz
        if world.get_block_type(fx, fy, fz) == "fire" and not fire_is_supported(world, fx, fy, fz):
            world.remove_block(fx, fy, fz, immediate_mesh=True)
            removed += 1
    return removed


def facing_from_yaw(yaw_degrees):
    a = yaw_degrees % 360.0
    if a < 0:
        a += 360.0
    if 45 <= a < 135:
        return 5
    if 135 <= a < 225:
        return 2
    if 225 <= a < 315:
        return 4
    return 3



def _door_lower_cell(world, x, y, z):
    if world.get_block_type(x, y, z) not in DOOR_TYPES:
        return x, y, z
    meta = world.get_metadata(x, y, z) if hasattr(world, "get_metadata") else 0
    if meta & 8:
        y -= 1
    return x, y, z


def _door_pair_axis(meta):
    return "x" if (int(meta) & 3) in (1, 3) else "z"


def align_door_meta_with_neighbor(world, x, y, z, meta, door_type="wooden_door"):
    meta = int(meta) & 3
    for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        nx, nz = x + dx, z + dz
        if world.get_block_type(nx, y, nz) != door_type:
            continue
        nmeta = int(world.get_metadata(nx, y, nz)) & 7 if hasattr(world, "get_metadata") else 0
        neighbor_axis = _door_pair_axis(nmeta)
        if (dx != 0 and neighbor_axis == "x") or (dz != 0 and neighbor_axis == "z"):
            return nmeta & 3
    return meta


def toggle_door_metadata(world, x, y, z):
    x, y, z = _door_lower_cell(world, x, y, z)
    if world.get_block_type(x, y, z) != "wooden_door":
        return False, None
    meta = int(world.get_metadata(x, y, z)) & 7 if hasattr(world, "get_metadata") else 0
    was_open = bool(meta & 4)
    return was_open, meta ^ 4


def placement_position(world, hit, face):
    hit = tuple(int(value) for value in hit)
    face = tuple(int(value) for value in face)
    get_type = getattr(world, "get_block_type", None)
    if callable(get_type) and get_type(*hit) == "snow":
        return hit
    return tuple(hit[i] + face[i] for i in range(3))


def can_place(world, block_type, x, y, z, face=None, player_yaw=0.0):
    height_limit = getattr(world, "CHUNK_HEIGHT", 128)
    if y < 0 or y >= height_limit:
        return False

    existing = world.get_block(x, y, z)
    if existing is not None:
        if existing.block_type not in ("water", "lava", "fire", "snow"):
            return False
        if existing.block_type == "snow" and block_type == "snow":
            return False

    if block_type in ("cake", "dispenser"):
        return _is_solid(world, x, y - 1, z)

    if block_type == "chest":
        directions = ((-1, 0), (1, 0), (0, -1), (0, 1))
        neighbors = [
            (x + dx, y, z + dz) for dx, dz in directions
            if world.get_block_type(x + dx, y, z + dz) == "chest"
        ]
        if len(neighbors) > 1:
            return False
        if neighbors:
            nx, ny, nz = neighbors[0]
            return not any(
                world.get_block_type(nx + dx, ny, nz + dz) == "chest"
                for dx, dz in directions
                if (nx + dx, ny, nz + dz) != (x, y, z)
            )
        return True

    if block_type in ("yellow_flower", "red_flower", "sapling"):
        below = world.get_block_type(x, y - 1, z) if hasattr(world, "get_block_type") else None
        if below is None:
            b = world.get_block(x, y - 1, z)
            below = b.block_type if b else None
        return below in _PLANT_SOIL

    if block_type == "reed":
        return reed_can_stay(world, x, y, z)

    if block_type == "cactus":
        return cactus_can_stay(world, x, y, z)

    if block_type in ("brown_mushroom", "red_mushroom"):
        return _normal_cube(world, x, y - 1, z)

    if block_type == "crops":
        b = world.get_block(x, y - 1, z)
        below = b.block_type if b else None
        return below in _CROP_SOIL

    if block_type == "fence":
        below = world.get_block_type(x, y - 1, z) if hasattr(world, "get_block_type") else None
        if below is None:
            b = world.get_block(x, y - 1, z)
            below = b.block_type if b else None
        if below == "fence":
            return False
        return _is_solid(world, x, y - 1, z)

    if block_type in ("rail", "redstone_wire", "stone_pressure_plate", "wooden_pressure_plate"):
        return _normal_cube(world, x, y - 1, z)

    if block_type == "stone_button":
        fx, fy, fz = tuple(face or (0, 0, 0))
        return ((fx == 1 and _is_opaque_cube(world, x - 1, y, z))
                or (fx == -1 and _is_opaque_cube(world, x + 1, y, z))
                or (fz == 1 and _is_opaque_cube(world, x, y, z - 1))
                or (fz == -1 and _is_opaque_cube(world, x, y, z + 1)))

    if block_type in ("wooden_door", "iron_door"):
        if hasattr(world, "CHUNK_HEIGHT") and y + 1 >= world.CHUNK_HEIGHT:
            return False
        above = world.get_block(x, y + 1, z)
        if above is not None and above.block_type not in ("water", "lava"):
            return False
        return _is_solid(world, x, y - 1, z)

    if block_type in ("torch", "redstone_torch", "unlit_redstone_torch", "lever"):
        if _is_opaque_cube(world, x, y - 1, z):
            return True
        if _is_opaque_cube(world, x - 1, y, z):
            return True
        if _is_opaque_cube(world, x + 1, y, z):
            return True
        if _is_opaque_cube(world, x, y, z - 1):
            return True
        if _is_opaque_cube(world, x, y, z + 1):
            return True
        return False

    if block_type == "ladder":
        return (
            _is_opaque_cube(world, x - 1, y, z)
            or _is_opaque_cube(world, x + 1, y, z)
            or _is_opaque_cube(world, x, y, z - 1)
            or _is_opaque_cube(world, x, y, z + 1)
        )

    if block_type == "fire":
        return fire_is_supported(world, x, y, z)

    if block_type in ("pumpkin", "jack_o_lantern"):
        return _is_solid(world, x, y - 1, z)

    if block_type in ("sign", "wall_sign"):
        fx, fy, fz = tuple(face or (0, 0, 0))
        if block_type == "sign" and (fx, fy, fz) == (0, 1, 0):
            return _normal_cube(world, x, y - 1, z)
        if block_type == "sign" or fy != 0 or abs(fx) + abs(fz) != 1:
            return False
        return _normal_cube(world, x - fx, y, z - fz)

    return True


def placement_meta(world, block_type, x, y, z, face=None, player_yaw=0.0):
    face = face or (0, 0, 0)
    fx, fy, fz = face

    if block_type == "stone_button":
        if fx == 1 and _is_opaque_cube(world, x - 1, y, z):
            return 1
        if fx == -1 and _is_opaque_cube(world, x + 1, y, z):
            return 2
        if fz == 1 and _is_opaque_cube(world, x, y, z - 1):
            return 3
        if fz == -1 and _is_opaque_cube(world, x, y, z + 1):
            return 4
        if _is_opaque_cube(world, x - 1, y, z): return 1
        if _is_opaque_cube(world, x + 1, y, z): return 2
        if _is_opaque_cube(world, x, y, z - 1): return 3
        if _is_opaque_cube(world, x, y, z + 1): return 4
        return 0

    if block_type in ("torch", "redstone_torch", "unlit_redstone_torch", "lever"):
        if fy == 1 and _is_opaque_cube(world, x, y - 1, z):
            return 5
        if fx == 1 and _is_opaque_cube(world, x - 1, y, z):
            return 1
        if fx == -1 and _is_opaque_cube(world, x + 1, y, z):
            return 2
        if fz == 1 and _is_opaque_cube(world, x, y, z - 1):
            return 3
        if fz == -1 and _is_opaque_cube(world, x, y, z + 1):
            return 4
        if _is_opaque_cube(world, x, y - 1, z):
            return 5
        if _is_opaque_cube(world, x - 1, y, z):
            return 1
        if _is_opaque_cube(world, x + 1, y, z):
            return 2
        if _is_opaque_cube(world, x, y, z - 1):
            return 3
        if _is_opaque_cube(world, x, y, z + 1):
            return 4
        return 5

    if block_type == "ladder":
        if fz == 1 and _is_opaque_cube(world, x, y, z - 1):
            return 3
        if fz == -1 and _is_opaque_cube(world, x, y, z + 1):
            return 2
        if fx == 1 and _is_opaque_cube(world, x - 1, y, z):
            return 5
        if fx == -1 and _is_opaque_cube(world, x + 1, y, z):
            return 4
        if _is_opaque_cube(world, x, y, z + 1):
            return 2
        if _is_opaque_cube(world, x, y, z - 1):
            return 3
        if _is_opaque_cube(world, x + 1, y, z):
            return 4
        if _is_opaque_cube(world, x - 1, y, z):
            return 5
        return 2

    if block_type in ("furnace", "furnace_lit", "chest"):
        return facing_from_yaw(player_yaw)

    if block_type == "dispenser":
        return facing_from_yaw(player_yaw)

    if block_type in ("pumpkin", "jack_o_lantern"):
        return {2: 0, 5: 1, 3: 2, 4: 3}[facing_from_yaw(player_yaw)]

    if block_type == "wall_sign":
        return {
            (0, 0, -1): 2,
            (0, 0, 1): 3,
            (-1, 0, 0): 4,
            (1, 0, 0): 5,
        }.get((fx, fy, fz), 2)

    if block_type == "sign":
        import math
        a = (float(player_yaw) + 180.0) * 16.0 / 360.0 - 0.5
        return int(math.floor(a)) & 15

    if block_type in ("stairs", "cobblestone_stairs"):
        return {3: 3, 5: 1, 2: 2, 4: 0}[facing_from_yaw(player_yaw)]


    if block_type in ("wooden_door", "iron_door"):
        a = player_yaw % 360.0
        if a < 0:
            a += 360.0
        if a < 45 or a >= 315:
            return 3
        if a < 135:
            return 2
        if a < 225:
            return 1
        return 0

    if block_type == "rail":
        meta = facing_from_yaw(player_yaw)
        return 1 if meta in (4, 5) else 0

    return 0


def _is_rail(world, x, y, z):
    return world.get_block_type(x, y, z) == "rail"

def _rail_at(world, x, y, z):
    if _is_rail(world, x, y, z):
        return (x, y, z)
    if _is_rail(world, x, y + 1, z):
        return (x, y + 1, z)
    if _is_rail(world, x, y - 1, z):
        return (x, y - 1, z)
    return None


_RAIL_CONNECTIONS = {
    0: ((0, 0, -1), (0, 0, 1)),
    1: ((-1, 0, 0), (1, 0, 0)),
    2: ((-1, 0, 0), (1, 1, 0)),
    3: ((-1, 1, 0), (1, 0, 0)),
    4: ((0, 1, -1), (0, 0, 1)),
    5: ((0, 0, -1), (0, 1, 1)),
    6: ((1, 0, 0), (0, 0, 1)),
    7: ((-1, 0, 0), (0, 0, 1)),
    8: ((-1, 0, 0), (0, 0, -1)),
    9: ((1, 0, 0), (0, 0, -1)),
}


class _MinecartTrackLogic:
    def __init__(self, world, x, y, z):
        self.world = world
        self.x = int(x)
        self.y = int(y)
        self.z = int(z)
        self.meta = int(world.get_metadata(self.x, self.y, self.z)) & 15
        self.connected = []
        self._calculate_connected()

    def _calculate_connected(self):
        self.connected = []
        for dx, dy, dz in _RAIL_CONNECTIONS.get(self.meta, _RAIL_CONNECTIONS[0]):
            self.connected.append((self.x + dx, self.y + dy, self.z + dz))

    def _find_logic(self, pos):
        found = _rail_at(self.world, pos[0], pos[1], pos[2])
        if found is None:
            return None
        return _MinecartTrackLogic(self.world, *found)

    def _is_connected_to(self, other):
        for cx, _cy, cz in self.connected:
            if cx == other.x and cz == other.z:
                return True
        return False

    def _is_in_track(self, x, z):
        for cx, _cy, cz in self.connected:
            if cx == x and cz == z:
                return True
        return False

    def _refresh_connected(self):
        i = 0
        while i < len(self.connected):
            other = self._find_logic(self.connected[i])
            if other is not None and other._is_connected_to(self):
                self.connected[i] = (other.x, other.y, other.z)
                i += 1
            else:
                self.connected.pop(i)

    def _can_accept(self, other):
        if self._is_connected_to(other):
            return True
        if len(self.connected) == 2:
            return False
        return True

    def _can_connect_from(self, x, y, z):
        other = self._find_logic((x, y, z))
        if other is None:
            return False
        other._refresh_connected()
        return other._can_accept(self)

    def _has_rail_neighbor(self, dx, dz):
        return _rail_at(self.world, self.x + dx, self.y, self.z + dz) is not None

    def connection_count(self):
        count = 0
        for dx, dz in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            if self._has_rail_neighbor(dx, dz):
                count += 1
        return count

    def _meta_from_flags(self, north, south, west, east, powered=False):
        meta = -1
        if (north or south) and not west and not east:
            meta = 0
        if (west or east) and not north and not south:
            meta = 1
        if south and east and not north and not west:
            meta = 6
        if south and west and not north and not east:
            meta = 7
        if north and west and not south and not east:
            meta = 8
        if north and east and not south and not west:
            meta = 9

        if meta == -1:
            if north or south:
                meta = 0
            if west or east:
                meta = 1
            if powered:
                if south and east:
                    meta = 6
                if west and south:
                    meta = 7
                if east and north:
                    meta = 9
                if north and west:
                    meta = 8
            else:
                if north and west:
                    meta = 8
                if east and north:
                    meta = 9
                if west and south:
                    meta = 7
                if south and east:
                    meta = 6

        if meta == 0:
            if self.world.get_block_type(self.x, self.y + 1, self.z - 1) == "rail":
                meta = 4
            if self.world.get_block_type(self.x, self.y + 1, self.z + 1) == "rail":
                meta = 5
        if meta == 1:
            if self.world.get_block_type(self.x + 1, self.y + 1, self.z) == "rail":
                meta = 2
            if self.world.get_block_type(self.x - 1, self.y + 1, self.z) == "rail":
                meta = 3
        if meta < 0:
            meta = 0
        return meta

    def _is_rail_powered(self):
        world = self.world
        redstone = getattr(world, "_redstone_system", None)
        positions = (
            (self.x, self.y, self.z),
            (self.x, self.y - 1, self.z),
        )
        if redstone is not None and hasattr(redstone, "_block_receives_power"):
            try:
                wire_positions = {
                    pos for pos, typ in redstone.components.items()
                    if typ == "redstone_wire" and world.get_block_type(*pos) == "redstone_wire"
                }
                wire_power = redstone._solve_wire_power(wire_positions)
                for pos in positions:
                    if redstone._block_receives_power(pos, wire_power):
                        return True
                for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, -1, 0)):
                    npos = (self.x + dx, self.y + dy, self.z + dz)
                    if redstone._block_receives_power(npos, wire_power):
                        if dy == -1 or redstone._is_full_cube(*npos):
                            return True
            except Exception:
                pass
        for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            nx, ny, nz = self.x + dx, self.y + dy, self.z + dz
            btype = world.get_block_type(nx, ny, nz)
            if btype == "redstone_torch":
                return True
            if btype == "lever" and (int(world.get_metadata(nx, ny, nz)) & 8):
                return True
            if btype == "stone_button" and (int(world.get_metadata(nx, ny, nz)) & 8):
                return True
            if btype in ("stone_pressure_plate", "wooden_pressure_plate") and (
                int(world.get_metadata(nx, ny, nz)) & 1
            ):
                return True
            if btype == "redstone_wire" and int(world.get_metadata(nx, ny, nz)) > 0:
                return True
        return False

    def _connect_to_neighbor(self, other):
        self.connected.append((other.x, other.y, other.z))
        north = self._is_in_track(self.x, self.z - 1)
        south = self._is_in_track(self.x, self.z + 1)
        west = self._is_in_track(self.x - 1, self.z)
        east = self._is_in_track(self.x + 1, self.z)
        meta = self._meta_from_flags(north, south, west, east, powered=self._is_rail_powered())
        self.world.set_block(self.x, self.y, self.z, "rail", meta=meta, immediate_mesh=True)

    def place(self, powered=None):
        if powered is None:
            powered = self._is_rail_powered()
        if self.connection_count() >= 3:
            north = self._has_rail_neighbor(0, -1)
            south = self._has_rail_neighbor(0, 1)
            west = self._has_rail_neighbor(-1, 0)
            east = self._has_rail_neighbor(1, 0)
        else:
            north = self._can_connect_from(self.x, self.y, self.z - 1)
            south = self._can_connect_from(self.x, self.y, self.z + 1)
            west = self._can_connect_from(self.x - 1, self.y, self.z)
            east = self._can_connect_from(self.x + 1, self.y, self.z)
        meta = self._meta_from_flags(north, south, west, east, powered=bool(powered))
        self.meta = meta
        self._calculate_connected()
        cur = int(self.world.get_metadata(self.x, self.y, self.z)) & 15
        if cur != meta:
            self.world.set_block(self.x, self.y, self.z, "rail", meta=meta, immediate_mesh=True)

        for pos in list(self.connected):
            neighbor = self._find_logic(pos)
            if neighbor is None:
                continue
            neighbor._refresh_connected()
            if neighbor._can_accept(self):
                neighbor._connect_to_neighbor(self)


def refresh_rail_shape(world, x, y, z, powered=None):
    if not _is_rail(world, x, y, z):
        return
    _MinecartTrackLogic(world, x, y, z).place(powered=powered)


def refresh_rail_and_neighbors(world, x, y, z):
    if _is_rail(world, x, y, z):
        refresh_rail_shape(world, x, y, z)
        return
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        for dy in (0, 1, -1):
            nx, ny, nz = x + dx, y + dy, z + dz
            if _is_rail(world, nx, ny, nz):
                refresh_rail_shape(world, nx, ny, nz)


def refresh_powered_rail_junctions(world, x, y, z):
    for dx, dy, dz in (
        (0, 0, 0),
        (1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1),
        (0, 1, 0), (0, -1, 0),
        (1, 1, 0), (-1, 1, 0), (0, 1, 1), (0, 1, -1),
        (1, -1, 0), (-1, -1, 0), (0, -1, 1), (0, -1, -1),
    ):
        nx, ny, nz = x + dx, y + dy, z + dz
        if not _is_rail(world, nx, ny, nz):
            continue
        logic = _MinecartTrackLogic(world, nx, ny, nz)
        if logic.connection_count() == 3:
            logic.place()

