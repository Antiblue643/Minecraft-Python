from __future__ import annotations

import heapq
import itertools
import json
import random
import time
from functools import lru_cache
from pathlib import Path

from game.world.chunk_indexing import find_chunk_block_indices

WATER = "water"
LAVA = "lava"
FLUIDS = frozenset((WATER, LAVA))
FLUID_BLOCK_TYPES = frozenset((
    "water", "water_still", "water_flowing", "stationary_water", "flowing_water",
    "lava", "lava_still", "lava_flowing", "stationary_lava", "flowing_lava",
))
HORIZONTAL = ((-1, 0), (1, 0), (0, -1), (0, 1))
NEIGHBORS_6 = ((-1, 0, 0), (1, 0, 0), (0, -1, 0), (0, 1, 0), (0, 0, -1), (0, 0, 1))
LAVA_HARDEN_NEIGHBORS = ((-1, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, -1), (0, 0, 1))
SPECIAL_FLOW_BLOCKERS = frozenset((
    "wooden_door", "sign", "standing_sign", "wall_sign", "ladder",
))


def liquid_tick_rate(fluid_type: str) -> int:
    return 5 if fluid_type == WATER else 30


def _fluid_family(block_type):
    if block_type in ("water", "water_still", "water_flowing", "stationary_water", "flowing_water"):
        return WATER
    if block_type in ("lava", "lava_still", "lava_flowing", "stationary_lava", "flowing_lava"):
        return LAVA
    return None


def get_flow_decay(world, x, y, z, fluid_type):
    if _fluid_family(world.get_block_type(x, y, z)) != fluid_type:
        return -1
    return int(world.get_metadata(x, y, z)) & 15


def get_effective_flow_decay(world, x, y, z, fluid_type):
    decay = get_flow_decay(world, x, y, z, fluid_type)
    return 0 if decay >= 8 else decay


@lru_cache(maxsize=1)
def _block_definitions():
    path = Path(__file__).resolve().parents[1] / "data" / "blocks.json"
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)["blocks"]


def _is_normal_cube(world, x, y, z):
    block_type = world.get_block_type(x, y, z)
    if block_type is None:
        return False
    definition = _block_definitions().get(str(block_type), {})
    return (
        definition.get("collision", True) is not False
        and definition.get("model", "cube") == "cube"
        and not definition.get("transparent", False)
        and definition.get("nCull", "all") == "all"
        and block_type not in ("water", "lava", "fire")
    )


