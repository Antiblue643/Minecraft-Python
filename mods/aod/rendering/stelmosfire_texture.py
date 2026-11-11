
from __future__ import annotations

import random

GRID_W = 16
GRID_H = 16
TEXTURE_SCALE = 8
TEXTURE_WIDTH = GRID_W * TEXTURE_SCALE
TEXTURE_HEIGHT = GRID_H * TEXTURE_SCALE


def empty_grid():
    return [[0.0 for _ in range(GRID_W)] for _ in range(GRID_H)]


def update_electricity(current_grid, *, rng=None):
    rng = rng if rng is not None else random
    try:
        source = [
            [max(0.0, min(1.0, float(current_grid[y][x])))
             for x in range(GRID_W)]
            for y in range(GRID_H)
        ]
    except (TypeError, ValueError, IndexError, KeyError):
        source = empty_grid()

    next_grid = [[value * 0.32 for value in row] for row in source]
    source_has_charge = any(value > 0.05 for row in source for value in row)
    if not source_has_charge or rng.random() < 0.4:
        seed_count = rng.randint(1, 8)
        for _ in range(seed_count):
            next_grid[GRID_H - 1][rng.randint(0, GRID_W - 1)] = 1.0

    for y in range(GRID_H - 1, 0, -1):
        for x in range(GRID_W):
            charge = source[y][x]
            if charge <= 0.3:
                continue
            for ty, tx in (
                (y - 1, x),
                (y - 1, (x - 1) % GRID_W),
                (y - 1, (x + 1) % GRID_W),
            ):
                if rng.random() < 0.24:
                    next_grid[ty][tx] = min(
                        1.0, next_grid[ty][tx] + charge * rng.uniform(0.7, 0.95)
                    )

    for y in range(GRID_H):
        for x in range(GRID_W):
            if source[y][x] > 0.05:
                next_grid[y][x] = min(
                    1.0, next_grid[y][x] + rng.random() * 0.05
                )
    return next_grid


def get_color(intensity):
    intensity = max(0.0, min(1.0, float(intensity)))
    if intensity <= 0.05:
        return (0, 0, 0)
    if intensity < 0.3:
        return (int(intensity * 150), 0, int(intensity * 255))
    if intensity < 0.7:
        return (0, int(intensity * 200), 255)
    return (int(intensity * 180), 255, 255)


def generate_texture_rgba(grid, *, scale=TEXTURE_SCALE):
    scale = max(1, int(scale))
    width, height = GRID_W * scale, GRID_H * scale
    pixels = bytearray(width * height * 4)
    for gy in range(GRID_H):
        for gx in range(GRID_W):
            intensity = max(0.0, min(1.0, float(grid[gy][gx])))
            red, green, blue = get_color(intensity)
            alpha = 0 if intensity <= 0.05 else min(
                255, int(255 * min(1.0, intensity * 1.35))
            )
            if alpha <= 0:
                continue
            pixel = bytes((red, green, blue, alpha))
            for oy in range(scale):
                row = (gy * scale + oy) * width * 4
                for ox in range(scale):
                    offset = row + (gx * scale + ox) * 4
                    pixels[offset:offset + 4] = pixel
    return width, height, bytes(pixels)


def _normalize(vector):
    length = sum(float(v) * float(v) for v in vector) ** 0.5
    if length <= 1.0e-8:
        return (0.0, 0.0, 0.0)
    return tuple(float(v) / length for v in vector)


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _quad_for_segment(start, end, side, width):
    def offset(point, amount):
        return tuple(point[i] + side[i] * amount for i in range(3))
    return (
        offset(start, -width), offset(start, width),
        offset(end, width), offset(end, -width),
    )




