from __future__ import annotations

import math
import random

from OpenGL.GL import *

from engine.textures import Texture
from game.entities.entity import Entity

_ARROW_TEXTURE = None
_ARROW_TEXTURE_PATH = "game/textures/item/arrows.png"
_ARROW_TEXTURE_SIZE = 32.0


def _arrow_texture_v(image_y):
    return 1.0 - float(image_y) / _ARROW_TEXTURE_SIZE


def _wrap_degrees(degrees):
    while degrees < -180.0:
        degrees += 360.0
    while degrees >= 180.0:
        degrees -= 360.0
    return degrees


class ArrowRendererMixin:
    def draw(self):
        if self.dead:
            return
        try:
            from OpenGL.GL import (
                GL_ALPHA_TEST, GL_CLAMP_TO_EDGE, GL_CULL_FACE, GL_LIGHTING, GL_QUADS,
                GL_RESCALE_NORMAL, GL_TEXTURE_2D, glBegin, glColor3f,
                glDisable, glEnable, glEnd, glPopMatrix, glPushMatrix,
                glRotatef, glScalef, glTexCoord2f, glTranslatef, glVertex3f,
            )
            from engine.textures import Texture
        except Exception:
            return

        global _ARROW_TEXTURE
        if _ARROW_TEXTURE is None:
            _ARROW_TEXTURE = Texture(_ARROW_TEXTURE_PATH, repeat_mode=GL_CLAMP_TO_EDGE)
        if _ARROW_TEXTURE is None or _ARROW_TEXTURE.texture_id is None:
            return

        world = self.world_obj
        partial = 0.0
        try:
            partial = float(world.partial_ticks())
        except Exception:
            pass
        partial = max(0.0, min(1.0, partial))
        x = self.prev_pos[0] + (self.pos[0] - self.prev_pos[0]) * partial
        y = self.prev_pos[1] + (self.pos[1] - self.prev_pos[1]) * partial
        z = self.prev_pos[2] + (self.pos[2] - self.prev_pos[2]) * partial
        yaw = self.prev_yaw + _wrap_degrees(self.yaw - self.prev_yaw) * partial
        pitch = self.prev_pitch + _wrap_degrees(self.pitch - self.prev_pitch) * partial

        renderer = getattr(world, "renderer", None) or Entity.renderer
        if renderer is not None and hasattr(renderer, "camera_relative_offset"):
            ox, oy, oz = renderer.camera_relative_offset(x, y, z)
        else:
            ox, oy, oz = x, y, z

        alpha_was_enabled = False
        try:
            from OpenGL.GL import glIsEnabled
            alpha_was_enabled = bool(glIsEnabled(GL_ALPHA_TEST))
        except Exception:
            pass
        cull_was_enabled = False
        try:
            cull_was_enabled = bool(glIsEnabled(GL_CULL_FACE))
        except Exception:
            pass
        lighting_was_enabled = False
        try:
            lighting_was_enabled = bool(glIsEnabled(GL_LIGHTING))
        except Exception:
            pass
        texture_was_enabled = False
        try:
            texture_was_enabled = bool(glIsEnabled(GL_TEXTURE_2D))
        except Exception:
            pass

        brightness = self.get_entity_brightness()
        try:
            glPushMatrix()
            glTranslatef(float(ox), float(oy), float(oz))
            glRotatef(float(yaw - 90.0), 0.0, 1.0, 0.0)
            glRotatef(float(pitch), 0.0, 0.0, 1.0)
            glEnable(GL_RESCALE_NORMAL)
            shake = self.arrow_shake - partial
            if shake > 0.0:
                glRotatef(-math.sin(shake * 3.0) * shake, 0.0, 0.0, 1.0)
            glRotatef(45.0, 1.0, 0.0, 0.0)
            glScalef(0.05625, 0.05625, 0.05625)
            glTranslatef(-4.0, 0.0, 0.0)
            glEnable(GL_TEXTURE_2D)
            glDisable(GL_LIGHTING)
            glDisable(GL_CULL_FACE)
            _ARROW_TEXTURE.bind()
            glColor3f(brightness, brightness, brightness)
            v0 = _arrow_texture_v(0)
            v5 = _arrow_texture_v(5)
            v10 = _arrow_texture_v(10)
            glBegin(GL_QUADS)
            glTexCoord2f(0.0, v5); glVertex3f(-7, -2, -2)
            glTexCoord2f(5/32, v5); glVertex3f(-7, -2, 2)
            glTexCoord2f(5/32, v10); glVertex3f(-7, 2, 2)
            glTexCoord2f(0.0, v10); glVertex3f(-7, 2, -2)
            glTexCoord2f(0.0, v5); glVertex3f(-7, 2, -2)
            glTexCoord2f(5/32, v5); glVertex3f(-7, 2, 2)
            glTexCoord2f(5/32, v10); glVertex3f(-7, -2, 2)
            glTexCoord2f(0.0, v10); glVertex3f(-7, -2, -2)
            glEnd()
            for _ in range(4):
                glRotatef(90.0, 1.0, 0.0, 0.0)
                glBegin(GL_QUADS)
                glTexCoord2f(0.0, v0); glVertex3f(-8, -2, 0)
                glTexCoord2f(0.5, v0); glVertex3f(8, -2, 0)
                glTexCoord2f(0.5, v5); glVertex3f(8, 2, 0)
                glTexCoord2f(0.0, v5); glVertex3f(-8, 2, 0)
                glEnd()
        finally:
            try:
                _ARROW_TEXTURE.unbind()
            except Exception:
                pass
            glColor3f(1.0, 1.0, 1.0)
            try:
                glDisable(GL_RESCALE_NORMAL)
                (glEnable if alpha_was_enabled else glDisable)(GL_ALPHA_TEST)
                (glEnable if cull_was_enabled else glDisable)(GL_CULL_FACE)
                (glEnable if lighting_was_enabled else glDisable)(GL_LIGHTING)
                (glEnable if texture_was_enabled else glDisable)(GL_TEXTURE_2D)
            finally:
                glPopMatrix()
        if self.fire > 0.0:
            try:
                from game.entities.item import ItemEntity
                ItemEntity._draw_fire_billboard(self, x, y, z)
            except Exception:
                pass
