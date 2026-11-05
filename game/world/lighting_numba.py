from __future__ import annotations

import numpy as np
from numba import njit

NUMBA_AVAILABLE = True

@njit(cache=True, nogil=True, fastmath=False)
def initialize_skylight(blocks, skylight, heightmap, opacity_lut,
                        width, depth, height):
    for lx in range(width):
        for lz in range(depth):
            base = (lx * depth + lz) * height
            top = height
            found = False
            for y in range(height - 1, -1, -1):
                bid = int(blocks[base + y])
                if bid != 0 and int(opacity_lut[bid]) > 0:
                    top = y + 1
                    found = True
                    break
            if not found:
                top = 0
            heightmap[lz * width + lx] = top & 0xFF

            for y in range(top, height):
                skylight[base + y] = 15
            light = 15
            for y in range(top - 1, -1, -1):
                bid = int(blocks[base + y])
                opacity = int(opacity_lut[bid])
                if opacity == 0:
                    opacity = 1
                light -= opacity
                if light < 0:
                    light = 0
                skylight[base + y] = light
                if light == 0 and opacity >= 15:
                    for yy in range(y):
                        skylight[base + yy] = 0
                    break

@njit(cache=True, nogil=True, fastmath=False)
def propagate_light(blocks, light_map, opacity_lut, source_lut,
                    width, depth, height, seed_intrinsic):
    volume = width * depth * height
    queue = np.empty((16, volume), dtype=np.int32)
    head = np.zeros(16, dtype=np.int32)
    tail = np.zeros(16, dtype=np.int32)
    pending = np.zeros((16, volume), dtype=np.uint8)

    for idx in range(volume):
        if seed_intrinsic:
            light_map[idx] = source_lut[int(blocks[idx])]
        level = int(light_map[idx])
        if level > 1:
            queue[level, tail[level]] = idx
            tail[level] += 1
            pending[level, idx] = 1

    for level in range(15, 1, -1):
        while head[level] < tail[level]:
            idx = queue[level, head[level]]
            head[level] += 1
            pending[level, idx] = 0
            if int(light_map[idx]) != level:
                continue

            cur = level
            y = idx % height
            column = idx // height
            lz = column % depth
            lx = column // depth

            for direction in range(6):
                nidx = -1
                if direction == 0:
                    if lx + 1 < width:
                        nidx = idx + depth * height
                elif direction == 1:
                    if lx > 0:
                        nidx = idx - depth * height
                elif direction == 2:
                    if y + 1 < height:
                        nidx = idx + 1
                elif direction == 3:
                    if y > 0:
                        nidx = idx - 1
                elif direction == 4:
                    if lz + 1 < depth:
                        nidx = idx + height
                else:
                    if lz > 0:
                        nidx = idx - height
                if nidx < 0:
                    continue

                opacity = int(opacity_lut[int(blocks[nidx])])
                if opacity == 0:
                    opacity = 1
                new_light = cur - opacity
                if new_light < 0:
                    new_light = 0
                if new_light > int(light_map[nidx]):
                    light_map[nidx] = new_light
                    if new_light > 1 and pending[new_light, nidx] == 0:
                        queue[new_light, tail[new_light]] = nidx
                        tail[new_light] += 1
                        pending[new_light, nidx] = 1

