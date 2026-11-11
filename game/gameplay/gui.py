from collections import deque
from array import array
import numpy as np
from ctypes import c_void_p
import math
import random
import time

import pygame as pg

from OpenGL.GL import (
    glEnable, glDisable, glBindTexture, glBegin, glEnd,
    glTexCoord2f, glVertex2f, glColor4f, glColor3f,
    GL_TEXTURE_2D, GL_BLEND, GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE,
    GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA,
    GL_ONE_MINUS_DST_COLOR, GL_ONE_MINUS_SRC_COLOR, GL_ZERO,
    GL_CULL_FACE, GL_LIGHTING, GL_ALL_ATTRIB_BITS,
    glPushAttrib, glPopAttrib,
    glBlendFunc, glTexEnvi, GL_QUADS, GL_LINES, GL_LINE_STRIP,
    GL_ALPHA_TEST, glAlphaFunc, GL_GREATER, GL_DEPTH_TEST, GL_REPEAT,
    glPushMatrix, glPopMatrix, glTranslatef, glScalef, glDepthMask, glIsEnabled,
    GL_ARRAY_BUFFER, GL_STREAM_DRAW, GL_FLOAT, GL_VERTEX_ARRAY, GL_COLOR_ARRAY,
    glGenBuffers, glBindBuffer, glBufferData, glDrawArrays,
    glEnableClientState, glDisableClientState, glVertexPointer, glColorPointer,
)

from engine.textures import Texture
from engine.lighting import brightness_from_level
from game.block import Block
from game.entities.item import Item
from game.gameplay.screens import VERSION_TEXT
from game.gameplay.record_notification import (
    record_notification_layout, record_notification_style,
)

_HOTBAR_U, _HOTBAR_V = 0, 0
_HOTBAR_W, _HOTBAR_H = 182, 22
_SEL_U, _SEL_V = 0, 22
_SEL_W, _SEL_H = 24, 24

_GUI_SCALE = 3


