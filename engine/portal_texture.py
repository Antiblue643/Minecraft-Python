
from __future__ import annotations

import math
import random


PORTAL_TEXTURE_INDEX = 14


class PortalTextureFX:

    def __init__(self):
        rng = random.Random(100)
        self.frames = []
        for frame in range(32):
            image = bytearray(16 * 16 * 4)
            for y in range(16):
                for x in range(16):
                    value = 0.0
                    for layer in range(2):
                        center = layer * 8.0
                        dx = (x - center) / 16.0 * 2.0
                        dy = (y - center) / 16.0 * 2.0
                        if dx < -1.0:
                            dx += 2.0
                        if dx >= 1.0:
                            dx -= 2.0
                        if dy < -1.0:
                            dy += 2.0
                        if dy >= 1.0:
                            dy -= 2.0
                        radius_sq = dx * dx + dy * dy
                        angle = math.atan2(dy, dx) + (
                            frame / 32.0 * math.tau - radius_sq * 10.0 + layer * 2
                        ) * (layer * 2 - 1)
                        wave = (math.sin(angle) + 1.0) / 2.0
                        value += wave / (radius_sq + 1.0) * 0.5
                    value += rng.random() * 0.1
                    red = int(value * value * 200.0 + 55.0) & 255
                    green = int(value ** 4 * 255.0) & 255
                    blue = int(value * 100.0 + 155.0) & 255
                    alpha = int(value * 100.0 + 155.0) & 255
                    offset = (y * 16 + x) * 4
                    image[offset:offset + 4] = bytes((red, green, blue, alpha))
            self.frames.append(bytes(image))


class PortalTextureAnimator:

    def __init__(self):
        self.fx = PortalTextureFX()
        self.frame = 0
        self._accum = 0.0
        self.tick_interval = 1.0 / 20.0

    def update(self, dt, terrain_texture, *, anaglyph=False):
        if terrain_texture is None:
            return
        self._accum += max(0.0, float(dt))
        while self._accum >= self.tick_interval:
            self._accum -= self.tick_interval
            self.frame = (self.frame + 1) & 31
            pixels = self.fx.frames[self.frame]
            if anaglyph:
                pixels = _anaglyph_pixels(pixels)
            terrain_texture.update_tile_rgba(
                PORTAL_TEXTURE_INDEX, pixels, tile_size=16
            )


def _anaglyph_pixels(pixels):
    out = bytearray(pixels)
    for offset in range(0, len(out), 4):
        red, green, blue = out[offset:offset + 3]
        out[offset] = (red * 30 + green * 59 + blue * 11) // 100
        out[offset + 1] = (red * 30 + green * 70) // 100
        out[offset + 2] = (red * 30 + blue * 70) // 100
    return bytes(out)


portal_animator = PortalTextureAnimator()
