
from __future__ import annotations

import math
from pathlib import Path


ICON_INDEX = 70
_LEGACY_ITEMS_PATH = "game/textures/gui/items.png"
_DIAL_PATH = "game/textures/misc/dial.png"


def _resolve_path(legacy_path: str) -> Path:
    from game.app.texturepacks import resolve_texture_path

    return Path(resolve_texture_path(legacy_path))


def _load_tile_pixels(path: Path, icon_index: int | None = None) -> list[int]:
    if not path.is_file():
        return [0] * 256
    try:
        import pygame as pg
    except ImportError:
        return [0] * 256

    surface = pg.image.load(str(path))
    width, height = surface.get_size()
    if icon_index is not None:
        if width < 256 or height < 256:
            return [0] * 256
        col = icon_index % 16
        row = icon_index // 16
        ox, oy = col * 16, row * 16
    else:
        if width < 16 or height < 16:
            return [0] * 256
        ox, oy = 0, 0

    pixels = []
    for y in range(16):
        for x in range(16):
            r, g, b, a = surface.get_at((ox + x, oy + y))
            pixels.append((a << 24) | (r << 16) | (g << 8) | b)
    return pixels


def _load_clock_base_pixels(path=None) -> list[int]:
    atlas = Path(path) if path is not None else _resolve_path(_LEGACY_ITEMS_PATH)
    return _load_tile_pixels(atlas, ICON_INDEX)


def _load_dial_pixels(path=None) -> list[int]:
    dial = Path(path) if path is not None else _resolve_path(_DIAL_PATH)
    return _load_tile_pixels(dial, None)


class ClockTextureFX:

    def __init__(self, base_pixels=None, dial_pixels=None):
        self.icon_index = ICON_INDEX
        self._base_pixels_injected = base_pixels is not None
        self._dial_pixels_injected = dial_pixels is not None
        self.clock_icon_image_data = list(
            base_pixels if base_pixels is not None else _load_clock_base_pixels()
        )
        self.dial_image_data = list(
            dial_pixels if dial_pixels is not None else _load_dial_pixels()
        )
        from game.app.texturepacks import get_active_texture_pack
        self._base_pack = get_active_texture_pack()
        self.image_data = bytearray(16 * 16 * 4)
        self.current_angle = 0.0
        self.angle_delta = 0.0
        self.world = None
        self.anaglyph_enabled = False

    def refresh_base_texture(self):
        if self._base_pixels_injected and self._dial_pixels_injected:
            return
        from game.app.texturepacks import get_active_texture_pack

        active_pack = get_active_texture_pack()
        if active_pack == self._base_pack:
            return
        if not self._base_pixels_injected:
            self.clock_icon_image_data = _load_clock_base_pixels()
        if not self._dial_pixels_injected:
            self.dial_image_data = _load_dial_pixels()
        self._base_pack = active_pack

    def on_tick(self):
        base = self.clock_icon_image_data
        dial = self.dial_image_data
        out = self.image_data
        anaglyph = self.anaglyph_enabled

        target = 0.0
        world = self.world
        if world is not None:
            try:
                celestial = float(world.get_celestial_angle(1.0))
                target = -celestial * math.pi * 2.0
                provider = getattr(world, "world_provider", None) or getattr(world, "dimension", None)
                if provider is not None and (
                    getattr(provider, "is_hell_world", False)
                    or getattr(provider, "has_no_sky", False)
                    or str(getattr(provider, "name", "")).lower() in ("hell", "nether")
                ):
                    import random
                    target = random.random() * math.pi * 2.0
            except Exception:
                target = 0.0

        delta = target - self.current_angle
        while delta < -math.pi:
            delta += math.pi * 2.0
        while delta >= math.pi:
            delta -= math.pi * 2.0
        if delta < -1.0:
            delta = -1.0
        if delta > 1.0:
            delta = 1.0
        self.angle_delta += delta * 0.1
        self.angle_delta *= 0.8
        self.current_angle += self.angle_delta

        sin_a = math.sin(self.current_angle)
        cos_a = math.cos(self.current_angle)

        for i in range(256):
            argb = base[i]
            a = (argb >> 24) & 255
            r = (argb >> 16) & 255
            g = (argb >> 8) & 255
            b = argb & 255

            if r == b and g == 0 and b > 0:
                u = -((i % 16) / 15.0 - 0.5)
                v = (i // 16) / 15.0 - 0.5
                su = int((u * cos_a + v * sin_a + 0.5) * 16.0)
                sv = int((v * cos_a - u * sin_a + 0.5) * 16.0)
                di = (su & 15) + (sv & 15) * 16
                dargb = dial[di]
                a = (dargb >> 24) & 255
                intensity = r
                r = ((dargb >> 16) & 255) * intensity // 255
                g = ((dargb >> 8) & 255) * intensity // 255
                b = (dargb & 255) * intensity // 255

            if anaglyph:
                grey = (r * 30 + g * 59 + b * 11) // 100
                g2 = (r * 30 + g * 70) // 100
                b2 = (r * 30 + b * 70) // 100
                r, g, b = grey, g2, b2

            out[i * 4 + 0] = r
            out[i * 4 + 1] = g
            out[i * 4 + 2] = b
            out[i * 4 + 3] = a


class ClockTextureAnimator:

    def __init__(self, base_pixels=None, dial_pixels=None):
        self.fx = ClockTextureFX(base_pixels=base_pixels, dial_pixels=dial_pixels)
        self._accum = 0.0
        self.tick_interval = 1.0 / 20.0

    def update(self, dt, item_texture, *, world=None, anaglyph=False):
        if item_texture is None:
            return
        self.fx.refresh_base_texture()
        self.fx.world = world
        self.fx.anaglyph_enabled = bool(anaglyph)
        self._accum += max(0.0, float(dt))
        while self._accum >= self.tick_interval:
            self._accum -= self.tick_interval
            self.fx.on_tick()
            item_texture.update_tile_rgba(
                self.fx.icon_index, self.fx.image_data, tile_size=16
            )


clock_animator = ClockTextureAnimator()
