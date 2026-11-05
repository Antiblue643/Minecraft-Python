
import math
from functools import lru_cache


def _mix32(value):
    value &= 0xFFFFFFFF
    value ^= value >> 16
    value = (value * 0x7FEB352D) & 0xFFFFFFFF
    value ^= value >> 15
    value = (value * 0x846CA68B) & 0xFFFFFFFF
    value ^= value >> 16
    return value & 0xFFFFFFFF


def _seed_for(seed, x, z, salt=0):
    value = (int(seed) & 0xFFFFFFFF) ^ _mix32(x * 0x1F123BB5)
    value ^= _mix32(z * 0x5F356495)
    value ^= _mix32(salt * 0x6C8E9CF5)
    return _mix32(value)


@lru_cache(maxsize=524288)
def _lattice_value(ix, iz, seed):
    value = _seed_for(seed, ix, iz)
    return (value / 2147483647.5) - 1.0


def _fade(t):
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _lerp(a, b, t):
    return a + (b - a) * t


def _noise2(x, z, seed):
    x0 = math.floor(x)
    z0 = math.floor(z)
    tx = _fade(x - x0)
    tz = _fade(z - z0)

    a = _lattice_value(x0, z0, seed)
    b = _lattice_value(x0 + 1, z0, seed)
    c = _lattice_value(x0, z0 + 1, seed)
    d = _lattice_value(x0 + 1, z0 + 1, seed)
    return _lerp(_lerp(a, b, tx), _lerp(c, d, tx), tz)