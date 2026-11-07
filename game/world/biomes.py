from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
from pathlib import Path
import struct
import zlib
from collections import OrderedDict

from game.world.java_random import JavaRandom
try:
    from game.world.terrain_numba import (
        NUMBA_AVAILABLE as _CLIMATE_NUMBA,
        np as _climate_np,
        fill_climate_arrays as _numba_fill_climate,
    )
except Exception:
    _CLIMATE_NUMBA = False
    _climate_np = None
    _numba_fill_climate = None


@dataclass(frozen=True)
class Biome:
    key: str
    name: str
    top_block: str = "grass_block"
    filler_block: str = "dirt"
    snowy: bool = False


BIOMES = {
    "rainforest": Biome("rainforest", "Rainforest"),
    "swampland": Biome("swampland", "Swampland"),
    "seasonal_forest": Biome("seasonal_forest", "Seasonal Forest"),
    "forest": Biome("forest", "Forest"),
    "savanna": Biome("savanna", "Savanna"),
    "shrubland": Biome("shrubland", "Shrubland"),
    "taiga": Biome("taiga", "Taiga", snowy=True),
    "desert": Biome("desert", "Desert", "sand", "sand"),
    "plains": Biome("plains", "Plains"),
    "ice_desert": Biome("ice_desert", "Ice Desert", "sand", "sand", True),
    "tundra": Biome("tundra", "Tundra", snowy=True),
    "hell": Biome("hell", "Hell", "netherrack", "netherrack"),
    "void": Biome("void", "Void"),
}


_SKEW = 0.5 * (3.0 ** 0.5 - 1.0)
_UNSKEW = (3.0 - 3.0 ** 0.5) / 6.0
_GRADIENTS = (
    (1, 1), (-1, 1), (1, -1), (-1, -1),
    (1, 0), (-1, 0), (1, 0), (-1, 0),
    (0, 1), (0, -1), (0, 1), (0, -1),
)


def _java_floor(value: float) -> int:
    integer = int(value)
    return integer if value > 0.0 else integer - 1


class _SimplexNoise2D:
    def __init__(self, rng: JavaRandom):
        self.x_offset = rng.next_double() * 256.0
        self.y_offset = rng.next_double() * 256.0
        self.z_offset = rng.next_double() * 256.0
        permutation = list(range(256)) + [0] * 256
        for i in range(256):
            j = rng.next_int(256 - i) + i
            permutation[i], permutation[j] = permutation[j], permutation[i]
            permutation[i + 256] = permutation[i]
        self.permutation = permutation

    @staticmethod
    def _dot(gradient, x, y):
        return gradient[0] * x + gradient[1] * y

    def sample(self, x: float, z: float, scale_x: float, scale_z: float,
               amplitude: float) -> float:
        x = x * scale_x + self.x_offset
        z = z * scale_z + self.y_offset
        skew = (x + z) * _SKEW
        i = _java_floor(x + skew)
        j = _java_floor(z + skew)
        unskew = (i + j) * _UNSKEW
        x0 = x - (i - unskew)
        z0 = z - (j - unskew)
        if x0 > z0:
            i1, j1 = 1, 0
        else:
            i1, j1 = 0, 1
        x1 = x0 - i1 + _UNSKEW
        z1 = z0 - j1 + _UNSKEW
        x2 = x0 - 1.0 + 2.0 * _UNSKEW
        z2 = z0 - 1.0 + 2.0 * _UNSKEW
        p = self.permutation
        ii, jj = i & 255, j & 255
        g0 = p[ii + p[jj]] % 12
        g1 = p[ii + i1 + p[jj + j1]] % 12
        g2 = p[ii + 1 + p[jj + 1]] % 12

        def contribution(dx, dz, gradient_index):
            attenuation = 0.5 - dx * dx - dz * dz
            if attenuation < 0.0:
                return 0.0
            attenuation *= attenuation
            return attenuation * attenuation * self._dot(
                _GRADIENTS[gradient_index], dx, dz
            )

        return 70.0 * (
            contribution(x0, z0, g0)
            + contribution(x1, z1, g1)
            + contribution(x2, z2, g2)
        ) * amplitude


