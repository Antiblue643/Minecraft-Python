
from __future__ import annotations

import math
import random

GRID_W = 16
GRID_H = 16
TEXTURE_SCALE = 8
TEXTURE_WIDTH = GRID_W * TEXTURE_SCALE
TEXTURE_HEIGHT = GRID_H * TEXTURE_SCALE


def empty_electricity_grid():
    return [[0.0 for _ in range(GRID_W)] for _ in range(GRID_H)]


def update_electricity_grid(current_grid, *, rng=None):
    rng = rng or random
    try:
        source = [
            [max(0.0, min(1.0, float(current_grid[y][x])))
             for x in range(GRID_W)]
            for y in range(GRID_H)
        ]
    except (TypeError, ValueError, IndexError):
        source = empty_electricity_grid()
    grid = [[value * 0.35 for value in row] for row in source]

    center_x = GRID_W // 2
    for y in range(GRID_H):
        wiggle = rng.choice((-1, 0, 1)) if rng.random() < 0.4 else 0
        target_x = max(center_x - 3, min(center_x + 3, center_x + wiggle))
        grid[y][target_x] = max(grid[y][target_x], rng.uniform(0.8, 1.0))

    center_y = GRID_H // 2
    for x in range(GRID_W):
        wiggle = rng.choice((-1, 0, 1)) if rng.random() < 0.4 else 0
        target_y = max(center_y - 3, min(center_y + 3, center_y + wiggle))
        grid[target_y][x] = max(grid[target_y][x], rng.uniform(0.8, 1.0))

    for y in range(GRID_H):
        for x in range(GRID_W):
            charge = source[y][x]
            if charge <= 0.4:
                continue
            up_y = (y - 1) % GRID_H
            down_y = (y + 1) % GRID_H
            targets = (
                (up_y, (x - 1) % GRID_W),
                (up_y, (x + 1) % GRID_W),
                (down_y, (x - 1) % GRID_W),
                (down_y, (x + 1) % GRID_W),
                (y, (x - 1) % GRID_W),
                (y, (x + 1) % GRID_W),
            )
            for ty, tx in targets:
                if rng.random() < 0.18:
                    branch_charge = charge * rng.uniform(0.5, 0.75)
                    grid[ty][tx] = min(1.0, grid[ty][tx] + branch_charge)

    for y in range(GRID_H):
        for x in range(GRID_W):
            if source[y][x] > 0.05:
                grid[y][x] = min(1.0, grid[y][x] + rng.random() * 0.04)
    return grid


def get_color(intensity):
    intensity = max(0.0, min(1.0, float(intensity)))
    if intensity <= 0.05:
        return (0, 0, 0)
    if intensity < 0.4:
        return (int(intensity * 120), 0, int(intensity * 255))
    if intensity < 0.75:
        return (0, int(intensity * 220), 255)
    return (220, 255, 255)


def generate_texture_rgba(grid, *, scale=TEXTURE_SCALE):
    scale = max(1, int(scale))
    width, height = GRID_W * scale, GRID_H * scale
    pixels = bytearray(width * height * 4)
    for gy in range(GRID_H):
        for gx in range(GRID_W):
            intensity = max(0.0, min(1.0, float(grid[gy][gx])))
            red, green, blue = get_color(intensity)
            alpha = 0 if intensity <= 0.05 else min(255, int(255 * min(1.0, intensity * 1.35)))
            if alpha <= 0:
                continue
            for oy in range(scale):
                row = (gy * scale + oy) * width * 4
                for ox in range(scale):
                    offset = row + (gx * scale + ox) * 4
                    pixels[offset:offset + 4] = bytes((red, green, blue, alpha))
    return width, height, bytes(pixels)


def _normalize(vector):
    length = math.sqrt(sum(float(v) * float(v) for v in vector))
    if length < 1.0e-8:
        return (0.0, 0.0, 0.0)
    return tuple(float(v) / length for v in vector)


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _add_scaled(point, vector, scale):
    return tuple(point[i] + vector[i] * scale for i in range(3))


def _quad_for_segment(start, end, side, width):
    return (
        _add_scaled(start, side, -width),
        _add_scaled(start, side, width),
        _add_scaled(end, side, width),
        _add_scaled(end, side, -width),
    )


