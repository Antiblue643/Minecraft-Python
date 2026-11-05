from __future__ import annotations

import math
from typing import Optional

from OpenGL.GL import *
from engine.textures import Texture
from game.block import Block

_PARTICLES_PATH = "game/textures/particles.png"

class ParticleRendererMixin:
    def render(self, partial, cam, right, up, brightness=1.0):
        x, y, z = self._center(partial, cam)
        s = 0.1 * self.scale
        u0 = (self.tex_index % 16) / 16.0
        u1 = u0 + 0.999 / 16.0
        v0 = (self.tex_index // 16) / 16.0
        v1 = v0 + 0.999 / 16.0
        tv0, tv1 = 1.0 - v1, 1.0 - v0
        rx, ry, rz = right
        ux, uy, uz = up
        glColor4f(self.red * brightness, self.green * brightness,
                  self.blue * brightness, 1.0)
        glTexCoord2f(u0, tv0)
        glVertex3f(x - rx * s - ux * s, y - ry * s - uy * s, z - rz * s - uz * s)
        glTexCoord2f(u0, tv1)
        glVertex3f(x - rx * s + ux * s, y - ry * s + uy * s, z - rz * s + uz * s)
        glTexCoord2f(u1, tv1)
        glVertex3f(x + rx * s + ux * s, y + ry * s + uy * s, z + rz * s + uz * s)
        glTexCoord2f(u1, tv0)
        glVertex3f(x + rx * s - ux * s, y + ry * s - uy * s, z + rz * s - uz * s)


class DiggingParticleRendererMixin:
    def render(self, partial, cam, right, up, brightness=1.0):
        x, y, z = self._center(partial, cam)
        s = 0.1 * self.scale
        u0 = ((self.tex_index % 16) + self.jitter_x / 4.0) / 16.0
        u1 = u0 + 0.999 / 64.0
        v0 = ((self.tex_index // 16) + self.jitter_y / 4.0) / 16.0
        v1 = v0 + 0.999 / 64.0
        tv0, tv1 = 1.0 - v1, 1.0 - v0
        rx, ry, rz = right
        ux, uy, uz = up
        glColor4f(self.red * brightness, self.green * brightness,
                  self.blue * brightness, 1.0)
        glTexCoord2f(u0, tv0)
        glVertex3f(x - rx * s - ux * s, y - ry * s - uy * s, z - rz * s - uz * s)
        glTexCoord2f(u0, tv1)
        glVertex3f(x - rx * s + ux * s, y - ry * s + uy * s, z - rz * s + uz * s)
        glTexCoord2f(u1, tv1)
        glVertex3f(x + rx * s + ux * s, y + ry * s + uy * s, z + rz * s + uz * s)
        glTexCoord2f(u1, tv0)
        glVertex3f(x + rx * s - ux * s, y + ry * s - uy * s, z + rz * s - uz * s)


class EffectRendererMixin:
    def _ensure_textures(self):
        if self._particles_tex is None:
            self._particles_tex = Texture(_PARTICLES_PATH)
        if self._terrain_tex is None:
            self._terrain_tex = Block._get_texture()
        if self._items_tex is None:
            from game.entities.item import Item
            self._items_tex = Item.item_texture or Texture("game/textures/gui/items.png")

    def render(
        self, camera_pos, yaw_deg, pitch_deg, partial: float = 0.0, world=None,
        fluid_mode: Optional[str] = None,
    ):
        from game.entities.particles import particle_is_inside_fluid

        if fluid_mode not in (None, "inside", "outside"):
            raise ValueError("fluid_mode must be None, 'inside', or 'outside'")
        if not any(self.layers[i] for i in (0, 1, 2)):
            return
        self._ensure_textures()
        yaw = math.radians(yaw_deg)
        pitch = math.radians(pitch_deg)
        cos_y, sin_y = math.cos(yaw), math.sin(yaw)
        cos_p, sin_p = math.cos(pitch), math.sin(pitch)
        right = (cos_y, 0.0, -sin_y)
        look = (-sin_y * cos_p, sin_p, -cos_y * cos_p)
        up = (
            right[1] * look[2] - right[2] * look[1],
            right[2] * look[0] - right[0] * look[2],
            right[0] * look[1] - right[1] * look[0],
        )
        glDisableClientState(GL_VERTEX_ARRAY)
        glDisableClientState(GL_TEXTURE_COORD_ARRAY)
        glDisableClientState(GL_COLOR_ARRAY)

        fog_was = bool(glIsEnabled(GL_FOG))
        cull_was = bool(glIsEnabled(GL_CULL_FACE))
        depth_was = bool(glIsEnabled(GL_DEPTH_TEST))
        blend_was = bool(glIsEnabled(GL_BLEND))
        alpha_was = bool(glIsEnabled(GL_ALPHA_TEST))
        texture_2d_was = bool(glIsEnabled(GL_TEXTURE_2D))
        lighting_was = bool(glIsEnabled(GL_LIGHTING))
        from OpenGL.GL import (
            glGetBooleanv, GL_DEPTH_WRITEMASK, glGetTexEnviv,
            GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE, glTexEnvi,
        )
        depth_mask_was = bool(glGetBooleanv(GL_DEPTH_WRITEMASK))
        texture_env_mode_was = int(glGetTexEnviv(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE))
        glDisable(GL_FOG)
        glDisable(GL_CULL_FACE)
        glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
        glDisable(GL_LIGHTING)
        glEnable(GL_DEPTH_TEST)
        glDepthMask(False)
        glEnable(GL_TEXTURE_2D)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glDisable(GL_ALPHA_TEST)

        light_cache = {}
        for layer_idx in (0, 1, 2):
            particles = self.layers[layer_idx]
            if fluid_mode is not None:
                want_inside = fluid_mode == "inside"
                particles = [
                    particle for particle in particles
                    if particle_is_inside_fluid(particle, world, partial) == want_inside
                ]
            if not particles:
                continue
            tex = (self._particles_tex, self._terrain_tex, self._items_tex)[layer_idx]
            if tex is None:
                continue
            glEnable(GL_TEXTURE_2D)
            tex.bind()
            glBegin(GL_QUADS)
            for p in particles:
                p.render(partial, camera_pos, right, up,
                         p.get_brightness(world, light_cache))
            glEnd()
            tex.unbind()

        glDepthMask(depth_mask_was)
        if depth_was:
            glEnable(GL_DEPTH_TEST)
        else:
            glDisable(GL_DEPTH_TEST)
        if fog_was:
            glEnable(GL_FOG)
        else:
            glDisable(GL_FOG)
        if cull_was:
            glEnable(GL_CULL_FACE)
        else:
            glDisable(GL_CULL_FACE)
        if blend_was:
            glEnable(GL_BLEND)
        else:
            glDisable(GL_BLEND)
        if alpha_was:
            glEnable(GL_ALPHA_TEST)
        else:
            glDisable(GL_ALPHA_TEST)
        glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, texture_env_mode_was)
        if texture_2d_was:
            glEnable(GL_TEXTURE_2D)
        else:
            glDisable(GL_TEXTURE_2D)
        if lighting_was:
            glEnable(GL_LIGHTING)
        else:
            glDisable(GL_LIGHTING)
        glColor4f(1.0, 1.0, 1.0, 1.0)
