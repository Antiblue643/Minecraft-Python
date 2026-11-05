from __future__ import annotations

import math

from OpenGL.GL import (
    GL_ALPHA_TEST, GL_BLEND, GL_COMPILE, GL_CULL_FACE, GL_DEPTH_FUNC, GL_GREATER,
    GL_LEQUAL, GL_MODELVIEW, GL_ONE_MINUS_SRC_ALPHA, GL_QUADS, GL_SRC_ALPHA,
    GL_TEXTURE, GL_TEXTURE_2D,
    glAlphaFunc, glBegin, glBindTexture, glBlendFunc, glCallList, glColor4f,
    glColorMask, glDepthFunc, glDepthMask, glDisable, glEnable, glEnd, glEndList,
    glGenLists, glGetIntegerv, glMatrixMode, glNewList, glPopMatrix,
    glPushMatrix, glScalef, glTexCoord2f, glTranslatef, glVertex3f,
)

from engine.textures import Texture
from game.block import Block

_CLOUD_PATH = "game/textures/clouds.png"
_FAST_CLOUD_HEIGHT = 120.0
_FANCY_CLOUD_HEIGHT = 108.0
_CLOUD_Y_OFFSET = 0.33


class CloudRenderer:
    def __init__(self):
        self._tex = None
        self.cloud_tick = 0
        self._clock_initialized = False
        self._fancy_lists = None
        self._fast_list = None

    def _ensure_texture(self):
        if self._tex is None:
            self._tex = Texture(_CLOUD_PATH)
        return self._tex

    def update(self):
        self.cloud_tick += 1

    def render(self, world, player, partial_ticks=0.0):
        if world is None or player is None:
            return
        pending = int(getattr(world, "_cloud_tick_pending", 0) or 0)
        if not self._clock_initialized:
            self.cloud_tick = max(0, int(getattr(world, "world_time", 0)) - pending)
            self._clock_initialized = True
        if pending:
            self.cloud_tick += pending
            world._cloud_tick_pending = 0
        if getattr(Block, "_fancy_graphics", True):
            self._render_fancy(world, player, partial_ticks)
        else:
            self._render_fast(world, player, partial_ticks)

    def _player_pose(self, player, partial):
        px = float(player.pos[0])
        py = float(player.pos[1])
        pz = float(player.pos[2])
        prev = getattr(player, "prev_pos", None)
        if prev is not None and len(prev) >= 3:
            px = float(prev[0]) + (px - float(prev[0])) * partial
            py = float(prev[1]) + (py - float(prev[1])) * partial
            pz = float(prev[2]) + (pz - float(prev[2])) * partial
        eye = float(getattr(player, "EYE_HEIGHT", getattr(player, "eye_height", 1.62)))
        return px, py, pz, eye

    def _begin_draw(self, tex):
        self._prev_depth_func = int(glGetIntegerv(GL_DEPTH_FUNC))
        glDepthFunc(GL_LEQUAL)
        glDisable(GL_CULL_FACE)
        glEnable(GL_ALPHA_TEST)
        glAlphaFunc(GL_GREATER, 0.1)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, tex.texture_id)

    def _end_draw(self):
        glBindTexture(GL_TEXTURE_2D, 0)
        glColor4f(1.0, 1.0, 1.0, 1.0)
        glDepthMask(True)
        glColorMask(True, True, True, True)
        glDepthFunc(getattr(self, "_prev_depth_func", GL_LEQUAL))
        glDisable(GL_BLEND)
        glDisable(GL_ALPHA_TEST)
        glEnable(GL_CULL_FACE)

    def _build_fast_list(self):
        tile = 32
        extent = 256 // tile
        uv_scale = 0.5 / 1024.0
        span = tile * extent
        lst = glGenLists(1)
        glNewList(lst, GL_COMPILE)
        glBegin(GL_QUADS)
        for x0 in range(-span, span, tile):
            for z0 in range(-span, span, tile):
                x1, z1 = x0 + tile, z0 + tile
                glTexCoord2f(x0 * uv_scale, z1 * uv_scale)
                glVertex3f(float(x0), 0.0, float(z1))
                glTexCoord2f(x1 * uv_scale, z1 * uv_scale)
                glVertex3f(float(x1), 0.0, float(z1))
                glTexCoord2f(x1 * uv_scale, z0 * uv_scale)
                glVertex3f(float(x1), 0.0, float(z0))
                glTexCoord2f(x0 * uv_scale, z0 * uv_scale)
                glVertex3f(float(x0), 0.0, float(z0))
        glEnd()
        glEndList()
        return lst

    def _build_fast_list(self):
        tile = 32
        extent = 256 // tile
        uv_scale = 0.5 / 1024.0
        span = tile * extent
        lst = glGenLists(1)
        glNewList(lst, GL_COMPILE)
        glBegin(GL_QUADS)
        for x0 in range(-span, span, tile):
            for z0 in range(-span, span, tile):
                x1, z1 = x0 + tile, z0 + tile
                glTexCoord2f(x0 * uv_scale, z1 * uv_scale)
                glVertex3f(float(x0), 0.0, float(z1))
                glTexCoord2f(x1 * uv_scale, z1 * uv_scale)
                glVertex3f(float(x1), 0.0, float(z1))
                glTexCoord2f(x1 * uv_scale, z0 * uv_scale)
                glVertex3f(float(x1), 0.0, float(z0))
                glTexCoord2f(x0 * uv_scale, z0 * uv_scale)
                glVertex3f(float(x0), 0.0, float(z0))
        glEnd()
        glEndList()
        return lst

    def _render_fast(self, world, player, partial):
        tex = self._ensure_texture()
        if tex is None or tex.texture_id is None:
            return

        px, py, pz, eye = self._player_pose(player, partial)
        r, g, b = world.get_cloud_color(partial)

        uv_scale = 0.5 / 1024.0
        drift = (float(self.cloud_tick) + partial) * 0.03
        cx = px + drift
        cz = pz
        cx -= math.floor(cx / 2048.0) * 2048.0
        cz -= math.floor(cz / 2048.0) * 2048.0
        u_off = cx * uv_scale
        v_off = cz * uv_scale
        cloud_y = _FAST_CLOUD_HEIGHT - (py + eye) + _CLOUD_Y_OFFSET

        if self._fast_list is None:
            self._fast_list = self._build_fast_list()

        self._begin_draw(tex)
        glDepthMask(False)
        glColor4f(r, g, b, 0.8)
        glMatrixMode(GL_TEXTURE)
        glPushMatrix()
        glTranslatef(u_off, v_off, 0.0)
        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        glTranslatef(0.0, cloud_y, 0.0)
        glCallList(self._fast_list)
        glPopMatrix()
        glMatrixMode(GL_TEXTURE)
        glPopMatrix()
        glMatrixMode(GL_MODELVIEW)
        self._end_draw()

    def _build_fancy_lists(self):
        thickness = 4.0
        uv_tile = 1.0 / 256.0
        cell = 8
        ring = 3
        eps = 1.0 / 1024.0
        top = thickness - eps

        def new_list():
            lst = glGenLists(1)
            glNewList(lst, GL_COMPILE)
            glBegin(GL_QUADS)
            return lst

        def end_list():
            glEnd()
            glEndList()

        cells = [(dx, dz) for dx in range(-ring + 1, ring + 1)
                 for dz in range(-ring + 1, ring + 1)]

        bottom = new_list()
        for dx, dz in cells:
            base_u, base_v = float(dx * cell), float(dz * cell)
            x0, z0 = base_u, base_v
            x1, z1 = x0 + cell, z0 + cell
            glTexCoord2f(base_u * uv_tile, (base_v + cell) * uv_tile)
            glVertex3f(x0, 0.0, z1)
            glTexCoord2f((base_u + cell) * uv_tile, (base_v + cell) * uv_tile)
            glVertex3f(x1, 0.0, z1)
            glTexCoord2f((base_u + cell) * uv_tile, base_v * uv_tile)
            glVertex3f(x1, 0.0, z0)
            glTexCoord2f(base_u * uv_tile, base_v * uv_tile)
            glVertex3f(x0, 0.0, z0)
        end_list()

        top_list = new_list()
        for dx, dz in cells:
            base_u, base_v = float(dx * cell), float(dz * cell)
            x0, z0 = base_u, base_v
            x1, z1 = x0 + cell, z0 + cell
            glTexCoord2f(base_u * uv_tile, (base_v + cell) * uv_tile)
            glVertex3f(x0, top, z1)
            glTexCoord2f((base_u + cell) * uv_tile, (base_v + cell) * uv_tile)
            glVertex3f(x1, top, z1)
            glTexCoord2f((base_u + cell) * uv_tile, base_v * uv_tile)
            glVertex3f(x1, top, z0)
            glTexCoord2f(base_u * uv_tile, base_v * uv_tile)
            glVertex3f(x0, top, z0)
        end_list()

        x_sides = new_list()
        for dx, dz in cells:
            base_u, base_v = float(dx * cell), float(dz * cell)
            x0, z0 = base_u, base_v
            z1 = z0 + cell
            offsets = []
            if dx > -1:
                offsets.append(0.0)
            if dx <= 1:
                offsets.append(1.0 - eps)
            for extra in offsets:
                for i in range(cell):
                    xi = x0 + i + extra
                    u = (base_u + i + 0.5) * uv_tile
                    glTexCoord2f(u, (base_v + cell) * uv_tile)
                    glVertex3f(xi, 0.0, z1)
                    glTexCoord2f(u, (base_v + cell) * uv_tile)
                    glVertex3f(xi, top, z1)
                    glTexCoord2f(u, base_v * uv_tile)
                    glVertex3f(xi, top, z0)
                    glTexCoord2f(u, base_v * uv_tile)
                    glVertex3f(xi, 0.0, z0)
        end_list()

        z_sides = new_list()
        for dx, dz in cells:
            base_u, base_v = float(dx * cell), float(dz * cell)
            x0, z0 = base_u, base_v
            x1 = x0 + cell
            offsets = []
            if dz > -1:
                offsets.append(0.0)
            if dz <= 1:
                offsets.append(1.0 - eps)
            for extra in offsets:
                for i in range(cell):
                    zi = z0 + i + extra
                    v = (base_v + i + 0.5) * uv_tile
                    glTexCoord2f(base_u * uv_tile, v)
                    glVertex3f(x0, top, zi)
                    glTexCoord2f((base_u + cell) * uv_tile, v)
                    glVertex3f(x1, top, zi)
                    glTexCoord2f((base_u + cell) * uv_tile, v)
                    glVertex3f(x1, 0.0, zi)
                    glTexCoord2f(base_u * uv_tile, v)
                    glVertex3f(x0, 0.0, zi)
        end_list()

        return (bottom, top_list, x_sides, z_sides)

    def _render_fancy(self, world, player, partial):
        tex = self._ensure_texture()
        if tex is None or tex.texture_id is None:
            return

        px, py, pz, eye = self._player_pose(player, partial)
        r, g, b = world.get_cloud_color(partial)

        scale = 12.0
        thickness = 4.0
        drift = (float(self.cloud_tick) + partial) * 0.03
        cx = (px + drift) / scale
        cz = pz / scale + 0.33
        cx -= math.floor(cx / 2048.0) * 2048.0
        cz -= math.floor(cz / 2048.0) * 2048.0
        cloud_y = _FANCY_CLOUD_HEIGHT - (py + eye) + _CLOUD_Y_OFFSET

        uv_tile = 1.0 / 256.0
        u_base = math.floor(cx) * uv_tile
        v_base = math.floor(cz) * uv_tile
        u_frac = cx - math.floor(cx)
        v_frac = cz - math.floor(cz)

        if self._fancy_lists is None:
            self._fancy_lists = self._build_fancy_lists()
        bottom, top_list, x_sides, z_sides = self._fancy_lists
        draw_bottom = cloud_y > -thickness - 1.0
        draw_top = cloud_y <= thickness + 1.0

        self._begin_draw(tex)
        glMatrixMode(GL_TEXTURE)
        glPushMatrix()
        glTranslatef(u_base, v_base, 0.0)
        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        glScalef(scale, 1.0, scale)
        glTranslatef(-u_frac, cloud_y, -v_frac)

        for pass_id in range(2):
            if pass_id == 0:
                glColorMask(False, False, False, False)
            else:
                glColorMask(True, True, True, True)
            if draw_bottom:
                glColor4f(r * 0.7, g * 0.7, b * 0.7, 0.8)
                glCallList(bottom)
            if draw_top:
                glColor4f(r, g, b, 0.8)
                glCallList(top_list)
            glColor4f(r * 0.9, g * 0.9, b * 0.9, 0.8)
            glCallList(x_sides)
            glColor4f(r * 0.8, g * 0.8, b * 0.8, 0.8)
            glCallList(z_sides)

        glPopMatrix()
        glMatrixMode(GL_TEXTURE)
        glPopMatrix()
        glMatrixMode(GL_MODELVIEW)
        self._end_draw()
