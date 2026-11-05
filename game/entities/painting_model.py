from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

from OpenGL.GL import *
from engine.textures import Texture

_ART_TEXTURE: Optional[Texture] = None
_ART_TEXTURE_PATH = "game/textures/art/kz.png"
_ATLAS = 256.0


def _art_texture() -> Optional[Texture]:
    global _ART_TEXTURE
    from game.app.texturepacks import resolve_texture_path
    if _ART_TEXTURE is None and Path(resolve_texture_path(_ART_TEXTURE_PATH)).is_file():
        _ART_TEXTURE = Texture(_ART_TEXTURE_PATH)
    return _ART_TEXTURE


class PaintingRendererMixin:
    def _brightness(self) -> float:
        world = self.world_obj
        if world is None:
            return 1.0
        from game.entities.painting import _hang_outward

        ox, oy, oz = _hang_outward(self.direction)
        lx = int(math.floor(self.tile_x + ox + 0.5))
        ly = int(math.floor(self.tile_y + 0.5))
        lz = int(math.floor(self.tile_z + oz + 0.5))
        try:
            from engine.lighting import brightness_from_level, face_shade
            level = world.get_light(lx, ly, lz)
            shade = face_shade((ox, 0.0, oz))
            return float(brightness_from_level(level, hell=bool(getattr(world, "is_hell_world", False))) * shade)
        except Exception:
            return 0.8
    def draw(self):
        if self.dead:
            return
        tex = _art_texture()
        if tex is None:
            return
        art = self.art
        cx, cy, cz = self._center
        renderer = getattr(self.world_obj, "renderer", None)
        if renderer is not None and hasattr(renderer, "camera_relative_offset"):
            dx, dy, dz = renderer.camera_relative_offset(cx, cy, cz)
        else:
            dx, dy, dz = cx, cy, cz

        br = self._brightness()
        lighting_was = bool(glIsEnabled(GL_LIGHTING))
        cull_was = bool(glIsEnabled(GL_CULL_FACE))
        glDisable(GL_LIGHTING)
        glDisable(GL_CULL_FACE)
        glEnable(GL_TEXTURE_2D)
        glEnable(GL_ALPHA_TEST)
        tex.bind()

        glPushMatrix()
        glTranslatef(dx, dy, dz)
        glRotatef(float(self.direction * 90), 0.0, 1.0, 0.0)
        glScalef(1.0 / 16.0, 1.0 / 16.0, 1.0 / 16.0)
        self._draw_mesh(art, br)
        glPopMatrix()

        tex.unbind()
        if lighting_was:
            glEnable(GL_LIGHTING)
        if cull_was:
            glEnable(GL_CULL_FACE)
    def _draw_mesh(self, art: ArtType, brightness: float) -> None:
        size_x = art.size_x
        size_y = art.size_y
        off_x = art.offset_x
        off_y = art.offset_y
        z_front = -0.5
        z_back = 0.5

        wood_u0 = 192.0 / _ATLAS
        wood_u1 = 208.0 / _ATLAS
        wood_v0 = 1.0 - 16.0 / _ATLAS
        wood_v1 = 1.0 - 0.0 / _ATLAS
        thin = 1.0 / _ATLAS

        tiles_x = size_x // 16
        tiles_y = size_y // 16
        origin_x = -size_x * 0.5
        origin_y = -size_y * 0.5

        for tx in range(tiles_x):
            for ty in range(tiles_y):
                x1 = origin_x + (tx + 1) * 16
                x0 = origin_x + tx * 16
                y1 = origin_y + (ty + 1) * 16
                y0 = origin_y + ty * 16

                u0 = (off_x + size_x - tx * 16) / _ATLAS
                u1 = (off_x + size_x - (tx + 1) * 16) / _ATLAS
                v_top = 1.0 - (off_y + size_y - (ty + 1) * 16) / _ATLAS
                v_bot = 1.0 - (off_y + size_y - ty * 16) / _ATLAS

                glColor3f(brightness, brightness, brightness)
                glBegin(GL_QUADS)

                glNormal3f(0.0, 0.0, -1.0)
                glTexCoord2f(u1, v_bot); glVertex3f(x1, y0, z_front)
                glTexCoord2f(u0, v_bot); glVertex3f(x0, y0, z_front)
                glTexCoord2f(u0, v_top); glVertex3f(x0, y1, z_front)
                glTexCoord2f(u1, v_top); glVertex3f(x1, y1, z_front)

                glNormal3f(0.0, 0.0, 1.0)
                glTexCoord2f(wood_u0, wood_v0); glVertex3f(x0, y0, z_back)
                glTexCoord2f(wood_u1, wood_v0); glVertex3f(x1, y0, z_back)
                glTexCoord2f(wood_u1, wood_v1); glVertex3f(x1, y1, z_back)
                glTexCoord2f(wood_u0, wood_v1); glVertex3f(x0, y1, z_back)

                glNormal3f(0.0, -1.0, 0.0)
                glTexCoord2f(wood_u0, wood_v0);       glVertex3f(x0, y0, z_front)
                glTexCoord2f(wood_u1, wood_v0);       glVertex3f(x1, y0, z_front)
                glTexCoord2f(wood_u1, wood_v0 + thin); glVertex3f(x1, y0, z_back)
                glTexCoord2f(wood_u0, wood_v0 + thin); glVertex3f(x0, y0, z_back)

                glNormal3f(0.0, 1.0, 0.0)
                glTexCoord2f(wood_u0, wood_v0);       glVertex3f(x0, y1, z_back)
                glTexCoord2f(wood_u1, wood_v0);       glVertex3f(x1, y1, z_back)
                glTexCoord2f(wood_u1, wood_v0 + thin); glVertex3f(x1, y1, z_front)
                glTexCoord2f(wood_u0, wood_v0 + thin); glVertex3f(x0, y1, z_front)

                glNormal3f(-1.0, 0.0, 0.0)
                glTexCoord2f(wood_u0, wood_v0);       glVertex3f(x0, y0, z_back)
                glTexCoord2f(wood_u0, wood_v1);       glVertex3f(x0, y1, z_back)
                glTexCoord2f(wood_u0 + thin, wood_v1); glVertex3f(x0, y1, z_front)
                glTexCoord2f(wood_u0 + thin, wood_v0); glVertex3f(x0, y0, z_front)

                glNormal3f(1.0, 0.0, 0.0)
                glTexCoord2f(wood_u0, wood_v0);       glVertex3f(x1, y0, z_front)
                glTexCoord2f(wood_u0, wood_v1);       glVertex3f(x1, y1, z_front)
                glTexCoord2f(wood_u0 + thin, wood_v1); glVertex3f(x1, y1, z_back)
                glTexCoord2f(wood_u0 + thin, wood_v0); glVertex3f(x1, y0, z_back)

                glEnd()
