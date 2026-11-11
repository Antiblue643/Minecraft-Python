from __future__ import annotations

import math

from OpenGL.GL import (
    GL_ALPHA_TEST, GL_BLEND, GL_CLAMP_TO_EDGE, GL_ONE_MINUS_SRC_ALPHA,
    GL_POLYGON_OFFSET_FILL, GL_QUADS, GL_SRC_ALPHA, GL_TEXTURE_2D, glBegin,
    glBindTexture, glBlendFunc, glColor4f, glDepthMask, glDisable, glEnable,
    glEnd, glPolygonOffset, glTexCoord2f, glVertex3f,
)

from engine.lighting import brightness_from_level
from engine.textures import Texture
from game.block import Block

_SHADOW_TEX = None
_SHADOW_PATH = "game/textures/shadow.png"
_NORMAL_CUBE_CACHE = {}

SHADOW_SIZE_LIVING = 0.5
SHADOW_SIZE_ITEM = 0.15


def _shadow_texture():
    global _SHADOW_TEX
    if _SHADOW_TEX is None:
        _SHADOW_TEX = Texture(_SHADOW_PATH, repeat_mode=GL_CLAMP_TO_EDGE)
    return _SHADOW_TEX


def _is_normal_cube(block_type):
    if not block_type:
        return False
    cached = _NORMAL_CUBE_CACHE.get(block_type)
    if cached is not None:
        return cached
    Block._load_defs()
    definition = Block._block_defs.get(block_type) or {}
    result = True
    if definition.get("model", "cube") != "cube":
        result = False
    elif definition.get("transparent"):
        result = False
    else:
        n_cull = definition.get("nCull", "all")
        if n_cull in ("none", "liquid"):
            result = False
    _NORMAL_CUBE_CACHE[block_type] = result
    return result


def render_entity_shadow(world, entity_x, entity_y, entity_z, shadow_size,
                         *, opacity=1.0, renderer=None):
    if world is None or shadow_size <= 0.0 or opacity <= 0.0:
        return
    if not getattr(Block, "_fancy_graphics", True):
        return

    try:
        from game.entities.entity import Entity
        cam = getattr(Entity, "_entity_shadow_camera", None)
        if cam is not None:
            cdx = entity_x - float(cam[0])
            cdy = entity_y - float(cam[1])
            cdz = entity_z - float(cam[2])
            if cdx * cdx + cdy * cdy + cdz * cdz > 48.0 * 48.0:
                return
    except Exception:
        pass

    rndr = renderer or getattr(world, "renderer", None)
    if rndr is None or not hasattr(rndr, "camera_relative_offset"):
        return

    tex = _shadow_texture()
    if tex is None or tex.texture_id is None:
        return

    radius = float(shadow_size)
    min_x = int(math.floor(entity_x - radius + 0.5))
    max_x = int(math.floor(entity_x + radius + 0.5))
    min_sy = int(math.floor(entity_y - 2.5 + 0.5))
    max_sy = int(math.floor(entity_y + 0.5 - 0.001))
    min_z = int(math.floor(entity_z - radius + 0.5))
    max_z = int(math.floor(entity_z + radius + 0.5))

    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glDepthMask(False)
    glEnable(GL_TEXTURE_2D)
    glDisable(GL_ALPHA_TEST)
    glEnable(GL_POLYGON_OFFSET_FILL)
    glPolygonOffset(-1.0, -1.0)
    glBindTexture(GL_TEXTURE_2D, tex.texture_id)

    glBegin(GL_QUADS)
    for sx in range(min_x, max_x + 1):
        for sy in range(min_sy, max_sy + 1):
            for sz in range(min_z, max_z + 1):
                solid_type = world.get_block_type(sx, sy, sz)
                if not _is_normal_cube(solid_type):
                    continue
                light = world.get_light(sx, sy + 1, sz)
                if light <= 3:
                    continue
                brightness = brightness_from_level(light, hell=bool(getattr(world, "is_hell_world", False)))

                top_y_world = sy + 0.5 + (1.0 / 64.0)
                if entity_y + 0.05 < top_y_world:
                    continue

                x0_w, x1_w = sx - 0.5, sx + 0.5
                z0_w, z1_w = sz - 0.5, sz + 0.5

                x0, y_s, z0 = rndr.camera_relative_offset(x0_w, top_y_world, z0_w)
                x1, _, z1 = rndr.camera_relative_offset(x1_w, top_y_world, z1_w)
                _, y_s, _ = rndr.camera_relative_offset(sx, top_y_world, sz)

                height_term = (entity_y - top_y_world) / 2.0
                alpha = (opacity - height_term) * 0.5 * brightness
                if alpha <= 0.0:
                    continue
                if alpha > 1.0:
                    alpha = 1.0

                u0 = (entity_x - x0_w) / (2.0 * radius) + 0.5
                u1 = (entity_x - x1_w) / (2.0 * radius) + 0.5
                v0 = (entity_z - z0_w) / (2.0 * radius) + 0.5
                v1 = (entity_z - z1_w) / (2.0 * radius) + 0.5

                glColor4f(1.0, 1.0, 1.0, float(alpha))
                glTexCoord2f(u0, v0)
                glVertex3f(x0, y_s, z0)
                glTexCoord2f(u0, v1)
                glVertex3f(x0, y_s, z1)
                glTexCoord2f(u1, v1)
                glVertex3f(x1, y_s, z1)
                glTexCoord2f(u1, v0)
                glVertex3f(x1, y_s, z0)
    glEnd()

    glBindTexture(GL_TEXTURE_2D, 0)
    glColor4f(1.0, 1.0, 1.0, 1.0)
    glDepthMask(True)
    glDisable(GL_POLYGON_OFFSET_FILL)
    glDisable(GL_BLEND)


def shadow_opacity_for_distance(world, entity_x, entity_y, entity_z,
                                shadow_opaque=1.0):
    rndr = getattr(world, "renderer", None)
    if rndr is None:
        return float(shadow_opaque)
    cam_x = getattr(rndr, "camX", entity_x)
    cam_y = getattr(rndr, "camY", entity_y)
    cam_z = getattr(rndr, "camZ", entity_z)
    dx = entity_x - cam_x
    dy = entity_y - cam_y
    dz = entity_z - cam_z
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    return max(0.0, (1.0 - dist / 256.0) * float(shadow_opaque))
