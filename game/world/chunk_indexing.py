from __future__ import annotations

import numpy as np


def find_chunk_block_indices(chunk, block_types):
    wanted = block_types if isinstance(block_types, (set, frozenset)) else frozenset(block_types)
    if not wanted:
        return np.empty(0, dtype=np.intp)

    palette = getattr(chunk, "_id_to_block_type", ())
    lookup = np.zeros(256, dtype=np.bool_)
    matched = False
    for block_id, name in enumerate(palette):
        if name in wanted and 0 <= block_id < 256:
            lookup[block_id] = True
            matched = True
    if not matched:
        return np.empty(0, dtype=np.intp)

    blocks = np.frombuffer(chunk.blocks, dtype=np.uint8)
    return np.flatnonzero(lookup[blocks])
