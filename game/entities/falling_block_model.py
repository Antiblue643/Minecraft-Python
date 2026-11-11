from __future__ import annotations

import math
import random

from OpenGL.GL import *

from engine.lighting import brightness_from_level
from game.entities.entity import Entity
from game.entities.block_cube_model import BLOCK_CUBE_FACES, FALLING_BLOCK_FACE_SLOTS


class FallingBlockRendererMixin:
    def draw(self):
        if self.dead:
            return
        world = Entity.world
        x, y, z = self.pos
        cx, cy, cz = self.center

        try:
            from game.entities.entity_shadow import (
                SHADOW_SIZE_LIVING, render_entity_shadow, shadow_opacity_for_distance,
            )
            opacity = shadow_opacity_for_distance(world, x, y, z)
            if opacity > 0.0:
                render_entity_shadow(world, x, y, z, SHADOW_SIZE_LIVING, opacity=opacity)
        except Exception:
            pass

        self._draw_cube(world, cx, cy, cz)
    def _face_brightness(self, world, bx, by, bz, center):
        try:
            return max(center, brightness_from_level(world.get_light(bx, by, bz), hell=bool(getattr(world, "is_hell_world", False))))
        except Exception:
            return center
    def _draw_cube(self, world, cx, cy, cz):
        from game.entities.item import BLOCK_ATLAS_COLS, BLOCK_ATLAS_TILE, Item

        defn = Item.block_defs.get(self.block_type)
        texture = Item.block_texture
        if not defn or texture is None:
            return
        faces = defn.get("faces", 0)
        bounds = defn.get("bounds") or (0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
        height = max(0.0, min(1.0, float(bounds[4]) - float(bounds[1])))
        if defn.get("model") != "snow_layer":
            height = 1.0

        def tile(slot):
            index = faces[slot] if isinstance(faces, list) else faces
            col = index % BLOCK_ATLAS_COLS
            row = index // BLOCK_ATLAS_COLS
            return col * BLOCK_ATLAS_TILE, 1.0 - (row + 1) * BLOCK_ATLAS_TILE

        bx, by, bz = self._cell()
        try:
            own = brightness_from_level(world.get_light(bx, by, bz), hell=bool(getattr(world, "is_hell_world", False)))
        except Exception:
            own = 1.0
        fb = self._face_brightness

        quads = []
        for face_name, offset, shade, corners in BLOCK_CUBE_FACES:
            nx, ny, nz = offset
            neighbour_brightness = fb(world, bx + nx, by + ny, bz + nz, own)
            quads.append((
                shade, neighbour_brightness,
                FALLING_BLOCK_FACE_SLOTS[face_name], corners,
            ))

        renderer = Entity.renderer
        if renderer is not None:
            ox, oy, oz = renderer.camera_relative_offset(cx, cy, cz)
        else:
            ox, oy, oz = float(cx), float(cy), float(cz)

        cull_was_enabled = bool(glIsEnabled(GL_CULL_FACE))
        lighting_was_enabled = bool(glIsEnabled(GL_LIGHTING))
        alpha_was_enabled = bool(glIsEnabled(GL_ALPHA_TEST))
        texture_was_enabled = bool(glIsEnabled(GL_TEXTURE_2D))
        glPushMatrix()
        try:
            glTranslatef(ox, oy, oz)
            texture.bind()
            glEnable(GL_TEXTURE_2D)
            glDisable(GL_LIGHTING)
            glDisable(GL_CULL_FACE)
            glBegin(GL_QUADS)
            for shade, brightness, slot, corners in quads:
                u, v = tile(slot)
                level = shade * brightness
                glColor3f(level, level, level)
                t = BLOCK_ATLAS_TILE
                uvs = ((u, v + t), (u, v), (u + t, v), (u + t, v + t))
                is_side = slot not in (0, 1)
                for (px, py, pz), (tu, tv) in zip(corners, uvs):
                    if height < 1.0:
                        py = -0.5 + (py + 0.5) * height
                        if is_side:
                            tv = v + t * (py + 0.5)
                    glTexCoord2f(tu, tv)
                    glVertex3f(px, py, pz)
            glEnd()
        finally:
            glColor3f(1.0, 1.0, 1.0)
            texture.unbind()
            (glEnable if cull_was_enabled else glDisable)(GL_CULL_FACE)
            (glEnable if lighting_was_enabled else glDisable)(GL_LIGHTING)
            (glEnable if alpha_was_enabled else glDisable)(GL_ALPHA_TEST)
            (glEnable if texture_was_enabled else glDisable)(GL_TEXTURE_2D)
            glPopMatrix()