def draw_electricity_segments(world, camera_pos, *, partial=0.0):
    segments = getattr(world, "_aod_tesla_arcs", None)
    if not isinstance(segments, list) or not segments:
        return 0
    grid = getattr(world, "_aod_tesla_electricity_grid", None)
    if not isinstance(grid, list):
        return 0

    try:
        from OpenGL.GL import (
            GL_ALPHA_TEST, GL_BLEND, GL_BLEND_DST, GL_BLEND_SRC, GL_DEPTH_TEST,
            GL_DEPTH_WRITEMASK, GL_LIGHTING, GL_NEAREST, GL_ONE, GL_QUADS,
            GL_RGBA, GL_SRC_ALPHA, GL_TEXTURE_2D, GL_TEXTURE_BINDING_2D,
            GL_TEXTURE_MAG_FILTER, GL_TEXTURE_MIN_FILTER, GL_TEXTURE_WRAP_S,
            GL_TEXTURE_WRAP_T, GL_UNSIGNED_BYTE, GL_CLAMP_TO_EDGE,
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

    texture_id = int(getattr(world, "_aod_tesla_texture_id", 0) or 0)
    old_binding = int(glGetIntegerv(GL_TEXTURE_BINDING_2D))
    blend_was = bool(glIsEnabled(GL_BLEND))
    depth_was = bool(glIsEnabled(GL_DEPTH_TEST))
    lighting_was = bool(glIsEnabled(GL_LIGHTING))
    texture_was = bool(glIsEnabled(GL_TEXTURE_2D))
    alpha_was = bool(glIsEnabled(GL_ALPHA_TEST))
    try:
        depth_mask_was = bool(glGetBooleanv(GL_DEPTH_WRITEMASK))
    except Exception:
        depth_mask_was = True
    try:
        old_blend_src = int(glGetIntegerv(GL_BLEND_SRC))
        old_blend_dst = int(glGetIntegerv(GL_BLEND_DST))
    except Exception:
        old_blend_src, old_blend_dst = GL_SRC_ALPHA, GL_ONE

    try:
        if not texture_id:
            texture_id = int(glGenTextures(1))
            world._aod_tesla_texture_id = texture_id
            world._aod_tesla_texture_upload_revision = -1
        glBindTexture(GL_TEXTURE_2D, texture_id)
        revision = int(getattr(world, "_aod_tesla_texture_revision", 0))
        uploaded = int(getattr(world, "_aod_tesla_texture_upload_revision", -1))
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
            world._aod_tesla_texture_upload_revision = revision

        glEnable(GL_TEXTURE_2D)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)
        glEnable(GL_DEPTH_TEST)
        glDepthMask(False)
        glDisable(GL_LIGHTING)
        glDisable(GL_ALPHA_TEST)

        rendered = 0
        for segment in segments:
            try:
                start = tuple(float(v) for v in segment["start"])
                end = tuple(float(v) for v in segment["end"])
                remaining = max(0.0, float(segment.get("remaining", 0.0)))
                lifetime = max(1.0e-6, float(segment.get("lifetime", 0.2)))
            except (KeyError, TypeError, ValueError):
                continue
            life = max(0.0, min(1.0, remaining / lifetime))
            if life <= 0.0:
                continue
            direction = _normalize(tuple(end[i] - start[i] for i in range(3)))
            if direction == (0.0, 0.0, 0.0):
                continue
            midpoint = tuple((start[i] + end[i]) * 0.5 for i in range(3))
            view = _normalize(tuple(cam[i] - midpoint[i] for i in range(3)))
            side_a = _normalize(_cross(direction, view))
            if side_a == (0.0, 0.0, 0.0):
                side_a = _normalize(_cross(direction, (0.0, 1.0, 0.0)))
            if side_a == (0.0, 0.0, 0.0):
                side_a = _normalize(_cross(direction, (1.0, 0.0, 0.0)))
            side_b = _normalize(_cross(direction, side_a))
            width = 0.22 if segment.get("kind") == "conductor" else 0.28
            alpha = life * (0.82 if segment.get("kind") == "conductor" else 0.95)
            glColor4f(1.0, 1.0, 1.0, alpha)
            for side in (side_a, side_b):
                vertices = _quad_for_segment(start, end, side, width)
                glBegin(GL_QUADS)
                glTexCoord2f(0.0, 0.0); glVertex3f(*(vertices[0][i] - cam[i] for i in range(3)))
                glTexCoord2f(1.0, 0.0); glVertex3f(*(vertices[1][i] - cam[i] for i in range(3)))
                glTexCoord2f(1.0, 1.0); glVertex3f(*(vertices[2][i] - cam[i] for i in range(3)))
                glTexCoord2f(0.0, 1.0); glVertex3f(*(vertices[3][i] - cam[i] for i in range(3)))
                glEnd()
            rendered += 1
        return rendered
    except Exception:
        return 0
    finally:
        glBindTexture(GL_TEXTURE_2D, old_binding)
        glDepthMask(depth_mask_was)
        glBlendFunc(old_blend_src, old_blend_dst)
        if alpha_was: glEnable(GL_ALPHA_TEST)
        else: glDisable(GL_ALPHA_TEST)
        if blend_was: glEnable(GL_BLEND)
        else: glDisable(GL_BLEND)
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
    pygame.display.set_caption("AOD procedural electricity texture")
    clock = pygame.time.Clock()
    grid = empty_electricity_grid()
    rng = random.Random()
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
        grid = update_electricity_grid(grid, rng=rng)
        screen.fill((0, 0, 0))
        cell = 512 // GRID_W
        for y in range(GRID_H):
            for x in range(GRID_W):
                color = get_color(grid[y][x])
                if color != (0, 0, 0):
                    pygame.draw.rect(screen, color, (x * cell, y * cell, cell, cell))
        pygame.display.flip()
        clock.tick(20)
    pygame.quit()


if __name__ == "__main__":
    preview()
