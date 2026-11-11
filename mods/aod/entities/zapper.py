
from __future__ import annotations

from pathlib import Path

from game.entities.creeper import Creeper
from game.entities.entity import Entity

from ..systems.zapper_electricity import (
    ZAPPER_IDLE_PULSE_TICKS,
    detonate_zapper,
    emit_zapper_idle_stelmos_fire,
)

ZAPPER_SAVE_ID = "aod:zapper"
ZAPPER_DEBUG_SPAWN_ID = 15  
_ZAPPER_TEXTURE = None
_ZAPPER_TEXTURE_ATTEMPTED = False


def _load_zapper_texture():
    global _ZAPPER_TEXTURE, _ZAPPER_TEXTURE_ATTEMPTED
    if _ZAPPER_TEXTURE is not None or _ZAPPER_TEXTURE_ATTEMPTED:
        return _ZAPPER_TEXTURE
    _ZAPPER_TEXTURE_ATTEMPTED = True
    texture_path = Path(__file__).resolve().parents[1] / "zapper.png"
    try:
        import pygame as pg
        from engine.textures import Texture
        if not texture_path.is_file():
            return None
        surface = pg.image.load(str(texture_path)).convert_alpha()
        if surface.get_size() != (64, 32):
            raise ValueError(f"Expected 64x32 Zapper texture, got {surface.get_size()}")
        _ZAPPER_TEXTURE = Texture(surface)
    except Exception as error:
        try:
            import logging
            logging.getLogger(__name__).debug("Could not load AOD Zapper skin: %s", error)
        except Exception:
            pass
    return _ZAPPER_TEXTURE


class Zapper(Creeper):

    HEALTH = 20
    FUSE_TIME = 30
    MOVE_SPEED = 0.7
    MAX_SPAWN_GROUP = 2
    ELECTRIC_RADIUS = 5.5
    ELECTRIC_DAMAGE = 7

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.skin_path = "mob/creeper.png"  
        self.skin_texture = _load_zapper_texture()
        self._zapper_pulse_counter = 0

    def _explode(self) -> None:
        world = Entity.world
        if world is not None:
            try:
                detonate_zapper(
                    world, self, radius=self.ELECTRIC_RADIUS,
                    maximum_damage=self.ELECTRIC_DAMAGE, rng=self._rng,
                )
            except Exception:
                pass
        self.dead = True
        self.health = 0
        self.player_to_attack = None

    def _living_ai_tick(self) -> None:
        super()._living_ai_tick()
        if self.dead or self.health <= 0:
            return
        self._zapper_pulse_counter += 1
        if self._zapper_pulse_counter >= ZAPPER_IDLE_PULSE_TICKS:
            self._zapper_pulse_counter = 0
            world = Entity.world
            if world is not None:
                try:
                    emit_zapper_idle_stelmos_fire(world, self, rng=self._rng)
                except Exception:
                    pass


EntityZapper = Zapper


def install_entity_hooks():
    _install_natural_spawn_hook()
    _install_debug_spawn_hook()
    _install_save_hooks()


def _install_debug_spawn_hook():
    try:
        from game.entities.debug_spawning import DEBUG_ENTITY_TYPES
        if isinstance(DEBUG_ENTITY_TYPES, dict):
            DEBUG_ENTITY_TYPES[ZAPPER_DEBUG_SPAWN_ID] = Zapper
            return True
    except Exception:
        return False
    return False


