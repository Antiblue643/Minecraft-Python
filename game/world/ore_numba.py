from __future__ import annotations

import numpy as np
from numba import njit

from game.world.terrain_numba import _next_double
from game.world.cave_numba import (
    SIN_TABLE, _F_PI, _cos_f, _f32, _next_float, _next_int, _sin_f,
)

STATUS_OK = 0
STATUS_OUT_OF_REGION = 1
STATUS_CAPACITY = 2

CHUNK_VOLUME = 16 * 16 * 128
MAX_PLACEMENTS = 16384


@njit(cache=True, nogil=True, fastmath=False)
def _sample_ore_vein_height(state, height):
    height = int(height)
    if height < 0:
        state, first_y = _next_int(state, -height)
        state, second_y = _next_int(state, -height)
        return state, first_y + second_y
    return _next_int(state, height)


@njit(cache=True, nogil=True, fastmath=False)
def populate_ore_veins(blocks4, start_x, start_z, state, stone_id, ore_ids,
                       vein_sizes, attempts, heights, sin_table, out):
    count = 0
    for ore_index in range(ore_ids.shape[0]):
        vein_size = int(vein_sizes[ore_index])
        ore_id = ore_ids[ore_index]
        for _attempt in range(int(attempts[ore_index])):
            state, rx = _next_int(state, 16)
            height = int(heights[ore_index])
            state, y = _sample_ore_vein_height(state, height)
            state, rz = _next_int(state, 16)
            x = start_x + rx
            z = start_z + rz

            state, fa = _next_float(state)
            angle = _f32(fa * _F_PI)
            sin_angle = _sin_f(sin_table, angle)
            cos_angle = _cos_f(sin_table, angle)
            float_size = _f32(float(vein_size))
            x8 = _f32(float(x + 8))
            z8 = _f32(float(z + 8))
            dx_off = _f32(_f32(sin_angle * float_size) / _f32(8.0))
            dz_off = _f32(_f32(cos_angle * float_size) / _f32(8.0))
            start_xf = _f32(x8 + dx_off)
            end_xf = _f32(x8 - dx_off)
            start_zf = _f32(z8 + dz_off)
            end_zf = _f32(z8 - dz_off)
            state, d1 = _next_int(state, 3)
            start_yf = float(y + d1 + 2)
            state, d2 = _next_int(state, 3)
            end_yf = float(y + d2 + 2)

            for step in range(vein_size + 1):
                t = float(step) / float(vein_size)
                center_x = start_xf + (end_xf - start_xf) * t
                center_y = start_yf + (end_yf - start_yf) * t
                center_z = start_zf + (end_zf - start_zf) * t
                state, rd = _next_double(state)
                random_radius = rd * float(vein_size) / 16.0
                step_angle = _f32(_f32(float(step) * _F_PI) / float_size)
                factor = _f32(_sin_f(sin_table, step_angle) + _f32(1.0))
                radius_xz = factor * random_radius + 1.0
                radius_y = factor * random_radius + 1.0
                half_xz = radius_xz / 2.0
                half_y = radius_y / 2.0
                bx_lo = int(center_x - half_xz)
                bx_hi = int(center_x + half_xz)
                by_lo = int(center_y - half_y)
                by_hi = int(center_y + half_y)
                bz_lo = int(center_z - half_xz)
                bz_hi = int(center_z + half_xz)
                for bx in range(bx_lo, bx_hi + 1):
                    nx = (float(bx) + 0.5 - center_x) / half_xz
                    for by in range(by_lo, by_hi + 1):
                        ny = (float(by) + 0.5 - center_y) / half_y
                        for bz in range(bz_lo, bz_hi + 1):
                            nz = (float(bz) + 0.5 - center_z) / half_xz
                            if nx * nx + ny * ny + nz * nz < 1.0:
                                if by < 0 or by >= 128:
                                    continue
                                lx = bx - start_x
                                lz = bz - start_z
                                if lx < 0 or lx >= 32 or lz < 0 or lz >= 32:
                                    return STATUS_OUT_OF_REGION, state, count
                                slot = (lx >> 4) + 2 * (lz >> 4)
                                index = ((lx & 15) * 16 + (lz & 15)) * 128 + by
                                if blocks4[slot, index] == stone_id:
                                    if count >= out.shape[0]:
                                        return STATUS_CAPACITY, state, count
                                    blocks4[slot, index] = ore_id
                                    out[count, 0] = bx
                                    out[count, 1] = by
                                    out[count, 2] = bz
                                    out[count, 3] = ore_index
                                    count += 1
    return STATUS_OK, state, count
