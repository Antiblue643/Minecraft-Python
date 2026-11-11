import math
import contextlib
import random
import json
import time
import os
import operator
import threading
import numpy as _np
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from typing import NamedTuple

from game.world.chunk import Chunk
from game.world.mesh_queue import select_mesh_build_jobs
from game.world.chunk_invalidation import (
    mark_chunk_boundary_dirty, invalidate_loaded_chunk_neighbors,
)
from game.gameplay.performance import PipelineProfiler
from game.block import Block
from game.world.lighting_numba import (
    NUMBA_AVAILABLE as LIGHTING_NUMBA_AVAILABLE,
    np as _lighting_np,
    initialize_skylight as _numba_initialize_skylight,
    propagate_light as _numba_propagate_light,
    relight_block_region as _numba_relight_block_region,
    relight_sky_region as _numba_relight_sky_region,
)
from game.world.value_noise import _noise2
from game.world.terrain import (AlphaTerrainGenerator, SEA_LEVEL, WORLD_HEIGHT,
                                NUMBA_AVAILABLE as TERRAIN_NUMBA_AVAILABLE,
                                _numba_surface_heights)
from game.world.population_snapshot import PopulationWorldSnapshot
from game.world.treegen import generate_alpha_tree, generate_alpha_big_tree
from game.block.ticks import run_random_ticks
from game.block.door_hinges import (is_door_mirrored, register_door_hinges,
                                    unregister_door_hinge, DOOR_TYPES as _DOOR_BLOCKS)
from game.rendering.view_culling import (
    CameraFrustum, chunk_in_frustum, visible_chunk_sections,
    cull_section_masks, sections_from_mask, MAX_MASKED_SECTIONS,
    chunk_load_radius, alpha_far_clip_for_chunk_radius,
    chunk_in_active_view as _chunk_in_active_view,
)
from game.rendering.gl_state import smooth_vertex_colors
from engine.lighting import Lighting
from engine.sky_math import sunrise_color
from game.world.biomes import biome_sky_color
from game.world.spawn_selection import (
    find_sand_spawn_candidate, find_sand_spawn_surface,
)
from game.world.debug_terrain import DebugTerrainGenerator
from game.world.nether_terrain import NetherTerrainGenerator, HELL_SEA_LEVEL


DEBUG_WORLD_SEED_OVERRIDE: int | None = None

BLOCK_LIGHT_RELIGHT_RADIUS = 14


def _terrain_worker_limit(debug_world: bool, cpu_count=None) -> int:
    if debug_world:
        return 1
    available_cpus = max(1, int(cpu_count or os.cpu_count() or 2))
    return max(1, min(6, available_cpus - 1))


_ENTITY_SPATIAL_CELL_SIZE = 8.0
_ENTITY_SPATIAL_MIN_COUNT = 24

_POPULATION_TILE_ENTITY_BLOCKS = frozenset((
    "chest", "furnace", "furnace_lit", "dispenser", "sign", "wall_sign",
    "mob_spawner", "note_block",
))
_POPULATION_FLUID_BLOCKS = frozenset(("water", "lava"))


class _ChunkSaveDeferred(RuntimeError):
    pass


class _ChunkTerrainWorkerResult(NamedTuple):
    generated_data: object = None
    saved_chunk: object = None
    source: str = "terrain"


