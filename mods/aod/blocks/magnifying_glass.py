
import json

from ..common import (
    MAGNIFYING_GLASS_BLOCK_ID, MAGNIFYING_GLASS_VARIANT,
    MOD_DIR, MOD_ID, map_aod_texture_indices,
)
from ..systems.magnifying_glass import (
    MAX_BLOCKS_PER_PULSE, MAX_EFFECTS_PER_PULSE, PULSE_INTERVAL,
    _block_definitions, age_solar_spots, focus_sunlight, sun_beam_direction,
)


def register_block():
    from game.block import Block
    from game.entities.item import Item

    Block._load_defs()
    try:
        data = json.loads((MOD_DIR / "data" / "blocks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        data = {}
    raw = data.get(MAGNIFYING_GLASS_VARIANT, {}) if isinstance(data, dict) else {}
    definition = dict(raw) if isinstance(raw, dict) else {}
    definition.update({
        "name": "Magnifying Glass",
        "mod_id": MOD_ID,
        "id": MAGNIFYING_GLASS_BLOCK_ID,
        "material": "glass",
        "transparent": True,
        "lightOpacity": 0,
        "collision": True,
    })
    definition["faces"] = map_aod_texture_indices(definition.get("faces", 14))
    definition["particleTexture"] = map_aod_texture_indices(
        definition.get("particleTexture", 14)
    )
    block_item = definition.get("blockitem")
    block_item = dict(block_item) if isinstance(block_item, dict) else {}
    block_item.setdefault("type", "block_item")
    block_item.setdefault("stack", 64)
    block_item.setdefault("id", 9106)
    definition["blockitem"] = block_item

    Block._block_defs[MAGNIFYING_GLASS_VARIANT] = definition
    Item.block_defs = Block._block_defs
    return definition


def _index_chunk(world, chunk):
    positions = getattr(world, "_aod_magnifying_glasses", None)
    if not isinstance(positions, set):
        positions = set()
        world._aod_magnifying_glasses = positions
    id_to_type = getattr(chunk, "_id_to_block_type", ())
    blocks = getattr(chunk, "blocks", ())
    height = int(getattr(chunk, "height", 0) or 0)
    depth = int(getattr(chunk, "depth", 0) or 0)
    if height <= 0 or depth <= 0:
        return 0
    found = 0
    for index, raw_id in enumerate(blocks):
        block_id = int(raw_id)
        if block_id <= 0 or block_id >= len(id_to_type):
            continue
        if id_to_type[block_id] != MAGNIFYING_GLASS_VARIANT:
            continue
        y = index % height
        column = index // height
        local_z = column % depth
        local_x = column // depth
        position = (
            int(chunk.start_x + local_x), int(y),
            int(chunk.start_z + local_z),
        )
        if position not in positions:
            positions.add(position)
        found += 1
    return found


def _ensure_world_index(world):
    if getattr(world, "_aod_magnifying_index_seeded", False):
        return
    if not isinstance(getattr(world, "_aod_magnifying_glasses", None), set):
        world._aod_magnifying_glasses = set()
    for chunk in (getattr(world, "chunks", {}) or {}).values():
        _index_chunk(world, chunk)
    world._aod_magnifying_index_seeded = True


def _tick_magnifying_glass_world(world, elapsed):
    try:
        dt = max(0.0, min(0.5, float(elapsed)))
    except (TypeError, ValueError, OverflowError):
        dt = 0.0
    accumulator = float(getattr(world, "_aod_magnifying_accumulator", 0.0)) + dt
    if accumulator < PULSE_INTERVAL:
        world._aod_magnifying_accumulator = accumulator
        return 0
    world._aod_magnifying_accumulator = accumulator % PULSE_INTERVAL

    direction = sun_beam_direction(world)
    if direction is None:
        return 0
    positions = getattr(world, "_aod_magnifying_glasses", set())
    if not positions:
        return 0

    active = sorted(tuple(position) for position in positions)
    cursor = int(getattr(world, "_aod_magnifying_cursor", 0)) % len(active)
    count = min(len(active), MAX_BLOCKS_PER_PULSE)
    if cursor + count <= len(active):
        batch = active[cursor:cursor + count]
    else:
        batch = active[cursor:] + active[:(cursor + count) % len(active)]
    world._aod_magnifying_cursor = (cursor + count) % len(active)

    processed = 0
    effects_remaining = MAX_EFFECTS_PER_PULSE
    definitions = _block_definitions()
    for position in batch:
        if (not _loaded(world, position)
                or world.get_block_type(*position) != MAGNIFYING_GLASS_VARIANT):
            positions.discard(position)
            continue
        result = focus_sunlight(
            world, position, direction, definitions=definitions,
            emit_effect=effects_remaining > 0,
        )
        if result.get("particles", 0):
            effects_remaining -= 1
        processed += 1
    return processed


def _loaded(world, position):
    chunks = getattr(world, "chunks", None)
    get_index = getattr(world, "_get_chunk_index", None)
    if isinstance(chunks, dict) and callable(get_index):
        try:
            return get_index(int(position[0]), int(position[2])) in chunks
        except Exception:
            return False
    return True


def _install_world_hooks():
    from game.world import World

    original_set_block = World.set_block
    if not getattr(original_set_block, "_aod_magnifying_patched", False):
        def set_block(world, x, y, z, block_type, meta=0, immediate_mesh=False):
            position = (int(x), int(y), int(z))
            result = original_set_block(
                world, x, y, z, block_type, meta=meta,
                immediate_mesh=immediate_mesh,
            )
            positions = getattr(world, "_aod_magnifying_glasses", None)
            if isinstance(positions, set):
                current = world.get_block_type(*position)
                if current == MAGNIFYING_GLASS_VARIANT:
                    positions.add(position)
                else:
                    positions.discard(position)
            return result
        set_block._aod_magnifying_patched = True
        World.set_block = set_block

    original_generate_chunk = World._generate_chunk_impl
    if not getattr(original_generate_chunk, "_aod_magnifying_patched", False):
        def generate_chunk_impl(world, *args, **kwargs):
            chunk = original_generate_chunk(world, *args, **kwargs)
            if chunk is not None and getattr(world, "_aod_magnifying_index_seeded", False):
                _index_chunk(world, chunk)
            return chunk
        generate_chunk_impl._aod_magnifying_patched = True
        World._generate_chunk_impl = generate_chunk_impl

    original_unload_chunk = World._unload_chunk
    if not getattr(original_unload_chunk, "_aod_magnifying_patched", False):
        def unload_chunk(world, cx, cz):
            positions = getattr(world, "_aod_magnifying_glasses", None)
            if isinstance(positions, set):
                size = int(getattr(world, "CHUNK_SIZE", 16))
                min_x, min_z = int(cx) * size, int(cz) * size
                max_x, max_z = min_x + size, min_z + size
                positions.difference_update({
                    position for position in positions
                    if min_x <= position[0] < max_x
                    and min_z <= position[2] < max_z
                })
            return original_unload_chunk(world, cx, cz)
        unload_chunk._aod_magnifying_patched = True
        World._unload_chunk = unload_chunk

    original_draw_particles = World.draw_particles
    if not getattr(original_draw_particles, "_aod_magnifying_patched", False):
        def draw_particles(world, camera_pos, yaw, pitch, partial=0.0, fluid_mode=None):
            result = original_draw_particles(
                world, camera_pos, yaw, pitch, partial=partial,
                fluid_mode=fluid_mode,
            )
            from ..rendering.solar_spot import draw_solar_spots
            draw_solar_spots(world, camera_pos, partial=partial)
            return result
        draw_particles._aod_magnifying_patched = True
        World.draw_particles = draw_particles

    original_update = World.update
    if not getattr(original_update, "_aod_magnifying_patched", False):
        def update(world, player_pos, dt=0.0):
            result = original_update(world, player_pos, dt=dt)
            age_solar_spots(world, dt)
            _ensure_world_index(world)
            if not bool(getattr(world, "_multiplayer_client_world", False)):
                _tick_magnifying_glass_world(world, dt)
            return result
        update._aod_magnifying_patched = True
        World.update = update


def initialize(game=None):
    register_block()
    _install_world_hooks()
    world = getattr(game, "world", None) if game is not None else None
    if world is not None:
        _ensure_world_index(world)
