from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


from game.content_data import load_painting_definitions
from game.entities.entity import MAX_DT, TICKS_PER_SECOND, Entity
from .painting_model import PaintingRendererMixin, _art_texture
from game.entities.item import ItemEntity, ItemStack
from game.block.support_rules import _material_is_solid

@dataclass(frozen=True)
class ArtType:
    title: str
    size_x: int
    size_y: int
    offset_x: int
    offset_y: int

ART_TYPES: Tuple[ArtType, ...] = tuple(
    ArtType(*motive) for motive in load_painting_definitions()
)




def _art_size_offset(size_px: int) -> float:
    return 0.5 if size_px in (32, 64) else 0.0


def direction_from_normal(normal) -> Optional[int]:
    nx, ny, nz = int(normal[0]), int(normal[1]), int(normal[2])
    if ny != 0:
        return None
    if (nx, nz) == (0, -1):
        return 0
    if (nx, nz) == (-1, 0):
        return 1
    if (nx, nz) == (0, 1):
        return 2
    if (nx, nz) == (1, 0):
        return 3
    return None


def _hang_outward(direction: int) -> Tuple[float, float, float]:
    if direction == 0:
        return (0.0, 0.0, -1.0)
    if direction == 1:
        return (-1.0, 0.0, 0.0)
    if direction == 2:
        return (0.0, 0.0, 1.0)
    return (1.0, 0.0, 0.0)


