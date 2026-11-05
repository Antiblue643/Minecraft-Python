from __future__ import annotations

import numpy as np
from numba import njit

NUMBA_AVAILABLE = True

@njit(cache=True, nogil=True, fastmath=False)
def _lerp(t, a, b):
    return a + t * (b - a)

@njit(cache=True, nogil=True, fastmath=False)
def _fade(t):
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)

@njit(cache=True, nogil=True, fastmath=False)
def _grad(hash_value, x, y, z):
    h = hash_value & 15
    u = x if h < 8 else y
    v = y if h < 4 else (z if h != 12 and h != 14 else x)
    return (u if (h & 1) == 0 else -u) + (v if (h & 2) == 0 else -v)

@njit(cache=True, nogil=True, fastmath=False)
def _java_noise_floor(value):
    if value != value:
        result = 0
    elif value >= 2147483647.0:
        result = 2147483647
    elif value <= -2147483648.0:
        result = -2147483648
    else:
        result = int(value)
    if value < result:
        result -= 1
        if result < -2147483648:
            result = 2147483647
    return result

@njit(cache=True, nogil=True, fastmath=False)
def surface_heights(blocks, width, depth, height, water_id, lava_id):
    heights = np.empty(width * depth, dtype=np.int16)
    for x in range(width):
        for z in range(depth):
            base = (x * depth + z) * height
            top = -1
            for y in range(height - 1, -1, -1):
                bid = int(blocks[base + y])
                if bid != 0 and bid != water_id and bid != lava_id:
                    top = y
                    break
            heights[x * depth + z] = top
    return heights

@njit(cache=True, nogil=True, fastmath=False)
def perlin_add_to_array(p, output, x, y, z, size_x, size_y, size_z,
                        scale_x, scale_y, scale_z, inverse_frequency,
                        offset_x, offset_y, offset_z):
    if size_y == 1:
        index = 0
        for ix in range(size_x):
            nx = (x + float(ix)) * scale_x + offset_x
            ix_floor = _java_noise_floor(nx)
            xi = ix_floor & 255
            xf = nx - ix_floor
            fade_x = _fade(xf)
            for iz in range(size_z):
                nz = (z + float(iz)) * scale_z + offset_z
                iz_floor = _java_noise_floor(nz)
                zi = iz_floor & 255
                zf = nz - iz_floor
                fade_z = _fade(zf)
                aa = p[p[xi]] + zi
                ba = p[p[xi + 1]] + zi
                x0 = _lerp(
                    fade_x,
                    _grad(p[aa], xf, 0.0, zf),
                    _grad(p[ba], xf - 1.0, 0.0, zf),
                )
                x1 = _lerp(
                    fade_x,
                    _grad(p[aa + 1], xf, 0.0, zf - 1.0),
                    _grad(p[ba + 1], xf - 1.0, 0.0, zf - 1.0),
                )
                output[index] += _lerp(fade_z, x0, x1) * inverse_frequency
                index += 1
        return
    index = 0
    last_y = -1
    g00 = 0.0
    g01 = 0.0
    g10 = 0.0
    g11 = 0.0
    for ix in range(size_x):
        nx = (x + float(ix)) * scale_x + offset_x
        ix_floor = _java_noise_floor(nx)
        xi = ix_floor & 255
        xf = nx - ix_floor
        fade_x = _fade(xf)
        for iz in range(size_z):
            nz = (z + float(iz)) * scale_z + offset_z
            iz_floor = _java_noise_floor(nz)
            zi = iz_floor & 255
            zf = nz - iz_floor
            fade_z = _fade(zf)
            for iy in range(size_y):
                ny = (y + float(iy)) * scale_y + offset_y
                iy_floor = _java_noise_floor(ny)
                yi = iy_floor & 255
                yf = ny - iy_floor
                fade_y = _fade(yf)
                if iy == 0 or yi != last_y:
                    last_y = yi
                    a = p[xi] + yi
                    b = p[xi + 1] + yi
                    aa = p[a] + zi
                    ab = p[a + 1] + zi
                    ba = p[b] + zi
                    bb = p[b + 1] + zi
                    g00 = _lerp(fade_x,
                                _grad(p[aa], xf, yf, zf),
                                _grad(p[ba], xf - 1.0, yf, zf))
                    g01 = _lerp(fade_x,
                                _grad(p[ab], xf, yf - 1.0, zf),
                                _grad(p[bb], xf - 1.0, yf - 1.0, zf))
                    g10 = _lerp(fade_x,
                                _grad(p[aa + 1], xf, yf, zf - 1.0),
                                _grad(p[ba + 1], xf - 1.0, yf, zf - 1.0))
                    g11 = _lerp(fade_x,
                                _grad(p[ab + 1], xf, yf - 1.0, zf - 1.0),
                                _grad(p[bb + 1], xf - 1.0, yf - 1.0, zf - 1.0))
                y0 = _lerp(fade_y, g00, g01)
                y1 = _lerp(fade_y, g10, g11)
                output[index] += _lerp(fade_z, y0, y1) * inverse_frequency
                index += 1

