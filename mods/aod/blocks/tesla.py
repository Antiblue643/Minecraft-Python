
import heapq
import json
import random

from ..common import MOD_DIR, MOD_ID, TESLA_BLOCK_ID, TESLA_VARIANT, map_aod_texture_indices
from ..systems.tesla_electricity import (
    TESLA_DAMAGE_INTERVAL,
    TESLA_PULSE_INTERVAL,
    age_tesla_arcs,
    discharge_tesla,
    expire_redstone_interference,
    install_redstone_interference_hooks,
    _living_entities,
    _build_living_entity_grid,
    _block_type_conduction_class,
    TESLA_MAX_ARCS_PER_FRAME,
    TESLA_MAX_ARCS_PER_PULSE,
)
from ..systems.zapper_electricity import age_zapper_stelmos_effects


def _load_definition():
    try:
        data = json.loads((MOD_DIR / "data" / "blocks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        data = {}
    source = data.get(TESLA_VARIANT, {}) if isinstance(data, dict) else {}
    return dict(source) if isinstance(source, dict) else {}


def register_block():
    from game.block import Block
    from game.entities.item import Item

    Block._load_defs()
    definition = _load_definition()
    definition.update({
        "name": "Tesla",
        "mod_id": MOD_ID,
        "id": TESLA_BLOCK_ID,
        "material": "metal",
        "transparent": False,
        "nCull": "all",
        "lightOpacity": 255,
        "collision": True,
    })
    definition["faces"] = map_aod_texture_indices(definition.get("faces", [9, 9, 10, 10, 9, 9]))
    definition["particleTexture"] = map_aod_texture_indices(
        definition.get("particleTexture", 8)
    )
    block_item = definition.get("blockitem")
    block_item = dict(block_item) if isinstance(block_item, dict) else {}
    block_item.setdefault("type", "block_item")
    block_item.setdefault("stack", 64)
    block_item.setdefault("id", 9104)
    definition["blockitem"] = block_item

    Block._block_defs[TESLA_VARIANT] = definition
    Item.block_defs = Block._block_defs
    return definition


def _index_chunk(world, chunk):
    positions = getattr(world, "_aod_tesla_blocks", None)
    if not isinstance(positions, set):
        positions = set()
        world._aod_tesla_blocks = positions
    id_to_type = getattr(chunk, "_id_to_block_type", ())
    blocks = getattr(chunk, "blocks", ())
    height = int(getattr(chunk, "height", 0) or 0)
    depth = int(getattr(chunk, "depth", 0) or 0)
    if height <= 0 or depth <= 0:
        return 0
    found = 0
    added = 0
    for index, raw_id in enumerate(blocks):
        block_id = int(raw_id)
        if block_id <= 0 or block_id >= len(id_to_type):
            continue
        if id_to_type[block_id] != TESLA_VARIANT:
            continue
        y = index % height
        column = index // height
        local_z = column % depth
        local_x = column // depth
        position = (int(chunk.start_x + local_x), int(y), int(chunk.start_z + local_z))
        if position not in positions:
            positions.add(position)
            added += 1
        found += 1
    if added:
        world._aod_tesla_index_revision = int(
            getattr(world, "_aod_tesla_index_revision", 0)
        ) + 1
    return found


def _ensure_world_index(world):
    if getattr(world, "_aod_tesla_index_seeded", False):
        return
    if not isinstance(getattr(world, "_aod_tesla_blocks", None), set):
        world._aod_tesla_blocks = set()
    for chunk in (getattr(world, "chunks", {}) or {}).values():
        _index_chunk(world, chunk)
    world._aod_tesla_index_seeded = True


def _tick_tesla_world(world, elapsed):
    try:
        dt = max(0.0, min(0.5, float(elapsed)))
    except (TypeError, ValueError, OverflowError):
        dt = 0.0

    expire_redstone_interference(world)
    age_tesla_arcs(world, dt)
    age_zapper_stelmos_effects(world, dt)

    positions = getattr(world, "_aod_tesla_blocks", set())
    next_pulse = getattr(world, "_aod_tesla_next_pulse", None)
    if not isinstance(next_pulse, dict):
        next_pulse = {}
        world._aod_tesla_next_pulse = next_pulse

    clock = float(getattr(world, "_aod_tesla_clock", 0.0)) + dt
    world._aod_tesla_clock = clock
    interval = min(TESLA_PULSE_INTERVAL, TESLA_DAMAGE_INTERVAL)
    index_revision = int(getattr(world, "_aod_tesla_index_revision", 0))
    heap = getattr(world, "_aod_tesla_pulse_heap", None)
    rebuild_heap = (
        not isinstance(heap, list)
        or getattr(world, "_aod_tesla_heap_revision", None) != index_revision
    )

    if rebuild_heap:
        active = sorted(tuple(position) for position in positions)
        active_set = set(active)
        for stale in tuple(next_pulse):
            if stale not in active_set:
                next_pulse.pop(stale, None)
        heap = []
        count = max(1, len(active))
        frame_start = clock - dt
        for index, position in enumerate(active):
            due = next_pulse.get(position)
            if due is None:
                phase = (index / float(count)) * interval
                due = frame_start + (phase if phase > 1.0e-9 else interval)
                next_pulse[position] = due
            heapq.heappush(heap, (float(due), position))
        world._aod_tesla_pulse_heap = heap
        world._aod_tesla_heap_revision = index_revision

    due_positions = []
    while heap and heap[0][0] <= clock + 1.0e-9:
        due, position = heapq.heappop(heap)
        if abs(float(next_pulse.get(position, due)) - due) > 1.0e-8:
            continue
        due_positions.append(position)
        next_due = due + interval
        next_pulse[position] = next_due
        heapq.heappush(heap, (next_due, position))

    if due_positions:
        entities = _living_entities(world)
        entity_grid = _build_living_entity_grid(entities)
        processed_redstone = set()
        visual_cursor = int(getattr(world, "_aod_tesla_visual_cursor", 0))
        visual_count = min(len(due_positions), max(0, TESLA_MAX_ARCS_PER_FRAME))
        visual_budget_per_coil = (
            min(TESLA_MAX_ARCS_PER_PULSE,
                max(1, TESLA_MAX_ARCS_PER_FRAME // visual_count))
            if visual_count else 0
        )
        if visual_count:
            offset = visual_cursor % len(due_positions)
            visual_event_indices = {
                (offset + index) % len(due_positions)
                for index in range(visual_count)
            }
            world._aod_tesla_visual_cursor = (offset + visual_count) % len(due_positions)
        else:
            visual_event_indices = set()
        for event_index, position in enumerate(due_positions):
            discharge_tesla(
                world, position, living_entities=entities,
                living_entity_grid=entity_grid,
                max_visual_arcs=(visual_budget_per_coil
                                 if event_index in visual_event_indices else 0),
                processed_redstone=processed_redstone,
            )
            sound_player = getattr(world, "sound_player", None)
            if callable(sound_player):
                sound_player(
                    "aod.tesla_spark", volume=0.4, pitch=random.uniform(0.8, 1.2),
                    position=tuple(float(value) for value in position),
                )


def _install_world_hooks():
    from game.world import World

    original_set_block = World.set_block
    if not getattr(original_set_block, "_aod_tesla_patched", False):
        def set_block(world, x, y, z, block_type, meta=0, immediate_mesh=False):
            position = (int(x), int(y), int(z))
            try:
                previous = world.get_block_type(*position)
            except Exception:
                previous = None
            result = original_set_block(
                world, x, y, z, block_type, meta=meta,
                immediate_mesh=immediate_mesh,
            )
            positions = getattr(world, "_aod_tesla_blocks", None)
            try:
                current = world.get_block_type(*position)
            except Exception:
                current = None
            if isinstance(positions, set):
                was_present = position in positions
                if current == TESLA_VARIANT:
                    positions.add(position)
                else:
                    positions.discard(position)
                is_present = position in positions
                if was_present != is_present:
                    world._aod_tesla_index_revision = int(
                        getattr(world, "_aod_tesla_index_revision", 0)
                    ) + 1
            if _block_type_conduction_class(previous) != _block_type_conduction_class(current):
                world._aod_tesla_topology_revision = int(
                    getattr(world, "_aod_tesla_topology_revision", 0)
                ) + 1
                cache = getattr(world, "_aod_tesla_network_cache", None)
                if isinstance(cache, dict):
                    cache.clear()
            return result
        set_block._aod_tesla_patched = True
        World.set_block = set_block

    original_generate_chunk = World._generate_chunk_impl
    if not getattr(original_generate_chunk, "_aod_tesla_patched", False):
        def generate_chunk_impl(world, *args, **kwargs):
            chunk = original_generate_chunk(world, *args, **kwargs)
            if chunk is not None and getattr(world, "_aod_tesla_index_seeded", False):
                _index_chunk(world, chunk)
            if chunk is not None:
                world._aod_tesla_topology_revision = int(
                    getattr(world, "_aod_tesla_topology_revision", 0)
                ) + 1
                cache = getattr(world, "_aod_tesla_network_cache", None)
                if isinstance(cache, dict):
                    cache.clear()
            return chunk
        generate_chunk_impl._aod_tesla_patched = True
        World._generate_chunk_impl = generate_chunk_impl

    original_unload_chunk = World._unload_chunk
    if not getattr(original_unload_chunk, "_aod_tesla_patched", False):
        def unload_chunk(world, cx, cz):
            positions = getattr(world, "_aod_tesla_blocks", None)
            if isinstance(positions, set):
                size = int(getattr(world, "CHUNK_SIZE", 16))
                min_x, min_z = int(cx) * size, int(cz) * size
                max_x, max_z = min_x + size, min_z + size
                removed = {
                    position for position in positions
                    if min_x <= position[0] < max_x and min_z <= position[2] < max_z
                }
                if removed:
                    positions.difference_update(removed)
                    world._aod_tesla_index_revision = int(
                        getattr(world, "_aod_tesla_index_revision", 0)
                    ) + 1
            world._aod_tesla_topology_revision = int(
                getattr(world, "_aod_tesla_topology_revision", 0)
            ) + 1
            cache = getattr(world, "_aod_tesla_network_cache", None)
            if isinstance(cache, dict):
                cache.clear()
            return original_unload_chunk(world, cx, cz)
        unload_chunk._aod_tesla_patched = True
        World._unload_chunk = unload_chunk

    original_update = World.update
    if not getattr(original_update, "_aod_tesla_patched", False):
        def update(world, player_pos, dt=0.0):
            result = original_update(world, player_pos, dt=dt)
            _ensure_world_index(world)
            _tick_tesla_world(world, dt)
            return result
        update._aod_tesla_patched = True
        World.update = update

    original_draw_particles = World.draw_particles
    if not getattr(original_draw_particles, "_aod_tesla_patched", False):
        def draw_particles(world, camera_pos, yaw, pitch, partial=0.0, fluid_mode=None):
            result = original_draw_particles(
                world, camera_pos, yaw, pitch, partial=partial,
                fluid_mode=fluid_mode,
            )
            from ..rendering.tesla_arcs import draw_tesla_arcs
            from ..rendering.stelmosfire_texture import draw_stelmosfire_edges
            draw_tesla_arcs(world, camera_pos, partial=partial)
            draw_stelmosfire_edges(world, camera_pos, partial=partial)
            return result
        draw_particles._aod_tesla_patched = True
        World.draw_particles = draw_particles


def initialize(game=None):
    register_block()
    install_redstone_interference_hooks()
    _install_world_hooks()
    world = getattr(game, "world", None) if game is not None else None
    if world is not None:
        _ensure_world_index(world)
