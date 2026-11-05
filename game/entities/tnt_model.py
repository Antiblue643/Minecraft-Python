from __future__ import annotations

from OpenGL.GL import *
from game.entities.entity import Entity
from game.entities.block_cube_model import BLOCK_CUBE_FACES, TNT_FACE_SLOTS

FUSE_SCALE_MUL = 1.0

class PrimedTNTRendererMixin:
    def draw(self):
        from game.entities.item import BLOCK_ATLAS_COLS, BLOCK_ATLAS_TILE, Item

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

        defn = Item.block_defs.get("tnt")
        texture = Item.block_texture
        if not defn or texture is None:
            return
        faces = defn.get("faces", 0)

        def tile(slot):
            index = faces[slot] if isinstance(faces, list) else faces
            col = index % BLOCK_ATLAS_COLS
            row = index // BLOCK_ATLAS_COLS
            return col * BLOCK_ATLAS_TILE, 1.0 - (row + 1) * BLOCK_ATLAS_TILE

        remaining = self.fuse + 1.0
        scale = 1.0
        if remaining < 10.0:
            t = min(max(1.0 - remaining / 10.0, 0.0), 1.0)
            t = (t * t) * (t * t)
            scale = 1.0 + t * (0.3 * FUSE_SCALE_MUL)
        flash_alpha = (1.0 - remaining / 100.0) * 0.8
        flashing = int(self.fuse) // 5 % 2 == 0

        brightness = self.get_entity_brightness()
        quads = [
            (shade, TNT_FACE_SLOTS[face_name], corners)
            for face_name, _offset, shade, corners in BLOCK_CUBE_FACES
        ]

        renderer = Entity.renderer
        if renderer is not None:
            ox, oy, oz = renderer.camera_relative_offset(cx, cy, cz)
        else:
            ox, oy, oz = float(cx), float(cy), float(cz)

        cull_was_enabled = bool(glIsEnabled(GL_CULL_FACE))
        lighting_was_enabled = bool(glIsEnabled(GL_LIGHTING))
        alpha_was_enabled = bool(glIsEnabled(GL_ALPHA_TEST))
        texture_was_enabled = bool(glIsEnabled(GL_TEXTURE_2D))
        blend_was_enabled = bool(glIsEnabled(GL_BLEND))
        glPushMatrix()
        try:
            glTranslatef(ox, oy, oz)
            if scale != 1.0:
                glScalef(scale, scale, scale)
            glDisable(GL_LIGHTING)
            glDisable(GL_CULL_FACE)

            texture.bind()
            glEnable(GL_TEXTURE_2D)
            glBegin(GL_QUADS)
            for shade, slot, corners in quads:
                u, v = tile(slot)
                level = shade * brightness
                glColor3f(level, level, level)
                t = BLOCK_ATLAS_TILE
                uvs = ((u, v + t), (u, v), (u + t, v), (u + t, v + t))
                for (px, py, pz), (tu, tv) in zip(corners, uvs):
                    glTexCoord2f(tu, tv)
                    glVertex3f(px, py, pz)
            glEnd()
            texture.unbind()

            if flashing and flash_alpha > 0.0:
                glDisable(GL_TEXTURE_2D)
                glEnable(GL_BLEND)
                glBlendFunc(GL_SRC_ALPHA, GL_DST_ALPHA)
                glDepthFunc(GL_LEQUAL)
                glColor4f(1.0, 1.0, 1.0, flash_alpha)
                glBegin(GL_QUADS)
                for _shade, _slot, corners in quads:
                    for px, py, pz in corners:
                        glVertex3f(px, py, pz)
                glEnd()
                glDepthFunc(GL_LESS)
                glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        finally:
            glColor4f(1.0, 1.0, 1.0, 1.0)
            (glEnable if blend_was_enabled else glDisable)(GL_BLEND)
            (glEnable if cull_was_enabled else glDisable)(GL_CULL_FACE)
            (glEnable if lighting_was_enabled else glDisable)(GL_LIGHTING)
            (glEnable if alpha_was_enabled else glDisable)(GL_ALPHA_TEST)
            (glEnable if texture_was_enabled else glDisable)(GL_TEXTURE_2D)
            glPopMatrix()
