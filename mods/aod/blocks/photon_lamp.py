
import json

from ..common import (
    MOD_DIR, MOD_ID, PHOTON_LAMP_BLOCK_ID, PHOTON_LAMP_LIT_BLOCK_ID,
    PHOTON_LAMP_VARIANT, PHOTON_LAMP_LIT_VARIANT, map_aod_texture_indices,
)
from ..systems.photon_radiation import apply_photon_exposure, clamp_signal
from contextlib import nullcontext

WIRE = "redstone_wire"
PHOTON_LAMP_UPDATE_BUDGET = 32
LAMP_TYPES = frozenset({PHOTON_LAMP_VARIANT, PHOTON_LAMP_LIT_VARIANT})
_NEIGHBORS = (
    (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0),
    (0, 0, 1), (0, 0, -1),
)


def _load_mod_block_data():
    try:
        data = json.loads((MOD_DIR / "data" / "blocks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        data = {}
    return data if isinstance(data, dict) else {}


def register_block():
    from game.block import Block
    from game.entities.item import Item

    Block._load_defs()
    data = _load_mod_block_data()
    off = data.get(PHOTON_LAMP_VARIANT, {})
    lit = data.get(PHOTON_LAMP_LIT_VARIANT, {})
    off = dict(off) if isinstance(off, dict) else {}
    lit = dict(lit) if isinstance(lit, dict) else {}

    common = {
        "mod_id": MOD_ID,
        "name": "Photon Lamp",
        "material": "glass",
        "transparent": False,
        "nCull": "all",
        "tool": "pickaxe",
        "toolSpeed": False,
        "hardness": 0.3,
        "resistance": 0.3,
        "lightOpacity": 255,
        "collision": True,
    }
    off.update(common)
    off.update({
        "id": PHOTON_LAMP_BLOCK_ID,
        "lightValue": 0,
    })
    off["faces"] = map_aod_texture_indices(off.get("faces", 7))
    block_item = off.get("blockitem")
    block_item = dict(block_item) if isinstance(block_item, dict) else {}
    block_item.setdefault("type", "block_item")
    block_item.setdefault("stack", 64)
    block_item.setdefault("id", 9103)
    off["blockitem"] = block_item

    lit.update(common)
    lit.update({
        "id": PHOTON_LAMP_LIT_BLOCK_ID,
        "lightValue": 15,
    })
    lit["faces"] = map_aod_texture_indices(lit.get("faces", 8))
    lit.pop("blockitem", None)

    Block._block_defs[PHOTON_LAMP_VARIANT] = off
    Block._block_defs[PHOTON_LAMP_LIT_VARIANT] = lit
    Item.block_defs = Block._block_defs
    return off, lit


def _wire_power_levels(world, redstone):
    if redstone is None:
        return {}
    revision = int(getattr(redstone, "_aod_photon_revision", 0))
    cache = getattr(world, "_aod_photon_wire_cache", None)
    if (isinstance(cache, tuple) and len(cache) == 4
            and cache[0] is redstone and cache[1] == revision
            and not bool(getattr(redstone, "dirty", False))):
        return cache[3]

    components = getattr(redstone, "components", {}) or {}
    wire_positions = {
        tuple(position)
        for position, block_type in components.items()
        if block_type == WIRE and world.get_block_type(*position) == WIRE
    }
    solve = getattr(redstone, "_solve_wire_power", None)
    if callable(solve):
        try:
            power = solve(wire_positions)
        except Exception:
            power = {}
    else:
        power = {}
    if not bool(getattr(redstone, "dirty", False)):
        world._aod_photon_wire_cache = (
            redstone, revision, len(components), power,
        )
    return power


def get_redstone_signal(world, position, wire_power=None):
    redstone = getattr(world, "_redstone_system", None)
    if redstone is None:
        return 0
    x, y, z = (int(value) for value in position)
    if wire_power is None:
        wire_power = _wire_power_levels(world, redstone)
    signal = 0
    direct_source = False
    source_check = getattr(redstone, "_source_can_power_wire", None)

    for dx, dy, dz in _NEIGHBORS:
        neighbor = (x + dx, y + dy, z + dz)
        neighbor_type = world.get_block_type(*neighbor)
        if neighbor_type == WIRE:
            if neighbor in wire_power:
                level = wire_power.get(neighbor, 0)
            else:
                try:
                    level = int(world.get_metadata(*neighbor)) & 15
                except Exception:
                    level = 0
            signal = max(signal, clamp_signal(level))
        elif callable(source_check):
            try:
                if source_check(neighbor, (x, y, z)):
                    direct_source = True
            except Exception:
                pass

    if direct_source:
        return 15
    if signal > 0:
        return signal

    receives_power = getattr(redstone, "_block_receives_power", None)
    if callable(receives_power):
        try:
            return 15 if receives_power((x, y, z), wire_power) else 0
        except Exception:
            return 0
    is_powered = getattr(redstone, "is_block_powered", None)
    if callable(is_powered):
        try:
            return 15 if is_powered(x, y, z) else 0
        except Exception:
            pass
    return 0


def _index_chunk(world, chunk):
    lamps = getattr(world, "_aod_photon_lamps", None)
    if not isinstance(lamps, set):
        lamps = set()
        world._aod_photon_lamps = lamps
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
        if id_to_type[block_id] not in LAMP_TYPES:
            continue
        y = index % height
        column = index // height
        local_z = column % depth
        local_x = column // depth
        position = (int(chunk.start_x + local_x), int(y), int(chunk.start_z + local_z))
        if position not in lamps:
            lamps.add(position)
            added += 1
        found += 1
    if added:
        world._aod_photon_index_revision = int(
            getattr(world, "_aod_photon_index_revision", 0)
        ) + 1
        world._aod_photon_radiation_revision = int(
            getattr(world, "_aod_photon_radiation_revision", 0)
        ) + 1
        world._aod_photon_refresh_requested = True
    return found


def _ensure_world_index(world):
    if getattr(world, "_aod_photon_index_seeded", False):
        return
    lamps = getattr(world, "_aod_photon_lamps", None)
    if not isinstance(lamps, set):
        world._aod_photon_lamps = set()
    for chunk in (getattr(world, "chunks", {}) or {}).values():
        _index_chunk(world, chunk)
    world._aod_photon_index_seeded = True
    world._aod_photon_refresh_requested = True


def _tick_lamp_states(world, *, force=False, budget=PHOTON_LAMP_UPDATE_BUDGET):
    lamps = getattr(world, "_aod_photon_lamps", set())
    redstone = getattr(world, "_redstone_system", None)
    revision = int(getattr(redstone, "_aod_photon_revision", 0)) if redstone is not None else -1
    index_revision = int(getattr(world, "_aod_photon_index_revision", 0))
    radiation_revision_before = int(
        getattr(world, "_aod_photon_radiation_revision", 0)
    )
    refresh_key = (id(redstone), revision, index_revision)
    pending_key = getattr(world, "_aod_photon_pending_refresh_key", None)
    pending = getattr(world, "_aod_photon_pending_refresh_positions", None)
    cursor = int(getattr(world, "_aod_photon_pending_refresh_cursor", 0) or 0)
    refresh_requested = bool(getattr(world, "_aod_photon_refresh_requested", False))

    if (force or refresh_requested or pending is None or pending_key != refresh_key):
        pending = tuple(sorted(lamps))
        cursor = 0
        world._aod_photon_pending_refresh_positions = pending
        world._aod_photon_pending_refresh_cursor = 0
        world._aod_photon_pending_refresh_key = refresh_key
        world._aod_photon_refresh_requested = False
        world._aod_photon_last_refresh_key = None
    elif cursor >= len(pending):
        if getattr(world, "_aod_photon_last_refresh_key", None) == refresh_key:
            return 0
        pending = tuple(sorted(lamps))
        cursor = 0
        world._aod_photon_pending_refresh_positions = pending
        world._aod_photon_pending_refresh_cursor = 0

    if not pending:
        world._aod_photon_last_refresh_key = refresh_key
        world._aod_photon_pending_refresh_positions = ()
        world._aod_photon_pending_refresh_cursor = 0
        world._aod_photon_refresh_requested = False
        return 0

    try:
        limit = max(1, int(budget))
    except (TypeError, ValueError, OverflowError):
        limit = PHOTON_LAMP_UPDATE_BUDGET
    stop = min(len(pending), cursor + limit)
    wire_power = _wire_power_levels(world, redstone) if redstone is not None else {}
    changed = 0
    bulk_edit = getattr(world, "bulk_edit", None)
    context = bulk_edit() if callable(bulk_edit) else nullcontext()
    with context:
        for position in pending[cursor:stop]:
            try:
                block_type = world.get_block_type(*position)
            except Exception:
                lamps.discard(position)
                continue
            if block_type not in LAMP_TYPES:
                lamps.discard(position)
                continue
            signal = get_redstone_signal(world, position, wire_power=wire_power)
            metadata = clamp_signal(world.get_metadata(*position))
            if signal > 0:
                if block_type == PHOTON_LAMP_LIT_VARIANT and metadata != signal:
                    try:
                        from ..rendering.photon_lamp_tint import _set_lamp_metadata_fast
                        fast_updated = _set_lamp_metadata_fast(world, position, signal)
                    except Exception:
                        fast_updated = False
                    if not fast_updated:
                        world.set_block(
                            *position, PHOTON_LAMP_LIT_VARIANT,
                            meta=signal, immediate_mesh=False,
                        )
                    changed += 1
                elif block_type != PHOTON_LAMP_LIT_VARIANT:
                    world.set_block(
                        *position, PHOTON_LAMP_LIT_VARIANT,
                        meta=signal, immediate_mesh=False,
                    )
                    changed += 1
            elif block_type == PHOTON_LAMP_LIT_VARIANT:
                world.set_block(
                    *position, PHOTON_LAMP_VARIANT,
                    meta=0, immediate_mesh=False,
                )
                changed += 1

    world._aod_photon_pending_refresh_cursor = stop
    world._aod_photon_refresh_requested = False
    if (changed and int(getattr(world, "_aod_photon_radiation_revision", 0))
            == radiation_revision_before):
        world._aod_photon_radiation_revision = radiation_revision_before + 1
    if stop >= len(pending):
        world._aod_photon_last_refresh_key = refresh_key
        world._aod_photon_pending_refresh_positions = ()
        world._aod_photon_pending_refresh_cursor = 0
    return changed


def _bump_redstone_revision(redstone):
    redstone._aod_photon_revision = int(
        getattr(redstone, "_aod_photon_revision", 0)
    ) + 1
    world = getattr(redstone, "world", None)
    if world is not None:
        world._aod_photon_wire_cache = None


def _install_redstone_revision_hooks():
    try:
        from game.block.redstone import RedstoneSystem
    except Exception:
        return

    for method_name in ("recompute", "on_block_changed", "on_chunk_loaded", "on_chunk_unloaded"):
        original = getattr(RedstoneSystem, method_name, None)
        if not callable(original) or getattr(original, "_aod_photon_patched", False):
            continue

        def make_wrapper(original_method, name):
            def wrapped(redstone, *args, **kwargs):
                result = original_method(redstone, *args, **kwargs)
                _bump_redstone_revision(redstone)
                return result
            wrapped._aod_photon_patched = True
            wrapped.__name__ = f"aod_photon_{name}"
            return wrapped

        setattr(RedstoneSystem, method_name, make_wrapper(original, method_name))


def _install_world_hooks():
    from game.world import World

    original_set_block = World.set_block
    if not getattr(original_set_block, "_aod_photon_patched", False):
        def set_block(world, x, y, z, block_type, meta=0, immediate_mesh=False):
            position = (int(x), int(y), int(z))
            try:
                previous = world.get_block_type(*position)
            except Exception:
                previous = None
            if previous in LAMP_TYPES:
                try:
                    previous_meta = world.get_metadata(*position)
                except Exception:
                    previous_meta = 0
            else:
                previous_meta = 0
            result = original_set_block(
                world, x, y, z, block_type, meta=meta,
                immediate_mesh=immediate_mesh,
            )
            lamps = getattr(world, "_aod_photon_lamps", None)
            try:
                current = world.get_block_type(*position)
            except Exception:
                current = None
            if current in LAMP_TYPES:
                try:
                    current_meta = world.get_metadata(*position)
                except Exception:
                    current_meta = 0
            else:
                current_meta = 0
            if isinstance(lamps, set):
                if current in LAMP_TYPES:
                    if position not in lamps:
                        world._aod_photon_index_revision = int(
                            getattr(world, "_aod_photon_index_revision", 0)
                        ) + 1
                    lamps.add(position)
                elif previous in LAMP_TYPES:
                    lamps.discard(position)
                    world._aod_photon_index_revision = int(
                        getattr(world, "_aod_photon_index_revision", 0)
                    ) + 1
            if previous in LAMP_TYPES or current in LAMP_TYPES:
                world._aod_photon_refresh_requested = True
                if previous != current or int(previous_meta) != int(current_meta):
                    world._aod_photon_radiation_revision = int(
                        getattr(world, "_aod_photon_radiation_revision", 0)
                    ) + 1
            return result
        set_block._aod_photon_patched = True
        World.set_block = set_block

    original_generate_chunk = World._generate_chunk_impl
    if not getattr(original_generate_chunk, "_aod_photon_patched", False):
        def generate_chunk_impl(world, *args, **kwargs):
            chunk = original_generate_chunk(world, *args, **kwargs)
            if chunk is not None and getattr(world, "_aod_photon_index_seeded", False):
                _index_chunk(world, chunk)
            return chunk
        generate_chunk_impl._aod_photon_patched = True
        World._generate_chunk_impl = generate_chunk_impl

    original_unload_chunk = World._unload_chunk
    if not getattr(original_unload_chunk, "_aod_photon_patched", False):
        def unload_chunk(world, cx, cz):
            lamps = getattr(world, "_aod_photon_lamps", None)
            if isinstance(lamps, set):
                min_x = int(cx) * int(getattr(world, "CHUNK_SIZE", 16))
                min_z = int(cz) * int(getattr(world, "CHUNK_SIZE", 16))
                max_x = min_x + int(getattr(world, "CHUNK_SIZE", 16))
                max_z = min_z + int(getattr(world, "CHUNK_SIZE", 16))
                removed = {
                    pos for pos in lamps
                    if min_x <= pos[0] < max_x and min_z <= pos[2] < max_z
                }
                if removed:
                    lamps.difference_update(removed)
                    world._aod_photon_index_revision = int(
                        getattr(world, "_aod_photon_index_revision", 0)
                    ) + 1
                    world._aod_photon_radiation_revision = int(
                        getattr(world, "_aod_photon_radiation_revision", 0)
                    ) + 1
                    world._aod_photon_refresh_requested = True
            return original_unload_chunk(world, cx, cz)
        unload_chunk._aod_photon_patched = True
        World._unload_chunk = unload_chunk

    original_update = World.update
    if not getattr(original_update, "_aod_photon_patched", False):
        def update(world, player_pos, dt=0.0):
            result = original_update(world, player_pos, dt=dt)
            _ensure_world_index(world)
            _tick_lamp_states(world)
            try:
                elapsed = max(0.0, min(0.5, float(dt)))
            except (TypeError, ValueError):
                elapsed = 0.0
            accumulator = float(getattr(world, "_aod_photon_damage_accum", 0.0)) + elapsed
            while accumulator >= 0.25:
                apply_photon_exposure(
                    world, getattr(world, "_aod_photon_lamps", ()), 0.25,
                )
                accumulator -= 0.25
            world._aod_photon_damage_accum = accumulator
            return result
        update._aod_photon_patched = True
        World.update = update


def initialize(game=None):
    register_block()
    _install_redstone_revision_hooks()
    _install_world_hooks()
    from ..rendering.photon_lamp_tint import initialize as initialize_tint
    initialize_tint()
    world = getattr(game, "world", None) if game is not None else None
    if world is not None:
        _ensure_world_index(world)
