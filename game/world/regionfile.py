
from __future__ import annotations

import gzip
import struct
import threading
import time
import zlib
from pathlib import Path

SECTOR_BYTES = 4096
CHUNKS_PER_REGION = 1024
HEADER_BYTES = 2 * SECTOR_BYTES
VERSION_GZIP = 1
VERSION_DEFLATE = 2
MAX_SECTORS = 255

_SLOT = struct.Struct(">I")
_PAYLOAD_HEAD = struct.Struct(">IB")


def region_coords(cx: int, cz: int) -> tuple[int, int]:
    return int(cx) >> 5, int(cz) >> 5


def region_filename(cx: int, cz: int) -> str:
    rx, rz = region_coords(cx, cz)
    return f"r.{rx}.{rz}.mcr"


class RegionFile:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._offsets = [0] * CHUNKS_PER_REGION
        self._timestamps = [0] * CHUNKS_PER_REGION
        self._free: list[bool] = []
        self._load()

    def _load(self):
        with self._lock:
            self._offsets = [0] * CHUNKS_PER_REGION
            self._timestamps = [0] * CHUNKS_PER_REGION
            if not self.path.is_file():
                self._free = [False, False]
                return
            with open(self.path, "r+b") as handle:
                size = handle.seek(0, 2)
                if size < HEADER_BYTES:
                    handle.write(bytes(HEADER_BYTES - size))
                    size = HEADER_BYTES
                if size % SECTOR_BYTES:
                    pad = SECTOR_BYTES - (size % SECTOR_BYTES)
                    handle.write(bytes(pad))
                    size += pad
                handle.seek(0)
                header = handle.read(HEADER_BYTES)
            sector_count = size // SECTOR_BYTES
            self._free = [True] * sector_count
            self._free[0] = self._free[1] = False
            offsets = struct.unpack(">1024I", header[:SECTOR_BYTES])
            self._timestamps = list(struct.unpack(">1024I", header[SECTOR_BYTES:]))
            for index, value in enumerate(offsets):
                if value == 0:
                    continue
                start, count = value >> 8, value & 0xFF
                if start < 2 or count == 0 or start + count > sector_count:
                    print(f"Region {self.path.name}: ignoring invalid slot {index}")
                    continue
                self._offsets[index] = value
                for sector in range(start, start + count):
                    self._free[sector] = False

    def _ensure_file(self):
        if self.path.is_file():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "wb") as handle:
            handle.write(bytes(HEADER_BYTES))
        self._free = [False, False]

    @staticmethod
    def _out_of_bounds(x, z):
        return not (0 <= x < 32 and 0 <= z < 32)

    @staticmethod
    def _slot(x, z):
        return x + z * 32

    def has_chunk(self, x: int, z: int) -> bool:
        if self._out_of_bounds(x, z):
            return False
        with self._lock:
            return self._offsets[self._slot(x, z)] != 0

    def chunk_timestamp(self, x: int, z: int) -> int:
        if self._out_of_bounds(x, z):
            return 0
        with self._lock:
            return self._timestamps[self._slot(x, z)]

    def iter_chunks(self):
        with self._lock:
            slots = [i for i, v in enumerate(self._offsets) if v]
        for index in slots:
            yield index % 32, index // 32

    def file_size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def read_chunk(self, x: int, z: int):
        if self._out_of_bounds(x, z):
            return None
        with self._lock:
            value = self._offsets[self._slot(x, z)]
            if value == 0:
                return None
            start, count = value >> 8, value & 0xFF
            if start + count > len(self._free):
                return None
            try:
                with open(self.path, "rb") as handle:
                    handle.seek(start * SECTOR_BYTES)
                    head = handle.read(_PAYLOAD_HEAD.size)
                    if len(head) < _PAYLOAD_HEAD.size:
                        return None
                    length, version = _PAYLOAD_HEAD.unpack(head)
                    if length < 1 or length > SECTOR_BYTES * count:
                        print(f"Region {self.path.name}[{x},{z}]: invalid length {length}")
                        return None
                    payload = handle.read(length - 1)
                if len(payload) != length - 1:
                    return None
                if version == VERSION_GZIP:
                    return gzip.decompress(payload)
                if version == VERSION_DEFLATE:
                    return zlib.decompress(payload)
                print(f"Region {self.path.name}[{x},{z}]: unknown version {version}")
                return None
            except (OSError, EOFError, zlib.error) as exc:
                print(f"Region {self.path.name}[{x},{z}]: read failed: {exc}")
                return None

    def write_chunk(self, x: int, z: int, raw_nbt: bytes) -> bool:
        return self._write_compressed(x, z, zlib.compress(bytes(raw_nbt)))

    def _find_free_run(self, needed):
        run_start = -1
        run_length = 0
        for index, free in enumerate(self._free):
            if free:
                if run_length == 0:
                    run_start = index
                run_length += 1
                if run_length >= needed:
                    return run_start
            else:
                run_length = 0
        return -1

    def _write_compressed(self, x, z, data) -> bool:
        if self._out_of_bounds(x, z):
            return False
        length = len(data)
        needed = (length + 5) // SECTOR_BYTES + 1
        if needed > MAX_SECTORS:
            print(f"Region {self.path.name}[{x},{z}]: chunk too large ({length} bytes)")
            return False
        slot = self._slot(x, z)
        with self._lock:
            try:
                self._ensure_file()
                with open(self.path, "r+b") as handle:
                    old = self._offsets[slot]
                    old_start, old_count = old >> 8, old & 0xFF
                    if old_start != 0 and old_count == needed:
                        self._write_payload(handle, old_start, data)
                    else:
                        start = self._find_free_run(needed)
                        if start == -1:
                            start = len(self._free)
                            handle.seek(0, 2)
                            handle.write(bytes(needed * SECTOR_BYTES))
                            self._free.extend([False] * needed)
                        else:
                            for sector in range(start, start + needed):
                                self._free[sector] = False
                        self._write_payload(handle, start, data)
                        new_value = (start << 8) | needed
                        self._write_slot(handle, slot * 4, new_value)
                        self._offsets[slot] = new_value
                        for sector in range(old_start, old_start + old_count):
                            if sector < len(self._free):
                                self._free[sector] = True
                    stamp = int(time.time())
                    self._write_slot(handle, SECTOR_BYTES + slot * 4, stamp)
                    self._timestamps[slot] = stamp
                    handle.flush()
                return True
            except OSError as exc:
                print(f"Region {self.path.name}[{x},{z}]: write failed: {exc}")
                self._load()
                return False

    @staticmethod
    def _write_payload(handle, sector, data):
        handle.seek(sector * SECTOR_BYTES)
        handle.write(_PAYLOAD_HEAD.pack(len(data) + 1, VERSION_DEFLATE))
        handle.write(data)

    @staticmethod
    def _write_slot(handle, position, value):
        handle.seek(position)
        handle.write(_SLOT.pack(value & 0xFFFFFFFF))

    def delete_chunk(self, x: int, z: int) -> bool:
        if self._out_of_bounds(x, z):
            return False
        slot = self._slot(x, z)
        with self._lock:
            old = self._offsets[slot]
            if old == 0:
                return True
            try:
                with open(self.path, "r+b") as handle:
                    self._write_slot(handle, slot * 4, 0)
                    self._write_slot(handle, SECTOR_BYTES + slot * 4, 0)
                    handle.flush()
            except OSError as exc:
                print(f"Region {self.path.name}[{x},{z}]: delete failed: {exc}")
                return False
            start, count = old >> 8, old & 0xFF
            for sector in range(start, start + count):
                if sector < len(self._free):
                    self._free[sector] = True
            self._offsets[slot] = 0
            self._timestamps[slot] = 0
            return True


