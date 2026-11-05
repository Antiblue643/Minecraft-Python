from __future__ import annotations

import math
import json
import struct
import time
from collections import OrderedDict
from pathlib import Path

from game.world.java_random import JavaRandom
from game.world.biomes import BiomeProvider, biome_from_climate
from game.content_ids import legacy_entity_id
from game.world.terrain_numba import (
    NUMBA_AVAILABLE, np as _np,
    perlin_add_to_array as _numba_perlin_add_to_array,
    fill_all_terrain as _numba_fill_all_terrain,
    replace_surface_blocks as _numba_replace_surface_blocks,
    carve_cave_region as _numba_carve_cave_region,
    surface_heights as _numba_surface_heights,
)

CHUNK_SIZE = 16
WORLD_HEIGHT = 128
SEA_LEVEL = 64

_BLOCK_LIGHT_OPACITY = None
_DUNGEON_LOOT_TABLE = None
_LAKE_NONBLOCKING_LEGACY_BLOCKS = frozenset({"fire", "torch", "ladder", "rail"})


def _load_dungeon_loot_table():
    global _DUNGEON_LOOT_TABLE
    if _DUNGEON_LOOT_TABLE is not None:
        return _DUNGEON_LOOT_TABLE
    try:
        path = Path(__file__).resolve().parents[1] / "data" / "loot.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        _DUNGEON_LOOT_TABLE = data.get("dungeon_loot", {})
    except (OSError, ValueError, KeyError, TypeError):
        _DUNGEON_LOOT_TABLE = {
            "roll_bound": 11,
            "entries": [
                {"roll": 0, "item": "saddle", "count": 1},
                {"roll": 1, "item": "iron_ingot", "count_min": 1, "count_max": 4},
                {"roll": 2, "item": "bread", "count": 1},
                {"roll": 3, "item": "wheat", "count_min": 1, "count_max": 4},
                {"roll": 4, "item": "gunpowder", "count_min": 1, "count_max": 4},
                {"roll": 5, "item": "string", "count_min": 1, "count_max": 4},
                {"roll": 6, "item": "bucket", "count": 1},
                {"roll": 7, "item": "redstone", "count_min": 1, "count_max": 4},
                {"roll": 8, "item": "golden_apple", "count": 1, "secondary_bound": 100, "secondary_roll": 0},
            ],
        }
    return _DUNGEON_LOOT_TABLE


def _light_opacity_for_population(block_type: str | None) -> int:
    global _BLOCK_LIGHT_OPACITY
    if block_type is None:
        return 0
    if _BLOCK_LIGHT_OPACITY is None:
        try:
            path = Path(__file__).resolve().parents[1] / "data" / "blocks.json"
            definitions = json.loads(path.read_text(encoding="utf-8"))["blocks"]
            _BLOCK_LIGHT_OPACITY = {
                name: max(0, min(255, int(definition.get("lightOpacity", 255))))
                for name, definition in definitions.items()
            }
        except (OSError, ValueError, KeyError, TypeError):
            _BLOCK_LIGHT_OPACITY = {}
    return int(_BLOCK_LIGHT_OPACITY.get(block_type, 255))


def _f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def _java_div2(value: int) -> int:
    return value // 2 if value >= 0 else -((-value) // 2)


def _signed64(value: int) -> int:
    value &= (1 << 64) - 1
    return value if value < (1 << 63) else value - (1 << 64)


_INT_MAX = 2147483647
_INT_MIN = -2147483648


def _java_int(value: float) -> int:
    if value != value:
        return 0
    if value >= _INT_MAX:
        return _INT_MAX
    if value <= _INT_MIN:
        return _INT_MIN
    return int(value)


def _java_noise_floor(value: float) -> int:
    result = _java_int(value)
    if value < result:
        result -= 1
        if result < _INT_MIN:
            result = _INT_MAX
    return result


def _lerp(t: float, a: float, b: float) -> float:
    return a + t * (b - a)


def _fade(t: float) -> float:
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _grad(hash_value: int, x: float, y: float, z: float) -> float:
    h = hash_value & 15
    u = x if h < 8 else y
    v = y if h < 4 else (z if h not in (12, 14) else x)
    return (u if (h & 1) == 0 else -u) + (v if (h & 2) == 0 else -v)


class _Perlin:
    def __init__(self, rng: JavaRandom, use_numba: bool = False):
        self.use_numba = bool(use_numba) and bool(NUMBA_AVAILABLE)
        self.x_offset = rng.next_double() * 256.0
        self.y_offset = rng.next_double() * 256.0
        self.z_offset = rng.next_double() * 256.0
        permutations = list(range(256)) + [0] * 256
        for i in range(256):
            j = rng.next_int(256 - i) + i
            permutations[i], permutations[j] = permutations[j], permutations[i]
            permutations[i + 256] = permutations[i]
        self.permutations = (_np.asarray(permutations, dtype=_np.int64)
                             if self.use_numba else permutations)

    def sample_coords(self, x: float, y: float, z: float) -> float:
        xi0, yi0, zi0 = _java_noise_floor(x), _java_noise_floor(y), _java_noise_floor(z)

        xi, yi, zi = xi0 & 255, yi0 & 255, zi0 & 255
        xf, yf, zf = x - xi0, y - yi0, z - zi0
        u, v, w = _fade(xf), _fade(yf), _fade(zf)
        p = self.permutations
        a = p[xi] + yi
        aa, ab = p[a] + zi, p[a + 1] + zi
        b = p[xi + 1] + yi
        ba, bb = p[b] + zi, p[b + 1] + zi

        x00 = _lerp(u, _grad(p[aa], xf, yf, zf),
                    _grad(p[ba], xf - 1.0, yf, zf))
        x10 = _lerp(u, _grad(p[ab], xf, yf - 1.0, zf),
                    _grad(p[bb], xf - 1.0, yf - 1.0, zf))
        x01 = _lerp(u, _grad(p[aa + 1], xf, yf, zf - 1.0),
                    _grad(p[ba + 1], xf - 1.0, yf, zf - 1.0))
        x11 = _lerp(u, _grad(p[ab + 1], xf, yf - 1.0, zf - 1.0),
                    _grad(p[bb + 1], xf - 1.0, yf - 1.0, zf - 1.0))
        return _lerp(w, _lerp(v, x00, x10), _lerp(v, x01, x11))

    def add_to_array(self, output: list[float], x: float, y: float, z: float,
                     size_x: int, size_y: int, size_z: int,
                     scale_x: float, scale_y: float, scale_z: float,
                     inverse_frequency: float) -> None:
        if size_y == 1 and not self.use_numba:
            p = self.permutations
            index = 0
            for ix in range(size_x):
                nx = (x + float(ix)) * scale_x + self.x_offset
                ix_floor = _java_noise_floor(nx)
                xi = ix_floor & 255
                xf = nx - ix_floor
                fade_x = _fade(xf)
                for iz in range(size_z):
                    nz = (z + float(iz)) * scale_z + self.z_offset
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
        if self.use_numba:
            _numba_perlin_add_to_array(
                self.permutations, output,
                float(x), float(y), float(z), size_x, size_y, size_z,
                float(scale_x), float(scale_y), float(scale_z),
                float(inverse_frequency), self.x_offset, self.y_offset, self.z_offset,
            )
            return
        index = 0
        last_y = -1
        g00 = g01 = g10 = g11 = 0.0
        for ix in range(size_x):
            nx = (x + float(ix)) * scale_x + self.x_offset
            ix_floor = _java_noise_floor(nx)
            xi = ix_floor & 255
            xf = nx - ix_floor
            fade_x = _fade(xf)
            for iz in range(size_z):
                nz = (z + float(iz)) * scale_z + self.z_offset
                iz_floor = _java_noise_floor(nz)
                zi = iz_floor & 255
                zf = nz - iz_floor
                fade_z = _fade(zf)
                for iy in range(size_y):
                    ny = (y + float(iy)) * scale_y + self.y_offset
                    iy_floor = _java_noise_floor(ny)
                    yi = iy_floor & 255
                    yf = ny - iy_floor
                    fade_y = _fade(yf)
                    if iy == 0 or yi != last_y:
                        last_y = yi
                        p = self.permutations
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


