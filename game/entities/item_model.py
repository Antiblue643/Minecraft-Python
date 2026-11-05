from __future__ import annotations

import math
import random

from OpenGL.GL import *
from game.entities.entity import Entity

BOB_HEIGHT = 0.1
BLOCK_SCALE = 0.125
ITEM_SCALE = 0.25
BLOCK_ATLAS_COLS = 16
BLOCK_ATLAS_TILE = 1 / BLOCK_ATLAS_COLS
ITEM_ATLAS_COLS = 16
ITEM_ATLAS_TILE = 1 / ITEM_ATLAS_COLS
_STAIRS_DROP_MESH_CACHE = {}
_FENCE_INV_MESH_CACHE = None


def _fence_inventory_mesh():
    global _FENCE_INV_MESH_CACHE
    if _FENCE_INV_MESH_CACHE is not None:
        return _FENCE_INV_MESH_CACHE
    from game.block import Block
    from engine.lighting import default_lighting

    block = Block("fence", (0.0, 0.0, 0.0))
    faces = block.faces
    texture = int(faces[0] if isinstance(faces, (list, tuple)) else faces)
    u_base, v_base = block._atlas_uv(texture)
    tile = 1.0 / 16.0
    boxes = [
        [0.5 - 2.0/16.0, 0.0, 0.0, 0.5 + 2.0/16.0, 1.0, 4.0/16.0],
        [0.5 - 2.0/16.0, 0.0, 1.0 - 4.0/16.0, 0.5 + 2.0/16.0, 1.0, 1.0],
        [0.5 - 1.0/16.0, 1.0 - 3.0/16.0, -2.0/16.0, 0.5 + 1.0/16.0, 1.0 - 1.0/16.0, 1.0 + 2.0/16.0],
        [0.5 - 1.0/16.0, 0.5 - 3.0/16.0, -2.0/16.0, 0.5 + 1.0/16.0, 0.5 - 1.0/16.0, 1.0 + 2.0/16.0],
    ]

    def clamp01(value):
        if value < 0.0:
            return 0.0
        if value > 1.0:
            return 1.0
        return value

    def classic_uv(direction, cx, cy, cz):
        x, y, z = cx + 0.5, cy + 0.5, cz + 0.5
        dx, dy, dz = direction
        if dx == 1:
            uf, vf = 1.0 - z, y
        elif dx == -1:
            uf, vf = z, y
        elif dz == 1:
            uf, vf = x, y
        elif dz == -1:
            uf, vf = 1.0 - x, y
        elif dy == 1:
            uf, vf = x, 1.0 - z
        else:
            uf, vf = x, z
        uf, vf = clamp01(uf), clamp01(vf)
        return (
            u_base + uf * tile * (15.99 / 16.0),
            v_base + vf * tile * (15.99 / 16.0),
        )

    out = []
    for bounds in boxes:
        centered = Block.classic_to_center(bounds)
        x0, y0, z0, x1, y1, z1 = centered
        quads = (
            ((0, 0, 1), ((x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1))),
            ((0, 0,-1), ((x1,y0,z0),(x0,y0,z0),(x0,y1,z0),(x1,y1,z0))),
            ((0, 1, 0), ((x0,y1,z1),(x1,y1,z1),(x1,y1,z0),(x0,y1,z0))),
            ((0,-1, 0), ((x0,y0,z0),(x1,y0,z0),(x1,y0,z1),(x0,y0,z1))),
            ((1, 0, 0), ((x1,y0,z1),(x1,y0,z0),(x1,y1,z0),(x1,y1,z1))),
            ((-1,0, 0), ((x0,y0,z0),(x0,y0,z1),(x0,y1,z1),(x0,y1,z0))),
        )
        for direction, quad in quads:
            vertices = list(quad)
            uvs = tuple(classic_uv(direction, *vertex) for vertex in quad)
            color = default_lighting.face_color(direction)
            out.append((direction, vertices, uvs, color))
    _FENCE_INV_MESH_CACHE = out
    return out