class Gui:
    def __init__(self, display):
        self.display = display
        self.debug = False
        self.show_chunk_borders = True

        self._frame_times = deque(maxlen=240)
        self._frame_graph_vbo = None
        self._debug_lines_cache = None
        self._debug_cache_until = 0.0
        self._flat_icon_batch_depth = 0
        self._flat_icon_batch_states = None
        self._gui_tex = Texture("game/textures/gui/gui.png")
        self._icons_tex = Texture("game/textures/gui/icons.png")
        self._vignette_tex = Texture("game/textures/misc/vignette.png")
        self._water_overlay_tex = Texture("game/textures/water.png", repeat_mode=GL_REPEAT)

        self._atlas_w = max(self._gui_tex.width, 1)
        self._atlas_h = max(self._gui_tex.height, 1)
        self._icons_w = max(self._icons_tex.width, 1)
        self._icons_h = max(self._icons_tex.height, 1)
        self._prev_vignette_brightness = 1.0

    def toggle_debug(self):
        self.debug = not self.debug
        self._debug_lines_cache = None
        self._debug_cache_until = 0.0

    def toggle_chunk_borders(self):
        self.show_chunk_borders = not self.show_chunk_borders

    def draw_item_tooltip(self, stack, mouse_x, mouse_y):
        if stack is None:
            return
        title = Item.display_name(stack.id, getattr(stack, "damage", 0))
        if not title:
            return

        mod_name = Item.mod_name(stack.id)
        mod_line = "\u00a75" + mod_name if mod_name else None

        display = self.display
        text_width = display.string_width(title)
        if mod_line:
            text_width = max(text_width, display.string_width(mod_line))
        tx = int(mouse_x) + 12
        ty = int(mouse_y) - 12

        from OpenGL.GL import (
            GL_BLEND, GL_DEPTH_TEST, GL_LIGHTING, GL_TEXTURE_2D,
            glBegin, glEnd, glVertex2f,
        )
        capabilities = (GL_BLEND, GL_DEPTH_TEST, GL_LIGHTING, GL_TEXTURE_2D)
        previous = tuple(
            (capability, bool(glIsEnabled(capability)))
            for capability in capabilities
        )
        try:
            glDisable(GL_DEPTH_TEST)
            glDisable(GL_LIGHTING)
            glDisable(GL_TEXTURE_2D)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            x0, y0 = tx - 3, ty - 3
            x1, y1 = tx + text_width + 3, ty + 11 + (10 if mod_line else 0)
            glBegin(GL_QUADS)
            glColor4f(0.0, 0.0, 0.0, 192.0 / 255.0)
            glVertex2f(x0, y0)
            glVertex2f(x1, y0)
            glVertex2f(x1, y1)
            glVertex2f(x0, y1)
            glEnd()
            glEnable(GL_TEXTURE_2D)
            display.draw_string(
                title, (tx, ty), (255, 255, 255), 1.0, shadow=True,
            )
            if mod_line:
                display.draw_string(
                    mod_line, (tx, ty + 10), (255, 255, 255), 1.0, shadow=True,
                )
        finally:
            glColor4f(1.0, 1.0, 1.0, 1.0)
            for capability, was_enabled in previous:
                (glEnable if was_enabled else glDisable)(capability)

    def record_frame(self, dt):
        self._frame_times.append(max(dt, 0.0))

    def _tex_quad(self, x, y, w, h, u, v, uw, vh):
        aw, ah = self._atlas_w, self._atlas_h
        u0 = u / aw
        u1 = (u + uw) / aw
        v0 = 1.0 - (v + vh) / ah
        v1 = 1.0 - v / ah

        glTexCoord2f(u0, v0); glVertex2f(x, y + h)
        glTexCoord2f(u1, v0); glVertex2f(x + w, y + h)
        glTexCoord2f(u1, v1); glVertex2f(x + w, y)
        glTexCoord2f(u0, v1); glVertex2f(x, y)

    @staticmethod
    def _atlas_uv(atlas_index):
        col = atlas_index % 16
        row = atlas_index // 16
        u = col / 16.0
        v1 = 1.0 - row / 16.0
        v0 = v1 - 1.0 / 16.0
        return u, v0, u + 1.0 / 16.0, v1

    @staticmethod
    def _icon_face_uv(atlas_index, point, face):
        x, y, z = point
        u0, v0, u1, v1 = Gui._atlas_uv(atlas_index)
        if face == "+x":
            uf, vf = 1.0 - z, y
        elif face == "-x":
            uf, vf = z, y
        elif face == "+z":
            uf, vf = x, y
        elif face == "-z":
            uf, vf = 1.0 - x, y
        elif face == "+y":
            uf, vf = x, 1.0 - z
        else:
            uf, vf = x, 1.0 - z
        uf = 0.0 if uf < 0.0 else (1.0 if uf > 1.0 else uf)
        vf = 0.0 if vf < 0.0 else (1.0 if vf > 1.0 else vf)
        return u0 + (u1 - u0) * uf, v0 + (v1 - v0) * vf

    @staticmethod
    def _project_icon_point_3d(point, x, y, size):
        bx, by, bz = (float(point[0]) - 0.5,
                      float(point[1]) - 0.5,
                      float(point[2]) - 0.5)
        cos45 = math.sqrt(0.5)
        sin45 = cos45
        rx = cos45 * bx + sin45 * bz
        rz = -sin45 * bx + cos45 * bz
        ry = (-math.sqrt(3.0) / 2.0) * by + 0.5 * rz
        depth = 0.5 * by + (math.sqrt(3.0) / 2.0) * rz
        scale = float(size) / 16.0
        return (
            x + scale * (-2.0 + 10.0 * (1.0 + rx)),
            y + scale * (3.0 + 10.0 * (0.5 + ry)),
            depth,
        )

    @staticmethod
    def _project_icon_point(point, x, y, size):
        sx, sy, _depth = Gui._project_icon_point_3d(point, x, y, size)
        return sx, sy

    def _draw_block_shape_icon(self, item, x, y, size, surfaces):
        definition = Item.block_defs.get(item.id, {})
        def atlas_for(slot):
            return Item.block_face_texture(
                item.id, slot, getattr(item, "damage", 0),
            )

        tint_item_id = getattr(item, "tint_item_id", item.id)

        Item.block_texture.bind()
        glEnable(GL_TEXTURE_2D)
        glBegin(GL_QUADS)
        try:
            is_cactus = definition.get("model") == "cactus"
            cactus_low, cactus_high = 1.0 / 16.0, 15.0 / 16.0
            cactus_span = cactus_high - cactus_low
            for slot, normal, points, shade in surfaces:
                atlas_idx = atlas_for(slot)
                face_tint = Item.block_item_tint(
                    tint_item_id, getattr(item, "damage", 0), slot=slot,
                )
                tint_rgb = tuple(channel / 255.0 for channel in face_tint)
                glColor4f(
                    shade * tint_rgb[0], shade * tint_rgb[1],
                    shade * tint_rgb[2], 1.0,
                )
                for point in points:
                    uv_point = point
                    if is_cactus:
                        uv_point = (
                            (point[0] - cactus_low) / cactus_span,
                            point[1],
                            (point[2] - cactus_low) / cactus_span,
                        )
                    glTexCoord2f(*self._icon_face_uv(atlas_idx, uv_point, normal))
                    glVertex2f(*self._project_icon_point(point, x, y, size))
        finally:
            glEnd()
            Item.block_texture.unbind()

    def _draw_depth_sorted_block_surfaces(self, item, x, y, size, surfaces):
        def mean_depth(surface):
            return sum(
                self._project_icon_point_3d(point, x, y, size)[2]
                for point in surface[2]
            ) / len(surface[2])

        ordered_surfaces = sorted(surfaces, key=mean_depth)
        self._draw_block_shape_icon(item, x, y, size, ordered_surfaces)

    def _draw_isometric_block_icon(self, item, x, y, size):
        surfaces = [
            (2, "-y", ((0, 0, 1), (0, 0, 0), (1, 0, 0), (1, 0, 1)), 0.50),
            (3, "+y", ((1, 1, 1), (1, 1, 0), (0, 1, 0), (0, 1, 1)), 1.0),
            (5, "-z", ((0, 1, 0), (1, 1, 0), (1, 0, 0), (0, 0, 0)), 0.60),
            (4, "+z", ((0, 1, 1), (0, 0, 1), (1, 0, 1), (1, 1, 1)), 0.60),
            (1, "-x", ((0, 1, 1), (0, 1, 0), (0, 0, 0), (0, 0, 1)), 0.80),
            (0, "+x", ((1, 0, 1), (1, 0, 0), (1, 1, 0), (1, 1, 1)), 0.80),
        ]

        self._draw_depth_sorted_block_surfaces(item, x, y, size, surfaces)

    def _draw_cactus_icon(self, item, x, y, size):
        low, high = 1.0 / 16.0, 15.0 / 16.0
        surfaces = [
            (2, "-y", ((low, 0, high), (low, 0, low), (high, 0, low), (high, 0, high)), 0.50),
            (3, "+y", ((high, 1, high), (high, 1, low), (low, 1, low), (low, 1, high)), 1.0),
            (5, "-z", ((low, 1, low), (high, 1, low), (high, 0, low), (low, 0, low)), 0.60),
            (4, "+z", ((low, 1, high), (low, 0, high), (high, 0, high), (high, 1, high)), 0.60),
            (1, "-x", ((low, 1, high), (low, 1, low), (low, 0, low), (low, 0, high)), 0.80),
            (0, "+x", ((high, 0, high), (high, 0, low), (high, 1, low), (high, 1, high)), 0.80),
        ]
        self._draw_depth_sorted_block_surfaces(item, x, y, size, surfaces)

    def _draw_farmland_icon(self, item, x, y, size):
        top = 15.0 / 16.0
        surfaces = [
            (2, "-y", ((0, 0, 1), (0, 0, 0), (1, 0, 0), (1, 0, 1)), 0.50),
            (3, "+y", ((1, top, 1), (1, top, 0), (0, top, 0), (0, top, 1)), 1.0),
            (5, "-z", ((0, top, 0), (1, top, 0), (1, 0, 0), (0, 0, 0)), 0.60),
            (4, "+z", ((0, top, 1), (0, 0, 1), (1, 0, 1), (1, top, 1)), 0.60),
            (1, "-x", ((0, top, 1), (0, top, 0), (0, 0, 0), (0, 0, 1)), 0.80),
            (0, "+x", ((1, 0, 1), (1, 0, 0), (1, top, 0), (1, top, 1)), 0.80),
        ]
        self._draw_depth_sorted_block_surfaces(item, x, y, size, surfaces)

    def _draw_slab_icon(self, item, x, y, size):
        self._draw_depth_sorted_block_surfaces(item, x, y, size, [
            (4, "+z", ((0, 0, 1), (1, 0, 1), (1, 0.5, 1), (0, 0.5, 1)), 0.62),
            (1, "-x", ((0, 0, 0), (0, 0, 1), (0, 0.5, 1), (0, 0.5, 0)), 0.78),
            (3, "+y", ((0, 0.5, 0), (1, 0.5, 0), (1, 0.5, 1), (0, 0.5, 1)), 1.0),
        ])

    def _draw_snow_layer_icon(self, item, x, y, size):
        top = 2.0 / 16.0
        self._draw_depth_sorted_block_surfaces(item, x, y, size, [
            (2, "-y", ((0, 0, 1), (0, 0, 0), (1, 0, 0), (1, 0, 1)), 0.50),
            (3, "+y", ((1, top, 1), (1, top, 0), (0, top, 0), (0, top, 1)), 1.0),
            (5, "-z", ((0, top, 0), (1, top, 0), (1, 0, 0), (0, 0, 0)), 0.60),
            (4, "+z", ((0, top, 1), (0, 0, 1), (1, 0, 1), (1, top, 1)), 0.60),
            (1, "-x", ((0, top, 1), (0, top, 0), (0, 0, 0), (0, 0, 1)), 0.80),
            (0, "+x", ((1, 0, 1), (1, 0, 0), (1, top, 0), (1, top, 1)), 0.80),
        ])

    def _draw_stairs_icon(self, item, x, y, size):
        self._draw_depth_sorted_block_surfaces(item, x, y, size, [
            (1, "-x", ((0, 0, 0), (0, 0, 1), (0, 0.5, 1), (0, 0.5, 0)), 0.78),
            (1, "-x", ((0, 0.5, 0), (0, 0.5, 0.5), (0, 1, 0.5), (0, 1, 0)), 0.78),
            (4, "+z", ((0, 0, 1), (1, 0, 1), (1, 0.5, 1), (0, 0.5, 1)), 0.62),
            (4, "+z", ((0, 0.5, 0.5), (1, 0.5, 0.5), (1, 1, 0.5), (0, 1, 0.5)), 0.72),
            (3, "+y", ((0, 1, 0), (1, 1, 0), (1, 1, 0.5), (0, 1, 0.5)), 1.0),
            (3, "+y", ((0, 0.5, 0.5), (1, 0.5, 0.5), (1, 0.5, 1), (0, 0.5, 1)), 0.92),
        ])

    def begin_flat_icon_batch(self):
        depth = int(getattr(self, "_flat_icon_batch_depth", 0))
        if depth == 0:
            glPushAttrib(GL_ALL_ATTRIB_BITS)
            self._flat_icon_batch_states = True
            glDisable(GL_CULL_FACE)
            glDisable(GL_DEPTH_TEST)
            glDisable(GL_LIGHTING)
            glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
        self._flat_icon_batch_depth = depth + 1

    def end_flat_icon_batch(self):
        depth = int(getattr(self, "_flat_icon_batch_depth", 0))
        if depth <= 0:
            return
        depth -= 1
        self._flat_icon_batch_depth = depth
        if depth != 0:
            return
        if getattr(self, "_flat_icon_batch_states", False):
            glPopAttrib()
        self._flat_icon_batch_states = None

    def _with_flat_icon_state(self, draw_func, *args):
        if int(getattr(self, "_flat_icon_batch_depth", 0)) > 0:
            return draw_func(*args)
        self.begin_flat_icon_batch()
        try:
            return draw_func(*args)
        finally:
            self.end_flat_icon_batch()

    def _draw_item_atlas_icon(self, atlas_index, x, y, size=16):
        return self._with_flat_icon_state(
            self._draw_item_atlas_icon_impl, atlas_index, x, y, size
        )

    def _draw_item_atlas_icon_impl(self, atlas_index, x, y, size=16):
        if Item.item_texture is None:
            return
        cols = 16
        tile = 1.0 / cols
        col = int(atlas_index) % cols
        row = int(atlas_index) // cols
        u0, u1 = col * tile, (col + 1) * tile
        v1 = 1.0 - row * tile
        v0 = v1 - tile

        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_TEXTURE_2D)
        glEnable(GL_ALPHA_TEST)
        glAlphaFunc(GL_GREATER, 0.01)
        glColor4f(1.0, 1.0, 1.0, 1.0)
        Item.item_texture.bind()
        glBegin(GL_QUADS)
        glTexCoord2f(u0, v0); glVertex2f(x, y + size)
        glTexCoord2f(u1, v0); glVertex2f(x + size, y + size)
        glTexCoord2f(u1, v1); glVertex2f(x + size, y)
        glTexCoord2f(u0, v1); glVertex2f(x, y)
        glEnd()
        Item.item_texture.unbind()

    def _draw_item_icon(self, item, x, y, size=16):
        return self._with_flat_icon_state(self._draw_item_icon_impl, item, x, y, size)

    def _draw_item_icon_impl(self, item, x, y, size=16):
        if item is None:
            return

        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_TEXTURE_2D)
        glEnable(GL_ALPHA_TEST)
        glAlphaFunc(GL_GREATER, 0.01)
        glColor4f(1.0, 1.0, 1.0, 1.0)

        if item.id in ("redstone_repeater", "bed"):
            block_item = Item.block_defs.get(item.id, {}).get("blockitem", {})
            item_def = Item.item_defs.get(item.id, {})
            atlas_index = item_def.get("texture", block_item.get("texture"))
            if atlas_index is not None and Item.item_texture is not None:
                self._draw_item_atlas_icon_impl(int(atlas_index), x, y, size)
                self._draw_durability_bar(item, x, y, size)
            return

        item_defn = Item.item_defs.get(item.id) if item.id in Item.item_defs else None
        if item_defn is not None and "texture" in item_defn and Item.item_texture is not None:
            atlas_idx = Item.icon_texture(item.id, getattr(item, "damage", 0))
            if isinstance(atlas_idx, list):
                atlas_idx = atlas_idx[0]
            cols = 16
            tile = 1.0 / cols
            col = atlas_idx % cols
            row = atlas_idx // cols
            u0 = col * tile
            u1 = u0 + tile
            v1 = 1.0 - row * tile
            v0 = v1 - tile
            Item.item_texture.bind()
            glColor4f(1, 1, 1, 1)
            glBegin(GL_QUADS)
            glTexCoord2f(u0, v0); glVertex2f(x, y + size)
            glTexCoord2f(u1, v0); glVertex2f(x + size, y + size)
            glTexCoord2f(u1, v1); glVertex2f(x + size, y)
            glTexCoord2f(u0, v1); glVertex2f(x, y)
            glEnd()
            Item.item_texture.unbind()
            self._draw_durability_bar(item, x, y, size)
            return

        if item.id in Item.block_defs and Item.block_texture is not None:
            defn = Item.block_defs[item.id]
            model = defn.get("model", "cube")
            if model == "cube":
                self._draw_isometric_block_icon(item, x, y, size)
                return
            if model == "fence":
                boxes = [
                    [0.5 - 2.0/16.0, 0.0, 0.0, 0.5 + 2.0/16.0, 1.0, 4.0/16.0],
                    [0.5 - 2.0/16.0, 0.0, 1.0 - 4.0/16.0, 0.5 + 2.0/16.0, 1.0, 1.0],
                    [0.5 - 1.0/16.0, 1.0 - 3.0/16.0, -2.0/16.0, 0.5 + 1.0/16.0, 1.0 - 1.0/16.0, 1.0 + 2.0/16.0],
                    [0.5 - 1.0/16.0, 0.5 - 3.0/16.0, -2.0/16.0, 0.5 + 1.0/16.0, 0.5 - 1.0/16.0, 1.0 + 2.0/16.0],
                ]
                surfaces = []
                for x0, y0, z0, x1, y1, z1 in boxes:
                    surfaces.extend([
                        (2, "-y", ((x0,y0,z1),(x0,y0,z0),(x1,y0,z0),(x1,y0,z1)), 0.50),
                        (3, "+y", ((x1,y1,z1),(x1,y1,z0),(x0,y1,z0),(x0,y1,z1)), 1.0),
                        (5, "-z", ((x1,y1,z0),(x0,y1,z0),(x0,y0,z0),(x1,y0,z0)), 0.60),
                        (4, "+z", ((x0,y1,z1),(x0,y0,z1),(x1,y0,z1),(x1,y1,z1)), 0.60),
                        (1, "-x", ((x0,y1,z1),(x0,y1,z0),(x0,y0,z0),(x0,y0,z1)), 0.80),
                        (0, "+x", ((x1,y0,z1),(x1,y0,z0),(x1,y1,z0),(x1,y1,z1)), 0.80),
                    ])
                self._draw_depth_sorted_block_surfaces(item, x, y, size, surfaces)
                return
            if model == "cactus":
                self._draw_cactus_icon(item, x, y, size)
                return
            if model == "stairs":
                self._draw_stairs_icon(item, x, y, size)
                return
            if model == "slab":
                self._draw_slab_icon(item, x, y, size)
                return
            if model == "farmland":
                self._draw_farmland_icon(item, x, y, size)
                return
            if model == "snow_layer":
                self._draw_snow_layer_icon(item, x, y, size)
                return
            if model == "button":
                x0, x1 = 5.0 / 16.0, 11.0 / 16.0
                y0, y1 = 6.0 / 16.0, 10.0 / 16.0
                z0, z1 = 6.0 / 16.0, 10.0 / 16.0
                self._draw_depth_sorted_block_surfaces(item, x, y, size, [
                    (2, "-y", ((x0,y0,z1),(x0,y0,z0),(x1,y0,z0),(x1,y0,z1)), 0.50),
                    (3, "+y", ((x1,y1,z1),(x1,y1,z0),(x0,y1,z0),(x0,y1,z1)), 1.0),
                    (5, "-z", ((x1,y1,z0),(x0,y1,z0),(x0,y0,z0),(x1,y0,z0)), 0.60),
                    (4, "+z", ((x0,y1,z1),(x0,y0,z1),(x1,y0,z1),(x1,y1,z1)), 0.60),
                    (1, "-x", ((x0,y1,z1),(x0,y1,z0),(x0,y0,z0),(x0,y0,z1)), 0.80),
                    (0, "+x", ((x1,y0,z1),(x1,y0,z0),(x1,y1,z0),(x1,y1,z1)), 0.80),
                ])
                return

            if model == "pressure_plate":
                low, high = 6.0 / 16.0, 10.0 / 16.0
                self._draw_depth_sorted_block_surfaces(item, x, y, size, [
                    (2, "-y", ((0, low, 1), (0, low, 0), (1, low, 0), (1, low, 1)), 0.50),
                    (3, "+y", ((1, high, 1), (1, high, 0), (0, high, 0), (0, high, 1)), 1.0),
                    (5, "-z", ((0, high, 0), (1, high, 0), (1, low, 0), (0, low, 0)), 0.60),
                    (4, "+z", ((0, high, 1), (0, low, 1), (1, low, 1), (1, high, 1)), 0.60),
                    (1, "-x", ((0, high, 1), (0, high, 0), (0, low, 0), (0, low, 1)), 0.80),
                    (0, "+x", ((1, low, 1), (1, low, 0), (1, high, 0), (1, high, 1)), 0.80),
                ])
                return

            faces = Item.block_faces(item.id, getattr(item, "damage", 0))
            atlas_idx = faces[4] if isinstance(faces, list) and len(faces) > 4 else (faces[0] if isinstance(faces, list) else faces)
            cols = 16
            tile = 1.0 / cols
            col = atlas_idx % cols
            row = atlas_idx // cols
            u0 = col * tile
            u1 = u0 + tile
            v1 = 1.0 - row * tile
            v0 = v1 - tile
            Item.block_texture.bind()
            glColor4f(1, 1, 1, 1)
            glBegin(GL_QUADS)
            glTexCoord2f(u0, v0); glVertex2f(x, y + size)
            glTexCoord2f(u1, v0); glVertex2f(x + size, y + size)
            glTexCoord2f(u1, v1); glVertex2f(x + size, y)
            glTexCoord2f(u0, v1); glVertex2f(x, y)
            glEnd()
            Item.block_texture.unbind()
            return

        if item.id in Item.item_defs and "texture" in Item.item_defs[item.id] and Item.item_texture is not None:
            defn = Item.item_defs[item.id]
            atlas_idx = Item.icon_texture(item.id, getattr(item, "damage", 0))
            if isinstance(atlas_idx, list):
                atlas_idx = atlas_idx[0]

            cols = 16
            tile = 1.0 / cols
            col = atlas_idx % cols
            row = atlas_idx // cols
            u0 = col * tile
            u1 = u0 + tile
            v1 = 1.0 - row * tile
            v0 = v1 - tile

            Item.item_texture.bind()
            glColor4f(1, 1, 1, 1)
            glBegin(GL_QUADS)
            glTexCoord2f(u0, v0); glVertex2f(x, y + size)
            glTexCoord2f(u1, v0); glVertex2f(x + size, y + size)
            glTexCoord2f(u1, v1); glVertex2f(x + size, y)
            glTexCoord2f(u0, v1); glVertex2f(x, y)
            glEnd()
            Item.item_texture.unbind()

    def _draw_durability_bar(self, item, x, y, size=16):
        max_d = getattr(item, "max_damage", None)
        if max_d is None:
            max_d = Item.max_damage(item.id)
        damage = int(getattr(item, "damage", 0) or 0)
        if not max_d or damage <= 0:
            return

        max_d = int(max_d)
        if max_d <= 0:
            return
        green = 255 - damage * 255 // max_d
        filled = max(0, min(13, 13 - damage * 13 // max_d))
        dark_red = (255 - green) // 4

        scale = max(float(size) / 16.0, 0.01)
        bar_x = x + 2.0 * scale
        bar_y = y + 13.0 * scale

        glDisable(GL_TEXTURE_2D)
        glBegin(GL_QUADS)
        glColor4f(0.0, 0.0, 0.0, 1.0)
        glVertex2f(bar_x, bar_y)
        glVertex2f(bar_x + 13 * scale, bar_y)
        glVertex2f(bar_x + 13 * scale, bar_y + 2 * scale)
        glVertex2f(bar_x, bar_y + 2 * scale)

        glColor4f(dark_red / 255.0, 63.0 / 255.0, 0.0, 1.0)
        glVertex2f(bar_x, bar_y)
        glVertex2f(bar_x + 12 * scale, bar_y)
        glVertex2f(bar_x + 12 * scale, bar_y + scale)
        glVertex2f(bar_x, bar_y + scale)

        if filled > 0:
            glColor4f((255 - green) / 255.0, green / 255.0, 0.0, 1.0)
            glVertex2f(bar_x, bar_y)
            glVertex2f(bar_x + filled * scale, bar_y)
            glVertex2f(bar_x + filled * scale, bar_y + scale)
            glVertex2f(bar_x, bar_y + scale)
        glEnd()
        glColor4f(1.0, 1.0, 1.0, 1.0)
        glEnable(GL_TEXTURE_2D)

    def draw_survival_hud(self, player):
        if player is None or getattr(player, "gamemode", 0) == 1:
            return

        scale = _GUI_SCALE
        scaled_w = (self.display.width + scale - 1) // scale
        scaled_h = (self.display.height + scale - 1) // scale
        center_x = (scaled_w // 2) * scale
        row_y = (scaled_h - 32) * scale
        heart_row_y = row_y - 1
        health = max(0, min(20, int(getattr(player, "health", 20))))
        prev_health = max(0, min(20, int(getattr(player, "prev_health", health))))
        hearts_life = max(0, int(getattr(player, "hearts_life", 0)))
        blink = hearts_life >= 10 and (hearts_life // 3) % 2 == 1

        aw, ah = self._atlas_w, self._atlas_h
        self._atlas_w, self._atlas_h = self._icons_w, self._icons_h
        glEnable(GL_TEXTURE_2D)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_ALPHA_TEST)
        glAlphaFunc(GL_GREATER, 0.01)
        glColor4f(1.0, 1.0, 1.0, 1.0)
        self._icons_tex.bind()
        try:
            glBegin(GL_QUADS)
            armor_value = max(0, min(20, int(player.inventory.get_total_armor_value())))
            if armor_value > 0:
                for i in range(10):
                    ix = center_x + (91 - i * 8 - 9) * scale
                    if i * 2 + 1 < armor_value:
                        armor_u = 34
                    elif i * 2 + 1 == armor_value:
                        armor_u = 25
                    else:
                        armor_u = 16
                    self._tex_quad(ix, row_y, 9 * scale, 9 * scale,
                                   armor_u, 9, 9, 9)

            shake_rng = random.Random(self._survival_hud_tick(player) * 312871)
            for i in range(10):
                ix = center_x - 91 * scale + i * 8 * scale
                jitter = shake_rng.randrange(2) * scale if health <= 4 else 0
                iy = heart_row_y + jitter
                self._tex_quad(ix, iy, 9 * scale, 9 * scale,
                               25 if blink else 16, 0, 9, 9)
                if blink:
                    if i * 2 + 1 < prev_health:
                        self._tex_quad(ix, iy, 9 * scale, 9 * scale, 70, 0, 9, 9)
                    elif i * 2 + 1 == prev_health:
                        self._tex_quad(ix, iy, 9 * scale, 9 * scale, 79, 0, 9, 9)
                if i * 2 + 1 < health:
                    self._tex_quad(ix, iy, 9 * scale, 9 * scale, 52, 0, 9, 9)
                elif i * 2 + 1 == health:
                    self._tex_quad(ix, iy, 9 * scale, 9 * scale, 61, 0, 9, 9)

            if getattr(player, "submerged", False):
                air = max(0, min(300, int(getattr(player, "air", 300))))
                empty_bubbles = max(0, min(10, int(math.ceil((air - 2) * 10.0 / 300.0))))
                total_bubbles = max(0, min(10, int(math.ceil(air * 10.0 / 300.0))))
                bubble_count = max(empty_bubbles, total_bubbles)
                bubble_y = (scaled_h - 41) * scale
                for i in range(bubble_count):
                    ix = center_x - 91 * scale + i * 8 * scale
                    bubble_u = 16 if i < empty_bubbles else 25
                    self._tex_quad(ix, bubble_y, 9 * scale, 9 * scale,
                                   bubble_u, 18, 9, 9)
            glEnd()
        finally:
            self._icons_tex.unbind()
            self._atlas_w, self._atlas_h = aw, ah
            glColor4f(1.0, 1.0, 1.0, 1.0)
            glDisable(GL_ALPHA_TEST)
            glDisable(GL_BLEND)
            glDisable(GL_TEXTURE_2D)

    @staticmethod
    def _survival_hud_tick(player):
        return int(getattr(player, "ticks_existed", 0))

    def _draw_hotbar_icon(self, item, ix, iy, icon_size, partial=0.0):
        anim = float(getattr(item, "animations_to_go", 0) or 0) - partial
        if anim > 0.0:
            s = 1.0 + anim / 5.0
            sx = 1.0 / s
            sy = (s + 1.0) / 2.0
            cx = ix + icon_size * 0.5
            cy = iy + icon_size * 0.75
            glPushMatrix()
            glTranslatef(cx, cy, 0.0)
            glScalef(sx, sy, 1.0)
            glTranslatef(-cx, -cy, 0.0)
            self._draw_item_icon(item, ix, iy, icon_size)
            glPopMatrix()
        else:
            self._draw_item_icon(item, ix, iy, icon_size)

    def draw_hotbar(self, inventory, partial=0.0):
        scale = _GUI_SCALE
        bar_w = _HOTBAR_W * scale
        bar_h = _HOTBAR_H * scale
        sw, sh = self.display.width, self.display.height
        scaled_w = (sw + scale - 1) // scale
        scaled_h = (sh + scale - 1) // scale
        x = (scaled_w // 2 - _HOTBAR_W // 2) * scale
        y = (scaled_h - _HOTBAR_H) * scale
        slot_px = 20 * scale
        icon_size = 16 * scale

        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_TEXTURE_2D)
        glEnable(GL_ALPHA_TEST)
        glAlphaFunc(GL_GREATER, 0.01)

        self._gui_tex.bind()
        glColor4f(1, 1, 1, 1)
        glBegin(GL_QUADS)
        self._tex_quad(x, y, bar_w, bar_h, _HOTBAR_U, _HOTBAR_V, _HOTBAR_W, _HOTBAR_H)
        glEnd()
        self._gui_tex.unbind()

        local = inventory.hotbar_selected_local()
        if local is not None:
            sel_w = _SEL_W * scale
            sel_h = _SEL_H * scale
            sx = x + local * slot_px - 1 * scale
            sy = y - 1 * scale
            glEnable(GL_TEXTURE_2D)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glColor4f(1.0, 1.0, 1.0, 1.0)
            self._gui_tex.bind()
            glBegin(GL_QUADS)
            self._tex_quad(sx, sy, sel_w, sel_h, _SEL_U, _SEL_V, _SEL_W, _SEL_H)
            glEnd()
            self._gui_tex.unbind()

        items = inventory.hotbar_items()
        self.begin_flat_icon_batch()
        try:
            for i, item in enumerate(items):
                ix = x + 3 * scale + i * slot_px
                iy = y + 3 * scale
                if item is not None:
                    self._draw_hotbar_icon(item, ix, iy, icon_size, partial)
                if item is not None and item.count > 1:
                    label = str(item.count)
                    tw = self.display.string_width(label, scale=scale)
                    self.display.draw_string(
                        label,
                        (ix + icon_size + 1 - tw, iy + icon_size - 8 * scale),
                        (255, 255, 255),
                        scale,
                    )
                    glEnable(GL_BLEND)
                    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
                    glEnable(GL_TEXTURE_2D)
                    glEnable(GL_ALPHA_TEST)
                    glAlphaFunc(GL_GREATER, 0.01)
                    glColor4f(1.0, 1.0, 1.0, 1.0)

        finally:
            self.end_flat_icon_batch()

        glDisable(GL_ALPHA_TEST)
        glDisable(GL_TEXTURE_2D)
        glDisable(GL_BLEND)

    def draw_crosshair(self):
        scale = _GUI_SCALE
        size = 16 * scale
        scaled_w = (self.display.width + scale - 1) // scale
        scaled_h = (self.display.height + scale - 1) // scale
        cx = (scaled_w // 2 - 7) * scale
        cy = (scaled_h // 2 - 7) * scale

        glEnable(GL_TEXTURE_2D)
        glEnable(GL_BLEND)
        glBlendFunc(GL_ONE_MINUS_DST_COLOR, GL_ONE_MINUS_SRC_COLOR)
        glColor4f(1.0, 1.0, 1.0, 1.0)
        self._icons_tex.bind()
        aw, ah = self._atlas_w, self._atlas_h
        self._atlas_w, self._atlas_h = self._icons_w, self._icons_h
        glBegin(GL_QUADS)
        self._tex_quad(cx, cy, size, size, 0, 0, 16, 16)
        glEnd()
        self._atlas_w, self._atlas_h = aw, ah
        self._icons_tex.unbind()
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glDisable(GL_BLEND)
        glDisable(GL_TEXTURE_2D)

    def draw_debug(self, player, world, fps, sound_debug_line=None):
        d = self.display
        left = 4
        top = 4
        line_h = 18

        if self.debug:
            now = time.perf_counter()
            if self._debug_lines_cache is None or now >= self._debug_cache_until:
                self._debug_lines_cache = self._build_debug_lines(
                    player, world, fps, sound_debug_line=sound_debug_line,
                )
                self._debug_cache_until = now + 0.2
            lines = list(self._debug_lines_cache)
            last_ms = self._frame_times[-1] * 1000.0 if self._frame_times else 0.0
            if lines:
                lines[0] = f"{fps} fps, {last_ms:.1f} ms"

            debug_entries = [
                (line, (left, top + i * line_h), (255, 255, 255), 2)
                for i, line in enumerate(lines)
            ]
            if hasattr(d, "draw_strings"):
                d.draw_strings(debug_entries)
            else:
                for line, pos, color, scale in debug_entries:
                    d.draw_string(line, pos, color, scale)

            graph_h = 60
            margin = 4
            self._draw_frame_graph(
                margin,
                float(self.display.height) - graph_h - margin,
            )

    def _build_debug_lines(self, player, world, fps, sound_debug_line=None):
        import math

        x, y, z = player.pos
        yaw = player.yaw % 360
        pitch = player.pitch

        facing_angle = (yaw + 180) % 360
        if 45 <= facing_angle < 135:
            facing = "west (-x)"
        elif 135 <= facing_angle < 225:
            facing = "north (-z)"
        elif 225 <= facing_angle < 315:
            facing = "east (+x)"
        else:
            facing = "south (+z)"

        feet_y = int(math.floor(y + 0.5))
        eye_y = int(math.floor(y + getattr(player, "EYE_HEIGHT", 1.62) + 0.5))
        bx = int(math.floor(x + 0.5))
        bz = int(math.floor(z + 0.5))

        def _light_at(lx, ly, lz):
            if not hasattr(world, "get_light"):
                return 0, 0, 0
            combined = world.get_light(lx, ly, lz)
            sky = (
                world._get_saved_light("sky", lx, ly, lz)
                if hasattr(world, "_get_saved_light") else 0
            )
            block = (
                world._get_saved_light("block", lx, ly, lz)
                if hasattr(world, "_get_saved_light") else 0
            )
            return combined, sky, block

        feet_c, feet_s, feet_b = _light_at(bx, feet_y, bz)
        eye_c, eye_s, eye_b = _light_at(bx, eye_y, bz)

        target = player.get_targeted_block() if hasattr(player, "get_targeted_block") else None
        if target is not None:
            tx, ty, tz = target
            ttype = world.get_block_type(tx, ty, tz) or "air"
            try:
                tmeta = world.get_metadata(tx, ty, tz)
            except Exception:
                tmeta = 0
            tc, ts, tb = _light_at(tx, ty, tz)
            target_line = f"target: {ttype} meta={tmeta} @ {tx},{ty},{tz}"
            target_light = f"  light c/s/b: {tc}/{ts}/{tb}"
        else:
            target_line = "target: none"
            target_light = ""

        biome_line = "biome: ?"
        try:
            get_biome = getattr(world, "get_biome", None)
            get_climate = getattr(world, "get_climate", None)
            if callable(get_biome):
                biome = get_biome(bx, bz)
                name = (
                    getattr(biome, "name", None)
                    or getattr(biome, "key", None)
                    or str(biome)
                )
                if callable(get_climate):
                    temp, humid = get_climate(bx, bz)
                    biome_line = (
                        f"biome: {name}  t={float(temp):.3f} h={float(humid):.3f}"
                    )
                else:
                    biome_line = f"biome: {name}"
        except Exception:
            pass

        world_seed = getattr(world, "seed", None)
        terrain_seed = getattr(getattr(world, "terrain", None), "seed", world_seed)
        if world_seed is None:
            seed_line = "seed: ?"
        elif terrain_seed is not None and int(terrain_seed) != int(world_seed):
            seed_line = f"seed: {int(world_seed)}  TERRAIN SEED MISMATCH: {int(terrain_seed)}"
        else:
            seed_line = f"seed: {int(world_seed)}"

        entity_line = "entities: ?"
        try:
            entities = list(getattr(world, "entities", ()) or ())
            total = len(entities)
            living = 0
            hostile = 0
            loaded = 0
            for ent in entities:
                if getattr(ent, "dead", False):
                    continue
                if bool(getattr(ent, "is_living_entity", False)):
                    living += 1
                if bool(getattr(ent, "is_hostile", False)):
                    hostile += 1
                chunk_loaded = getattr(world, "_entity_chunk_loaded", None)
                if callable(chunk_loaded):
                    if chunk_loaded(ent):
                        loaded += 1
                else:
                    loaded += 1
            paused = ""
            try:
                from game.entities.mob import Mob
                if Mob.global_ai_paused:
                    paused = "  AI:PAUSED"
            except Exception:
                pass
            entity_line = (
                f"entities: {total}  living: {living}  hostile: {hostile}  "
                f"loaded: {loaded}{paused}"
            )
        except Exception:
            pass

        lines = [
            "",  
            f"x: {x:.2f}",
            f"y: {y:.2f}",
            f"z: {z:.2f}",
            seed_line,
            entity_line,
            f"yaw: {yaw:.1f}  pitch: {pitch:.1f}",
            f"facing: {facing}",
            f"feet light c/s/b: {feet_c}/{feet_s}/{feet_b}",
            f"eye  light c/s/b: {eye_c}/{eye_s}/{eye_b}",
            biome_line,
            target_line,
        ]
        if target_light:
            lines.append(target_light)
        if sound_debug_line:
            lines.append(sound_debug_line)
        return lines

    def _draw_frame_graph(self, x, y):
        times = self._frame_times
        if not times:
            return

        graph_h = 60
        graph_w = len(times)
        glDisable(GL_TEXTURE_2D)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glColor4f(0.0, 0.0, 0.0, 0.4)
        glBegin(GL_QUADS)
        glVertex2f(x, y)
        glVertex2f(x + graph_w, y)
        glVertex2f(x + graph_w, y + graph_h)
        glVertex2f(x, y + graph_h)
        glEnd()

        budget = 1.0 / 60.0
        ref_y = y + graph_h - int((budget / (budget * 2)) * graph_h)
        glColor4f(0.3, 0.3, 0.3, 0.8)
        glBegin(GL_LINES)
        glVertex2f(x, ref_y)
        glVertex2f(x + graph_w, ref_y)
        glEnd()

        data = array("f")
        max_t = budget * 2.0
        for i, frame_time in enumerate(times):
            height = max(1, int(min(frame_time / max_t, 1.0) * graph_h))
            if frame_time <= budget:
                color = (0.2, 0.85, 0.2)
            elif frame_time <= budget * 1.5:
                color = (0.9, 0.85, 0.15)
            else:
                color = (0.9, 0.2, 0.15)
            bx = x + i
            by = y + graph_h - height
            for vx, vy in (
                (bx, by), (bx + 1, by), (bx + 1, y + graph_h), (bx, y + graph_h),
            ):
                data.extend((vx, vy, *color))

        if data:
            if not getattr(self, "_frame_graph_vbo", None):
                self._frame_graph_vbo = int(glGenBuffers(1))
            glBindBuffer(GL_ARRAY_BUFFER, self._frame_graph_vbo)
            graph_data = np.frombuffer(data, dtype=np.float32)
            glBufferData(
                GL_ARRAY_BUFFER, graph_data.nbytes, graph_data, GL_STREAM_DRAW,
            )
            glEnableClientState(GL_VERTEX_ARRAY)
            glEnableClientState(GL_COLOR_ARRAY)
            try:
                glVertexPointer(2, GL_FLOAT, 20, c_void_p(0))
                glColorPointer(3, GL_FLOAT, 20, c_void_p(8))
                glDrawArrays(GL_QUADS, 0, len(data) // 5)
            finally:
                glDisableClientState(GL_COLOR_ARRAY)
                glDisableClientState(GL_VERTEX_ARRAY)
                glBindBuffer(GL_ARRAY_BUFFER, 0)
                glColor4f(1.0, 1.0, 1.0, 1.0)
        glDisable(GL_BLEND)

    def draw_version(self):
        if not self.debug:
            self.display.draw_string(
                f"Minecraft Python ({VERSION_TEXT})", (4, 4), color=(255, 255, 255), scale=2
            )

    def draw_inside_block_overlay(self, atlas_index: int):
        w, h = float(self.display.width), float(self.display.height)
        atlas_index = max(0, int(atlas_index))
        u0, v0, u1, v1 = self._atlas_uv(atlas_index)
        from OpenGL.GL import glIsEnabled
        blend_on = bool(glIsEnabled(GL_BLEND))
        alpha_on = bool(glIsEnabled(GL_ALPHA_TEST))
        depth_on = bool(glIsEnabled(GL_DEPTH_TEST))
        cull_on = bool(glIsEnabled(GL_CULL_FACE))
        texture_on = bool(glIsEnabled(GL_TEXTURE_2D))

        tex = Block._get_texture()
        glBindTexture(GL_TEXTURE_2D, tex.texture_id)
        glDisable(GL_ALPHA_TEST)
        glDisable(GL_DEPTH_TEST)
        glDisable(GL_CULL_FACE)
        glEnable(GL_TEXTURE_2D)
        glDisable(GL_BLEND)
        glColor4f(0.1, 0.1, 0.1, 0.5)
        glBegin(GL_QUADS)
        glTexCoord2f(u1, v0); glVertex2f(0.0, h)
        glTexCoord2f(u0, v0); glVertex2f(w, h)
        glTexCoord2f(u0, v1); glVertex2f(w, 0.0)
        glTexCoord2f(u1, v1); glVertex2f(0.0, 0.0)
        glEnd()
        glColor4f(1.0, 1.0, 1.0, 1.0)
        (glEnable if blend_on else glDisable)(GL_BLEND)
        (glEnable if alpha_on else glDisable)(GL_ALPHA_TEST)
        (glEnable if depth_on else glDisable)(GL_DEPTH_TEST)
        (glEnable if cull_on else glDisable)(GL_CULL_FACE)
        (glEnable if texture_on else glDisable)(GL_TEXTURE_2D)

    def draw_water_overlay(self, player):
        w, h = float(self.display.width), float(self.display.height)
        yaw = float(getattr(player, "yaw", 0.0))
        pitch = float(getattr(player, "pitch", 0.0))
        brightness = 0.7
        try:
            world = getattr(player, "world", None)
            if world is not None:
                light = world.get_light(
                    math.floor(player.pos[0] + 0.5),
                    math.floor(player.pos[1] + player.EYE_HEIGHT + 0.5),
                    math.floor(player.pos[2] + 0.5),
                )
                brightness = brightness_from_level(light, hell=bool(getattr(world, "is_hell_world", False)))
        except Exception:
            pass
        u_off = -yaw / 64.0
        v_off = pitch / 64.0
        span = 1.0

        from OpenGL.GL import glGetBooleanv, GL_DEPTH_WRITEMASK
        depth_mask = bool(glGetBooleanv(GL_DEPTH_WRITEMASK))
        blend_on = bool(glIsEnabled(GL_BLEND))
        depth_on = bool(glIsEnabled(GL_DEPTH_TEST))
        tex_on = bool(glIsEnabled(GL_TEXTURE_2D))
        alpha_on = bool(glIsEnabled(GL_ALPHA_TEST))

        try:
            glDisable(GL_DEPTH_TEST)
            glDepthMask(False)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glEnable(GL_TEXTURE_2D)
            glDisable(GL_ALPHA_TEST)
            glColor4f(brightness, brightness, brightness, 0.5)
            self._water_overlay_tex.bind()
            glDisable(GL_ALPHA_TEST)
            glBegin(GL_QUADS)
            glTexCoord2f(span + u_off, span + v_off); glVertex2f(0.0, h)
            glTexCoord2f(0.0 + u_off, span + v_off); glVertex2f(w, h)
            glTexCoord2f(0.0 + u_off, 0.0 + v_off); glVertex2f(w, 0.0)
            glTexCoord2f(span + u_off, 0.0 + v_off); glVertex2f(0.0, 0.0)
            glEnd()
            self._water_overlay_tex.unbind()
        finally:
            glColor4f(1.0, 1.0, 1.0, 1.0)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            if not blend_on:
                glDisable(GL_BLEND)
            else:
                glEnable(GL_BLEND)
            if depth_on:
                glEnable(GL_DEPTH_TEST)
            else:
                glDisable(GL_DEPTH_TEST)
            glDepthMask(depth_mask)
            if tex_on:
                glEnable(GL_TEXTURE_2D)
            else:
                glDisable(GL_TEXTURE_2D)
            if alpha_on:
                glEnable(GL_ALPHA_TEST)
            else:
                glDisable(GL_ALPHA_TEST)

    def draw_pumpkin_overlay(self, player):
        inventory = getattr(player, "inventory", None)
        armor = getattr(inventory, "armor", None)
        helmet = armor[0] if armor else None
        if helmet is None or helmet.id != "pumpkin":
            return
        if getattr(self, "_pumpkin_tex", None) is None:
            from OpenGL.GL import GL_LINEAR, GL_CLAMP_TO_EDGE
            self._pumpkin_tex = Texture(
                "game/textures/misc/pumpkinblur.png",
                repeat_mode=GL_CLAMP_TO_EDGE,
                min_filter=GL_LINEAR,
                mag_filter=GL_LINEAR,
            )
        w, h = float(self.display.width), float(self.display.height)
        depth_on = bool(glIsEnabled(GL_DEPTH_TEST))
        alpha_on = bool(glIsEnabled(GL_ALPHA_TEST))
        try:
            glDisable(GL_DEPTH_TEST)
            glDepthMask(False)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glEnable(GL_TEXTURE_2D)
            glDisable(GL_ALPHA_TEST)
            glColor4f(1.0, 1.0, 1.0, 1.0)
            self._pumpkin_tex.bind(alpha_test=False)
            glBegin(GL_QUADS)
            glTexCoord2f(0.0, 1.0); glVertex2f(0.0, 0.0)
            glTexCoord2f(1.0, 1.0); glVertex2f(w, 0.0)
            glTexCoord2f(1.0, 0.0); glVertex2f(w, h)
            glTexCoord2f(0.0, 0.0); glVertex2f(0.0, h)
            glEnd()
            self._pumpkin_tex.unbind()
        finally:
            glDepthMask(True)
            if depth_on:
                glEnable(GL_DEPTH_TEST)
            if alpha_on:
                glEnable(GL_ALPHA_TEST)
            glColor4f(1.0, 1.0, 1.0, 1.0)

    def draw_vignette(self, brightness=1.0):
        brightness = 1.0 - max(0.0, min(1.0, float(brightness)))
        self._prev_vignette_brightness += (
            brightness - self._prev_vignette_brightness
        ) * 0.01
        v = self._prev_vignette_brightness
        w, h = float(self.display.width), float(self.display.height)

        from OpenGL.GL import glGetBooleanv, GL_DEPTH_WRITEMASK
        depth_mask = bool(glGetBooleanv(GL_DEPTH_WRITEMASK))
        blend_on = bool(glIsEnabled(GL_BLEND))
        depth_on = bool(glIsEnabled(GL_DEPTH_TEST))
        tex_on = bool(glIsEnabled(GL_TEXTURE_2D))
        alpha_on = bool(glIsEnabled(GL_ALPHA_TEST))

        try:
            glDisable(GL_DEPTH_TEST)
            glDepthMask(False)
            glEnable(GL_BLEND)
            glBlendFunc(GL_ZERO, GL_ONE_MINUS_SRC_COLOR)
            glEnable(GL_TEXTURE_2D)
            glDisable(GL_ALPHA_TEST)
            glColor4f(v, v, v, 1.0)
            self._vignette_tex.bind()
            glBegin(GL_QUADS)
            glTexCoord2f(0.0, 1.0); glVertex2f(0.0, 0.0)
            glTexCoord2f(1.0, 1.0); glVertex2f(w, 0.0)
            glTexCoord2f(1.0, 0.0); glVertex2f(w, h)
            glTexCoord2f(0.0, 0.0); glVertex2f(0.0, h)
            glEnd()
            self._vignette_tex.unbind()
        finally:
            glColor4f(1.0, 1.0, 1.0, 1.0)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            if not blend_on:
                glDisable(GL_BLEND)
            else:
                glEnable(GL_BLEND)
            if depth_on:
                glEnable(GL_DEPTH_TEST)
            else:
                glDisable(GL_DEPTH_TEST)
            glDepthMask(depth_mask)
            if tex_on:
                glEnable(GL_TEXTURE_2D)
            else:
                glDisable(GL_TEXTURE_2D)
            if alpha_on:
                glEnable(GL_ALPHA_TEST)
            else:
                glDisable(GL_ALPHA_TEST)

    def draw_portal_overlay(self, player, partial=0.0):
        from OpenGL.GL import glGetBooleanv, GL_DEPTH_WRITEMASK

        previous = float(getattr(player, "prev_portal_time", 0.0))
        current = float(getattr(player, "portal_time", 0.0))
        progress = max(0.0, min(1.0, previous + (current - previous) * float(partial)))
        if progress <= 0.0:
            return
        alpha = progress ** 4 * 0.8 + 0.2
        w, h = float(self.display.width), float(self.display.height)
        depth_mask = bool(glGetBooleanv(GL_DEPTH_WRITEMASK))
        blend_on = bool(glIsEnabled(GL_BLEND))
        depth_on = bool(glIsEnabled(GL_DEPTH_TEST))
        texture_on = bool(glIsEnabled(GL_TEXTURE_2D))
        alpha_on = bool(glIsEnabled(GL_ALPHA_TEST))
        try:
            glDisable(GL_DEPTH_TEST)
            glDepthMask(False)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glEnable(GL_TEXTURE_2D)
            glDisable(GL_ALPHA_TEST)
            glColor4f(1.0, 1.0, 1.0, alpha)
            texture = Block._get_texture()
            glBindTexture(GL_TEXTURE_2D, texture.texture_id)
            u0, v0, u1, v1 = self._atlas_uv(14)
            glBegin(GL_QUADS)
            glTexCoord2f(u1, v0); glVertex2f(0.0, h)
            glTexCoord2f(u0, v0); glVertex2f(w, h)
            glTexCoord2f(u0, v1); glVertex2f(w, 0.0)
            glTexCoord2f(u1, v1); glVertex2f(0.0, 0.0)
            glEnd()
        finally:
            glBindTexture(GL_TEXTURE_2D, 0)
            glColor4f(1.0, 1.0, 1.0, 1.0)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            (glEnable if blend_on else glDisable)(GL_BLEND)
            (glEnable if depth_on else glDisable)(GL_DEPTH_TEST)
            glDepthMask(depth_mask)
            (glEnable if texture_on else glDisable)(GL_TEXTURE_2D)
            (glEnable if alpha_on else glDisable)(GL_ALPHA_TEST)

    def draw_chat(self, game=None):
        if game is None:
            game = getattr(self, "_game", None)
        if game is None:
            return
        chat_log = getattr(game, "chat_log", None)
        if chat_log is None or len(chat_log) == 0:
            return
        cur = getattr(game, "current_screen", None)
        chat_open = cur is not None and cur.__class__.__name__ == "GuiChat"
        rows = chat_log.visible(chat_open=chat_open)
        if not rows:
            return

        scale = _GUI_SCALE
        sw = self.display.width
        sh = self.display.height
        scaled_h = (sh + scale - 1) // scale
        anchor_y = (scaled_h - 48) * scale

        x0 = 2 * scale
        x1 = (2 + 320) * scale

        glDisable(GL_TEXTURE_2D)
        glDisable(GL_ALPHA_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        for row, line, alpha in rows:
            if alpha <= 0:
                continue
            y = anchor_y + (-row * 9) * scale
            bg_a = (alpha // 2) / 255.0
            y0 = y - 1 * scale
            y1 = y + 8 * scale
            glColor4f(0.0, 0.0, 0.0, bg_a)
            glBegin(GL_QUADS)
            glVertex2f(x0, y0)
            glVertex2f(x1, y0)
            glVertex2f(x1, y1)
            glVertex2f(x0, y1)
            glEnd()

        glColor4f(1.0, 1.0, 1.0, 1.0)

        for row, line, alpha in rows:
            if alpha <= 0:
                continue
            y = anchor_y + (-row * 9) * scale
            self.display.draw_string(
                line.message,
                (2 * scale, y),
                (255, 255, 255),
                scale=float(scale),
                shadow=True,
                alpha=alpha / 255.0,
            )

        glEnable(GL_ALPHA_TEST)
        glDisable(GL_BLEND)
        glColor4f(1.0, 1.0, 1.0, 1.0)

    def draw_sleep_overlay(self, player):
        if player is None:
            return
        timer = int(getattr(player, "sleep_timer", 0) or 0)
        if timer <= 0:
            return
        if timer <= 100:
            progress = timer / 100.0
        else:
            progress = 1.0 - (timer - 100) / 10.0
        progress = max(0.0, min(1.0, float(progress)))
        if progress <= 0.0:
            return
        alpha = (220.0 / 255.0) * progress
        w = int(self.display.width)
        h = int(self.display.height)
        glDisable(GL_TEXTURE_2D)
        glDisable(GL_ALPHA_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glColor4f(16.0 / 255.0, 16.0 / 255.0, 16.0 / 255.0, alpha)
        glBegin(GL_QUADS)
        glVertex2f(0, 0)
        glVertex2f(w, 0)
        glVertex2f(w, h)
        glVertex2f(0, h)
        glEnd()
        glColor4f(1.0, 1.0, 1.0, 1.0)
        glDisable(GL_BLEND)
        glEnable(GL_ALPHA_TEST)
        glEnable(GL_TEXTURE_2D)

    def draw_actionbar(self, player):

        message = str(getattr(player, "_actionbar_message", "") or "")
        if not message:
            return

        now = time.monotonic()
        expires_at = float(getattr(player, "_actionbar_expires_at", 0.0) or 0.0)
        remaining = expires_at - now
        if remaining <= 0.0:
            player._actionbar_message = ""
            return

        style_kind = str(getattr(player, "_actionbar_style", "record") or "record")
        if style_kind == "plain":
            alpha = 1.0 if remaining > 1.0 else max(0.0, remaining)
            color = (255, 255, 255)
        else:
            style = record_notification_style(remaining)
            if style is None:
                return
            color, alpha = style

        logical_width = self.display.string_width(message) + 1
        x, y, scale = record_notification_layout(
            self.display.width, self.display.height, logical_width, _GUI_SCALE,
        )
        self.display.draw_string(
            message, (x, y), color, scale=scale, shadow=False, alpha=alpha,
        )

    def draw(self, player, world, fps, sound_debug_line=None):
        brightness = 1.0
        if player is not None:
            getter = getattr(player, "get_entity_brightness", None)
            if callable(getter):
                try:
                    brightness = float(getter())
                except Exception:
                    brightness = 1.0
            elif world is not None:
                try:
                    import math
                    bx = int(math.floor(player.pos[0] + 0.5))
                    by = int(math.floor(player.pos[1] + getattr(player, "EYE_HEIGHT", 1.62) * 0.66 + 0.5))
                    bz = int(math.floor(player.pos[2] + 0.5))
                    light = world.get_light(bx, by, bz) if hasattr(world, "get_light") else 15
                    from engine.lighting import brightness_from_level
                    brightness = float(brightness_from_level(light, hell=bool(getattr(world, "is_hell_world", False))))
                except Exception:
                    brightness = 1.0
        settings = getattr(player, "settings", None) if player is not None else None
        if settings is None:
            settings = getattr(self, "settings", None)
        if settings is None:
            game = getattr(self, "_game", None)
            settings = getattr(game, "settings", None) if game is not None else None
        if settings is None or getattr(settings, "fancy_graphics", True):
            self.draw_vignette(brightness)
        self.draw_crosshair()
        if player is not None:
            self.draw_survival_hud(player)
        partial = getattr(player, "inventory_animation_partial", 0.0) if player else 0.0
        if player is not None:
            self.draw_hotbar(player.inventory, partial=partial)
            self.draw_actionbar(player)
            self.draw_sleep_overlay(player)
        self.draw_chat()
        self.draw_debug(player, world, fps, sound_debug_line=sound_debug_line)
        self.draw_version()