def _normalize_vector(vector):
    length = (vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2]) ** 0.5
    if length < 1.0e-8:
        return (0.0, 0.0, 0.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def fluid_flow_vector(world, x, y, z, fluid_type=WATER):
    current = get_effective_flow_decay(world, x, y, z, fluid_type)
    flow_x = flow_y = flow_z = 0.0
    for dx, dz in ((-1, 0), (0, -1), (1, 0), (0, 1)):
        neighbor = get_effective_flow_decay(world, x + dx, y, z + dz, fluid_type)
        if neighbor < 0:
            neighbor = get_effective_flow_decay(world, x + dx, y - 1, z + dz, fluid_type)
            if neighbor >= 0:
                difference = neighbor - (current - 8)
                flow_x += dx * difference
                flow_z += dz * difference
        else:
            difference = neighbor - current
            flow_x += dx * difference
            flow_z += dz * difference

    if int(world.get_metadata(x, y, z)) & 8:
        visible_side = False
        for dx, dz in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            if visible_side:
                break
            for yy in (y, y + 1):
                neighbor_type = world.get_block_type(x + dx, yy, z + dz)
                if (_fluid_family(neighbor_type) != fluid_type
                        and not _is_normal_cube(world, x + dx, yy, z + dz)):
                    visible_side = True
                    break
        if visible_side:
            flow_x, flow_y, flow_z = _normalize_vector((flow_x, flow_y, flow_z))
            flow_y -= 6.0

    return _normalize_vector((flow_x, flow_y, flow_z))


def _is_solid_for_flow(world, x, y, z):
    block_type = world.get_block_type(x, y, z)
    if block_type in SPECIAL_FLOW_BLOCKERS:
        return True
    if block_type is None:
        return False
    if _fluid_family(block_type) is not None:
        return False
    definition = _block_definitions().get(str(block_type), {})
    return definition.get("collision", True) is not False


def liquid_can_displace(world, x, y, z, fluid_type):
    target = _fluid_family(world.get_block_type(x, y, z))
    if target == fluid_type or target == LAVA:
        return False
    return not _is_solid_for_flow(world, x, y, z)


def _schedule_adjacent(scheduler, x, y, z):
    for dx, dy, dz in NEIGHBORS_6:
        nx, ny, nz = x + dx, y + dy, z + dz
        if not (0 <= ny < scheduler.world.CHUNK_HEIGHT):
            continue
        fluid = _fluid_family(scheduler.world.get_block_type(nx, ny, nz))
        if fluid:
            scheduler.schedule(nx, ny, nz, fluid)


def _emit_lava_mix_particles(world, x, y, z, rng=None):
    rng = rng or random
    sound_player = getattr(world, "sound_player", None)
    if callable(sound_player):
        pitch = 2.6 + (rng.random() - rng.random()) * 0.8
        sound_player(
            "random.fizz", volume=0.5, pitch=pitch,
            position=(float(x), float(y), float(z)),
        )

    spawn = getattr(world, "spawn_particle", None)
    if not callable(spawn):
        return
    for _ in range(8):
        spawn("largesmoke", x - 0.5 + rng.random(), y + 0.7,
              z - 0.5 + rng.random(), 0.0, 0.0, 0.0)


def _try_harden_lava(world, x, y, z, rng):
    if world.get_block_type(x, y, z) != LAVA:
        return False
    if not any(_fluid_family(world.get_block_type(x + dx, y + dy, z + dz)) == WATER
               for dx, dy, dz in LAVA_HARDEN_NEIGHBORS):
        return False
    meta = int(world.get_metadata(x, y, z)) & 15
    if meta == 0:
        replacement = "obsidian"
    elif meta <= 4:
        replacement = "cobblestone"
    else:
        replacement = None
    if replacement is not None:
        world.set_block(x, y, z, replacement, immediate_mesh=False)
    _emit_lava_mix_particles(world, x, y, z, rng)
    return replacement is not None


def _flow_into_block(world, x, y, z, fluid_type, new_decay, rng):
    if not liquid_can_displace(world, x, y, z, fluid_type):
        return False
    displaced = world.get_block_type(x, y, z)
    if displaced is not None:
        if fluid_type == LAVA:
            _emit_lava_mix_particles(world, x, y, z)
        elif _fluid_family(displaced) is None:
            try:
                from game.block.drops import block_drops
                for stack in block_drops(displaced, world.get_metadata(x, y, z), rng):
                    world.spawn_item_stack(x, y, z, stack)
            except (ImportError, AttributeError):
                pass
    world.set_block(x, y, z, fluid_type, meta=new_decay, immediate_mesh=False)
    return True


def _calculate_flow_cost(world, x, y, z, fluid_type, depth, previous_direction):
    best = 1000
    for direction, (dx, dz) in enumerate(HORIZONTAL):
        if previous_direction == 0 and direction == 1:
            continue
        if previous_direction == 1 and direction == 0:
            continue
        if previous_direction == 2 and direction == 3:
            continue
        if previous_direction == 3 and direction == 2:
            continue
        nx, nz = x + dx, z + dz
        if _is_solid_for_flow(world, nx, y, nz):
            continue
        neighbor_type = _fluid_family(world.get_block_type(nx, y, nz))
        neighbor_decay = get_flow_decay(world, nx, y, nz, fluid_type)
        if neighbor_type == fluid_type and neighbor_decay == 0:
            continue
        if not _is_solid_for_flow(world, nx, y - 1, nz):
            return depth
        if depth < 4:
            cost = _calculate_flow_cost(
                world, nx, y, nz, fluid_type, depth + 1, direction,
            )
            best = min(best, cost)
    return best


def _optimal_flow_directions(world, x, y, z, fluid_type):
    block_cache = {}
    solid_cache = {}
    decay_cache = {}
    cost_cache = {}
    blocks = _block_definitions()

    def block_type_at(px, py, pz):
        key = (px, py, pz)
        if key not in block_cache:
            block_cache[key] = world.get_block_type(px, py, pz)
        return block_cache[key]

    def is_solid_at(px, py, pz):
        key = (px, py, pz)
        if key in solid_cache:
            return solid_cache[key]
        block_type = block_type_at(px, py, pz)
        if block_type in SPECIAL_FLOW_BLOCKERS:
            result = True
        elif block_type is None or _fluid_family(block_type) is not None:
            result = False
        else:
            definition = blocks.get(str(block_type), {})
            result = definition.get("collision", True) is not False
        solid_cache[key] = result
        return result

    def flow_decay_at(px, pz):
        key = (px, pz)
        if key in decay_cache:
            return decay_cache[key]
        block_type = _fluid_family(block_type_at(px, y, pz))
        value = (int(world.get_metadata(px, y, pz)) & 15) if block_type == fluid_type else -1
        decay_cache[key] = value
        return value

    def calculate_cost(px, pz, depth, previous_direction):
        key = (px, pz, depth, previous_direction)
        cached = cost_cache.get(key)
        if cached is not None:
            return cached
        best = 1000
        for direction, (dx, dz) in enumerate(HORIZONTAL):
            if ((previous_direction == 0 and direction == 1)
                    or (previous_direction == 1 and direction == 0)
                    or (previous_direction == 2 and direction == 3)
                    or (previous_direction == 3 and direction == 2)):
                continue
            nx, nz = px + dx, pz + dz
            if is_solid_at(nx, y, nz):
                continue
            neighbor_decay = flow_decay_at(nx, nz)
            if neighbor_decay == 0:
                continue
            if not is_solid_at(nx, y - 1, nz):
                best = min(best, depth)
                best = depth
                break
            if depth < 4:
                best = min(best, calculate_cost(nx, nz, depth + 1, direction))
        cost_cache[key] = best
        return best

    costs = []
    for direction, (dx, dz) in enumerate(HORIZONTAL):
        nx, nz = x + dx, z + dz
        if is_solid_at(nx, y, nz):
            costs.append(1000)
            continue
        neighbor_decay = flow_decay_at(nx, nz)
        if neighbor_decay == 0:
            costs.append(1000)
            continue
        if not is_solid_at(nx, y - 1, nz):
            costs.append(0)
        else:
            costs.append(calculate_cost(nx, nz, 1, direction))
    best = min(costs, default=1000)
    return [cost == best for cost in costs]


def tick_fluid(world, x, y, z, rng=None, scheduler=None):
    if rng is None:
        rng = random
    fluid_type = _fluid_family(world.get_block_type(x, y, z))
    if fluid_type is None:
        return
    fluid_type_step = 1 if fluid_type == WATER else 2
    decay = get_flow_decay(world, x, y, z, fluid_type)
    update_flow = True

    if decay > 0:
        smallest = -100
        adjacent_sources = 0
        for dx, dz in HORIZONTAL:
            neighbor_decay = get_flow_decay(world, x + dx, y, z + dz, fluid_type)
            if neighbor_decay < 0:
                continue
            if neighbor_decay == 0:
                adjacent_sources += 1
            if neighbor_decay >= 8:
                neighbor_decay = 0
            if smallest < 0 or neighbor_decay < smallest:
                smallest = neighbor_decay
        new_decay = smallest + fluid_type_step
        if new_decay >= 8 or smallest < 0:
            new_decay = -1

        above_decay = get_flow_decay(world, x, y + 1, z, fluid_type)
        if above_decay >= 0:
            new_decay = above_decay if above_decay >= 8 else above_decay + 8

        if adjacent_sources >= 2 and fluid_type == WATER:
            if _is_normal_cube(world, x, y - 1, z):
                new_decay = 0
            else:
                below_family = _fluid_family(world.get_block_type(x, y - 1, z))
                below_meta = int(world.get_metadata(x, y - 1, z)) & 15
                if below_family == WATER and below_meta == 0:
                    new_decay = 0

        if (fluid_type == LAVA and decay < 8 and 0 <= new_decay < 8
                and new_decay > decay and rng.randrange(4) != 0):
            new_decay = decay
            update_flow = False

        if new_decay != decay:
            if new_decay < 0:
                world.set_block(x, y, z, None, immediate_mesh=False)
            else:
                world.set_block(x, y, z, fluid_type, meta=new_decay, immediate_mesh=False)
                if scheduler is not None:
                    scheduler.schedule(x, y, z, fluid_type)
        elif update_flow:
            if scheduler is not None:
                scheduler.mark_stationary(x, y, z)
        elif fluid_type == LAVA and scheduler is not None:
            scheduler.schedule(x, y, z, fluid_type)
    else:
        if scheduler is not None:
            scheduler.mark_stationary(x, y, z)

    decay = get_flow_decay(world, x, y, z, fluid_type)
    if liquid_can_displace(world, x, y - 1, z, fluid_type):
        new_decay = decay if decay >= 8 else decay + 8
        world.set_block(x, y - 1, z, fluid_type, meta=new_decay, immediate_mesh=False)
        return

    if decay >= 0 and (decay == 0 or _is_solid_for_flow(world, x, y - 1, z)):
        horizontal_decay = decay + fluid_type_step
        if decay >= 8:
            horizontal_decay = 1
        if horizontal_decay >= 8:
            return
        for should_flow, (dx, dz) in zip(
            _optimal_flow_directions(world, x, y, z, fluid_type), HORIZONTAL,
        ):
            if should_flow:
                _flow_into_block(
                    world, x + dx, y, z + dz, fluid_type, horizontal_decay, rng,
                )


class FluidTickScheduler:
    def __init__(self, world):
        self.world = world
        self._heap = []
        self._pending = {}
        self._moving_cells = set()
        self._cells_by_chunk = {}
        self._counter = itertools.count()
        self._handling_change = 0
        self._heap_compaction_deferred = False
        self.last_processed = 0
        self.last_batch_stats = {
            "processed": 0, "pending": 0, "heap": 0, "active_cells": 0,
            "max_cell_ms": 0.0, "slow_cells": [], "budget_exhausted": False,
            "limit_exhausted": False, "due_work_queued": False,
            "heap_compaction_deferred": False,
        }

    def is_moving(self, x, y=None, z=None):
        key = (int(x), int(y), int(z)) if y is not None and z is not None else tuple(x)
        return key in self._moving_cells

    def _track_cell(self, key):
        chunk_key = self.world._get_chunk_index(key[0], key[2])
        self._cells_by_chunk.setdefault(chunk_key, set()).add(key)

    def _untrack_cell(self, key):
        chunk_key = self.world._get_chunk_index(key[0], key[2])
        cells = self._cells_by_chunk.get(chunk_key)
        if cells is not None:
            cells.discard(key)
            if not cells:
                self._cells_by_chunk.pop(chunk_key, None)

    def mark_stationary(self, x, y, z):
        key = (int(x), int(y), int(z))
        self._moving_cells.discard(key)
        self._pending.pop(key, None)
        self._untrack_cell(key)

    def on_chunk_unloaded(self, cx, cz):
        cells = self._cells_by_chunk.pop((int(cx), int(cz)), ())
        for key in cells:
            self._moving_cells.discard(key)
            self._pending.pop(key, None)
        self._compact_heap_if_needed()

    def schedule(self, x, y, z, fluid_type=None, delay=None):
        if not (0 <= int(y) < self.world.CHUNK_HEIGHT):
            return False
        x, y, z = int(x), int(y), int(z)
        fluid_type = fluid_type or _fluid_family(self.world.get_block_type(x, y, z))
        if fluid_type not in FLUIDS or _fluid_family(self.world.get_block_type(x, y, z)) != fluid_type:
            return False
        due = int(self.world.world_time) + (liquid_tick_rate(fluid_type) if delay is None else max(0, int(delay)))
        key = (x, y, z)
        self._moving_cells.add(key)
        self._track_cell(key)
        previous = self._pending.get(key)
        if previous is not None and previous[1] == fluid_type and previous[0] <= due:
            return True
        self._pending[key] = (due, fluid_type)
        heapq.heappush(self._heap, (due, next(self._counter), x, y, z, fluid_type))
        return True

    def _loaded_area(self, x, z):
        chunks = getattr(self.world, "chunks", None)
        if not isinstance(chunks, dict):
            return True
        cx0, cz0 = self.world._get_chunk_index(x - 8, z - 8)
        cx1, cz1 = self.world._get_chunk_index(x + 8, z + 8)
        return all((cx, cz) in chunks
                   for cx in range(cx0, cx1 + 1)
                   for cz in range(cz0, cz1 + 1))

    def process_due(self, rng=None, limit=500, budget_ms=None):
        if rng is None:
            rng = random
        started = time.perf_counter()
        deadline = (None if budget_ms is None else
                    started + max(0.0, float(budget_ms)) / 1000.0)
        processed = 0
        max_cell_ms = 0.0
        slow_cells = []
        budget_exhausted = False
        while self._heap and processed < max(0, int(limit)):
            if deadline is not None and time.perf_counter() >= deadline:
                budget_exhausted = True
                break
            due, serial, x, y, z, fluid_type = self._heap[0]
            if due > int(self.world.world_time):
                break
            heapq.heappop(self._heap)
            key = (x, y, z)
            if self._pending.get(key) != (due, fluid_type):
                continue
            self._pending.pop(key, None)
            if _fluid_family(self.world.get_block_type(x, y, z)) != fluid_type:
                self._moving_cells.discard(key)
                self._untrack_cell(key)
                continue
            if not self._loaded_area(x, z):
                self.schedule(x, y, z, fluid_type)
                continue
            cell_started = time.perf_counter()
            tick_fluid(self.world, x, y, z, rng=rng, scheduler=self)
            cell_ms = (time.perf_counter() - cell_started) * 1000.0
            max_cell_ms = max(max_cell_ms, cell_ms)
            if cell_ms >= 1.0 and len(slow_cells) < 8:
                slow_cells.append({
                    "x": int(x), "y": int(y), "z": int(z),
                    "fluid": fluid_type, "duration_ms": round(cell_ms, 3),
                })
            processed += 1
        self.last_processed = processed
        self._compact_heap_if_needed()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        due_work_queued = bool(self._heap and self._heap[0][0] <= int(self.world.world_time))
        budget_exhausted = bool(budget_exhausted and due_work_queued)
        limit_exhausted = bool(processed >= max(0, int(limit)) and due_work_queued)
        self.last_batch_stats = {
            "processed": int(processed),
            "pending": int(self.pending_count),
            "heap": int(len(self._heap)),
            "active_cells": int(len(self._moving_cells)),
            "max_cell_ms": round(max_cell_ms, 3),
            "slow_cells": slow_cells,
            "budget_ms": None if budget_ms is None else round(float(budget_ms), 3),
            "budget_exhausted": budget_exhausted,
            "limit_exhausted": limit_exhausted,
            "due_work_queued": due_work_queued,
            "heap_compaction_deferred": bool(self._heap_compaction_deferred),
            "duration_ms_internal": round(elapsed_ms, 3),
        }
        return processed

    def _compact_heap_if_needed(self):
        threshold = max(1024, len(self._pending) * 4 + 64)
        if len(self._heap) <= threshold:
            self._heap_compaction_deferred = False
            return
        if len(self._heap) > 4096:
            self._heap_compaction_deferred = True
            return
        self._heap = [
            entry for entry in self._heap
            if self._pending.get((entry[2], entry[3], entry[4])) == (entry[0], entry[5])
        ]
        heapq.heapify(self._heap)
        self._heap_compaction_deferred = False

    def on_block_changed(self, x, y, z, old_type, new_type):
        self._handling_change += 1
        try:
            changed_fluid = _fluid_family(new_type)
            pos = (int(x), int(y), int(z))
            if changed_fluid:
                self._moving_cells.add(pos)
                self.schedule(x, y, z, changed_fluid)
            else:
                self._moving_cells.discard(pos)
                self._pending.pop(pos, None)
                self._untrack_cell(pos)
            _schedule_adjacent(self, x, y, z)

            if self._handling_change == 1:
                if new_type == LAVA and old_type != LAVA:
                    _try_harden_lava(self.world, x, y, z, getattr(self.world, "_tick_rng", None) or random)
                for dx, dy, dz in NEIGHBORS_6:
                    nx, ny, nz = x + dx, y + dy, z + dz
                    if 0 <= ny < self.world.CHUNK_HEIGHT and self.world.get_block_type(nx, ny, nz) == LAVA:
                        _try_harden_lava(self.world, nx, ny, nz, getattr(self.world, "_tick_rng", None) or random)
        finally:
            self._handling_change -= 1

    def on_chunk_loaded(self, chunk):
        source_ids = getattr(chunk, "_source_block_ids", None)
        blocks = chunk.blocks
        metadata = chunk.metadata
        height, depth, width = chunk.height, chunk.depth, chunk.width
        has_source_ids = source_ids is not None and len(source_ids) == len(blocks)
        for raw_index in find_chunk_block_indices(chunk, FLUID_BLOCK_TYPES):
            index = int(raw_index)
            internal_id = int(blocks[index])
            if not internal_id or internal_id >= len(chunk._id_to_block_type):
                continue
            fluid_type = _fluid_family(chunk._id_to_block_type[internal_id])
            if fluid_type is None:
                continue
            classic_id = source_ids[index] if has_source_ids else None
            if (classic_id in (8, 10)) or (int(metadata[index]) & 15) != 0:
                y = index % height
                column = index // height
                lz = column % depth
                lx = column // depth
                self.schedule(chunk.start_x + lx, y, chunk.start_z + lz, fluid_type)

    @property
    def pending_count(self):
        return len(self._pending)
