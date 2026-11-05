from __future__ import annotations


class PopulationChunkSnapshot:
    def __init__(self, chunk, id_to_block_type):
        self.cx = chunk.cx
        self.cz = chunk.cz
        self.width = chunk.width
        self.height = chunk.height
        self.depth = chunk.depth
        self.start_x = chunk.start_x
        self.start_z = chunk.start_z
        self.blocks = bytearray(chunk.blocks)
        self.skylight = bytearray(getattr(chunk, "skylight", ()))
        self.blocklight = bytearray(getattr(chunk, "blocklight", ()))
        self._id_to_block_type = id_to_block_type

    def get_block_type(self, x, y, z):
        if y < 0 or y >= self.height:
            return None
        lx = x - self.start_x
        lz = z - self.start_z
        if not (0 <= lx < self.width and 0 <= lz < self.depth):
            return None
        block_id = self.blocks[(lx * self.depth + lz) * self.height + y]
        if block_id == 0 or block_id >= len(self._id_to_block_type):
            return None
        return self._id_to_block_type[block_id]

    def set_block_type(self, x, y, z, block_type, id_for_type):
        if y < 0 or y >= self.height:
            return
        lx = x - self.start_x
        lz = z - self.start_z
        if not (0 <= lx < self.width and 0 <= lz < self.depth):
            return
        block_id = 0 if block_type is None else id_for_type(block_type)
        self.blocks[(lx * self.depth + lz) * self.height + y] = block_id


class PopulationWorldSnapshot:

    POPULATION_CHUNK_RADIUS = 1

    def __init__(self, world, focus_key=None):
        self.seed = world.seed
        self.CHUNK_SIZE = world.CHUNK_SIZE
        self.CHUNK_HEIGHT = world.CHUNK_HEIGHT
        self.SEA_LEVEL = world.SEA_LEVEL
        self.renderer = None
        self.profiler = world.profiler
        self.block_type_to_id = dict(world.block_type_to_id)
        self.id_to_block_type = list(world.id_to_block_type)
        self._placing_generated_features = False
        self._terrain_futures = dict(getattr(world, "_terrain_futures", {}))
        self._opaque_tree_predicate = world._is_opaque_tree_block

        if focus_key is None:
            selected_keys = set(world.chunks) | set(world._worldgen_block_overrides)
        else:
            focus_cx, focus_cz = map(int, focus_key)
            radius = self.POPULATION_CHUNK_RADIUS
            selected_keys = {
                (focus_cx + dx, focus_cz + dz)
                for dx in range(-radius, radius + 1)
                for dz in range(-radius, radius + 1)
            }
        self.focus_key = None if focus_key is None else tuple(map(int, focus_key))
        self.chunks = {
            key: PopulationChunkSnapshot(world.chunks[key], self.id_to_block_type)
            for key in selected_keys if key in world.chunks
        }
        self._original_worldgen_block_overrides = {
            key: dict(values)
            for key, values in world._worldgen_block_overrides.items()
            if key in selected_keys
        }
        self._worldgen_block_overrides = {
            key: dict(values)
            for key, values in self._original_worldgen_block_overrides.items()
        }
        self._writes = {}
        self._generated_chest_loot = {}
        self._generated_spawner_mobs = {}

    def _chunk_index(self, x, z):
        import math
        return (math.floor((int(x) + 0.5) / self.CHUNK_SIZE),
                math.floor((int(z) + 0.5) / self.CHUNK_SIZE))

    def get_block_type(self, x, y, z):
        if y < 0 or y >= self.CHUNK_HEIGHT:
            return None
        chunk = self.chunks.get(self._chunk_index(x, z))
        return None if chunk is None else chunk.get_block_type(int(x), int(y), int(z))

    def get_light(self, x, y, z, *, use_neighbor_max=True):
        if y < 0 or y >= self.CHUNK_HEIGHT:
            return 0
        chunk = self.chunks.get(self._chunk_index(x, z))
        if chunk is None:
            return 0
        lx = int(x) - chunk.start_x
        lz = int(z) - chunk.start_z
        if not (0 <= lx < chunk.width and 0 <= lz < chunk.depth):
            return 0
        index = (lx * chunk.depth + lz) * chunk.height + int(y)
        sky = chunk.skylight[index] if index < len(chunk.skylight) else 0
        block = chunk.blocklight[index] if index < len(chunk.blocklight) else 0
        return max(int(sky), int(block))

    def _ensure_block_type_id(self, block_type):
        block_type = str(block_type)
        block_id = self.block_type_to_id.get(block_type)
        if block_id is not None:
            return block_id
        if len(self.id_to_block_type) >= 256:
            raise ValueError("This compact chunk format supports at most 255 block types.")
        block_id = len(self.id_to_block_type)
        self.id_to_block_type.append(block_type)
        self.block_type_to_id[block_type] = block_id
        return block_id

    def _is_opaque_tree_block(self, block_type):
        return self._opaque_tree_predicate(block_type)

    def set_block(self, x, y, z, block_type, meta=0, immediate_mesh=False):
        if y < 0 or y >= self.CHUNK_HEIGHT:
            return
        if block_type is not None and not isinstance(block_type, str):
            block_type = getattr(block_type, "block_type", str(block_type))
        self._writes[(x, y, z)] = block_type
        cx = x // self.CHUNK_SIZE
        cz = z // self.CHUNK_SIZE
        chunk = self.chunks.get((cx, cz))
        if chunk is not None:
            chunk.set_block_type(x, y, z, block_type, self._ensure_block_type_id)

    def set_generated_chest_loot(self, x, y, z, contents):
        position = (int(x), int(y), int(z))
        self._generated_chest_loot[position] = {
            int(slot): {
                "id": str(entry["id"]),
                "count": int(entry.get("count", 1)),
                "damage": int(entry.get("damage", 0)),
            }
            for slot, entry in contents.items()
            if 0 <= int(slot) < 27 and entry and entry.get("id")
        }

    def collect_generated_chest_loot(self):
        return {
            position: {slot: dict(entry) for slot, entry in contents.items()}
            for position, contents in self._generated_chest_loot.items()
        }

    def set_generated_spawner_mob(self, x, y, z, mob_id):
        self._generated_spawner_mobs[(int(x), int(y), int(z))] = str(mob_id)

    def collect_generated_spawner_mobs(self):
        return dict(self._generated_spawner_mobs)

    def collect_changes(self):
        changes = dict(self._writes)
        for key, current in self._worldgen_block_overrides.items():
            before = self._original_worldgen_block_overrides.get(key, {})
            for pos, value in current.items():
                if pos not in before or before[pos] != value:
                    changes[pos] = value
        return changes
