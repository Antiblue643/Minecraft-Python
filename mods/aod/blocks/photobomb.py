
import json
import random

from ..common import (
    MOD_DIR, MOD_ID, PHOTOBOMB_BLOCK_ID, PHOTOBOMB_VARIANT,
    map_aod_texture_indices,
)
from ..systems.photobomb_thermal import photobomb_thermal_pulse


def register_block():
    from game.block import Block
    from game.entities.item import Item

    Block._load_defs()
    try:
        data = json.loads((MOD_DIR / "data" / "blocks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        data = {}
    raw = data.get(PHOTOBOMB_VARIANT, {}) if isinstance(data, dict) else {}
    definition = dict(raw) if isinstance(raw, dict) else {}
    definition.update({
        "name": "Photobomb",
        "mod_id": MOD_ID,
        "id": PHOTOBOMB_BLOCK_ID,
        "material": "tnt",
        "dropTool": "none",
        "tntBlock": True,
    })
    definition["faces"] = map_aod_texture_indices(definition.get("faces", 0))
    particle_texture = definition.get("particleTexture")
    if particle_texture is None:
        faces = definition.get("faces", 0)
        particle_texture = (
            int(faces[0]) if isinstance(faces, (list, tuple)) and faces
            else int(faces) if not isinstance(faces, (list, tuple)) else 0
        )
    definition["particleTexture"] = map_aod_texture_indices(particle_texture)

    block_item = definition.get("blockitem")
    block_item = dict(block_item) if isinstance(block_item, dict) else {}
    block_item.setdefault("type", "block_item")
    block_item.setdefault("stack", 64)
    block_item.setdefault("id", 9105)
    definition["blockitem"] = block_item

    Block._block_defs[PHOTOBOMB_VARIANT] = definition
    Item.block_defs = Block._block_defs
    return definition


def _spawn_photobomb_tnt(world, x, y, z, *, fuse=None, play_sound=True):
    from game.entities.tnt import DEFAULT_FUSE, PrimedTNT, _play

    entity = PrimedTNT.at_block(int(x), int(y), int(z))
    entity._aod_variant = PHOTOBOMB_VARIANT
    if fuse is not None:
        entity.fuse = max(0.0, min(float(DEFAULT_FUSE), float(fuse)))
    world.spawn_entity(entity)
    if play_sound:
        _play(world, "random.fuse", 1.0, 1.0, entity.center)
    return entity


def _patch_photobomb_tnt_behaviors():
    import game.entities.tnt as tnt_module

    original_ignite = tnt_module.ignite_tnt
    if not getattr(original_ignite, "_aod_photobomb_patched", False):
        def ignite_tnt(world, x, y, z, fuse=None, play_sound=True):
            if (world is not None
                    and world.get_block_type(int(x), int(y), int(z)) == PHOTOBOMB_VARIANT):
                return _spawn_photobomb_tnt(
                    world, x, y, z, fuse=fuse, play_sound=play_sound,
                )
            return original_ignite(
                world, x, y, z, fuse=fuse, play_sound=play_sound,
            )

        ignite_tnt._aod_photobomb_patched = True
        tnt_module.ignite_tnt = ignite_tnt

    original_destroy_cells = tnt_module._destroy_cells
    if not getattr(original_destroy_cells, "_aod_photobomb_patched", False):
        def destroy_cells(world, cells, x, y, z, power, rng,
                          stats=None, light_changes=None):
            positions = []
            seen = set()
            for position in cells:
                cell = tuple(int(value) for value in position)
                if cell in seen:
                    continue
                seen.add(cell)
                if world.get_block_type(*cell) == PHOTOBOMB_VARIANT:
                    positions.append(cell)

            chained = original_destroy_cells(
                world, cells, x, y, z, power, rng,
                stats=stats, light_changes=light_changes,
            ) or []
            for bx, by, bz in positions:
                base = max(1, int(tnt_module.DEFAULT_FUSE))
                fuse = rng.randrange(max(1, base // 4)) + base // 8
                chained.append(_spawn_photobomb_tnt(
                    world, bx, by, bz, fuse=fuse, play_sound=False,
                ))
            return chained

        destroy_cells._aod_photobomb_patched = True
        tnt_module._destroy_cells = destroy_cells

    original_explosion = tnt_module.create_explosion
    if not getattr(original_explosion, "_aod_photobomb_patched", False):
        def create_explosion(world, source, x, y, z,
                             power=tnt_module.EXPLOSION_POWER,
                             rng=random, fire=False):
            if getattr(source, "_aod_variant", None) == PHOTOBOMB_VARIANT:
                return photobomb_thermal_pulse(
                    world, source, x, y, z, rng=rng,
                )
            return original_explosion(
                world, source, x, y, z, power=power, rng=rng, fire=fire,
            )

        create_explosion._aod_photobomb_patched = True
        tnt_module.create_explosion = create_explosion


def initialize():
    register_block()
    _patch_photobomb_tnt_behaviors()
