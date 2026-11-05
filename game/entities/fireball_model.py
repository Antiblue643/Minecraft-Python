from __future__ import annotations

import math


SNOWBALL_ATLAS_INDEX = 14
FIREBALL_HALF_WIDTH = 1.0
FIREBALL_HALF_HEIGHT = 1.0
FIREBALL_VERTICAL_OFFSET = 0.5


def camera_billboard_axes(yaw_degrees: float, pitch_degrees: float):
    yaw = math.radians(float(yaw_degrees))
    pitch = math.radians(float(pitch_degrees))
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    cos_p, sin_p = math.cos(pitch), math.sin(pitch)
    right = (cos_y, 0.0, -sin_y)
    look = (-sin_y * cos_p, sin_p, -cos_y * cos_p)
    up = (
        right[1] * look[2] - right[2] * look[1],
        right[2] * look[0] - right[0] * look[2],
        right[0] * look[1] - right[1] * look[0],
    )
    return right, up, look


def fireball_billboard_vertices(position, yaw_degrees: float, pitch_degrees: float):
    right, up, _look = camera_billboard_axes(yaw_degrees, pitch_degrees)
    x, y, z = (float(v) for v in position)
    bottom = FIREBALL_VERTICAL_OFFSET - FIREBALL_HALF_HEIGHT
    top = FIREBALL_VERTICAL_OFFSET + FIREBALL_HALF_HEIGHT
    left = -FIREBALL_HALF_WIDTH
    right_edge = FIREBALL_HALF_WIDTH

    def point(horizontal, vertical):
        return (
            x + right[0] * horizontal + up[0] * vertical,
            y + right[1] * horizontal + up[1] * vertical,
            z + right[2] * horizontal + up[2] * vertical,
        )

    return (
        point(left, bottom),
        point(right_edge, bottom),
        point(right_edge, top),
        point(left, top),
    )


def projectile_billboard_vertices(position, yaw_degrees: float, pitch_degrees: float,
                                  half_width: float = 0.25,
                                  bottom: float = 0.0, top: float = 0.5):
    right, up, look = camera_billboard_axes(yaw_degrees, pitch_degrees)
    x, y, z = (float(v) for v in position)

    def point(horizontal, vertical):
        return (
            x + right[0] * horizontal + up[0] * vertical,
            y + right[1] * horizontal + up[1] * vertical,
            z + right[2] * horizontal + up[2] * vertical,
        )

    vertices = (
        point(-half_width, bottom),
        point(half_width, bottom),
        point(half_width, top),
        point(-half_width, top),
    )
    return vertices, (-look[0], -look[1], -look[2])


def snowball_icon_uvs():
    tile = 1.0 / 16.0
    col = SNOWBALL_ATLAS_INDEX % 16
    row = SNOWBALL_ATLAS_INDEX // 16
    u0, u1 = col * tile, (col + 1) * tile
    v0, v1 = 1.0 - (row + 1) * tile, 1.0 - row * tile
    return u0, v0, u1, v1


def draw_fireball(entity):
    from OpenGL.GL import (
        GL_ALPHA_TEST, GL_CULL_FACE, GL_LIGHTING, GL_TEXTURE_2D,
        GL_QUADS, glBegin, glColor4f, glDisable, glEnable, glEnd,
        glIsEnabled, glPopMatrix, glPushMatrix, glTexCoord2f, glVertex3f,
    )
    from game.entities.entity import Entity
    from game.entities.item import Item

    texture = Item.item_texture
    if texture is None:
        return
    renderer = getattr(entity.world_obj, "renderer", None) or Entity.renderer
    partial = 0.0
    try:
        partial = max(0.0, min(1.0, float(entity.world_obj.partial_ticks())))
    except Exception:
        pass
    x, y, z = (
        float(entity.prev_pos[i]) + (float(entity.pos[i]) - float(entity.prev_pos[i])) * partial
        for i in range(3)
    )
    if renderer is not None and hasattr(renderer, "camera_relative_offset"):
        origin = renderer.camera_relative_offset(x, y, z)
        camera_yaw = float(getattr(renderer, "yaw", 0.0))
        camera_pitch = float(getattr(renderer, "pitch", 0.0))
    else:
        origin = (x, y, z)
        camera_yaw = camera_pitch = 0.0

    vertices = fireball_billboard_vertices(origin, camera_yaw, camera_pitch)
    u0, v0, u1, v1 = snowball_icon_uvs()
    brightness = max(0.0, min(1.0, float(entity.get_entity_brightness())))
    alpha_was = bool(glIsEnabled(GL_ALPHA_TEST))
    cull_was = bool(glIsEnabled(GL_CULL_FACE))
    lighting_was = bool(glIsEnabled(GL_LIGHTING))
    texture_was = bool(glIsEnabled(GL_TEXTURE_2D))
    matrix_pushed = False
    try:
        glPushMatrix()
        matrix_pushed = True
        glEnable(GL_TEXTURE_2D)
        glDisable(GL_LIGHTING)
        glDisable(GL_CULL_FACE)
        texture.bind()
        glColor4f(brightness, brightness, brightness, 1.0)
        glBegin(GL_QUADS)
        glTexCoord2f(u0, v0); glVertex3f(*vertices[0])
        glTexCoord2f(u1, v0); glVertex3f(*vertices[1])
        glTexCoord2f(u1, v1); glVertex3f(*vertices[2])
        glTexCoord2f(u0, v1); glVertex3f(*vertices[3])
        glEnd()
    finally:
        try:
            texture.unbind()
        finally:
            glColor4f(1.0, 1.0, 1.0, 1.0)
            (glEnable if alpha_was else glDisable)(GL_ALPHA_TEST)
            (glEnable if cull_was else glDisable)(GL_CULL_FACE)
            (glEnable if lighting_was else glDisable)(GL_LIGHTING)
            (glEnable if texture_was else glDisable)(GL_TEXTURE_2D)
            if matrix_pushed:
                glPopMatrix()
