from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, wait

NBTLIB_VERSION = "1.12.1"


from game.content_ids import (
    CLASSIC_BLOCK_NAME_TO_ID,
    CLASSIC_BLOCK_ID_TO_NAME,
    CLASSIC_ITEM_NAME_TO_ID,
    CLASSIC_ITEM_ID_TO_NAME,
    CLASSIC_BLOCK_ITEM_ID_TO_NAME,
    ITEM_NAME_ALIASES,
    CLASSIC_ENTITY_KEY_TO_ID,
    CLASSIC_LIVING_ENTITY_IDS,
    classic_block_id,
    classic_block_id_or_air,
    classic_item_id,
    item_name_from_classic_id,
    legacy_entity_id,
)

def _nbtlib():
    try:
        import nbtlib
        return nbtlib
    except ImportError as exc:
        raise RuntimeError(
            "World saving requires nbtlib. Install the pinned dependency with "
            "`python -m pip install -r requirements.txt`."
        ) from exc


def _tag_types():
    nbtlib = _nbtlib()
    from nbtlib import tag
    return nbtlib, tag


def _new_gzip_nbt_file(nbtlib, payload):
    from nbtlib import tag
    nbt_file = nbtlib.File({"": tag.Compound(payload)})
    try:
        nbt_file.gzipped = True
    except Exception:
        pass
    return nbt_file


def _find_named_compound(root, name, *, max_depth=4):
    if not hasattr(root, "get"):
        root_compound = getattr(root, "root", None)
        if root_compound is not None and root_compound is not root:
            return _find_named_compound(root_compound, name, max_depth=max_depth)
        return None
    try:
        value = root.get(name)
    except Exception:
        value = None
    if value is not None and hasattr(value, "get"):
        return value
    root_compound = getattr(root, "root", None)
    if root_compound is not None and root_compound is not root and hasattr(root_compound, "get"):
        try:
            value = root_compound.get(name)
        except Exception:
            value = None
        if value is not None and hasattr(value, "get"):
            return value
    if max_depth <= 0:
        return None
    try:
        children = list(root.values())
    except Exception:
        return None
    for child in children:
        if hasattr(child, "get"):
            found = _find_named_compound(child, name, max_depth=max_depth - 1)
            if found is not None:
                return found
    return None


def _load_nbt_file(nbtlib, path):
    try:
        return nbtlib.load(str(path), gzipped=True)
    except TypeError:
        return nbtlib.load(str(path))


def _validate_written_nbt(nbtlib, path, root_key, *, expected_blocks=None):
    check = _load_nbt_file(nbtlib, path)
    root_view = check
    if "" in check and root_key not in check:
        root_view = check[""]
    if not hasattr(root_view, "get") or root_key not in root_view:
        raise ValueError(
            f"temporary NBT has no root-level {root_key!r} compound; refusing to replace save"
        )
    compound = root_view[root_key]
    if not hasattr(compound, "get"):
        raise ValueError(f"temporary NBT root {root_key!r} is not a compound")
    if expected_blocks is not None:
        blocks = compound.get("Blocks")
        if blocks is None or len(blocks) != expected_blocks:
            actual = None if blocks is None else len(blocks)
            raise ValueError(
                f"temporary chunk Blocks array has length {actual}, expected {expected_blocks}"
            )


def signed_byte(value: int) -> int:
    value = int(value) & 0xFF
    return value if value < 128 else value - 256


def unpack_signed_bytes(values) -> bytes:
    if isinstance(values, (bytes, bytearray, memoryview)):
        return bytes(values)
    try:
        import numpy as np
        array = np.asarray(values)
        if array.dtype.kind in "iu":
            return array.astype(np.uint8, copy=False).tobytes()
    except Exception:
        pass
    return bytes(int(value) & 0xFF for value in values)


