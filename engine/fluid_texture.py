from __future__ import annotations

import math
import random

import numpy as np
from numba import njit

MAX_CATCHUP_TICKS = 3

_COS_SHIFT = np.array([int(math.cos(i * math.pi * 2.0 / 16.0) * 1.2) for i in range(16)],
                      dtype=np.int64)
_SIN_SHIFT = np.array([int(math.sin(i * math.pi * 2.0 / 16.0) * 1.2) for i in range(16)],
                      dtype=np.int64)


@njit(cache=True, nogil=True, fastmath=False)
def _water_still_step(red, green, blue, alpha, rnd):
    for x in range(16):
        for y in range(16):
            value = 0.0
            for xx in range(x - 1, x + 2):
                value += red[(xx & 15) + y * 16]
            green[x + y * 16] = value / 3.3 + blue[x + y * 16] * 0.8
    for x in range(16):
        for y in range(16):
            i = x + y * 16
            blue[i] = max(0.0, blue[i] + alpha[i] * 0.05)
            alpha[i] -= 0.1
            if rnd[i] < 0.05:
                alpha[i] = 0.5


@njit(cache=True, nogil=True, fastmath=False)
def _water_flow_step(red, green, blue, alpha, rnd):
    for x in range(16):
        for y in range(16):
            value = 0.0
            for yy in range(y - 2, y + 1):
                value += red[(x & 15) + (yy & 15) * 16]
            green[x + y * 16] = value / 3.2 + blue[x + y * 16] * 0.8
    for x in range(16):
        for y in range(16):
            i = x + y * 16
            blue[i] = max(0.0, blue[i] + alpha[i] * 0.05)
            alpha[i] -= 0.3
            if rnd[i] < 0.2:
                alpha[i] = 0.5


@njit(cache=True, nogil=True, fastmath=False)
def _lava_step(red, green, blue, alpha, rnd, y_shifts, x_shifts):
    for x in range(16):
        for y in range(16):
            value = 0.0
            y_shift = y_shifts[y]
            x_shift = x_shifts[x]
            for xx in range(x - 1, x + 2):
                for yy in range(y - 1, y + 2):
                    sx = (xx + y_shift) & 15
                    sy = (yy + x_shift) & 15
                    value += red[sx + sy * 16]
            i = x + y * 16
            green[i] = value / 10.0 + (
                blue[(x & 15) + (y & 15) * 16]
                + blue[((x + 1) & 15) + (y & 15) * 16]
                + blue[((x + 1) & 15) + ((y + 1) & 15) * 16]
                + blue[(x & 15) + ((y + 1) & 15) * 16]
            ) / 4.0 * 0.8
            blue[i] = max(0.0, blue[i] + alpha[i] * 0.01)
            alpha[i] -= 0.06
            if rnd[i] < 0.005:
                alpha[i] = 1.5


@njit(cache=True, nogil=True, fastmath=False)
def _write_water(red, shift, out):
    for i in range(256):
        value = min(1.0, max(0.0, red[(i - shift) & 255]))
        level = value * value
        at = i * 4
        out[at] = int(32.0 + level * 32.0) & 255
        out[at + 1] = int(50.0 + level * 64.0) & 255
        out[at + 2] = 255
        out[at + 3] = int(146.0 + level * 50.0) & 255


@njit(cache=True, nogil=True, fastmath=False)
def _write_lava(red, shift, out):
    for i in range(256):
        value = min(1.0, max(0.0, red[(i - shift) & 255] * 2.0))
        at = i * 4
        out[at] = int(value * 100.0 + 155.0) & 255
        out[at + 1] = int(value * value * 255.0) & 255
        out[at + 2] = int(value * value * value * value * 128.0) & 255
        out[at + 3] = 255


class _FluidTextureFX:
    def __init__(self, icon_index: int, seed=None):
        self.icon_index = int(icon_index)
        self.red = np.zeros(256, dtype=np.float64)
        self.green = np.zeros(256, dtype=np.float64)
        self.blue = np.zeros(256, dtype=np.float64)
        self.alpha = np.zeros(256, dtype=np.float64)
        self.image_data = bytearray(16 * 16 * 4)
        self._out = np.frombuffer(self.image_data, dtype=np.uint8)
        self.tick_counter = 0
        self.rng = random.Random(seed)

    def _draw_randoms(self):
        rng = self.rng.random
        drawn = np.fromiter((rng() for _ in range(256)), dtype=np.float64, count=256)
        return np.ascontiguousarray(drawn.reshape(16, 16).T).ravel()

    @staticmethod
    def _clamp01(value):
        return min(1.0, max(0.0, value))


class WaterStillTextureFX(_FluidTextureFX):
    def __init__(self, seed=None):
        super().__init__(205, seed)

    def on_tick(self):
        self.tick_counter += 1
        _water_still_step(self.red, self.green, self.blue, self.alpha,
                          self._draw_randoms())
        self.red, self.green = self.green, self.red
        _write_water(self.red, 0, self._out)


class WaterFlowTextureFX(_FluidTextureFX):
    tile_span = 2

    def __init__(self, seed=None):
        super().__init__(206, seed)

    def on_tick(self):
        self.tick_counter += 1
        _water_flow_step(self.red, self.green, self.blue, self.alpha,
                         self._draw_randoms())
        self.red, self.green = self.green, self.red
        _write_water(self.red, self.tick_counter * 16, self._out)


class LavaStillTextureFX(_FluidTextureFX):
    def __init__(self, seed=None):
        super().__init__(237, seed)

    def on_tick(self):
        _lava_step(self.red, self.green, self.blue, self.alpha,
                   self._draw_randoms(), _COS_SHIFT, _SIN_SHIFT)
        self.red, self.green = self.green, self.red
        _write_lava(self.red, 0, self._out)


class LavaFlowTextureFX(_FluidTextureFX):
    tile_span = 2

    def __init__(self, seed=None):
        super().__init__(238, seed)

    def on_tick(self):
        _lava_step(self.red, self.green, self.blue, self.alpha,
                   self._draw_randoms(), _SIN_SHIFT, _SIN_SHIFT)
        self.red, self.green = self.green, self.red
        _write_lava(self.red, (self.tick_counter // 3) * 16, self._out)


class FluidTextureAnimator:

    def __init__(self, seed=None):
        rng = random.Random(seed)
        seeds = [rng.randrange(2**31) for _ in range(4)]
        self.layers = [
            WaterStillTextureFX(seeds[0]),
            LavaStillTextureFX(seeds[1]),
            WaterFlowTextureFX(seeds[2]),
            LavaFlowTextureFX(seeds[3]),
        ]
        self._accum = 0.0
        self.tick_interval = 1.0 / 20.0

    def update(self, dt, terrain_texture):
        if terrain_texture is None:
            return
        self._accum += max(0.0, float(dt))
        ticks = 0
        while self._accum >= self.tick_interval and ticks < MAX_CATCHUP_TICKS:
            self._accum -= self.tick_interval
            ticks += 1
            for layer in self.layers:
                layer.on_tick()
        if ticks == 0:
            return
        if self._accum >= self.tick_interval:
            self._accum %= self.tick_interval
        for layer in self.layers:
            terrain_texture.update_tile_rgba(
                layer.icon_index,
                layer.image_data,
                tile_size=16,
                tile_span=getattr(layer, "tile_span", 1),
                tile_row_step=1,
            )


def _warm_up():
    for cls in (WaterStillTextureFX, WaterFlowTextureFX,
                LavaStillTextureFX, LavaFlowTextureFX):
        cls(seed=0).on_tick()


_warm_up()

fluid_animator = FluidTextureAnimator()