@njit(cache=True, nogil=True, fastmath=False)
def fill_all_terrain(blocks, field, stone_id, water_id):
    for gx in range(4):
        for gz in range(4):
            i00 = (gx * 5 + gz) * 17
            i01 = (gx * 5 + gz + 1) * 17
            i10 = ((gx + 1) * 5 + gz) * 17
            i11 = ((gx + 1) * 5 + gz + 1) * 17
            n00, n01, n10, n11 = field[i00], field[i01], field[i10], field[i11]
            dy00 = (field[i00 + 1] - n00) * 0.125
            dy01 = (field[i01 + 1] - n01) * 0.125
            dy10 = (field[i10 + 1] - n10) * 0.125
            dy11 = (field[i11 + 1] - n11) * 0.125
            for gy in range(16):
                for sy in range(8):
                    cur00, cur01 = n00, n01
                    step_x00 = (n10 - n00) * 0.25
                    step_x01 = (n11 - n01) * 0.25
                    density_x0, density_x1 = cur00, cur01
                    for sx in range(4):
                        density = density_x0
                        step_z = (density_x1 - density_x0) * 0.25
                        x = gx * 4 + sx
                        for sz in range(4):
                            z = gz * 4 + sz
                            y = gy * 8 + sy
                            block = water_id if y < 64 else 0
                            if density > 0.0:
                                block = stone_id
                            blocks[(x * 16 + z) * 128 + y] = block
                            density += step_z
                        density_x0 += step_x00
                        density_x1 += step_x01
                    n00 += dy00
                    n01 += dy01
                    n10 += dy10
                    n11 += dy11
                n00, n01, n10, n11 = (
                    field[i00 + gy + 1], field[i01 + gy + 1],
                    field[i10 + gy + 1], field[i11 + gy + 1],
                )
                if gy < 15:
                    dy00 = (field[i00 + gy + 2] - n00) * 0.125
                    dy01 = (field[i01 + gy + 2] - n01) * 0.125
                    dy10 = (field[i10 + gy + 2] - n10) * 0.125
                    dy11 = (field[i11 + gy + 2] - n11) * 0.125

@njit(cache=True, nogil=True, fastmath=False)
def carve_cave_region(blocks, cx, cz, x, y, z, radius, vertical_radius,
                      x0, x1, y0, y1, z0, z1,
                      water_id, stone_id, dirt_id, grass_id, lava_id):
    touches_water = False
    ix = x0
    while not touches_water and ix < x1:
        iz = z0
        while not touches_water and iz < z1:
            iy = y1 + 1
            while not touches_water and iy >= y0 - 1:
                if 0 <= iy < 128:
                    block_id = blocks[(ix * 16 + iz) * 128 + iy]
                    if block_id == water_id:
                        touches_water = True
                    if (iy != y0 - 1 and ix != x0 and ix != x1 - 1
                            and iz != z0 and iz != z1 - 1):
                        iy = y0
                iy -= 1
            iz += 1
        ix += 1

    if touches_water:
        return True
    for local_x in range(x0, x1):
        norm_x = (local_x + cx * 16 + 0.5 - x) / radius
        for local_z in range(z0, z1):
            norm_z = (local_z + cz * 16 + 0.5 - z) / radius
            horizontal = norm_x * norm_x + norm_z * norm_z
            if horizontal >= 1.0:
                continue
            index = (local_x * 16 + local_z) * 128 + y1
            had_grass = False
            for carve_y in range(y1 - 1, y0 - 1, -1):
                norm_y = (carve_y + 0.5 - y) / vertical_radius
                if norm_y > -0.7 and horizontal + norm_y * norm_y < 1.0:
                    block_id = blocks[index]
                    if block_id == grass_id:
                        had_grass = True
                    if block_id == stone_id or block_id == dirt_id or block_id == grass_id:
                        if carve_y < 10:
                            blocks[index] = lava_id
                        else:
                            blocks[index] = 0
                            if had_grass and blocks[index - 1] == dirt_id:
                                blocks[index - 1] = grass_id
                index -= 1
    return False

_MULTIPLIER = 0x5DEECE66D
_ADDEND = 0xB
_MASK = (1 << 48) - 1

@njit(cache=True, nogil=True, fastmath=False)
def _next_bits(state, bits):
    state = (state * _MULTIPLIER + _ADDEND) & _MASK
    return state, state >> (48 - bits)

@njit(cache=True, nogil=True, fastmath=False)
def _next_int_bound(state, bound):
    while True:
        state, bits = _next_bits(state, 31)
        value = bits % bound
        if bits - value + (bound - 1) <= 0x7FFFFFFF:
            return state, value

