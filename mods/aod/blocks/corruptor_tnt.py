import json
import math
import random

from ..common import (
    MOD_DIR, MOD_ID, CORRUPTOR_TNT_BLOCK_ID, CORRUPTOR_TNT_RADIUS,
    CORRUPTOR_TNT_VARIANT, KINOKO_VARIANT, PHOTOBOMB_VARIANT,
    map_aod_texture_indices,
)
from ..systems.corruption import corrupt_around

_TNT_RENDERER_PATCHED = False

def register_block():
    from game.block import Block
    from game.entities.item import Item

    Block._load_defs()
    data_path = MOD_DIR / "data" / "blocks.json"
    try:
        mod_block_defs = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        mod_block_defs = {}
    source = mod_block_defs.get(CORRUPTOR_TNT_VARIANT, {})
    definition = dict(source) if isinstance(source, dict) else {}
    definition.update({
        "name": "Corruptor TNT",
        "mod_id": MOD_ID,
        "id": CORRUPTOR_TNT_BLOCK_ID,
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
    block_item.setdefault("id", 9101)
    definition["blockitem"] = block_item

    Block._block_defs[CORRUPTOR_TNT_VARIANT] = definition
    Item.block_defs = Block._block_defs

def _spawn_corruptor_tnt(world, x, y, z, *, fuse=None, play_sound=True):
    from game.entities.tnt import DEFAULT_FUSE, PrimedTNT, _play

    entity = PrimedTNT.at_block(int(x), int(y), int(z))
    entity._aod_variant = CORRUPTOR_TNT_VARIANT
    if fuse is not None:
        entity.fuse = max(0.0, min(float(DEFAULT_FUSE), float(fuse)))
    world.spawn_entity(entity)
    if play_sound:
        _play(world, "random.fuse", 1.0, 1.0, entity.center)
    return entity

def _patch_corruptor_tnt_behaviors():
    import game.entities.tnt as tnt_module

    original_ignite = tnt_module.ignite_tnt
    if not getattr(original_ignite, "_aod_corruptor_tnt_patched", False):
        def ignite_tnt(world, x, y, z, fuse=None, play_sound=True):
            if (world is not None
                    and world.get_block_type(int(x), int(y), int(z)) == CORRUPTOR_TNT_VARIANT):
                return _spawn_corruptor_tnt(
                    world, x, y, z, fuse=fuse, play_sound=play_sound,
                )
            return original_ignite(
                world, x, y, z, fuse=fuse, play_sound=play_sound,
            )

        ignite_tnt._aod_corruptor_tnt_patched = True
        tnt_module.ignite_tnt = ignite_tnt

    original_destroy_cells = tnt_module._destroy_cells
    if not getattr(original_destroy_cells, "_aod_corruptor_tnt_patched", False):
        def destroy_cells(world, cells, x, y, z, power, rng,
                          stats=None, light_changes=None):
            chained_positions = []
            seen = set()
            for position in cells:
                cell = tuple(int(value) for value in position)
                if cell in seen:
                    continue
                seen.add(cell)
                if world.get_block_type(*cell) == CORRUPTOR_TNT_VARIANT:
                    chained_positions.append(cell)

            chained = original_destroy_cells(
                world, cells, x, y, z, power, rng,
                stats=stats, light_changes=light_changes,
            ) or []
            for bx, by, bz in chained_positions:
                fuse = rng.randrange(max(1, int(tnt_module.DEFAULT_FUSE) // 4))
                fuse += int(tnt_module.DEFAULT_FUSE) // 8
                chained.append(_spawn_corruptor_tnt(
                    world, bx, by, bz, fuse=fuse, play_sound=False,
                ))
            return chained

        destroy_cells._aod_corruptor_tnt_patched = True
        tnt_module._destroy_cells = destroy_cells

    original_explosion = tnt_module.create_explosion
    if not getattr(original_explosion, "_aod_corruptor_tnt_patched", False):
        def create_explosion(world, source, x, y, z,
                             power=tnt_module.EXPLOSION_POWER,
                             rng=random, fire=False):
            result = original_explosion(
                world, source, x, y, z, power=power, rng=rng, fire=fire,
            )
            if getattr(source, "_aod_variant", None) == CORRUPTOR_TNT_VARIANT:
                center = tuple(int(math.floor(float(value) + 0.5)) for value in (x, y, z))
                corrupt_around(
                    world, *center, CORRUPTOR_TNT_RADIUS, rng,
                )
            return result

        create_explosion._aod_corruptor_tnt_patched = True
        tnt_module.create_explosion = create_explosion

def patch_renderer():
    global _TNT_RENDERER_PATCHED
    if _TNT_RENDERER_PATCHED:
        return
    try:
        from game.entities.tnt_model import PrimedTNTRendererMixin
        from game.entities.item import Item
    except Exception:
        return

    original = PrimedTNTRendererMixin.draw

    def draw(self):
        variant = getattr(self, "_aod_variant", None)
        if variant not in (CORRUPTOR_TNT_VARIANT, KINOKO_VARIANT, PHOTOBOMB_VARIANT):
            return original(self)
        definition = Item.block_defs.get(variant)
        if not isinstance(definition, dict):
            return original(self)
        vanilla_definition = Item.block_defs.get("tnt")
        Item.block_defs["tnt"] = definition
        try:
            return original(self)
        finally:
            if vanilla_definition is None:
                Item.block_defs.pop("tnt", None)
            else:
                Item.block_defs["tnt"] = vanilla_definition

    draw._aod_corruptor_tnt_patched = True
    PrimedTNTRendererMixin.draw = draw
    _TNT_RENDERER_PATCHED = True

def initialize():
    register_block()
    _patch_corruptor_tnt_behaviors()
    patch_renderer()
    from .tnt_breaking import patch_player_breaking
    patch_player_breaking()