class _Octaves2D:
    def __init__(self, seed: int, octave_count: int):
        rng = JavaRandom(seed)
        self.generators = [_SimplexNoise2D(rng) for _ in range(octave_count)]

    def sample(self, x: float, z: float, scale_x: float, scale_z: float,
               persistence: float) -> float:
        scale_x /= 1.5
        scale_z /= 1.5
        frequency = 1.0
        amplitude_reciprocal = 1.0
        total = 0.0
        for generator in self.generators:
            total += generator.sample(
                x, z, scale_x * frequency, scale_z * frequency,
                0.55 / amplitude_reciprocal,
            )
            frequency *= persistence
            amplitude_reciprocal *= 0.5
        return total


def _float32(value: float) -> float:
    return struct.unpack(">f", struct.pack(">f", float(value)))[0]


def _classify_climate(temperature: float, humidity: float) -> Biome:
    temperature = _float32(temperature)
    humidity = _float32(humidity)
    moisture = _float32(temperature * humidity)
    if temperature < 0.1:
        key = "tundra"
    elif moisture < 0.2:
        if temperature < 0.5:
            key = "tundra"
        elif temperature < 0.95:
            key = "savanna"
        else:
            key = "desert"
    elif moisture > 0.5 and temperature < 0.7:
        key = "swampland"
    elif temperature < 0.5:
        key = "taiga"
    elif temperature < 0.97:
        key = "shrubland" if moisture < 0.35 else "forest"
    elif moisture < 0.45:
        key = "plains"
    elif moisture < 0.9:
        key = "seasonal_forest"
    else:
        key = "rainforest"
    return BIOMES[key]


_BIOME_LOOKUP = tuple(
    tuple(_classify_climate(
        _float32(_float32(temp_index) / _float32(63.0)),
        _float32(_float32(humidity_index) / _float32(63.0)),
    ) for temp_index in range(64))
    for humidity_index in range(64)
)


def biome_from_climate(temperature: float, humidity: float) -> Biome:
    temp_index = max(0, min(63, int(float(temperature) * 63.0)))
    humidity_index = max(0, min(63, int(float(humidity) * 63.0)))
    return _BIOME_LOOKUP[humidity_index][temp_index]


