
from __future__ import annotations

import random

import numpy as np
from numba import njit

MAX_CATCHUP_TICKS = 3


@njit(cache=True, nogil=True, fastmath=False)
def _fire_step(cur, last, bottom_heat, initial_strength, out):
    for x in range(16):
        for y in range(20):
            strength = initial_strength
            heat = cur[x + (y + 1) % 20 * 16] * strength
            for dx in range(x - 1, x + 2):
                for dy in range(y, y + 2):
                    if 0 <= dx < 16 and 0 <= dy < 20:
                        heat += cur[dx + dy * 16]
                    strength += 1
            last[x + y * 16] = heat / (strength * 1.06)
            if y >= 19:
                last[x + y * 16] = bottom_heat[x]

    for i in range(256):
        t = last[i] * 1.8
        if t > 1.0:
            t = 1.0
        if t < 0.0:
            t = 0.0
        r = int(t * 155.0 + 100.0)
        g = int(t * t * 255.0)
        b = int(t * t * t * t * t * t * t * t * t * t * 255.0)
        a = 255 if t >= 0.5 else 0
        out[i * 4 + 0] = r & 255
        out[i * 4 + 1] = g & 255
        out[i * 4 + 2] = b & 255
        out[i * 4 + 3] = a & 255


class FireTextureFX:

    def __init__(self, layer=0, initial_strength=16):
        self.layer = int(layer)
        self.icon_index = 31 + self.layer * 16
        self.initial_strength = int(initial_strength)
        self.current = np.zeros(320, dtype=np.float64)
        self.last = np.zeros(320, dtype=np.float64)
        self.image_data = bytearray(16 * 16 * 4)
        self._out = np.frombuffer(self.image_data, dtype=np.uint8)
        self._bottom = np.zeros(16, dtype=np.float64)

    def on_tick(self):
        rnd = random.random
        bottom = self._bottom
        for x in range(16):
            bottom[x] = rnd() * rnd() * rnd() * 4.0 + rnd() * 0.1 + 0.2
        _fire_step(self.current, self.last, bottom, self.initial_strength, self._out)
        self.current, self.last = self.last, self.current


class FireTextureAnimator:

    def __init__(self):
        self.layers = [FireTextureFX(0), FireTextureFX(1)]
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
                layer.icon_index, layer.image_data, tile_size=16
            )


def _warm_up():
    fx = FireTextureFX()
    _fire_step(fx.current, fx.last, fx._bottom, fx.initial_strength, fx._out)


_warm_up()

fire_animator = FireTextureAnimator()