def draw_stelmosfire_edges(world, camera_pos, *, partial=0.0):
    segments = getattr(world, "_aod_zapper_stelmos_edges", None)
    if not isinstance(segments, list) or not segments:
        return 0
    grid = getattr(world, "_aod_zapper_stelmos_grid", None)
    if not isinstance(grid, list):
        return 0

    try:
        from OpenGL.GL import (
            GL_ALPHA_TEST, GL_BLEND, GL_BLEND_DST, GL_BLEND_SRC, GL_CLAMP_TO_EDGE,
            GL_CULL_FACE, GL_DEPTH_TEST, GL_DEPTH_WRITEMASK, GL_LIGHTING, GL_NEAREST, GL_ONE,
            GL_QUADS, GL_RGBA, GL_SRC_ALPHA, GL_TEXTURE_2D,
            GL_TEXTURE_BINDING_2D, GL_TEXTURE_MAG_FILTER, GL_TEXTURE_MIN_FILTER,
            GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T, GL_UNSIGNED_BYTE,
            glBegin, glBindTexture, glBlendFunc, glColor4f, glDepthMask,
            glDisable, glEnable, glEnd, glGenTextures, glGetBooleanv,
            glGetIntegerv, glIsEnabled, glTexCoord2f, glTexImage2D,
            glTexParameteri, glVertex3f,
        )
    except Exception:
        return 0

    try:
        cam = tuple(float(value) for value in camera_pos[:3])
    except (TypeError, ValueError):
        cam = (0.0, 0.0, 0.0)

    texture_id = int(getattr(world, "_aod_zapper_stelmos_texture_id", 0) or 0)
    try:
        old_binding = int(glGetIntegerv(GL_TEXTURE_BINDING_2D))
    except Exception:
        old_binding = 0
    blend_was = bool(glIsEnabled(GL_BLEND))
    cull_was = bool(glIsEnabled(GL_CULL_FACE))
    depth_was = bool(glIsEnabled(GL_DEPTH_TEST))
    lighting_was = bool(glIsEnabled(GL_LIGHTING))
    texture_was = bool(glIsEnabled(GL_TEXTURE_2D))
    alpha_was = bool(glIsEnabled(GL_ALPHA_TEST))
    try:
        depth_mask_was = bool(glGetBooleanv(GL_DEPTH_WRITEMASK))
    except Exception:
        depth_mask_was = True
    try:
        old_src = int(glGetIntegerv(GL_BLEND_SRC))
        old_dst = int(glGetIntegerv(GL_BLEND_DST))
    except Exception:
        old_src, old_dst = GL_SRC_ALPHA, GL_ONE

    rendered = 0
    try:
        if not texture_id:
            texture_id = int(glGenTextures(1))
            world._aod_zapper_stelmos_texture_id = texture_id
            world._aod_zapper_stelmos_uploaded_revision = -1
        glBindTexture(GL_TEXTURE_2D, texture_id)
        revision = int(getattr(world, "_aod_zapper_stelmos_revision", 0))
        uploaded = int(getattr(world, "_aod_zapper_stelmos_uploaded_revision", -1))
        if uploaded != revision:
            width, height, data = generate_texture_rgba(grid)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(
                GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0,
                GL_RGBA, GL_UNSIGNED_BYTE, data,
            )
            world._aod_zapper_stelmos_uploaded_revision = revision

        glEnable(GL_TEXTURE_2D)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)
        glDisable(GL_CULL_FACE)
        glEnable(GL_DEPTH_TEST)
        glDepthMask(False)
        glDisable(GL_LIGHTING)
        glDisable(GL_ALPHA_TEST)

        for segment in segments:
            try:
                vertices = tuple(tuple(float(v) for v in point)
                                 for point in segment["vertices"])
                remaining = max(0.0, float(segment.get("remaining", 0.0)))
                lifetime = max(1.0e-6, float(segment.get("lifetime", 0.25)))
                if len(vertices) != 4 or any(len(point) != 3 for point in vertices):
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            life = max(0.0, min(1.0, remaining / lifetime))
            if life <= 0.0:
                continue
            uvs = segment.get("uvs", (
                (0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0),
            ))
            if len(uvs) != 4:
                uvs = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
            glColor4f(1.0, 1.0, 1.0, 0.88 * life)
            glBegin(GL_QUADS)
            for uv, vertex in zip(uvs, vertices):
                glTexCoord2f(float(uv[0]), float(uv[1]))
                glVertex3f(*(vertex[i] - cam[i] for i in range(3)))
            glEnd()
            rendered += 1
        return rendered
    except Exception:
        return 0
    finally:
        glBindTexture(GL_TEXTURE_2D, old_binding)
        glDepthMask(depth_mask_was)
        glBlendFunc(old_src, old_dst)
        if alpha_was: glEnable(GL_ALPHA_TEST)
        else: glDisable(GL_ALPHA_TEST)
        if blend_was: glEnable(GL_BLEND)
        else: glDisable(GL_BLEND)
        if cull_was: glEnable(GL_CULL_FACE)
        else: glDisable(GL_CULL_FACE)
        if depth_was: glEnable(GL_DEPTH_TEST)
        else: glDisable(GL_DEPTH_TEST)
        if lighting_was: glEnable(GL_LIGHTING)
        else: glDisable(GL_LIGHTING)
        if texture_was: glEnable(GL_TEXTURE_2D)
        else: glDisable(GL_TEXTURE_2D)
        glColor4f(1.0, 1.0, 1.0, 1.0)


def preview():
    import pygame

    pygame.init()
    screen = pygame.display.set_mode((512, 512))
    pygame.display.set_caption("St. Elmo's Fire")
    clock = pygame.time.Clock()
    grid = empty_grid()
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
        grid = update_electricity(grid)
        screen.fill((0, 0, 0))
        cell = 512 // GRID_W
        for y in range(GRID_H):
            for x in range(GRID_W):
                color = get_color(grid[y][x])
                if color != (0, 0, 0):
                    pygame.draw.rect(screen, color,
                                     (x * cell, y * cell, cell, cell))
        pygame.display.flip()
        clock.tick(20)
    pygame.quit()


if __name__ == "__main__":
    preview()