def biome_sky_color(temperature: float) -> tuple[int, int, int]:
    climate = _float32(_float32(temperature) / _float32(3.0))
    climate = max(-1.0, min(1.0, climate))
    hue = _float32(_float32(224.0 / 360.0) - _float32(climate * _float32(0.05)))
    saturation = _float32(_float32(0.5) + _float32(climate * _float32(0.1)))
    brightness = _float32(1.0)

    if saturation == 0.0:
        channel = int(_float32(_float32(brightness * 255.0) + 0.5))
        return channel, channel, channel

    h = _float32((hue - float(math.floor(hue))) * 6.0)
    sector = int(math.floor(h))
    fraction = _float32(h - float(math.floor(h)))
    p = _float32(brightness * _float32(1.0 - saturation))
    q = _float32(brightness * _float32(1.0 - _float32(saturation * fraction)))
    t = _float32(brightness * _float32(1.0 - _float32(saturation * _float32(1.0 - fraction))))
    if sector == 0:
        rgb = (brightness, t, p)
    elif sector == 1:
        rgb = (q, brightness, p)
    elif sector == 2:
        rgb = (p, brightness, t)
    elif sector == 3:
        rgb = (p, q, brightness)
    elif sector == 4:
        rgb = (t, p, brightness)
    else:
        rgb = (brightness, p, q)
    return tuple(
        int(_float32(_float32(channel * 255.0) + 0.5))
        for channel in rgb
    )


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def _read_rgb_png(path: str | Path) -> tuple[int, int, bytes]:
    raw = Path(path).read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    offset = 8
    width = height = bit_depth = color_type = None
    compressed = bytearray()
    while offset + 12 <= len(raw):
        length = struct.unpack_from(">I", raw, offset)[0]
        chunk_type = raw[offset + 4:offset + 8]
        chunk = raw[offset + 8:offset + 8 + length]
        offset += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
            if bit_depth != 8 or color_type not in (2, 6) or interlace != 0:
                raise ValueError("color map must be a non-interlaced 8-bit RGB/RGBA PNG")
        elif chunk_type == b"IDAT":
            compressed.extend(chunk)
        elif chunk_type == b"IEND":
            break
    if not width or not height:
        raise ValueError("PNG has no image header")
    channels = 3 if color_type == 2 else 4

    stride = width * channels
    decoded = zlib.decompress(bytes(compressed))
    expected = height * (stride + 1)
    if len(decoded) != expected:
        raise ValueError("unexpected PNG scanline length")
    pixels = bytearray(height * stride)
    src_offset = 0
    for y in range(height):
        filter_type = decoded[src_offset]
        src_offset += 1
        row = bytearray(decoded[src_offset:src_offset + stride])
        src_offset += stride
        previous = pixels[(y - 1) * stride:y * stride] if y else None
        for i in range(stride):
            left = row[i - channels] if i >= channels else 0
            above = previous[i] if previous is not None else 0
            upper_left = previous[i - channels] if previous is not None and i >= channels else 0
            if filter_type == 1:
                row[i] = (row[i] + left) & 255
            elif filter_type == 2:
                row[i] = (row[i] + above) & 255
            elif filter_type == 3:
                row[i] = (row[i] + ((left + above) >> 1)) & 255
            elif filter_type == 4:
                row[i] = (row[i] + _paeth(left, above, upper_left)) & 255
            elif filter_type != 0:
                raise ValueError(f"unsupported PNG filter: {filter_type}")
        pixels[y * stride:(y + 1) * stride] = row

    if channels == 3:
        return width, height, bytes(pixels)
    rgb = bytearray(width * height * 3)
    for index in range(width * height):
        rgb[index * 3:index * 3 + 3] = pixels[index * 4:index * 4 + 3]
    return width, height, bytes(rgb)


@lru_cache(maxsize=8)
def _load_color_map(resolved_path: str):
    return _read_rgb_png(resolved_path)


@lru_cache(maxsize=8)
def _resolved_color_map_path(texture_name: str, pack_name: str) -> str:
    from game.app.texturepacks import resolve_texture_path
    legacy_path = f"game/textures/misc/{texture_name}color.png"
    resolved = resolve_texture_path(legacy_path, pack_name=pack_name)
    return str(Path(resolved).resolve())


def _color_map_tint(texture_name: str, temperature: float, humidity: float):
    from game.app.texturepacks import get_active_texture_pack
    resolved = _resolved_color_map_path(texture_name, get_active_texture_pack())
    width, height, rgb = _load_color_map(resolved)
    x = max(0, min(width - 1, int((1.0 - temperature) * 255.0)))
    y = max(0, min(height - 1, int((1.0 - temperature * humidity) * 255.0)))
    offset = (y * width + x) * 3
    return rgb[offset], rgb[offset + 1], rgb[offset + 2]