@njit(cache=True, nogil=True, fastmath=False)
def _next_double(state):
    state, high = _next_bits(state, 26)
    state, low = _next_bits(state, 27)
    return state, ((high << 27) + low) / 9007199254740992.0

@njit(cache=True, nogil=True, fastmath=False)
def replace_surface_blocks(blocks, sand_noise, gravel_noise, stone_noise,
                           biome_top_ids, biome_filler_ids,
                           bedrock_id, sand_id, gravel_id,
                           stone_id, water_id, chunk_seed):
    state = ((chunk_seed ^ _MULTIPLIER) & _MASK)
    for x in range(16):
        for z in range(16):
            noise_index = x + z * 16
            column_index = x * 16 + z
            state, random_value = _next_double(state)
            sand_flag = sand_noise[noise_index] + random_value * 0.2 > 0.0
            state, random_value = _next_double(state)
            gravel_flag = gravel_noise[noise_index] + random_value * 0.2 > 3.0
            state, random_value = _next_double(state)
            stone_depth = int(stone_noise[noise_index] / 3.0 + 3.0 + random_value * 0.25)
            remaining = -1
            top_id = int(biome_top_ids[column_index])
            filler_id = int(biome_filler_ids[column_index])
            for y in range(127, -1, -1):
                state, bedrock_roll = _next_int_bound(state, 5)
                index = (x * 16 + z) * 128 + y
                if y <= bedrock_roll:
                    blocks[index] = bedrock_id
                else:
                    block_id = blocks[index]
                    if block_id == 0:
                        remaining = -1
                    elif block_id == stone_id:
                        if remaining == -1:
                            if stone_depth <= 0:
                                top_id = 0
                                filler_id = stone_id
                            elif 60 <= y <= 65:
                                top_id = int(biome_top_ids[column_index])
                                filler_id = int(biome_filler_ids[column_index])
                                if gravel_flag:
                                    top_id = 0
                                    filler_id = gravel_id
                                if sand_flag:
                                    top_id = sand_id
                                    filler_id = sand_id
                            if y < 64 and top_id == 0:
                                top_id = water_id
                            remaining = stone_depth
                            blocks[index] = top_id if y >= 63 else filler_id
                        elif remaining > 0:
                            remaining -= 1
                            blocks[index] = filler_id
    return state


@njit(cache=True, nogil=True, fastmath=False)
def simplex2d_sample(permutation, x_offset, y_offset, x, z, scale_x, scale_z, amplitude):
    SKEW = 0.5 * (1.7320508075688772 - 1.0)
    UNSKEW = (3.0 - 1.7320508075688772) / 6.0
    x = x * scale_x + x_offset
    z = z * scale_z + y_offset
    skew = (x + z) * SKEW
    _sx = x + skew
    i = int(_sx)
    if not (_sx > 0.0):
        i -= 1
    _sz = z + skew
    j = int(_sz)
    if not (_sz > 0.0):
        j -= 1
    unskew = (i + j) * UNSKEW
    x0 = x - (i - unskew)
    z0 = z - (j - unskew)
    if x0 > z0:
        i1 = 1
        j1 = 0
    else:
        i1 = 0
        j1 = 1
    x1 = x0 - i1 + UNSKEW
    z1 = z0 - j1 + UNSKEW
    x2 = x0 - 1.0 + 2.0 * UNSKEW
    z2 = z0 - 1.0 + 2.0 * UNSKEW
    ii = i & 255
    jj = j & 255
    g0 = permutation[ii + permutation[jj]] % 12
    g1 = permutation[ii + i1 + permutation[jj + j1]] % 12
    g2 = permutation[ii + 1 + permutation[jj + 1]] % 12
    total = 0.0
    for k in range(3):
        if k == 0:
            dx, dz, gi = x0, z0, g0
        elif k == 1:
            dx, dz, gi = x1, z1, g1
        else:
            dx, dz, gi = x2, z2, g2
        attenuation = 0.5 - dx * dx - dz * dz
        if attenuation >= 0.0:
            attenuation *= attenuation
            if gi == 0:
                gx, gz = 1.0, 1.0
            elif gi == 1:
                gx, gz = -1.0, 1.0
            elif gi == 2:
                gx, gz = 1.0, -1.0
            elif gi == 3:
                gx, gz = -1.0, -1.0
            elif gi == 4 or gi == 6:
                gx, gz = 1.0, 0.0
            elif gi == 5 or gi == 7:
                gx, gz = -1.0, 0.0
            elif gi == 8 or gi == 10:
                gx, gz = 0.0, 1.0
            else:
                gx, gz = 0.0, -1.0
            total += attenuation * attenuation * (gx * dx + gz * dz)
    return 70.0 * total * amplitude