@njit(cache=True, nogil=True, fastmath=False)
def relight_block_region(blocks, light, loaded, opacity_lut, emit_lut,
                         nx, nz, ny):
    cells = nx * nz * ny
    queue = np.empty(cells * 16, dtype=np.int32)
    qlevel = np.empty(cells * 16, dtype=np.uint8)
    head = 0
    tail = 0

    for i in range(1, nx - 1):
        for k in range(1, nz - 1):
            if loaded[i * nz + k] == 0:
                continue
            base = (i * nz + k) * ny
            for j in range(ny):
                bid = int(blocks[base + j])
                emitted = 0
                if bid != 0:
                    emitted = int(emit_lut[bid])
                light[base + j] = emitted
                if emitted > 1:
                    queue[tail] = base + j
                    qlevel[tail] = emitted
                    tail += 1

    for i in range(nx):
        for k in range(nz):
            if i > 0 and i < nx - 1 and k > 0 and k < nz - 1:
                continue
            base = (i * nz + k) * ny
            for j in range(ny):
                value = int(light[base + j])
                if value > 1:
                    queue[tail] = base + j
                    qlevel[tail] = value
                    tail += 1

    while head < tail:
        idx = int(queue[head])
        level = int(qlevel[head])
        head += 1
        j = idx % ny
        column = idx // ny
        k = column % nz
        i = column // nz
        for direction in range(6):
            ni = i
            nk = k
            nj = j
            if direction == 0:
                ni = i + 1
            elif direction == 1:
                ni = i - 1
            elif direction == 2:
                nj = j + 1
            elif direction == 3:
                nj = j - 1
            elif direction == 4:
                nk = k + 1
            else:
                nk = k - 1
            if ni < 1 or ni > nx - 2 or nk < 1 or nk > nz - 2:
                continue
            if nj < 0 or nj >= ny:
                continue
            if loaded[ni * nz + nk] == 0:
                continue
            nidx = (ni * nz + nk) * ny + nj
            opacity = int(opacity_lut[int(blocks[nidx])])
            if opacity == 0:
                opacity = 1
            new_light = level - opacity
            if new_light < 0:
                new_light = 0
            if new_light > int(light[nidx]):
                light[nidx] = new_light
                if new_light > 1:
                    queue[tail] = nidx
                    qlevel[tail] = new_light
                    tail += 1

@njit(cache=True, nogil=True, fastmath=False)
def relight_sky_region(blocks, light, loaded, allowed, seed_mask,
                       opacity_lut, nx, nz, ny):
    volume = nx * nz * ny
    queue = np.empty((16, volume), dtype=np.int32)
    queue_level = np.empty((16, volume), dtype=np.uint8)
    head = np.zeros(16, dtype=np.int32)
    tail = np.zeros(16, dtype=np.int32)
    pending = np.zeros((16, volume), dtype=np.uint8)

    for idx in range(volume):
        if seed_mask[idx] == 0:
            continue
        level = int(light[idx])
        if level > 1 and level < 16 and pending[level, idx] == 0:
            queue[level, tail[level]] = idx
            queue_level[level, tail[level]] = level
            tail[level] += 1
            pending[level, idx] = 1

    for level in range(15, 1, -1):
        while head[level] < tail[level]:
            idx = int(queue[level, head[level]])
            queued_level = int(queue_level[level, head[level]])
            head[level] += 1
            pending[queued_level, idx] = 0
            if int(light[idx]) != queued_level:
                continue

            y = idx % ny
            column = idx // ny
            z = column % nz
            x = column // nz

            for direction in range(6):
                ni = x
                nk = z
                nj = y
                if direction == 0:
                    ni = x + 1
                elif direction == 1:
                    ni = x - 1
                elif direction == 2:
                    nj = y + 1
                elif direction == 3:
                    nj = y - 1
                elif direction == 4:
                    nk = z + 1
                else:
                    nk = z - 1
                if ni < 0 or ni >= nx or nk < 0 or nk >= nz:
                    continue
                if nj < 0 or nj >= ny:
                    continue
                ncol = ni * nz + nk
                if loaded[ncol] == 0 or allowed[ncol] == 0:
                    continue
                nidx = ncol * ny + nj
                opacity = int(opacity_lut[int(blocks[nidx])])
                if opacity == 0:
                    opacity = 1
                new_light = queued_level - opacity
                if new_light < 0:
                    new_light = 0
                old_light = int(light[nidx])
                if new_light > old_light:
                    light[nidx] = new_light
                    if new_light > 1 and pending[new_light, nidx] == 0:
                        queue[new_light, tail[new_light]] = nidx
                        queue_level[new_light, tail[new_light]] = new_light
                        tail[new_light] += 1
                        pending[new_light, nidx] = 1
