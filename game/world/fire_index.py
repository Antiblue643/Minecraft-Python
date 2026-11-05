from __future__ import annotations

import math

from game.world.chunk_indexing import find_chunk_block_indices


class FirePositionIndex:

    def __init__(self, world):
        self.world = world
        self._by_chunk: dict[tuple[int, int], set[tuple[int, int, int]]] = {}

    def replace_chunk(self, chunk):
        chunk_x = int(getattr(chunk, "cx", int(chunk.start_x) // int(chunk.width)))
        chunk_z = int(getattr(chunk, "cz", int(chunk.start_z) // int(chunk.depth)))
        key = (chunk_x, chunk_z)
        positions = set()
        height = int(chunk.height)
        depth = int(chunk.depth)
        start_x = int(chunk.start_x)
        start_z = int(chunk.start_z)

        for raw_index in find_chunk_block_indices(chunk, {"fire"}):
            index = int(raw_index)
            y = index % height
            column = index // height
            local_z = column % depth
            local_x = column // depth
            positions.add((start_x + local_x, y, start_z + local_z))

        if positions:
            self._by_chunk[key] = positions
        else:
            self._by_chunk.pop(key, None)

    def remove_chunk(self, cx, cz):
        self._by_chunk.pop((int(cx), int(cz)), None)

    def on_block_changed(self, x, y, z, old_type, new_type):
        if old_type == new_type or (old_type != "fire" and new_type != "fire"):
            return
        key = self.world._get_chunk_index(x, z)
        positions = self._by_chunk.get(key)
        pos = (int(x), int(y), int(z))

        if old_type == "fire" and positions is not None:
            positions.discard(pos)
            if not positions:
                self._by_chunk.pop(key, None)
                positions = None

        if new_type == "fire":
            if positions is None:
                positions = self._by_chunk.setdefault(key, set())
            positions.add(pos)

    def nearby(self, player_x, player_z, radius):
        px, pz, radius = int(player_x), int(player_z), max(0, int(radius))
        chunk_size = max(1, int(getattr(self.world, "CHUNK_SIZE", 16)))
        min_cx = math.floor((px - radius + 0.5) / chunk_size)
        max_cx = math.floor((px + radius + 0.5) / chunk_size)
        min_cz = math.floor((pz - radius + 0.5) / chunk_size)
        max_cz = math.floor((pz + radius + 0.5) / chunk_size)

        result = []
        for (cx, cz), positions in self._by_chunk.items():
            if not (min_cx <= cx <= max_cx and min_cz <= cz <= max_cz):
                continue
            result.extend(
                sorted(
                    (pos for pos in positions
                     if abs(pos[0] - px) <= radius and abs(pos[2] - pz) <= radius),
                    key=lambda pos: (pos[0], pos[2], pos[1]),
                )
            )
        return result
