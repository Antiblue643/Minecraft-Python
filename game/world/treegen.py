
from __future__ import annotations

import math
import random
from typing import Callable, Optional

from game.java_random import JavaRandom

_AIR = None
_LOG = "log"
_LEAVES = "leaves"
_GRASS = "grass_block"
_DIRT = "dirt"

_OTHER_COORD_PAIRS = (2, 0, 0, 1, 2, 1)


def _floor_double(value: float) -> int:
    i = int(value)
    return i - 1 if value < i else i


def _get_id(world, x, y, z):
    block = world.get_block_type(x, y, z)
    if block is None:
        return 0
    if block == _LEAVES:
        return 18
    if block == _LOG:
        return 17
    return 1  


def _is_opaque(block, is_opaque: Callable) -> bool:
    if block is None or block == _LEAVES:
        return False
    return is_opaque(block)


def generate_alpha_tree(world, x, base_y, z, rng, is_opaque, world_height=128):
    height = rng.randrange(3) + 4  
    if base_y < 1 or base_y + height + 1 > world_height:
        return False

    can_grow = True
    for cy in range(base_y, base_y + 1 + height + 1):
        radius = 1
        if cy == base_y:
            radius = 0
        if cy >= base_y + 1 + height - 2:
            radius = 2
        if cy < 0 or cy >= world_height:
            can_grow = False
            break
        for bx in range(x - radius, x + radius + 1):
            if not can_grow:
                break
            for bz in range(z - radius, z + radius + 1):
                block = world.get_block_type(bx, cy, bz)
                if block is not None and block != _LEAVES:
                    can_grow = False
                    break
            if not can_grow:
                break
        if not can_grow:
            break

    if not can_grow:
        return False

    ground = world.get_block_type(x, base_y - 1, z)
    if ground not in (_GRASS, _DIRT) or base_y >= world_height - height - 1:
        return False

    world.set_block(x, base_y - 1, z, _DIRT)

    for cy in range(base_y - 3 + height, base_y + height + 1):
        dy = cy - (base_y + height)
        leaf_radius = 1 - int(dy / 2)
        for bx in range(x - leaf_radius, x + leaf_radius + 1):
            dx = bx - x
            for bz in range(z - leaf_radius, z + leaf_radius + 1):
                dz = bz - z
                if (
                    abs(dx) == leaf_radius
                    and abs(dz) == leaf_radius
                    and (rng.randrange(2) == 0 or dy == 0)
                ):
                    continue
                existing = world.get_block_type(bx, cy, bz)
                if not _is_opaque(existing, is_opaque):
                    world.set_block(bx, cy, bz, _LEAVES)

    for i in range(height):
        existing = world.get_block_type(x, base_y + i, z)
        if existing is None or existing == _LEAVES:
            world.set_block(x, base_y + i, z, _LOG)

    return True


