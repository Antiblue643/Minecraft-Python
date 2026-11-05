from __future__ import annotations

import math

import numpy as np
from numba import njit

from game.world.terrain_numba import (
    _MASK, _MULTIPLIER, _next_bits, carve_cave_region,
)

_F_PI = float(np.float32(math.pi))
_F_SIN_SCALE = float(np.float32(10430.378))

SIN_TABLE = np.asarray(
    [float(np.float32(math.sin(i * math.pi * 2.0 / 65536.0)))
     for i in range(65536)],
    dtype=np.float64,
)


@njit(cache=True, nogil=True, fastmath=False)
def _f32(value):
    return np.float64(np.float32(value))


@njit(cache=True, nogil=True, fastmath=False)
def _sin_f(table, angle):
    return table[int(_f32(_f32(angle) * _F_SIN_SCALE)) & 65535]


@njit(cache=True, nogil=True, fastmath=False)
def _cos_f(table, angle):
    return table[int(_f32(_f32(angle) * _F_SIN_SCALE + 16384.0)) & 65535]


@njit(cache=True, nogil=True, fastmath=False)
def _next_int(state, bound):
    if (bound & (bound - 1)) == 0:
        state, bits = _next_bits(state, 31)
        return state, (bound * bits) >> 31
    while True:
        state, bits = _next_bits(state, 31)
        value = bits % bound
        if bits - value + (bound - 1) <= 0x7FFFFFFF:
            return state, value


@njit(cache=True, nogil=True, fastmath=False)
def _next_float(state):
    state, bits = _next_bits(state, 24)
    return state, bits / 16777216.0


@njit(cache=True, nogil=True, fastmath=False)
def _next_long(state):
    state, high = _next_bits(state, 32)
    state, low = _next_bits(state, 32)
    if high >= 2147483648:
        high -= 4294967296
    if low >= 2147483648:
        low -= 4294967296
    return state, (high << 32) + low


@njit(cache=True, nogil=True, fastmath=False)
def _trunc_div2(value):
    if value < 0:
        return (value >> 1) + (value & 1)
    return value >> 1


@njit(cache=True, nogil=True, fastmath=False)
def _floor_double(value):
    integer = int(value)
    return integer - 1 if value < integer else integer


