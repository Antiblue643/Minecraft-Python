from __future__ import annotations

import math

from game.entities.entity import MAX_DT, TICKS_PER_SECOND, Entity
from .falling_block_model import FallingBlockRendererMixin
from game.entities.item import Item, ItemEntity, ItemStack
from game.block.support_rules import GRAVITY_BLOCKS

SIZE = 0.98
HALF = SIZE * 0.5
GRAVITY = 0.04
DRAG = 0.98
MAX_FALL_TICKS = 100

_REPLACEABLE = frozenset({"water", "lava", "fire", "snow"})

def can_fall_below(world, x, y, z):
    block_type = world.get_block_type(x, y, z)
    return block_type is None or block_type in _REPLACEABLE


def try_to_fall(world, x, y, z):
    block_type = world.get_block_type(x, y, z)
    if block_type not in GRAVITY_BLOCKS or y < 0:
        return None
    if not can_fall_below(world, x, y - 1, z):
        return None
    meta = world.get_metadata(x, y, z)
    world.set_block(x, y, z, None, immediate_mesh=True)
    entity = FallingBlockEntity.at_block(block_type, x, y, z, meta=meta)
    world.spawn_entity(entity)
    return entity


def turn_redstone_ore_off(world, x, y, z):
    if world.get_block_type(x, y, z) == "redstone_ore_lit":
        world.set_block(
            x, y, z, "redstone_ore", meta=world.get_metadata(x, y, z),
            immediate_mesh=True,
        )


TICK_HANDLERS = {block_type: try_to_fall for block_type in GRAVITY_BLOCKS}
TICK_HANDLERS["redstone_ore_lit"] = turn_redstone_ore_off


def _tick_redstone_torch(world, x, y, z):
    redstone = getattr(world, "_redstone_system", None)
    if redstone is not None:
        redstone.apply_torch_update(x, y, z)


TICK_HANDLERS["redstone_torch"] = _tick_redstone_torch
TICK_HANDLERS["unlit_redstone_torch"] = _tick_redstone_torch

class FallingBlockEntity(FallingBlockRendererMixin, Entity):

    def __init__(self, block_type, pos, vel=(0.0, 0.0, 0.0), meta=0, fall_time=0.0):
        super().__init__(pos, (SIZE, SIZE, SIZE))
        self.block_type = block_type
        self.prevent_entity_spawning = True
        self.meta = int(meta)
        self.vel = [float(vel[0]), float(vel[1]), float(vel[2])]
        self.fall_time = float(fall_time)   

    @classmethod
    def at_block(cls, block_type, bx, by, bz, meta=0):
        return cls(block_type, (float(bx), by - HALF, float(bz)), meta=meta)

    @property
    def center(self):
        return self.pos[0], self.pos[1] + HALF, self.pos[2]

    def _cell(self):
        cx, cy, cz = self.center
        return math.floor(cx + 0.5), math.floor(cy + 0.5), math.floor(cz + 0.5)

    def _drop_as_item(self):
        world = Entity.world
        if world is None or not Item.exists(self.block_type):
            return
        cx, cy, cz = self.center
        world.spawn_entity(ItemEntity(
            ItemStack(self.block_type, 1), (cx, cy - 0.125, cz), pickup_delay=10,
        ))

    def _land(self):
        world = Entity.world
        x, y, z = self._cell()
        existing = world.get_block_type(x, y, z)
        placeable = existing is None or existing in _REPLACEABLE
        if placeable and world._get_chunk_index(x, z) in world.chunks:
            world.set_block(x, y, z, self.block_type, meta=self.meta, immediate_mesh=True)
        else:
            self._drop_as_item()

    def update(self, dt):
        world = Entity.world
        if self.dead or world is None:
            return
        if self.block_type not in GRAVITY_BLOCKS:
            self.dead = True
            return

        ticks = min(dt, MAX_DT) * TICKS_PER_SECOND
        self.fall_time += ticks
        self.vel[1] -= GRAVITY * ticks
        self.move(self.vel[0] * ticks, self.vel[1] * ticks, self.vel[2] * ticks)
        drag = DRAG ** ticks
        self.vel[0] *= drag
        self.vel[1] *= drag
        self.vel[2] *= drag

        if self._blocked_down:
            self.dead = True
            self._land()
        elif self.fall_time > MAX_FALL_TICKS:
            self.dead = True
            self._drop_as_item()