class _Octaves:
    def __init__(self, rng: JavaRandom, octave_count: int, use_numba: bool = False):
        self.use_numba = bool(use_numba) and bool(NUMBA_AVAILABLE)
        self.generators = [_Perlin(rng, use_numba=self.use_numba) for _ in range(octave_count)]

    def generate_2d(self, x: float, z: float) -> float:
        total = 0.0
        frequency = 1.0
        for generator in self.generators:
            total += generator.sample_coords(
                x * frequency + generator.x_offset,
                z * frequency + generator.y_offset,
                generator.z_offset,
            ) / frequency
            frequency /= 2.0
        return total

    def generate(self, x: float, y: float, z: float,
                 size_x: int, size_y: int, size_z: int,
                 scale_x: float, scale_y: float, scale_z: float) -> list[float]:

        output = (_np.zeros(size_x * size_y * size_z, dtype=_np.float64)
                  if self.use_numba else [0.0] * (size_x * size_y * size_z))
        frequency = 1.0
        for generator in self.generators:
            generator.add_to_array(
                output, x, y, z, size_x, size_y, size_z,
                scale_x * frequency, scale_y * frequency, scale_z * frequency,
                1.0 / frequency,
            )
            frequency /= 2.0
        return output


_SIN_TABLE = tuple(_f32(math.sin(i * math.pi * 2.0 / 65536.0)) for i in range(65536))
_F_PI = _f32(math.pi)
_F_TWO = _f32(2.0)
_F_HALF = _f32(0.5)
_F_QUARTER = _f32(0.25)
_F_EIGHTH = _f32(0.125)
_F_SIN_SCALE = _f32(10430.378)
_F_16384 = _f32(16384.0)


def _sin_f(angle: float) -> float:
    index = int(_f32(_f32(angle) * _F_SIN_SCALE)) & 65535
    return _SIN_TABLE[index]


def _cos_f(angle: float) -> float:
    index = int(_f32(_f32(angle) * _F_SIN_SCALE + _F_16384)) & 65535
    return _SIN_TABLE[index]


def _floor_double(value: float) -> int:
    integer = int(value)
    return integer - 1 if value < integer else integer