class World:
    def __init__(
        self,
        renderer,
        seed=None,
        view_distance=8,
        initial_radius=2,
        chunks_per_update=1,
        mesh_builds_per_frame=8,
        mesh_time_budget_ms=10.0,
        progress_callback=None,
        save_dir=None,
        snow_covered=None,
        headless=False,
        debug_world=False,
        accurate_generation=None,
        dimension_id=0,
        initial_position=None,
    ):
        self.renderer = renderer
        self.dimension_id = int(dimension_id)
        if self.dimension_id not in (0, -1):
            raise ValueError(f"Unsupported dimension id: {self.dimension_id}")
        self.is_hell_world = self.dimension_id == -1
        self.world_provider = type("WorldProviderState", (), {"is_hell_world": self.is_hell_world, "dimension_id": self.dimension_id})()
        self.headless = bool(headless)
        self.progress_callback = progress_callback
        self.sound_player = None
        self.profiler = PipelineProfiler(samples=180, enabled=True)
        self._notify_loading(0.0, "Reading world data")
        self.save_store = None
        self._loaded_level_data = None
        self.saved_player_state = None
        if save_dir is not None:
            from game.world.savegame import WorldSave
            self.save_store = WorldSave(save_dir)
            self._loaded_level_data = self.save_store.level_data
            if self._loaded_level_data:
                self.saved_player_state = self._loaded_level_data.get("Player")
                if seed is None:
                    seed = int(self._loaded_level_data.get("RandomSeed", 0))
        if seed is None and self._loaded_level_data is None and DEBUG_WORLD_SEED_OVERRIDE is not None:
            seed = int(DEBUG_WORLD_SEED_OVERRIDE)
        self.lighting = Lighting()
        self.smooth_lighting_enabled = True
        Block._load_defs()

        self.debug_world = (
            False if self.is_hell_world else
            (bool(self._loaded_level_data.get("DebugWorld", False))
             if self._loaded_level_data is not None else bool(debug_world))
        )
        if self._loaded_level_data is not None:
            if "McpyAccurateGeneration" in self._loaded_level_data:
                self.accurate_generation = bool(
                    self._loaded_level_data.get("McpyAccurateGeneration", False)
                )
            else:
                self.accurate_generation = False
        elif accurate_generation is not None:
            self.accurate_generation = bool(accurate_generation)
        else:
            self.accurate_generation = False
        self._terrain_use_numba = (not self.accurate_generation) and bool(
            TERRAIN_NUMBA_AVAILABLE
        )
        if seed is None:
            if self.debug_world:
                seed = random.getrandbits(64) - 2**63
        if self.is_hell_world:
            if seed is None:
                seed = random.getrandbits(64) - 2**63
            self.seed = int(seed)
            self.terrain = NetherTerrainGenerator(self.seed, world_height=WORLD_HEIGHT)
        elif self.debug_world:
            self.seed = int(seed)
            self.terrain = DebugTerrainGenerator(self.seed)
        elif seed is None:
            chosen_seed = None
            chosen_terrain = None
            for _attempt in range(24):
                candidate_seed = random.getrandbits(64) - 2**63
                candidate_terrain = AlphaTerrainGenerator(
                    candidate_seed,
                    sea_level=SEA_LEVEL,
                    world_height=WORLD_HEIGHT,
                    use_numba=self._terrain_use_numba,
                )
                has_land = any(
                    candidate_terrain.height_at(x, z) > SEA_LEVEL + 1
                    for x in range(-20, 21, 2)
                    for z in range(-20, 21, 2)
                )
                if has_land:
                    chosen_seed = candidate_seed
                    chosen_terrain = candidate_terrain
                    break
            if chosen_terrain is None:
                chosen_seed = random.getrandbits(64) - 2**63
                chosen_terrain = AlphaTerrainGenerator(
                    chosen_seed,
                    sea_level=SEA_LEVEL,
                    world_height=WORLD_HEIGHT,
                    use_numba=self._terrain_use_numba,
                )
            self.seed = chosen_seed
            if int(chosen_terrain.seed) == int(self.seed):
                self.terrain = chosen_terrain
            else:
                self.terrain = AlphaTerrainGenerator(
                    self.seed,
                    sea_level=SEA_LEVEL,
                    world_height=WORLD_HEIGHT,
                    use_numba=self._terrain_use_numba,
                )
        else:
            self.seed = int(seed)
            self.terrain = AlphaTerrainGenerator(
                self.seed,
                sea_level=SEA_LEVEL,
                world_height=WORLD_HEIGHT,
                use_numba=self._terrain_use_numba,
            )

        if int(self.terrain.seed) != int(self.seed):
            raise RuntimeError(
                f"World seed mismatch: world={self.seed}, terrain={self.terrain.seed}"
            )

        self.biome_provider = self.terrain.biomes

        if snow_covered is not None:
            self.snow_covered = bool(snow_covered)
        elif self._loaded_level_data:
            self.snow_covered = bool(self._loaded_level_data.get("SnowCovered", False))
        elif save_dir is not None:
            self.snow_covered = False
        else:
            self.snow_covered = False
        self.terrain.snow_covered = self.snow_covered

        self.id_to_block_type = [None]
        self.block_type_to_id = {}
        for block_type in Block._block_defs:
            self._ensure_block_type_id(block_type)

        self.chunks = {}
        self.CHUNK_SIZE = 16
        self.CHUNK_HEIGHT = WORLD_HEIGHT
        self.SEA_LEVEL = HELL_SEA_LEVEL if self.is_hell_world else SEA_LEVEL

        self._light_queue = deque()
        self._light_queue_set = set()
        self._max_light_updates_per_frame = 512
        self._max_light_queue_size = 8192
        self._light_queue_overflow_logged = False

        self._deferred_natural_light_changes = deque()
        self._pending_natural_light_changes = {}
        self._natural_light_changes_by_chunk = {}
        self._natural_light_change_serial = 0
        self._natural_light_jobs_per_frame = 1
        self._natural_light_budget_ms = 2.0
        self._natural_light_step_limit = 256
        self._active_natural_light_job = None
        self._active_natural_light_info = None

        self.block_overrides = {}
        self._worldgen_block_overrides = {}
        self._populated_chunks = set()
        self.door_hinges = {}
        self.door_partners = {}
        self._tick_accum = 0.0
        self.world_time = int((self._loaded_level_data or {}).get("Time", 0))
        self._update_skylight_subtracted()
        self.spawn_x = int((self._loaded_level_data or {}).get("SpawnX", 0))
        self.spawn_y = int((self._loaded_level_data or {}).get("SpawnY", 64))
        self.spawn_z = int((self._loaded_level_data or {}).get("SpawnZ", 0))
        self.sky_color = 8961023
        self.fog_color = 12638463
        self.cloud_color = 16777215
        self.entities = []
        self._entity_spatial_index = None
        self._entity_spatial_positions = None
        self._entity_spatial_columns = None
        self.tile_entities = {}
        self._tick_rng = None
        self._particle_rng = random.Random(self.seed ^ 0xD15A1A7)
        self._particle_viewer_pos = None
        from game.world.fluid_physics import FluidTickScheduler
        self._fluid_scheduler = FluidTickScheduler(self)
        from game.world.fire_index import FirePositionIndex
        self._fire_index = FirePositionIndex(self)
        from game.block.updates import BlockUpdateScheduler
        self._block_updates = BlockUpdateScheduler(self)
        from game.block.redstone import RedstoneSystem
        self._redstone_system = RedstoneSystem(self)
        self._sky_renderer = None
        from game.entities.particles import EffectRenderer
        self.effects = EffectRenderer()

        self._placing_generated_features = False
        self._network_edit_depth = 0
        self._network_block_callback = None
        self._network_bed_claim_callback = None
        self._network_world_event_callback = None
        self._network_sleep_cycle_callback = None
        self._network_note_block_callback = None
        self._mp_suppress_world_events = False
        self._network_entity_spawn_callback = None
        self._network_attack_callback = None
        self._network_entity_interact_callback = None
        self._network_entity_push_callback = None
        self._network_player_attack_callback = None
        self._multiplayer_client_world = False
        self._mp_remote_player_positions = []
        self._mp_chunk_stream_keys = set()

        self._processing_natural_tick = False
        self._player_edit_revision = 0
        self._lighting_lut_cache = None
        self._lighting_lut_size = 0
        self._warm_lighting_kernels()


        self.view_distance = max(2, int(view_distance))
        self.initial_radius = max(1, int(initial_radius))
        self._precomputed_spawn_position = None
        if (not self.debug_world and not self.is_hell_world
                and self._loaded_level_data is None and self.saved_player_state is None):
            self._precomputed_spawn_position = find_sand_spawn_candidate(
                self.terrain, search_radius=128, scan_start_y=63,
            )
        self.chunks_per_update = max(1, int(chunks_per_update))
        self.mesh_builds_per_frame = max(1, int(mesh_builds_per_frame))
        self.mesh_time_budget_ms = max(1.0, float(mesh_time_budget_ms))
        self._mesh_build_avg_ms = 3.5
        self._center_chunk = (0, 0)
        if initial_position is not None:
            self._initial_center_chunk = self._get_chunk_index(initial_position[0], initial_position[2])
        elif self.saved_player_state and self.saved_player_state.get("pos"):
            saved_pos = self.saved_player_state["pos"]
            self._initial_center_chunk = self._get_chunk_index(saved_pos[0], saved_pos[2])
        elif self._loaded_level_data:
            self._initial_center_chunk = self._get_chunk_index(self.spawn_x, self.spawn_z)
        elif self._precomputed_spawn_position is not None:
            self._initial_center_chunk = self._get_chunk_index(
                self._precomputed_spawn_position[0], self._precomputed_spawn_position[2],
            )
        else:
            self._initial_center_chunk = (0, 0)
        self._center_chunk = self._initial_center_chunk

        if self.renderer is not None:
            self.renderer.far_clip = alpha_far_clip_for_chunk_radius(self.view_distance)
            self.renderer.near_clip = 0.05
            self.renderer.render_distance_index = {13: 0, 8: 1, 4: 2, 2: 3}.get(
                self.view_distance, 1,
            )

        center_cx, center_cz = self._initial_center_chunk
        initial = [
            (center_cx + dx, center_cz + dz)
            for dx in range(-self.initial_radius, self.initial_radius + 1)
            for dz in range(-self.initial_radius, self.initial_radius + 1)
        ]
        initial.sort(key=lambda key: (
            (key[0] - center_cx) ** 2 + (key[1] - center_cz) ** 2,
            key[0], key[1],
        ))
        total = max(1, len(initial))
        self._notify_loading(0.0, "Building terrain")
        for index, (cx, cz) in enumerate(initial, 1):
            self._generate_chunk(cx, cz)
            self._notify_loading(0.55 * index / total, f"Building terrain ({index}/{total})")

        deferred_decorations = []
        for index, (cx, cz) in enumerate(initial, 1):
            key = (cx, cz)
            if key in self._populated_chunks:
                pass
            elif ((isinstance(self.terrain, AlphaTerrainGenerator) or self.is_hell_world)
                    and not self._population_neighborhood_loaded(key)):
                deferred_decorations.append(key)
            else:
                self._decorate_chunk(cx, cz)
            self._notify_loading(0.55 + 0.08 * index / total,
                                 f"Placing features ({index}/{total})")

        for index, (cx, cz) in enumerate(initial, 1):
            self._apply_overrides(cx, cz)
            self._notify_loading(0.63 + 0.05 * index / total,
                                 f"Applying saved blocks ({index}/{total})")

        for index, (cx, cz) in enumerate(initial, 1):
            chunk = self.chunks.get((cx, cz))
            if chunk is not None:
                self._init_chunk_lighting(chunk)
            self._notify_loading(0.68 + 0.27 * index / total,
                                 f"Lighting terrain ({index}/{total})")

        if not self.headless:
            chunks = list(self.chunks.values())
            for index, chunk in enumerate(chunks, 1):
                chunk._build_mesh()
                self._notify_loading(0.95 + 0.05 * index / max(1, len(chunks)),
                                     f"Preparing chunks ({index}/{len(chunks)})")
        self._update_skylight_subtracted()
        self._notify_loading(1.0, "Ready")

        self._terrain_worker_count = _terrain_worker_limit(self.debug_world)
        self._terrain_executor = None
        self._terrain_futures = {}
        self._terrain_requested_at = {}
        self._terrain_ready_at = {}
        self._chunk_load_failures = set()
        self._chunk_save_wait_started = {}
        self._chunks_needing_decoration = deque(deferred_decorations)
        self._chunks_needing_lighting = deque()
        self._terrain_worker_local = threading.local()
        self._terrain_max_inflight = self._terrain_worker_count * 2
        self._terrain_integrate_budget_ms = 5.0
        self._terrain_integrate_max_per_frame = 3
        self._terrain_integrate_avg_ms = 1.5
        self._population_executor = None
        self._population_future = None
        self._population_future_key = None
        self._population_future_chunk = None
        self._population_future_neighborhood = None
        self._population_future_revision = 0
        self._population_commit_state = None
        self._population_commit_budget_ms = 2.0
        self._population_commit_max_ops = 192
        self._population_worker_local = threading.local()

    def _notify_loading(self, progress, status):
        if self.progress_callback is not None:
            self.progress_callback(max(0.0, min(1.0, float(progress))), str(status))

    def _ensure_block_type_id(self, block_type):
        block_type = str(block_type)
        existing = self.block_type_to_id.get(block_type)
        if existing is not None:
            return existing
        if len(self.id_to_block_type) >= 256:
            raise ValueError("This compact chunk format supports at most 255 block types.")
        block_id = len(self.id_to_block_type)
        self.id_to_block_type.append(block_type)
        self.block_type_to_id[block_type] = block_id
        return block_id

    def _get_chunk_index(self, x, z):
        size = self.CHUNK_SIZE
        if type(x) is int and type(z) is int:
            return (x >> 4, z >> 4) if size == 16 else (x // size, z // size)
        return (math.floor((x + 0.5) / size),
                math.floor((z + 0.5) / size))

    def _chunk_load_radius(self):
        return chunk_load_radius(self.view_distance)

    def _chunk_in_active_view(self, cx, cz):
        center_cx, center_cz = getattr(self, "_center_chunk", (0, 0))
        return _chunk_in_active_view(
            cx, cz, center_cx, center_cz,
            getattr(self, "view_distance", 0),
        )

    def _chunk_ring_is_loaded(self, center_cx, center_cz, radius):
        radius = max(0, int(radius))
        radius_sq = radius * radius
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                if dx * dx + dz * dz > radius_sq:
                    continue
                if (center_cx + dx, center_cz + dz) not in self.chunks:
                    return False
        return True

    def get_block_type(self, x, y, z):
        if y < 0 or y >= self.CHUNK_HEIGHT:
            return None

        if type(x) is int and type(z) is int:
            cx = x >> 4
            cz = z >> 4
        else:
            cx = (x + 0.5) // 16
            cz = (z + 0.5) // 16
        chunk = self.chunks.get((cx, cz))
        if chunk is None:
            return None
        if type(chunk) is Chunk:
            lx = int(x) - chunk.start_x
            lz = int(z) - chunk.start_z
            if 0 <= lx < 16 and 0 <= lz < 16:
                block_id = chunk.blocks[(lx * chunk.depth + lz) * chunk.height + int(y)]
                return chunk._id_to_block_type[block_id] if block_id else None
        return chunk.get_block_type(x, y, z)

    def get_block(self, x, y, z):
        block_type = self.get_block_type(x, y, z)
        if block_type is None:
            return None
        block = Block(block_type, (x, y, z), world=self, renderer=self.renderer)
        block._mesh_meta = self.get_metadata(x, y, z)
        return block

    def _light_signature(self, block_type):
        if block_type is None:
            return (0, 0)
        definition = Block._block_defs.get(str(block_type), {})
        return (
            int(definition.get("lightOpacity", 255)),
            int(definition.get("lightValue", 0)),
        )

    def is_door_mirrored(self, x, y, z):
        return is_door_mirrored(self, x, y, z)

    def register_door_hinges(self, x, y, z, meta):
        return register_door_hinges(self, x, y, z, meta)

    def _eject_jukebox_record_on_removal(self, x, y, z, meta):
        record_id = {1: "record_13", 2: "record_cat"}.get(int(meta) & 15)
        if record_id is None:
            return False

        position = (float(x) + 0.5, float(y) + 0.5, float(z) + 0.5)
        music_player = getattr(self, "record_music_player", None)
        stop_record = getattr(music_player, "stop_record", None)
        if callable(stop_record):
            stop_record(position)

        from game.entities.item import ItemEntity, ItemStack
        item_position = (
            float(x) + random.random() * 0.7 + 0.15,
            float(y) + random.random() * 0.7 + 0.66,
            float(z) + random.random() * 0.7 + 0.15,
        )
        self.spawn_entity(ItemEntity(
            ItemStack(record_id, 1), item_position, pickup_delay=10,
        ))
        return True

    def _index_fire_chunk(self, chunk):
        fire_index = getattr(self, "_fire_index", None)
        replace_chunk = getattr(fire_index, "replace_chunk", None)
        if callable(replace_chunk):
            replace_chunk(chunk)

    def _remove_fire_chunk_index(self, cx, cz):
        fire_index = getattr(self, "_fire_index", None)
        remove_chunk = getattr(fire_index, "remove_chunk", None)
        if callable(remove_chunk):
            remove_chunk(cx, cz)

    def _note_fire_block_change(self, x, y, z, old_type, new_type):
        fire_index = getattr(self, "_fire_index", None)
        changed = getattr(fire_index, "on_block_changed", None)
        if callable(changed):
            changed(x, y, z, old_type, new_type)

    def set_block(self, x, y, z, block_type, meta=0, immediate_mesh=False):
        if y < 0 or y >= self.CHUNK_HEIGHT:
            return

        if isinstance(block_type, Block):
            block_type = block_type.block_type
        if block_type is not None:
            block_type = str(block_type)
            self._ensure_block_type_id(block_type)

        meta = int(meta) & 15
        cx, cz = self._get_chunk_index(x, z)
        chunk = self.chunks.get((cx, cz))
        old_block_type = chunk.get_block_type(x, y, z) if chunk is not None else None
        old_meta = chunk.get_metadata(x, y, z) if chunk is not None else 0
        if chunk is not None:
            if old_block_type == block_type and old_meta == meta:
                return
            if (old_block_type == "ice" and block_type is None
                    and not self._placing_generated_features):
                block_type, meta = "water", 0
                self._ensure_block_type_id(block_type)

        if (
            chunk is not None
            and old_block_type == "jukebox"
            and block_type != "jukebox"
            and old_meta > 0
            and not self._placing_generated_features
            and not bool(getattr(self, "_multiplayer_client_world", False))
        ):
            self._eject_jukebox_record_on_removal(x, y, z, old_meta)

        if old_block_type in ("wooden_door", "iron_door") and block_type != old_block_type:
            door_y = y - 1 if (old_meta & 8) else y
            unregister_door_hinge(self, x, door_y, z)

        if (
            old_block_type == "bed"
            and block_type != "bed"
            and not getattr(self, "_breaking_bed_pair", False)
            and not self._placing_generated_features
        ):
            self._breaking_bed_pair = True
            try:
                from game.block.bed import break_bed_pair
                break_bed_pair(self, x, y, z)
            finally:
                self._breaking_bed_pair = False
            return

        _TILE_ENTITY_BLOCKS = (
            "chest", "furnace", "furnace_lit", "dispenser", "sign", "wall_sign", "mob_spawner", "note_block",
        )
        old_is_container = old_block_type in _TILE_ENTITY_BLOCKS
        new_is_container = block_type in _TILE_ENTITY_BLOCKS
        same_furnace = (
            old_block_type in ("furnace", "furnace_lit")
            and block_type in ("furnace", "furnace_lit")
        )
        if old_is_container and not same_furnace and old_block_type != block_type:
            te = self.tile_entities.pop((x, y, z), None)
            if te is not None:
                te.on_removed()
        if new_is_container and not same_furnace and (
            (x, y, z) not in self.tile_entities or not old_is_container
        ):
            from game.block.tile_entity import create_tile_entity
            te = create_tile_entity(self, block_type, x, y, z)
            if te is not None:
                self.tile_entities[(x, y, z)] = te

        block_type_changed = old_block_type != block_type
        old_light_signature = self._light_signature(old_block_type)
        new_light_signature = self._light_signature(block_type)
        lighting_changed = old_light_signature != new_light_signature

        if not self._placing_generated_features and not self._processing_natural_tick:
            self._player_edit_revision += 1
            overrides = self.block_overrides.setdefault((cx, cz), {})
            overrides[(x, y, z)] = (block_type, meta) if block_type is not None else None

        if chunk is not None:
            chunk.set_block(x, y, z, block_type, meta=meta)
            if (old_block_type in ("log", "leaves")
                    and old_block_type != block_type
                    and not getattr(self, "_placing_generated_features", False)):
                from game.block.leaf_decay import on_block_removed
                on_block_removed(self, old_block_type, int(x), int(y), int(z))
            self._note_fire_block_change(x, y, z, old_block_type, block_type)
            if not self._placing_generated_features and block_type_changed and lighting_changed:
                if self._processing_natural_tick or getattr(self, "_bulk_edit_depth", 0):
                    new_emission = int(new_light_signature[1])
                    old_emission = int(old_light_signature[1])
                    if new_emission >= 15 and new_emission > old_emission:
                        self._queue_natural_light_change(
                            x, y, z, recompute_block_light=False,
                        )
                        self._enqueue_light("block_increase", x, y, z)
                    else:
                        self._queue_natural_light_change(x, y, z)
                else:
                    self._on_block_light_changed(x, y, z)
            self._mark_neighbors_dirty(x, y, z)
        else:
            self._mark_neighbors_dirty(x, y, z)
        if (immediate_mesh and not self._placing_generated_features
                and not self._processing_natural_tick):
            self._rebuild_edited_mesh_sections(x, y, z)

        if chunk is not None and (old_block_type != block_type or old_meta != meta):
            scheduler = getattr(self, "_fluid_scheduler", None)
            if scheduler is not None:
                scheduler.on_block_changed(x, y, z, old_block_type, block_type)
            if not self._placing_generated_features:
                from game.block.support_rules import (
                    GRAVITY_BLOCKS, STAIR_TYPES, notify_neighbors, update_stair_block,
                )
                notify_neighbors(self, x, y, z)
                if block_type in GRAVITY_BLOCKS:
                    self.schedule_block_update(x, y, z, block_type)
                if block_type in STAIR_TYPES:
                    update_stair_block(self, x, y, z)

            if (old_block_type == "rail" and block_type != "rail"
                    and not self._placing_generated_features):
                from game.block.placement import refresh_rail_and_neighbors
                refresh_rail_and_neighbors(self, x, y, z)

            redstone = getattr(self, "_redstone_system", None)
            if redstone is not None and not self._placing_generated_features:
                redstone.on_block_changed(x, y, z, old_block_type, block_type)

        if (self._network_edit_depth > 0 and self._network_block_callback is not None
                and (old_block_type != block_type or old_meta != meta)):
            try:
                final_type = self.get_block_type(x, y, z)
                final_meta = self.get_metadata(x, y, z)
                self._network_block_callback(int(x), int(y), int(z), final_type, int(final_meta))
            except Exception:
                pass

        if (old_block_type != block_type
                and not self._placing_generated_features
                and not getattr(self, "_portal_update_depth", 0)):
            from game.block.portal_rules import on_block_changed
            on_block_changed(self, x, y, z, old_block_type, block_type)

    def _queue_natural_light_change(self, x, y, z, recompute_block_light=True):
        key = (int(x), int(y), int(z))
        existing = self._pending_natural_light_changes.get(key)
        if existing is not None:
            token, existing_full_recompute = existing
            if recompute_block_light and not existing_full_recompute:
                self._pending_natural_light_changes[key] = (token, True)
            return False
        self._natural_light_change_serial += 1
        token = self._natural_light_change_serial
        self._pending_natural_light_changes[key] = (token, bool(recompute_block_light))
        self._deferred_natural_light_changes.append((key, token))
        chunk_key = self._get_chunk_index(key[0], key[2])
        self._natural_light_changes_by_chunk.setdefault(chunk_key, set()).add(key)
        return True

    def _on_natural_block_light_changed(self, x, y, z, *, recompute_block_light=True):
        cx, cz = self._get_chunk_index(x, z)
        chunk = self.chunks.get((cx, cz))
        if chunk is None:
            return {"skipped": "unloaded"}

        lx = x - chunk.start_x
        lz = z - chunk.start_z
        old_hm = int(chunk.heightmap[lz * chunk.width + lx])
        changed_sections = set()
        new_hm = self._relight_column_sky(
            x, z, mark_dirty=False, changed_sections=changed_sections,
            y_min=0, y_max=self.CHUNK_HEIGHT - 1, recompute_height=True,
        )

        if new_hm < old_hm:
            sky_radius = 14
            do_decrease = False
        elif new_hm > old_hm:
            sky_radius = 2
            do_decrease = True
        else:
            sky_radius = 8
            do_decrease = True

        yield {"phase": "sky", "sky_radius": sky_radius}
        yield from self._relight_sky_region_steps(
            x, z, y=y, radius=sky_radius, do_decrease=do_decrease,
            skip_column_rebuild=True, changed_sections=changed_sections,
        )

        block_radius = 0
        if recompute_block_light:
            block_radius = 12
            yield {"phase": "block", "block_radius": block_radius}
            yield from self._recompute_block_light_region_steps(
                x, y, z, radius=block_radius,
            )
        else:
            self._mark_neighbors_dirty(x, y, z)

        return {
            "sky_radius": sky_radius,
            "block_radius": block_radius,
            "heightmap_delta": int(new_hm) - int(old_hm),
        }

    def _discard_natural_light_changes_for_chunk(self, cx, cz):
        chunk_key = (int(cx), int(cz))
        keys = self._natural_light_changes_by_chunk.pop(chunk_key, ())
        for key in keys:
            self._pending_natural_light_changes.pop(key, None)
        active = self._active_natural_light_info
        if active is not None:
            active_x, _active_y, active_z = active["key"]
            chunk_x0, chunk_x1 = chunk_key[0] * self.CHUNK_SIZE, (chunk_key[0] + 1) * self.CHUNK_SIZE - 1
            chunk_z0, chunk_z1 = chunk_key[1] * self.CHUNK_SIZE, (chunk_key[1] + 1) * self.CHUNK_SIZE - 1
            dx = 0 if chunk_x0 <= active_x <= chunk_x1 else min(abs(active_x - chunk_x0), abs(active_x - chunk_x1))
            dz = 0 if chunk_z0 <= active_z <= chunk_z1 else min(abs(active_z - chunk_z0), abs(active_z - chunk_z1))
            if dx * dx + dz * dz <= 14 * 14:
                active_key = active["key"]
                active_chunk_key = self._get_chunk_index(active_x, active_z)
                self._active_natural_light_job = None
                self._active_natural_light_info = None
                if active_chunk_key != chunk_key and active_chunk_key in self.chunks:
                    self._queue_natural_light_change(*active_key, recompute_block_light=True)

    def _process_deferred_natural_light_changes(self, max_updates=None, max_stale_entries=128):
        if max_updates is None:
            max_updates = self._natural_light_jobs_per_frame
        max_updates = max(0, int(max_updates))
        started = time.perf_counter()
        deadline = started + self._natural_light_budget_ms / 1000.0
        processed = 0
        stale = 0
        steps = 0
        max_job_ms = 0.0
        slow_jobs = []
        last_job = None
        max_pops = max_updates + max(0, int(max_stale_entries))
        popped = 0

        while steps < self._natural_light_step_limit and time.perf_counter() < deadline:
            if self._active_natural_light_job is None:
                if not self._deferred_natural_light_changes or popped >= max_pops:
                    break
                key, token = self._deferred_natural_light_changes.popleft()
                popped += 1
                pending = self._pending_natural_light_changes.get(key)
                if pending is None or pending[0] != token:
                    stale += 1
                    continue
                recompute_block_light = bool(pending[1])
                self._pending_natural_light_changes.pop(key, None)
                chunk_key = self._get_chunk_index(key[0], key[2])
                by_chunk = self._natural_light_changes_by_chunk.get(chunk_key)
                if by_chunk is not None:
                    by_chunk.discard(key)
                    if not by_chunk:
                        self._natural_light_changes_by_chunk.pop(chunk_key, None)
                if chunk_key not in self.chunks:
                    stale += 1
                    continue

                self._active_natural_light_info = {
                    "key": key,
                    "phase": "column",
                    "phase_cpu_ms": {"column": 0.0, "sky": 0.0, "block": 0.0},
                    "cpu_ms": 0.0,
                    "max_step_ms": 0.0,
                    "started_perf": time.perf_counter(),
                    "recompute_block_light": recompute_block_light,
                    "phase_details": {},
                }
                self._active_natural_light_job = self._on_natural_block_light_changed(
                    *key, recompute_block_light=recompute_block_light,
                )

            info = self._active_natural_light_info
            phase_before = info["phase"]
            step_started = time.perf_counter()
            try:
                yielded = next(self._active_natural_light_job)
            except StopIteration as complete:
                step_ms = (time.perf_counter() - step_started) * 1000.0
                info["phase_cpu_ms"][phase_before] = info["phase_cpu_ms"].get(phase_before, 0.0) + step_ms
                info["cpu_ms"] += step_ms
                info["max_step_ms"] = max(info["max_step_ms"], step_ms)
                processed += 1
                total_cpu_ms = info["cpu_ms"]
                total_wall_ms = (time.perf_counter() - info["started_perf"]) * 1000.0
                max_job_ms = max(max_job_ms, total_cpu_ms)
                completed_details = complete.value if isinstance(complete.value, dict) else {}
                last_job = {
                    "x": info["key"][0], "y": info["key"][1], "z": info["key"][2],
                    "cpu_ms": round(total_cpu_ms, 3),
                    "max_step_ms": round(info["max_step_ms"], 3),
                    "elapsed_ms": round(total_wall_ms, 3),
                    "phase_cpu_ms": {k: round(v, 3) for k, v in info["phase_cpu_ms"].items()},
                    **completed_details,
                }
                if total_cpu_ms >= 2.0 and len(slow_jobs) < 4:
                    slow_jobs.append(last_job)
                self._active_natural_light_job = None
                self._active_natural_light_info = None
                if processed >= max_updates:
                    break
                continue

            step_ms = (time.perf_counter() - step_started) * 1000.0
            info["phase_cpu_ms"][phase_before] = info["phase_cpu_ms"].get(phase_before, 0.0) + step_ms
            info["cpu_ms"] += step_ms
            info["max_step_ms"] = max(info["max_step_ms"], step_ms)
            steps += 1
            if isinstance(yielded, dict):
                if yielded.get("phase"):
                    info["phase"] = yielded["phase"]
                info["phase_details"].update(yielded)

        active_info = self._active_natural_light_info
        active_job = None
        if active_info is not None:
            key = active_info["key"]
            active_job = {
                "x": key[0], "y": key[1], "z": key[2],
                "phase": active_info["phase"],
                "cpu_ms": round(active_info["cpu_ms"], 3),
                "max_step_ms": round(active_info["max_step_ms"], 3),
                "elapsed_ms": round((time.perf_counter() - active_info["started_perf"]) * 1000.0, 3),
                "phase_cpu_ms": {k: round(v, 3) for k, v in active_info["phase_cpu_ms"].items()},
                **active_info["phase_details"],
            }
            last_job = active_job
            max_job_ms = max(max_job_ms, active_info["cpu_ms"])

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        due_work_queued = bool(self._deferred_natural_light_changes or active_info is not None)
        budget_exhausted = bool(due_work_queued and (
            elapsed_ms >= self._natural_light_budget_ms or steps >= self._natural_light_step_limit
        ))
        if processed or stale or steps or due_work_queued:
            self.profiler.record_ms(
                "lighting.natural_flush", elapsed_ms,
                event_details={
                    "processed": processed,
                    "steps": steps,
                    "stale_skipped": stale,
                    "pending": len(self._pending_natural_light_changes),
                    "queue_entries": len(self._deferred_natural_light_changes),
                    "active": active_info is not None,
                    "active_job": active_job,
                    "max_job_ms": round(max_job_ms, 3),
                    "last_job": last_job,
                    "slow_jobs": slow_jobs,
                    "budget_ms": self._natural_light_budget_ms,
                    "budget_exhausted": budget_exhausted,
                    "due_work_queued": due_work_queued,
                },
            )
        return processed

    def _rebuild_edited_mesh_sections(self, x, y, z):
        if self.headless:
            return
        cx, cz = self._get_chunk_index(x, z)
        target_chunks = {(cx, cz)}
        local_x = x - cx * self.CHUNK_SIZE
        local_z = z - cz * self.CHUNK_SIZE
        if local_x == 0:
            target_chunks.add((cx - 1, cz))
        elif local_x == self.CHUNK_SIZE - 1:
            target_chunks.add((cx + 1, cz))
        if local_z == 0:
            target_chunks.add((cx, cz - 1))
        elif local_z == self.CHUNK_SIZE - 1:
            target_chunks.add((cx, cz + 1))

        for key in target_chunks:
            chunk = self.chunks.get(key)
            if chunk is None or not chunk._has_mesh:
                continue
            section_height = chunk.section_height
            section_idx = max(0, min(chunk.section_count - 1, int(y) // section_height))
            candidates = {section_idx}
            if int(y) % section_height == 0:
                candidates.add(section_idx - 1)
            if int(y) % section_height == section_height - 1:
                candidates.add(section_idx + 1)
            for idx in sorted(candidates):
                if idx in chunk.dirty_sections:
                    chunk._build_mesh(idx, commit_partial=True)

    def get_metadata(self, x, y, z):
        cx, cz = self._get_chunk_index(x, z)
        chunk = self.chunks.get((cx, cz))
        if chunk is None:
            return 0
        return chunk.get_metadata(x, y, z)

    def _on_block_light_changed(self, x, y, z, *, recompute_block_light=True):
        cx, cz = self._get_chunk_index(x, z)
        chunk = self.chunks.get((cx, cz))
        old_hm = 0
        if chunk is not None:
            lx = x - chunk.start_x
            lz = z - chunk.start_z
            if 0 <= lx < chunk.width and 0 <= lz < chunk.depth:
                old_hm = int(chunk.heightmap[lz * chunk.width + lx])

        changed_sections = set()
        new_hm = self._relight_column_sky(
            x, z, mark_dirty=False, changed_sections=changed_sections,
            y_min=0, y_max=self.CHUNK_HEIGHT - 1, recompute_height=True,
        )

        if new_hm > old_hm and y > 0 and self._opacity_at(x, y, z) >= 255:
            flora_below = self.get_block_type(x, y - 1, z)
            if flora_below in ("yellow_flower", "red_flower", "sapling", "crops"):
                from game.block.support_rules import check_support
                check_support(self, x, y - 1, z)


        if new_hm < old_hm:
            sky_radius = 14
            do_decrease = False
        elif new_hm > old_hm:
            sky_radius = 2
            do_decrease = True
        else:
            sky_radius = 8
            do_decrease = True

        self._relight_sky_region(
            x, z, y=y, radius=sky_radius, do_decrease=do_decrease,
            skip_column_rebuild=True, changed_sections=changed_sections,
        )
        if recompute_block_light:
            self._recompute_block_light_region(
                x, y, z, radius=BLOCK_LIGHT_RELIGHT_RADIUS,
            )
        else:
            self._mark_neighbors_dirty(x, y, z)

    def _relight_column_sky(self, x, z, mark_dirty=True, changed_sections=None,
                            y_min=0, y_max=None, recompute_height=True):
        cx, cz = self._get_chunk_index(x, z)
        chunk = self.chunks.get((cx, cz))
        if chunk is None:
            return 0
        lx = x - chunk.start_x
        lz = z - chunk.start_z
        if not (0 <= lx < chunk.width and 0 <= lz < chunk.depth):
            return 0
        height = chunk.height
        y_min = max(0, int(y_min))
        y_max = height - 1 if y_max is None else min(height - 1, int(y_max))
        if y_min > y_max:
            return int(chunk.heightmap[lz * chunk.width + lx])

        base = chunk._local_index(lx, 0, lz)
        if recompute_height:
            top = 0
            for yy in range(height - 1, -1, -1):
                bid = chunk.blocks[base + yy]
                if bid and chunk._opacity_for_id(bid) > 0:
                    top = yy + 1
                    break
            chunk.heightmap[lz * chunk.width + lx] = top & 0xFF
        else:
            top = int(chunk.heightmap[lz * chunk.width + lx])

        section_h = chunk.section_height

        def fill_range(start_y, end_y, value):
            if start_y >= end_y:
                return
            for section_idx in range(start_y // section_h,
                                     (end_y - 1) // section_h + 1):
                a = max(start_y, section_idx * section_h)
                b = min(end_y, (section_idx + 1) * section_h)
                lo, hi = base + a, base + b
                replacement = bytes((value,)) * (b - a)
                if chunk.skylight[lo:hi] != replacement:
                    chunk.skylight[lo:hi] = replacement
                    if changed_sections is not None:
                        changed_sections.add((cx, cz, section_idx))
            if mark_dirty and changed_sections is None:
                chunk.mark_dirty_at(start_y)

        fill_range(max(y_min, top), min(y_max + 1, height), 15)

        light = 15
        yy = top - 1
        while yy >= y_min:
            bid = chunk.blocks[base + yy]
            opacity = chunk._opacity_for_id(bid)
            if opacity == 0:
                opacity = 1
            light = max(0, light - opacity)
            if yy <= y_max:
                fill_range(yy, yy + 1, light)
            if light == 0:
                fill_range(y_min, min(yy, y_max + 1), 0)
                break
            yy -= 1
        if mark_dirty and changed_sections is None:
            chunk.mark_dirty_at(max(0, top - 1))
        return top

    def _column_vertical_sky(self, wx, wy, wz):
        if self.is_hell_world:
            return 0
        if wy < 0:
            return 0
        if wy >= self.CHUNK_HEIGHT:
            return 15
        cx, cz = self._get_chunk_index(wx, wz)
        chunk = self.chunks.get((cx, cz))
        if chunk is None:
            return 15
        lx = wx - chunk.start_x
        lz = wz - chunk.start_z
        if not (0 <= lx < chunk.width and 0 <= lz < chunk.depth):
            return 15
        top = int(chunk.heightmap[lz * chunk.width + lx])
        if wy >= top:
            return 15
        light = 15
        base = chunk._local_index(lx, 0, lz)
        for yy in range(top - 1, wy - 1, -1):
            bid = chunk.blocks[base + yy]
            opacity = chunk._opacity_for_id(bid) if bid else 0
            if opacity == 0:
                opacity = 1
            light = max(0, light - opacity)
            if light == 0:
                return 0
        return light

    def _relight_sky_region(self, x, z, y=None, radius=14, do_decrease=True,
                            skip_column_rebuild=False, changed_sections=None):
        for _ in self._relight_sky_region_steps(
            x, z, y=y, radius=radius, do_decrease=do_decrease,
            skip_column_rebuild=skip_column_rebuild, changed_sections=changed_sections,
        ):
            pass

    def _relight_sky_region_steps(self, x, z, y=None, radius=14, do_decrease=True,
                                  skip_column_rebuild=False, changed_sections=None):
        if y is None:
            y = self.CHUNK_HEIGHT // 2
        y = int(y)
        if radius < 1:
            if changed_sections:
                for cx, cz, section_idx in changed_sections:
                    chunk = self.chunks.get((cx, cz))
                    if chunk is not None:
                        chunk.mark_dirty_at(section_idx * chunk.section_height)
            return

        y_min = max(0, y - radius - 1)
        y_max = min(self.CHUNK_HEIGHT - 1, y + radius + 1)
        if changed_sections is None:
            changed_sections = set()
        directions = ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                      (0, -1, 0), (0, 0, 1), (0, 0, -1))
        r2 = radius * radius

        def in_horizontal_radius(wx, wz):
            return (wx - x) * (wx - x) + (wz - z) * (wz - z) <= r2

        def mark_section(wx, wy, wz):
            n_cx, n_cz = self._get_chunk_index(wx, wz)
            neighbor = self.chunks.get((n_cx, n_cz))
            if neighbor is not None:
                changed_sections.add((n_cx, n_cz, wy // neighbor.section_height))

        old_column = []
        cx, cz = self._get_chunk_index(x, z)
        edited_chunk = self.chunks.get((cx, cz))
        if edited_chunk is not None:
            for wy in range(self.CHUNK_HEIGHT):
                old_column.append(self._get_saved_light("sky", x, wy, z))

        if not skip_column_rebuild:
            self._relight_column_sky(
                x, z, mark_dirty=False, changed_sections=changed_sections,
                y_min=0, y_max=self.CHUNK_HEIGHT - 1, recompute_height=True,
            )

        vertical_column_cache = {}

        def cached_column_vertical_sky(wx, wy, wz):
            if wy < 0:
                return 0
            if wy >= self.CHUNK_HEIGHT:
                return 15
            n_cx, n_cz = self._get_chunk_index(wx, wz)
            neighbor = self.chunks.get((n_cx, n_cz))
            if neighbor is None:
                return 15
            nlx, nlz = wx - neighbor.start_x, wz - neighbor.start_z
            column_key = (n_cx, n_cz, nlx, nlz)
            levels = vertical_column_cache.get(column_key)
            if levels is None:
                top = int(neighbor.heightmap[nlz * neighbor.width + nlx])
                levels = bytearray([15]) * self.CHUNK_HEIGHT
                light = 15
                base = neighbor._local_index(nlx, 0, nlz)
                for yy in range(top - 1, -1, -1):
                    bid = neighbor.blocks[base + yy]
                    opacity = neighbor._opacity_for_id(bid) if bid else 0
                    if opacity == 0:
                        opacity = 1
                    light = max(0, light - opacity)
                    levels[yy] = light
                    if light == 0:
                        if yy > 0:
                            levels[:yy] = bytes(yy)
                        break
                vertical_column_cache[column_key] = levels
            return int(levels[wy])

        if do_decrease:
            profile_count = 0
            for wx in range(x - radius, x + radius + 1):
                for wz in range(z - radius, z + radius + 1):
                    if in_horizontal_radius(wx, wz):
                        cached_column_vertical_sky(wx, 0, wz)
                        profile_count += 1
                        if profile_count % 4 == 0:
                            yield

        decreased = deque()
        increase_seeds = []

        if edited_chunk is not None:
            if skip_column_rebuild:
                y0 = max(0, y - radius)
                y1 = min(self.CHUNK_HEIGHT - 1, y + radius)
                for wy in range(y0, y1 + 1):
                    new_val = self._get_saved_light("sky", x, wy, z)
                    if new_val > 1:
                        increase_seeds.append((x, wy, z, new_val))
                    if do_decrease:
                        decreased.append((x, wy, z))
            else:
                for wy in range(self.CHUNK_HEIGHT):
                    new_val = self._get_saved_light("sky", x, wy, z)
                    old_val = old_column[wy]
                    if new_val != old_val:
                        mark_section(x, wy, z)
                    if do_decrease and new_val < old_val:
                        decreased.append((x, wy, z))
                    if new_val > 1:
                        increase_seeds.append((x, wy, z, new_val))

        if edited_chunk is not None and radius >= 1:
            for nwx, nwz in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
                if not in_horizontal_radius(nwx, nwz):
                    continue
                n_cx, n_cz = self._get_chunk_index(nwx, nwz)
                if (n_cx, n_cz) not in self.chunks:
                    continue
                for wy in range(self.CHUNK_HEIGHT):
                    n_val = self._get_saved_light("sky", nwx, wy, nwz)
                    if n_val > 1:
                        increase_seeds.append((nwx, wy, nwz, n_val))

        sky_work_steps = 0
        if do_decrease:
            seen_dec = set()
            decrease_pops = 0
            while decreased:
                wx, wy, wz = decreased.popleft()
                decrease_pops += 1
                if decrease_pops % 64 == 0:
                    yield
                key = (wx, wy, wz)
                if key in seen_dec:
                    continue
                seen_dec.add(key)
                cur = self._get_saved_light("sky", wx, wy, wz)
                for ddx, ddy, ddz in directions:
                    sky_work_steps += 1
                    if sky_work_steps % 128 == 0:
                        yield
                    nx, ny, nz = wx + ddx, wy + ddy, wz + ddz
                    if ny < 0 or ny >= self.CHUNK_HEIGHT:
                        continue
                    if not in_horizontal_radius(nx, nz):
                        continue
                    opacity = self._opacity_at(nx, ny, nz)
                    if opacity == 0:
                        opacity = 1
                    neighbor_val = self._get_saved_light("sky", nx, ny, nz)
                    if neighbor_val <= max(0, cur - opacity):
                        continue
                    best = cached_column_vertical_sky(nx, ny, nz)
                    for pdx, pdy, pdz in directions:
                        px, py, pz = nx + pdx, ny + pdy, nz + pdz
                        if py < 0 or py >= self.CHUNK_HEIGHT:
                            continue
                        parent_light = self._get_saved_light("sky", px, py, pz)
                        best = max(best, max(0, parent_light - opacity))
                    if best < neighbor_val:
                        self._set_saved_light("sky", nx, ny, nz, best, mark_dirty=False)
                        mark_section(nx, ny, nz)
                        decreased.append((nx, ny, nz))
                        if best > 1:
                            increase_seeds.append((nx, ny, nz, best))

        buckets = [deque() for _ in range(16)]
        queued = [set() for _ in range(16)]

        def enqueue_inc(level, pos):
            if level > 1 and pos not in queued[level]:
                queued[level].add(pos)
                buckets[level].append(pos)

        seed_steps = 0
        for wx, wy, wz, level in increase_seeds:
            enqueue_inc(level, (wx, wy, wz))
            seed_steps += 1
            if seed_steps % 128 == 0:
                yield

        for level in range(15, 1, -1):
            bucket = buckets[level]
            pending = queued[level]
            increase_pops = 0
            while bucket:
                wx, wy, wz = bucket.popleft()
                increase_pops += 1
                if increase_pops % 64 == 0:
                    yield
                pending.discard((wx, wy, wz))
                cur = self._get_saved_light("sky", wx, wy, wz)
                if cur != level:
                    continue
                for ddx, ddy, ddz in directions:
                    sky_work_steps += 1
                    if sky_work_steps % 128 == 0:
                        yield
                    nx, ny, nz = wx + ddx, wy + ddy, wz + ddz
                    if ny < 0 or ny >= self.CHUNK_HEIGHT:
                        continue
                    if not in_horizontal_radius(nx, nz):
                        continue
                    opacity = self._opacity_at(nx, ny, nz)
                    if opacity == 0:
                        opacity = 1
                    new_light = max(0, cur - opacity)
                    old_val = self._get_saved_light("sky", nx, ny, nz)
                    if new_light > old_val:
                        self._set_saved_light("sky", nx, ny, nz, new_light, mark_dirty=False)
                        mark_section(nx, ny, nz)
                        enqueue_inc(new_light, (nx, ny, nz))

        dirty_steps = 0
        for cx, cz, section_idx in changed_sections:
            chunk = self.chunks.get((cx, cz))
            if chunk is not None:
                chunk.mark_dirty_at(section_idx * chunk.section_height)
            dirty_steps += 1
            if dirty_steps % 64 == 0:
                yield


    def _recompute_block_light_region(
        self, x, y, z, radius=BLOCK_LIGHT_RELIGHT_RADIUS,
    ):
        radius = max(0, int(radius))
        y = int(y)
        if y + radius < 0 or y - radius >= self.CHUNK_HEIGHT:
            return
        if LIGHTING_NUMBA_AVAILABLE and _numba_relight_block_region is not None:
            try:
                self._recompute_block_light_region_numba(x, y, z, radius)
                return
            except Exception as exc:  
                if not getattr(self, "_numba_relight_warned", False):
                    self._numba_relight_warned = True
                    print(f"[Lighting] numba region relight failed, using Python path: {exc!r}")
        for _ in self._recompute_block_light_region_steps(x, y, z, radius=radius):
            pass

    def _recompute_block_light_region_numba(
        self, x, y, z, radius=BLOCK_LIGHT_RELIGHT_RADIUS,
    ):
        np = _lighting_np
        radius = max(0, int(radius))
        y = int(y)
        world_height = int(self.CHUNK_HEIGHT)
        y0 = max(0, y - radius)
        y1 = min(world_height - 1, y + radius)
        if y0 > y1:
            return
        Block._load_defs()
        opacity_lut, light_lut = self._get_lighting_luts()
        size = self.CHUNK_SIZE
        ny = y1 - y0 + 1
        span = 2 * radius + 3                
        x_lo = x - radius - 1
        z_lo = z - radius - 1

        blocks = np.zeros((span, span, ny), dtype=np.uint8)
        light = np.zeros((span, span, ny), dtype=np.uint8)
        loaded = np.zeros((span, span), dtype=np.uint8)

        overlaps = []
        cx_first = (x_lo + 0) // size
        cx_last = (x_lo + span - 1) // size
        cz_first = (z_lo + 0) // size
        cz_last = (z_lo + span - 1) // size
        for cx in range(cx_first, cx_last + 1):
            for cz in range(cz_first, cz_last + 1):
                chunk = self.chunks.get((cx, cz))
                if chunk is None:
                    continue
                wx0 = max(x_lo, chunk.start_x)
                wx1 = min(x_lo + span, chunk.start_x + chunk.width)
                wz0 = max(z_lo, chunk.start_z)
                wz1 = min(z_lo + span, chunk.start_z + chunk.depth)
                if wx0 >= wx1 or wz0 >= wz1:
                    continue
                i0, i1 = wx0 - x_lo, wx1 - x_lo
                k0, k1 = wz0 - z_lo, wz1 - z_lo
                lx0, lx1 = wx0 - chunk.start_x, wx1 - chunk.start_x
                lz0, lz1 = wz0 - chunk.start_z, wz1 - chunk.start_z
                shape = (chunk.width, chunk.depth, chunk.height)
                cb = np.frombuffer(chunk.blocks, dtype=np.uint8).reshape(shape)
                cl = np.frombuffer(chunk.blocklight, dtype=np.uint8).reshape(shape)
                blocks[i0:i1, k0:k1, :] = cb[lx0:lx1, lz0:lz1, y0:y1 + 1]
                light[i0:i1, k0:k1, :] = cl[lx0:lx1, lz0:lz1, y0:y1 + 1]
                loaded[i0:i1, k0:k1] = 1
                overlaps.append((chunk, cl, i0, i1, k0, k1, lx0, lx1, lz0, lz1))

        center_emits = self._light_value_at(x, y, z) > 0
        if not center_emits and not light.any():
            return

        before = light.copy()
        _numba_relight_block_region(
            blocks.reshape(-1), light.reshape(-1), loaded.reshape(-1),
            opacity_lut, light_lut, span, span, ny,
        )

        section_height = None
        for chunk, cl, i0, i1, k0, k1, lx0, lx1, lz0, lz1 in overlaps:
            new_slice = light[i0:i1, k0:k1, :]
            diff = new_slice != before[i0:i1, k0:k1, :]
            if not diff.any():
                continue
            cl[lx0:lx1, lz0:lz1, y0:y1 + 1] = new_slice
            section_height = chunk.section_height
            for j in np.nonzero(diff.any(axis=(0, 1)))[0]:
                chunk.mark_dirty_at(((y0 + int(j)) // section_height) * section_height)
        self._mark_neighbors_dirty(x, y, z)

    def _recompute_block_light_region_steps(
        self, x, y, z, radius=BLOCK_LIGHT_RELIGHT_RADIUS,
    ):
        radius = max(0, int(radius))
        y = int(y)
        if y + radius < 0 or y - radius >= self.CHUNK_HEIGHT:
            return
        has_block_light = self._light_value_at(x, y, z) > 0
        if not has_block_light:
            y0_check = max(0, y - radius - 1)
            y1_check = min(self.CHUNK_HEIGHT - 1, y + radius + 1)
            scan_steps = 0
            for wx in range(x - radius - 1, x + radius + 2):
                if has_block_light:
                    break
                for wz in range(z - radius - 1, z + radius + 2):
                    scan_steps += 1
                    if scan_steps % 32 == 0:
                        yield
                    cx, cz = self._get_chunk_index(wx, wz)
                    chunk = self.chunks.get((cx, cz))
                    if chunk is None:
                        continue
                    lx, lz = wx - chunk.start_x, wz - chunk.start_z
                    base = chunk._local_index(lx, y0_check, lz)
                    if any(chunk.blocklight[base:base + (y1_check - y0_check + 1)]):
                        has_block_light = True
                        break
        if not has_block_light:
            return
        q = deque()
        previous_values = []
        y0 = max(0, y - radius)
        y1 = min(self.CHUNK_HEIGHT - 1, y + radius)

        work_steps = 0
        for wx in range(x - radius, x + radius + 1):
            for wz in range(z - radius, z + radius + 1):
                cx, cz = self._get_chunk_index(wx, wz)
                chunk = self.chunks.get((cx, cz))
                if chunk is None:
                    continue
                lx = wx - chunk.start_x
                lz = wz - chunk.start_z
                for wy in range(y0, y1 + 1):
                    idx = chunk._local_index(lx, wy, lz)
                    previous_values.append((chunk, idx, wy, int(chunk.blocklight[idx])))
                    bid = chunk.blocks[idx]
                    emitted = chunk._light_value_for_id(bid) if bid else 0
                    chunk.blocklight[idx] = emitted
                    if emitted > 0:
                        q.append((wx, wy, wz, emitted))
                    work_steps += 1
                    if work_steps % 256 == 0:
                        yield

        for wx in range(x - radius - 1, x + radius + 2):
            for wz in range(z - radius - 1, z + radius + 2):
                if abs(wx - x) != radius + 1 and abs(wz - z) != radius + 1:
                    continue
                for wy in range(y0, y1 + 1):
                    border = self._get_saved_light("block", wx, wy, wz)
                    if border > 1:
                        q.append((wx, wy, wz, border))
                    work_steps += 1
                    if work_steps % 256 == 0:
                        yield

        steps = 0
        max_steps = max((2 * radius + 1) ** 3 * 8, len(q) * 16)
        while q and steps < max_steps:
            steps += 1
            cx_, cy, cz_, level = q.popleft()
            if steps % 128 == 0:
                yield
            if level <= 1:
                continue
            for dx, dy, dz in (
                (1, 0, 0), (-1, 0, 0), (0, 1, 0),
                (0, -1, 0), (0, 0, 1), (0, 0, -1),
            ):
                nx, ny, nz = cx_ + dx, cy + dy, cz_ + dz
                if (abs(nx - x) > radius or abs(ny - y) > radius
                        or abs(nz - z) > radius):
                    continue
                if ny < 0 or ny >= self.CHUNK_HEIGHT:
                    continue
                if self.chunks.get(self._get_chunk_index(nx, nz)) is None:
                    continue
                opacity = self._opacity_at(nx, ny, nz)
                if opacity == 0:
                    opacity = 1
                new_light = max(0, level - opacity)
                old = self._get_saved_light("block", nx, ny, nz)
                if new_light > old:
                    self._set_saved_light("block", nx, ny, nz, new_light, mark_dirty=False)
                    q.append((nx, ny, nz, new_light))
                work_steps += 1
                if work_steps % 256 == 0:
                    yield

        changed_sections = set()
        for chunk, idx, wy, old_value in previous_values:
            work_steps += 1
            if work_steps % 512 == 0:
                yield
            if int(chunk.blocklight[idx]) != old_value:
                changed_sections.add((chunk.cx, chunk.cz, wy // chunk.section_height))
        for cx, cz, section_idx in changed_sections:
            chunk = self.chunks.get((cx, cz))
            if chunk is not None:
                chunk.mark_dirty_at(section_idx * chunk.section_height)
        self._mark_neighbors_dirty(x, y, z)

    def _enqueue_light(self, kind, x, y, z):
        if kind not in ("block", "block_increase"):
            return False
        if len(self._light_queue) >= self._max_light_queue_size:
            if kind != "block_increase" or len(self._light_queue) >= 65536:
                if not self._light_queue_overflow_logged:
                    self.profiler.record_event(
                        "lighting.queue_overflow", kind=kind, x=int(x), y=int(y),
                        z=int(z), pending=len(self._light_queue),
                    )
                    self._light_queue_overflow_logged = True
                return False
        key = (kind, int(x), int(y), int(z))
        if key not in self._light_queue_set:
            self._light_queue_set.add(key)
            self._light_queue.append(key)
            return True
        return False

    def _propagate_block_light_increase(self, x, y, z):
        if not (0 <= y < self.CHUNK_HEIGHT):
            return
        if self.chunks.get(self._get_chunk_index(x, z)) is None:
            return
        emitted = self._light_value_at(x, y, z)
        current = self._get_saved_light("block", x, y, z)
        if emitted > current:
            self._set_saved_light("block", x, y, z, emitted, mark_dirty=True)
            current = emitted
        if current <= 1:
            return
        for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                           (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            nx, ny, nz = x + dx, y + dy, z + dz
            if not (0 <= ny < self.CHUNK_HEIGHT):
                continue
            if self.chunks.get(self._get_chunk_index(nx, nz)) is None:
                continue
            opacity = self._opacity_at(nx, ny, nz)
            if opacity <= 0:
                opacity = 1
            new_light = max(0, current - opacity)
            old_light = self._get_saved_light("block", nx, ny, nz)
            if new_light > old_light:
                self._set_saved_light("block", nx, ny, nz, new_light, mark_dirty=True)
                self._enqueue_light("block_increase", nx, ny, nz)

    def _process_light_queue(self, full=False):
        started = time.perf_counter()
        budget = self._max_light_updates_per_frame if not full else self._max_light_queue_size
        processed = 0
        increase_nodes = 0
        full_rebuilds = 0
        while self._light_queue and processed < budget:
            kind, x, y, z = self._light_queue.popleft()
            self._light_queue_set.discard((kind, x, y, z))
            if kind == "block":
                self._recompute_block_light_region(
                    x, y, z, radius=BLOCK_LIGHT_RELIGHT_RADIUS,
                )
                full_rebuilds += 1
            elif kind == "block_increase":
                self._propagate_block_light_increase(x, y, z)
                increase_nodes += 1
            processed += 1
        if len(self._light_queue) < self._max_light_queue_size // 2:
            self._light_queue_overflow_logged = False
        self.profiler.record_ms(
            "lighting.queue", (time.perf_counter() - started) * 1000.0,
            event_details={
                "processed": processed, "increase_nodes": increase_nodes,
                "full_rebuilds": full_rebuilds, "pending": len(self._light_queue),
            },
        )

    def _get_saved_light(self, kind, x, y, z):
        if y < 0:
            return 0
        if y >= self.CHUNK_HEIGHT:
            return 0 if self.is_hell_world or kind != "sky" else 15
        cx, cz = self._get_chunk_index(x, z)
        chunk = self.chunks.get((cx, cz))
        if chunk is None:
            return 0
        if kind == "sky":
            return chunk.get_skylight(x, y, z)
        return chunk.get_blocklight(x, y, z)

    def _set_saved_light(self, kind, x, y, z, level, mark_dirty=True):
        if y < 0 or y >= self.CHUNK_HEIGHT:
            return
        cx, cz = self._get_chunk_index(x, z)
        chunk = self.chunks.get((cx, cz))
        if chunk is None:
            return
        lx = x - chunk.start_x
        lz = z - chunk.start_z
        if kind == "sky":
            chunk.set_skylight_local(lx, y, lz, level)
        else:
            chunk.set_blocklight_local(lx, y, lz, level)
        if mark_dirty:
            chunk.mark_dirty_at(y)

    def _opacity_at(self, x, y, z):
        bt = self.get_block_type(x, y, z)
        if bt is None:
            return 0
        return int(Block._block_defs.get(bt, {}).get("lightOpacity", 255))

    def _light_value_at(self, x, y, z):
        bt = self.get_block_type(x, y, z)
        if bt is None:
            return 0
        return int(Block._block_defs.get(bt, {}).get("lightValue", 0))

    _LIGHT_NEIGHBOR_MAX_BLOCKS = frozenset({
        "stone_slab", "farmland", "stairs", "cobblestone_stairs",
    })

    def get_light(self, x, y, z, *, use_neighbor_max=True):
        if y < 0:
            return 0
        if y >= self.CHUNK_HEIGHT:
            return 0 if self.is_hell_world else max(0, 15 - self.lighting.skylight_subtracted)

        x, y, z = int(x), int(y), int(z)
        if use_neighbor_max:
            block_type = self.get_block_type(x, y, z)
            if block_type in self._LIGHT_NEIGHBOR_MAX_BLOCKS:
                best = self.get_light(x, y + 1, z, use_neighbor_max=False)
                for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    neighbor = self.get_light(x + dx, y, z + dz, use_neighbor_max=False)
                    if neighbor > best:
                        best = neighbor
                return best

        cx, cz = self._get_chunk_index(x, z)
        chunk = self.chunks.get((cx, cz))
        if chunk is None:
            return 0 if self.is_hell_world else 15
        return chunk.get_combined_light(x, y, z)

    def remove_block(self, x, y, z, immediate_mesh=False):
        self.set_block(x, y, z, None, immediate_mesh=immediate_mesh)

    @contextlib.contextmanager
    def bulk_edit(self):
        self._bulk_edit_depth = getattr(self, "_bulk_edit_depth", 0) + 1
        try:
            yield self
        finally:
            self._bulk_edit_depth -= 1

    def _relight_sky_regions_for_explosion_numba(self, centers, allowed_columns,
                                                  changed_sections):
        if _numba_relight_sky_region is None or _lighting_np is None:
            return None
        try:
            min_x = min(x for x, _z in allowed_columns)
            max_x = max(x for x, _z in allowed_columns)
            min_z = min(z for _x, z in allowed_columns)
            max_z = max(z for _x, z in allowed_columns)
            span_x = max_x - min_x + 1
            span_z = max_z - min_z + 1
            height = self.CHUNK_HEIGHT
            np = _lighting_np

            blocks = np.zeros((span_x, span_z, height), dtype=np.uint8)
            light = np.zeros((span_x, span_z, height), dtype=np.uint8)
            loaded = np.zeros((span_x, span_z), dtype=np.uint8)
            allowed = np.zeros((span_x, span_z), dtype=np.uint8)
            seed_mask = np.zeros((span_x, span_z, height), dtype=np.uint8)

            for wx, wz in allowed_columns:
                allowed[wx - min_x, wz - min_z] = 1

            overlaps = []
            for chunk in self.chunks.values():
                block_buffer = getattr(chunk, "blocks", None)
                if block_buffer is None:
                    continue
                wx0 = max(min_x, chunk.start_x)
                wx1 = min(max_x + 1, chunk.start_x + chunk.width)
                wz0 = max(min_z, chunk.start_z)
                wz1 = min(max_z + 1, chunk.start_z + chunk.depth)
                if wx0 >= wx1 or wz0 >= wz1:
                    continue

                i0, i1 = wx0 - min_x, wx1 - min_x
                k0, k1 = wz0 - min_z, wz1 - min_z
                lx0, lx1 = wx0 - chunk.start_x, wx1 - chunk.start_x
                lz0, lz1 = wz0 - chunk.start_z, wz1 - chunk.start_z
                copy_height = min(height, chunk.height)
                block_view = np.frombuffer(block_buffer, dtype=np.uint8).reshape(
                    (chunk.width, chunk.depth, chunk.height),
                )
                sky_buffer = np.frombuffer(chunk.skylight, dtype=np.uint8).reshape(
                    (chunk.width, chunk.depth, chunk.height),
                )
                blocks[i0:i1, k0:k1, :copy_height] = block_view[
                    lx0:lx1, lz0:lz1, :copy_height,
                ]
                light[i0:i1, k0:k1, :copy_height] = sky_buffer[
                    lx0:lx1, lz0:lz1, :copy_height,
                ]
                loaded[i0:i1, k0:k1] = 1
                overlaps.append((
                    chunk, sky_buffer, i0, i1, k0, k1,
                    lx0, lx1, lz0, lz1, copy_height,
                ))

            for x, z, min_y, _max_y in centers:
                ix, iz = x - min_x, z - min_z
                if not (0 <= ix < span_x and 0 <= iz < span_z):
                    continue
                if loaded[ix, iz] == 0:
                    continue
                y0 = max(0, min_y - 12)
                y1 = min(height - 1, min_y + 12)
                seed_mask[ix, iz, y0:y1 + 1] = 1
                for sx, sz in ((x + 1, z), (x - 1, z),
                               (x, z + 1), (x, z - 1)):
                    si, sj = sx - min_x, sz - min_z
                    if (0 <= si < span_x and 0 <= sj < span_z
                            and loaded[si, sj] != 0):
                        seed_mask[si, sj, :] = 1

            opacity_lut, _light_lut = self._get_lighting_luts()
            if opacity_lut is None:
                return None
            _numba_relight_sky_region(
                blocks.reshape(-1), light.reshape(-1), loaded.reshape(-1),
                allowed.reshape(-1), seed_mask.reshape(-1), opacity_lut,
                span_x, span_z, height,
            )

            for (chunk, sky_buffer, i0, i1, k0, k1,
                 lx0, lx1, lz0, lz1, copy_height) in overlaps:
                new_slice = light[i0:i1, k0:k1, :copy_height]
                old_slice = sky_buffer[lx0:lx1, lz0:lz1, :copy_height]
                diff = new_slice != old_slice
                if not diff.any():
                    continue
                old_slice[...] = new_slice
                for section_y in np.nonzero(diff.any(axis=(0, 1)))[0]:
                    changed_sections.add((
                        chunk.cx, chunk.cz,
                        int(section_y) // chunk.section_height,
                    ))
            return len(allowed_columns)
        except Exception as exc:
            if not getattr(self, "_numba_sky_relight_warned", False):
                self._numba_sky_relight_warned = True
                print(f"[Lighting] Numba explosion skylight relight failed, "
                      f"using Python path: {exc!r}")
            return None

    def _relight_sky_regions_for_explosion(self, by_column, radius=12,
                                           changed_sections=None):
        if not by_column:
            return 0
        radius = max(1, int(radius))
        if changed_sections is None:
            changed_sections = set()

        centers = []
        for (x, z), y_range in by_column.items():
            if isinstance(y_range, (tuple, list)):
                y_min, y_max = int(y_range[0]), int(y_range[1])
            else:
                y_min = y_max = int(y_range)
            centers.append((int(x), int(z), y_min, y_max))
        radius_sq = radius * radius
        allowed_columns = set()
        for x, z, _y_min, _y_max in centers:
            for dx in range(-radius, radius + 1):
                remaining = radius_sq - dx * dx
                max_dz = math.isqrt(remaining)
                for dz in range(-max_dz, max_dz + 1):
                    allowed_columns.add((x + dx, z + dz))

        if (LIGHTING_NUMBA_AVAILABLE and _numba_relight_sky_region is not None
                and getattr(self, "_lighting_lut_cache", None) is not None):
            numba_result = self._relight_sky_regions_for_explosion_numba(
                centers, allowed_columns, changed_sections,
            )
            if numba_result is not None:
                return numba_result

        directions = ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                      (0, -1, 0), (0, 0, 1), (0, 0, -1))
        buckets = [deque() for _ in range(16)]
        pending = [set() for _ in range(16)]

        def enqueue(level, pos):
            level = int(level)
            if 1 < level < 16 and pos not in pending[level]:
                pending[level].add(pos)
                buckets[level].append(pos)

        for x, z, min_y, _max_y in centers:
            cx, cz = self._get_chunk_index(x, z)
            if (cx, cz) not in self.chunks:
                continue
            y0 = max(0, min_y - radius)
            y1 = min(self.CHUNK_HEIGHT - 1, min_y + radius)
            for wy in range(y0, y1 + 1):
                level = self._get_saved_light("sky", x, wy, z)
                enqueue(level, (x, wy, z))

            for nx, nz in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
                ncx, ncz = self._get_chunk_index(nx, nz)
                if (ncx, ncz) not in self.chunks:
                    continue
                for wy in range(self.CHUNK_HEIGHT):
                    level = self._get_saved_light("sky", nx, wy, nz)
                    enqueue(level, (nx, wy, nz))

        sky_work_steps = 0
        for level in range(15, 1, -1):
            bucket = buckets[level]
            level_pending = pending[level]
            while bucket:
                wx, wy, wz = bucket.popleft()
                level_pending.discard((wx, wy, wz))
                if self._get_saved_light("sky", wx, wy, wz) != level:
                    continue
                for ddx, ddy, ddz in directions:
                    sky_work_steps += 1
                    nx, ny, nz = wx + ddx, wy + ddy, wz + ddz
                    if ny < 0 or ny >= self.CHUNK_HEIGHT:
                        continue
                    if (nx, nz) not in allowed_columns:
                        continue
                    opacity = self._opacity_at(nx, ny, nz)
                    if opacity == 0:
                        opacity = 1
                    new_light = max(0, level - opacity)
                    old_light = self._get_saved_light("sky", nx, ny, nz)
                    if new_light > old_light:
                        self._set_saved_light(
                            "sky", nx, ny, nz, new_light, mark_dirty=False,
                        )
                        ncx, ncz = self._get_chunk_index(nx, nz)
                        neighbor = self.chunks.get((ncx, ncz))
                        if neighbor is not None:
                            changed_sections.add((
                                ncx, ncz, ny // neighbor.section_height,
                            ))
                        enqueue(new_light, (nx, ny, nz))

        return len(allowed_columns)

    def relight_after_explosion(self, cells):
        if isinstance(cells, dict):
            sky_cells = tuple(cells.get("sky", ()))
            block_cells = tuple(cells.get("block", ()))
        else:
            sky_cells = block_cells = tuple(cells)
        if not block_cells and not sky_cells:
            return {"sky_columns": 0, "sky_region_columns": 0,
                    "blocklight_radius": 0}

        by_column = {}
        for cell in sky_cells:
            x, y, z = int(cell[0]), int(cell[1]), int(cell[2])
            key = (x, z)
            previous = by_column.get(key)
            if previous is None:
                by_column[key] = [y, y]
            else:
                previous[0] = min(previous[0], y)
                previous[1] = max(previous[1], y)

        changed_sections = set()
        for cell in block_cells:
            self._pending_natural_light_changes.pop(
                (int(cell[0]), int(cell[1]), int(cell[2])), None,
            )

        for (x, z) in by_column:
            cx, cz = self._get_chunk_index(x, z)
            if (cx, cz) not in self.chunks:
                continue
            self._relight_column_sky(
                x, z, mark_dirty=False, changed_sections=changed_sections,
                y_min=0, y_max=self.CHUNK_HEIGHT - 1, recompute_height=True,
            )

        sky_region_columns = self._relight_sky_regions_for_explosion(
            by_column, radius=12, changed_sections=changed_sections,
        ) if by_column else 0

        blocklight_radius = 0
        if block_cells:
            min_x = min(int(cell[0]) for cell in block_cells)
            max_x = max(int(cell[0]) for cell in block_cells)
            min_y = min(int(cell[1]) for cell in block_cells)
            max_y = max(int(cell[1]) for cell in block_cells)
            min_z = min(int(cell[2]) for cell in block_cells)
            max_z = max(int(cell[2]) for cell in block_cells)
            cx = (min_x + max_x) // 2
            cy = (min_y + max_y) // 2
            cz = (min_z + max_z) // 2
            blocklight_radius = min(
                14,
                max(12, max(max_x - min_x, max_y - min_y,
                            max_z - min_z) // 2 + 4),
            )
            self._recompute_block_light_region(
                cx, cy, cz, radius=blocklight_radius,
            )

        for scx, scz, section_idx in changed_sections:
            chunk = self.chunks.get((scx, scz))
            if chunk is not None:
                chunk.mark_dirty_at(section_idx * chunk.section_height)

        return {
            "sky_columns": len(by_column),
            "sky_region_columns": sky_region_columns,
            "blocklight_radius": blocklight_radius,
        }

    def get_tile_entity(self, x, y, z):
        return self.tile_entities.get((int(x), int(y), int(z)))

    def set_tile_entity(self, x, y, z, te):
        key = (int(x), int(y), int(z))
        if te is None:
            self.tile_entities.pop(key, None)
        else:
            self.tile_entities[key] = te

    def set_generated_chest_loot(self, x, y, z, contents):
        x, y, z = int(x), int(y), int(z)
        if self.get_block_type(x, y, z) != "chest":
            return False
        from game.block.tile_entity import TileEntityChest
        from game.entities.item import ItemStack

        position = (x, y, z)
        te = self.tile_entities.get(position)
        if not isinstance(te, TileEntityChest):
            te = TileEntityChest(self, x, y, z)
            self.tile_entities[position] = te
        for slot, entry in contents.items():
            slot = int(slot)
            item_id = str(entry.get("id", ""))
            if not (0 <= slot < te.SIZE) or not item_id:
                continue
            count = max(1, min(64, int(entry.get("count", 1))))
            damage = max(0, int(entry.get("damage", 0)))
            te.set_stack(slot, ItemStack(item_id, count, damage))
        return True

    def set_generated_spawner_mob(self, x, y, z, mob_id):
        x, y, z = int(x), int(y), int(z)
        if self.get_block_type(x, y, z) != "mob_spawner":
            return False
        from game.block.tile_entity import TileEntityMobSpawner

        position = (x, y, z)
        te = self.tile_entities.get(position)
        if not isinstance(te, TileEntityMobSpawner):
            te = TileEntityMobSpawner(self, x, y, z)
            self.tile_entities[position] = te
        te.mob_id = str(mob_id)
        return True

    def spawn_item_stack(self, x, y, z, stack):
        if stack is None or stack.count <= 0:
            return None
        from game.entities.item import ItemEntity
        import random
        pos = (float(x) + 0.5, float(y) + 0.5, float(z) + 0.5)
        vel = (
            (random.random() - 0.5) * 0.1,
            0.2,
            (random.random() - 0.5) * 0.1,
        )
        entity = ItemEntity(stack.copy() if hasattr(stack, "copy") else stack, pos, velocity=vel, pickup_delay=10)
        self.spawn_entity(entity)
        return entity

    def set_world_time(self, new_time):
        old_time = int(getattr(self, "world_time", 0))
        new_time = max(0, int(new_time))
        delta = new_time - old_time
        if delta:
            for scheduler in (
                getattr(self, "_block_updates", None),
                getattr(self, "_fluid_scheduler", None),
                getattr(self, "_redstone_system", None),
            ):
                shift = getattr(scheduler, "shift_world_time", None)
                if callable(shift):
                    shift(delta)

            for tile_entity in getattr(self, "tile_entities", {}).values():
                shift_te = getattr(tile_entity, "shift_world_time", None)
                if callable(shift_te):
                    shift_te(delta)
                scheduled = getattr(tile_entity, "_scheduled_tick", None)
                if scheduled is not None:
                    tile_entity._scheduled_tick = int(scheduled) + delta

        self.world_time = new_time
        updater = getattr(self, "_update_skylight_subtracted", None)
        if callable(updater):
            updater()
        if callable(getattr(self, "is_daytime", None)) and self.is_daytime():
            wake = getattr(self, "_wake_all_sleeping_players", None)
            if callable(wake):
                wake()
        return new_time

    def schedule_block_update(self, x, y, z, block_type, delay=None):
        from game.block.updates import TICK_RATES
        if delay is None:
            delay = TICK_RATES.get(block_type, 10)
        return self._block_updates.schedule(x, y, z, block_type, delay)

    def _restore_saved_block_ticks(self, ticks):
        if bool(getattr(self, "_multiplayer_client_world", False)):
            return 0
        restored = 0
        for x, y, z, block_type, delay in ticks:
            if self.get_block_type(x, y, z) != block_type:
                continue
            if self._block_updates.schedule(x, y, z, block_type, delay):
                restored += 1
        return restored

    def _run_block_updates(self):
        if bool(getattr(self, "_multiplayer_client_world", False)):
            return 0
        from game.entities.falling_block import TICK_HANDLERS
        return self._block_updates.process_due(TICK_HANDLERS)

    def drop_block_as_item(self, block_type, meta, x, y, z, rng=None):
        from game.block.drops import block_drops, scatter_position
        from game.entities.item import ItemEntity
        rng = random if rng is None else rng
        for stack in block_drops(block_type, meta, rng=rng):
            self.spawn_entity(ItemEntity(
                stack, scatter_position((x, y, z), rng=rng), pickup_delay=10,
            ))

    def update_tile_entities(self):
        if bool(getattr(self, "_multiplayer_client_world", False)):
            return
        for key, te in list(self.tile_entities.items()):
            if te is None:
                continue
            if self._get_chunk_index(te.x, te.z) not in self.chunks:
                continue
            try:
                te.update()
            except Exception as exc:
                print(f"Tile entity update failed at {key}: {exc!r}")

    @staticmethod
    def _entity_spatial_cell(pos):
        size = _ENTITY_SPATIAL_CELL_SIZE
        return (
            math.floor(float(pos[0]) / size),
            math.floor(float(pos[1]) / size),
            math.floor(float(pos[2]) / size),
        )

    def _rebuild_entity_spatial_index(self):
        if len(self.entities) < _ENTITY_SPATIAL_MIN_COUNT:
            self._entity_spatial_index = None
            self._entity_spatial_positions = None
            self._entity_spatial_columns = None
            return False

        buckets = {}
        positions = {}
        cell_for = self._entity_spatial_cell
        for entity in self.entities:
            if getattr(entity, "dead", False):
                continue
            try:
                key = cell_for(entity.pos)
            except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
                continue
            buckets.setdefault(key, []).append(entity)
            positions[id(entity)] = key

        self._entity_spatial_index = buckets
        self._entity_spatial_positions = positions
        self._entity_spatial_columns = None
        return True

    def _ensure_entity_spatial_columns(self):
        columns = getattr(self, "_entity_spatial_columns", None)
        if columns is not None:
            return columns
        buckets = getattr(self, "_entity_spatial_index", None)
        if not isinstance(buckets, dict):
            return None
        columns = {}
        for key, entities in buckets.items():
            try:
                cell_x, cell_y, cell_z = key
            except (TypeError, ValueError):
                continue
            columns.setdefault((cell_x, cell_z), {})[cell_y] = entities
        self._entity_spatial_columns = columns
        return columns

    def _sync_entity_spatial_index(self, entity):
        buckets = getattr(self, "_entity_spatial_index", None)
        positions = getattr(self, "_entity_spatial_positions", None)
        columns = getattr(self, "_entity_spatial_columns", None)
        if buckets is None or positions is None:
            return

        entity_id = id(entity)
        old_key = positions.get(entity_id)
        if getattr(entity, "dead", False):
            new_key = None
        else:
            try:
                new_key = self._entity_spatial_cell(entity.pos)
            except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
                new_key = None

        if old_key == new_key:
            return

        old_column = (old_key[0], old_key[2]) if old_key is not None else None
        new_column = (new_key[0], new_key[2]) if new_key is not None else None

        if old_key is not None:
            old_bucket = buckets.get(old_key)
            if old_bucket is not None:
                for index, candidate in enumerate(old_bucket):
                    if candidate is entity:
                        del old_bucket[index]
                        break
                if not old_bucket:
                    buckets.pop(old_key, None)
                    if columns is not None:
                        old_column_buckets = columns.get(old_column)
                        if old_column_buckets is not None:
                            old_column_buckets.pop(old_key[1], None)
                            if not old_column_buckets:
                                columns.pop(old_column, None)
            positions.pop(entity_id, None)

        if new_key is not None:
            new_bucket = buckets.setdefault(new_key, [])
            new_bucket.append(entity)
            positions[entity_id] = new_key
            if columns is not None:
                columns.setdefault(new_column, {})[new_key[1]] = new_bucket

    def iter_entities_in_bounds(
            self, min_x, min_y, min_z, max_x, max_y, max_z):
        buckets = getattr(self, "_entity_spatial_index", None)
        if buckets is None:
            yield from self.entities
            return

        size = _ENTITY_SPATIAL_CELL_SIZE
        x0, x1 = math.floor(min_x / size), math.floor(max_x / size)
        y0, y1 = math.floor(min_y / size), math.floor(max_y / size)
        z0, z1 = math.floor(min_z / size), math.floor(max_z / size)
        for cell_x in range(x0, x1 + 1):
            for cell_y in range(y0, y1 + 1):
                for cell_z in range(z0, z1 + 1):
                    for entity in buckets.get((cell_x, cell_y, cell_z), ()):
                        if getattr(entity, "dead", False):
                            continue
                        pos = entity.pos
                        if (min_x <= pos[0] <= max_x
                                and min_y <= pos[1] <= max_y
                                and min_z <= pos[2] <= max_z):
                            yield entity

    def spawn_entity(self, entity):
        callback = getattr(self, "_network_entity_spawn_callback", None)
        is_remote = getattr(entity, "_mp_remote_entity", False)
        if callable(callback) and not is_remote:
            entity_type = type(entity).__name__
            if entity_type == "ItemEntity":
                try:
                    callback(entity)
                except Exception:
                    pass
            elif getattr(self, "_multiplayer_client_world", False) and entity_type in {
                "PrimedTNT", "ArrowEntity", "SnowballEntity", "EggEntity", "PaintingEntity",
                "BoatEntity", "MinecartEntity",
            }:
                try:
                    callback(entity)
                except Exception:
                    pass
                return entity
        self.entities.append(entity)
        self._sync_entity_spatial_index(entity)
        return entity

    def _entity_chunk_loaded(self, entity):
        return self._get_chunk_index(entity.pos[0], entity.pos[2]) in self.chunks

    def _damage_entity_touching_cactus(self, entity, dt=0.0):
        if not hasattr(entity, "health"):
            return
        cooldown = max(0.0, float(getattr(entity, "_cactus_contact_cooldown", 0.0)) - max(0.0, float(dt)))
        entity._cactus_contact_cooldown = cooldown
        if cooldown > 0.0:
            return
        attack = getattr(entity, "attack_entity_from", None)
        get_box = getattr(entity, "get_bounding_box", None)
        positions_for_aabb = getattr(entity, "_block_positions_for_aabb", None)
        if not callable(attack) or not callable(get_box) or not callable(positions_for_aabb):
            return
        try:
            left, bottom, front, right, top, back = get_box()
            positions = positions_for_aabb(*entity.pos)
        except Exception:
            return
        for bx, by, bz in positions:
            if self.get_block_type(bx, by, bz) != "cactus":
                continue
            if (left < bx + 0.5 and right > bx - 0.5
                    and bottom < by + 0.5 and top > by - 0.5
                    and front < bz + 0.5 and back > bz - 0.5):
                try:
                    attack(None, 1)
                    entity._cactus_contact_cooldown = 0.5
                except TypeError:
                    try:
                        attack(None, amount=1)
                        entity._cactus_contact_cooldown = 0.5
                    except Exception:
                        pass
                except Exception:
                    pass
                return

    def update_entities(self, dt):
        self._vehicle_cache = None  
        self._rebuild_entity_spatial_index()
        client_world = bool(getattr(self, "_multiplayer_client_world", False))
        from game.entities.mob import Mob
        if client_world:
            from game.entities.item import ItemEntity
        player = getattr(self, "player", None)
        try:
            px = float(player.pos[0]) if player is not None else 0.0
            pz = float(player.pos[2]) if player is not None else 0.0
        except Exception:
            px = pz = 0.0
        near_sq = 48.0 * 48.0
        mid_sq = 80.0 * 80.0
        for entity in self.entities:
            if entity.dead:
                self._sync_entity_spatial_index(entity)
                continue
            if client_world and not getattr(entity, "_mp_remote_entity", False):
                if isinstance(entity, (Mob, ItemEntity)):
                    entity.dead = True
                    self._sync_entity_spatial_index(entity)
                    continue
            if getattr(entity, "_mp_remote_entity", False):
                continue
            if entity.pos[1] < -64.0:
                entity.dead = True
                self._sync_entity_spatial_index(entity)
                continue
            if self._entity_chunk_loaded(entity):
                skip_full = False
                if (
                    player is not None
                    and entity is not player
                    and isinstance(entity, Mob)
                ):
                    dx = float(entity.pos[0]) - px
                    dz = float(entity.pos[2]) - pz
                    dist_sq = dx * dx + dz * dz
                    tick_counter = int(getattr(entity, "_sim_lod_tick", 0)) + 1
                    entity._sim_lod_tick = tick_counter
                    if dist_sq > mid_sq:
                        if tick_counter & 3:
                            skip_full = True
                    elif dist_sq > near_sq:
                        if tick_counter & 1:
                            skip_full = True
                if not skip_full:
                    entity.update(dt)
                    if entity is not player and not entity.dead:
                        self._damage_entity_touching_cactus(entity, dt)
                    self._sync_entity_spatial_index(entity)
        if any(entity.dead for entity in self.entities):
            self.entities = [entity for entity in self.entities if not entity.dead]
        if getattr(self, "effects", None) is not None:
            self.effects.update(dt, world=self)

    def draw_entities(self):
        max_dist = (self.view_distance + 1) * self.CHUNK_SIZE
        renderer = self.renderer
        player = getattr(self, "player", None)
        camera_pos = getattr(player, "pos", (0.0, 64.0, 0.0))
        if renderer is not None:
            cx = float(getattr(renderer, "camX", camera_pos[0]))
            cy = float(getattr(renderer, "camY", camera_pos[1]))
            cz = float(getattr(renderer, "camZ", camera_pos[2]))
        else:
            cx = float(camera_pos[0])
            cy = float(camera_pos[1])
            cz = float(camera_pos[2])
        frustum = None
        if renderer is not None:
            try:
                frustum = self._camera_frustum()
            except Exception:
                frustum = None
        max_dist_sq = max_dist * max_dist
        from game.entities.entity import Entity
        Entity._entity_draw_lighting_batched = True
        Entity._entity_shadow_camera = (cx, cy, cz)
        try:
            try:
                from engine.legacy_lighting import (
                    enable_standard_item_lighting,
                    disable_standard_item_lighting,
                )
                enable_standard_item_lighting()
                lighting_on = True
            except Exception:
                lighting_on = False
            for entity in self.entities:
                if entity.dead or not self._entity_chunk_loaded(entity):
                    continue
                ex, ey, ez = (float(v) for v in entity.pos)
                ecx, ecz = self._get_chunk_index(ex, ez)
                if not self._chunk_in_active_view(ecx, ecz):
                    continue
                dx, dz = ex - cx, ez - cz
                if dx * dx + dz * dz > max_dist_sq:
                    continue
                if frustum is not None:
                    size = getattr(entity, "size", (0.6, 1.8, 0.6))
                    try:
                        half_x = max(0.05, float(size[0]) * 0.5)
                        height = max(0.1, float(size[1]))
                        half_z = max(0.05, float(size[2]) * 0.5)
                        visible = frustum.intersects_aabb(
                            ex - half_x, ey, ez - half_z,
                            ex + half_x, ey + height, ez + half_z,
                            padding=0.25,
                        )
                    except Exception:
                        visible = True
                    if not visible:
                        continue
                entity.draw()
            if lighting_on:
                try:
                    disable_standard_item_lighting()
                except Exception:
                    pass
        finally:
            Entity._entity_draw_lighting_batched = False
            Entity._entity_shadow_camera = None
        try:
            from game.block.sign_renderer import draw_all_signs
            draw_all_signs(self, max_dist=max_dist)
        except Exception as exc:
            print(f"Sign render failed: {exc!r}")
        try:
            from game.block.mob_spawner_renderer import draw_all_mob_spawners
            draw_all_mob_spawners(self, max_dist=max_dist)
        except Exception as exc:
            print(f"Mob spawner render failed: {exc!r}")

    def _emit_multiplayer_particle_event(self, name, x, y, z, mx=0.0, my=0.0, mz=0.0, **extra):
        if getattr(self, "_mp_suppress_world_events", False):
            return
        callback = getattr(self, "_network_world_event_callback", None)
        if callable(callback):
            event = {
                "kind": "particle", "name": str(name),
                "x": float(x), "y": float(y), "z": float(z),
                "mx": float(mx), "my": float(my), "mz": float(mz),
            }
            event.update(extra)
            try:
                callback(event)
            except Exception:
                pass

    def spawn_particle(self, name, x, y, z, mx=0.0, my=0.0, mz=0.0):
        effects = getattr(self, "effects", None)
        if effects is None:
            return False
        viewer = getattr(self, "_particle_viewer_pos", None)
        if viewer is not None:
            dx, dy, dz = float(viewer[0]) - x, float(viewer[1]) - y, float(viewer[2]) - z
            if dx * dx + dy * dy + dz * dz > 256.0:
                return False
        before = effects.count()
        effects.spawn_particle(name, x, y, z, mx, my, mz, world=self)
        added = effects.count() > before
        if added:
            self._emit_multiplayer_particle_event(name, x, y, z, mx, my, mz)
        return added

    def _run_random_display_updates(self, player_pos, samples=1000):
        from game.entities.particle_events import run_random_display_updates
        run_random_display_updates(self, player_pos, self._particle_rng, samples=samples)

    def draw_particles(self, camera_pos, yaw, pitch, partial=0.0, fluid_mode=None):
        if getattr(self, "effects", None) is not None:
            self.effects.render(
                camera_pos, yaw, pitch, partial, world=self, fluid_mode=fluid_mode
            )

    def _apply_overrides(self, cx, cz):
        chunk = self.chunks.get((cx, cz))
        if chunk is None:
            return
        for (x, y, z), value in self.block_overrides.get((cx, cz), {}).items():
            if value is None:
                chunk.remove_block(x, y, z)
            elif isinstance(value, tuple):
                block_type, meta = value
                chunk.set_block(x, y, z, block_type, meta=meta)
            else:
                chunk.set_block(x, y, z, value)
        self._index_fire_chunk(chunk)


    def _create_mp_chunk_shell(self, cx, cz):
        size = int(self.CHUNK_SIZE) * int(self.CHUNK_SIZE) * int(self.CHUNK_HEIGHT)
        return self._generate_chunk(
            int(cx), int(cz),
            generated_data=bytearray(size),
            saved_chunk=None,
            saved_chunk_loaded=True,
            save_ready_checked=True,
        )

    def _generate_chunk(self, cx, cz, generated_data=None, saved_chunk=None,
                        saved_chunk_loaded=False, save_ready_checked=False):
        started = time.perf_counter()
        try:
            chunk = self._generate_chunk_impl(
                cx, cz, generated_data=generated_data,
                saved_chunk=saved_chunk, saved_chunk_loaded=saved_chunk_loaded,
                save_ready_checked=save_ready_checked,
            )
            if chunk is None:
                return None
            getattr(self, "_chunk_load_failures", set()).discard((int(cx), int(cz)))
            self.profiler.record_event(
                "chunk.load", cx=int(cx), cz=int(cz),
                duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
                source=("save" if getattr(chunk, "_loaded_from_save", False)
                        else ("worker_data" if generated_data is not None else "terrain")),
            )
            return chunk
        except _ChunkSaveDeferred:
            self.profiler.record_event(
                "chunk.load_deferred", cx=int(cx), cz=int(cz),
                reason="previous-save-pending",
            )
            return None
        except Exception as exc:
            self.profiler.record_event(
                "chunk.load_failed", cx=int(cx), cz=int(cz),
                duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
                error=type(exc).__name__, message=str(exc)[:300],
            )
            raise
        finally:
            self.profiler.record_since("chunk.integrate", started)

    def _generate_chunk_impl(self, cx, cz, generated_data=None, saved_chunk=None,
                             saved_chunk_loaded=False, save_ready_checked=False):
        key = (cx, cz)
        if key in self.chunks:
            return self.chunks[key]

        if self.save_store is not None:
            if save_ready_checked:
                save_ready = True
            else:
                has_pending_save = getattr(self.save_store, "has_chunk_save_pending", None)
                save_gate_started = time.perf_counter()
                if (not callable(has_pending_save) or has_pending_save(cx, cz)):
                    save_ready = self.save_store.poll_chunk_save_ready(cx, cz)
                else:
                    save_ready = True
                record_since = getattr(self.profiler, "record_since", None)
                if callable(record_since):
                    record_since("chunk.integrate.save_gate", save_gate_started)
            if save_ready is False:
                raise _ChunkSaveDeferred((cx, cz))
            if save_ready is None:
                getattr(self, "_chunk_load_failures", set()).add(key)
                self.profiler.record_event(
                    "chunk.load_blocked_save_failure", cx=int(cx), cz=int(cz),
                )
                return None
            if not saved_chunk_loaded:
                load_started = time.perf_counter()
                saved_chunk = self.save_store.load_chunk(cx, cz)
                self.profiler.record_since("chunk.integrate.save_load", load_started)
        else:
            saved_chunk = None
        migrated_wire_indices = frozenset()
        popped_gear_indices = frozenset()
        normalized_saved_block_ids = None
        if saved_chunk is not None:
            migration_started = time.perf_counter()
            from game.world.savegame import CLASSIC_BLOCK_ID_TO_NAME, CLASSIC_BLOCK_NAME_TO_ID
            from game.world.legacy_migrations import migrate_legacy_redstone_wire
            from game.block.fire_rules import is_normal_cube

            Block._load_defs()
            supporting_ids = {
                classic_id for name, classic_id in CLASSIC_BLOCK_NAME_TO_ID.items()
                if is_normal_cube(Block._block_defs.get(name))
            }
            normalized_saved_block_ids, migrated_wire_indices, popped_gear_indices = (
                migrate_legacy_redstone_wire(
                    saved_chunk["raw_blocks"], height=self.CHUNK_HEIGHT,
                    supporting_block_ids=supporting_ids,
                )
            )
            saved_blocks = bytearray(len(normalized_saved_block_ids))
            for index, classic_id in enumerate(normalized_saved_block_ids):
                name = CLASSIC_BLOCK_ID_TO_NAME.get(classic_id)
                if name is not None and name in Block._block_defs:
                    saved_blocks[index] = self._ensure_block_type_id(name)
            for index, name in saved_chunk.get("block_names", ()):
                if name == "gears":
                    continue
                if name in Block._block_defs and 0 <= index < len(saved_blocks):
                    saved_blocks[index] = self._ensure_block_type_id(name)
            generated_data = saved_blocks
            self.profiler.record_since("chunk.integrate.saved_migration", migration_started)
        elif generated_data is not None and isinstance(self.terrain, AlphaTerrainGenerator):
            started = time.perf_counter()
            self.terrain._remember_surface(
                cx, cz, generated_data, self.CHUNK_SIZE, self.CHUNK_SIZE,
                self.block_type_to_id,
            )
            self.profiler.record_since("chunk.integrate.surface", started)

        started = time.perf_counter()
        chunk = Chunk(
            self, cx, cz,
            self.CHUNK_SIZE, self.CHUNK_HEIGHT, self.CHUNK_SIZE,
            self.renderer, generated_data=generated_data,
        )
        chunk._loaded_from_save = saved_chunk is not None
        if saved_chunk is not None:
            count = len(chunk.blocks)
            for attr, key_name in (("metadata", "metadata"), ("skylight", "skylight"),
                                   ("blocklight", "blocklight")):
                values = saved_chunk.get(key_name, b"")
                if len(values) == count:
                    getattr(chunk, attr)[:] = values
            bed_id = self.block_type_to_id.get("bed")
            if bed_id is not None:
                for index, block_id in enumerate(chunk.blocks):
                    if int(block_id) == int(bed_id) and (chunk.metadata[index] & 4):
                        chunk.metadata[index] &= 0x0B
            heights = saved_chunk.get("heightmap", b"")
            if len(heights) == len(chunk.heightmap):
                chunk.heightmap[:] = heights
            chunk.terrain_populated = bool(saved_chunk.get("TerrainPopulated", False))
            chunk._source_block_ids = bytes(
                normalized_saved_block_ids if normalized_saved_block_ids is not None
                else saved_chunk["raw_blocks"]
            )
            for index in migrated_wire_indices | popped_gear_indices:
                if 0 <= index < len(chunk.metadata):
                    chunk.metadata[index] = 0
            chunk._source_internal_blocks = bytes(chunk.blocks)
            chunk._source_metadata = bytes(chunk.metadata)
            chunk._mcpy_block_names = {
                index: name for index, name in saved_chunk.get("block_names", ())
                if name in Block._block_defs and name != "gears"
            }
            chunk.mark_saved_state(self, entity_count=len(saved_chunk.get("entities", ())))
            if chunk.terrain_populated:
                self._populated_chunks.add(key)
            for record in saved_chunk.get("door_hinges", ()):
                pos = tuple(record.get("pos", ()))
                if len(pos) == 3:
                    self.door_hinges[pos] = bool(record.get("mirrored", False))
            for record in saved_chunk.get("door_partners", ()):
                pos = tuple(record.get("pos", ()))
                partner = tuple(record.get("partner", ()))
                if len(pos) == 3 and len(partner) == 3:
                    self.door_partners[pos] = partner
        self.profiler.record_since("chunk.integrate.construct", started)

        started = time.perf_counter()
        self._apply_chunk_overrides_to_buffer(
            chunk, self._worldgen_block_overrides.get(key, {}), player_edits=False,
        )
        self._apply_chunk_overrides_to_buffer(
            chunk, self.block_overrides.get(key, {}), player_edits=True,
        )
        self.profiler.record_since("chunk.integrate.overrides", started)
        self.chunks[key] = chunk
        self._index_fire_chunk(chunk)
        if chunk.dirty or chunk.dirty_sections:
            self.note_chunk_mesh_dirty(key)
        started = time.perf_counter()
        if getattr(self, "_redstone_system", None) is not None:
            self._redstone_system.on_chunk_loaded(chunk)
        self.profiler.record_since("chunk.integrate.redstone_index", started)

        if saved_chunk is not None:
            self._restore_saved_block_ticks(saved_chunk.get("tile_ticks", ()))

        started = time.perf_counter()
        self._fluid_scheduler.on_chunk_loaded(chunk)
        self.profiler.record_since("chunk.integrate.fluid_index", started)

        started = time.perf_counter()
        for position, value in self._worldgen_block_overrides.get(key, {}).items():
            if value in ("water", "lava"):
                self._fluid_scheduler.schedule(*position, value)
        for position, value in self.block_overrides.get(key, {}).items():
            block_type = value[0] if isinstance(value, tuple) else value
            if block_type in ("water", "lava"):
                self._fluid_scheduler.schedule(*position, block_type)
        self.profiler.record_since("chunk.integrate.fluid_overrides", started)

        started = time.perf_counter()
        if saved_chunk is not None and saved_chunk.get("entities"):
            self._restore_saved_entities(saved_chunk["entities"])
        self.profiler.record_since("chunk.integrate.restore_entities", started)

        started = time.perf_counter()
        if saved_chunk is not None and saved_chunk.get("tile_entities") and self.save_store is not None:
            self.save_store._decode_tile_entities(self, saved_chunk["tile_entities"])
        self.profiler.record_since("chunk.integrate.restore_tile_entities", started)

        started = time.perf_counter()
        self._invalidate_loaded_chunk_neighbors(cx, cz)
        self.profiler.record_since("chunk.integrate.neighbors", started)
        return chunk

    def _restore_saved_entities(self, records):
        from game.entities.item import ItemEntity, ItemStack
        from game.entities.arrow import ArrowEntity
        from game.content_ids import CLASSIC_LIVING_ENTITY_IDS, legacy_entity_id
        for record in records:
            if record.get("id") == legacy_entity_id("minecart"):
                try:
                    from game.entities.minecart import MinecartEntity
                    cart = MinecartEntity(
                        self, record.get("pos", (0.0, 64.0, 0.0)),
                        velocity=record.get("motion", (0.0, 0.0, 0.0)),
                        cart_type=record.get("cart_type", 0),
                    )
                    rotation = record.get("rotation", (0.0, 0.0))
                    cart.yaw = cart.prev_yaw = float(rotation[0])
                    cart.damage_taken = max(0, int(record.get("damage_taken", 0)))
                    cart.time_since_hit = max(0, int(record.get("time_since_hit", 0)))
                    cart.fuel = int(record.get("fuel", 0))
                    cart.push_x = float(record.get("push_x", 0.0))
                    cart.push_z = float(record.get("push_z", 0.0))
                    for entry in record.get("cargo_items", ()):
                        from game.entities.item import ItemStack
                        slot = int(entry.get("slot", -1))
                        if 0 <= slot < cart.INVENTORY_SIZE:
                            cart.cargo_items[slot] = ItemStack(
                                entry.get("id", "air"), int(entry.get("count", 1)),
                                int(entry.get("damage", 0)),
                            )
                    self.entities.append(cart)
                except Exception as exc:
                    print(f"Could not restore a minecart: {exc}")
                continue
            if record.get("id") == legacy_entity_id("boat"):
                try:
                    from game.entities.boat import BoatEntity
                    boat = BoatEntity(
                        self, record.get("pos", (0.0, 64.0, 0.0)),
                        velocity=record.get("motion", (0.0, 0.0, 0.0)),
                    )
                    rotation = record.get("rotation", (0.0, 0.0))
                    boat.yaw = boat.prev_yaw = float(rotation[0])
                    boat.damage_taken = max(0, int(record.get("damage_taken", 0)))
                    boat.time_since_hit = max(0, int(record.get("time_since_hit", 0)))
                    self.entities.append(boat)
                except Exception as exc:
                    print(f"Could not restore a boat: {exc}")
                continue
            if record.get("id") == legacy_entity_id("arrow"):
                try:
                    arrow = ArrowEntity(
                        self, record.get("pos", (0.0, 64.0, 0.0)),
                        velocity=record.get("motion", (0.0, 0.0, 0.0)),
                    )
                    rotation = record.get("rotation", (0.0, 0.0))
                    arrow.yaw = arrow.prev_yaw = float(rotation[0])
                    arrow.pitch = arrow.prev_pitch = float(rotation[1])
                    arrow.prev_pos[:] = arrow.pos
                    arrow.fire = float(record.get("fire", 0))
                    arrow.x_tile = int(record.get("x_tile", -1))
                    arrow.y_tile = int(record.get("y_tile", -1))
                    arrow.z_tile = int(record.get("z_tile", -1))
                    arrow.in_tile = record.get("in_tile")
                    arrow.arrow_shake = float(record.get("arrow_shake", 0))
                    arrow.in_ground = bool(record.get("in_ground", False))
                    arrow.ticks_in_ground = float(record.get("ticks_in_ground", 0))
                    arrow.ticks_in_air = float(record.get("ticks_in_air", 0))
                    self.entities.append(arrow)
                except Exception as exc:
                    print(f"Could not restore an arrow: {exc}")
                continue
            if record.get("id") == legacy_entity_id("fireball"):
                try:
                    from game.entities.fireball import FireballEntity
                    fireball = FireballEntity(
                        self, record.get("pos", (0.0, 64.0, 0.0)),
                        acceleration=record.get("acceleration", (0.0, 0.0, 0.0)),
                    )
                    fireball.vel[:] = [float(v) for v in record.get("motion", (0.0, 0.0, 0.0))]
                    rotation = record.get("rotation", (0.0, 0.0))
                    fireball.yaw = fireball.prev_yaw = float(rotation[0])
                    fireball.pitch = fireball.prev_pitch = float(rotation[1])
                    fireball.x_tile = int(record.get("x_tile", -1))
                    fireball.y_tile = int(record.get("y_tile", -1))
                    fireball.z_tile = int(record.get("z_tile", -1))
                    fireball.in_tile = record.get("in_tile")
                    fireball.shake = int(record.get("shake", 0))
                    fireball.in_ground = bool(record.get("in_ground", False))
                    fireball.age = float(record.get("age", 0.0))
                    fireball.ticks_in_air = float(record.get("ticks_in_air", 0))
                    fireball.prev_pos[:] = fireball.pos
                    self.entities.append(fireball)
                except Exception as exc:
                    print(f"Could not restore a fireball: {exc}")
                continue
            if record.get("id") == legacy_entity_id("painting"):
                try:
                    from game.entities.painting import PaintingEntity, ART_TYPES
                    art = None
                    motive = str(record.get("motive", "Kebab") or "Kebab")
                    for candidate in ART_TYPES:
                        if candidate.title == motive:
                            art = candidate
                            break
                    if art is None:
                        art = ART_TYPES[0]
                    painting = PaintingEntity(
                        self,
                        int(record.get("tile_x", 0)),
                        int(record.get("tile_y", 64)),
                        int(record.get("tile_z", 0)),
                        int(record.get("direction", 0)) & 3,
                        art,
                    )
                    self.entities.append(painting)
                except Exception as exc:
                    print(f"Could not restore a painting: {exc}")
                continue
            if record.get("id") == legacy_entity_id("falling_block"):
                try:
                    from game.entities.falling_block import FallingBlockEntity
                    self.entities.append(FallingBlockEntity(
                        record["tile_name"], record["pos"],
                        vel=record.get("motion", (0.0, 0.0, 0.0)),
                        fall_time=record.get("fall_time", 0),
                    ))
                except Exception as exc:
                    print(f"Could not restore a falling block: {exc}")
                continue
            save_id = record.get("id")
            if save_id in CLASSIC_LIVING_ENTITY_IDS:
                try:
                    from game.world.savegame import WorldSave
                    registry = WorldSave._living_mob_registry()
                    cls = registry.get(save_id)
                    if cls is None:
                        continue
                    pos = record.get("pos", (0.0, 64.0, 0.0))
                    mob = cls(pos)
                    if hasattr(mob, "set_slime_size"):
                        mob.set_slime_size(int(record.get("slime_size", 1)))
                    motion = record.get("motion", (0.0, 0.0, 0.0))
                    mob.vel[0] = float(motion[0])
                    mob.vel[1] = float(motion[1])
                    mob.vel[2] = float(motion[2])
                    rotation = record.get("rotation", (0.0, 0.0))
                    mob.yaw = mob.prev_yaw = float(rotation[0])
                    mob.render_yaw_offset = mob.prev_render_yaw_offset = float(rotation[0])
                    mob.rotation_pitch = float(rotation[1])
                    mob.health = int(record.get("health", getattr(cls, "HEALTH", 10)))
                    mob.prev_health = mob.health
                    mob._fire_ticks = int(record.get("fire", 0))
                    if hasattr(mob, "air"):
                        mob.air = int(record.get("air", 300))
                    mob.on_ground = bool(record.get("on_ground", False))
                    mob.hurt_time = int(record.get("hurt_time", 0))
                    mob.attack_time = int(record.get("attack_time", 0))
                    mob.death_time = int(record.get("death_time", 0))
                    if save_id == legacy_entity_id("sheep"):
                        mob.sheared = bool(record.get("sheared", False))
                        mob.set_fleece_color(int(record.get("fleece_color", 0)))
                    if save_id == legacy_entity_id("pig"):
                        mob.saddled = bool(record.get("saddled", False))
                    if save_id == legacy_entity_id("zombie_pigman"):
                        mob.restore_anger(int(record.get("anger", 0)))
                    if save_id == legacy_entity_id("creeper"):
                        mob.time_since_ignited = int(record.get("fuse", 0))
                        mob.last_active_time = mob.time_since_ignited
                    self.entities.append(mob)
                except Exception as exc:
                    print(f"Could not restore a {save_id}: {exc}")
                continue
            if save_id != legacy_entity_id("item"):
                continue
            try:
                entity = ItemEntity(
                    ItemStack(record["item_name"], record.get("count", 1), record.get("damage", 0)),
                    record.get("pos", (0.0, 64.0, 0.0)),
                    velocity=record.get("motion", (0.0, 0.0, 0.0)),
                    pickup_delay=record.get("pickup_delay", 0),
                )
                entity.age = float(record.get("age", 0))
                entity.health = int(record.get("health", 5))
                entity.on_ground = bool(record.get("on_ground", False))
                self.entities.append(entity)
            except Exception as exc:
                print(f"Could not restore a dropped item: {exc}")

    def save_world(self, player=None, *, stop_workers=False):
        if self.save_store is None:
            return False
        return self.save_store.save_world(self, player, stop_workers=stop_workers)

    def begin_save(self, player=None, *, stop_workers=False):
        if self.save_store is None:
            return None
        return self.save_store.begin_save(self, player, stop_workers=stop_workers)

    def set_spawn_from_player(self, player):
        self.spawn_x = int(round(float(player.pos[0])))
        self.spawn_y = int(math.floor(float(player.pos[1]) + 0.5))
        self.spawn_z = int(round(float(player.pos[2])))

    def _apply_chunk_overrides_to_buffer(self, chunk, overrides, player_edits=False):
        start_x, start_z = chunk.start_x, chunk.start_z
        width, depth, height = chunk.width, chunk.depth, chunk.height
        blocks, metadata = chunk.blocks, chunk.metadata
        ensure_id = self._ensure_block_type_id
        for (x, y, z), value in overrides.items():
            if not (0 <= y < height):
                continue
            lx, lz = x - start_x, z - start_z
            if not (0 <= lx < width and 0 <= lz < depth):
                continue
            meta = 0
            block_type = value
            if isinstance(value, tuple):
                block_type, meta = value
            if block_type is None:
                block_id = 0
                meta = 0
            else:
                block_id = ensure_id(block_type)
            idx = (lx * depth + lz) * height + y
            if blocks[idx] != block_id:
                invalidate = getattr(chunk, "_invalidate_mesh_air_cache", None)
                if callable(invalidate):
                    invalidate()
            blocks[idx] = block_id
            metadata[idx] = int(meta) & 15

    def _terrain_worker_generate(self, cx, cz, block_ids):
        started = time.perf_counter()
        source = "terrain"
        try:
            if self.save_store is not None:
                load_started = time.perf_counter()
                skip_load = False
                if self.debug_world:
                    if not self.save_store.chunk_exists(int(cx), int(cz)):
                        skip_load = True
                if not skip_load:
                    saved_chunk = self.save_store.load_chunk(int(cx), int(cz))
                    load_ms = (time.perf_counter() - load_started) * 1000.0
                    if saved_chunk is not None:
                        source = "saved"
                        self.profiler.record_ms(
                            "terrain.worker.load_saved", load_ms,
                            event_details={"cx": int(cx), "cz": int(cz), "found": True},
                        )
                        return _ChunkTerrainWorkerResult(
                            generated_data=None, saved_chunk=saved_chunk, source="saved",
                        )
                    self.profiler.record_ms(
                        "terrain.worker.load_saved", load_ms,
                        event_details={"cx": int(cx), "cz": int(cz), "found": False},
                    )
                else:
                    self.profiler.record_ms(
                        "terrain.worker.load_saved", 0.0,
                        event_details={"cx": int(cx), "cz": int(cz), "found": False, "skipped": True},
                    )

            if getattr(self, "_multiplayer_client_world", False):
                size = int(self.CHUNK_SIZE) * int(self.CHUNK_SIZE) * int(self.CHUNK_HEIGHT)
                self.profiler.record_ms(
                    "terrain.worker.generate", 0.0,
                    event_details={"cx": int(cx), "cz": int(cz), "source": "mp_shell"},
                )
                return _ChunkTerrainWorkerResult(
                    generated_data=bytearray(size), saved_chunk=None, source="mp_shell",
                )

            generator = getattr(self._terrain_worker_local, "generator", None)
            if generator is None:
                if self.debug_world:
                    generator = DebugTerrainGenerator(self.seed)
                elif self.is_hell_world:
                    generator = NetherTerrainGenerator(self.seed, world_height=WORLD_HEIGHT)
                else:
                    generator = AlphaTerrainGenerator(
                        self.seed, sea_level=SEA_LEVEL, world_height=WORLD_HEIGHT,
                        snow_covered=self.snow_covered,
                        use_numba=self._terrain_use_numba,
                    )
                self._terrain_worker_local.generator = generator
            generate_started = time.perf_counter()
            generated_data = generator.generate_chunk_data(
                int(cx), int(cz), self.CHUNK_SIZE, self.CHUNK_SIZE, block_ids,
            )
            self.profiler.record_ms(
                "terrain.worker.generate", (time.perf_counter() - generate_started) * 1000.0,
                event_details={"cx": int(cx), "cz": int(cz)},
            )
            return _ChunkTerrainWorkerResult(
                generated_data=generated_data, saved_chunk=None, source="terrain",
            )
        finally:
            self.profiler.record_ms(
                "terrain.worker", (time.perf_counter() - started) * 1000.0,
                event_details={"cx": int(cx), "cz": int(cz), "source": source},
            )

    def _ensure_terrain_executor(self):
        if self._terrain_executor is None:
            self._terrain_executor = ThreadPoolExecutor(
                max_workers=self._terrain_worker_count,
                thread_name_prefix="minecraft-terrain",
            )
        return self._terrain_executor

    def _request_chunk_terrain(self, wanted):
        available = self._terrain_max_inflight - len(self._terrain_futures)
        if available <= 0 or not wanted:
            return
        count = min(available, max(self.chunks_per_update, self._terrain_worker_count))
        executor = self._ensure_terrain_executor()
        block_ids = dict(self.block_type_to_id)
        submitted = 0
        for cx, cz in wanted:
            if submitted >= count:
                break
            key = (cx, cz)
            if key in self.chunks or key in self._terrain_futures:
                continue
            has_pending_save = getattr(self.save_store, "has_chunk_save_pending", None)
            if (self.save_store is not None and callable(has_pending_save)
                    and has_pending_save(cx, cz)):
                save_ready = self.save_store.poll_chunk_save_ready(cx, cz)
                if save_ready is False:
                    self.profiler.record_event(
                        "chunk.request_deferred_save", cx=int(cx), cz=int(cz),
                    )
                    continue
                if save_ready is None:
                    self._chunk_load_failures.add(key)
                    self.profiler.record_event(
                        "chunk.load_blocked_save_failure", cx=int(cx), cz=int(cz),
                    )
                    continue
            submitted_at = time.perf_counter()
            future = executor.submit(
                self._terrain_worker_generate, cx, cz, block_ids,
            )
            self._terrain_futures[key] = future
            self._terrain_requested_at[key] = submitted_at
            self._terrain_ready_at.pop(key, None)
            self.profiler.record_event(
                "chunk.request", cx=int(cx), cz=int(cz),
                in_flight=len(self._terrain_futures),
            )
            submitted += 1

    def _poll_chunk_terrain(self, center_cx, center_cz, load_sq, max_ready=None):
        ready = []
        now = time.perf_counter()
        ready_times = getattr(self, "_terrain_ready_at", None)
        if ready_times is None:
            ready_times = self._terrain_ready_at = {}
        for key, future in list(self._terrain_futures.items()):
            if not future.done():
                continue
            ready_at = ready_times.setdefault(key, now)
            dx, dz = key[0] - center_cx, key[1] - center_cz
            ready.append((dx * dx + dz * dz, ready_at, key, future))
        ready.sort(key=lambda item: (item[0], item[1], item[2]))

        integrated = 0
        if max_ready is None:
            max_ready = self._terrain_integrate_max_per_frame
        batch_started = time.perf_counter()
        for distance_sq, ready_at, key, future in ready:
            if integrated >= max_ready:
                break
            if integrated:
                elapsed_ms = (time.perf_counter() - batch_started) * 1000.0
                if elapsed_ms >= self._terrain_integrate_budget_ms:
                    break
            cx, cz = key
            if self.save_store is not None:
                has_pending_save = getattr(self.save_store, "has_chunk_save_pending", None)
                save_gate_started = time.perf_counter()
                if (not callable(has_pending_save) or has_pending_save(cx, cz)):
                    save_ready = self.save_store.poll_chunk_save_ready(cx, cz)
                else:
                    save_ready = True
                record_since = getattr(self.profiler, "record_since", None)
                if callable(record_since):
                    record_since("chunk.integrate.save_gate", save_gate_started)
                if save_ready is False:
                    self._chunk_save_wait_started.setdefault(key, time.perf_counter())
                    self.profiler.record_event(
                        "chunk.integrate_wait_save", cx=int(cx), cz=int(cz),
                    )
                    continue
                wait_started = self._chunk_save_wait_started.pop(key, None)
                if wait_started is not None:
                    self.profiler.record_ms(
                        "chunk.save_wait", (time.perf_counter() - wait_started) * 1000.0,
                        event_details={"cx": int(cx), "cz": int(cz)},
                    )
                if save_ready is None:
                    self._terrain_futures.pop(key, None)
                    self._terrain_requested_at.pop(key, None)
                    self._terrain_ready_at.pop(key, None)
                    self._chunk_load_failures.add(key)
                    self.profiler.record_event(
                        "chunk.load_blocked_save_failure", cx=int(cx), cz=int(cz),
                    )
                    continue
            self._terrain_futures.pop(key, None)
            try:
                worker_result = future.result()
            except Exception as exc:
                self._chunk_save_wait_started.pop(key, None)
                self._terrain_requested_at.pop(key, None)
                self._terrain_ready_at.pop(key, None)
                self._chunk_load_failures.add(key)
                self.profiler.record_event(
                    "chunk.terrain_worker_failed", cx=int(cx), cz=int(cz),
                    error=type(exc).__name__, message=str(exc)[:240],
                )
                print(f"Terrain worker failed for chunk {key}: {exc!r}")
                continue

            dx, dz = cx - center_cx, cz - center_cz
            network_stream_keys = getattr(self, "_mp_chunk_stream_keys", set())
            if ((dx * dx + dz * dz > load_sq and key not in network_stream_keys)
                    or key in self.chunks):
                self._chunk_save_wait_started.pop(key, None)
                self._terrain_requested_at.pop(key, None)
                self._terrain_ready_at.pop(key, None)
                continue

            if isinstance(worker_result, _ChunkTerrainWorkerResult):
                generated_data = worker_result.generated_data
                saved_chunk = worker_result.saved_chunk
                saved_chunk_loaded = True
                source = worker_result.source
            else:
                generated_data = worker_result
                saved_chunk = None
                saved_chunk_loaded = False
                source = "terrain"

            integrate_started = time.perf_counter()
            requested_at = self._terrain_requested_at.get(key)
            completed_at = self._terrain_ready_at.get(key, ready_at)
            if requested_at is not None:
                self.profiler.record_ms(
                    "chunk.terrain_future_latency",
                    (completed_at - requested_at) * 1000.0,
                    event_details={"cx": int(cx), "cz": int(cz), "source": source},
                )
                self.profiler.record_ms(
                    "chunk.terrain_integration_queue_wait",
                    (integrate_started - completed_at) * 1000.0,
                    event_details={"cx": int(cx), "cz": int(cz),
                                   "distance_sq": int(distance_sq)},
                )

            chunk = self._generate_chunk(
                cx, cz, generated_data=generated_data,
                saved_chunk=saved_chunk, saved_chunk_loaded=saved_chunk_loaded,
                save_ready_checked=(self.save_store is not None),
            )
            if chunk is None:
                self._terrain_futures[key] = future
                continue

            integrated_at = time.perf_counter()
            integrate_ms = (integrated_at - integrate_started) * 1000.0
            self._terrain_integrate_avg_ms = (
                0.8 * self._terrain_integrate_avg_ms + 0.2 * integrate_ms
            )
            requested_at = self._terrain_requested_at.pop(key, None)
            self._terrain_ready_at.pop(key, None)
            if requested_at is not None:
                self.profiler.record_ms(
                    "chunk.stream_ready_latency",
                    (integrated_at - requested_at) * 1000.0,
                    event_details={"cx": int(cx), "cz": int(cz),
                                   "source": source},
                )

            if key in self._populated_chunks:
                if key not in self._chunks_needing_lighting:
                    self._chunks_needing_lighting.append(key)
            elif self.debug_world:
                self._populated_chunks.add(key)
                chunk = self.chunks.get(key)
                if chunk is not None:
                    chunk.terrain_populated = True
                    if not any(chunk.blocks):
                        chunk.mark_saved_state(self, entity_count=0)
                if key not in self._chunks_needing_lighting:
                    self._chunks_needing_lighting.append(key)
            elif key != self._population_future_key and key not in self._chunks_needing_decoration:
                self._chunks_needing_decoration.append(key)
            integrated += 1

        if integrated:
            self.profiler.record_ms(
                "chunk.terrain_integration_batch",
                (time.perf_counter() - batch_started) * 1000.0,
                event_details={
                    "integrated": integrated,
                    "ready_backlog": len(ready) - integrated,
                    "budget_ms": self._terrain_integrate_budget_ms,
                    "integrate_avg_ms": round(self._terrain_integrate_avg_ms, 3),
                },
            )

    def _ensure_population_executor(self):
        if self._population_executor is None:
            self._population_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="minecraft-population",
            )
        return self._population_executor

    def _population_worker(self, snapshot, cx, cz):
        generator = getattr(self._population_worker_local, "generator", None)
        if generator is None:
            if self.is_hell_world:
                generator = NetherTerrainGenerator(self.seed, world_height=WORLD_HEIGHT)
            else:
                generator = AlphaTerrainGenerator(
                    self.seed, sea_level=SEA_LEVEL, world_height=WORLD_HEIGHT,
                    snow_covered=self.snow_covered,
                    use_numba=self._terrain_use_numba,
                )
            self._population_worker_local.generator = generator
        started = time.perf_counter()
        try:
            generator.populate_chunk(snapshot, cx, cz)
            return (
                snapshot.collect_changes(),
                snapshot.collect_generated_chest_loot(),
                snapshot.collect_generated_spawner_mobs(),
            )
        finally:
            self.profiler.record_since("population.chunk", started)

    def _population_neighborhood_snapshot(self, key):
        cx, cz = key
        keys = ((cx, cz), (cx + 1, cz), (cx, cz + 1), (cx + 1, cz + 1))
        if any(k not in self.chunks for k in keys):
            return None
        return tuple((k, self.chunks[k]) for k in keys)

    def _population_neighborhood_loaded(self, key):
        return self._population_neighborhood_snapshot(key) is not None

    def _population_neighborhood_unchanged(self, key, snapshot):
        if snapshot is None:
            return False
        return all(self.chunks.get(chunk_key) is chunk
                   for chunk_key, chunk in snapshot)

    def _start_population(self, key):
        if getattr(self, "_multiplayer_client_world", False):
            return
        if self.debug_world:
            if key in self.chunks and key not in self._populated_chunks:
                self._populated_chunks.add(key)
                self.chunks[key].terrain_populated = True
                if key not in self._chunks_needing_lighting:
                    self._chunks_needing_lighting.append(key)
            return False
        if (key not in self.chunks or key in self._populated_chunks
                or self._population_future is not None):
            return False
        neighborhood = self._population_neighborhood_snapshot(key)
        if neighborhood is None:
            return False
        snapshot_started = time.perf_counter()
        snapshot = PopulationWorldSnapshot(self, focus_key=key)
        self.profiler.record_since("population.snapshot", snapshot_started)
        cx, cz = key
        self._population_future_key = key
        self._population_future_chunk = self.chunks[key]
        self._population_future_neighborhood = neighborhood
        self._population_future_revision = self._player_edit_revision
        self._population_future = self._ensure_population_executor().submit(
            self._population_worker, snapshot, cx, cz,
        )
        return True

    def _commit_population_changes(
            self, target_key, changes, chest_loot=None, spawner_mobs=None):
        started = time.perf_counter()
        relight = set()
        dirty_sections_by_chunk = {}
        chest_loot = chest_loot or {}
        spawner_mobs = spawner_mobs or {}
        self._placing_generated_features = True

        def queue_dirty(chunk_key, chunk, y):
            if chunk is None:
                return
            section_height = max(1, int(chunk.section_height))
            section_idx = max(0, min(chunk.section_count - 1, int(y) // section_height))
            queued = dirty_sections_by_chunk.setdefault(chunk_key, set())
            queued.add(section_idx)
            if int(y) % section_height == 0 and section_idx > 0:
                queued.add(section_idx - 1)
            if (int(y) % section_height == section_height - 1
                    and section_idx + 1 < chunk.section_count):
                queued.add(section_idx + 1)

        light_signature_cache = {}
        def light_signature(block_type):
            try:
                return light_signature_cache[block_type]
            except KeyError:
                value = self._light_signature(block_type)
                light_signature_cache[block_type] = value
                return value

        try:
            tile_entity_blocks = _POPULATION_TILE_ENTITY_BLOCKS
            fluid_scheduler = self._fluid_scheduler
            ensure_block_id = self._ensure_block_type_id
            for (x, y, z), block_type in changes.items():
                chunk_key = self._get_chunk_index(x, z)
                self._worldgen_block_overrides.setdefault(chunk_key, {})[(x, y, z)] = block_type
                chunk = self.chunks.get(chunk_key)
                if chunk is None:
                    continue
                if (x, y, z) in self.block_overrides.get(chunk_key, {}):
                    continue

                if isinstance(block_type, Block):
                    block_type = block_type.block_type
                if block_type is not None:
                    block_type = str(block_type)
                idx = chunk._index(x, y, z)
                if idx is None:
                    old_type = chunk.get_block_type(x, y, z)
                    if old_type == block_type:
                        continue
                    changed_light = light_signature(old_type) != light_signature(block_type)
                    if changed_light and chunk.light_ready and chunk_key != target_key:
                        relight.add(chunk_key)
                    self.set_block(x, y, z, block_type)
                    continue
                old_id = chunk.blocks[idx]
                id_to_type = chunk._id_to_block_type
                old_type = id_to_type[old_id] if old_id < len(id_to_type) else None
                if old_type == block_type:
                    continue

                changed_light = light_signature(old_type) != light_signature(block_type)
                if changed_light and chunk.light_ready and chunk_key != target_key:
                    relight.add(chunk_key)

                if (old_type in tile_entity_blocks or block_type in tile_entity_blocks
                        or old_type in _DOOR_BLOCKS or block_type in _DOOR_BLOCKS):
                    self.set_block(x, y, z, block_type)
                    continue

                new_id = 0 if block_type is None else ensure_block_id(block_type)
                if old_id != new_id:
                    chunk._invalidate_mesh_air_cache()
                chunk.blocks[idx] = new_id
                chunk.metadata[idx] = 0
                if old_id != new_id:
                    chunk._mcpy_block_names.pop(idx, None)
                if old_type == "fire" or block_type == "fire":
                    self._note_fire_block_change(x, y, z, old_type, block_type)
                queue_dirty(chunk_key, chunk, y)

                cx, cz = chunk_key
                size = self.CHUNK_SIZE
                lx, lz = x - cx * size, z - cz * size
                if lx == 0:
                    queue_dirty((cx - 1, cz), self.chunks.get((cx - 1, cz)), y)
                elif lx == size - 1:
                    queue_dirty((cx + 1, cz), self.chunks.get((cx + 1, cz)), y)
                if lz == 0:
                    queue_dirty((cx, cz - 1), self.chunks.get((cx, cz - 1)), y)
                elif lz == size - 1:
                    queue_dirty((cx, cz + 1), self.chunks.get((cx, cz + 1)), y)

                if (fluid_scheduler is not None
                        and (old_type in _POPULATION_FLUID_BLOCKS
                             or block_type in _POPULATION_FLUID_BLOCKS)):
                    fluid_scheduler.on_block_changed(x, y, z, old_type, block_type)

            for chunk_key, sections in dirty_sections_by_chunk.items():
                chunk = self.chunks.get(chunk_key)
                if chunk is not None and sections:
                    chunk.dirty_sections.update(sections)
                    chunk.dirty = True

            for (x, y, z), contents in chest_loot.items():
                chunk_key = self._get_chunk_index(x, z)
                if (x, y, z) in self.block_overrides.get(chunk_key, {}):
                    continue
                if self.get_block_type(x, y, z) != "chest":
                    continue
                self.set_generated_chest_loot(x, y, z, contents)
            for (x, y, z), mob_id in spawner_mobs.items():
                chunk_key = self._get_chunk_index(x, z)
                if (x, y, z) in self.block_overrides.get(chunk_key, {}):
                    continue
                self.set_generated_spawner_mob(x, y, z, mob_id)
        finally:
            self._placing_generated_features = False
            self.profiler.record_since("population.commit", started)

        for key in relight:
            if key not in self._chunks_needing_lighting:
                self._chunks_needing_lighting.append(key)

    def _make_population_commit_state(
            self, target_key, changes, chest_loot=None, spawner_mobs=None,
            protected_neighborhood=None):
        chest_loot = chest_loot or {}
        spawner_mobs = spawner_mobs or {}
        def operations():
            for item in changes.items():
                yield "block", item
            for item in chest_loot.items():
                yield "chest", item
            for item in spawner_mobs.items():
                yield "spawner", item

        protected_keys = tuple(
            chunk_key for chunk_key, _chunk in (protected_neighborhood or ())
        )
        return {
            "target_key": target_key,
            "operations": iter(operations()),
            "total_operations": len(changes) + len(chest_loot) + len(spawner_mobs),
            "processed_operations": 0,
            "dirty_sections_by_chunk": {},
            "relight": set(),
            "light_signature_cache": {},
            "started": time.perf_counter(),
            "protected_keys": protected_keys,
            "exhausted": False,
        }

    def _population_commit_light_signature(self, state, block_type):
        cache = state["light_signature_cache"]
        try:
            return cache[block_type]
        except KeyError:
            result = self._light_signature(block_type)
            cache[block_type] = result
            return result

    @staticmethod
    def _queue_population_commit_dirty(state, chunk_key, chunk, block_y):
        if chunk is None:
            return
        section_height = max(1, int(chunk.section_height))
        section_idx = max(
            0, min(chunk.section_count - 1, int(block_y) // section_height)
        )
        queued = state["dirty_sections_by_chunk"].setdefault(chunk_key, set())
        queued.add(section_idx)
        if int(block_y) % section_height == 0 and section_idx > 0:
            queued.add(section_idx - 1)
        if (int(block_y) % section_height == section_height - 1
                and section_idx + 1 < chunk.section_count):
            queued.add(section_idx + 1)

    def _apply_population_commit_operation(self, state, kind, item):
        if kind == "chest":
            (x, y, z), contents = item
            chunk_key = self._get_chunk_index(x, z)
            if (x, y, z) in self.block_overrides.get(chunk_key, {}):
                return
            if self.get_block_type(x, y, z) == "chest":
                self.set_generated_chest_loot(x, y, z, contents)
            return
        if kind == "spawner":
            (x, y, z), mob_id = item
            chunk_key = self._get_chunk_index(x, z)
            if (x, y, z) in self.block_overrides.get(chunk_key, {}):
                return
            self.set_generated_spawner_mob(x, y, z, mob_id)
            return

        (x, y, z), block_value = item
        block_type = block_value
        metadata = 0
        if isinstance(block_value, tuple) and len(block_value) == 2:
            block_type, metadata = block_value
        metadata = int(metadata) & 15
        if isinstance(block_type, Block):
            block_type = block_type.block_type
        if block_type is not None:
            block_type = str(block_type)

        stored_value = (block_type, metadata) if metadata else block_type
        target_key = state["target_key"]
        chunk_key = self._get_chunk_index(x, z)
        self._worldgen_block_overrides.setdefault(chunk_key, {})[(x, y, z)] = stored_value
        chunk = self.chunks.get(chunk_key)
        if chunk is None or (x, y, z) in self.block_overrides.get(chunk_key, {}):
            return

        relight = state["relight"]
        idx = chunk._index(x, y, z)
        if idx is None:
            old_type = chunk.get_block_type(x, y, z)
            old_meta = self.get_metadata(x, y, z)
            if old_type == block_type and old_meta == metadata:
                return
            if (self._population_commit_light_signature(state, old_type) != self._population_commit_light_signature(state, block_type)
                    and chunk.light_ready and chunk_key != target_key):
                relight.add(chunk_key)
            self.set_block(x, y, z, block_type, meta=metadata)
            return

        old_id = chunk.blocks[idx]
        id_to_type = chunk._id_to_block_type
        old_type = id_to_type[old_id] if old_id < len(id_to_type) else None
        old_meta = int(chunk.metadata[idx]) & 15
        if old_type == block_type and old_meta == metadata:
            return
        if (self._population_commit_light_signature(state, old_type) != self._population_commit_light_signature(state, block_type)
                and chunk.light_ready and chunk_key != target_key):
            relight.add(chunk_key)

        if (old_type in _POPULATION_TILE_ENTITY_BLOCKS
                or block_type in _POPULATION_TILE_ENTITY_BLOCKS
                or old_type in _DOOR_BLOCKS or block_type in _DOOR_BLOCKS):
            self.set_block(x, y, z, block_type, meta=metadata)
            return

        new_id = 0 if block_type is None else self._ensure_block_type_id(block_type)
        if old_id != new_id:
            chunk._invalidate_mesh_air_cache()
        chunk.blocks[idx] = new_id
        chunk.metadata[idx] = metadata
        if old_id != new_id:
            chunk._mcpy_block_names.pop(idx, None)
        if old_type == "fire" or block_type == "fire":
            self._note_fire_block_change(x, y, z, old_type, block_type)

        self._queue_population_commit_dirty(state, chunk_key, chunk, y)
        cx, cz = chunk_key
        size = self.CHUNK_SIZE
        lx, lz = x - cx * size, z - cz * size
        if lx == 0:
            self._queue_population_commit_dirty(
                state, (cx - 1, cz), self.chunks.get((cx - 1, cz)), y,
            )
        elif lx == size - 1:
            self._queue_population_commit_dirty(
                state, (cx + 1, cz), self.chunks.get((cx + 1, cz)), y,
            )
        if lz == 0:
            self._queue_population_commit_dirty(
                state, (cx, cz - 1), self.chunks.get((cx, cz - 1)), y,
            )
        elif lz == size - 1:
            self._queue_population_commit_dirty(
                state, (cx, cz + 1), self.chunks.get((cx, cz + 1)), y,
            )

        if (old_type in _POPULATION_FLUID_BLOCKS
                or block_type in _POPULATION_FLUID_BLOCKS):
            fluid_scheduler = self._fluid_scheduler
            if fluid_scheduler is not None:
                fluid_scheduler.on_block_changed(x, y, z, old_type, block_type)

    def _process_population_commit_batch(self):
        state = self._population_commit_state
        if state is None:
            return True
        started = time.perf_counter()
        processed = 0
        previous_placing = self._placing_generated_features
        backlog = 0
        try:
            backlog = len(getattr(self, "_terrain_ready_queue", ()) or ())
            backlog += len(getattr(self, "_terrain_futures", {}) or {})
        except Exception:
            backlog = 0
        max_ops = self._population_commit_max_ops
        budget_ms = self._population_commit_budget_ms
        if backlog >= 4:
            max_ops = max(max_ops, 256)
            budget_ms = max(budget_ms, 3.5)
        apply_op = self._apply_population_commit_operation
        operations = state["operations"]
        self._placing_generated_features = True
        try:
            while (processed < max_ops
                   and (time.perf_counter() - started) * 1000.0
                   < budget_ms):
                try:
                    kind, item = next(operations)
                except StopIteration:
                    state["exhausted"] = True
                    break
                apply_op(state, kind, item)
                processed += 1
                state["processed_operations"] += 1
        finally:
            self._placing_generated_features = previous_placing

        exhausted = bool(state["exhausted"])
        if exhausted:
            for chunk_key, sections in state["dirty_sections_by_chunk"].items():
                chunk = self.chunks.get(chunk_key)
                if chunk is not None and sections:
                    chunk.dirty_sections.update(sections)
                    chunk.dirty = True
            for key in state["relight"]:
                if key not in self._chunks_needing_lighting:
                    self._chunks_needing_lighting.append(key)
            total_ms = (time.perf_counter() - state["started"]) * 1000.0
            self.profiler.record_ms(
                "population.commit_total", total_ms,
                event_details={"operations": state["processed_operations"]},
            )

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.profiler.record_ms(
            "population.commit", elapsed_ms,
            event_details={
                "processed": processed,
                "total": state["total_operations"],
                "completed": exhausted,
            },
        )
        return exhausted

    def _finish_population_commit(self):
        state = self._population_commit_state
        if state is None:
            return "population-commit-none"
        key = state["target_key"]
        self._population_commit_state = None
        self._populated_chunks.add(key)
        if key in self.chunks:
            self._apply_overrides(*key)
            if key not in self._chunks_needing_lighting:
                self._chunks_needing_lighting.append(key)
        return "population-commit"

    def _process_one_chunk_stage(self):
        if self._population_commit_state is not None:
            if self._process_population_commit_batch():
                return self._finish_population_commit()
            return "population-commit-batch"

        if self._population_future is not None and self._population_future.done():
            future = self._population_future
            key = self._population_future_key
            source_chunk = self._population_future_chunk
            source_neighborhood = self._population_future_neighborhood
            self._population_future = None
            self._population_future_key = None
            self._population_future_chunk = None
            self._population_future_neighborhood = None
            generated_chest_loot = {}
            generated_spawner_mobs = {}
            try:
                population_result = future.result()
                if (isinstance(population_result, tuple)
                        and len(population_result) in (2, 3)
                        and isinstance(population_result[0], dict)
                        and isinstance(population_result[1], dict)):
                    changes, generated_chest_loot = population_result[:2]
                    if len(population_result) == 3 and isinstance(population_result[2], dict):
                        generated_spawner_mobs = population_result[2]
                else:
                    changes = population_result
            except Exception as exc:
                print(f"Population worker failed for chunk {key}: {exc!r}")
                if (self.chunks.get(key) is source_chunk and source_chunk is not None
                        and self._population_neighborhood_unchanged(
                            key, source_neighborhood
                        )):
                    self._decorate_chunk(*key)
                    self._apply_overrides(*key)
                    if key not in self._chunks_needing_lighting:
                        self._chunks_needing_lighting.append(key)
                elif key in self.chunks:
                    self._chunks_needing_decoration.appendleft(key)
                return "population-error-fallback"

            if self._population_future_revision != self._player_edit_revision:
                if key in self.chunks:
                    self._chunks_needing_decoration.appendleft(key)
                return "population-retry"

            if self.chunks.get(key) is not source_chunk or source_chunk is None:
                if key in self.chunks:
                    self._chunks_needing_decoration.appendleft(key)
                    return "population-retry-reloaded-source"
                return "population-discard-unloaded"
            if not self._population_neighborhood_unchanged(key, source_neighborhood):
                self._chunks_needing_decoration.appendleft(key)
                return "population-retry-neighbor-reloaded"

            self._population_commit_state = self._make_population_commit_state(
                key, changes, generated_chest_loot, generated_spawner_mobs,
                protected_neighborhood=source_neighborhood,
            )
            if self._process_population_commit_batch():
                return self._finish_population_commit()
            return "population-commit-batch"

        while self._chunks_needing_lighting:
            key = self._chunks_needing_lighting.popleft()
            chunk = self.chunks.get(key)
            if chunk is None:
                continue
            self._init_chunk_lighting(chunk)
            chunk.mark_all_dirty()
            return "lighting"

        if self._population_future is not None:
            return "population-wait"

        pending = len(self._chunks_needing_decoration)
        for _ in range(pending):
            key = self._chunks_needing_decoration.popleft()
            if key not in self.chunks or key in self._populated_chunks:
                continue
            if not self._population_neighborhood_loaded(key):
                self._chunks_needing_decoration.append(key)
                continue
            if self._start_population(key):
                return "population-start"
        return "population-wait-neighbors" if self._chunks_needing_decoration else None

    def shutdown(self, wait=False):
        for future in self._terrain_futures.values():
            future.cancel()
        self._terrain_futures.clear()
        self._terrain_requested_at.clear()
        self._terrain_ready_at.clear()
        executor, self._terrain_executor = self._terrain_executor, None
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=True)
        population_future = self._population_future
        if population_future is not None:
            population_future.cancel()
        self._population_future = None
        self._population_future_key = None
        self._population_future_chunk = None
        self._population_future_neighborhood = None
        population_executor, self._population_executor = self._population_executor, None
        if population_executor is not None:
            population_executor.shutdown(wait=wait, cancel_futures=True)

    def _warm_lighting_kernels(self):
        if not LIGHTING_NUMBA_AVAILABLE:
            return
        opacity_lut, light_lut = self._get_lighting_luts()
        blocks = _lighting_np.zeros(8, dtype=_lighting_np.uint8)
        sky = _lighting_np.zeros(8, dtype=_lighting_np.uint8)
        block = _lighting_np.zeros(8, dtype=_lighting_np.uint8)
        heightmap = _lighting_np.zeros(1, dtype=_lighting_np.uint8)
        _numba_initialize_skylight(blocks, sky, heightmap, opacity_lut, 1, 1, 8)
        _numba_propagate_light(blocks, sky, opacity_lut, light_lut, 1, 1, 8, False)
        _numba_propagate_light(blocks, block, opacity_lut, light_lut, 1, 1, 8, True)
        if _numba_relight_block_region is not None:
            _numba_relight_block_region(
                _lighting_np.zeros(27, dtype=_lighting_np.uint8),
                _lighting_np.zeros(27, dtype=_lighting_np.uint8),
                _lighting_np.zeros(9, dtype=_lighting_np.uint8),
                opacity_lut, light_lut, 3, 3, 3,
            )
        if _numba_relight_sky_region is not None:
            _numba_relight_sky_region(
                _lighting_np.zeros(8, dtype=_lighting_np.uint8),
                _lighting_np.zeros(8, dtype=_lighting_np.uint8),
                _lighting_np.ones(4, dtype=_lighting_np.uint8),
                _lighting_np.ones(4, dtype=_lighting_np.uint8),
                _lighting_np.zeros(8, dtype=_lighting_np.uint8),
                opacity_lut, 2, 2, 2,
            )
        if TERRAIN_NUMBA_AVAILABLE:
            _numba_surface_heights(blocks, 1, 1, 8, 1, -1)

    def _get_lighting_luts(self):
        if not LIGHTING_NUMBA_AVAILABLE:
            return None, None
        palette_size = len(self.id_to_block_type)
        if self._lighting_lut_cache is None or self._lighting_lut_size < palette_size:
            opacity = [0] * palette_size
            emission = [0] * palette_size
            for block_id, block_type in enumerate(self.id_to_block_type):
                if not block_type:
                    continue
                definition = Block._block_defs.get(block_type, {})
                opacity[block_id] = max(0, min(255, int(definition.get("lightOpacity", 255))))
                emission[block_id] = max(0, min(15, int(definition.get("lightValue", 0))))
            self._lighting_lut_cache = (
                _lighting_np.asarray(opacity, dtype=_lighting_np.uint8),
                _lighting_np.asarray(emission, dtype=_lighting_np.uint8),
            )
            self._lighting_lut_size = palette_size
        return self._lighting_lut_cache

    def _init_chunk_lighting(self, chunk):
        started = time.perf_counter()
        try:
            return self._init_chunk_lighting_impl(chunk)
        finally:
            self.profiler.record_since("lighting.chunk", started)

    def _init_chunk_lighting_impl(self, chunk):
        Block._load_defs()
        if not any(chunk.blocks):
            height = chunk.height
            chunk.heightmap = bytearray(chunk.width * chunk.depth)  
            sky_value = 0 if self.is_hell_world else 15
            chunk.skylight = bytearray(bytes((sky_value,)) * (chunk.width * chunk.depth * height))
            chunk.blocklight = bytearray(chunk.width * chunk.depth * height)
            chunk.light_ready = True
            self.note_chunk_mesh_dirty((chunk.cx, chunk.cz))
            self._invalidate_loaded_chunk_neighbors(chunk.cx, chunk.cz)
            return
        chunk.compute_heightmap_and_skylight()
        if self.is_hell_world:
            chunk.skylight = bytearray(chunk.width * chunk.depth * chunk.height)
        else:
            self._propagate_skylight_chunk(chunk)
        self._flood_block_light_chunk(chunk)
        self._reconcile_loaded_chunk_lighting(chunk)
        self.note_chunk_mesh_dirty((chunk.cx, chunk.cz))
        self._invalidate_loaded_chunk_neighbors(chunk.cx, chunk.cz)

    def _propagate_skylight_chunk(self, chunk):
        if LIGHTING_NUMBA_AVAILABLE:
            opacity_lut, light_lut = self._get_lighting_luts()
            _numba_propagate_light(
                _lighting_np.frombuffer(chunk.blocks, dtype=_lighting_np.uint8),
                _lighting_np.frombuffer(chunk.skylight, dtype=_lighting_np.uint8),
                opacity_lut, light_lut, chunk.width, chunk.depth, chunk.height, False,
            )
            return

        width, depth, height = chunk.width, chunk.depth, chunk.height
        buckets = [deque() for _ in range(16)]
        pending = [set() for _ in range(16)]

        def enqueue(level, pos):
            if level > 1 and pos not in pending[level]:
                pending[level].add(pos)
                buckets[level].append(pos)

        for lx in range(width):
            for lz in range(depth):
                base = chunk._local_index(lx, 0, lz)
                for y in range(height):
                    level = int(chunk.skylight[base + y])
                    enqueue(level, (lx, y, lz))

        directions = ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                      (0, -1, 0), (0, 0, 1), (0, 0, -1))
        for level in range(15, 1, -1):
            bucket, level_pending = buckets[level], pending[level]
            while bucket:
                lx, y, lz = bucket.popleft()
                level_pending.discard((lx, y, lz))
                idx = chunk._local_index(lx, y, lz)
                cur = int(chunk.skylight[idx])
                if cur != level:
                    continue
                for dx, dy, dz in directions:
                    nx, ny, nz = lx + dx, y + dy, lz + dz
                    if not (0 <= nx < width and 0 <= nz < depth and 0 <= ny < height):
                        continue
                    nidx = chunk._local_index(nx, ny, nz)
                    opacity = chunk._opacity_for_id(chunk.blocks[nidx])
                    if opacity == 0:
                        opacity = 1
                    new_light = max(0, cur - opacity)
                    if new_light > int(chunk.skylight[nidx]):
                        chunk.skylight[nidx] = new_light
                        enqueue(new_light, (nx, ny, nz))

    def _flood_block_light_chunk(self, chunk):
        if LIGHTING_NUMBA_AVAILABLE:
            opacity_lut, light_lut = self._get_lighting_luts()
            _numba_propagate_light(
                _lighting_np.frombuffer(chunk.blocks, dtype=_lighting_np.uint8),
                _lighting_np.frombuffer(chunk.blocklight, dtype=_lighting_np.uint8),
                opacity_lut, light_lut, chunk.width, chunk.depth, chunk.height, True,
            )
            return

        chunk.blocklight[:] = bytes(len(chunk.blocklight))
        chunk.seed_block_lights()
        self._propagate_block_light_chunk(chunk)

    def _propagate_block_light_chunk(self, chunk):
        if LIGHTING_NUMBA_AVAILABLE:
            opacity_lut, light_lut = self._get_lighting_luts()
            _numba_propagate_light(
                _lighting_np.frombuffer(chunk.blocks, dtype=_lighting_np.uint8),
                _lighting_np.frombuffer(chunk.blocklight, dtype=_lighting_np.uint8),
                opacity_lut, light_lut, chunk.width, chunk.depth, chunk.height, False,
            )
            return

        width, depth, height = chunk.width, chunk.depth, chunk.height
        blocklight = chunk.blocklight
        buckets = [deque() for _ in range(16)]
        pending = [set() for _ in range(16)]

        def enqueue(level, pos):
            if level > 1 and pos not in pending[level]:
                pending[level].add(pos)
                buckets[level].append(pos)

        for lx in range(width):
            for lz in range(depth):
                base = chunk._local_index(lx, 0, lz)
                for y in range(height):
                    enqueue(int(blocklight[base + y]), (lx, y, lz))

        directions = ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                      (0, -1, 0), (0, 0, 1), (0, 0, -1))
        for level in range(15, 1, -1):
            bucket, level_pending = buckets[level], pending[level]
            while bucket:
                lx, y, lz = bucket.popleft()
                level_pending.discard((lx, y, lz))
                idx = chunk._local_index(lx, y, lz)
                cur = int(blocklight[idx])
                if cur != level:
                    continue
                for dx, dy, dz in directions:
                    nx, ny, nz = lx + dx, y + dy, lz + dz
                    if not (0 <= nx < width and 0 <= nz < depth and 0 <= ny < height):
                        continue
                    nidx = chunk._local_index(nx, ny, nz)
                    opacity = chunk._opacity_for_id(chunk.blocks[nidx])
                    if opacity == 0:
                        opacity = 1
                    new_light = max(0, cur - opacity)
                    if new_light > int(blocklight[nidx]):
                        blocklight[nidx] = new_light
                        enqueue(new_light, (nx, ny, nz))

    @staticmethod
    def _transfer_border_light(source, target, source_idx, target_idx, light_attr):
        source_light = getattr(source, light_attr)
        target_light = getattr(target, light_attr)
        level = int(source_light[source_idx])
        if level <= 1:
            return False
        opacity = target._opacity_for_id(target.blocks[target_idx])
        if opacity == 0:
            opacity = 1
        candidate = max(0, level - opacity)
        if candidate > int(target_light[target_idx]):
            target_light[target_idx] = candidate
            return True
        return False

    def _sync_chunk_lighting_border(self, first, second, axis):
        changed_first = False
        changed_second = False
        height = min(first.height, second.height)
        if axis == "x":
            first_x, second_x = first.width - 1, 0
            for along in range(min(first.depth, second.depth)):
                for y in range(height):
                    first_idx = first._local_index(first_x, y, along)
                    second_idx = second._local_index(second_x, y, along)
                    for light_attr in ("skylight", "blocklight"):
                        changed_second |= self._transfer_border_light(
                            first, second, first_idx, second_idx, light_attr
                        )
                        changed_first |= self._transfer_border_light(
                            second, first, second_idx, first_idx, light_attr
                        )
        else:
            first_z, second_z = first.depth - 1, 0
            for along in range(min(first.width, second.width)):
                for y in range(height):
                    first_idx = first._local_index(along, y, first_z)
                    second_idx = second._local_index(along, y, second_z)
                    for light_attr in ("skylight", "blocklight"):
                        changed_second |= self._transfer_border_light(
                            first, second, first_idx, second_idx, light_attr
                        )
                        changed_first |= self._transfer_border_light(
                            second, first, second_idx, first_idx, light_attr
                        )
        return changed_first, changed_second

    def _reconcile_loaded_chunk_lighting(self, source_chunk):
        source_key = (source_chunk.cx, source_chunk.cz)
        queue = deque([source_key])
        queued = {source_key}
        changed_chunks = {source_key}
        skip_source_first_pass = True

        def enqueue(key):
            if key not in queued:
                queued.add(key)
                queue.append(key)

        while queue:
            key = queue.popleft()
            queued.discard(key)
            current = self.chunks.get(key)
            if current is None or not current.light_ready:
                continue

            if key == source_key and skip_source_first_pass:
                skip_source_first_pass = False
            else:
                self._propagate_skylight_chunk(current)
                self._propagate_block_light_chunk(current)

            adjacent = (
                ((key[0] - 1, key[1]), "x", "neighbor-first"),
                ((key[0] + 1, key[1]), "x", "current-first"),
                ((key[0], key[1] - 1), "z", "neighbor-first"),
                ((key[0], key[1] + 1), "z", "current-first"),
            )
            for neighbor_key, axis, ordering in adjacent:
                neighbor = self.chunks.get(neighbor_key)
                if neighbor is None or not neighbor.light_ready:
                    continue
                if ordering == "neighbor-first":
                    changed_neighbor, changed_current = self._sync_chunk_lighting_border(
                        neighbor, current, axis
                    )
                else:
                    changed_current, changed_neighbor = self._sync_chunk_lighting_border(
                        current, neighbor, axis
                    )
                if changed_current:
                    changed_chunks.add(key)
                    enqueue(key)
                if changed_neighbor:
                    changed_chunks.add(neighbor_key)
                    enqueue(neighbor_key)

        for key in changed_chunks:
            chunk = self.chunks.get(key)
            if chunk is not None:
                chunk.mark_all_dirty()

    def _decorate_chunk(self, cx, cz):
        started = time.perf_counter()
        try:
            result = self._decorate_chunk_impl(cx, cz)
            if (cx, cz) in self.chunks:
                self._populated_chunks.add((cx, cz))
                self.chunks[(cx, cz)].terrain_populated = True
            return result
        finally:
            self.profiler.record_since("population.chunk", started)

    def _decorate_chunk_impl(self, cx, cz):
        if (cx, cz) not in self.chunks:
            return
        if self.debug_world:
            return
        if self.is_hell_world:
            self.terrain.populate_chunk(self, cx, cz)
            return
        if isinstance(self.terrain, AlphaTerrainGenerator):
            self.terrain.populate_chunk(self, cx, cz)
            return

        decor_seed = (
            (self.seed & 0xFFFFFFFFFFFFFFFF)
            ^ ((cx * 341873128712) & 0xFFFFFFFFFFFFFFFF)
            ^ ((cz * 132897987541) & 0xFFFFFFFFFFFFFFFF)
            ^ 0x5DEECE66D
        )
        tree_rng = random.Random(decor_seed ^ 0x4F9939F508)
        flower_rng = random.Random(decor_seed ^ 0x1EF1565BD5)
        start_x = cx * self.CHUNK_SIZE
        start_z = cz * self.CHUNK_SIZE

        self._placing_generated_features = True
        try:
            density = _noise2(cx * 0.5, cz * 0.5, self.seed + 733)
            tree_count = max(0, int((density * 2.0 + tree_rng.random() * 4.0 + 4.0) / 3.0))
            if tree_rng.randrange(10) == 0:
                tree_count += 1

            for _ in range(tree_count):
                x = start_x + tree_rng.randrange(2, self.CHUNK_SIZE - 2)
                z = start_z + tree_rng.randrange(2, self.CHUNK_SIZE - 2)
                surface_y = self.terrain.height_at(x, z)
                if surface_y <= self.SEA_LEVEL + 1:
                    continue
                tree_world = self
                if tree_rng.randrange(10) == 0:
                    generated = generate_alpha_big_tree(
                        tree_world, x, surface_y + 1, z, tree_rng,
                        self._is_opaque_tree_block, self.CHUNK_HEIGHT,
                    )
                    if not generated:
                        generate_alpha_tree(
                            tree_world, x, surface_y + 1, z, tree_rng,
                            self._is_opaque_tree_block, self.CHUNK_HEIGHT,
                        )
                else:
                    generate_alpha_tree(
                        tree_world, x, surface_y + 1, z, tree_rng,
                        self._is_opaque_tree_block, self.CHUNK_HEIGHT,
                    )

            for lx in range(self.CHUNK_SIZE):
                wx = start_x + lx
                for lz in range(self.CHUNK_SIZE):
                    wz = start_z + lz
                    roll = flower_rng.random()
                    if not (0.0105 <= roll < 0.026):
                        continue
                    surface_y = self.terrain.height_at(wx, wz)
                    if surface_y <= self.SEA_LEVEL + 1:
                        continue
                    if self.get_block_type(wx, surface_y, wz) != "grass_block":
                        continue
                    if self.get_block_type(wx, surface_y + 1, wz) is not None:
                        continue
                    flower = "yellow_flower" if flower_rng.random() < 0.65 else "red_flower"
                    self.set_block(wx, surface_y + 1, wz, flower)
        finally:
            self._placing_generated_features = False

    @staticmethod
    def _is_opaque_tree_block(block_type):
        if block_type is None:
            return False
        definition = Block._block_defs.get(block_type, {})
        return (
            definition.get("model", "cube") == "cube"
            and definition.get("nCull", "all") == "all"
            and definition.get("collision", True) is not False
            and not definition.get("transparent", False)
        )

    def _place_tree(self, x, base_y, z, rng):
        return generate_alpha_tree(
            self, x, base_y, z, rng, self._is_opaque_tree_block, self.CHUNK_HEIGHT
        )

    def find_spawn_position(self, search_radius=30):
        if self.debug_world:
            return (0.0, float(self.terrain.PLATFORM_Y) + 0.5, 0.0)
        if self.is_hell_world:
            return (0.5, 65.0, 0.5)
        candidate = self._precomputed_spawn_position
        if candidate is not None:
            x, _y, z = candidate
            surface_y = find_sand_spawn_surface(
                self, int(round(x)), int(round(z)), self.CHUNK_HEIGHT,
            )
            if surface_y is not None:
                return (float(x), float(surface_y) + 0.5, float(z))

        for radius in range(max(0, int(search_radius)) + 1):
            for dx in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    if max(abs(dx), abs(dz)) != radius:
                        continue
                    surface_y = find_sand_spawn_surface(
                        self, dx, dz, self.CHUNK_HEIGHT,
                    )
                    if surface_y is not None:
                        return (float(dx), float(surface_y) + 0.5, float(dz))

        surface_y = max(self.terrain.height_at(0, 0), self.SEA_LEVEL)
        return (0.0, float(surface_y) + 0.5, 0.0)

    def _mark_neighbors_dirty(self, x, y, z):
        cx, cz = self._get_chunk_index(x, z)
        self._mark_chunk_dirty(cx, cz, y=y)

        lx = x - cx * self.CHUNK_SIZE
        lz = z - cz * self.CHUNK_SIZE
        if lx == 0:
            self._mark_chunk_dirty(cx - 1, cz, y=y)
        elif lx == self.CHUNK_SIZE - 1:
            self._mark_chunk_dirty(cx + 1, cz, y=y)

        if lz == 0:
            self._mark_chunk_dirty(cx, cz - 1, y=y)
        elif lz == self.CHUNK_SIZE - 1:
            self._mark_chunk_dirty(cx, cz + 1, y=y)

        edge_dx = -1 if lx == 0 else (1 if lx == self.CHUNK_SIZE - 1 else 0)
        edge_dz = -1 if lz == 0 else (1 if lz == self.CHUNK_SIZE - 1 else 0)
        if edge_dx and edge_dz:
            self._mark_chunk_dirty(cx + edge_dx, cz + edge_dz, y=y)

    def _mark_chunk_dirty(self, cx, cz, y=None):
        chunk = self.chunks.get((cx, cz))
        if chunk is not None:
            if y is None:
                chunk.mark_all_dirty()
            else:
                chunk.mark_dirty_at(y)

    @staticmethod
    def _mark_chunk_boundary_dirty(chunk, edge):
        return mark_chunk_boundary_dirty(chunk, edge)

    def _invalidate_loaded_chunk_neighbors(self, cx, cz):
        return invalidate_loaded_chunk_neighbors(self.chunks, cx, cz)

    def _unload_chunk(self, cx, cz):
        key = (cx, cz)
        chunk = self.chunks.get(key)
        if chunk is None:
            return
        commit_state = getattr(self, "_population_commit_state", None)
        if commit_state is not None:
            protected = commit_state.get("protected_keys", ())
            if key in protected:
                return
        unload_started = time.perf_counter()
        save_started = unload_started
        save_ms = 0.0
        cleanup_started = None
        boundary_sections_marked = 0
        dirty_sections_before = len(getattr(chunk, "dirty_sections", ()))
        if self.save_store is not None:
            now = time.monotonic()
            if now < getattr(chunk, "_save_retry_after", 0.0):
                return
            saved_ok = self.save_store.queue_chunk_save_if_needed(self, chunk)
            save_ms = (time.perf_counter() - save_started) * 1000.0
            if not saved_ok:
                chunk._save_retry_after = now + 5.0
                return
            chunk._save_retry_after = 0.0
        else:
            save_ms = (time.perf_counter() - save_started) * 1000.0
        cleanup_started = time.perf_counter()

        try:
            from game.entities.item import ItemEntity
            from game.entities.falling_block import FallingBlockEntity
            from game.entities.arrow import ArrowEntity
            from game.entities.painting import PaintingEntity
            from game.world.savegame import WorldSave
            living = tuple(WorldSave._living_mob_registry().values())
            persistent_types = (ItemEntity, FallingBlockEntity, ArrowEntity, PaintingEntity) + living
            self.entities = [
                entity for entity in self.entities
                if not (isinstance(entity, persistent_types)
                        and self._get_chunk_index(entity.pos[0], entity.pos[2]) == key)
            ]
        except Exception:
            pass

        for te_key, te in list(self.tile_entities.items()):
            try:
                tx, _ty, tz = te_key
            except (TypeError, ValueError):
                tx, _ty, tz = getattr(te, "x", 0), getattr(te, "y", 0), getattr(te, "z", 0)
            if self._get_chunk_index(tx, tz) == key:
                self.tile_entities.pop(te_key, None)

        self._fluid_scheduler.on_chunk_unloaded(cx, cz)
        self._remove_fire_chunk_index(cx, cz)
        self._discard_natural_light_changes_for_chunk(cx, cz)
        redstone = getattr(self, "_redstone_system", None)
        if redstone is not None and hasattr(redstone, "on_chunk_unloaded"):
            try:
                redstone.on_chunk_unloaded(cx, cz, getattr(self, "CHUNK_SIZE", 16))
            except Exception:
                pass
        self.chunks.pop(key, None)
        chunk.release()
        cleanup_ms = (time.perf_counter() - cleanup_started) * 1000.0

        neighbor_edges = (
            ((cx - 1, cz), "east"),
            ((cx + 1, cz), "west"),
            ((cx, cz - 1), "south"),
            ((cx, cz + 1), "north"),
        )
        invalidation_started = time.perf_counter()
        for neighbor_key, edge in neighbor_edges:
            neighbor = self.chunks.get(neighbor_key)
            if neighbor is not None:
                boundary_sections_marked += self._mark_chunk_boundary_dirty(neighbor, edge)
        invalidation_ms = (time.perf_counter() - invalidation_started) * 1000.0
        self.profiler.record_event(
            "chunk.unload", cx=int(cx), cz=int(cz),
            duration_ms=round((time.perf_counter() - unload_started) * 1000.0, 3),
            save_ms=round(save_ms, 3), save_queued=bool(self.save_store is not None),
            cleanup_ms=round(cleanup_ms, 3),
            neighbor_invalidation_ms=round(invalidation_ms, 3),
            neighbor_sections_marked=int(boundary_sections_marked),
            dirty_sections=dirty_sections_before,
        )

    def _camera_frustum(self, aspect=None):
        renderer = self.renderer
        aspect = aspect or getattr(self, "_draw_aspect", 16 / 9)
        if renderer is None:
            camera_x = self._center_chunk[0] * self.CHUNK_SIZE + 8
            camera_z = self._center_chunk[1] * self.CHUNK_SIZE + 8
            camera_y = 64.0
            yaw = pitch = 0.0
            fov = 90.0
            near_clip, far_clip = 0.1, 256.0
        else:
            camera_x = float(getattr(
                renderer, "view_x",
                getattr(renderer, "camX", self._center_chunk[0] * self.CHUNK_SIZE + 8),
            ))
            camera_y = float(getattr(
                renderer, "view_y",
                getattr(renderer, "camY", getattr(self, "_player_y", 64.0)),
            ))
            camera_z = float(getattr(
                renderer, "view_z",
                getattr(renderer, "camZ", self._center_chunk[1] * self.CHUNK_SIZE + 8),
            ))
            yaw = float(getattr(renderer, "yaw", 0.0))
            pitch = float(getattr(renderer, "pitch", 0.0))
            fov = float(getattr(renderer, "fov", 90.0))
            near_clip = float(getattr(renderer, "near_clip", 0.1))
            far_clip = float(getattr(renderer, "far_clip", 256.0))
        return CameraFrustum.from_camera(
            camera_x, camera_y, camera_z, yaw, pitch, fov, aspect,
            near_clip=near_clip, far_clip=far_clip,
        )

    def _camera_column_bounds(self, frustum):
        return frustum.horizontal_bounds_in_y(-2.0, self.CHUNK_HEIGHT + 2.0)

    def get_celestial_angle(self, partial_ticks=0.0):
        if self.is_hell_world:
            return 0.5
        day = int(self.world_time % 24000)
        t = (day + float(partial_ticks)) / 24000.0 - 0.25
        if t < 0.0:
            t += 1.0
        if t > 1.0:
            t -= 1.0
        raw = t
        eased = 1.0 - (math.cos(t * math.pi) + 1.0) / 2.0
        return raw + (eased - raw) / 3.0

    def calculate_skylight_subtracted(self, partial_ticks=1.0):
        if self.is_hell_world:
            return 0
        angle = self.get_celestial_angle(partial_ticks)
        factor = 1.0 - (math.cos(angle * math.pi * 2.0) * 2.0 + 0.5)
        factor = max(0.0, min(1.0, factor))
        return int(factor * 11.0)

    def is_daytime(self) -> bool:
        if self.is_hell_world:
            return False
        return int(getattr(self.lighting, "skylight_subtracted", 0)) < 4

    def _sleep_cycle_players(self):
        from game.sleep_cycle import sleep_cycle_players
        return sleep_cycle_players(self)

    def update_all_players_sleeping_flag(self) -> bool:
        from game.sleep_cycle import update_all_players_sleeping_flag
        return update_all_players_sleeping_flag(self)

    def _wake_all_sleeping_players(self) -> None:
        from game.sleep_cycle import wake_all_sleeping_players
        wake_all_sleeping_players(self)

    def _try_skip_night(self) -> None:
        from game.sleep_cycle import try_skip_night
        try_skip_night(self)

    def get_render_skylight_subtracted(self, partial_ticks=None):
        if self.is_hell_world:
            return 0.0
        if partial_ticks is None:
            partial_ticks = self.partial_ticks()
        angle = self.get_celestial_angle(partial_ticks)
        factor = 1.0 - (math.cos(angle * math.pi * 2.0) * 2.0 + 0.5)
        return max(0.0, min(1.0, factor)) * 11.0

    def _day_brightness(self, partial_ticks=0.0):
        if self.is_hell_world:
            return 1.0
        angle = self.get_celestial_angle(partial_ticks)
        factor = math.cos(angle * math.pi * 2.0) * 2.0 + 0.5
        return max(0.0, min(1.0, factor))

    def get_biome(self, x: int, z: int):
        return self.biome_provider.get_biome(int(x), int(z))

    def get_climate(self, x: int, z: int):
        return self.biome_provider.get_climate(int(x), int(z))

    def get_sky_color(self, partial_ticks=0.0, player=None):
        if self.is_hell_world:
            return (0.2, 0.03, 0.03)
        bright = self._day_brightness(partial_ticks)
        if player is not None and getattr(player, "pos", None) is not None:
            x = math.floor(float(player.pos[0]))
            z = math.floor(float(player.pos[2]))
        else:
            x = int(getattr(self, "spawn_x", 0))
            z = int(getattr(self, "spawn_z", 0))
        provider = getattr(self, "biome_provider", None)
        if provider is None:
            c = self.sky_color
            raw = (((c >> 16) & 255), ((c >> 8) & 255), (c & 255))
        else:
            raw_temperature = provider.get_raw_temperature(x, z)
            raw = biome_sky_color(raw_temperature)
        r8, g8, b8 = raw
        r = r8 / 255.0 * bright
        g = g8 / 255.0 * bright
        b = b8 / 255.0 * bright
        return (r, g, b)

    def get_sunrise_color(self, partial_ticks=0.0):
        if self.is_hell_world:
            return None
        return sunrise_color(self.get_celestial_angle(partial_ticks))

    def get_fog_color(self, partial_ticks=0.0):
        if self.is_hell_world:
            return (0.2, 0.03, 0.03)
        bright = self._day_brightness(partial_ticks)
        c = self.fog_color
        r = ((c >> 16) & 255) / 255.0 * (bright * 0.94 + 0.06)
        g = ((c >> 8) & 255) / 255.0 * (bright * 0.94 + 0.06)
        b = (c & 255) / 255.0 * (bright * 0.91 + 0.09)
        return (r, g, b)

    def get_cloud_color(self, partial_ticks=0.0):
        if self.is_hell_world:
            return (0.0, 0.0, 0.0)
        bright = self._day_brightness(partial_ticks)
        c = self.cloud_color
        r = ((c >> 16) & 255) / 255.0 * (bright * 0.9 + 0.1)
        g = ((c >> 8) & 255) / 255.0 * (bright * 0.9 + 0.1)
        b = (c & 255) / 255.0 * (bright * 0.85 + 0.15)
        return (r, g, b)

    def get_star_brightness(self, partial_ticks=0.0):
        if self.is_hell_world:
            return 0.0
        angle = self.get_celestial_angle(partial_ticks)
        factor = 1.0 - (math.cos(angle * math.pi * 2.0) * 2.0 + 12.0 / 16.0)
        factor = max(0.0, min(1.0, factor))
        return factor * factor * 0.5

    def partial_ticks(self):
        return min(self._tick_accum / 0.05, 1.0)

    def _update_skylight_subtracted(self):
        new_sub = self.calculate_skylight_subtracted(1.0)
        if new_sub == self.lighting.skylight_subtracted:
            return
        self.lighting.skylight_subtracted = new_sub
        for chunk in self.chunks.values():
            mark_lighting_dirty = getattr(chunk, "mark_lighting_dirty", None)
            if callable(mark_lighting_dirty):
                mark_lighting_dirty()
            elif hasattr(chunk, "mark_all_dirty"):
                chunk.mark_all_dirty()

    def render_sky(self, partial_ticks=None, aspect=None, player=None):
        if partial_ticks is None:
            partial_ticks = self.partial_ticks()
        if self._sky_renderer is None:
            from game.world.sky import SkyRenderer
            self._sky_renderer = SkyRenderer()
        self._sky_renderer.render(
            self, partial_ticks, renderer=self.renderer, aspect=aspect,
            player=player,
        )

    def render_clouds(self, player, partial_ticks=None):
        if self.is_hell_world:
            return
        if partial_ticks is None:
            partial_ticks = self.partial_ticks()
        if getattr(self, "_cloud_renderer", None) is None:
            from game.world.clouds import CloudRenderer
            self._cloud_renderer = CloudRenderer()
        self._cloud_renderer.render(self, player, partial_ticks)

    def force_chunks_around(self, x, z, radius=None):
        center_cx, center_cz = self._get_chunk_index(x, z)
        self._center_chunk = (center_cx, center_cz)
        self._player_y = float(getattr(self, "_player_y", 64.0))
        load_radius = int(radius) if radius is not None else self._chunk_load_radius()
        load_sq = load_radius * load_radius
        wanted = []
        for dx in range(-load_radius, load_radius + 1):
            for dz in range(-load_radius, load_radius + 1):
                if dx * dx + dz * dz > load_sq:
                    continue
                key = (center_cx + dx, center_cz + dz)
                existing = self.chunks.get(key)
                if existing is None:
                    wanted.append(key)
                elif (not existing.light_ready
                        and key not in self._terrain_futures
                        and key != getattr(self, "_population_future_key", None)):
                    wanted.append(key)
        if wanted:
            from game.world.terrain import AlphaTerrainGenerator
            wanted.sort(key=lambda key: (
                (key[0] - center_cx) ** 2 + (key[1] - center_cz) ** 2,
                key[0], key[1],
            ))
            immediate = wanted[:min(9, len(wanted))]
            rest = [key for key in wanted[len(immediate):] if key not in self.chunks]
            for cx, cz in immediate:
                key = (cx, cz)
                if key not in self.chunks:
                    self._generate_chunk(cx, cz)
                if key not in self._populated_chunks:
                    self._decorate_chunk(cx, cz)
                self._apply_overrides(cx, cz)
                chunk = self.chunks.get(key)
                if chunk is not None:
                    self._init_chunk_lighting(chunk)
                    chunk.mark_all_dirty()
            if rest:
                self._request_chunk_terrain(rest)
        for dx in range(-load_radius, load_radius + 1):
            for dz in range(-load_radius, load_radius + 1):
                if dx * dx + dz * dz > load_sq:
                    continue
                chunk = self.chunks.get((center_cx + dx, center_cz + dz))
                if chunk is not None and chunk.light_ready:
                    chunk.mark_all_dirty()
        for _ in range(8):
            try:
                stage = self._process_one_chunk_stage()
                if stage == "population-commit-batch":
                    break
            except Exception:
                break

    _REMOTE_RANDOM_TICK_MIN_SEPARATION = 48.0
    _REMOTE_RANDOM_TICK_MAX_CENTERS = 8

    def _run_random_ticks_for_players(self, player_pos):
        if getattr(self, "_multiplayer_client_world", False):
            return None

        publish = self._network_block_callback is not None
        if publish:
            self._network_edit_depth += 1
        try:
            stats = run_random_ticks(
                self, player_pos[0], player_pos[2], rng=self._tick_rng,
            )
            centers = [(float(player_pos[0]), float(player_pos[2]))]
            min_sep_sq = self._REMOTE_RANDOM_TICK_MIN_SEPARATION ** 2
            for pos in getattr(self, "_mp_remote_player_positions", ()) or ():
                if len(centers) > self._REMOTE_RANDOM_TICK_MAX_CENTERS:
                    break
                try:
                    rx, rz = float(pos[0]), float(pos[2])
                except (IndexError, TypeError, ValueError):
                    continue
                if not (math.isfinite(rx) and math.isfinite(rz)):
                    continue
                if any((rx - cx) ** 2 + (rz - cz) ** 2 < min_sep_sq for cx, cz in centers):
                    continue
                centers.append((rx, rz))
                run_random_ticks(self, rx, rz, rng=self._tick_rng)
            return stats
        finally:
            if publish:
                self._network_edit_depth -= 1

    def update(self, player_pos, dt=0.0):
        if self.save_store is not None:
            self.save_store.poll_pending_saves()

        self._particle_viewer_pos = tuple(float(v) for v in player_pos)

        fluid_budget_remaining_ms = 4.0
        if dt > 0.0:
            self._tick_accum += min(dt, 0.25)
            ticks = 0
            while self._tick_accum >= 0.05 and ticks < 4:
                self._tick_accum -= 0.05
                self.world_time += 1
                ticks += 1
                self._try_skip_night()
                if getattr(self, "_cloud_renderer", None) is not None:
                    self._cloud_renderer.update()
                else:
                    self._cloud_tick_pending = getattr(self, "_cloud_tick_pending", 0) + 1
                self._update_skylight_subtracted()
                if self._tick_rng is None:
                    self._tick_rng = random.Random(self.seed ^ 0xC0FFEE)
                self._processing_natural_tick = True
                try:
                    fluid_frame_budget_ms = max(0.0, fluid_budget_remaining_ms)
                    fluid_started = time.perf_counter()
                    fluid_processed = self._fluid_scheduler.process_due(
                        rng=self._tick_rng, limit=96,
                        budget_ms=fluid_frame_budget_ms,
                    )
                    fluid_elapsed_ms = (time.perf_counter() - fluid_started) * 1000.0
                    fluid_budget_remaining_ms = max(
                        0.0, fluid_budget_remaining_ms - fluid_elapsed_ms,
                    )
                    fluid_details = dict(getattr(self._fluid_scheduler, "last_batch_stats", {}))
                    fluid_details.update({
                        "processed": int(fluid_processed),
                        "pending": int(self._fluid_scheduler.pending_count),
                        "heap": len(self._fluid_scheduler._heap),
                        "active_cells": len(self._fluid_scheduler._moving_cells),
                        "frame_budget_ms": fluid_frame_budget_ms,
                        "deferred_natural_light_pending": len(self._pending_natural_light_changes),
                    })
                    self.profiler.record_ms(
                        "fluid.tick_batch", fluid_elapsed_ms,
                        event_details=fluid_details,
                    )
                    random_started = time.perf_counter()
                    self._run_block_updates()
                    redstone = getattr(self, "_redstone_system", None)
                    if redstone is not None:
                        publish_redstone = (
                            self._network_block_callback is not None
                            and not bool(getattr(self, "_multiplayer_client_world", False))
                        )
                        if publish_redstone:
                            self._network_edit_depth += 1
                        try:
                            redstone.tick(player_pos)
                        finally:
                            if publish_redstone:
                                self._network_edit_depth = max(
                                    0, self._network_edit_depth - 1,
                                )
                    random_stats = self._run_random_ticks_for_players(player_pos)
                    self.profiler.record_ms(
                        "random.tick_batch",
                        (time.perf_counter() - random_started) * 1000.0,
                        event_details=random_stats if isinstance(random_stats, dict) else None,
                    )
                    self.update_tile_entities()
                    from game.entities.spawning import spawn_natural_mobs
                    spawn_natural_mobs(self, self._tick_rng)
                    display_started = time.perf_counter()
                    self._run_random_display_updates(player_pos, samples=1000)
                    self.profiler.record_ms(
                        "particle.display_tick", (time.perf_counter() - display_started) * 1000.0,
                        event_details={"samples": 1000},
                    )
                finally:
                    self._processing_natural_tick = False

        center_cx, center_cz = self._get_chunk_index(player_pos[0], player_pos[2])
        prev_center = self._center_chunk
        self._center_chunk = (center_cx, center_cz)
        self._player_y = float(player_pos[1])

        load_radius = self._chunk_load_radius()
        unload_radius = load_radius + 1
        load_sq = load_radius * load_radius
        unload_sq = unload_radius * unload_radius

        unload_candidates = []
        commit_state = getattr(self, "_population_commit_state", None)
        protected_population_chunks = (
            set(commit_state.get("protected_keys", ())) if commit_state else set()
        )
        retry_now = time.monotonic()
        network_stream_keys = getattr(self, "_mp_chunk_stream_keys", set())
        for key, chunk in self.chunks.items():
            cx, cz = key
            if key in protected_population_chunks:
                continue
            if key in network_stream_keys:
                continue
            dx, dz = cx - center_cx, cz - center_cz
            if dx * dx + dz * dz <= unload_sq:
                continue
            if retry_now < getattr(chunk, "_save_retry_after", 0.0):
                continue
            unload_candidates.append((dx * dx + dz * dz, key))
        if unload_candidates:
            unload_candidates.sort(key=lambda item: item[0], reverse=True)
            excess = len(unload_candidates)
            budget = 1 if excess <= 2 else min(16, 2 + excess // 3)
            for _distance_squared, unload_key in unload_candidates[:budget]:
                self._unload_chunk(*unload_key)

        network_stream_keys = getattr(self, "_mp_chunk_stream_keys", set())
        for key, future in list(self._terrain_futures.items()):
            dx, dz = key[0] - center_cx, key[1] - center_cz
            if dx * dx + dz * dz > load_sq and key not in network_stream_keys:
                future.cancel()
                self._terrain_futures.pop(key, None)
                self._chunk_save_wait_started.pop(key, None)
                self._terrain_requested_at.pop(key, None)
                self._terrain_ready_at.pop(key, None)


        self._process_deferred_natural_light_changes()

        self._process_light_queue(full=False)
        self._process_one_chunk_stage()
        self._poll_chunk_terrain(center_cx, center_cz, load_sq)

        if (
            prev_center == self._center_chunk
            and self._chunk_ring_is_loaded(center_cx, center_cz, load_radius)
        ):
            return

        wanted = []
        for dx in range(-load_radius, load_radius + 1):
            for dz in range(-load_radius, load_radius + 1):
                if dx * dx + dz * dz > load_sq:
                    continue
                key = (center_cx + dx, center_cz + dz)
                if (key not in self.chunks and key not in self._terrain_futures
                        and key not in self._chunk_load_failures):
                    wanted.append(key)

        if not wanted:
            return

        wanted.sort(key=lambda key: (
            (key[0] - center_cx) ** 2 + (key[1] - center_cz) ** 2,
            key[0], key[1],
        ))
        self._request_chunk_terrain(wanted)

    def _natural_light_section_is_pending(self, cx, cz, section_idx):
        info = self._active_natural_light_info
        if info is None:
            return False
        x, y, z = info["key"]
        origin_chunk = self._get_chunk_index(x, z)
        phase = info.get("phase", "column")
        details = info.get("phase_details", {})
        if phase == "block":
            radius = int(details.get("block_radius", 12))
            y_min = max(0, y - radius)
            y_max = min(self.CHUNK_HEIGHT - 1, y + radius)
            block_origin_column = False
        else:
            radius = int(details.get("sky_radius", 14))
            y_min = max(0, y - radius - 1)
            y_max = min(self.CHUNK_HEIGHT - 1, y + radius + 1)
            block_origin_column = (cx, cz) == origin_chunk
        chunk = self.chunks.get((cx, cz))
        if chunk is None:
            return False
        x0, x1 = chunk.start_x, chunk.start_x + chunk.width - 1
        z0, z1 = chunk.start_z, chunk.start_z + chunk.depth - 1
        dx = 0 if x0 <= x <= x1 else min(abs(x - x0), abs(x - x1))
        dz = 0 if z0 <= z <= z1 else min(abs(z - z0), abs(z - z1))
        if dx * dx + dz * dz > radius * radius:
            return False
        if block_origin_column:
            return True
        section_y0 = int(section_idx) * chunk.section_height
        section_y1 = section_y0 + chunk.section_height - 1
        return section_y0 <= y_max and section_y1 >= y_min


    def note_chunk_mesh_dirty(self, key):
        dirty_keys = getattr(self, "_dirty_mesh_keys", None)
        if dirty_keys is None:
            dirty_keys = set()
            self._dirty_mesh_keys = dirty_keys
        dirty_keys.add(key)

    def draw(self, aspect=16 / 9):
        if self.headless:
            return
        self._draw_aspect = aspect
        if self._center_chunk is None:
            viewer_pos = getattr(self, "_particle_viewer_pos", None)
            if viewer_pos is not None:
                self._center_chunk = self._get_chunk_index(
                    viewer_pos[0], viewer_pos[2],
                )
            else:
                self._center_chunk = getattr(
                    self, "_initial_center_chunk", (0, 0),
                )
        center_chunk = self.chunks.get(self._center_chunk)
        section_height = (
            center_chunk.section_height if center_chunk is not None else 16
        )
        section_count = (
            self.CHUNK_HEIGHT + section_height - 1
        ) // section_height
        self._center_section = max(
            0,
            min(
                section_count - 1,
                int(getattr(self, "_player_y", section_height * 4) // section_height),
            ),
        )
        dirty_keys = getattr(self, "_dirty_mesh_keys", None)
        if dirty_keys is None:
            dirty_keys = set()
            self._dirty_mesh_keys = dirty_keys
        if not dirty_keys:
            for key, chunk in self.chunks.items():
                if chunk.dirty or chunk.dirty_sections:
                    dirty_keys.add(key)
        jobs = []
        stale = []
        for key in dirty_keys:
            chunk = self.chunks.get(key)
            if chunk is None:
                stale.append(key)
                continue
            if not chunk.light_ready:
                continue
            if chunk.dirty and not chunk.dirty_sections:
                chunk.mark_all_dirty()
            if not chunk.dirty:
                stale.append(key)
                continue
            distance = (key[0] - self._center_chunk[0]) ** 2 + (key[1] - self._center_chunk[1]) ** 2
            for section_idx in chunk.dirty_sections:
                if self._natural_light_section_is_pending(key[0], key[1], section_idx):
                    continue
                jobs.append((distance, key[0], key[1], abs(section_idx - self._center_section), section_idx, key))
        for key in stale:
            dirty_keys.discard(key)

        jobs = select_mesh_build_jobs(jobs, self.mesh_builds_per_frame, self.chunks)

        mesh_frame_start = time.perf_counter()
        built_this_frame = 0
        for _distance, _cx, _cz, _ydistance, section_idx, key in jobs:
            if built_this_frame >= self.mesh_builds_per_frame:
                break
            elapsed_ms = (time.perf_counter() - mesh_frame_start) * 1000.0
            if built_this_frame and elapsed_ms + self._mesh_build_avg_ms > self.mesh_time_budget_ms:
                break
            chunk = self.chunks.get(key)
            if chunk is None or section_idx not in chunk.dirty_sections:
                continue
            build_start = time.perf_counter()
            chunk._build_mesh(section_idx)
            build_ms = (time.perf_counter() - build_start) * 1000.0
            self._mesh_build_avg_ms = 0.8 * self._mesh_build_avg_ms + 0.2 * build_ms
            built_this_frame += 1
        self.profiler.record_ms("mesh.frame_build", (time.perf_counter() - mesh_frame_start) * 1000.0)

        aspect = getattr(self, "_draw_aspect", 16 / 9)
        frustum = self._camera_frustum(aspect)
        center_cx, center_cz = self._center_chunk
        cull_started = time.perf_counter()
        active_center = getattr(self, "_active_view_center", None)
        active_keys = getattr(self, "_active_view_keys", None)
        active_count = getattr(self, "_active_view_chunk_count", -1)
        if (active_keys is None or active_center != (center_cx, center_cz)
                or active_count != len(self.chunks)):
            radius = max(0, int(getattr(self, "view_distance", 0)))
            keys = []
            for dx in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    key = (center_cx + dx, center_cz + dz)
                    if key in self.chunks:
                        keys.append(key)
            self._active_view_keys = keys
            self._active_view_center = (center_cx, center_cz)
            self._active_view_chunk_count = len(self.chunks)
            active_keys = keys
            self._cull_cache_key = None

        batch = self._cull_visible_batch(frustum, active_keys)
        self.profiler.record_ms(
            "render.chunk_cull", (time.perf_counter() - cull_started) * 1000.0,
            event_details={"loaded_chunks": len(self.chunks), "visible_chunks": len(batch)},
        )
        self._visible_batch = batch
        with smooth_vertex_colors():
            Chunk.draw_batch(batch, phase="opaque")

    def _cull_geometry(self, active_keys):
        geom = getattr(self, "_cull_geom", None)
        chunks_get = self.chunks.get
        if (geom is not None and getattr(self, "_cull_cache_key", None) is not None
                and geom["keys"] is active_keys
                and all(map(operator.is_, geom["chunks"], map(chunks_get, active_keys)))):
            return geom
        chunks = [chunks_get(key) for key in active_keys]
        if any(chunk is None for chunk in chunks):
            chunks = [chunk for chunk in chunks if chunk is not None]
            keys = [(chunk.cx, chunk.cz) for chunk in chunks]
        else:
            keys = active_keys
        if not chunks:
            geom = {"keys": active_keys, "chunks": [], "empty": True}
            self._cull_geom = geom
            self._cull_cache_key = "geometry"
            return geom
        first = chunks[0]
        section_count = first.section_count
        section_height = first.section_height
        if (section_count > MAX_MASKED_SECTIONS
                or any(c.section_count != section_count or c.section_height != section_height
                       for c in chunks)):
            self._cull_geom = None
            return None
        cs = self.CHUNK_SIZE
        geom = {
            "keys": active_keys,
            "chunks": chunks,
            "empty": False,
            "x0": _np.array([cx * cs for cx, _cz in keys], dtype=_np.float64),
            "z0": _np.array([cz * cs for _cx, cz in keys], dtype=_np.float64),
            "masks": _np.zeros(len(chunks), dtype=_np.int64),
            "section_count": section_count,
            "section_height": section_height,
        }
        self._cull_geom = geom
        self._cull_cache_key = "geometry"
        return geom

    def _cull_visible_batch(self, frustum, active_keys):
        geom = self._cull_geometry(active_keys)
        if geom is None:
            return self._cull_visible_batch_python(frustum, active_keys)
        if geom["empty"]:
            return []
        masks = geom["masks"]
        cull_section_masks(
            _np.asarray(frustum.planes, dtype=_np.float64),
            geom["x0"], geom["z0"],
            geom["section_count"], geom["section_height"],
            self.CHUNK_HEIGHT, self.CHUNK_SIZE, 2.0, masks,
        )
        section_count = geom["section_count"]
        return [
            (chunk, sections_from_mask(mask, section_count))
            for chunk, mask in zip(geom["chunks"], masks.tolist())
            if mask
        ]

    def _cull_visible_batch_python(self, frustum, active_keys):
        batch = []
        for key in active_keys:
            chunk = self.chunks.get(key)
            if chunk is None:
                continue
            cx, cz = key
            if not chunk_in_frustum(
                frustum, cx, cz,
                chunk_size=self.CHUNK_SIZE,
                world_height=self.CHUNK_HEIGHT,
                padding=2.0,
            ):
                continue
            sections = visible_chunk_sections(
                frustum, cx, cz, chunk_size=self.CHUNK_SIZE,
                world_height=self.CHUNK_HEIGHT,
                section_height=chunk.section_height, padding=2.0,
            )
            if sections:
                batch.append((chunk, sections))
        return batch

    def draw_transparent(self):
        batch = getattr(self, "_visible_batch", None)
        self._visible_batch = None
        if batch:
            with smooth_vertex_colors():
                Chunk.draw_batch(batch, phase="transparent")

    def _chunk_in_view(self, cx, cz, aspect=16 / 9, frustum=None):
        if frustum is None:
            frustum = self._camera_frustum(aspect)
        return chunk_in_frustum(
            frustum, cx, cz,
            chunk_size=self.CHUNK_SIZE,
            world_height=self.CHUNK_HEIGHT,
            padding=2.0,
        )



class WorldFeature:
    def __init__(self):
        self.cache = {}

    def load_features(self, directory='game/data/features'):
        if not os.path.exists(directory): return
        for filename in os.listdir(directory):
            if '.c3df' in filename:
                filepath = os.path.join(directory, filename)
                name = filename.replace('.c3df', '')
                self.cache[name] = self.decode_feature(filepath)

    def decode_feature(self, filepath):
        f_data = {'w': 0, 'h': 0, 'd': 0, 'ox': 0, 'oy': 0, 'oz': 0, 'key': {}, 'blocks': {}}
        with open(filepath, 'r') as f:
            lines = [l.strip() for l in f if l.strip()]

        layer_y = 0
        i = 0
        while i < len(lines):
            line = lines[i]
            if ':' in line and not line.startswith('key'):
                k, v = line.split(':', 1)
                if k in 'whdxyz': f_data[k if k in 'whd' else 'o' + k] = int(v.strip())
            elif line.startswith('key:'):
                f_data['key'] = json.loads(line.split(':', 1)[1])
            elif line == 'BEGIN_LAYER':
                i += 1
                raw = ""
                while i < len(lines) and lines[i] != 'END_LAYER':
                    raw += lines[i] + " "
                    i += 1
                tokens = [t.strip() for t in raw.split(',') if t.strip()]
                for idx, val in enumerate(tokens):
                    lx, lz = idx % f_data['w'], idx // f_data['w']
                    if val != '0': f_data['blocks'][(lx, layer_y, lz)] = val
                layer_y += 1
            i += 1
        return f_data

    def place_feature(self, world, name, wx, wy, wz):
        if name not in self.cache: return
        f = self.cache[name]
        for (lx, ly, lz), val in f['blocks'].items():
            target_x = wx + (lx - f['ox'])
            target_y = wy + (ly - f['oy'])
            target_z = wz + (lz - f['oz'])
            block_type = f['key'].get(val)
            if block_type:
                world.set_block(target_x, target_y, target_z, block_type)