def pack_nibbles(values) -> bytes:
    if not isinstance(values, (bytes, bytearray, memoryview)):
        values = list(values)
    n = len(values)
    if n == 0:
        return b""
    try:
        import numpy as np
        arr = np.frombuffer(values, dtype=np.uint8) if isinstance(values, (bytes, bytearray, memoryview)) else np.asarray(values, dtype=np.uint8)
        out = np.empty((n + 1) // 2, dtype=np.uint8)
        even = arr[0::2] & 0x0F
        if n % 2 == 0:
            out[:] = even | ((arr[1::2] & 0x0F) << 4)
        else:
            out[:-1] = even[:-1] | ((arr[1::2] & 0x0F) << 4)
            out[-1] = even[-1]
        return out.tobytes()
    except Exception:
        result = bytearray((n + 1) // 2)
        for index in range(0, n, 2):
            lo = int(values[index]) & 0x0F
            hi = (int(values[index + 1]) & 0x0F) if index + 1 < n else 0
            result[index // 2] = lo | (hi << 4)
        return bytes(result)


def classic_metadata_bytes(chunk) -> bytearray:
    meta = bytearray(chunk.metadata)
    id_to_name = getattr(chunk, "_id_to_block_type", None)
    if not id_to_name:
        return meta
    chest_ids = [
        internal_id for internal_id, name in enumerate(id_to_name[:256])
        if name == "chest"
    ]
    if not chest_ids:
        return meta
    blocks = chunk.blocks
    for chest_id in chest_ids:
        needle = bytes((chest_id,))
        index = blocks.find(needle)
        while index >= 0:
            meta[index] = 0
            index = blocks.find(needle, index + 1)
    return meta


def chest_facing_to_persist(world, x, y, z):
    try:
        facing = int(world.get_metadata(int(x), int(y), int(z))) & 15
    except (AttributeError, TypeError, ValueError):
        return None
    return facing if facing in (2, 3, 4, 5) else None


def restore_chest_facing(world, x, y, z, facing):
    try:
        facing = int(facing)
        x, y, z = int(x), int(y), int(z)
    except (TypeError, ValueError):
        return False
    if facing not in (2, 3, 4, 5):
        return False
    try:
        cx, cz = world._get_chunk_index(x, z)
        chunk = world.chunks.get((cx, cz))
    except (AttributeError, TypeError, ValueError):
        return False
    if chunk is None or chunk.get_block_type(x, y, z) != "chest":
        return False
    chunk.set_metadata(x, y, z, facing)
    if hasattr(chunk, "mark_dirty_at"):
        chunk.mark_dirty_at(y)
    return True


def unpack_nibbles(values, count: int) -> bytearray:
    count = max(0, int(count))
    if count == 0:
        return bytearray()
    raw = unpack_signed_bytes(values)
    try:
        import numpy as np
        packed = np.frombuffer(raw, dtype=np.uint8)
        out = np.empty(count, dtype=np.uint8)
        n = min(count, len(packed) * 2)
        even_n = n // 2
        out[0:even_n * 2:2] = packed[:even_n] & 0x0F
        out[1:even_n * 2:2] = (packed[:even_n] >> 4) & 0x0F
        if n % 2:
            out[n - 1] = packed[even_n] & 0x0F
        if n < count:
            out[n:] = 0
        return bytearray(out.tobytes())
    except Exception:
        out = bytearray(count)
        limit = min(count, len(raw) * 2)
        for index in range(0, limit, 2):
            byte = raw[index // 2]
            out[index] = byte & 0x0F
            if index + 1 < limit:
                out[index + 1] = (byte >> 4) & 0x0F
        return out


def base36(value: int) -> str:
    value = int(value)
    if value == 0:
        return "0"
    negative = value < 0
    value = abs(value)
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = []
    while value:
        value, rem = divmod(value, 36)
        result.append(chars[rem])
    text = "".join(reversed(result))
    return "-" + text if negative else text


def classic_chunk_relative_path(cx: int, cz: int) -> Path:
    return (Path(base36(int(cx) & 63)) / base36(int(cz) & 63) /
            f"c.{base36(cx)}.{base36(cz)}.dat")


def _float_list(tag_module, kind, values):
    tag_type = getattr(tag_module, kind)
    return tag_module.List[tag_type]([tag_type(value) for value in values])


def _byte_array(tag_module, data):
    try:
        import numpy as np
        arr = np.frombuffer(data, dtype=np.uint8) if isinstance(data, (bytes, bytearray, memoryview)) else np.asarray(data, dtype=np.uint8)
        signed = arr.view(np.int8)
        return tag_module.ByteArray(signed.tolist())
    except Exception:
        return tag_module.ByteArray([signed_byte(v) for v in data])


def _as_int(compound, key, default=0):
    try:
        return int(compound[key])
    except (KeyError, TypeError, ValueError):
        return int(default)


def _as_float(compound, key, default=0.0):
    try:
        return float(compound[key])
    except (KeyError, TypeError, ValueError):
        return float(default)


def _java_rotation_to_port(rotation):
    yaw = float(rotation[0]) if len(rotation) > 0 else 0.0
    pitch = float(rotation[1]) if len(rotation) > 1 else 0.0
    return [180.0 - yaw, -pitch]


def _port_rotation_to_java(rotation):
    return _java_rotation_to_port(rotation)


def _vector(compound, key, length, cast=float, defaults=None):
    if defaults is None:
        defaults = [0.0] * length
    try:
        raw = compound[key]
    except (KeyError, TypeError, AttributeError):
        return list(defaults)
    candidates = [raw]
    try:
        wrapped = raw.value
    except Exception:
        wrapped = None
    if wrapped is not None and wrapped is not raw:
        candidates.append(wrapped)
    for candidate in candidates:
        try:
            values = [cast(value) for value in candidate]
            return (values + list(defaults))[:length]
        except (TypeError, ValueError, AttributeError):
            continue
    return list(defaults)


class WorldSave:

    def __init__(self, save_dir):
        _nbtlib()
        self.root = Path(save_dir).expanduser().resolve()
        self.level_data = self.load_level()
        self._write_executor = None
        self._pending_save_records = []
        self._pending_by_key = {}
        self._failed_snapshots = {}
        self._failed_retry_attempted = set()
        self._save_lock = threading.RLock()
        self._save_closed = False

    @property
    def level_path(self):
        return self.root / "level.dat"

    def chunk_path(self, cx, cz):
        return self.root / classic_chunk_relative_path(cx, cz)

    def has_saved_world(self):
        return self.level_data is not None

    def load_level(self):
        candidates = (self.root / "level.dat", self.root / "level.dat_old")
        encountered_file = False
        last_error = None
        for path in candidates:
            if not path.is_file():
                continue
            encountered_file = True
            try:
                nbtlib, tag = _tag_types()
                root = _load_nbt_file(nbtlib, path)
                data = _find_named_compound(root, "Data")
                if data is None:
                    if any(key in root for key in ("RandomSeed", "SizeOnDisk", "SpawnX")):
                        print(f"Recovering nonstandard level root in {path}; it will be normalized on save.")
                        data = root
                    else:
                        raise ValueError("missing root Data compound")
                player_tag = data.get("Player")
                player = self._decode_player(player_tag) if player_tag is not None else None
                return {
                    "RandomSeed": _as_int(data, "RandomSeed"),
                    "SpawnX": _as_int(data, "SpawnX"),
                    "SpawnY": _as_int(data, "SpawnY", 64),
                    "SpawnZ": _as_int(data, "SpawnZ"),
                    "Time": _as_int(data, "Time"),
                    "SizeOnDisk": _as_int(data, "SizeOnDisk"),
                    "SnowCovered": bool(_as_int(data, "SnowCovered")),
                    "DebugWorld": bool(_as_int(data, "DebugWorld")),
                    "McpyAccurateGeneration": bool(
                        _as_int(data, "McpyAccurateGeneration", 1)
                    ),
                    "Player": player,
                }
            except RuntimeError:
                raise
            except Exception as exc:
                last_error = exc
                print(f"Could not read {path}: {exc}")
        if encountered_file:
            raise RuntimeError(
                f"Could not read level.dat or level.dat_old in {self.root}; "
                "refusing to overwrite the existing world."
            ) from last_error
        return None

    def chunk_exists(self, cx, cz):
        return self.chunk_path(cx, cz).is_file()

    def load_chunk(self, cx, cz):
        path = self.chunk_path(cx, cz)
        if not path.is_file():
            return None
        try:
            nbtlib, tag = _tag_types()
            root = _load_nbt_file(nbtlib, path)
            level = _find_named_compound(root, "Level")
            if level is None:
                if all(key in root for key in ("xPos", "zPos", "Blocks")):
                    print(f"Recovering nonstandard chunk root in {path}; it will be normalized on save.")
                    level = root
                else:
                    raise ValueError("missing root Level compound")
            x_pos = _as_int(level, "xPos", cx)
            z_pos = _as_int(level, "zPos", cz)
            if (x_pos, z_pos) != (int(cx), int(cz)):
                raise ValueError(f"chunk coordinates disagree: {(x_pos, z_pos)}")
            raw_blocks = unpack_signed_bytes(level.get("Blocks", []))
            if len(raw_blocks) != 16 * 16 * 128:
                raise ValueError(f"unexpected Blocks length {len(raw_blocks)}")
            count = len(raw_blocks)
            raw_data_tag = level.get("Data", [])
            raw_sky_tag = level.get("SkyLight", [])
            raw_block_tag = level.get("BlockLight", [])
            raw_data = unpack_nibbles(raw_data_tag, count)
            sky = unpack_nibbles(raw_sky_tag, count)
            block = unpack_nibbles(raw_block_tag, count)
            heightmap = unpack_signed_bytes(level.get("HeightMap", []))
            if len(heightmap) != 256:
                heightmap = bytearray(256)
            block_names = []
            for entry in level.get("McpyBlockNames", []):
                try:
                    index, name = int(entry["Index"]), str(entry["Name"])
                    if 0 <= index < count:
                        block_names.append((index, name))
                except (KeyError, TypeError, ValueError):
                    continue
            entities = [self._decode_entity(entry) for entry in level.get("Entities", [])]
            tile_entities = list(level.get("TileEntities", []) or [])
            door_hinges = []
            for entry in level.get("McpyDoorHinges", []):
                try:
                    door_hinges.append({
                        "pos": (_as_int(entry, "X"), _as_int(entry, "Y"), _as_int(entry, "Z")),
                        "mirrored": bool(_as_int(entry, "Mirrored")),
                    })
                except Exception:
                    continue
            door_partners = []
            for entry in level.get("McpyDoorPartners", []):
                try:
                    door_partners.append({
                        "pos": (_as_int(entry, "X"), _as_int(entry, "Y"), _as_int(entry, "Z")),
                        "partner": (_as_int(entry, "PartnerX"), _as_int(entry, "PartnerY"),
                                    _as_int(entry, "PartnerZ")),
                    })
                except Exception:
                    continue
            return {
                "xPos": x_pos, "zPos": z_pos,
                "LastUpdate": _as_int(level, "LastUpdate"),
                "raw_blocks": raw_blocks, "metadata": raw_data,
                "skylight": sky, "blocklight": block,
                "heightmap": heightmap,
                "light_data_valid": (
                    len(raw_data_tag) == count // 2
                    and len(raw_sky_tag) == count // 2
                    and len(raw_block_tag) == count // 2
                ),
                "TerrainPopulated": bool(_as_int(level, "TerrainPopulated")),
                "block_names": block_names, "entities": [e for e in entities if e], "tile_entities": tile_entities,
                "door_hinges": door_hinges, "door_partners": door_partners,
            }
        except RuntimeError:
            raise
        except Exception as exc:
            print(f"Could not read chunk {cx},{cz} from {path}: {exc}")
            return None

    @staticmethod
    def _persistent_entity_types():
        from game.entities.item import ItemEntity
        from game.entities.falling_block import FallingBlockEntity
        from game.entities.arrow import ArrowEntity
        from game.entities.fireball import FireballEntity
        from game.entities.painting import PaintingEntity
        from game.entities.minecart import MinecartEntity
        from game.entities.boat import BoatEntity
        living = tuple(WorldSave._living_mob_registry().values())
        return (
            ItemEntity, FallingBlockEntity, ArrowEntity, FireballEntity,
            PaintingEntity, MinecartEntity, BoatEntity,
        ) + living

    @staticmethod
    def chunk_has_persistent_entities(world, chunk):
        persistent_types = WorldSave._persistent_entity_types()
        key = (int(chunk.cx), int(chunk.cz))
        columns = getattr(world, "_entity_spatial_columns", None)
        buckets = getattr(world, "_entity_spatial_index", None)
        cell_for = getattr(world, "_entity_spatial_cell", None)
        chunk_index = getattr(world, "_get_chunk_index", None)
        ensure_columns = getattr(world, "_ensure_entity_spatial_columns", None)
        if columns is None and callable(ensure_columns):
            columns = ensure_columns()

        if isinstance(columns, dict) and callable(cell_for):
            chunk_size = float(getattr(world, "CHUNK_SIZE", getattr(chunk, "width", 16)))
            if not (chunk_size > 0.0):
                chunk_size = float(getattr(chunk, "width", 16) or 16)
            epsilon = 1e-9
            min_x = key[0] * chunk_size - 0.5
            max_x = (key[0] + 1) * chunk_size - 0.5 - epsilon
            min_z = key[1] * chunk_size - 0.5
            max_z = (key[1] + 1) * chunk_size - 0.5 - epsilon
            min_cell = cell_for((min_x, 0.0, min_z))
            max_cell = cell_for((max_x, 0.0, max_z))
            min_x_cell, max_x_cell = sorted((int(min_cell[0]), int(max_cell[0])))
            min_z_cell, max_z_cell = sorted((int(min_cell[2]), int(max_cell[2])))
            for cell_x in range(min_x_cell, max_x_cell + 1):
                for cell_z in range(min_z_cell, max_z_cell + 1):
                    y_buckets = columns.get((cell_x, cell_z), {})
                    for entities in y_buckets.values():
                        for entity in entities:
                            if getattr(entity, "dead", False) or not isinstance(entity, persistent_types):
                                continue
                            try:
                                entity_key = tuple(chunk_index(entity.pos[0], entity.pos[2])) if callable(chunk_index) else (
                                    int((float(entity.pos[0]) + 0.5) // chunk_size),
                                    int((float(entity.pos[2]) + 0.5) // chunk_size),
                                )
                            except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
                                continue
                            if entity_key == key:
                                return True
            return False

        if isinstance(buckets, dict) and callable(cell_for):
            chunk_size = float(getattr(world, "CHUNK_SIZE", getattr(chunk, "width", 16)))
            if not (chunk_size > 0.0):
                chunk_size = float(getattr(chunk, "width", 16) or 16)
            epsilon = 1e-9
            min_cell = cell_for((key[0] * chunk_size - 0.5, 0.0, key[1] * chunk_size - 0.5))
            max_cell = cell_for(((key[0] + 1) * chunk_size - 0.5 - epsilon, 0.0,
                                 (key[1] + 1) * chunk_size - 0.5 - epsilon))
            min_x_cell, max_x_cell = sorted((int(min_cell[0]), int(max_cell[0])))
            min_z_cell, max_z_cell = sorted((int(min_cell[2]), int(max_cell[2])))
            for cell, entities in buckets.items():
                try:
                    cell_x, _cell_y, cell_z = cell
                except (TypeError, ValueError):
                    continue
                if not (min_x_cell <= cell_x <= max_x_cell
                        and min_z_cell <= cell_z <= max_z_cell):
                    continue
                for entity in entities:
                    if getattr(entity, "dead", False) or not isinstance(entity, persistent_types):
                        continue
                    try:
                        entity_key = tuple(chunk_index(entity.pos[0], entity.pos[2])) if callable(chunk_index) else (
                            int((float(entity.pos[0]) + 0.5) // chunk_size),
                            int((float(entity.pos[2]) + 0.5) // chunk_size),
                        )
                    except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
                        continue
                    if entity_key == key:
                        return True
            return False

        for entity in getattr(world, "entities", ()):
            if getattr(entity, "dead", False) or not isinstance(entity, persistent_types):
                continue
            try:
                entity_key = tuple(chunk_index(entity.pos[0], entity.pos[2])) if callable(chunk_index) else (
                    int((float(entity.pos[0]) + 0.5) // 16),
                    int((float(entity.pos[2]) + 0.5) // 16),
                )
            except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
                continue
            if entity_key == key:
                return True
        return False

    @staticmethod
    def chunks_with_items(world):
        persistent_types = WorldSave._persistent_entity_types()
        keys = set()
        for entity in list(getattr(world, "entities", ())):
            if (isinstance(entity, persistent_types)
                    and not getattr(entity, "dead", False)):
                try:
                    keys.add(tuple(world._get_chunk_index(entity.pos[0], entity.pos[2])))
                except Exception:
                    continue
        return keys

    @staticmethod
    def _record_save_gate_timing(world, stage, started, details=None):
        profiler = getattr(world, "profiler", None)
        record_ms = getattr(profiler, "record_ms", None)
        if callable(record_ms):
            record_ms(
                f"chunk.save_gate.{stage}",
                (time.perf_counter() - started) * 1000.0,
                event_details=details,
            )

    def save_chunk_if_needed(self, world, chunk, item_chunks=None):
        key = (chunk.cx, chunk.cz)
        started = time.perf_counter()
        has_items = (
            key in item_chunks if item_chunks is not None
            else self.chunk_has_persistent_entities(world, chunk)
        )
        self._record_save_gate_timing(
            world, "entities", started,
            {"spatial_index": getattr(world, "_entity_spatial_index", None) is not None},
        )

        started = time.perf_counter()
        has_tile_entities = self._chunk_has_tile_entities(world, chunk)
        self._record_save_gate_timing(world, "tile_entities", started)

        started = time.perf_counter()
        needs_saving = chunk.needs_saving(world, has_items)
        self._record_save_gate_timing(world, "needs_saving", started)
        if not has_tile_entities and not needs_saving:
            return True

        has_blocks = True
        if not has_tile_entities and not has_items:
            started = time.perf_counter()
            has_blocks = any(chunk.blocks)
            self._record_save_gate_timing(world, "empty_check", started)
        if not has_tile_entities and not has_items and not has_blocks:
            path = self.chunk_path(chunk.cx, chunk.cz)
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass
            chunk.mark_saved_state(world, fingerprint=chunk.fingerprint(), entity_count=0)
            return True
        return self.save_chunk(world, chunk)

    def _prepare_chunk_snapshot(self, world, chunk):
        prepare_started = time.perf_counter()
        profiler = getattr(world, "profiler", None)

        def record_part(name, part_started):
            if profiler is not None:
                profiler.record_ms(
                    f"chunk.save_snapshot.{name}",
                    (time.perf_counter() - part_started) * 1000.0,
                )

        part_started = time.perf_counter()
        fingerprint = chunk.fingerprint()
        record_part("fingerprint", part_started)
        nbtlib, tag = _tag_types()
        path = self.chunk_path(chunk.cx, chunk.cz)

        part_started = time.perf_counter()
        numeric_blocks, custom_names = self._encode_chunk_blocks(chunk)
        record_part("blocks", part_started)

        part_started = time.perf_counter()
        metadata = pack_nibbles(classic_metadata_bytes(chunk))
        sky = pack_nibbles(bytes(chunk.skylight))
        block_light = pack_nibbles(bytes(chunk.blocklight))
        heightmap = bytes(int(value) & 0xFF for value in chunk.heightmap)
        if len(heightmap) != 256:
            heightmap = (heightmap + bytes(256))[:256]
        record_part("arrays", part_started)

        part_started = time.perf_counter()
        entity_records = self._encode_entities(world, chunk.cx, chunk.cz)
        record_part("entities", part_started)
        part_started = time.perf_counter()
        tile_records = self._encode_tile_entities(world, chunk.cx, chunk.cz)
        record_part("tile_entities", part_started)
        populated = bool(
            getattr(chunk, "terrain_populated", False)
            or (chunk.cx, chunk.cz) in getattr(world, "_populated_chunks", set())
        )
        part_started = time.perf_counter()
        level = tag.Compound({
            "xPos": tag.Int(int(chunk.cx)),
            "zPos": tag.Int(int(chunk.cz)),
            "LastUpdate": tag.Long(int(world.world_time)),
            "Blocks": _byte_array(tag, numeric_blocks),
            "Data": _byte_array(tag, metadata),
            "SkyLight": _byte_array(tag, sky),
            "BlockLight": _byte_array(tag, block_light),
            "HeightMap": _byte_array(tag, heightmap),
            "TerrainPopulated": tag.Byte(1 if populated else 0),
            "Entities": tag.List[tag.Compound](entity_records),
            "TileEntities": tag.List[tag.Compound](tile_records),
        })
        door_hinges = []
        for (x, y, z), mirrored in getattr(world, "door_hinges", {}).items():
            if world._get_chunk_index(x, z) == (chunk.cx, chunk.cz):
                door_hinges.append(tag.Compound({
                    "X": tag.Int(int(x)), "Y": tag.Int(int(y)),
                    "Z": tag.Int(int(z)), "Mirrored": tag.Byte(1 if mirrored else 0),
                }))
        if door_hinges:
            level["McpyDoorHinges"] = tag.List[tag.Compound](door_hinges)
        door_partners = []
        for (x, y, z), (px, py, pz) in getattr(world, "door_partners", {}).items():
            if world._get_chunk_index(x, z) == (chunk.cx, chunk.cz):
                door_partners.append(tag.Compound({
                    "X": tag.Int(int(x)), "Y": tag.Int(int(y)),
                    "Z": tag.Int(int(z)), "PartnerX": tag.Int(int(px)),
                    "PartnerY": tag.Int(int(py)), "PartnerZ": tag.Int(int(pz)),
                }))
        if door_partners:
            level["McpyDoorPartners"] = tag.List[tag.Compound](door_partners)
        if custom_names:
            level["McpyBlockNames"] = tag.List[tag.Compound]([
                tag.Compound({"Index": tag.Int(int(index)), "Name": tag.String(name)})
                for index, name in custom_names
            ])

        nbt_file = _new_gzip_nbt_file(nbtlib, {"Level": level})
        record_part("nbt_assembly", part_started)
        temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        snapshot = {
            "key": (int(chunk.cx), int(chunk.cz)),
            "chunk": chunk,
            "path": path,
            "temp_path": temp_path,
            "nbtlib": nbtlib,
            "nbt_file": nbt_file,
            "expected_blocks": len(numeric_blocks),
            "numeric_blocks": bytes(numeric_blocks),
            "internal_blocks": bytes(chunk.blocks),
            "raw_metadata": bytes(chunk.metadata),
            "custom_names": dict(custom_names),
            "fingerprint": fingerprint,
            "populated": populated,
            "entity_count": len(entity_records),
            "prepared_at": time.perf_counter(),
            "prepare_ms": (time.perf_counter() - prepare_started) * 1000.0,
            "profiler": profiler,
            "write_ms": 0.0,
            "write_bytes": 0,
        }
        return snapshot

    @staticmethod
    def _write_chunk_snapshot(snapshot):
        started = time.perf_counter()
        path = snapshot["path"]
        temp_path = snapshot["temp_path"]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            snapshot["nbt_file"].save(str(temp_path), gzipped=True)
            _validate_written_nbt(
                snapshot["nbtlib"], temp_path, "Level",
                expected_blocks=snapshot["expected_blocks"],
            )
            snapshot["write_bytes"] = temp_path.stat().st_size
            os.replace(temp_path, path)
            return True
        except Exception as exc:
            print(
                f"Could not save chunk {snapshot['key'][0]},{snapshot['key'][1]}: {exc}"
            )
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass
            return False
        finally:
            snapshot["write_ms"] = (time.perf_counter() - started) * 1000.0

    @staticmethod
    def _commit_chunk_snapshot(snapshot):
        chunk = snapshot["chunk"]
        chunk._source_block_ids = snapshot["numeric_blocks"]
        chunk._source_internal_blocks = snapshot["internal_blocks"]
        chunk._source_metadata = snapshot["raw_metadata"]
        chunk._mcpy_block_names = dict(snapshot["custom_names"])
        chunk._saved_fingerprint = snapshot["fingerprint"]
        chunk._saved_populated = snapshot["populated"]
        chunk._saved_entity_count = int(snapshot["entity_count"])

    def save_chunk(self, world, chunk):
        if not self.wait_for_chunk_save(chunk.cx, chunk.cz):
            return False
        try:
            snapshot = self._prepare_chunk_snapshot(world, chunk)
        except Exception as exc:
            print(f"Could not prepare chunk {chunk.cx},{chunk.cz}: {exc}")
            return False
        saved = self._write_chunk_snapshot(snapshot)
        if saved:
            self._commit_chunk_snapshot(snapshot)
        return saved

    def _ensure_write_executor(self):
        with self._save_lock:
            if self._save_closed:
                raise RuntimeError("World save store is closed")
            if self._write_executor is None:
                self._write_executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="minecraft-chunk-save"
                )
            return self._write_executor

    def queue_chunk_save(self, world, chunk):
        try:
            snapshot = self._prepare_chunk_snapshot(world, chunk)
            executor = self._ensure_write_executor()
            future = executor.submit(self._write_chunk_snapshot, snapshot)
        except Exception as exc:
            print(f"Could not queue chunk {chunk.cx},{chunk.cz}: {exc}")
            return False
        key = snapshot["key"]
        profiler = snapshot.get("profiler")
        if profiler is not None:
            profiler.record_ms("chunk.save_snapshot", snapshot.get("prepare_ms", 0.0))
        record = {"future": future, "snapshot": snapshot}
        with self._save_lock:
            self._pending_save_records.append(record)
            self._pending_by_key[key] = future
            self._failed_snapshots.pop(key, None)
            retry_attempted = getattr(self, "_failed_retry_attempted", None)
            if retry_attempted is not None:
                retry_attempted.discard(key)
        return future

    def _chunk_has_tile_entities(self, world, chunk):
        key = (chunk.cx, chunk.cz)
        chunk_index = getattr(world, "_get_chunk_index", None)
        for te_key, te in getattr(world, "tile_entities", {}).items():
            if te is None:
                continue
            try:
                tx, _ty, tz = te_key
                entity_key = tuple(chunk_index(tx, tz)) if callable(chunk_index) else (
                    int(float(tx) // 16), int(float(tz) // 16)
                )
                if entity_key == key:
                    return True
            except (TypeError, ValueError, OverflowError):
                continue
        return False

    def queue_chunk_save_if_needed(self, world, chunk, item_chunks=None):
        key = (chunk.cx, chunk.cz)
        started = time.perf_counter()
        has_items = (
            key in item_chunks if item_chunks is not None
            else self.chunk_has_persistent_entities(world, chunk)
        )
        self._record_save_gate_timing(
            world, "entities", started,
            {"spatial_index": getattr(world, "_entity_spatial_index", None) is not None},
        )

        started = time.perf_counter()
        has_te = self._chunk_has_tile_entities(world, chunk)
        self._record_save_gate_timing(world, "tile_entities", started)

        started = time.perf_counter()
        needs_saving = chunk.needs_saving(world, has_items)
        self._record_save_gate_timing(world, "needs_saving", started)
        if not has_te and not needs_saving:
            return True

        has_blocks = True
        if not has_te and not has_items:
            started = time.perf_counter()
            has_blocks = any(chunk.blocks)
            self._record_save_gate_timing(world, "empty_check", started)
        if not has_te and not has_items and not has_blocks:
            path = self.chunk_path(chunk.cx, chunk.cz)
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass
            chunk.mark_saved_state(world, fingerprint=chunk.fingerprint(), entity_count=0)
            return True
        return self.queue_chunk_save(world, chunk) is not False

    def poll_pending_saves(self):
        completed = 0
        remaining = []
        with self._save_lock:
            records = tuple(self._pending_save_records)
        for record in records:
            future = record["future"]
            if not future.done():
                remaining.append(record)
                continue
            snapshot = record["snapshot"]
            key = snapshot["key"]
            try:
                saved = bool(future.result())
            except Exception as exc:  
                print(f"Background save failed for chunk {key[0]},{key[1]}: {exc}")
                saved = False
            profiler = snapshot.get("profiler")
            if profiler is not None:
                profiler.record_ms(
                    "chunk.save_worker_write", snapshot.get("write_ms", 0.0),
                    event_details={"bytes": int(snapshot.get("write_bytes", 0)),
                                   "success": bool(saved)},
                )
            if saved:
                self._commit_chunk_snapshot(snapshot)
                self._failed_snapshots.pop(key, None)
                retry_attempted = getattr(self, "_failed_retry_attempted", None)
                if retry_attempted is not None:
                    retry_attempted.discard(key)
            else:
                self._failed_snapshots[key] = snapshot
            if self._pending_by_key.get(key) is future:
                self._pending_by_key.pop(key, None)
            completed += 1
        remaining_ids = {id(record) for record in remaining}
        with self._save_lock:
            self._pending_save_records = [
                record for record in self._pending_save_records
                if id(record) in remaining_ids
            ]
        return completed

    def has_chunk_save_pending(self, cx, cz):
        key = (int(cx), int(cz))
        with self._save_lock:
            return key in self._pending_by_key or key in self._failed_snapshots

    def poll_chunk_save_ready(self, cx, cz):
        key = (int(cx), int(cz))
        self.poll_pending_saves()
        with self._save_lock:
            if key in self._pending_by_key:
                return False
            failed = self._failed_snapshots.get(key)
            if failed is None:
                return True
            retry_attempted = getattr(self, "_failed_retry_attempted", None)
            if retry_attempted is None:
                retry_attempted = self._failed_retry_attempted = set()
            if key in retry_attempted:
                return None
            retry_attempted.add(key)

        try:
            executor = self._ensure_write_executor()
            future = executor.submit(self._write_chunk_snapshot, failed)
        except Exception as exc:
            print(f"Could not retry chunk save {key[0]},{key[1]}: {exc}")
            return None
        record = {"future": future, "snapshot": failed}
        with self._save_lock:
            self._pending_save_records.append(record)
            self._pending_by_key[key] = future
        profiler = failed.get("profiler")
        if profiler is not None:
            profiler.record_event(
                "chunk.save_retry_queued", cx=key[0], cz=key[1],
            )
        return False

    def wait_for_chunk_save(self, cx, cz):
        key = (int(cx), int(cz))
        while True:
            self.poll_pending_saves()
            future = self._pending_by_key.get(key)
            if future is None:
                break
            try:
                future.result()
            except Exception:
                pass
        failed = self._failed_snapshots.pop(key, None)
        if failed is None:
            return True
        try:
            executor = self._ensure_write_executor()
            future = executor.submit(self._write_chunk_snapshot, failed)
        except Exception as exc:
            print(f"Could not retry chunk save {key[0]},{key[1]}: {exc}")
            self._failed_snapshots[key] = failed
            return False
        record = {"future": future, "snapshot": failed}
        with self._save_lock:
            self._pending_save_records.append(record)
            self._pending_by_key[key] = future
        future.result()
        self.poll_pending_saves()
        return key not in self._failed_snapshots

    def has_pending_saves(self):
        self.poll_pending_saves()
        with self._save_lock:
            return bool(self._pending_save_records)

    def flush_pending_saves(self, *, wait_for_completion=True):
        if not wait_for_completion:
            self.poll_pending_saves()
            return not self._failed_snapshots
        while True:
            with self._save_lock:
                futures = [record["future"] for record in self._pending_save_records]
            if not futures:
                break
            wait(futures)
            self.poll_pending_saves()
        if self._failed_snapshots:
            retry_items = list(self._failed_snapshots.items())
            self._failed_snapshots.clear()
            executor = self._ensure_write_executor()
            retry_records = []
            for key, snapshot in retry_items:
                future = executor.submit(self._write_chunk_snapshot, snapshot)
                record = {"future": future, "snapshot": snapshot}
                retry_records.append(record)
                with self._save_lock:
                    self._pending_save_records.append(record)
                    self._pending_by_key[key] = future
            wait([record["future"] for record in retry_records])
            self.poll_pending_saves()
        return not self._failed_snapshots

    def close(self, *, wait_for_completion=True):
        ok = self.flush_pending_saves(wait_for_completion=wait_for_completion)
        with self._save_lock:
            executor, self._write_executor = self._write_executor, None
            self._save_closed = True
        if executor is not None:
            executor.shutdown(wait=wait_for_completion, cancel_futures=not wait_for_completion)
        return ok

    def _encode_chunk_blocks(self, chunk):
        count = len(chunk.blocks)
        prior_raw = getattr(chunk, "_source_block_ids", None)
        prior_internal = getattr(chunk, "_source_internal_blocks", None)
        prior_meta = getattr(chunk, "_source_metadata", None)
        can_preserve = (prior_raw is not None and len(prior_raw) == count
                        and prior_internal is not None and len(prior_internal) == count
                        and prior_meta is not None and len(prior_meta) == count)
        id_to_name = chunk._id_to_block_type

        if (can_preserve
                and chunk.blocks == prior_internal
                and chunk.metadata == prior_meta):
            has_fluids = any(
                name in ("water", "lava")
                and chunk.blocks.find(bytes((internal_id,))) >= 0
                for internal_id, name in enumerate(id_to_name[:256])
                if name is not None
            )
            if not has_fluids:
                loaded_names = getattr(chunk, "_mcpy_block_names", {})
                return bytes(prior_raw), sorted(loaded_names.items())

        if not can_preserve:
            lookup = bytearray(256)
            for internal_id, name in enumerate(id_to_name[:256]):
                lookup[internal_id] = classic_block_id_or_air(name, 0) & 0xFF
            result = bytearray(chunk.blocks.translate(bytes(lookup)))
            custom_names = []

            for internal_id, name in enumerate(id_to_name[:256]):
                if name is None:
                    continue
                classic_id = lookup[internal_id]
                if CLASSIC_BLOCK_ID_TO_NAME.get(classic_id) == name:
                    continue
                needle = bytes((internal_id,))
                index = chunk.blocks.find(needle)
                while index >= 0:
                    custom_names.append((index, name))
                    index = chunk.blocks.find(needle, index + 1)
            custom_names.sort(key=lambda entry: entry[0])

            scheduler = getattr(getattr(chunk, "world", None), "_fluid_scheduler", None)
            height, depth, width = chunk.height, chunk.depth, chunk.width
            fluid_ids = tuple(
                (internal_id, name) for internal_id, name in enumerate(id_to_name[:256])
                if name in ("water", "lava")
            )
            for internal_id, name in fluid_ids:
                flowing_id = 8 if name == "water" else 10
                still_id = flowing_id + 1
                needle = bytes((internal_id,))
                index = chunk.blocks.find(needle)
                while index >= 0:
                    meta = chunk.metadata[index] & 15
                    if meta:
                        result[index] = flowing_id
                    elif scheduler is None:
                        result[index] = still_id
                    else:
                        y = index % height
                        column = index // height
                        lz = column % depth
                        lx = column // depth
                        moving = scheduler.is_moving(
                            chunk.start_x + lx, y, chunk.start_z + lz,
                        )
                        result[index] = flowing_id if moving else still_id
                    index = chunk.blocks.find(needle, index + 1)
            return bytes(result), custom_names

        result = bytearray(count)
        custom_names = []
        loaded_names = getattr(chunk, "_mcpy_block_names", {})
        scheduler = getattr(getattr(chunk, "world", None), "_fluid_scheduler", None)
        height, depth, width = chunk.height, chunk.depth, chunk.width
        for index, internal_id in enumerate(chunk.blocks):
            name = id_to_name[internal_id] if internal_id < len(id_to_name) else None
            meta = chunk.metadata[index] & 15
            unchanged = (internal_id == prior_internal[index]
                         and meta == (prior_meta[index] & 15))
            if name in ("water", "lava"):
                if meta == 0:
                    if scheduler is not None:
                        y = index % height
                        column = index // height
                        lz = column % depth
                        lx = column // depth
                        moving = scheduler.is_moving(
                            chunk.start_x + lx, y, chunk.start_z + lz,
                        )
                    else:
                        moving = False
                    classic_id = ((8 if moving else 9) if name == "water"
                                  else (10 if moving else 11))
                else:
                    classic_id = classic_block_id(name, meta)
            elif unchanged:
                classic_id = prior_raw[index]
            else:
                classic_id = classic_block_id_or_air(name, meta)
            result[index] = int(classic_id) & 0xFF

            canonical_name = CLASSIC_BLOCK_ID_TO_NAME.get(int(classic_id) & 0xFF)
            if name is not None and canonical_name != name and not unchanged:
                custom_names.append((index, name))
            elif name is not None and canonical_name != name and unchanged:
                if loaded_names.get(index) == name:
                    custom_names.append((index, name))
        return bytes(result), custom_names


    def _encode_tile_entities(self, world, cx, cz):
        nbtlib, tag = _tag_types()
        result = []
        for (x, y, z), te in list(getattr(world, "tile_entities", {}).items()):
            if te is None:
                continue
            if world._get_chunk_index(x, z) != (cx, cz):
                continue
            data = te.to_dict() if hasattr(te, "to_dict") else None
            if not data:
                continue
            te_type = data.get("type")
            if te_type == "chest":
                items = tag.List[tag.Compound]([])
                for entry in data.get("items", []):
                    items.append(self._encode_te_item(tag, entry))
                record = {
                    "id": tag.String("Chest"),
                    "x": tag.Int(int(x)), "y": tag.Int(int(y)), "z": tag.Int(int(z)),
                    "Items": items,
                }
                facing = chest_facing_to_persist(world, x, y, z)
                if facing is not None:
                    record["McpyFacing"] = tag.Byte(facing)
                result.append(tag.Compound(record))
            elif te_type == "furnace":
                items = tag.List[tag.Compound]([])
                for entry in data.get("items", []):
                    items.append(self._encode_te_item(tag, entry))
                result.append(tag.Compound({
                    "id": tag.String("Furnace"),
                    "x": tag.Int(int(x)), "y": tag.Int(int(y)), "z": tag.Int(int(z)),
                    "Items": items,
                    "BurnTime": tag.Short(int(data.get("burn", 0))),
                    "CookTime": tag.Short(int(data.get("cook", 0))),
                    "McpyBurnTotal": tag.Short(int(data.get("burn_total", 0))),
                }))
            elif te_type == "dispenser":
                items = tag.List[tag.Compound]([])
                for entry in data.get("items", []):
                    items.append(self._encode_te_item(tag, entry))
                result.append(tag.Compound({
                    "id": tag.String("Trap"),
                    "x": tag.Int(int(x)), "y": tag.Int(int(y)), "z": tag.Int(int(z)),
                    "Items": items,
                }))
            elif te_type == "sign":
                lines = data.get("text") or ["", "", "", ""]
                result.append(tag.Compound({
                    "id": tag.String("Sign"),
                    "x": tag.Int(int(x)), "y": tag.Int(int(y)), "z": tag.Int(int(z)),
                    "Text1": tag.String(str(lines[0] if len(lines) > 0 else "")),
                    "Text2": tag.String(str(lines[1] if len(lines) > 1 else "")),
                    "Text3": tag.String(str(lines[2] if len(lines) > 2 else "")),
                    "Text4": tag.String(str(lines[3] if len(lines) > 3 else "")),
                }))
            elif te_type == "mob_spawner":
                result.append(tag.Compound({
                    "id": tag.String("MobSpawner"),
                    "x": tag.Int(int(x)), "y": tag.Int(int(y)), "z": tag.Int(int(z)),
                    "EntityId": tag.String(str(data.get("entity_id", legacy_entity_id("pig")))),
                    "Delay": tag.Short(int(data.get("delay", 20))),
                }))
            elif te_type == "note_block":
                result.append(tag.Compound({
                    "id": tag.String("Music"),
                    "x": tag.Int(int(x)), "y": tag.Int(int(y)), "z": tag.Int(int(z)),
                    "note": tag.Byte(max(0, min(24, int(data.get("note", 0))))),
                }))
        return result

    def _encode_te_item(self, tag, entry):
        item_name = entry.get("id", "")
        numeric_id = classic_item_id(item_name) if item_name else 0
        if numeric_id is None:
            numeric_id = 0
        item_compound = tag.Compound({
            "id": tag.Short(int(numeric_id) & 0xFFFF),
            "Count": tag.Byte(signed_byte(int(entry.get("count", 1)))),
            "Damage": tag.Short(int(entry.get("damage", 0))),
            "Slot": tag.Byte(signed_byte(int(entry.get("slot", 0)))),
        })
        if item_name and item_name_from_classic_id(int(numeric_id) & 0xFFFF) != item_name:
            item_compound["McpyId"] = tag.String(str(item_name))
        return item_compound

    def _decode_tile_entities(self, world, records):
        from game.block.tile_entity import (
            TileEntityChest, TileEntityDispenser, TileEntityFurnace,
            TileEntityMobSpawner, TileEntitySign, TileEntityNote,
        )
        from game.entities.item import ItemStack
        for entry in records or []:
            try:
                te_id = str(entry.get("id", ""))
                x = _as_int(entry, "x")
                y = _as_int(entry, "y")
                z = _as_int(entry, "z")
                items_raw = entry.get("Items", []) or []
                if te_id == "Chest":
                    te = TileEntityChest(world, x, y, z)
                    for item in items_raw:
                        slot = _as_int(item, "Slot") & 0xFF
                        name = str(item.get("McpyId", "")) or item_name_from_classic_id(_as_int(item, "id") & 0xFFFF)
                        if name is None or not (0 <= slot < te.SIZE):
                            continue
                        count = max(1, min(64, _as_int(item, "Count", 1) & 0xFF))
                        te.contents[slot] = ItemStack(name, count, _as_int(item, "Damage"))
                    world.tile_entities[(x, y, z)] = te
                    if "McpyFacing" in entry:
                        restore_chest_facing(
                            world, x, y, z, _as_int(entry, "McpyFacing", 0),
                        )
                elif te_id == "Furnace":
                    te = TileEntityFurnace(world, x, y, z)
                    te.furnace_burn_time = _as_int(entry, "BurnTime")
                    te.furnace_cook_time = _as_int(entry, "CookTime")
                    te.current_item_burn_time = _as_int(entry, "McpyBurnTotal")
                    for item in items_raw:
                        slot = _as_int(item, "Slot") & 0xFF
                        name = str(item.get("McpyId", "")) or item_name_from_classic_id(_as_int(item, "id") & 0xFFFF)
                        if name is None or not (0 <= slot < 3):
                            continue
                        count = max(1, min(64, _as_int(item, "Count", 1) & 0xFF))
                        te.furnace_item_stacks[slot] = ItemStack(name, count, _as_int(item, "Damage"))
                    world.tile_entities[(x, y, z)] = te
                elif te_id == "Trap":
                    te = TileEntityDispenser(world, x, y, z)
                    for item in items_raw:
                        slot = _as_int(item, "Slot") & 0xFF
                        name = str(item.get("McpyId", "")) or item_name_from_classic_id(_as_int(item, "id") & 0xFFFF)
                        if name is None or not (0 <= slot < te.SIZE):
                            continue
                        count = max(1, min(64, _as_int(item, "Count", 1) & 0xFF))
                        te.contents[slot] = ItemStack(name, count, _as_int(item, "Damage"))
                    world.tile_entities[(x, y, z)] = te
                elif te_id == "Sign":
                    te = TileEntitySign(world, x, y, z)
                    for i in range(4):
                        te.sign_text[i] = str(entry.get(f"Text{i + 1}", "") or "")[:15]
                    world.tile_entities[(x, y, z)] = te
                elif te_id == "MobSpawner":
                    te = TileEntityMobSpawner(world, x, y, z)
                    te.mob_id = str(entry.get("EntityId", legacy_entity_id("pig")) or legacy_entity_id("pig"))
                    te.delay = _as_int(entry, "Delay", 20)
                    world.tile_entities[(x, y, z)] = te
                elif te_id == "Music":
                    te = TileEntityNote(world, x, y, z)
                    te.note = max(0, min(24, _as_int(entry, "note", 0)))
                    world.tile_entities[(x, y, z)] = te
            except Exception as exc:
                print(f"Could not restore a tile entity: {exc}")


    _LIVING_MOB_TYPES = None

    @classmethod
    def _living_mob_registry(cls):
        if cls._LIVING_MOB_TYPES is None:
            from game.entities.mob import Mob
            from game.entities.monster import Monster
            from game.entities.zombie import Zombie
            from game.entities.pigzombie import ZombiePigman
            from game.entities.giant import Giant
            from game.entities.pig import Pig
            from game.entities.sheep import Sheep
            from game.entities.cow import Cow
            from game.entities.chicken import Chicken
            from game.entities.skeleton import Skeleton
            from game.entities.spider import Spider
            from game.entities.creeper import Creeper
            from game.entities.slime import Slime
            from game.entities.ghast import Ghast
            from game.entities.squid import Squid
            cls._LIVING_MOB_TYPES = {
                legacy_entity_id("mob"): Mob,
                legacy_entity_id("monster"): Monster,
                legacy_entity_id("zombie"): Zombie,
                legacy_entity_id("zombie_pigman"): ZombiePigman,
                legacy_entity_id("giant"): Giant,
                legacy_entity_id("pig"): Pig,
                legacy_entity_id("sheep"): Sheep,
                legacy_entity_id("cow"): Cow,
                legacy_entity_id("chicken"): Chicken,
                legacy_entity_id("skeleton"): Skeleton,
                legacy_entity_id("spider"): Spider,
                legacy_entity_id("creeper"): Creeper,
                legacy_entity_id("slime"): Slime,
                legacy_entity_id("ghast"): Ghast,
                legacy_entity_id("squid"): Squid,
            }
        return cls._LIVING_MOB_TYPES

    @classmethod
    def _living_mob_save_id(cls, entity):
        registry = cls._living_mob_registry()
        for entity_key, cls_ in (
            ("creeper", registry[legacy_entity_id("creeper")]),
            ("skeleton", registry[legacy_entity_id("skeleton")]),
            ("spider", registry[legacy_entity_id("spider")]),
            ("slime", registry[legacy_entity_id("slime")]),
            ("ghast", registry[legacy_entity_id("ghast")]),
            ("squid", registry[legacy_entity_id("squid")]),
            ("giant", registry[legacy_entity_id("giant")]),
            ("zombie_pigman", registry[legacy_entity_id("zombie_pigman")]),
            ("zombie", registry[legacy_entity_id("zombie")]),
            ("pig", registry[legacy_entity_id("pig")]),
            ("sheep", registry[legacy_entity_id("sheep")]),
            ("cow", registry[legacy_entity_id("cow")]),
            ("chicken", registry[legacy_entity_id("chicken")]),
            ("monster", registry[legacy_entity_id("monster")]),
            ("mob", registry[legacy_entity_id("mob")]),
        ):
            if isinstance(entity, cls_):
                return legacy_entity_id(entity_key)
        return None

    def _encode_entities(self, world, cx, cz):
        nbtlib, tag = _tag_types()
        from game.entities.item import ItemEntity
        result = []
        for entity in list(getattr(world, "entities", ())):
            if not isinstance(entity, ItemEntity) or getattr(entity, "dead", False):
                continue
            try:
                if world._get_chunk_index(entity.pos[0], entity.pos[2]) != (cx, cz):
                    continue
            except Exception:
                continue
            item_name = str(entity.item.id)
            numeric_id = classic_item_id(item_name)
            if numeric_id is None:
                numeric_id = 0
            item_compound = tag.Compound({
                "id": tag.Short(int(numeric_id)),
                "Count": tag.Byte(signed_byte(entity.item.count)),
                "Damage": tag.Short(int(entity.item.damage)),
            })
            if item_name != item_name_from_classic_id(numeric_id):
                item_compound["McpyId"] = tag.String(item_name)
            record = tag.Compound({
                "id": tag.String(legacy_entity_id("item")),
                "Pos": _float_list(tag, "Double", [float(v) + 0.5 for v in entity.pos]),
                "Motion": _float_list(tag, "Double", entity.vel),
                "Rotation": _float_list(tag, "Float", (0.0, 0.0)),
                "FallDistance": tag.Float(0.0),
                "Fire": tag.Short(0), "Air": tag.Short(300),
                "OnGround": tag.Byte(1 if entity.on_ground else 0),
                "Health": tag.Short(int(getattr(entity, "health", 5))),
                "Age": tag.Short(min(32767, int(getattr(entity, "age", 0)))),
                "Item": item_compound,
                "McpyPickupDelay": tag.Short(max(0, min(32767, int(getattr(entity, "pickup_delay", 0))))),
            })
            result.append(record)
        from game.entities.falling_block import FallingBlockEntity
        for entity in list(getattr(world, "entities", ())):
            if not isinstance(entity, FallingBlockEntity) or getattr(entity, "dead", False):
                continue
            try:
                if world._get_chunk_index(entity.pos[0], entity.pos[2]) != (cx, cz):
                    continue
                tile = classic_block_id(entity.block_type)
            except Exception:
                continue
            center = entity.center
            result.append(tag.Compound({
                "id": tag.String(legacy_entity_id("falling_block")),
                "Pos": _float_list(tag, "Double", [float(v) + 0.5 for v in center]),
                "Motion": _float_list(tag, "Double", entity.vel),
                "Rotation": _float_list(tag, "Float", (0.0, 0.0)),
                "FallDistance": tag.Float(0.0),
                "Fire": tag.Short(0), "Air": tag.Short(300),
                "OnGround": tag.Byte(0),
                "Tile": tag.Byte(signed_byte(tile)),
                "McpyFallTime": tag.Short(max(0, min(32767, int(entity.fall_time)))),
            }))
        from game.entities.arrow import ArrowEntity
        for entity in list(getattr(world, "entities", ())):
            if not isinstance(entity, ArrowEntity) or getattr(entity, "dead", False):
                continue
            try:
                if world._get_chunk_index(entity.pos[0], entity.pos[2]) != (cx, cz):
                    continue
                try:
                    in_tile_id = classic_block_id(entity.in_tile) if entity.in_tile is not None else 0
                except ValueError:
                    in_tile_id = 0
            except Exception:
                continue
            record = tag.Compound({
                "id": tag.String(legacy_entity_id("arrow")),
                "Pos": _float_list(tag, "Double", [float(v) + 0.5 for v in entity.pos]),
                "Motion": _float_list(tag, "Double", entity.vel),
                "Rotation": _float_list(tag, "Float", (entity.yaw, entity.pitch)),
                "FallDistance": tag.Float(0.0),
                "Fire": tag.Short(max(0, min(32767, int(entity.fire)))),
                "Air": tag.Short(300),
                "OnGround": tag.Byte(0),
                "xTile": tag.Short(int(entity.x_tile)),
                "yTile": tag.Short(int(entity.y_tile)),
                "zTile": tag.Short(int(entity.z_tile)),
                "inTile": tag.Byte(signed_byte(in_tile_id)),
                "shake": tag.Byte(max(0, min(127, int(entity.arrow_shake)))),
                "inGround": tag.Byte(1 if entity.in_ground else 0),
                "McpyTicksInGround": tag.Short(max(0, min(32767, int(entity.ticks_in_ground)))),
                "McpyTicksInAir": tag.Short(max(0, min(32767, int(entity.ticks_in_air)))),
            })
            if entity.in_tile is not None and CLASSIC_BLOCK_NAME_TO_ID.get(entity.in_tile) != in_tile_id:
                record["McpyInTile"] = tag.String(str(entity.in_tile))
            result.append(record)

        from game.entities.fireball import FireballEntity
        for entity in list(getattr(world, "entities", ())):
            if not isinstance(entity, FireballEntity) or getattr(entity, "dead", False):
                continue
            try:
                if world._get_chunk_index(entity.pos[0], entity.pos[2]) != (cx, cz):
                    continue
                in_tile_id = classic_block_id(entity.in_tile) if entity.in_tile is not None else 0
            except (Exception,):
                in_tile_id = 0
            record = tag.Compound({
                "id": tag.String(legacy_entity_id("fireball")),
                "Pos": _float_list(tag, "Double", [float(v) + 0.5 for v in entity.pos]),
                "Motion": _float_list(tag, "Double", entity.vel),
                "Rotation": _float_list(tag, "Float", (float(entity.yaw), float(entity.pitch))),
                "FallDistance": tag.Float(0.0),
                "Fire": tag.Short(0), "Air": tag.Short(300),
                "OnGround": tag.Byte(0),
                "xTile": tag.Short(int(getattr(entity, "x_tile", -1))),
                "yTile": tag.Short(int(getattr(entity, "y_tile", -1))),
                "zTile": tag.Short(int(getattr(entity, "z_tile", -1))),
                "inTile": tag.Byte(signed_byte(in_tile_id)),
                "shake": tag.Byte(max(0, min(127, int(getattr(entity, "shake", 0))))),
                "inGround": tag.Byte(1 if getattr(entity, "in_ground", False) else 0),
                "McpyAcceleration": _float_list(tag, "Double", entity.acceleration),
                "McpyAge": tag.Float(float(getattr(entity, "age", 0.0))),
                "McpyTicksInAir": tag.Short(max(0, min(32767, int(getattr(entity, "ticks_in_air", 0))))),
            })
            if entity.in_tile is not None and CLASSIC_BLOCK_NAME_TO_ID.get(entity.in_tile) != in_tile_id:
                record["McpyInTile"] = tag.String(str(entity.in_tile))
            result.append(record)

        from game.entities.minecart import MinecartEntity
        for entity in list(getattr(world, "entities", ())):
            if not isinstance(entity, MinecartEntity) or getattr(entity, "dead", False):
                continue
            try:
                if world._get_chunk_index(entity.pos[0], entity.pos[2]) != (cx, cz):
                    continue
            except Exception:
                continue
            record = {
                "id": tag.String(legacy_entity_id("minecart")),
                "Pos": _float_list(tag, "Double", [float(v) + 0.5 for v in entity.pos]),
                "Motion": _float_list(tag, "Double", entity.vel),
                "Rotation": _float_list(tag, "Float", (float(entity.yaw), 0.0)),
                "FallDistance": tag.Float(0.0),
                "Fire": tag.Short(0), "Air": tag.Short(300),
                "OnGround": tag.Byte(1 if entity.on_ground else 0),
                "Damage": tag.Int(max(0, int(entity.damage_taken))),
                "McpyTimeSinceHit": tag.Short(max(0, min(32767, int(entity.time_since_hit)))),
                "Type": tag.Int(max(0, min(2, int(getattr(entity, "cart_type", 0))))),
            }
            cart_type = int(getattr(entity, "cart_type", 0))
            if cart_type == 2:
                record.update({
                    "PushX": tag.Double(float(getattr(entity, "push_x", 0.0))),
                    "PushZ": tag.Double(float(getattr(entity, "push_z", 0.0))),
                    "Fuel": tag.Short(max(-32768, min(32767, int(getattr(entity, "fuel", 0))))),
                })
            elif cart_type == 1:
                items = tag.List[tag.Compound]([])
                for slot, stack in enumerate(getattr(entity, "cargo_items", ())):
                    if stack is None or int(getattr(stack, "count", 0)) <= 0:
                        continue
                    items.append(self._encode_te_item(tag, {
                        "slot": slot, "id": str(stack.id), "count": int(stack.count),
                        "damage": int(stack.damage),
                    }))
                record["Items"] = items
            result.append(tag.Compound(record))

        from game.entities.boat import BoatEntity
        for entity in list(getattr(world, "entities", ())):
            if not isinstance(entity, BoatEntity) or getattr(entity, "dead", False):
                continue
            try:
                if world._get_chunk_index(entity.pos[0], entity.pos[2]) != (cx, cz):
                    continue
            except Exception:
                continue
            result.append(tag.Compound({
                "id": tag.String(legacy_entity_id("boat")),
                "Pos": _float_list(tag, "Double", [float(v) + 0.5 for v in entity.pos]),
                "Motion": _float_list(tag, "Double", entity.vel),
                "Rotation": _float_list(tag, "Float", (float(entity.yaw), 0.0)),
                "FallDistance": tag.Float(0.0),
                "Fire": tag.Short(0), "Air": tag.Short(300),
                "OnGround": tag.Byte(1 if entity.on_ground else 0),
                "Damage": tag.Int(max(0, int(entity.damage_taken))),
                "McpyTimeSinceHit": tag.Short(max(0, min(32767, int(entity.time_since_hit)))),
            }))
        
        from game.entities.painting import PaintingEntity
        for entity in list(getattr(world, "entities", ())):
            if not isinstance(entity, PaintingEntity) or getattr(entity, "dead", False):
                continue
            try:
                if world._get_chunk_index(entity.pos[0], entity.pos[2]) != (cx, cz):
                    continue
            except Exception:
                continue
            cx_, cy_, cz_ = entity.center
            result.append(tag.Compound({
                "id": tag.String(legacy_entity_id("painting")),
                "Pos": _float_list(tag, "Double", [float(cx_) + 0.5, float(cy_) + 0.5, float(cz_) + 0.5]),
                "Motion": _float_list(tag, "Double", (0.0, 0.0, 0.0)),
                "Rotation": _float_list(tag, "Float", (float(entity.yaw), 0.0)),
                "FallDistance": tag.Float(0.0),
                "Fire": tag.Short(0), "Air": tag.Short(300),
                "OnGround": tag.Byte(0),
                "Dir": tag.Byte(signed_byte(int(entity.direction) & 3)),
                "TileX": tag.Int(int(entity.tile_x)),
                "TileY": tag.Int(int(entity.tile_y)),
                "TileZ": tag.Int(int(entity.tile_z)),
                "Motive": tag.String(str(entity.art.title)),
            }))

        living_registry = self._living_mob_registry()
        living_classes = tuple(living_registry.values())
        for entity in list(getattr(world, "entities", ())):
            if not isinstance(entity, living_classes) or getattr(entity, "dead", False):
                continue
            if int(getattr(entity, "health", 0)) <= 0:
                continue
            try:
                if world._get_chunk_index(entity.pos[0], entity.pos[2]) != (cx, cz):
                    continue
            except Exception:
                continue
            save_id = self._living_mob_save_id(entity)
            if save_id is None:
                continue
            yaw = float(getattr(entity, "yaw", 0.0))
            pitch = float(getattr(entity, "rotation_pitch", 0.0))
            java_rot = _port_rotation_to_java((yaw, pitch))
            record = tag.Compound({
                "id": tag.String(save_id),
                "Pos": _float_list(tag, "Double", [float(v) + 0.5 for v in entity.pos]),
                "Motion": _float_list(tag, "Double", [
                    float(entity.vel[0]), float(entity.vel[1]), float(entity.vel[2]),
                ]),
                "Rotation": _float_list(tag, "Float", java_rot),
                "FallDistance": tag.Float(float(getattr(entity, "_fall_distance", 0.0))),
                "Fire": tag.Short(max(0, min(32767, int(getattr(entity, "_fire_ticks", 0))))),
                "Air": tag.Short(max(0, min(300, int(getattr(entity, "air", 300))))),
                "OnGround": tag.Byte(1 if getattr(entity, "on_ground", False) else 0),
                "Health": tag.Short(max(0, min(32767, int(getattr(entity, "health", 10))))),
                "AttackTime": tag.Short(max(0, min(32767, int(getattr(entity, "attack_time", 0))))),
                "HurtTime": tag.Short(max(0, min(32767, int(getattr(entity, "hurt_time", 0))))),
                "DeathTime": tag.Short(max(0, min(32767, int(getattr(entity, "death_time", 0))))),
            })
            if save_id == legacy_entity_id("sheep"):
                record["Sheared"] = tag.Byte(1 if getattr(entity, "sheared", False) else 0)
                record["Color"] = tag.Byte(int(getattr(entity, "fleece_color", 0)) & 15)
            if save_id == legacy_entity_id("pig"):
                record["Saddle"] = tag.Byte(1 if getattr(entity, "saddled", False) else 0)
            if save_id == legacy_entity_id("slime"):
                record["Size"] = tag.Int(max(0, int(getattr(entity, "slime_size", 1)) - 1))
            if save_id == legacy_entity_id("zombie_pigman"):
                record["Anger"] = tag.Short(max(0, min(32767, int(getattr(entity, "anger_level", 0)))))
            if save_id == legacy_entity_id("creeper"):
                record["McpyFuse"] = tag.Short(max(0, min(32767, int(getattr(entity, "time_since_ignited", 0)))))
            result.append(record)
        return result

    @staticmethod
    def _decode_entity(entry):
        try:
            if str(entry.get("id", "")) == legacy_entity_id("minecart"):
                cart_type = max(0, min(2, _as_int(entry, "Type", 0)))
                cargo_items = []
                for item in entry.get("Items", []) or []:
                    slot = _as_int(item, "Slot") & 0xFF
                    item_id = _as_int(item, "id") & 0xFFFF
                    item_name = str(item.get("McpyId", "")) or item_name_from_classic_id(item_id)
                    count = max(1, min(64, _as_int(item, "Count", 1) & 0xFF))
                    if item_name is not None and 0 <= slot < 27:
                        cargo_items.append({
                            "slot": slot, "id": item_name, "count": count,
                            "damage": _as_int(item, "Damage"),
                        })
                return {
                    "id": legacy_entity_id("minecart"),
                    "pos": [value - 0.5 for value in _vector(
                        entry, "Pos", 3, float, [0.5, 64.5, 0.5],
                    )],
                    "motion": _vector(entry, "Motion", 3, float, [0.0, 0.0, 0.0]),
                    "rotation": _vector(entry, "Rotation", 2, float, [0.0, 0.0]),
                    "damage_taken": max(0, _as_int(entry, "Damage")),
                    "time_since_hit": max(0, _as_int(entry, "McpyTimeSinceHit")),
                    "cart_type": cart_type,
                    "fuel": _as_int(entry, "Fuel"),
                    "push_x": _as_float(entry, "PushX"),
                    "push_z": _as_float(entry, "PushZ"),
                    "cargo_items": cargo_items,
                }
            if str(entry.get("id", "")) == legacy_entity_id("boat"):
                return {
                    "id": legacy_entity_id("boat"),
                    "pos": [value - 0.5 for value in _vector(
                        entry, "Pos", 3, float, [0.5, 64.5, 0.5],
                    )],
                    "motion": _vector(entry, "Motion", 3, float, [0.0, 0.0, 0.0]),
                    "rotation": _vector(entry, "Rotation", 2, float, [0.0, 0.0]),
                    "damage_taken": max(0, _as_int(entry, "Damage")),
                    "time_since_hit": max(0, _as_int(entry, "McpyTimeSinceHit")),
                }
            if str(entry.get("id", "")) == legacy_entity_id("arrow"):
                pos = [value - 0.5 for value in _vector(entry, "Pos", 3, float, [0.5, 64.5, 0.5])]
                rotation = _vector(entry, "Rotation", 2, float, [0.0, 0.0])
                in_tile_id = _as_int(entry, "inTile") & 0xFF
                in_tile = str(entry.get("McpyInTile", "")) or CLASSIC_BLOCK_ID_TO_NAME.get(in_tile_id)
                return {
                    "id": legacy_entity_id("arrow"), "pos": pos,
                    "motion": _vector(entry, "Motion", 3, float, [0.0, 0.0, 0.0]),
                    "rotation": rotation,
                    "fire": max(0, _as_int(entry, "Fire")),
                    "x_tile": _as_int(entry, "xTile", -1),
                    "y_tile": _as_int(entry, "yTile", -1),
                    "z_tile": _as_int(entry, "zTile", -1),
                    "in_tile": in_tile,
                    "arrow_shake": max(0, _as_int(entry, "shake")),
                    "in_ground": bool(_as_int(entry, "inGround")),
                    "ticks_in_ground": max(0, _as_int(entry, "McpyTicksInGround")),
                    "ticks_in_air": max(0, _as_int(entry, "McpyTicksInAir")),
                }
            if str(entry.get("id", "")) == legacy_entity_id("fireball"):
                pos = [value - 0.5 for value in _vector(entry, "Pos", 3, float, [0.5, 64.5, 0.5])]
                in_tile_id = _as_int(entry, "inTile") & 0xFF
                in_tile = str(entry.get("McpyInTile", "")) or CLASSIC_BLOCK_ID_TO_NAME.get(in_tile_id)
                return {
                    "id": legacy_entity_id("fireball"), "pos": pos,
                    "motion": _vector(entry, "Motion", 3, float, [0.0, 0.0, 0.0]),
                    "rotation": _vector(entry, "Rotation", 2, float, [0.0, 0.0]),
                    "x_tile": _as_int(entry, "xTile", -1),
                    "y_tile": _as_int(entry, "yTile", -1),
                    "z_tile": _as_int(entry, "zTile", -1),
                    "in_tile": in_tile,
                    "shake": max(0, _as_int(entry, "shake")),
                    "in_ground": bool(_as_int(entry, "inGround")),
                    "acceleration": _vector(entry, "McpyAcceleration", 3, float, [0.0, 0.0, 0.0]),
                    "age": max(0.0, _as_float(entry, "McpyAge")),
                    "ticks_in_air": max(0, _as_int(entry, "McpyTicksInAir")),
                }
            if str(entry.get("id", "")) == legacy_entity_id("painting"):
                return {
                    "id": legacy_entity_id("painting"),
                    "direction": _as_int(entry, "Dir") & 3,
                    "tile_x": _as_int(entry, "TileX"),
                    "tile_y": _as_int(entry, "TileY"),
                    "tile_z": _as_int(entry, "TileZ"),
                    "motive": str(entry.get("Motive", "Kebab") or "Kebab"),
                }
            if str(entry.get("id", "")) == legacy_entity_id("falling_block"):
                from game.entities.falling_block import HALF
                center = [value - 0.5 for value in _vector(entry, "Pos", 3, float, [0.5, 64.5, 0.5])]
                tile_name = CLASSIC_BLOCK_ID_TO_NAME.get(_as_int(entry, "Tile") & 0xFF)
                if tile_name is None:
                    return None
                return {
                    "id": legacy_entity_id("falling_block"),
                    "pos": [center[0], center[1] - HALF, center[2]],
                    "motion": _vector(entry, "Motion", 3, float, [0.0, 0.0, 0.0]),
                    "tile_name": tile_name,
                    "fall_time": max(0, _as_int(entry, "McpyFallTime")),
                }
            save_id = str(entry.get("id", ""))
            if save_id in CLASSIC_LIVING_ENTITY_IDS:
                pos = [value - 0.5 for value in _vector(entry, "Pos", 3, float, [0.5, 64.5, 0.5])]
                motion = _vector(entry, "Motion", 3, float, [0.0, 0.0, 0.0])
                rotation = _java_rotation_to_port(
                    _vector(entry, "Rotation", 2, float, [0.0, 0.0])
                )
                return {
                    "id": save_id,
                    "pos": pos,
                    "motion": motion,
                    "rotation": rotation,
                    "health": max(1, _as_int(entry, "Health", 10)),
                    "fire": max(0, _as_int(entry, "Fire")),
                    "air": max(0, min(300, _as_int(entry, "Air", 300))),
                    "on_ground": bool(_as_int(entry, "OnGround")),
                    "fall_distance": _as_float(entry, "FallDistance"),
                    "hurt_time": max(0, _as_int(entry, "HurtTime")),
                    "attack_time": max(0, _as_int(entry, "AttackTime")),
                    "death_time": max(0, _as_int(entry, "DeathTime")),
                    "sheared": bool(_as_int(entry, "Sheared")),
                    "fleece_color": _as_int(entry, "Color") & 15,
                    "saddled": bool(_as_int(entry, "Saddle")),
                    "fuse": max(0, _as_int(entry, "McpyFuse")),
                    "anger": max(0, _as_int(entry, "Anger")),
                    "slime_size": max(1, _as_int(entry, "Size") + 1),
                }
            if str(entry.get("id", "")) != legacy_entity_id("item"):
                return None
            pos = [value - 0.5 for value in _vector(entry, "Pos", 3, float, [0.5, 64.5, 0.5])]
            motion = _vector(entry, "Motion", 3, float, [0.0, 0.0, 0.0])
            rotation = _vector(entry, "Rotation", 2, float, [0.0, 0.0])
            item = entry.get("Item", {})
            item_id = _as_int(item, "id") & 0xFFFF
            item_name = str(item.get("McpyId", "")) or item_name_from_classic_id(item_id)
            if item_name is None:
                return None
            count = max(1, min(127, _as_int(item, "Count", 1) & 0xFF))
            return {
                "id": legacy_entity_id("item"), "pos": pos, "motion": motion,
                "rotation": rotation, "item_name": item_name, "count": count,
                "damage": _as_int(item, "Damage"),
                "age": max(0, _as_int(entry, "Age")),
                "health": _as_int(entry, "Health", 5),
                "pickup_delay": _as_int(entry, "McpyPickupDelay"),
                "on_ground": bool(_as_int(entry, "OnGround")),
            }
        except Exception:
            return None

    @staticmethod
    def _decode_player(player_tag):
        inventory = []
        custom_ids = {}
        try:
            custom_ids = {
                int(item["Slot"]): str(item["Id"])
                for item in player_tag.get("McpyInventory", [])
            }
        except Exception:
            custom_ids = {}
        for stack in player_tag.get("Inventory", []):
            slot = _as_int(stack, "Slot") & 0xFF
            numeric_id = _as_int(stack, "id") & 0xFFFF
            item_name = custom_ids.get(slot) or str(stack.get("McpyId", "")) or item_name_from_classic_id(numeric_id)
            if item_name is None:
                continue
            inventory.append({
                "slot": slot, "id": item_name,
                "count": max(1, min(127, _as_int(stack, "Count", 1) & 0xFF)),
                "damage": _as_int(stack, "Damage"),
            })
        java_pos = _vector(player_tag, "Pos", 3, float, [0.5, 64.0 + 1.62, 0.5])
        return {
            "pos": [java_pos[0] - 0.5, java_pos[1] - 1.62, java_pos[2] - 0.5],
            "motion": _vector(player_tag, "Motion", 3, float, [0.0, 0.0, 0.0]),

            "rotation": _java_rotation_to_port(
                _vector(player_tag, "Rotation", 2, float, [0.0, 0.0])
            ),
            "on_ground": bool(_as_int(player_tag, "OnGround")),
            "fall_distance": _as_float(player_tag, "FallDistance"),
            "health": max(0, min(20, _as_int(player_tag, "Health", 20))),
            "air": max(-19, min(300, _as_int(player_tag, "Air", 300))),
            "fire_ticks": max(-20, min(32767, _as_int(player_tag, "Fire", 0))),
            "gamemode": _as_int(player_tag, "McpyGamemode"),
            "selected_slot": 0,
            "inventory": inventory,
        }

    def restore_player(self, player, state):
        if not state:
            return
        player.pos[:] = [float(v) for v in state.get("pos", player.pos)]
        player.vel[:] = [float(v) for v in state.get("motion", player.vel)]
        rotation = state.get("rotation", (0.0, 0.0))
        player.yaw, player.pitch = float(rotation[0]), float(rotation[1])
        player.on_ground = bool(state.get("on_ground", False))
        player._fall_distance = float(state.get("fall_distance", 0.0))
        player.health = max(0, min(20, int(state.get("health", 20))))
        player.prev_health = player.health
        player.air = max(-19, min(300, int(state.get("air", 300))))
        player._fire_ticks = max(-20, min(32767, int(state.get("fire_ticks", 0))))
        player.gamemode = int(state.get("gamemode", getattr(player, "gamemode", 0)))
        player.inventory.current_item = 0
        from game.entities.item import ItemStack
        player.inventory.main[:] = [None] * len(player.inventory.main)
        player.inventory.armor[:] = [None] * len(player.inventory.armor)
        for stack in state.get("inventory", []):
            slot = int(stack.get("slot", -1)) & 0xFF
            name = str(stack.get("id", ""))
            if name == "corruptor_arrow":
                try:
                    from game.app.mods import is_mod_enabled
                    if not is_mod_enabled("arrows", default=False):
                        continue
                except Exception:
                    pass
            item_stack = ItemStack(name, int(stack.get("count", 1)), int(stack.get("damage", 0)))
            if 0 <= slot < player.inventory.MAIN_SIZE:
                player.inventory.main[slot] = item_stack
            elif 100 <= slot < 100 + player.inventory.ARMOR_SIZE:
                player.inventory.armor[3 - (slot - 100)] = item_stack
        sync_camera = getattr(player, "sync_camera", None)
        if callable(sync_camera) and getattr(player, "renderer", None) is not None:
            sync_camera()

    def save_level(self, world, player=None):
        try:
            if not self.flush_pending_saves(wait_for_completion=True):
                return False
            nbtlib, tag = _tag_types()
            self.root.mkdir(parents=True, exist_ok=True)
            chunks_size = sum(
                p.stat().st_size for p in self.root.rglob("c.*.*.dat") if p.is_file()
            )
            data = tag.Compound({
                "RandomSeed": tag.Long(int(world.seed)),
                "SpawnX": tag.Int(int(getattr(world, "spawn_x", 0))),
                "SpawnY": tag.Int(int(getattr(world, "spawn_y", 64))),
                "SpawnZ": tag.Int(int(getattr(world, "spawn_z", 0))),
                "Time": tag.Long(int(world.world_time)),
                "SizeOnDisk": tag.Long(int(chunks_size)),
                "LastPlayed": tag.Long(int(time.time() * 1000)),
                "SnowCovered": tag.Byte(1 if getattr(world, "snow_covered", False) else 0),
                "DebugWorld": tag.Byte(1 if getattr(world, "debug_world", False) else 0),
                "McpyAccurateGeneration": tag.Byte(
                    1 if getattr(world, "accurate_generation", True) else 0
                ),
            })
            if player is not None:
                data["Player"] = self._encode_player(player, tag)
            elif self.level_data is not None and self.level_data.get("Player") is not None:
                data["Player"] = self._encode_player_state(self.level_data["Player"], tag)
            root = _new_gzip_nbt_file(nbtlib, {"Data": data})
            temp = self.root / "level.dat_new"
            root.save(str(temp), gzipped=True)
            _validate_written_nbt(nbtlib, temp, "Data")
            old = self.root / "level.dat_old"
            current = self.root / "level.dat"
            if current.exists():
                try:
                    os.replace(current, old)
                except OSError:
                    pass
            os.replace(temp, current)
            self.level_data = {
                "RandomSeed": int(world.seed), "SpawnX": int(world.spawn_x),
                "SpawnY": int(world.spawn_y), "SpawnZ": int(world.spawn_z),
                "Time": int(world.world_time), "SizeOnDisk": chunks_size,
                "SnowCovered": bool(getattr(world, "snow_covered", False)),
                "DebugWorld": bool(getattr(world, "debug_world", False)),
                "Player": (self._decode_player(data["Player"])
                           if "Player" in data else None),
            }
            return True
        except Exception as exc:
            print(f"Could not save level metadata: {exc}")
            try:
                if 'temp' in locals() and temp.exists():
                    temp.unlink()
            except OSError:
                pass
            return False

    @staticmethod
    def _encode_player_state(state, tag):
        inventory_tags = []
        custom_inventory = []
        for stack in state.get("inventory", []):
            slot = int(stack.get("slot", 0)) & 0xFF
            item_name = str(stack.get("id", ""))
            record = tag.Compound({
                "Slot": tag.Byte(signed_byte(slot)),
                "id": tag.Short(int(classic_item_id(item_name) or 0)),
                "Count": tag.Byte(signed_byte(stack.get("count", 1))),
                "Damage": tag.Short(int(stack.get("damage", 0))),
            })
            numeric_id = int(record["id"]) & 0xFFFF
            if item_name != item_name_from_classic_id(numeric_id):
                record["McpyId"] = tag.String(item_name)
            inventory_tags.append(record)
            custom_inventory.append(tag.Compound({
                "Slot": tag.Byte(signed_byte(slot)), "Id": tag.String(item_name),
            }))
        feet = state.get("pos", [0.0, 64.0, 0.0])
        java_pos = [
            float(feet[0]) + 0.5,
            float(feet[1]) + 1.62,
            float(feet[2]) + 0.5,
        ]
        return tag.Compound({
            "Pos": _float_list(tag, "Double", java_pos),
            "Motion": _float_list(tag, "Double", state.get("motion", [0.0, 0.0, 0.0])),
            "Rotation": _float_list(tag, "Float", _port_rotation_to_java(
                state.get("rotation", [0.0, 0.0])
            )),
            "FallDistance": tag.Float(float(state.get("fall_distance", 0.0))),
            "Fire": tag.Short(max(-20, min(32767, int(state.get("fire_ticks", 0))))),
            "Air": tag.Short(max(-19, min(300, int(state.get("air", 300))))),
            "OnGround": tag.Byte(1 if state.get("on_ground", False) else 0),
            "Health": tag.Short(max(0, min(20, int(state.get("health", 20))))),
            "HurtTime": tag.Short(0), "DeathTime": tag.Short(0), "AttackTime": tag.Short(0),
            "Inventory": tag.List[tag.Compound](inventory_tags),
            "McpyGamemode": tag.Int(int(state.get("gamemode", 0))),
            "McpyInventory": tag.List[tag.Compound](custom_inventory),
        })

    def _encode_player(self, player, tag):
        inventory_tags = []
        custom_inventory = []
        for slot, stack in enumerate(player.inventory.main):
            if stack is not None:
                inventory_tags.append(self._encode_item_stack(slot, stack, tag))
                custom_inventory.append(tag.Compound({
                    "Slot": tag.Byte(slot), "Id": tag.String(str(stack.id)),
                }))
        for armor_slot, stack in enumerate(player.inventory.armor):
            if stack is not None:
                slot = 100 + (3 - armor_slot)
                inventory_tags.append(self._encode_item_stack(slot, stack, tag))
                custom_inventory.append(tag.Compound({
                    "Slot": tag.Byte(slot), "Id": tag.String(str(stack.id)),
                }))
        eye = float(getattr(player, "EYE_HEIGHT", 1.62))
        java_pos = [
            float(player.pos[0]) + 0.5,
            float(player.pos[1]) + eye,
            float(player.pos[2]) + 0.5,
        ]
        record = tag.Compound({
            "Pos": _float_list(tag, "Double", java_pos),
            "Motion": _float_list(tag, "Double", player.vel),
            "Rotation": _float_list(tag, "Float", _port_rotation_to_java(
                (player.yaw, player.pitch)
            )),
            "FallDistance": tag.Float(float(getattr(
                player, "_fall_distance", getattr(player, "fall_distance", 0.0)))),
            "Fire": tag.Short(max(-20, min(32767, int(getattr(player, "_fire_ticks", 0))))),
            "Air": tag.Short(max(-19, min(300, int(getattr(player, "air", 300))))),
            "OnGround": tag.Byte(1 if player.on_ground else 0),
            "Health": tag.Short(max(0, min(20, int(getattr(player, "health", 20))))),
            "HurtTime": tag.Short(0), "DeathTime": tag.Short(0), "AttackTime": tag.Short(0),
            "Inventory": tag.List[tag.Compound](inventory_tags),
            "McpyGamemode": tag.Int(int(getattr(player, "gamemode", 0))),
            "McpyInventory": tag.List[tag.Compound](custom_inventory),
        })
        return record

    @staticmethod
    def _encode_item_stack(slot, stack, tag):
        name = str(stack.id)
        numeric_id = classic_item_id(name)
        if numeric_id is None:
            numeric_id = 0
        record = tag.Compound({
            "Slot": tag.Byte(signed_byte(slot)),
            "id": tag.Short(int(numeric_id)),
            "Count": tag.Byte(signed_byte(stack.count)),
            "Damage": tag.Short(int(stack.damage)),
        })
        if name != item_name_from_classic_id(numeric_id):
            record["McpyId"] = tag.String(name)
        return record

    def begin_save(self, world, player=None, *, stop_workers=False):
        return SaveJob(self, world, player, stop_workers=stop_workers)

    def save_world(self, world, player=None, *, stop_workers=False):
        job = self.begin_save(world, player, stop_workers=stop_workers)
        job.run_to_completion()
        return job.success


class SaveJob:

    def __init__(self, store, world, player=None, *, stop_workers=False):
        if stop_workers:
            world.shutdown(wait=True)
        self.store = store
        self.world = world
        self.player = player
        self._chunks = list(world.chunks.values())
        self._item_chunks = store.chunks_with_items(world)
        self._tile_entity_chunks = {
            tuple(world._get_chunk_index(x, z))
            for (x, _y, z), te in list(getattr(world, "tile_entities", {}).items())
            if te is not None
        }
        self._next = 0
        self._futures = []
        self._level_done = False
        self.success = True

    @property
    def total(self):
        return len(self._chunks) + 1

    @property
    def completed(self):
        return self._next + (1 if self._level_done else 0)

    @property
    def done(self):
        return self._level_done

    @property
    def progress(self):
        return min(1.0, self.completed / self.total)

    def step(self, max_chunks=2):
        if self._level_done:
            return True
        self.store.poll_pending_saves()
        submitted = 0
        while submitted < max(1, int(max_chunks)) and self._next < len(self._chunks):
            chunk = self._chunks[self._next]
            self._next += 1
            if self.world.chunks.get((chunk.cx, chunk.cz)) is not chunk:
                continue
            key = (chunk.cx, chunk.cz)
            if (key in self._tile_entity_chunks
                    or chunk.needs_saving(self.world, key in self._item_chunks)):
                future = self.store.queue_chunk_save(self.world, chunk)
                if future is False:
                    self.success = False
                else:
                    self._futures.append(future)
                submitted += 1
            else:
                submitted += 1

        if self._next < len(self._chunks):
            return False

        self.store.poll_pending_saves()
        if any(not future.done() for future in self._futures):
            return False
        self.store.poll_pending_saves()
        for future in self._futures:
            try:
                self.success = bool(future.result()) and self.success
            except Exception:
                self.success = False
        if self.store.has_pending_saves():
            return False
        if self.store._failed_snapshots:
            self.success = self.store.flush_pending_saves(wait_for_completion=True) and self.success
        else:
            self.success = self.store.flush_pending_saves(wait_for_completion=False) and self.success
        self.success = self.store.save_level(self.world, self.player) and self.success
        self._level_done = True
        return True

    def run_to_completion(self):
        while not self.step(64):
            with self.store._save_lock:
                pending = [record["future"] for record in self.store._pending_save_records]
            if pending:
                wait(pending)
        return self.success