class AlphaTerrainGenerator:

    _WRAPPER_TYPES = (
        "bedrock", "grass_block", "sand", "gravel", "dirt", "stone", "water",
        "coal_ore", "iron_ore", "gold_ore", "diamond_ore", "lava",
    )

    def __init__(self, seed: int, sea_level: int = SEA_LEVEL,
                 world_height: int = WORLD_HEIGHT, snow_covered: bool = False,
                 use_numba: bool | None = None):
        if int(world_height) != 128:
            raise ValueError("Alpha Java reference chunks are exactly 128 blocks high")
        if int(sea_level) != 64:
            raise ValueError("Alpha Java reference sea level is 64")
        self.seed = int(seed)
        self.sea_level = int(sea_level)
        self.world_height = int(world_height)
        self.snow_covered = bool(snow_covered)
        if use_numba is None:
            self.use_numba = bool(NUMBA_AVAILABLE)
        else:
            self.use_numba = bool(use_numba) and bool(NUMBA_AVAILABLE)
        self.biomes = BiomeProvider(self.seed, use_numba=self.use_numba)

        rng = JavaRandom(self.seed)
        _nu = self.use_numba
        self.noise_gen1 = _Octaves(rng, 16, use_numba=_nu)
        self.noise_gen2 = _Octaves(rng, 16, use_numba=_nu)
        self.noise_gen3 = _Octaves(rng, 8, use_numba=_nu)
        self.noise_gen4 = _Octaves(rng, 4, use_numba=_nu)
        self.noise_gen5 = _Octaves(rng, 4, use_numba=_nu)
        self.noise_gen6 = _Octaves(rng, 10, use_numba=_nu)
        self.noise_gen7 = _Octaves(rng, 16, use_numba=_nu)
        self.mob_spawner_noise = _Octaves(rng, 8, use_numba=_nu)
        self.rand = rng

        self._actual_surface = OrderedDict()
        self._actual_surface_limit = 131072
        self._estimated_surface = OrderedDict()
        self._estimated_surface_limit = 65536
        self._chunk_surface_ranges = OrderedDict()
        self._chunk_surface_ranges_limit = 4096
        self._population_terrain_cache = OrderedDict()
        self._population_terrain_cache_limit = 32

    def _initialize_noise_field(self, origin_x: int, origin_z: int) -> list[float]:
        size_x, size_y, size_z = 5, 17, 5
        noise6 = self.noise_gen6.generate(
            origin_x, 10.0, origin_z, 5, 1, 5, 1.121, 1.0, 1.121,
        )
        noise7 = self.noise_gen7.generate(
            origin_x, 10.0, origin_z, 5, 1, 5, 200.0, 1.0, 200.0,
        )
        noise3 = self.noise_gen3.generate(
            origin_x, 0.0, origin_z, 5, 17, 5,
            684.412 / 80.0, 684.412 / 160.0, 684.412 / 80.0,
        )
        noise1 = self.noise_gen1.generate(
            origin_x, 0.0, origin_z, 5, 17, 5, 684.412, 684.412, 684.412,
        )
        noise2 = self.noise_gen2.generate(
            origin_x, 0.0, origin_z, 5, 17, 5, 684.412, 684.412, 684.412,
        )
        output = (_np.zeros(size_x * size_y * size_z, dtype=_np.float64)
                  if self.use_numba else [0.0] * (size_x * size_y * size_z))
        node_index = 0
        density_index = 0
        climate_stride = 16 // size_x
        climate_offset = climate_stride // 2
        world_x0 = origin_x * 4
        world_z0 = origin_z * 4
        temps, humids = self.biomes.fill_climate_region(world_x0, world_z0, 16, 16)
        for ix in range(size_x):
            for iz in range(size_z):
                lx = ix * climate_stride + climate_offset
                lz = iz * climate_stride + climate_offset
                temperature = temps[lz * 16 + lx]
                humidity = humids[lz * 16 + lx]
                moisture = humidity * temperature
                climate_factor = 1.0 - moisture
                climate_factor *= climate_factor
                climate_factor *= climate_factor
                climate_factor = 1.0 - climate_factor

                var16 = (noise6[node_index] + 256.0) / 512.0
                var16 *= climate_factor
                if var16 > 1.0:
                    var16 = 1.0
                var20 = noise7[node_index] / 8000.0
                if var20 < 0.0:
                    var20 = -var20 * 0.3
                var20 = var20 * 3.0 - 2.0
                if var20 < 0.0:
                    var20 /= 2.0
                    if var20 < -1.0:
                        var20 = -1.0
                    var20 /= 1.4
                    var20 /= 2.0
                    var16 = 0.0
                else:
                    if var20 > 1.0:
                        var20 = 1.0
                    var20 /= 8.0
                if var16 < 0.0:
                    var16 = 0.0
                var16 += 0.5
                var20 = var20 * size_y / 16.0
                var22 = size_y / 2.0 + var20 * 4.0
                node_index += 1
                for iy in range(size_y):
                    var27 = (float(iy) - var22) * 12.0 / var16
                    if var27 < 0.0:
                        var27 *= 4.0
                    var29 = noise1[density_index] / 512.0
                    var31 = noise2[density_index] / 512.0
                    var33 = (noise3[density_index] / 10.0 + 1.0) / 2.0
                    if var33 < 0.0:
                        density = var29
                    elif var33 > 1.0:
                        density = var31
                    else:
                        density = var29 + (var31 - var29) * var33
                    density -= var27
                    if iy > size_y - 4:
                        fade = float(_f32(_f32(iy - (size_y - 4)) / _f32(3.0)))
                        density = density * (1.0 - fade) + -10.0 * fade
                    output[density_index] = density
                    density_index += 1
        return output

    def _replace_blocks(self, cx: int, cz: int, data: bytearray,
                        ids: dict[str, int], width: int = 16, depth: int = 16) -> None:
        sand_noise = self.noise_gen4.generate(
            cx * 16, cz * 16, 0.0, 16, 16, 1,
            1.0 / 32.0, 1.0 / 32.0, 1.0,
        )
        gravel_noise = self.noise_gen4.generate(
            cz * 16, 109.0134, cx * 16, 16, 1, 16,
            1.0 / 32.0, 1.0, 1.0 / 32.0,
        )
        stone_noise = self.noise_gen5.generate(
            cx * 16, cz * 16, 0.0, 16, 16, 1,
            1.0 / 16.0, 1.0 / 16.0, 1.0 / 16.0,
        )
        bedrock_id = ids["bedrock"]
        grass_id, dirt_id = ids["grass_block"], ids["dirt"]
        sand_id, gravel_id = ids["sand"], ids["gravel"]
        stone_id, water_id = ids["stone"], ids["water"]
        biome_top = [grass_id] * 256
        biome_filler = [dirt_id] * 256
        temps, humids = self.biomes.fill_climate_region(cx * 16, cz * 16, 16, 16)
        for x in range(16):
            for z in range(16):
                climate_index = z * 16 + x
                biome = biome_from_climate(temps[climate_index], humids[climate_index])
                column_index = x * 16 + z
                biome_top[column_index] = int(ids.get(biome.top_block, grass_id))
                biome_filler[column_index] = int(ids.get(biome.filler_block, dirt_id))

        if self.use_numba:
            chunk_seed = _signed64(cx * 341873128712 + cz * 132897987541)
            _numba_replace_surface_blocks(
                _np.frombuffer(data, dtype=_np.uint8),
                _np.asarray(sand_noise, dtype=_np.float64),
                _np.asarray(gravel_noise, dtype=_np.float64),
                _np.asarray(stone_noise, dtype=_np.float64),
                _np.asarray(biome_top, dtype=_np.int64),
                _np.asarray(biome_filler, dtype=_np.int64),
                bedrock_id, sand_id, gravel_id, stone_id, water_id,
                chunk_seed,
            )
            return

        for x in range(16):
            for z in range(16):
                noise_index = x + z * 16
                column_index = x * 16 + z
                sand_flag = sand_noise[noise_index] + self.rand.next_double() * 0.2 > 0.0
                gravel_flag = gravel_noise[noise_index] + self.rand.next_double() * 0.2 > 3.0
                stone_depth = int(
                    stone_noise[noise_index] / 3.0 + 3.0 + self.rand.next_double() * 0.25
                )
                remaining = -1
                top_id = biome_top[column_index]
                filler_id = biome_filler[column_index]
                for y in range(127, -1, -1):
                    index = (x * 16 + z) * 128 + y
                    if y <= self.rand.next_int(5):
                        data[index] = bedrock_id
                    else:
                        block_id = data[index]
                        if block_id == 0:
                            remaining = -1
                        elif block_id == stone_id:
                            if remaining == -1:
                                if stone_depth <= 0:
                                    top_id, filler_id = 0, stone_id
                                elif 60 <= y <= 65:
                                    top_id = biome_top[column_index]
                                    filler_id = biome_filler[column_index]
                                    if gravel_flag:
                                        top_id, filler_id = 0, gravel_id
                                    if sand_flag:
                                        top_id, filler_id = sand_id, sand_id
                                if y < 64 and top_id == 0:
                                    top_id = water_id
                                remaining = stone_depth
                                data[index] = top_id if y >= 63 else filler_id
                            elif remaining > 0:
                                remaining -= 1
                                data[index] = filler_id

    def _generate_caves_numba(self, cx: int, cz: int, data: bytearray,
                              ids: dict[str, int]) -> None:
        from game.world.cave_numba import SIN_TABLE, generate_caves
        self.rand.set_seed(self.seed)
        final_state = generate_caves(
            _np.frombuffer(data, dtype=_np.uint8), int(cx), int(cz),
            self.seed & ((1 << 48) - 1), SIN_TABLE,
            int(ids["water"]), int(ids["stone"]), int(ids["dirt"]),
            int(ids["grass_block"]), int(ids.get("lava", 0) or 0),
        )
        self.rand.seed = int(final_state)

    def _generate_caves(self, cx: int, cz: int, data: bytearray,
                        ids: dict[str, int]) -> None:
        if self.use_numba and getattr(self, "numba_caves", True):
            self._generate_caves_numba(cx, cz, data, ids)
            return
        self.rand.set_seed(self.seed)
        odd_x = _signed64(_java_div2(self.rand.next_long()) * 2 + 1)
        odd_z = _signed64(_java_div2(self.rand.next_long()) * 2 + 1)
        if self.use_numba:
            self._cave_block_view = _np.frombuffer(data, dtype=_np.uint8)
        else:
            self._cave_block_view = None
        self._cave_ids = (
            int(ids["water"]), int(ids["stone"]), int(ids["dirt"]),
            int(ids["grass_block"]), int(ids.get("lava", 0) or 0),
        )
        for source_x in range(cx - 8, cx + 9):
            for source_z in range(cz - 8, cz + 9):
                source_seed = _signed64(source_x * odd_x + source_z * odd_z) ^ self.seed
                self.rand.set_seed(source_seed)
                cave_count = self.rand.next_int(
                    self.rand.next_int(self.rand.next_int(40) + 1) + 1
                )
                if self.rand.next_int(15) != 0:
                    cave_count = 0
                for _ in range(cave_count):
                    x = source_x * 16 + self.rand.next_int(16)
                    y = self.rand.next_int(self.rand.next_int(120) + 8)
                    z = source_z * 16 + self.rand.next_int(16)
                    branches = 1
                    if self.rand.next_int(4) == 0:
                        self._generate_large_cave_node(cx, cz, data, ids, x, y, z)
                        branches += self.rand.next_int(4)
                    for _branch in range(branches):
                        yaw = _f32(_f32(self.rand.next_float() * _F_PI) * _F_TWO)
                        pitch = _f32(_f32(self.rand.next_float() - _F_HALF) * _F_TWO)
                        pitch = _f32(pitch / _f32(8.0))
                        radius = _f32(_f32(self.rand.next_float() * _F_TWO) + self.rand.next_float())
                        self._generate_cave_node(
                            cx, cz, data, ids, x, y, z, radius, yaw, pitch,
                            0, 0, 1.0,
                        )

    def _generate_large_cave_node(self, cx: int, cz: int, data: bytearray,
                                  ids: dict[str, int], x: float, y: float, z: float) -> None:
        radius = _f32(1.0 + _f32(self.rand.next_float() * _f32(6.0)))
        self._generate_cave_node(cx, cz, data, ids, x, y, z,
                                 radius, 0.0, 0.0, -1, -1, 0.5)

    def _generate_cave_node(self, cx: int, cz: int, data: bytearray,
                            ids: dict[str, int], x: float, y: float, z: float,
                            width: float, yaw: float, pitch: float,
                            start_step: int, end_step: int, vertical_scale: float) -> None:
        center_x, center_z = float(cx * 16 + 8), float(cz * 16 + 8)
        yaw_delta = _f32(0.0)
        pitch_delta = _f32(0.0)
        rng = JavaRandom(self.rand.next_long())
        width = _f32(width)
        yaw, pitch = _f32(yaw), _f32(pitch)
        if end_step <= 0:
            end_step = 112 - rng.next_int(28)
        is_large = False
        if start_step == -1:
            start_step = end_step // 2
            is_large = True
        split_step = rng.next_int(end_step // 2) + end_step // 4
        gentle = rng.next_int(6) == 0
        step = start_step
        while step < end_step:
            angle = _f32(_f32(_f32(float(step) * _F_PI)) / _f32(float(end_step)))
            radius = 1.5 + float(_f32(_f32(_sin_f(angle) * width) * _f32(1.0)))
            vertical_radius = radius * vertical_scale
            cos_pitch = _cos_f(pitch)
            sin_pitch = _sin_f(pitch)
            x += float(_f32(_cos_f(yaw) * cos_pitch))
            y += float(sin_pitch)
            z += float(_f32(_sin_f(yaw) * cos_pitch))
            pitch = _f32(pitch * (_f32(0.92) if gentle else _f32(0.7)))
            pitch = _f32(pitch + _f32(pitch_delta * _f32(0.1)))
            yaw = _f32(yaw + _f32(yaw_delta * _f32(0.1)))
            pitch_delta = _f32(pitch_delta * _f32(0.9))
            yaw_delta = _f32(yaw_delta * _f32(12.0 / 16.0))
            pitch_delta = _f32(pitch_delta + _f32(
                _f32(_f32(rng.next_float() - rng.next_float()) * rng.next_float()) * _f32(2.0)
            ))
            yaw_delta = _f32(yaw_delta + _f32(
                _f32(_f32(rng.next_float() - rng.next_float()) * rng.next_float()) * _f32(4.0)
            ))
            if not is_large and step == split_step and width > 1.0:
                child_width_1 = _f32(_f32(rng.next_float() * _F_HALF) + _F_HALF)
                child_yaw_1 = _f32(yaw - _f32(_F_PI * _F_HALF))
                child_pitch = _f32(pitch / _f32(3.0))
                self._generate_cave_node(cx, cz, data, ids, x, y, z,
                                         child_width_1, child_yaw_1, child_pitch,
                                         step, end_step, 1.0)
                child_width_2 = _f32(_f32(rng.next_float() * _F_HALF) + _F_HALF)
                child_yaw_2 = _f32(yaw + _f32(_F_PI * _F_HALF))
                self._generate_cave_node(cx, cz, data, ids, x, y, z,
                                         child_width_2, child_yaw_2, child_pitch,
                                         step, end_step, 1.0)
                return
            if is_large or rng.next_int(4) != 0:
                dx, dz = x - center_x, z - center_z
                remaining = float(end_step - step)
                width_pad = float(width + 2.0 + 16.0)
                if dx * dx + dz * dz - remaining * remaining > width_pad * width_pad:
                    return
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
                if self.use_numba:
                    block_view = getattr(self, "_cave_block_view", None)
                    if block_view is None:
                        block_view = _np.frombuffer(data, dtype=_np.uint8)
                    wid, sid, did, gid, lid = self._cave_ids
                    touches_water = _numba_carve_cave_region(
                        block_view,
                        cx, cz, x, y, z, radius, vertical_radius,
                        x0, x1, y0, y1, z0, z1,
                        wid, sid, did, gid, lid,
                    )
                    if not touches_water and is_large:
                        break
                else:
                    touches_water = False
                    ix = x0
                    while not touches_water and ix < x1:
                        iz = z0
                        while not touches_water and iz < z1:
                            iy = y1 + 1
                            while not touches_water and iy >= y0 - 1:
                                if 0 <= iy < 128:
                                    block_id = data[(ix * 16 + iz) * 128 + iy]
                                    if block_id == ids["water"]:
                                        touches_water = True
                                    if (iy != y0 - 1 and ix != x0 and ix != x1 - 1
                                            and iz != z0 and iz != z1 - 1):
                                        iy = y0
                                iy -= 1
                            iz += 1
                        ix += 1
                    if not touches_water:
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
                                        block_id = data[index]
                                        if block_id == ids["grass_block"]:
                                            had_grass = True
                                        if block_id in (ids["stone"], ids["dirt"], ids["grass_block"]):
                                            if carve_y < 10:
                                                data[index] = ids.get("lava", 0)
                                            else:
                                                data[index] = 0
                                                if had_grass and data[index - 1] == ids["dirt"]:
                                                    data[index - 1] = ids["grass_block"]
                                    index -= 1
                        if is_large:
                            break
            step += 1

    def _remember_surface(self, cx: int, cz: int, data: bytearray,
                          width: int, depth: int, ids: dict[str, int]) -> None:
        water_id, lava_id = ids["water"], ids.get("lava", -1)
        if self.use_numba:
            heights = _numba_surface_heights(
                _np.frombuffer(data, dtype=_np.uint8), width, depth, 128,
                water_id, lava_id,
            ).tolist()
        else:
            heights = []
            for x in range(width):
                for z in range(depth):
                    base = (x * depth + z) * 128
                    top = -1
                    for y in range(127, -1, -1):
                        block = data[base + y]
                        if block and block != water_id and block != lava_id:
                            top = y
                            break
                    heights.append(top)
        for x in range(width):
            for z in range(depth):
                top = int(heights[x * depth + z])
                self._actual_surface[(cx * width + x, cz * depth + z)] = top
        while len(self._actual_surface) > self._actual_surface_limit:
            self._actual_surface.popitem(last=False)
        valid = [h for h in heights if h >= 0]
        self._chunk_surface_ranges[(cx, cz)] = (
            min(valid) if valid else 0, max(valid) if valid else 0
        )
        self._chunk_surface_ranges.move_to_end((cx, cz))
        while len(self._chunk_surface_ranges) > self._chunk_surface_ranges_limit:
            self._chunk_surface_ranges.popitem(last=False)

    def _estimate_chunk_heights(self, cx: int, cz: int) -> None:
        field = self._initialize_noise_field(cx * 4, cz * 4)
        heights = [-1] * 256
        for gx in range(4):
            for gz in range(4):
                base = ((gx * 5 + gz) * 17)
                base_z = ((gx * 5 + gz + 1) * 17)
                nx = (((gx + 1) * 5 + gz) * 17)
                nxz = (((gx + 1) * 5 + gz + 1) * 17)
                for gy in range(16):
                    n00, n01, n10, n11 = field[base + gy], field[base_z + gy], field[nx + gy], field[nxz + gy]
                    dy00 = (field[base + gy + 1] - n00) * 0.125
                    dy01 = (field[base_z + gy + 1] - n01) * 0.125
                    dy10 = (field[nx + gy + 1] - n10) * 0.125
                    dy11 = (field[nxz + gy + 1] - n11) * 0.125
                    for sy in range(8):
                        cur00, cur01 = n00, n01
                        sx00, sx01 = (n10 - n00) * 0.25, (n11 - n01) * 0.25
                        y = gy * 8 + sy
                        for sx in range(4):
                            density = cur00
                            inc_z = (cur01 - cur00) * 0.25
                            x, z0 = gx * 4 + sx, gz * 4
                            for sz in range(4):
                                if density > 0.0:
                                    col = x * 16 + z0 + sz
                                    if y > heights[col]:
                                        heights[col] = y
                                density += inc_z
                            cur00 += sx00
                            cur01 += sx01
                        n00 += dy00
                        n01 += dy01
                        n10 += dy10
                        n11 += dy11
        for x in range(16):
            for z in range(16):
                self._estimated_surface[(cx * 16 + x, cz * 16 + z)] = heights[x * 16 + z]
        while len(self._estimated_surface) > self._estimated_surface_limit:
            self._estimated_surface.popitem(last=False)

    def height_at(self, x: int, z: int) -> int:
        key = (int(x), int(z))
        if key in self._actual_surface:
            self._actual_surface.move_to_end(key)
            return self._actual_surface[key]
        if key in self._estimated_surface:
            self._estimated_surface.move_to_end(key)
            return self._estimated_surface[key]
        cx, cz = math.floor(x / 16), math.floor(z / 16)
        self._estimate_chunk_heights(cx, cz)
        return self._estimated_surface.get(key, 63)

    def chunk_surface_range(self, cx: int, cz: int) -> tuple[int, int]:
        return self._chunk_surface_ranges.get((int(cx), int(cz)), (0, 127))

    def generate_chunk_data(self, cx: int, cz: int, width: int, depth: int,
                            block_type_to_id: dict[str, int]) -> bytearray:
        if width != 16 or depth != 16:
            raise ValueError("Alpha terrain generation requires 16x16 horizontal chunks")
        needed = ("bedrock", "grass_block", "sand", "gravel", "dirt", "stone", "water")
        ids = {name: int(block_type_to_id[name]) for name in needed}
        ids["lava"] = int(block_type_to_id.get("lava", 0))
        ids["ice"] = int(block_type_to_id.get("ice", 0) or 0)
        data = bytearray(16 * 16 * 128)
        chunk_seed = _signed64(cx * 341873128712 + cz * 132897987541)
        self.rand.set_seed(chunk_seed)
        field = self._initialize_noise_field(cx * 4, cz * 4)
        self._fill_all_terrain(data, field, ids)
        if self.snow_covered:
            ice_id = int(block_type_to_id.get("ice", 0) or 0)
            if ice_id:
                self._freeze_top_water(data, ids["water"], ice_id)
        self._replace_blocks(cx, cz, data, ids, width, depth)
        self._apply_biome_surface_blocks(cx, cz, data, ids)
        self._generate_caves(cx, cz, data, ids)
        self._remember_surface(cx, cz, data, width, depth, ids)
        return data

    @staticmethod
    def _freeze_top_water(data: bytearray, water_id: int, ice_id: int) -> None:
        for column in range(256):
            index = column * 128 + 63
            if data[index] == water_id:
                data[index] = ice_id

    def _apply_biome_surface_blocks(self, cx: int, cz: int,
                                    data: bytearray, ids: dict[str, int]) -> None:
        water_id = ids["water"]
        ice_id = ids.get("ice", 0)
        if not ice_id:
            return
        temps, _humids = self.biomes.fill_climate_region(cx * 16, cz * 16, 16, 16)
        for lx in range(16):
            for lz in range(16):
                temperature = temps[lz * 16 + lx]
                index = (lx * 16 + lz) * 128 + 63
                if temperature < 0.5 and data[index] == water_id:
                    data[index] = ice_id

    def _fill_all_terrain(self, data: bytearray, field: list[float], ids: dict[str, int]) -> None:
        stone_id, water_id = ids["stone"], ids["water"]
        if self.use_numba:
            block_view = _np.frombuffer(data, dtype=_np.uint8)
            field_view = _np.asarray(field, dtype=_np.float64)
            _numba_fill_all_terrain(block_view, field_view, stone_id, water_id)
            return
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
                                data[(x * 16 + z) * 128 + y] = block
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

    def _population_get(self, world, x: int, y: int, z: int):
        if y < 0 or y >= 128:
            return None
        cx, cz = math.floor(x / 16), math.floor(z / 16)
        chunk = world.chunks.get((cx, cz))
        if chunk is not None:
            return chunk.get_block_type(x, y, z)
        saved = world._worldgen_block_overrides.get((cx, cz), {})
        pos = (x, y, z)
        if pos in saved:
            return saved[pos]
        key = (cx, cz)
        cached = self._population_terrain_cache.get(key)
        if cached is None:
            future = getattr(world, "_terrain_futures", {}).get(key)
            if future is not None and future.done():
                try:
                    cached = (future.result(), world.id_to_block_type)
                except Exception:
                    cached = None

            if cached is None:
                rng_state = self.rand.seed
                started = time.perf_counter()
                try:
                    data = self.generate_chunk_data(cx, cz, 16, 16, world.block_type_to_id)
                finally:
                    self.rand.seed = rng_state
                    profiler = getattr(world, "profiler", None)
                    if profiler is not None:
                        profiler.record_since("population.terrain_fallback", started)
                cached = (data, world.id_to_block_type)

            self._population_terrain_cache[key] = cached
            self._population_terrain_cache.move_to_end(key)
            while len(self._population_terrain_cache) > self._population_terrain_cache_limit:
                self._population_terrain_cache.popitem(last=False)
        else:
            self._population_terrain_cache.move_to_end(key)
        data, reverse_palette = cached
        block_id = data[((x - cx * 16) * 16 + (z - cz * 16)) * 128 + y]
        return reverse_palette[block_id] if block_id < len(reverse_palette) else None

    def _population_set(
        self,
        world,
        x: int,
        y: int,
        z: int,
        block_type: str | None,
        meta: int = 0,
    ) -> None:
        if y < 0 or y >= 128:
            return
        cx, cz = math.floor(x / 16), math.floor(z / 16)
        world._worldgen_block_overrides.setdefault((cx, cz), {})[(x, y, z)] = block_type
        chunk = world.chunks.get((cx, cz))
        if chunk is None:
            return
        was_generating = world._placing_generated_features
        world._placing_generated_features = True
        try:
            world.set_block(x, y, z, block_type, meta=int(meta) & 15)
        finally:
            world._placing_generated_features = was_generating

    def _population_height_value(self, world, x: int, z: int) -> int:
        for y in range(127, -1, -1):
            block = self._population_get(world, x, y, z)
            if block is not None and block not in (
                "yellow_flower", "red_flower", "brown_mushroom", "red_mushroom"
            ):
                return y + 1
        return 0

    def _population_heightmap_value(self, world, x: int, z: int) -> int:
        cx, cz = math.floor(x / 16), math.floor(z / 16)
        chunk = world.chunks.get((cx, cz))
        if chunk is not None:
            getter = getattr(chunk, "get_height_value", None)
            if callable(getter) and bool(getattr(chunk, "light_ready", False)):
                return int(getter(x - cx * 16, z - cz * 16))

            blocks = getattr(chunk, "blocks", None)
            palette = getattr(chunk, "_id_to_block_type", None)
            if palette is None:
                palette = getattr(chunk, "_palette", None)
            if blocks is not None and palette is not None:
                lx, lz = x - cx * 16, z - cz * 16
                height = int(getattr(chunk, "height", 128))
                depth = int(getattr(chunk, "depth", 16))
                if 0 <= lx < 16 and 0 <= lz < depth:
                    base = (lx * depth + lz) * height
                    for y in range(height - 1, -1, -1):
                        block_id = blocks[base + y]
                        if not block_id or block_id >= len(palette):
                            continue
                        block_type = palette[block_id]
                        if block_type is not None and _light_opacity_for_population(block_type) > 0:
                            return y + 1
                    return 0

        for y in range(127, -1, -1):
            block = self._population_get(world, x, y, z)
            if block is not None and _light_opacity_for_population(block) > 0:
                return y + 1
        return 0

    @staticmethod
    def _population_is_solid(block_type: str | None) -> bool:
        if block_type is None:
            return False
        return block_type not in {
            "water", "lava", "yellow_flower", "red_flower",
            "brown_mushroom", "red_mushroom", "leaves",
        }

    def _populate_minable(self, world, rng: JavaRandom, ore_type: str, vein_size: int,
                          x: int, y: int, z: int) -> bool:
        angle = _f32(rng.next_float() * _F_PI)
        sin_angle, cos_angle = _sin_f(angle), _cos_f(angle)
        float_size = _f32(float(vein_size))
        start_x = float(_f32(_f32(float(_f32(x + 8))) +
                             _f32(_f32(sin_angle * float_size) / _f32(8.0))))
        end_x = float(_f32(_f32(float(_f32(x + 8))) -
                           _f32(_f32(sin_angle * float_size) / _f32(8.0))))
        start_z = float(_f32(_f32(float(_f32(z + 8))) +
                             _f32(_f32(cos_angle * float_size) / _f32(8.0))))
        end_z = float(_f32(_f32(float(_f32(z + 8))) -
                           _f32(_f32(cos_angle * float_size) / _f32(8.0))))
        start_y = float(y + rng.next_int(3) + 2)
        end_y = float(y + rng.next_int(3) + 2)
        for step in range(vein_size + 1):
            t = float(step) / float(vein_size)
            center_x = start_x + (end_x - start_x) * t
            center_y = start_y + (end_y - start_y) * t
            center_z = start_z + (end_z - start_z) * t
            random_radius = rng.next_double() * float(vein_size) / 16.0
            angle = _f32(_f32(float(step) * _F_PI) / float_size)
            factor = float(_f32(_sin_f(angle) + _f32(1.0)))
            radius_xz = factor * random_radius + 1.0
            radius_y = factor * random_radius + 1.0
            for bx in range(int(center_x - radius_xz / 2.0),
                            int(center_x + radius_xz / 2.0) + 1):
                for by in range(int(center_y - radius_y / 2.0),
                                int(center_y + radius_y / 2.0) + 1):
                    for bz in range(int(center_z - radius_xz / 2.0),
                                    int(center_z + radius_xz / 2.0) + 1):
                        nx = (float(bx) + 0.5 - center_x) / (radius_xz / 2.0)
                        ny = (float(by) + 0.5 - center_y) / (radius_y / 2.0)
                        nz = (float(bz) + 0.5 - center_z) / (radius_xz / 2.0)
                        if nx * nx + ny * ny + nz * nz < 1.0:
                            if self._population_get(world, bx, by, bz) == "stone":
                                self._population_set(world, bx, by, bz, ore_type)
        return True

    def _populate_clay(self, world, rng: JavaRandom, x: int, y: int, z: int,
                       vein_size: int = 32) -> bool:
        if self._population_get(world, x, y, z) != "water":
            return False
        angle = _f32(rng.next_float() * _F_PI)
        sine, cosine = _sin_f(angle), _cos_f(angle)
        size = float(vein_size)
        start_x = float(_f32(float(_f32(x + 8)) + _f32(_f32(sine * size) / _f32(8.0))))
        end_x = float(_f32(float(_f32(x + 8)) - _f32(_f32(sine * size) / _f32(8.0))))
        start_z = float(_f32(float(_f32(z + 8)) + _f32(_f32(cosine * size) / _f32(8.0))))
        end_z = float(_f32(float(_f32(z + 8)) - _f32(_f32(cosine * size) / _f32(8.0))))
        start_y = float(y + rng.next_int(3) + 2)
        end_y = float(y + rng.next_int(3) + 2)
        for step in range(vein_size + 1):
            t = float(step) / float(vein_size)
            center_x = start_x + (end_x - start_x) * t
            center_y = start_y + (end_y - start_y) * t
            center_z = start_z + (end_z - start_z) * t
            random_radius = rng.next_double() * size / 16.0
            factor = float(_f32(_sin_f(_f32(float(step) * _F_PI / size)) + _f32(1.0)))
            radius_xz = factor * random_radius + 1.0
            radius_y = factor * random_radius + 1.0
            for bx in range(int(center_x - radius_xz / 2.0), int(center_x + radius_xz / 2.0) + 1):
                for by in range(int(center_y - radius_y / 2.0), int(center_y + radius_y / 2.0) + 1):
                    for bz in range(int(center_z - radius_xz / 2.0), int(center_z + radius_xz / 2.0) + 1):
                        nx = (bx + 0.5 - center_x) / (radius_xz / 2.0)
                        ny = (by + 0.5 - center_y) / (radius_y / 2.0)
                        nz = (bz + 0.5 - center_z) / (radius_xz / 2.0)
                        if nx * nx + ny * ny + nz * nz < 1.0 and self._population_get(world, bx, by, bz) == "sand":
                            self._population_set(world, bx, by, bz, "clay_block")
        return True

    def _populate_reed(self, world, rng: JavaRandom, x: int, y: int, z: int) -> bool:
        from game.block.plant_rules import reed_can_stay

        class _PopulationPlantView:
            def get_block_type(_self, bx, by, bz):
                return self._population_get(world, bx, by, bz)

        view = _PopulationPlantView()
        for _ in range(20):
            bx = x + rng.next_int(4) - rng.next_int(4)
            bz = z + rng.next_int(4) - rng.next_int(4)
            if self._population_get(world, bx, y, bz) is not None:
                continue
            if not any(self._population_get(world, bx + dx, y - 1, bz + dz) == "water"
                       for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                continue
            height = min(3, 2 + rng.next_int(rng.next_int(3) + 1))
            for dy in range(height):
                if reed_can_stay(view, bx, y + dy, bz):
                    self._population_set(world, bx, y + dy, bz, "reed")
        return True

    def _populate_flower(self, world, rng: JavaRandom, flower_type: str,
                         x: int, y: int, z: int) -> bool:
        is_mushroom = flower_type in ("brown_mushroom", "red_mushroom")
        for _ in range(64):
            bx = x + rng.next_int(8) - rng.next_int(8)
            by = y + rng.next_int(4) - rng.next_int(4)
            bz = z + rng.next_int(8) - rng.next_int(8)
            if by < 0 or by >= 128:
                continue
            if self._population_get(world, bx, by, bz) is not None:
                continue
            ground = self._population_get(world, bx, by - 1, bz)
            if is_mushroom:
                if not world._is_opaque_tree_block(ground):
                    continue
            elif ground not in ("grass_block", "dirt", "farmland"):
                continue

            sky_light = 15
            sky_visible = True
            for above_y in range(127, by, -1):
                above = self._population_get(world, bx, above_y, bz)
                if above is None:
                    continue
                light_opacity = _light_opacity_for_population(above)
                if light_opacity > 0:
                    sky_visible = False
                sky_light = max(0, sky_light - min(15, light_opacity))
                if sky_light == 0 and light_opacity >= 15:
                    break
            can_stay = (sky_light <= 13 if is_mushroom else (sky_light >= 8 or sky_visible))
            if can_stay:
                self._population_set(world, bx, by, bz, flower_type)
        return True

    def _populate_lake(
        self, world, rng: JavaRandom, liquid_type: str, x: int, y: int, z: int,
    ) -> bool:
        from game.block.material import material_for_block

        x -= 8
        z -= 8
        while y > 0 and self._population_get(world, x, y, z) is None:
            y -= 1
        y -= 4

        lake_mask = bytearray(16 * 16 * 8)
        ellipsoid_count = rng.next_int(4) + 4
        for _ in range(ellipsoid_count):
            size_x = rng.next_double() * 6.0 + 3.0
            size_y = rng.next_double() * 4.0 + 2.0
            size_z = rng.next_double() * 6.0 + 3.0
            center_x = rng.next_double() * (16.0 - size_x - 2.0) + 1.0 + size_x / 2.0
            center_y = rng.next_double() * (8.0 - size_y - 4.0) + 2.0 + size_y / 2.0
            center_z = rng.next_double() * (16.0 - size_z - 2.0) + 1.0 + size_z / 2.0

            for lx in range(1, 15):
                dx = (lx - center_x) / (size_x / 2.0)
                for lz in range(1, 15):
                    dz = (lz - center_z) / (size_z / 2.0)
                    for ly in range(1, 7):
                        dy = (ly - center_y) / (size_y / 2.0)
                        if dx * dx + dy * dy + dz * dz < 1.0:
                            lake_mask[(lx * 16 + lz) * 8 + ly] = 1

        for lx in range(16):
            for lz in range(16):
                for ly in range(8):
                    index = (lx * 16 + lz) * 8 + ly
                    if lake_mask[index]:
                        continue
                    adjacent = (
                        (lx < 15 and lake_mask[((lx + 1) * 16 + lz) * 8 + ly])
                        or (lx > 0 and lake_mask[((lx - 1) * 16 + lz) * 8 + ly])
                        or (lz < 15 and lake_mask[(lx * 16 + lz + 1) * 8 + ly])
                        or (lz > 0 and lake_mask[(lx * 16 + lz - 1) * 8 + ly])
                        or (ly < 7 and lake_mask[(lx * 16 + lz) * 8 + ly + 1])
                        or (ly > 0 and lake_mask[(lx * 16 + lz) * 8 + ly - 1])
                    )
                    if not adjacent:
                        continue
                    block = self._population_get(world, x + lx, y + ly, z + lz)
                    material = material_for_block(block)
                    if ly >= 4:
                        if material.is_liquid:
                            return False
                    else:
                        blocks_movement = (
                            material.is_solid
                            and block not in _LAKE_NONBLOCKING_LEGACY_BLOCKS
                        )
                        if not blocks_movement and block != liquid_type:
                            return False

        for lx in range(16):
            for lz in range(16):
                for ly in range(8):
                    if lake_mask[(lx * 16 + lz) * 8 + ly]:
                        self._population_set(
                            world, x + lx, y + ly, z + lz,
                            liquid_type if ly < 4 else None,
                        )

        sky_light_at = getattr(world, "_get_saved_light", None)
        for lx in range(16):
            for lz in range(16):
                for ly in range(4, 8):
                    if not lake_mask[(lx * 16 + lz) * 8 + ly]:
                        continue
                    px, py, pz = x + lx, y + ly, z + lz
                    if self._population_get(world, px, py - 1, pz) != "dirt":
                        continue
                    if callable(sky_light_at):
                        try:
                            sky_light = sky_light_at("sky", px, py, pz)
                        except (AttributeError, KeyError, TypeError, ValueError):
                            sky_light = 0
                    else:
                        sky_light = 15
                    if sky_light > 0:
                        self._population_set(world, px, py - 1, pz, "grass_block")

        if liquid_type == "lava":
            for lx in range(16):
                for lz in range(16):
                    for ly in range(8):
                        index = (lx * 16 + lz) * 8 + ly
                        if lake_mask[index]:
                            continue
                        adjacent = (
                            (lx < 15 and lake_mask[((lx + 1) * 16 + lz) * 8 + ly])
                            or (lx > 0 and lake_mask[((lx - 1) * 16 + lz) * 8 + ly])
                            or (lz < 15 and lake_mask[(lx * 16 + lz + 1) * 8 + ly])
                            or (lz > 0 and lake_mask[(lx * 16 + lz - 1) * 8 + ly])
                            or (ly < 7 and lake_mask[(lx * 16 + lz) * 8 + ly + 1])
                            or (ly > 0 and lake_mask[(lx * 16 + lz) * 8 + ly - 1])
                        )
                        if not adjacent:
                            continue
                        if ly >= 4 and rng.next_int(2) == 0:
                            continue
                        px, py, pz = x + lx, y + ly, z + lz
                        block = self._population_get(world, px, py, pz)
                        blocks_movement = (
                            material_for_block(block).is_solid
                            and block not in _LAKE_NONBLOCKING_LEGACY_BLOCKS
                        )
                        if blocks_movement:
                            self._population_set(world, px, py, pz, "stone")
        return True

    def _populate_liquid(self, world, liquid_type: str, x: int, y: int, z: int) -> bool:
        if self._population_get(world, x, y + 1, z) != "stone":
            return False
        if self._population_get(world, x, y - 1, z) != "stone":
            return False
        center = self._population_get(world, x, y, z)
        if center is not None and center != "stone":
            return False
        solid_neighbors = 0
        air_neighbors = 0
        for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            neighbor = self._population_get(world, x + dx, y, z + dz)
            if neighbor == "stone":
                solid_neighbors += 1
            if neighbor is None:
                air_neighbors += 1
        if solid_neighbors == 3 and air_neighbors == 1:
            self._population_set(world, x, y, z, liquid_type)
        return True

    @staticmethod
    def _pick_dungeon_loot_item(rng: JavaRandom):
        table = _load_dungeon_loot_table()
        roll_bound = int(table.get("roll_bound", 11))
        roll = rng.next_int(roll_bound)
        for entry in table.get("entries", ()):
            if int(entry.get("roll", -1)) != roll:
                continue
            secondary_bound = entry.get("secondary_bound")
            if secondary_bound is not None:
                secondary_roll = int(entry.get("secondary_roll", 0))
                if rng.next_int(int(secondary_bound)) != secondary_roll:
                    return None
            item = entry.get("item")
            if item is None and entry.get("items"):
                choices = list(entry.get("items", ()))
                if not choices:
                    return None
                item = choices[rng.next_int(len(choices))]
            if item is None:
                return None
            if "count_min" in entry or "count_max" in entry:
                lo = int(entry.get("count_min", 1))
                hi = int(entry.get("count_max", lo))
                count = rng.next_int(hi - lo + 1) + lo
            else:
                count = int(entry.get("count", 1))
            return (str(item), count)
        return None

    @staticmethod
    def _pick_dungeon_room_radii(rng: JavaRandom):
        return rng.next_int(2) + 2, rng.next_int(2) + 2

    def _populate_dungeon(self, world, rng: JavaRandom, x: int, y: int, z: int) -> bool:
        room_height = 3
        radius_x, radius_z = self._pick_dungeon_room_radii(rng)
        openings = 0
        for bx in range(x - radius_x - 1, x + radius_x + 2):
            for by in range(y - 1, y + room_height + 2):
                for bz in range(z - radius_z - 1, z + radius_z + 2):
                    block = self._population_get(world, bx, by, bz)
                    solid = self._population_is_solid(block)
                    if by == y - 1 and not solid:
                        return False
                    if by == y + room_height + 1 and not solid:
                        return False
                    if ((bx in (x - radius_x - 1, x + radius_x + 1)
                         or bz in (z - radius_z - 1, z + radius_z + 1))
                            and by == y and block is None
                            and self._population_get(world, bx, by + 1, bz) is None):
                        openings += 1
        if not 1 <= openings <= 5:
            return False

        for bx in range(x - radius_x - 1, x + radius_x + 2):
            for by in range(y + room_height, y - 2, -1):
                for bz in range(z - radius_z - 1, z + radius_z + 2):
                    interior = (bx != x - radius_x - 1 and by != y - 1
                                and bz != z - radius_z - 1
                                and bx != x + radius_x + 1
                                and by != y + room_height + 1
                                and bz != z + radius_z + 1)
                    current = self._population_get(world, bx, by, bz)
                    if interior:
                        self._population_set(world, bx, by, bz, None)
                    elif by >= 0 and not self._population_is_solid(
                            self._population_get(world, bx, by - 1, bz)):
                        self._population_set(world, bx, by, bz, None)
                    elif self._population_is_solid(current):
                        if by == y - 1 and rng.next_int(4) != 0:
                            self._population_set(world, bx, by, bz, "mossy_cobblestone")
                        else:
                            self._population_set(world, bx, by, bz, "cobblestone")

        for _chest in range(2):
            for _attempt in range(3):
                bx = x + rng.next_int(radius_x * 2 + 1) - radius_x
                bz = z + rng.next_int(radius_z * 2 + 1) - radius_z
                if self._population_get(world, bx, y, bz) is not None:
                    continue
                solid_neighbors = sum(
                    1 for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1))
                    if self._population_is_solid(self._population_get(world, bx + dx, y, bz + dz))
                )
                if solid_neighbors != 1:
                    continue
                self._population_set(world, bx, y, bz, "chest")
                contents = {}
                for _loot_roll in range(8):
                    loot_item = self._pick_dungeon_loot_item(rng)
                    if loot_item is None:
                        continue
                    item_id, count = loot_item
                    slot = rng.next_int(27)
                    contents[slot] = {"id": item_id, "count": count, "damage": 0}
                set_loot = getattr(world, "set_generated_chest_loot", None)
                if set_loot is not None:
                    set_loot(bx, y, bz, contents)
                break
        mob_roll = rng.next_int(4)
        mob_id = (
            legacy_entity_id("skeleton"), legacy_entity_id("zombie"),
            legacy_entity_id("zombie"), legacy_entity_id("spider"),
        )[mob_roll]
        self._population_set(world, x, y, z, "mob_spawner")
        set_mob = getattr(world, "set_generated_spawner_mob", None)
        if callable(set_mob):
            set_mob(x, y, z, mob_id)
        return True

    def _populate_cactus(self, world, rng: JavaRandom,
                         x: int, y: int, z: int) -> bool:
        from game.block.plant_rules import cactus_can_stay

        view = _PopulationWorldView(self, world)
        for _ in range(10):
            bx = x + rng.next_int(8) - rng.next_int(8)
            by = y + rng.next_int(4) - rng.next_int(4)
            bz = z + rng.next_int(8) - rng.next_int(8)
            if self._population_get(world, bx, by, bz) is not None:
                continue
            height = 1 + rng.next_int(rng.next_int(3) + 1)
            for dy in range(height):
                py = by + dy
                if 0 <= py < 128 and cactus_can_stay(view, bx, py, bz):
                    self._population_set(world, bx, py, bz, "cactus")
        return True

    _ORE_VEINS = (
        ("dirt", 32, 20, 128),
        ("gravel", 32, 10, 128),
        ("coal_ore", 16, 20, 128),
        ("iron_ore", 8, 20, 64),
        ("gold_ore", 8, 2, 32),
        ("redstone_ore", 7, 8, 16),
        ("diamond_ore", 7, 1, 16),
    )

    def _populate_ore_veins_fast(self, world, cx: int, cz: int) -> bool:
        if not self.use_numba or not getattr(self, "numba_ores", True):
            return False
        from game.world import ore_numba
        chunks = []
        for key in ((cx, cz), (cx + 1, cz), (cx, cz + 1), (cx + 1, cz + 1)):
            chunk = world.chunks.get(key)
            blocks = getattr(chunk, "blocks", None)
            if (blocks is None or len(blocks) != ore_numba.CHUNK_VOLUME
                    or getattr(chunk, "height", 128) != 128
                    or getattr(chunk, "depth", 16) != 16
                    or getattr(chunk, "width", 16) != 16):
                return False
            chunks.append(blocks)
        type_to_id = world.block_type_to_id
        stone_id = type_to_id.get("stone")
        ore_ids = [type_to_id.get(entry[0]) for entry in self._ORE_VEINS]
        if stone_id is None or any(i is None or not 0 < i < 256 for i in ore_ids):
            return False

        stacked = _np.empty((4, ore_numba.CHUNK_VOLUME), dtype=_np.uint8)
        for slot, blocks in enumerate(chunks):
            stacked[slot] = _np.frombuffer(blocks, dtype=_np.uint8)
        out = _np.empty((ore_numba.MAX_PLACEMENTS, 4), dtype=_np.int32)
        status, state, count = ore_numba.populate_ore_veins(
            stacked, cx * 16, cz * 16, int(self.rand.seed), int(stone_id),
            _np.asarray(ore_ids, dtype=_np.int64),
            _np.asarray([e[1] for e in self._ORE_VEINS], dtype=_np.int64),
            _np.asarray([e[2] for e in self._ORE_VEINS], dtype=_np.int64),
            _np.asarray([e[3] for e in self._ORE_VEINS], dtype=_np.int64),
            ore_numba.SIN_TABLE, out,
        )
        if status != ore_numba.STATUS_OK:
            return False
        self.rand.seed = int(state)
        ore_types = [entry[0] for entry in self._ORE_VEINS]
        placements = out[:count].tolist()
        for x, y, z, ore_index in placements:
            self._population_set(world, x, y, z, ore_types[ore_index])
        return True

    def populate_chunk(self, world, cx: int, cz: int) -> None:
        if (cx, cz) not in world.chunks:
            return
        rng = self.rand
        rng.set_seed(self.seed)
        odd_x = _signed64(_java_div2(rng.next_long()) * 2 + 1)
        odd_z = _signed64(_java_div2(rng.next_long()) * 2 + 1)
        rng.set_seed(_signed64(cx * odd_x + cz * odd_z) ^ self.seed)
        start_x, start_z = cx * 16, cz * 16
        was_generating = world._placing_generated_features
        world._placing_generated_features = True
        try:
            if rng.next_int(4) == 0:
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(128)
                z = start_z + rng.next_int(16) + 8
                self._populate_lake(world, rng, "water", x, y, z)

            if rng.next_int(8) == 0:
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(rng.next_int(120) + 8)
                z = start_z + rng.next_int(16) + 8
                if y < 64 or rng.next_int(10) == 0:
                    self._populate_lake(world, rng, "lava", x, y, z)

            for _ in range(8):
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(128)
                z = start_z + rng.next_int(16) + 8
                self._populate_dungeon(world, rng, x, y, z)

            for _ in range(10):
                x = start_x + rng.next_int(16)
                y = rng.next_int(128)
                z = start_z + rng.next_int(16)
                self._populate_clay(world, rng, x, y, z, 32)

            if not self._populate_ore_veins_fast(world, cx, cz):
                for block_type, vein_size, attempts, height in self._ORE_VEINS:
                    for _ in range(attempts):
                        x = start_x + rng.next_int(16)
                        y = rng.next_int(height)
                        z = start_z + rng.next_int(16)
                        self._populate_minable(world, rng, block_type, vein_size, x, y, z)

            biome = self.biomes.get_biome(start_x + 16, start_z + 16)
            density = self.mob_spawner_noise.generate_2d(
                start_x * 0.5, start_z * 0.5,
            ) / 8.0
            biome_tree_count = int((density + rng.next_double() * 4.0 + 4.0) / 3.0)
            tree_count = 0
            if rng.next_int(10) == 0:
                tree_count += 1
            if biome.key in ("forest", "rainforest", "taiga"):
                tree_count += biome_tree_count + 5
            elif biome.key == "seasonal_forest":
                tree_count += biome_tree_count + 2
            elif biome.key in ("desert", "tundra", "plains"):
                tree_count -= 20

            use_big_tree = rng.next_int(10) == 0
            if biome.key == "rainforest" and rng.next_int(3) == 0:
                use_big_tree = True

            from game.world.treegen import (
                create_alpha_big_tree_generator, generate_alpha_tree,
            )
            tree_world = _PopulationWorldView(self, world)
            big_tree_generator = (
                create_alpha_big_tree_generator(
                    tree_world, rng, world._is_opaque_tree_block, 128,
                ) if use_big_tree else None
            )
            if big_tree_generator is not None:
                big_tree_generator.set_scale(1.0, 1.0, 1.0)
            for _ in range(max(0, tree_count)):
                x = start_x + rng.next_int(16) + 8
                z = start_z + rng.next_int(16) + 8
                surface_y = self._population_heightmap_value(world, x, z)
                if use_big_tree:
                    big_tree_generator.generate(x, surface_y, z)
                else:
                    generate_alpha_tree(
                        tree_world, x, surface_y, z, rng,
                        world._is_opaque_tree_block, 128,
                    )

            for _ in range(2):
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(128)
                z = start_z + rng.next_int(16) + 8
                self._populate_flower(world, rng, "yellow_flower", x, y, z)
            if rng.next_int(2) == 0:
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(128)
                z = start_z + rng.next_int(16) + 8
                self._populate_flower(world, rng, "red_flower", x, y, z)
            if rng.next_int(4) == 0:
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(128)
                z = start_z + rng.next_int(16) + 8
                self._populate_flower(world, rng, "brown_mushroom", x, y, z)
            if rng.next_int(8) == 0:
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(128)
                z = start_z + rng.next_int(16) + 8
                self._populate_flower(world, rng, "red_mushroom", x, y, z)

            for _ in range(10):
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(128)
                z = start_z + rng.next_int(16) + 8
                self._populate_reed(world, rng, x, y, z)

            if rng.next_int(32) == 0:
                ox = start_x + rng.next_int(16) + 8
                oy = rng.next_int(128)
                oz = start_z + rng.next_int(16) + 8
                for _ in range(64):
                    px = ox + rng.next_int(8) - rng.next_int(8)
                    py = oy + rng.next_int(4) - rng.next_int(4)
                    pz = oz + rng.next_int(8) - rng.next_int(8)
                    if py < 1 or py >= 128:
                        continue
                    if self._population_get(world, px, py, pz) is not None:
                        continue
                    if self._population_get(world, px, py - 1, pz) != "grass_block":
                        continue
                    meta = rng.next_int(4)
                    self._population_set(world, px, py, pz, "pumpkin", meta=meta)

            cactus_attempts = 10 if biome.key == "desert" else 0
            for _ in range(cactus_attempts):
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(128)
                z = start_z + rng.next_int(16) + 8
                self._populate_cactus(world, rng, x, y, z)

            for _ in range(50):
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(rng.next_int(120) + 8)
                z = start_z + rng.next_int(16) + 8
                self._populate_liquid(world, "water", x, y, z)
            for _ in range(20):
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(rng.next_int(rng.next_int(112) + 8) + 8)
                z = start_z + rng.next_int(16) + 8
                self._populate_liquid(world, "lava", x, y, z)
            self._populate_snow_layers(world, start_x, start_z)
        finally:
            world._placing_generated_features = was_generating

    @staticmethod
    def _population_material_definitions():
        from game.block import Block
        Block._load_defs()
        return Block._block_defs

    def _population_top_solid_or_liquid_height(
        self, world, x: int, z: int, block_definitions=None,
    ) -> int:
        from game.block.material import material_for_block
        if block_definitions is None:
            block_definitions = self._population_material_definitions()
        for y in range(127, -1, -1):
            block = self._population_get(world, x, y, z)
            if block is None:
                continue
            material = material_for_block(block, block_definitions.get(block, {}))
            if material.is_solid or material.is_liquid:
                return y + 1
        return -1

    def _populate_snow_layers(self, world, start_x: int, start_z: int,
                              snowy_biomes_only: bool = False) -> None:
        from game.block.material import material_for_block
        block_definitions = self._population_material_definitions()
        for x in range(start_x + 8, start_x + 24):
            for z in range(start_z + 8, start_z + 24):
                y = self._population_top_solid_or_liquid_height(
                    world, x, z, block_definitions,
                )
                if not (0 < y < 128):
                    continue
                if self._population_get(world, x, y, z) is not None:
                    continue
                below = self._population_get(world, x, y - 1, z)
                if below is None or below == "ice":
                    continue
                if not material_for_block(
                    below, block_definitions.get(below, {}),
                ).is_solid:
                    continue
                temperature = self.biomes.get_climate(x, z)[0]
                adjusted_temperature = temperature - (y - 64) / 64.0 * 0.3
                if self.snow_covered or adjusted_temperature < 0.5:
                    self._population_set(world, x, y, z, "snow")


class _PopulationWorldView:

    def __init__(self, generator: AlphaTerrainGenerator, world):
        self.generator = generator
        self.world = world

    def get_block_type(self, x, y, z):
        return self.generator._population_get(self.world, x, y, z)

    def set_block(self, x, y, z, block_type, meta=0, immediate_mesh=False):
        self.generator._population_set(self.world, x, y, z, block_type)

    def generate_chunk(self, cx: int, cz: int,
                       block_type_to_id: dict[str, int] | None = None) -> dict[tuple[int, int, int], str]:
        palette = block_type_to_id or {name: i + 1 for i, name in enumerate(self._WRAPPER_TYPES)}
        data = self.generate_chunk_data(cx, cz, 16, 16, palette)
        reverse = {v: k for k, v in palette.items()}
        result = {}
        for x in range(16):
            for z in range(16):
                base = (x * 16 + z) * 128
                for y in range(128):
                    block_id = data[base + y]
                    if block_id:
                        result[(cx * 16 + x, y, cz * 16 + z)] = reverse.get(block_id, "unknown")
        return result