@njit(cache=True, nogil=True, fastmath=False)
def _carve_cave_tree(blocks, cx, cz, rand_state, x_in, y_in, z_in, width_in,
                     yaw_in, pitch_in, start_in, end_in, vscale_in, sin_table,
                     water_id, stone_id, dirt_id, grass_id, lava_id):
    stack = np.empty((16, 9), dtype=np.float64)
    stack[0, 0] = x_in
    stack[0, 1] = y_in
    stack[0, 2] = z_in
    stack[0, 3] = width_in
    stack[0, 4] = yaw_in
    stack[0, 5] = pitch_in
    stack[0, 6] = float(start_in)
    stack[0, 7] = float(end_in)
    stack[0, 8] = vscale_in
    sp = 1
    center_x = float(cx * 16 + 8)
    center_z = float(cz * 16 + 8)

    while sp > 0:
        sp -= 1
        x = stack[sp, 0]
        y = stack[sp, 1]
        z = stack[sp, 2]
        width = _f32(stack[sp, 3])
        yaw = _f32(stack[sp, 4])
        pitch = _f32(stack[sp, 5])
        start_step = int(stack[sp, 6])
        end_step = int(stack[sp, 7])
        vertical_scale = stack[sp, 8]

        yaw_delta = 0.0
        pitch_delta = 0.0
        rand_state, seed_long = _next_long(rand_state)
        rng = (seed_long ^ _MULTIPLIER) & _MASK

        if end_step <= 0:
            rng, drawn = _next_int(rng, 28)
            end_step = 112 - drawn
        is_large = False
        if start_step == -1:
            start_step = end_step // 2
            is_large = True
        rng, drawn = _next_int(rng, end_step // 2)
        split_step = drawn + end_step // 4
        rng, drawn = _next_int(rng, 6)
        gentle = drawn == 0

        step = start_step
        while step < end_step:
            angle = _f32(_f32(_f32(float(step) * _F_PI)) / _f32(float(end_step)))
            radius = 1.5 + _f32(_f32(_sin_f(sin_table, angle) * width) * 1.0)
            vertical_radius = radius * vertical_scale
            cos_pitch = _cos_f(sin_table, pitch)
            sin_pitch = _sin_f(sin_table, pitch)
            x += _f32(_cos_f(sin_table, yaw) * cos_pitch)
            y += sin_pitch
            z += _f32(_sin_f(sin_table, yaw) * cos_pitch)
            if gentle:
                pitch = _f32(pitch * _f32(0.92))
            else:
                pitch = _f32(pitch * _f32(0.7))
            pitch = _f32(pitch + _f32(pitch_delta * _f32(0.1)))
            yaw = _f32(yaw + _f32(yaw_delta * _f32(0.1)))
            pitch_delta = _f32(pitch_delta * _f32(0.9))
            yaw_delta = _f32(yaw_delta * _f32(12.0 / 16.0))
            rng, fa = _next_float(rng)
            rng, fb = _next_float(rng)
            rng, fc = _next_float(rng)
            pitch_delta = _f32(pitch_delta + _f32(_f32(_f32(fa - fb) * fc) * _f32(2.0)))
            rng, fa = _next_float(rng)
            rng, fb = _next_float(rng)
            rng, fc = _next_float(rng)
            yaw_delta = _f32(yaw_delta + _f32(_f32(_f32(fa - fb) * fc) * _f32(4.0)))

            if (not is_large) and step == split_step and width > 1.0:
                rng, fa = _next_float(rng)
                child_width_1 = _f32(_f32(fa * 0.5) + 0.5)
                child_yaw_1 = _f32(yaw - _f32(_F_PI * 0.5))
                child_pitch = _f32(pitch / _f32(3.0))
                rng, fa = _next_float(rng)
                child_width_2 = _f32(_f32(fa * 0.5) + 0.5)
                child_yaw_2 = _f32(yaw + _f32(_F_PI * 0.5))
                stack[sp, 0] = x
                stack[sp, 1] = y
                stack[sp, 2] = z
                stack[sp, 3] = child_width_2
                stack[sp, 4] = child_yaw_2
                stack[sp, 5] = child_pitch
                stack[sp, 6] = float(step)
                stack[sp, 7] = float(end_step)
                stack[sp, 8] = 1.0
                sp += 1
                stack[sp, 0] = x
                stack[sp, 1] = y
                stack[sp, 2] = z
                stack[sp, 3] = child_width_1
                stack[sp, 4] = child_yaw_1
                stack[sp, 5] = child_pitch
                stack[sp, 6] = float(step)
                stack[sp, 7] = float(end_step)
                stack[sp, 8] = 1.0
                sp += 1
                break

            run_step = True
            if not is_large:
                rng, drawn = _next_int(rng, 4)
                run_step = drawn != 0
            if run_step:
                dx = x - center_x
                dz = z - center_z
                remaining = float(end_step - step)
                width_pad = width + 2.0 + 16.0
                if dx * dx + dz * dz - remaining * remaining > width_pad * width_pad:
                    break
                if not (center_x - 16.0 - radius * 2.0 <= x <= center_x + 16.0 + radius * 2.0
                        and center_z - 16.0 - radius * 2.0 <= z <= center_z + 16.0 + radius * 2.0):
                    step += 1
                    continue
                x0 = max(0, _floor_double(x - radius) - cx * 16 - 1)
                x1 = min(16, _floor_double(x + radius) - cx * 16 + 1)
                y0 = max(1, _floor_double(y - vertical_radius) - 1)
                y1 = min(120, _floor_double(y + vertical_radius) + 1)
                z0 = max(0, _floor_double(z - radius) - cz * 16 - 1)
                z1 = min(16, _floor_double(z + radius) - cz * 16 + 1)
                touches_water = carve_cave_region(
                    blocks, cx, cz, x, y, z, radius, vertical_radius,
                    x0, x1, y0, y1, z0, z1,
                    water_id, stone_id, dirt_id, grass_id, lava_id,
                )
                if (not touches_water) and is_large:
                    break
            step += 1
    return rand_state


@njit(cache=True, nogil=True, fastmath=False)
def generate_caves(blocks, cx, cz, seed48, sin_table,
                   water_id, stone_id, dirt_id, grass_id, lava_id):
    state = (seed48 ^ _MULTIPLIER) & _MASK
    state, long_x = _next_long(state)
    odd_x = _trunc_div2(long_x) * 2 + 1
    state, long_z = _next_long(state)
    odd_z = _trunc_div2(long_z) * 2 + 1

    for source_x in range(cx - 8, cx + 9):
        for source_z in range(cz - 8, cz + 9):
            source_seed = (source_x * odd_x + source_z * odd_z) ^ seed48
            state = (source_seed ^ _MULTIPLIER) & _MASK
            state, a = _next_int(state, 40)
            state, b = _next_int(state, a + 1)
            state, cave_count = _next_int(state, b + 1)
            state, gate = _next_int(state, 15)
            if gate != 0:
                cave_count = 0
            for _cave in range(cave_count):
                state, ox = _next_int(state, 16)
                state, span = _next_int(state, 120)
                state, y = _next_int(state, span + 8)
                state, oz = _next_int(state, 16)
                x = float(source_x * 16 + ox)
                yf = float(y)
                z = float(source_z * 16 + oz)
                branches = 1
                state, big = _next_int(state, 4)
                if big == 0:
                    state, fr = _next_float(state)
                    radius = _f32(1.0 + _f32(fr * _f32(6.0)))
                    state = _carve_cave_tree(
                        blocks, cx, cz, state, x, yf, z, radius, 0.0, 0.0,
                        -1, -1, 0.5, sin_table,
                        water_id, stone_id, dirt_id, grass_id, lava_id,
                    )
                    state, extra = _next_int(state, 4)
                    branches += extra
                for _branch in range(branches):
                    state, f1 = _next_float(state)
                    yaw = _f32(_f32(f1 * _F_PI) * 2.0)
                    state, f2 = _next_float(state)
                    pitch = _f32(_f32(f2 - 0.5) * 2.0)
                    pitch = _f32(pitch / _f32(8.0))
                    state, f3 = _next_float(state)
                    state, f4 = _next_float(state)
                    radius = _f32(_f32(f3 * 2.0) + f4)
                    state = _carve_cave_tree(
                        blocks, cx, cz, state, x, yf, z, radius, yaw, pitch,
                        0, 0, 1.0, sin_table,
                        water_id, stone_id, dirt_id, grass_id, lava_id,
                    )
    return state