class RegionFileCache:

    def __init__(self, world_root):
        self.region_dir = Path(world_root) / "region"
        self._files: dict[Path, RegionFile] = {}
        self._lock = threading.RLock()

    def get(self, cx: int, cz: int, create: bool = False):
        path = self.region_dir / region_filename(cx, cz)
        with self._lock:
            region = self._files.get(path)
            if region is None:
                if not create and not path.is_file():
                    return None
                region = RegionFile(path)
                self._files[path] = region
            return region

    def has_chunk(self, cx: int, cz: int) -> bool:
        region = self.get(cx, cz)
        return region is not None and region.has_chunk(int(cx) & 31, int(cz) & 31)

    def read_chunk(self, cx: int, cz: int):
        region = self.get(cx, cz)
        if region is None:
            return None
        return region.read_chunk(int(cx) & 31, int(cz) & 31)

    def write_chunk(self, cx: int, cz: int, raw_nbt: bytes) -> bool:
        return self.get(cx, cz, create=True).write_chunk(
            int(cx) & 31, int(cz) & 31, raw_nbt
        )

    def delete_chunk(self, cx: int, cz: int) -> bool:
        region = self.get(cx, cz)
        if region is None:
            return True
        return region.delete_chunk(int(cx) & 31, int(cz) & 31)

    def total_size(self) -> int:
        try:
            return sum(
                p.stat().st_size for p in self.region_dir.glob("r.*.*.mcr") if p.is_file()
            )
        except OSError:
            return 0

    def close(self):
        with self._lock:
            self._files.clear()