def _install_natural_spawn_hook():
    try:
        from game.entities.spawning import NaturalMobSpawner
    except Exception:
        return False
    original = NaturalMobSpawner.__init__
    if getattr(original, "_aod_zapper_patched", False):
        return True

    def init(spawner, max_spawns, entity_types, **kwargs):
        types = tuple(entity_types)
        creeper_class = next((entity_type for entity_type in types
                              if getattr(entity_type, "__name__", "") == "Creeper"
                              and getattr(entity_type, "__module__", "") == "game.entities.creeper"),
                             None)
        if creeper_class is not None and Zapper not in types:
            weighted = []
            for entity_type in types:
                if entity_type is creeper_class:
                    weighted.extend((creeper_class,) * 4)
                    weighted.append(Zapper)
                else:
                    weighted.extend((entity_type,) * 5)
            types = tuple(weighted)
        return original(spawner, max_spawns, types, **kwargs)

    init._aod_zapper_patched = True
    NaturalMobSpawner.__init__ = init
    return True


def _install_save_hooks():
    try:
        import game.content_ids as content_ids
        import game.world.savegame as savegame
        from game.world import World
        from game.world.savegame import WorldSave
    except Exception:
        return False

    if ZAPPER_SAVE_ID not in content_ids.CLASSIC_LIVING_ENTITY_IDS:
        content_ids.CLASSIC_LIVING_ENTITY_IDS = (
            tuple(content_ids.CLASSIC_LIVING_ENTITY_IDS) + (ZAPPER_SAVE_ID,)
        )
    savegame.CLASSIC_LIVING_ENTITY_IDS = content_ids.CLASSIC_LIVING_ENTITY_IDS

    registry_method = WorldSave._living_mob_registry
    if not getattr(registry_method, "_aod_zapper_patched", False):
        original_registry = registry_method.__func__

        def living_registry(cls):
            registry = original_registry(cls)
            if registry.get(ZAPPER_SAVE_ID) is not Zapper:
                registry[ZAPPER_SAVE_ID] = Zapper
            return registry

        living_registry._aod_zapper_patched = True
        WorldSave._living_mob_registry = classmethod(living_registry)

    encode_method = WorldSave._encode_entities
    if not getattr(encode_method, "_aod_zapper_patched", False):
        def encode_entities(save, world, cx, cz):
            records = encode_method(save, world, cx, cz)
            try:
                from nbtlib import tag
                chunk_key = (int(cx), int(cz))
                for entity in getattr(world, "entities", ()):
                    if not isinstance(entity, Zapper):
                        continue
                    if world._get_chunk_index(entity.pos[0], entity.pos[2]) != chunk_key:
                        continue
                    for record in records:
                        if str(record.get("id", "")) == ZAPPER_SAVE_ID:
                            record["McpyFuse"] = tag.Short(max(
                                0, min(32767, int(getattr(entity, "time_since_ignited", 0)))
                            ))
                            break
            except Exception:
                pass
            return records
        encode_entities._aod_zapper_patched = True
        WorldSave._encode_entities = encode_entities

    save_id_method = WorldSave._living_mob_save_id
    if not getattr(save_id_method, "_aod_zapper_patched", False):
        original_save_id = save_id_method.__func__

        def living_save_id(cls, entity):
            if isinstance(entity, Zapper):
                return ZAPPER_SAVE_ID
            return original_save_id(cls, entity)

        living_save_id._aod_zapper_patched = True
        WorldSave._living_mob_save_id = classmethod(living_save_id)

    original_restore = World._restore_saved_entities
    if not getattr(original_restore, "_aod_zapper_patched", False):
        def restore_saved_entities(world, records):
            records = tuple(records or ())
            before = {id(entity) for entity in getattr(world, "entities", ())}
            result = original_restore(world, records)
            zapper_records = [record for record in records
                              if record.get("id") == ZAPPER_SAVE_ID]
            if zapper_records:
                new_zappers = [entity for entity in getattr(world, "entities", ())
                               if id(entity) not in before and isinstance(entity, Zapper)]
                for entity, record in zip(new_zappers, zapper_records):
                    fuse = max(0, int(record.get("fuse", 0) or 0))
                    entity.time_since_ignited = fuse
                    entity.last_active_time = fuse
            return result
        restore_saved_entities._aod_zapper_patched = True
        World._restore_saved_entities = restore_saved_entities

    return True