class PaintingEntity(PaintingRendererMixin, Entity):
    def __init__(
        self,
        world,
        tile_x: int,
        tile_y: int,
        tile_z: int,
        direction: int,
        art: Optional[ArtType] = None,
    ):
        super().__init__((0.0, 0.0, 0.0), (0.5, 0.5, 0.5))
        self.world_obj = world
        self.tile_x = int(tile_x)
        self.tile_y = int(tile_y)
        self.tile_z = int(tile_z)
        self.direction = int(direction) & 3
        self.art = art or ART_TYPES[0]
        self._tick_counter = 0.0
        self._center = (0.0, 0.0, 0.0)
        self._half_extents = (0.25, 0.25, 0.25)
        self.collision_border_size = 0.02
        self.set_direction(self.direction)

    @classmethod
    def try_place(cls, world, tile_x, tile_y, tile_z, direction, rng=None):
        rng = rng or random
        candidates: List[ArtType] = []
        probe = cls(world, tile_x, tile_y, tile_z, direction, ART_TYPES[0])
        for art in ART_TYPES:
            probe.art = art
            probe.set_direction(direction)
            if probe.on_valid_surface():
                candidates.append(art)
        if not candidates:
            return None
        return cls(world, tile_x, tile_y, tile_z, direction, candidates[rng.randrange(len(candidates))])

    def set_direction(self, direction: int) -> None:
        self.direction = int(direction) & 3
        self.yaw = float(self.direction * 90)
        art = self.art

        half_x = float(art.size_x)
        half_y = float(art.size_y)
        half_z = float(art.size_x)
        if self.direction not in (0, 2):
            half_x = 0.5
        else:
            half_z = 0.5
        half_x /= 32.0
        half_y /= 32.0
        half_z /= 32.0

        px = float(self.tile_x)
        py = float(self.tile_y)
        pz = float(self.tile_z)
        hang = 9.0 / 16.0
        ox, oy, oz = _hang_outward(self.direction)
        px += ox * hang
        pz += oz * hang

        if self.direction == 0:
            px -= _art_size_offset(art.size_x)
        elif self.direction == 1:
            pz += _art_size_offset(art.size_x)
        elif self.direction == 2:
            px += _art_size_offset(art.size_x)
        elif self.direction == 3:
            pz -= _art_size_offset(art.size_x)
        py += _art_size_offset(art.size_y)

        self._center = (px, py, pz)
        self._half_extents = (half_x, half_y, half_z)
        self.size = (half_x * 2.0, half_y * 2.0, half_z * 2.0)
        thick = min(half_x, half_z)
        ox, _oy, oz = _hang_outward(self.direction)
        col_x = px + ox * thick
        col_z = pz + oz * thick
        self.pos = [col_x, py - half_y, col_z]

    @property
    def center(self):
        return self._center

    def on_valid_surface(self) -> bool:
        world = self.world_obj
        if world is None:
            return False
        art = self.art
        tiles_x = max(1, art.size_x // 16)
        tiles_y = max(1, art.size_y // 16)

        if self._aabb_hits_solid_blocks(world):
            return False

        cx, cy, cz = self._center
        base_y = int(math.floor(cy - art.size_y / 32.0 + 0.5))
        if self.direction in (0, 2):
            base_u = int(math.floor(cx - art.size_x / 32.0 + 0.5))
        else:
            base_u = int(math.floor(cz - art.size_x / 32.0 + 0.5))

        for iu in range(tiles_x):
            for iv in range(tiles_y):
                by = base_y + iv
                if self.direction in (0, 2):
                    bx = base_u + iu
                    bz = self.tile_z
                else:
                    bx = self.tile_x
                    bz = base_u + iu
                if not _material_is_solid(world.get_block_type(bx, by, bz)):
                    return False

        for entity in getattr(world, "entities", ()):
            if entity is self or getattr(entity, "dead", False):
                continue
            if isinstance(entity, PaintingEntity) and self._aabb_overlaps(entity):
                return False
        return True

    def _aabb_hits_solid_blocks(self, world) -> bool:
        cx, cy, cz = self._center
        hx, hy, hz = self._half_extents
        pad = 0.1 / 16.0
        min_x, max_x = cx - hx + pad, cx + hx - pad
        min_y, max_y = cy - hy + pad, cy + hy - pad
        min_z, max_z = cz - hz + pad, cz + hz - pad
        x0 = int(math.floor(min_x + 0.5))
        x1 = int(math.floor(max_x + 0.5))
        y0 = int(math.floor(min_y + 0.5))
        y1 = int(math.floor(max_y + 0.5))
        z0 = int(math.floor(min_z + 0.5))
        z1 = int(math.floor(max_z + 0.5))
        for bx in range(x0, x1 + 1):
            for by in range(y0, y1 + 1):
                for bz in range(z0, z1 + 1):
                    if not _material_is_solid(world.get_block_type(bx, by, bz)):
                        continue
                    if (max_x <= bx - 0.5 or min_x >= bx + 0.5
                            or max_y <= by - 0.5 or min_y >= by + 0.5
                            or max_z <= bz - 0.5 or min_z >= bz + 0.5):
                        continue
                    return True
        return False


    def _aabb_overlaps(self, other: "PaintingEntity") -> bool:
        a = self._center
        b = other._center
        ha = self._half_extents
        hb = other._half_extents
        return (
            abs(a[0] - b[0]) < ha[0] + hb[0]
            and abs(a[1] - b[1]) < ha[1] + hb[1]
            and abs(a[2] - b[2]) < ha[2] + hb[2]
        )

    def can_be_collided_with(self):
        return not self.dead

    def get_interaction_bounds(self):
        cx, cy, cz = self._center
        hx, hy, hz = self._half_extents
        return (cx - hx, cy - hy, cz - hz, cx + hx, cy + hy, cz + hz)

    def attack_entity_from(self, attacker=None, amount=1):
        if self.dead:
            return False
        self.dead = True
        world = self.world_obj
        if world is not None:
            cx, cy, cz = self._center
            ox, _oy, oz = _hang_outward(self.direction)
            sx = cx + ox * 0.15
            sy = cy
            sz = cz + oz * 0.15
            world.spawn_entity(ItemEntity(
                ItemStack("painting", 1),
                (sx, sy, sz),
                pickup_delay=10,
            ))
        return True

    def update(self, dt):
        if self.dead or self.world_obj is None:
            return
        ticks = min(dt, MAX_DT) * TICKS_PER_SECOND
        self._tick_counter += ticks
        if self._tick_counter >= 100.0:
            self._tick_counter = 0.0
            if not self.on_valid_surface():
                self.attack_entity_from()






def try_place_painting(world, tile_x, tile_y, tile_z, normal) -> bool:
    direction = direction_from_normal(normal)
    if direction is None:
        return False
    painting = PaintingEntity.try_place(world, tile_x, tile_y, tile_z, direction)
    if painting is None:
        return False
    world.spawn_entity(painting)
    return True