class _BigTreeGenerator:

    def __init__(self, world, rng, is_opaque, world_height=128):
        self.world = world
        self.population_rng = rng
        self.rand = JavaRandom(0) if hasattr(rng, "next_long") else random.Random(0)
        self.is_opaque = is_opaque
        self.world_height = world_height
        self.base_pos = [0, 0, 0]
        self.height_limit = 0
        self.height = 0
        self.height_attenuation = 0.618
        self.branch_density = 1.0
        self.branch_slope = 0.381
        self.scale_width = 1.0
        self.leaf_density = 1.0
        self.trunk_size = 1
        self.height_limit_limit = 12
        self.leaf_distance_limit = 4
        self.leaf_nodes: list[list[int]] = []
        self.set_scale(1.0, 1.0, 1.0)

    def _next_int(self, bound: int) -> int:
        if hasattr(self.rand, "next_int"):
            return self.rand.next_int(bound)
        return self.rand.randrange(bound)

    def _next_float(self) -> float:
        if hasattr(self.rand, "next_float"):
            return self.rand.next_float()
        return self.rand.random()

    def set_scale(self, height_scale: float, width_scale: float, leaf_density: float) -> None:
        self.height_limit_limit = int(height_scale * 12.0)
        if height_scale > 0.5:
            self.leaf_distance_limit = 5
        self.scale_width = width_scale
        self.leaf_density = leaf_density

    def generate(self, x: int, y: int, z: int) -> bool:
        if hasattr(self.population_rng, "next_long"):
            seed = self.population_rng.next_long()
            self.rand.set_seed(seed)
        elif hasattr(self.population_rng, "getrandbits"):
            seed = self.population_rng.getrandbits(64)
            self.rand.seed(seed)
        self.base_pos = [x, y, z]
        if self.height_limit == 0:
            self.height_limit = 5 + self._next_int(self.height_limit_limit)
        if not self._valid_tree_location():
            return False
        self._generate_leaf_node_list()
        self._generate_leaves()
        self._generate_trunk()
        self._generate_leaf_node_bases()
        return True

    def _block_id(self, x, y, z) -> int:
        return _get_id(self.world, x, y, z)

    def _set_block(self, x, y, z, block_type: str) -> None:
        if 0 <= y < self.world_height:
            self.world.set_block(x, y, z, block_type)

    def _layer_size(self, y_rel: int) -> float:
        if float(y_rel) < float(self.height_limit) * 0.3:
            return -1.618
        half = float(self.height_limit) / 2.0
        offset = half - float(y_rel)
        if offset == 0.0:
            size = half
        elif abs(offset) >= half:
            size = 0.0
        else:
            size = math.sqrt(abs(half) ** 2 - abs(offset) ** 2)
        return size * 0.5

    def _leaf_size(self, dist: int) -> float:
        if 0 <= dist < self.leaf_distance_limit:
            if dist != 0 and dist != self.leaf_distance_limit - 1:
                return 3.0
            return 2.0
        return -1.0

    def _check_block_line(self, a: list[int], b: list[int]) -> int:
        delta = [b[i] - a[i] for i in range(3)]
        primary = 0
        for i in range(3):
            if abs(delta[i]) > abs(delta[primary]):
                primary = i
        if delta[primary] == 0:
            return -1
        secondary = _OTHER_COORD_PAIRS[primary]
        tertiary = _OTHER_COORD_PAIRS[primary + 3]
        step = 1 if delta[primary] > 0 else -1
        slope_s = delta[secondary] / delta[primary]
        slope_t = delta[tertiary] / delta[primary]
        pos = [0, 0, 0]
        cursor = 0
        end = delta[primary] + step
        while cursor != end:
            pos[primary] = a[primary] + cursor
            pos[secondary] = int(a[secondary] + cursor * slope_s)
            pos[tertiary] = int(a[tertiary] + cursor * slope_t)
            block_id = self._block_id(pos[0], pos[1], pos[2])
            if block_id != 0 and block_id != 18:
                break
            cursor += step
        return -1 if cursor == end else abs(cursor)

    def _valid_tree_location(self) -> bool:
        start = [self.base_pos[0], self.base_pos[1], self.base_pos[2]]
        end = [self.base_pos[0], self.base_pos[1] + self.height_limit - 1, self.base_pos[2]]
        ground = self.world.get_block_type(
            self.base_pos[0], self.base_pos[1] - 1, self.base_pos[2]
        )
        if ground not in (_GRASS, _DIRT):
            return False
        blocked = self._check_block_line(start, end)
        if blocked == -1:
            return True
        if blocked < 6:
            return False
        self.height_limit = blocked
        return True

    def _generate_leaf_node_list(self) -> None:
        self.height = int(self.height_limit * self.height_attenuation)
        if self.height >= self.height_limit:
            self.height = self.height_limit - 1

        node_count = int(1.382 + (self.leaf_density * self.height_limit / 13.0) ** 2)
        if node_count < 1:
            node_count = 1

        nodes: list[list[int]] = [[0, 0, 0, 0] for _ in range(node_count * self.height_limit)]
        y = self.base_pos[1] + self.height_limit - self.leaf_distance_limit
        filled = 1
        trunk_top = self.base_pos[1] + self.height
        y_rel = y - self.base_pos[1]
        nodes[0][0] = self.base_pos[0]
        nodes[0][1] = y
        nodes[0][2] = self.base_pos[2]
        nodes[0][3] = trunk_top
        y -= 1

        while y_rel >= 0:
            layer = self._layer_size(y_rel)
            if layer < 0.0:
                y -= 1
                y_rel -= 1
                continue
            for _ in range(node_count):
                radius = self.scale_width * layer * (self._next_float() + 0.328)
                angle = self._next_float() * 2.0 * math.pi
                nx = int(radius * math.sin(angle) + self.base_pos[0] + 0.5)
                nz = int(radius * math.cos(angle) + self.base_pos[2] + 0.5)
                leaf_pos = [nx, y, nz]
                leaf_top = [nx, y + self.leaf_distance_limit, nz]
                if self._check_block_line(leaf_pos, leaf_top) == -1:
                    branch_base = [self.base_pos[0], self.base_pos[1], self.base_pos[2]]
                    horiz = math.sqrt(
                        abs(self.base_pos[0] - leaf_pos[0]) ** 2
                        + abs(self.base_pos[2] - leaf_pos[2]) ** 2
                    )
                    branch_y = leaf_pos[1] - horiz * self.branch_slope
                    if branch_y > trunk_top:
                        branch_base[1] = trunk_top
                    else:
                        branch_base[1] = int(branch_y)
                    if self._check_block_line(branch_base, leaf_pos) == -1:
                        nodes[filled][0] = nx
                        nodes[filled][1] = y
                        nodes[filled][2] = nz
                        nodes[filled][3] = branch_base[1]
                        filled += 1
            y -= 1
            y_rel -= 1

        self.leaf_nodes = [nodes[i][:] for i in range(filled)]

    def _gen_tree_layer(self, x: int, y: int, z: int, radius: float, axis: int, block: str) -> None:
        extent = int(radius + 0.618)
        secondary = _OTHER_COORD_PAIRS[axis]
        tertiary = _OTHER_COORD_PAIRS[axis + 3]
        center = [x, y, z]
        pos = [0, 0, 0]
        pos[axis] = center[axis]
        for dx in range(-extent, extent + 1):
            pos[secondary] = center[secondary] + dx
            for dz in range(-extent, extent + 1):
                dist = math.sqrt((abs(dx) + 0.5) ** 2 + (abs(dz) + 0.5) ** 2)
                if dist > radius:
                    continue
                pos[tertiary] = center[tertiary] + dz
                block_id = self._block_id(pos[0], pos[1], pos[2])
                if block_id == 0 or block_id == 18:
                    self._set_block(pos[0], pos[1], pos[2], block)

    def _generate_leaf_node(self, x: int, y: int, z: int) -> None:
        for cy in range(y, y + self.leaf_distance_limit):
            size = self._leaf_size(cy - y)
            self._gen_tree_layer(x, cy, z, size, 1, _LEAVES)

    def _place_block_line(self, a: list[int], b: list[int], block: str) -> None:
        delta = [b[i] - a[i] for i in range(3)]
        primary = 0
        for i in range(3):
            if abs(delta[i]) > abs(delta[primary]):
                primary = i
        if delta[primary] == 0:
            return
        secondary = _OTHER_COORD_PAIRS[primary]
        tertiary = _OTHER_COORD_PAIRS[primary + 3]
        step = 1 if delta[primary] > 0 else -1
        slope_s = delta[secondary] / delta[primary]
        slope_t = delta[tertiary] / delta[primary]
        pos = [0, 0, 0]
        cursor = 0
        end = delta[primary] + step
        while cursor != end:
            pos[primary] = _floor_double(a[primary] + cursor + 0.5)
            pos[secondary] = _floor_double(a[secondary] + cursor * slope_s + 0.5)
            pos[tertiary] = _floor_double(a[tertiary] + cursor * slope_t + 0.5)
            self._set_block(pos[0], pos[1], pos[2], block)
            cursor += step

    def _generate_leaves(self) -> None:
        for node in self.leaf_nodes:
            self._generate_leaf_node(node[0], node[1], node[2])

    def _leaf_node_needs_base(self, height_above_base: int) -> bool:
        return float(height_above_base) >= float(self.height_limit) * 0.2

    def _generate_trunk(self) -> None:
        x = self.base_pos[0]
        y0 = self.base_pos[1]
        y1 = self.base_pos[1] + self.height
        z = self.base_pos[2]
        start = [x, y0, z]
        end = [x, y1, z]
        self._place_block_line(start, end, _LOG)
        if self.trunk_size == 2:
            start[0] += 1
            end[0] += 1
            self._place_block_line(start, end, _LOG)
            start[2] += 1
            end[2] += 1
            self._place_block_line(start, end, _LOG)
            start[0] -= 1
            end[0] -= 1
            self._place_block_line(start, end, _LOG)

    def _generate_leaf_node_bases(self) -> None:
        base = [self.base_pos[0], self.base_pos[1], self.base_pos[2]]
        for node in self.leaf_nodes:
            tip = [node[0], node[1], node[2]]
            base[1] = node[3]
            height_above = base[1] - self.base_pos[1]
            if self._leaf_node_needs_base(height_above):
                self._place_block_line(base, tip, _LOG)


def create_alpha_big_tree_generator(world, rng, is_opaque, world_height=128):
    return _BigTreeGenerator(world, rng, is_opaque, world_height=world_height)


def generate_alpha_big_tree(world, x, base_y, z, rng, is_opaque, world_height=128):
    gen = create_alpha_big_tree_generator(world, rng, is_opaque, world_height)
    return gen.generate(x, base_y, z)
