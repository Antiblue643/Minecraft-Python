from __future__ import annotations

import numpy as np

_KEY_BIAS = 1 << 20
_KEY_STRIDE = 1 << 21


def supports_vector_sampling(world) -> bool:
    chunks = getattr(world, "chunks", None)
    return (
        isinstance(chunks, dict)
        and getattr(world, "CHUNK_SIZE", None) == 16
        and isinstance(getattr(world, "block_type_to_id", None), dict)
        and isinstance(getattr(world, "CHUNK_HEIGHT", None), int)
    )


def numpy_rng(world, rng):
    gen = getattr(world, "_sampling_np_rng", None)
    if gen is None:
        seed = rng.getrandbits(64) if hasattr(rng, "getrandbits") else 0x5EED
        gen = np.random.default_rng(seed)
        world._sampling_np_rng = gen
    return gen


def id_lookup_table(world, names):
    cache = getattr(world, "_sampling_lut_cache", None)
    if cache is None:
        cache = world._sampling_lut_cache = {}
    key = tuple(sorted(names))
    palette_size = len(world.block_type_to_id)
    entry = cache.get(key)
    if entry is not None and entry[0] == palette_size:
        return entry[1]
    lut = np.zeros(256, dtype=bool)
    for name in key:
        block_id = world.block_type_to_id.get(name)
        if block_id is not None and 0 < int(block_id) < 256:
            lut[int(block_id)] = True
    cache[key] = (palette_size, lut)
    return lut


def sampled_hits(world, xs, ys, zs, lut):
    height = world.CHUNK_HEIGHT
    inside = (ys >= 0) & (ys < height)
    if not inside.all():
        xs, ys, zs = xs[inside], ys[inside], zs[inside]
    if xs.size == 0:
        return []

    cx = xs >> 4
    cz = zs >> 4
    keys = (cx + _KEY_BIAS) * _KEY_STRIDE + (cz + _KEY_BIAS)
    order = np.argsort(keys, kind="stable")
    sorted_keys = keys[order]
    boundaries = np.flatnonzero(np.diff(sorted_keys)) + 1
    starts = np.concatenate(([0], boundaries))
    ends = np.concatenate((boundaries, [sorted_keys.size]))

    chunks = world.chunks
    hits = []
    for start, end in zip(starts.tolist(), ends.tolist()):
        key = int(sorted_keys[start])
        chunk = chunks.get((key // _KEY_STRIDE - _KEY_BIAS, key % _KEY_STRIDE - _KEY_BIAS))
        if chunk is None:
            continue
        sel = order[start:end]
        x_sel, y_sel, z_sel = xs[sel], ys[sel], zs[sel]
        linear = (((x_sel & 15) * chunk.depth) + (z_sel & 15)) * chunk.height + y_sel
        blocks = np.frombuffer(chunk.blocks, dtype=np.uint8)
        try:
            ids = blocks[linear]
        finally:
            del blocks
        flagged = np.flatnonzero(lut[ids])
        for j in flagged.tolist():
            hits.append((int(x_sel[j]), int(y_sel[j]), int(z_sel[j]), int(ids[j])))
    return hits
