from __future__ import annotations

import heapq

TICK_RATES = {
    "sand": 3,
    "gravel": 3,
    "redstone_ore_lit": 30,
    "redstone_torch": 2,
    "unlit_redstone_torch": 2,
}

MAX_UPDATES_PER_TICK = 1000


class BlockUpdateScheduler:
    def __init__(self, world):
        self.world = world
        self._heap = []
        self._pending = set()
        self._sequence = 0

    @property
    def pending_count(self):
        return len(self._pending)

    def schedule(self, x, y, z, block_type, delay):
        key = (int(x), int(y), int(z), block_type)
        if key in self._pending:
            return False
        world = self.world
        if world._get_chunk_index(key[0], key[2]) not in world.chunks:
            return False
        due = int(world.world_time) + max(0, int(delay))
        self._sequence += 1
        heapq.heappush(self._heap, (due, self._sequence, key))
        self._pending.add(key)
        return True

    def pending_for_chunk(self, cx, cz):
        world = self.world
        now = int(world.world_time)
        chunk_key = (int(cx), int(cz))
        result = []
        for due, _seq, key in sorted(self._heap):
            if key not in self._pending:
                continue
            x, y, z, block_type = key
            if world._get_chunk_index(x, z) != chunk_key:
                continue
            result.append((x, y, z, block_type, max(0, int(due) - now)))
        return result

    def shift_world_time(self, delta):
        delta = int(delta)
        if not delta or not self._heap:
            return 0
        self._heap = [
            (due + delta, sequence, key)
            for due, sequence, key in self._heap
        ]
        heapq.heapify(self._heap)
        return len(self._heap)

    def process_due(self, handlers, limit=MAX_UPDATES_PER_TICK):
        world = self.world
        now = int(world.world_time)
        processed = 0
        while self._heap and self._heap[0][0] <= now and processed < limit:
            _due, _seq, key = heapq.heappop(self._heap)
            self._pending.discard(key)
            processed += 1
            x, y, z, block_type = key
            if world._get_chunk_index(x, z) not in world.chunks:
                continue
            if world.get_block_type(x, y, z) != block_type:
                continue
            handler = handlers.get(block_type)
            if handler is not None:
                handler(world, x, y, z)
        return processed
