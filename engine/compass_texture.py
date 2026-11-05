
from __future__ import annotations

import math
from pathlib import Path


ICON_INDEX = 54
_LEGACY_ITEMS_PATH = "game/textures/gui/items.png"


def _resolve_items_atlas_path():
    from game.app.texturepacks import resolve_texture_path

    return Path(resolve_texture_path(_LEGACY_ITEMS_PATH))


def _load_compass_base_pixels(path=None):
    atlas_path = Path(path) if path is not None else _resolve_items_atlas_path()
    if not atlas_path.is_file():
        return [0] * 256

    try:
        import pygame as pg
    except ImportError:
        return [0] * 256

    surface = pg.image.load(str(atlas_path))
    width, height = surface.get_size()
    if width < 256 or height < 256:
        return [0] * 256
    col = ICON_INDEX % 16
    row = ICON_INDEX // 16
    pixels = []
    for y in range(16):
        for x in range(16):
            r, g, b, a = surface.get_at((col * 16 + x, row * 16 + y))
            pixels.append((a << 24) | (r << 16) | (g << 8) | b)
    return pixels


class CompassTextureFX:

    def __init__(self, base_pixels=None):
        self.icon_index = ICON_INDEX
        self._base_pixels_injected = base_pixels is not None
        self.compass_icon_image_data = list(
            base_pixels if base_pixels is not None else _load_compass_base_pixels()
        )
        from game.app.texturepacks import get_active_texture_pack
        self._base_pack = get_active_texture_pack()
        self.image_data = bytearray(16 * 16 * 4)
        self.current_angle = 0.0
        self.angle_delta = 0.0
        self.player = None
        self.world = None
        self.anaglyph_enabled = False

    def refresh_base_texture(self):
        if self._base_pixels_injected:
            return
        from game.app.texturepacks import get_active_texture_pack

        active_pack = get_active_texture_pack()
        if active_pack == self._base_pack:
            return
        self.compass_icon_image_data = _load_compass_base_pixels()
        self._base_pack = active_pack

    def on_tick(self):
        data = self.compass_icon_image_data
        out = self.image_data
        anaglyph = self.anaglyph_enabled

        for i in range(256):
            argb = data[i]
            a = (argb >> 24) & 255
            r = (argb >> 16) & 255
            g = (argb >> 8) & 255
            b = argb & 255
            if anaglyph:
                grey = (r * 30 + g * 59 + b * 11) // 100
                g2 = (r * 30 + g * 70) // 100
                b2 = (r * 30 + b * 70) // 100
                r, g, b = grey, g2, b2
            out[i * 4 + 0] = r
            out[i * 4 + 1] = g
            out[i * 4 + 2] = b
            out[i * 4 + 3] = a

        target_angle = 0.0
        player = self.player
        world = self.world
        if world is not None and player is not None:
            spawn_x = float(getattr(world, "spawn_x", getattr(world, "spawnX", 0.0)) or 0.0)
            spawn_z = float(getattr(world, "spawn_z", getattr(world, "spawnZ", 0.0)) or 0.0)
            pos = getattr(player, "pos", None)
            if pos is not None and len(pos) >= 3:
                dx = spawn_x - float(pos[0])
                dz = spawn_z - float(pos[2])
                yaw = float(getattr(player, "yaw", getattr(player, "rotation_yaw", 0.0)) or 0.0)
                target_angle = (90.0 - yaw) * math.pi / 180.0 - math.atan2(dz, dx)

        delta = target_angle - self.current_angle
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

        for n in range(-4, 5):
            x = int(8.5 + cos_a * n * 0.3)
            y = int(7.5 - sin_a * n * 0.3 * 0.5)
            if 0 <= x < 16 and 0 <= y < 16:
                idx = y * 16 + x
                r = g = b = 100
                a = 255
                if anaglyph:
                    grey = (r * 30 + g * 59 + b * 11) // 100
                    g2 = (r * 30 + g * 70) // 100
                    b2 = (r * 30 + b * 70) // 100
                    r, g, b = grey, g2, b2
                out[idx * 4 + 0] = r
                out[idx * 4 + 1] = g
                out[idx * 4 + 2] = b
                out[idx * 4 + 3] = a

        for n in range(-8, 17):
            x = int(8.5 + sin_a * n * 0.3)
            y = int(7.5 + cos_a * n * 0.3 * 0.5)
            if 0 <= x < 16 and 0 <= y < 16:
                idx = y * 16 + x
                if n >= 0:
                    r, g, b = 255, 20, 20
                else:
                    r = g = b = 100
                a = 255
                if anaglyph:
                    grey = (r * 30 + g * 59 + b * 11) // 100
                    g2 = (r * 30 + g * 70) // 100
                    b2 = (r * 30 + b * 70) // 100
                    r, g, b = grey, g2, b2
                out[idx * 4 + 0] = r
                out[idx * 4 + 1] = g
                out[idx * 4 + 2] = b
                out[idx * 4 + 3] = a


class CompassTextureAnimator:

    def __init__(self, base_pixels=None):
        self.fx = CompassTextureFX(base_pixels=base_pixels)
        self._accum = 0.0
        self.tick_interval = 1.0 / 20.0

    def update(self, dt, item_texture, *, player=None, world=None, anaglyph=False):
        if item_texture is None:
            return
        self.fx.refresh_base_texture()
        self.fx.player = player
        self.fx.world = world
        self.fx.anaglyph_enabled = bool(anaglyph)
        self._accum += max(0.0, float(dt))
        while self._accum >= self.tick_interval:
            self._accum -= self.tick_interval
            self.fx.on_tick()
            item_texture.update_tile_rgba(
                self.fx.icon_index, self.fx.image_data, tile_size=16
            )


compass_animator = CompassTextureAnimator()