def _stack_copies(count):
    n = 1
    if count > 1:
        n = 2
    if count > 5:
        n = 3
    if count > 20:
        n = 4
    return n


class ItemRendererMixin:
    def _render_position(self):
        from game.entities.item import PICKUP_ANIMATION_TICKS

        if self.pickup_ticks is None or self._collector is None:
            return self.pos[0], self.pos[1], self.pos[2]
        f = min(self.pickup_ticks / PICKUP_ANIMATION_TICKS, 1.0)
        f *= f
        fx, fy, fz = self._pickup_from
        tx, ty, tz = self._collector.pos
        ty += 0.8 - self.size[1] * 0.5
        return fx + (tx - fx) * f, fy + (ty - fy) * f, fz + (tz - fz) * f

    def _block_uv(self, atlas_index):
        col = atlas_index % BLOCK_ATLAS_COLS
        row = atlas_index // BLOCK_ATLAS_COLS
        return col * BLOCK_ATLAS_TILE, 1 - (row + 1) * BLOCK_ATLAS_TILE

    def _item_uv(self, atlas_index):
        col = atlas_index % ITEM_ATLAS_COLS
        row = atlas_index // ITEM_ATLAS_COLS
        return col * ITEM_ATLAS_TILE, 1 - (row + 1) * ITEM_ATLAS_TILE

    def draw(self):
        if self.dead:
            return
        x, y, z = self._render_position()

        if self.in_water or bool(getattr(self, "_is_projectile_billboard", False)):
            bob = 0.0
        else:
            bob = math.sin(self.age / 10.0 + self.hover_start) * BOB_HEIGHT + BOB_HEIGHT

        try:
            from game.entities.entity_shadow import (
                SHADOW_SIZE_ITEM, render_entity_shadow, shadow_opacity_for_distance,
            )
            opacity = shadow_opacity_for_distance(Entity.world, x, y, z)
            if opacity > 0.0:
                render_entity_shadow(
                    Entity.world, x, y, z, SHADOW_SIZE_ITEM, opacity=opacity,
                )
        except Exception:
            pass

        self._draw_brightness = self.get_entity_brightness()

        copies = _stack_copies(self.item.count)
        rng = random.Random(187)

        spin = math.degrees(self.age / 20.0 + self.hover_start)
        if self._is_block:
            self._draw_block(x, y + self.size[1] * 0.5 + bob, z, spin, copies, rng)
        elif getattr(self, "_is_fence", False):
            self._draw_fence(x, y + self.size[1] * 0.5 + bob, z, spin, copies, rng)
        elif getattr(self, "_is_cactus", False) or getattr(self, "_is_snow_layer", False):
            self._draw_block_model(x, y + self.size[1] * 0.5 + bob, z, spin, copies, rng)
        elif getattr(self, "_is_stairs", False):
            self._draw_stairs(x, y + self.size[1] * 0.5 + bob, z, spin, copies, rng)
        elif getattr(self, "_is_pressure_plate", False):
            self._draw_pressure_plate(x, y + self.size[1] * 0.5 + bob, z, spin, copies, rng)
        elif getattr(self, "_is_button", False):
            self._draw_button(x, y + self.size[1] * 0.5 + bob, z, spin, copies, rng)
        else:
            self._draw_billboard(x, y + bob, z, copies, rng)
        if self._burning_ticks > 0.0:
            center_y = (
                y + self.size[1] * 0.5 + bob
                if (self._is_block or getattr(self, "_is_cactus", False)
                        or getattr(self, "_is_fence", False))
                else y + ITEM_SCALE + bob
            )
            self._draw_fire_billboard(x, center_y, z)
        glColor3f(1.0, 1.0, 1.0)
    def _draw_fire_billboard(self, x, center_y, z):
        from game.block import Block
        from game.entities.player_model import _fire_tile_uv

        texture = Block._get_texture()
        if texture is None:
            return

        renderer = getattr(self, "renderer", None) or Entity.renderer
        if renderer is not None:
            ox, oy, oz = renderer.camera_relative_offset(x, center_y, z)
            view_yaw = float(getattr(renderer, "yaw", 0.0))
        else:
            ox, oy, oz = float(x), float(center_y), float(z)
            view_yaw = 0.0

        alpha_was_enabled = bool(glIsEnabled(GL_ALPHA_TEST))
        cull_was_enabled = bool(glIsEnabled(GL_CULL_FACE))
        lighting_was_enabled = bool(glIsEnabled(GL_LIGHTING))
        texture_was_enabled = bool(glIsEnabled(GL_TEXTURE_2D))

        half_width = 0.22
        half_height = 0.27
        u0, v0, u1, v1 = _fire_tile_uv(31)
        try:
            glPushMatrix()
            glTranslatef(ox, oy, oz)
            glRotatef(view_yaw, 0.0, 1.0, 0.0)
            glEnable(GL_TEXTURE_2D)
            glDisable(GL_CULL_FACE)
            glDisable(GL_LIGHTING)
            glEnable(GL_ALPHA_TEST)
            texture.bind()
            glAlphaFunc(GL_GREATER, 0.1)
            glColor4f(1.0, 1.0, 1.0, 1.0)
            hw = half_width
            hh = half_height
            glPushMatrix()
            glTranslatef(0.0, 0.0, 0.0)
            glBegin(GL_QUADS)
            glTexCoord2f(u1, v0); glVertex3f( hw, -hh, 0.0)
            glTexCoord2f(u0, v0); glVertex3f(-hw, -hh, 0.0)
            glTexCoord2f(u0, v1); glVertex3f(-hw,  hh, 0.0)
            glTexCoord2f(u1, v1); glVertex3f( hw,  hh, 0.0)
            glEnd()
            glPopMatrix()
        finally:
            texture.unbind()
            glColor4f(1.0, 1.0, 1.0, 1.0)
            glAlphaFunc(GL_GREATER, 0.5)
            (glEnable if alpha_was_enabled else glDisable)(GL_ALPHA_TEST)
            (glEnable if cull_was_enabled else glDisable)(GL_CULL_FACE)
            (glEnable if lighting_was_enabled else glDisable)(GL_LIGHTING)
            (glEnable if texture_was_enabled else glDisable)(GL_TEXTURE_2D)
            glPopMatrix()
    def _draw_block(self, x, y, z, spin, copies, rng):
        from game.entities.item import Item

        defn = Item.block_defs.get(self.item.id)
        if not defn or Item.block_texture is None:
            return

        faces = defn.get("faces", 0)

        def face_idx(slot):
            if slot == 3 and self.item.id == "grass_block" and isinstance(faces, list):
                return faces[0]
            return faces[slot] if isinstance(faces, list) else faces

        t = BLOCK_ATLAS_TILE
        s = BLOCK_SCALE
        b = self._draw_brightness

        glPushMatrix()
        renderer = getattr(self, "renderer", None) or Entity.renderer
        if renderer is not None:
            ox, oy, oz = renderer.camera_relative_offset(x, y, z)
        else:
            ox, oy, oz = float(x), float(y), float(z)
        glTranslatef(ox, oy, oz)
        glRotatef(spin, 0, 1, 0)

        Item.block_texture.bind()
        glEnable(GL_TEXTURE_2D)
        blend_was_on = bool(glIsEnabled(GL_BLEND))
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        for copy in range(copies):
            glPushMatrix()
            if copy > 0:
                glTranslatef(
                    (rng.random() * 2.0 - 1.0) * 0.2,
                    (rng.random() * 2.0 - 1.0) * 0.2,
                    (rng.random() * 2.0 - 1.0) * 0.2,
                )

            u, v = self._block_uv(face_idx(4))
            glColor3f(0.9 * b, 0.9 * b, 0.9 * b)
            glBegin(GL_QUADS)
            glNormal3f(0, 0, 1)
            glTexCoord2f(u,     v + t); glVertex3f(-s,  s,  s)
            glTexCoord2f(u,     v    ); glVertex3f(-s, -s,  s)
            glTexCoord2f(u + t, v    ); glVertex3f( s, -s,  s)
            glTexCoord2f(u + t, v + t); glVertex3f( s,  s,  s)
            glEnd()

            u, v = self._block_uv(face_idx(5))
            glColor3f(0.9 * b, 0.9 * b, 0.9 * b)
            glBegin(GL_QUADS)
            glNormal3f(0, 0, -1)
            glTexCoord2f(u,     v + t); glVertex3f( s,  s, -s)
            glTexCoord2f(u,     v    ); glVertex3f( s, -s, -s)
            glTexCoord2f(u + t, v    ); glVertex3f(-s, -s, -s)
            glTexCoord2f(u + t, v + t); glVertex3f(-s,  s, -s)
            glEnd()

            u, v = self._block_uv(face_idx(2))
            glColor3f(0.5 * b, 0.5 * b, 0.5 * b)
            glBegin(GL_QUADS)
            glNormal3f(0, -1, 0)
            glTexCoord2f(u,     v    ); glVertex3f(-s, -s, -s)
            glTexCoord2f(u + t, v    ); glVertex3f( s, -s, -s)
            glTexCoord2f(u + t, v + t); glVertex3f( s, -s,  s)
            glTexCoord2f(u,     v + t); glVertex3f(-s, -s,  s)
            glEnd()

            u, v = self._block_uv(face_idx(3))
            glColor3f(1.0 * b, 1.0 * b, 1.0 * b)
            glBegin(GL_QUADS)
            glNormal3f(0, 1, 0)
            glTexCoord2f(u,     v + t); glVertex3f(-s,  s, -s)
            glTexCoord2f(u,     v    ); glVertex3f(-s,  s,  s)
            glTexCoord2f(u + t, v    ); glVertex3f( s,  s,  s)
            glTexCoord2f(u + t, v + t); glVertex3f( s,  s, -s)
            glEnd()

            u, v = self._block_uv(face_idx(0))
            glColor3f(0.75 * b, 0.75 * b, 0.75 * b)
            glBegin(GL_QUADS)
            glNormal3f(1, 0, 0)
            glTexCoord2f(u,     v + t); glVertex3f( s,  s,  s)
            glTexCoord2f(u,     v    ); glVertex3f( s, -s,  s)
            glTexCoord2f(u + t, v    ); glVertex3f( s, -s, -s)
            glTexCoord2f(u + t, v + t); glVertex3f( s,  s, -s)
            glEnd()

            u, v = self._block_uv(face_idx(1))
            glColor3f(0.75 * b, 0.75 * b, 0.75 * b)
            glBegin(GL_QUADS)
            glNormal3f(-1, 0, 0)
            glTexCoord2f(u,     v + t); glVertex3f(-s,  s, -s)
            glTexCoord2f(u,     v    ); glVertex3f(-s, -s, -s)
            glTexCoord2f(u + t, v    ); glVertex3f(-s, -s,  s)
            glTexCoord2f(u + t, v + t); glVertex3f(-s,  s,  s)
            glEnd()
            glPopMatrix()

        if not blend_was_on:
            glDisable(GL_BLEND)
        glDisable(GL_TEXTURE_2D)
        Item.block_texture.unbind()
        glPopMatrix()

    def _draw_fence(self, x, center_y, z, spin, copies, rng):
        from game.entities.item import Item

        if Item.block_texture is None:
            return
        mesh_data = _fence_inventory_mesh()
        if not mesh_data:
            return

        renderer = getattr(self, "renderer", None) or Entity.renderer
        if renderer is not None:
            ox, oy, oz = renderer.camera_relative_offset(x, center_y, z)
        else:
            ox, oy, oz = float(x), float(center_y), float(z)

        texture = Item.block_texture
        texture.bind()
        glEnable(GL_TEXTURE_2D)
        try:
            glPushMatrix()
            glTranslatef(ox, oy, oz)
            glRotatef(spin, 0.0, 1.0, 0.0)
            for copy in range(copies):
                glPushMatrix()
                if copy > 0:
                    glTranslatef(
                        (rng.random() * 2.0 - 1.0) * 0.2,
                        (rng.random() * 2.0 - 1.0) * 0.2,
                        (rng.random() * 2.0 - 1.0) * 0.2,
                    )
                glScalef(0.5, 0.5, 0.5)
                for normal, vertices, uvs, color in mesh_data:
                    shade = self._draw_brightness
                    glColor3f(color[0] * shade, color[1] * shade, color[2] * shade)
                    glBegin(GL_QUADS)
                    glNormal3f(*normal)
                    for (uu, vv), (vx, vy, vz) in zip(uvs, vertices):
                        glTexCoord2f(uu, vv)
                        glVertex3f(vx, vy, vz)
                    glEnd()
                glPopMatrix()
            glPopMatrix()
        finally:
            glColor3f(1.0, 1.0, 1.0)
            glDisable(GL_TEXTURE_2D)
            texture.unbind()

    def _draw_block_model(self, x, center_y, z, spin, copies, rng):
        from game.block import Block
        from game.entities.item import Item

        if Item.block_texture is None:
            return
        try:
            block = Block(self.item.id, (0.0, 0.0, 0.0))
            block._mesh_meta = 0
            mesh_data = block.get_mesh_data()
        except Exception:
            return
        if not mesh_data:
            return

        renderer = getattr(self, "renderer", None) or Entity.renderer
        if renderer is not None:
            ox, oy, oz = renderer.camera_relative_offset(x, center_y, z)
        else:
            ox, oy, oz = float(x), float(center_y), float(z)

        texture = Item.block_texture
        texture.bind()
        glEnable(GL_TEXTURE_2D)
        try:
            glPushMatrix()
            glTranslatef(ox, oy, oz)
            glRotatef(spin, 0.0, 1.0, 0.0)
            for copy in range(copies):
                glPushMatrix()
                if copy > 0:
                    glTranslatef(
                        (rng.random() * 2.0 - 1.0) * 0.2,
                        (rng.random() * 2.0 - 1.0) * 0.2,
                        (rng.random() * 2.0 - 1.0) * 0.2,
                    )
                glScalef(0.5, 0.5, 0.5)
                for normal, vertices, uvs, _opaque, _transparent, color in mesh_data:
                    shade = self._draw_brightness
                    glColor3f(color[0] * shade, color[1] * shade, color[2] * shade)
                    glBegin(GL_QUADS)
                    glNormal3f(*normal)
                    for (uu, vv), (vx, vy, vz) in zip(uvs, vertices):
                        glTexCoord2f(uu, vv)
                        glVertex3f(vx, vy, vz)
                    glEnd()
                glPopMatrix()
            glPopMatrix()
        finally:
            glColor3f(1.0, 1.0, 1.0)
            glDisable(GL_TEXTURE_2D)
            texture.unbind()

    def _draw_stairs(self, x, center_y, z, spin, copies, rng):
        from game.block import Block
        from game.entities.item import Item

        if Item.block_texture is None:
            return
        mesh_data = _STAIRS_DROP_MESH_CACHE.get(self.item.id)
        if mesh_data is None:
            try:
                block = Block(self.item.id, (0.0, 0.0, 0.0))
                block._mesh_meta = 0
                mesh_data = block.get_mesh_data()
                _STAIRS_DROP_MESH_CACHE[self.item.id] = mesh_data
            except Exception:
                return
        if not mesh_data:
            return

        renderer = getattr(self, "renderer", None) or Entity.renderer
        if renderer is not None:
            ox, oy, oz = renderer.camera_relative_offset(x, center_y, z)
        else:
            ox, oy, oz = float(x), float(center_y), float(z)

        texture = Item.block_texture
        texture.bind()
        glEnable(GL_TEXTURE_2D)
        try:
            glPushMatrix()
            glTranslatef(ox, oy, oz)
            glRotatef(spin, 0.0, 1.0, 0.0)
            for copy in range(copies):
                glPushMatrix()
                if copy > 0:
                    glTranslatef(
                        (rng.random() * 2.0 - 1.0) * 0.2,
                        (rng.random() * 2.0 - 1.0) * 0.2,
                        (rng.random() * 2.0 - 1.0) * 0.2,
                    )
                glScalef(0.5, 0.5, 0.5)
                for normal, vertices, uvs, _opaque, _transparent, color in mesh_data:
                    shade = self._draw_brightness
                    glColor3f(float(color[0]) * shade, float(color[1]) * shade,
                              float(color[2]) * shade)
                    glBegin(GL_QUADS)
                    glNormal3f(*normal)
                    for (uu, vv), (vx, vy, vz) in zip(uvs, vertices):
                        glTexCoord2f(uu, vv)
                        glVertex3f(vx, vy, vz)
                    glEnd()
                glPopMatrix()
            glPopMatrix()
        finally:
            glColor3f(1.0, 1.0, 1.0)
            glDisable(GL_TEXTURE_2D)
            texture.unbind()

    def _draw_pressure_plate(self, x, center_y, z, spin, copies, rng):
        from game.entities.item import Item

        defn = Item.block_defs.get(self.item.id)
        if not defn or Item.block_texture is None:
            return
        face_value = defn.get("faces", 0)
        if isinstance(face_value, list):
            tile_index = face_value[3] if len(face_value) > 3 else face_value[0]
        else:
            tile_index = face_value
        u, v = self._block_uv(tile_index)
        t = BLOCK_ATLAS_TILE
        s = 0.25
        half_y = 0.0625
        b = self._draw_brightness
        renderer = getattr(self, "renderer", None) or Entity.renderer

        faces = [
            ((0, 1, 0), ((-s, half_y, -s), (-s, half_y, s), (s, half_y, s), (s, half_y, -s)), 1.0, (0.0, 1.0)),
            ((0, -1, 0), ((-s, -half_y, s), (-s, -half_y, -s), (s, -half_y, -s), (s, -half_y, s)), 0.5, (0.0, 1.0)),
            ((0, 0, 1), ((-s, -half_y, s), (s, -half_y, s), (s, half_y, s), (-s, half_y, s)), 0.6, (0.375, 0.625)),
            ((0, 0, -1), ((s, -half_y, -s), (-s, -half_y, -s), (-s, half_y, -s), (s, half_y, -s)), 0.6, (0.375, 0.625)),
            ((1, 0, 0), ((s, -half_y, s), (s, -half_y, -s), (s, half_y, -s), (s, half_y, s)), 0.8, (0.375, 0.625)),
            ((-1, 0, 0), ((-s, -half_y, -s), (-s, -half_y, s), (-s, half_y, s), (-s, half_y, -s)), 0.8, (0.375, 0.625)),
        ]
        texture = Item.block_texture
        texture.bind()
        glEnable(GL_TEXTURE_2D)
        try:
            for copy in range(copies):
                glPushMatrix()
                if renderer is not None:
                    ox, oy, oz = renderer.camera_relative_offset(x, center_y, z)
                else:
                    ox, oy, oz = float(x), float(center_y), float(z)
                glTranslatef(ox, oy, oz)
                glRotatef(spin, 0, 1, 0)
                if copy > 0:
                    glTranslatef(
                        (rng.random() * 2.0 - 1.0) * 0.2,
                        (rng.random() * 2.0 - 1.0) * 0.2,
                        (rng.random() * 2.0 - 1.0) * 0.2,
                    )
                for normal, vertices, shade, vfractions in faces:
                    vf0, vf1 = vfractions
                    if normal[1] != 0:
                        uv = ((u, v + t), (u, v), (u + t, v), (u + t, v + t))
                    else:
                        vb0, vb1 = v + t * vf0, v + t * vf1
                        uv = ((u, vb0), (u + t, vb0), (u + t, vb1), (u, vb1))
                    glColor3f(shade*b, shade*b, shade*b)
                    glBegin(GL_QUADS)
                    glNormal3f(*normal)
                    for (uu, vv), (vx, vy, vz) in zip(uv, vertices):
                        glTexCoord2f(uu, vv)
                        glVertex3f(vx, vy, vz)
                    glEnd()
                glPopMatrix()
        finally:
            texture.unbind()
            glColor3f(1.0, 1.0, 1.0)

    def _draw_button(self, x, center_y, z, spin, copies, rng):
        from game.entities.item import Item

        defn = Item.block_defs.get(self.item.id)
        texture = Item.block_texture
        if not defn or texture is None:
            return
        face_value = defn.get("faces", 1)
        tile_index = face_value[0] if isinstance(face_value, list) else int(face_value)
        u, v = self._block_uv(tile_index)
        t = BLOCK_ATLAS_TILE
        brightness = self._draw_brightness
        item_model_scale = 0.5
        sx = item_model_scale * 6 / 32
        sy = item_model_scale * 4 / 32
        sz = item_model_scale * 4 / 32
        x0, x1, y0, y1, z0, z1 = -sx, sx, -sy, sy, -sz, sz
        quads = (
            ((0, 0, 1), ((x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)), 0.8),
            ((0, 0,-1), ((x1,y0,z0),(x0,y0,z0),(x0,y1,z0),(x1,y1,z0)), 0.8),
            ((0, 1, 0), ((x0,y1,z1),(x1,y1,z1),(x1,y1,z0),(x0,y1,z0)), 1.0),
            ((0,-1, 0), ((x0,y0,z0),(x1,y0,z0),(x1,y0,z1),(x0,y0,z1)), 0.5),
            ((1, 0, 0), ((x1,y0,z1),(x1,y0,z0),(x1,y1,z0),(x1,y1,z1)), 0.7),
            ((-1,0,0), ((x0,y0,z0),(x0,y0,z1),(x0,y1,z1),(x0,y1,z0)), 0.7),
        )
        renderer = getattr(self, "renderer", None) or Entity.renderer
        if renderer is not None:
            ox, oy, oz = renderer.camera_relative_offset(x, center_y, z)
        else:
            ox, oy, oz = float(x), float(center_y), float(z)
        texture.bind()
        glEnable(GL_TEXTURE_2D)
        try:
            for copy in range(copies):
                glPushMatrix()
                glTranslatef(ox, oy, oz)
                glRotatef(spin, 0, 1, 0)
                if copy > 0:
                    glTranslatef(
                        (rng.random() * 2.0 - 1.0) * 0.2,
                        (rng.random() * 2.0 - 1.0) * 0.2,
                        (rng.random() * 2.0 - 1.0) * 0.2,
                    )
                for normal, vertices, shade in quads:
                    glColor3f(shade * brightness, shade * brightness, shade * brightness)
                    glBegin(GL_QUADS)
                    glNormal3f(*normal)
                    for vx, vy, vz in vertices:
                        xf = vx / item_model_scale + 0.5
                        yf = vy / item_model_scale + 0.5
                        zf = vz / item_model_scale + 0.5
                        if normal[0] > 0:
                            uf, vf = 1.0 - zf, yf
                        elif normal[0] < 0:
                            uf, vf = zf, yf
                        elif normal[2] > 0:
                            uf, vf = xf, yf
                        elif normal[2] < 0:
                            uf, vf = 1.0 - xf, yf
                        elif normal[1] > 0:
                            uf, vf = xf, 1.0 - zf
                        else:
                            uf, vf = xf, zf
                        glTexCoord2f(u + uf * t, v + vf * t)
                        glVertex3f(vx, vy, vz)
                    glEnd()
                glPopMatrix()
        finally:
            glColor3f(1.0, 1.0, 1.0)
            glDisable(GL_TEXTURE_2D)
            texture.unbind()

    def _draw_billboard(self, x, y, z, copies, rng):
        from game.entities.item import Item

        item_def = Item.item_defs.get(self.item.id)
        block_def = Item.block_defs.get(self.item.id)
        use_item_sprite = item_def is not None and "texture" in item_def
        if use_item_sprite:
            atlas_index = item_def.get("texture", 0)
            if isinstance(atlas_index, list):
                atlas_index = atlas_index[0]
            texture = Item.item_texture
            t = ITEM_ATLAS_TILE
            use_block_uv = False
        elif block_def is not None:
            faces = block_def.get("faces", 0)
            atlas_index = faces[0] if isinstance(faces, list) else faces
            texture = Item.block_texture
            t = BLOCK_ATLAS_TILE
            use_block_uv = True
        else:
            return
        if texture is None:
            return
        u, v = self._block_uv(atlas_index) if use_block_uv else self._item_uv(atlas_index)
        s = ITEM_SCALE

        renderer = Entity.renderer
        if renderer is not None:
            angle = float(getattr(renderer, "yaw", 0.0))
            pitch = float(getattr(renderer, "pitch", 0.0))
        else:
            angle = pitch = 0.0
        projectile_billboard = bool(getattr(self, "_is_projectile_billboard", False))

        texture.bind()
        glEnable(GL_TEXTURE_2D)
        b = self._draw_brightness
        glColor3f(b, b, b)

        for copy in range(copies):
            glPushMatrix()
            if renderer is not None:
                ox, oy, oz = renderer.camera_relative_offset(x, y, z)
            else:
                ox, oy, oz = float(x), float(y), float(z)
            if copy > 0:
                ox += (rng.random() * 2.0 - 1.0) * 0.15
                oy += (rng.random() * 2.0 - 1.0) * 0.15
                oz += (rng.random() * 2.0 - 1.0) * 0.15

            if not projectile_billboard:
                glTranslatef(ox, oy, oz)
                glRotatef(angle, 0, 1, 0)

            glBegin(GL_QUADS)
            if projectile_billboard:
                from game.entities.fireball_model import projectile_billboard_vertices
                vertices, normal = projectile_billboard_vertices(
                    (ox, oy, oz), angle, pitch, half_width=s,
                    bottom=0.0, top=s * 2.0,
                )
                glNormal3f(*normal)
                glTexCoord2f(u,     v    ); glVertex3f(*vertices[0])
                glTexCoord2f(u + t, v    ); glVertex3f(*vertices[1])
                glTexCoord2f(u + t, v + t); glVertex3f(*vertices[2])
                glTexCoord2f(u,     v + t); glVertex3f(*vertices[3])
            else:
                glNormal3f(0, 0, 1)
                glTexCoord2f(u,     v    ); glVertex3f(-s, 0,   0)
                glTexCoord2f(u + t, v    ); glVertex3f( s, 0,   0)
                glTexCoord2f(u + t, v + t); glVertex3f( s, s*2, 0)
                glTexCoord2f(u,     v + t); glVertex3f(-s, s*2, 0)
            glEnd()
            glPopMatrix()

        glDisable(GL_TEXTURE_2D)
        texture.unbind()