class BiomeProvider:
    def __init__(self, seed: int, use_numba: bool = False):
        self.seed = int(seed)
        self.use_numba = bool(use_numba)
        self.temperature_noise = _Octaves2D(self.seed * 9871, 4)
        self.humidity_noise = _Octaves2D(self.seed * 39811, 4)
        self.detail_noise = _Octaves2D(self.seed * 543321, 2)
        self._climate_cache = OrderedDict()
        self._raw_temperature_cache = OrderedDict()
        self._climate_cache_limit = 65536
        self._grass_tint_cache = OrderedDict()
        self._foliage_tint_cache = OrderedDict()
        self._tint_cache_limit = 65536

    def get_climate(self, x: int, z: int) -> tuple[float, float]:
        key = (int(x), int(z))
        cached = self._climate_cache.get(key)
        if cached is not None:
            self._climate_cache.move_to_end(key)
            return cached
        x, z = key
        temperature_scale = _float32(0.025)
        humidity_scale = _float32(0.05)
        temperature_noise = self.temperature_noise.sample(
            x, z, temperature_scale, temperature_scale, 0.25
        )
        humidity_noise = self.humidity_noise.sample(
            x, z, humidity_scale, humidity_scale, 1.0 / 3.0
        )
        detail = self.detail_noise.sample(x, z, 0.25, 0.25, 0.5882352941176471)
        detail = detail * 1.1 + 0.5
        temperature = (temperature_noise * 0.15 + 0.7) * 0.99 + detail * 0.01
        humidity = (humidity_noise * 0.15 + 0.5) * 0.998 + detail * 0.002
        temperature = 1.0 - (1.0 - temperature) * (1.0 - temperature)
        temperature = max(0.0, min(1.0, temperature))
        humidity = max(0.0, min(1.0, humidity))
        result = (temperature, humidity)
        self._climate_cache[key] = result
        if len(self._climate_cache) > self._climate_cache_limit:
            self._climate_cache.popitem(last=False)
        return result

    def fill_climate_region(self, x0: int, z0: int, size_x: int, size_z: int):
        n = size_x * size_z
        x0 = int(x0)
        z0 = int(z0)
        temperature_scale = _float32(0.025)
        humidity_scale = _float32(0.05)
        cache = self._climate_cache
        cache_limit = self._climate_cache_limit

        all_cached = True
        temps = [0.0] * n
        humids = [0.0] * n
        for lz in range(size_z):
            wz = z0 + lz
            row = lz * size_x
            for lx in range(size_x):
                wx = x0 + lx
                cached = cache.get((wx, wz))
                if cached is None:
                    all_cached = False
                    break
                temps[row + lx] = cached[0]
                humids[row + lx] = cached[1]
            if not all_cached:
                break
        if all_cached:
            return temps, humids

        if self.use_numba and _CLIMATE_NUMBA and _numba_fill_climate is not None:
            packs = getattr(self, "_numba_climate_packs", None)
            if packs is None:
                def _pack(octaves):
                    gens = octaves.generators
                    count = len(gens)
                    perm = _climate_np.empty(count * 512, dtype=_climate_np.int64)
                    xoff = _climate_np.empty(count, dtype=_climate_np.float64)
                    yoff = _climate_np.empty(count, dtype=_climate_np.float64)
                    for i, g in enumerate(gens):
                        perm[i * 512:(i + 1) * 512] = _climate_np.asarray(
                            g.permutation, dtype=_climate_np.int64,
                        )
                        xoff[i] = float(g.x_offset)
                        yoff[i] = float(g.y_offset)
                    return perm, xoff, yoff, count
                packs = {
                    "temp": _pack(self.temperature_noise),
                    "humid": _pack(self.humidity_noise),
                    "detail": _pack(self.detail_noise),
                }
                self._numba_climate_packs = packs
            tp, tx, ty, tc = packs["temp"]
            hp, hx, hy, hc = packs["humid"]
            dp, dx, dy, dc = packs["detail"]
            t_arr = _climate_np.empty(n, dtype=_climate_np.float64)
            h_arr = _climate_np.empty(n, dtype=_climate_np.float64)
            _numba_fill_climate(
                tp, tx, ty, tc,
                hp, hx, hy, hc,
                dp, dx, dy, dc,
                x0, z0, size_x, size_z,
                t_arr, h_arr,
                float(temperature_scale), float(humidity_scale),
            )
            temps = t_arr.tolist()
            humids = h_arr.tolist()
            for lz in range(size_z):
                wz = z0 + lz
                row = lz * size_x
                for lx in range(size_x):
                    wx = x0 + lx
                    idx = row + lx
                    cache[(wx, wz)] = (temps[idx], humids[idx])
            while len(cache) > cache_limit:
                cache.popitem(last=False)
            return temps, humids

        for lz in range(size_z):
            wz = z0 + lz
            row = lz * size_x
            for lx in range(size_x):
                wx = x0 + lx
                key = (wx, wz)
                cached = cache.get(key)
                if cached is not None:
                    t, h = cached
                else:
                    temperature_noise = self.temperature_noise.sample(
                        wx, wz, temperature_scale, temperature_scale, 0.25
                    )
                    humidity_noise = self.humidity_noise.sample(
                        wx, wz, humidity_scale, humidity_scale, 1.0 / 3.0
                    )
                    detail = self.detail_noise.sample(wx, wz, 0.25, 0.25, 0.5882352941176471)
                    detail = detail * 1.1 + 0.5
                    t = (temperature_noise * 0.15 + 0.7) * 0.99 + detail * 0.01
                    h = (humidity_noise * 0.15 + 0.5) * 0.998 + detail * 0.002
                    t = 1.0 - (1.0 - t) * (1.0 - t)
                    t = max(0.0, min(1.0, t))
                    h = max(0.0, min(1.0, h))
                    cache[key] = (t, h)
                temps[row + lx] = t
                humids[row + lx] = h
        while len(cache) > cache_limit:
            cache.popitem(last=False)
        return temps, humids

    def get_raw_temperature(self, x: int, z: int) -> float:
        key = (int(x), int(z))
        cached = self._raw_temperature_cache.get(key)
        if cached is not None:
            self._raw_temperature_cache.move_to_end(key)
            return cached
        scale = _float32(0.025)
        value = self.temperature_noise.sample(key[0], key[1], scale, scale, 0.5)
        self._raw_temperature_cache[key] = value
        if len(self._raw_temperature_cache) > self._climate_cache_limit:
            self._raw_temperature_cache.popitem(last=False)
        return value

    def get_biome(self, x: int, z: int) -> Biome:
        temperature, humidity = self.get_climate(x, z)
        return biome_from_climate(temperature, humidity)

    def get_tint(self, x: int, z: int, kind: str) -> tuple[int, int, int]:
        cache = self._grass_tint_cache if kind == "grass" else self._foliage_tint_cache
        from game.app.texturepacks import get_active_texture_pack
        key = (int(x), int(z), get_active_texture_pack())
        cached = cache.get(key)
        if cached is not None:
            cache.move_to_end(key)
            return cached
        temperature, humidity = self.get_climate(key[0], key[1])
        try:
            tint = _color_map_tint("grass" if kind == "grass" else "foliage",
                                   temperature, humidity)
        except (OSError, ValueError, struct.error, zlib.error):
            tint = (127, 178, 56) if kind == "grass" else (89, 174, 48)
        cache[key] = tint
        if len(cache) > self._tint_cache_limit:
            cache.popitem(last=False)
        return tint

    def get_grass_tint(self, x: int, z: int) -> tuple[int, int, int]:
        return self.get_tint(x, z, "grass")

    def get_foliage_tint(self, x: int, z: int) -> tuple[int, int, int]:
        return self.get_tint(x, z, "foliage")


class VoidBiomeProvider(BiomeProvider):

    def get_biome(self, x: int, z: int) -> Biome:
        return BIOMES["void"]

    def get_grass_tint(self, x: int, z: int) -> tuple[int, int, int]:
        return (255, 255, 255)

    def get_foliage_tint(self, x: int, z: int) -> tuple[int, int, int]:
        return (255, 255, 255)