@njit(cache=True, nogil=True, fastmath=False)
def octaves2d_sample(perm_table, x_offs, y_offs, octave_count,
                     x, z, scale_x, scale_z, persistence):
    scale_x = scale_x / 1.5
    scale_z = scale_z / 1.5
    frequency = 1.0
    amplitude_reciprocal = 1.0
    total = 0.0
    for o in range(octave_count):
        base = o * 512
        total += _simplex_with_base(
            perm_table, base, x_offs[o], y_offs[o],
            x, z, scale_x * frequency, scale_z * frequency,
            0.55 / amplitude_reciprocal,
        )
        frequency *= persistence
        amplitude_reciprocal *= 0.5
    return total


@njit(cache=True, nogil=True, fastmath=False)
def _simplex_with_base(perm_table, base, x_offset, y_offset, x, z, scale_x, scale_z, amplitude):
    SKEW = 0.5 * (1.7320508075688772 - 1.0)
    UNSKEW = (3.0 - 1.7320508075688772) / 6.0
    x = x * scale_x + x_offset
    z = z * scale_z + y_offset
    skew = (x + z) * SKEW
    _sx = x + skew
    i = int(_sx)
    if not (_sx > 0.0):
        i -= 1
    _sz = z + skew
    j = int(_sz)
    if not (_sz > 0.0):
        j -= 1
    unskew = (i + j) * UNSKEW
    x0 = x - (i - unskew)
    z0 = z - (j - unskew)
    if x0 > z0:
        i1 = 1
        j1 = 0
    else:
        i1 = 0
        j1 = 1
    x1 = x0 - i1 + UNSKEW
    z1 = z0 - j1 + UNSKEW
    x2 = x0 - 1.0 + 2.0 * UNSKEW
    z2 = z0 - 1.0 + 2.0 * UNSKEW
    ii = i & 255
    jj = j & 255
    g0 = perm_table[base + ii + perm_table[base + jj]] % 12
    g1 = perm_table[base + ii + i1 + perm_table[base + jj + j1]] % 12
    g2 = perm_table[base + ii + 1 + perm_table[base + jj + 1]] % 12
    total = 0.0
    for k in range(3):
        if k == 0:
            dx, dz, gi = x0, z0, g0
        elif k == 1:
            dx, dz, gi = x1, z1, g1
        else:
            dx, dz, gi = x2, z2, g2
        attenuation = 0.5 - dx * dx - dz * dz
        if attenuation >= 0.0:
            attenuation *= attenuation
            if gi == 0:
                gx, gz = 1.0, 1.0
            elif gi == 1:
                gx, gz = -1.0, 1.0
            elif gi == 2:
                gx, gz = 1.0, -1.0
            elif gi == 3:
                gx, gz = -1.0, -1.0
            elif gi == 4 or gi == 6:
                gx, gz = 1.0, 0.0
            elif gi == 5 or gi == 7:
                gx, gz = -1.0, 0.0
            elif gi == 8 or gi == 10:
                gx, gz = 0.0, 1.0
            else:
                gx, gz = 0.0, -1.0
            total += attenuation * attenuation * (gx * dx + gz * dz)
    return 70.0 * total * amplitude


@njit(cache=True, nogil=True, fastmath=False)
def fill_climate_arrays(temp_perm, temp_xoff, temp_yoff, temp_octaves,
                        humid_perm, humid_xoff, humid_yoff, humid_octaves,
                        detail_perm, detail_xoff, detail_yoff, detail_octaves,
                        x0, z0, size_x, size_z,
                        temps, humids,
                        temperature_scale, humidity_scale):
    for lz in range(size_z):
        wz = z0 + lz
        row = lz * size_x
        for lx in range(size_x):
            wx = x0 + lx
            temperature_noise = octaves2d_sample(
                temp_perm, temp_xoff, temp_yoff, temp_octaves,
                wx, wz, temperature_scale, temperature_scale, 0.25,
            )
            humidity_noise = octaves2d_sample(
                humid_perm, humid_xoff, humid_yoff, humid_octaves,
                wx, wz, humidity_scale, humidity_scale, 1.0 / 3.0,
            )
            detail = octaves2d_sample(
                detail_perm, detail_xoff, detail_yoff, detail_octaves,
                wx, wz, 0.25, 0.25, 0.5882352941176471,
            )
            detail = detail * 1.1 + 0.5
            t = (temperature_noise * 0.15 + 0.7) * 0.99 + detail * 0.01
            h = (humidity_noise * 0.15 + 0.5) * 0.998 + detail * 0.002
            t = 1.0 - (1.0 - t) * (1.0 - t)
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            if h < 0.0:
                h = 0.0
            elif h > 1.0:
                h = 1.0
            temps[row + lx] = t
            humids[row + lx] = h
