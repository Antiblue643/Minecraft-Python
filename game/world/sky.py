from __future__ import annotations

import math

from OpenGL.GL import (
    glBegin, glEnd, glVertex3f, glTexCoord2f, glColor3f, glColor4f,
    glEnable, glDisable, glBlendFunc, glDepthMask, glPushMatrix, glPopMatrix,
    glRotatef, glMatrixMode,
    glFogfv, glFogf, glFogi, glHint,
    GL_QUADS, GL_TRIANGLE_FAN, GL_TEXTURE_2D, GL_BLEND, GL_FOG,
    GL_ALPHA_TEST, GL_CULL_FACE, GL_ONE, GL_SRC_ALPHA,
    GL_ONE_MINUS_SRC_ALPHA, GL_SMOOTH, GL_FLAT, glShadeModel,
    GL_MODELVIEW, GL_LINEAR,
    GL_FOG_MODE, GL_FOG_COLOR, GL_FOG_START, GL_FOG_END, GL_FOG_HINT, GL_NICEST,
)
from engine.textures import Texture
from game.world.java_random import JavaRandom
from engine.sky_math import sunrise_fan_vertices

_SUN_PATH = "game/textures/terrain/sun.png"
_MOON_PATH = "game/textures/terrain/moon.png"

_CELESTIAL_RADIUS = 100.0
_STAR_QUADS = None


def _build_stars():
    global _STAR_QUADS
    if _STAR_QUADS is not None:
        return _STAR_QUADS

    rng = JavaRandom(10842)
    quads = []
    for _ in range(1500):
        x = rng.next_float() * 2.0 - 1.0
        y = rng.next_float() * 2.0 - 1.0
        z = rng.next_float() * 2.0 - 1.0
        size = 0.25 + rng.next_float() * 0.25
        dist2 = x * x + y * y + z * z
        if not (0.01 < dist2 < 1.0):
            continue
        inv = 1.0 / math.sqrt(dist2)
        x *= inv
        y *= inv
        z *= inv
        px = x * _CELESTIAL_RADIUS
        py = y * _CELESTIAL_RADIUS
        pz = z * _CELESTIAL_RADIUS
        azimuth = math.atan2(x, z)
        sin_az, cos_az = math.sin(azimuth), math.cos(azimuth)
        polar = math.atan2(math.sqrt(x * x + z * z), y)
        sin_pol, cos_pol = math.sin(polar), math.cos(polar)
        spin = rng.next_double() * math.pi * 2.0
        sin_sp, cos_sp = math.sin(spin), math.cos(spin)

        verts = []
        for corner in range(4):
            u = ((corner & 2) - 1) * size
            v = (((corner + 1) & 2) - 1) * size
            up = u * cos_sp - v * sin_sp
            vp = v * cos_sp + u * sin_sp
            out_y = up * sin_pol
            out_radial = -up * cos_pol
            out_x = out_radial * sin_az - vp * cos_az
            out_z = vp * sin_az + out_radial * cos_az
            verts.append((px + out_x, py + out_y, pz + out_z))
        quads.append(verts)

    _STAR_QUADS = quads
    return _STAR_QUADS


class SkyRenderer:
    def __init__(self):
        self._sun = None
        self._moon = None
        self._stars = None

    def _ensure_textures(self):
        if self._sun is None:
            self._sun = Texture(_SUN_PATH)
        if self._moon is None:
            self._moon = Texture(_MOON_PATH)
        if self._stars is None:
            self._stars = _build_stars()

    def render(self, world, partial_ticks=0.0, renderer=None, aspect=None,
               player=None):
        if bool(getattr(world, "is_hell_world", False)):
            sky_r, sky_g, sky_b = world.get_sky_color(partial_ticks, player=player)
            fog_r, fog_g, fog_b = world.get_fog_color(partial_ticks)
            glFogfv(GL_FOG_COLOR, (fog_r, fog_g, fog_b, 1.0))
            glDisable(GL_TEXTURE_2D)
            glDisable(GL_ALPHA_TEST)
            glDepthMask(False)
            glDisable(GL_CULL_FACE)
            glEnable(GL_FOG)
            glColor3f(sky_r, sky_g, sky_b)
            self._draw_sky_plane(16.0)
            glDisable(GL_FOG)
            glColor3f(sky_r * 0.55, sky_g * 0.18, sky_b * 0.18)
            self._draw_sky_plane(-16.0)
            glEnable(GL_TEXTURE_2D)
            glEnable(GL_ALPHA_TEST)
            glDepthMask(True)
            glEnable(GL_CULL_FACE)
            glEnable(GL_FOG)
            glColor4f(1.0, 1.0, 1.0, 1.0)
            glMatrixMode(GL_MODELVIEW)
            return
        self._ensure_textures()
        sky_r, sky_g, sky_b = world.get_sky_color(partial_ticks, player=player)
        if renderer is not None and getattr(renderer, "fog_color", None) is not None:
            fog_r, fog_g, fog_b = tuple(renderer.fog_color)
            terrain_far = float(getattr(renderer, "far_clip", 128.0))
        else:
            fog_r, fog_g, fog_b = world.get_fog_color(partial_ticks)
            terrain_far = 128.0
        angle = world.get_celestial_angle(partial_ticks)
        fluid_mode = getattr(renderer, "fluid_mode", None) if renderer is not None else None

        glFogfv(GL_FOG_COLOR, (fog_r, fog_g, fog_b, 1.0))
        if fluid_mode is None:
            glFogi(GL_FOG_MODE, GL_LINEAR)
            glFogf(GL_FOG_START, 0.0)
            glFogf(GL_FOG_END, terrain_far * 0.80)
        glHint(GL_FOG_HINT, GL_NICEST)

        glDisable(GL_TEXTURE_2D)
        glDisable(GL_ALPHA_TEST)
        glDepthMask(False)
        glDisable(GL_CULL_FACE)

        glEnable(GL_FOG)
        glColor3f(sky_r, sky_g, sky_b)
        self._draw_sky_plane(16.0)

        glDisable(GL_FOG)

        sunrise_rgba = world.get_sunrise_color(partial_ticks)
        if sunrise_rgba is not None:
            self._draw_sunrise(sunrise_rgba, angle)

        glEnable(GL_TEXTURE_2D)
        glEnable(GL_BLEND)
        glBlendFunc(GL_ONE, GL_ONE)
        glPushMatrix()
        glColor4f(1.0, 1.0, 1.0, 1.0)
        glRotatef(angle * 360.0, 1.0, 0.0, 0.0)

        sun_size = 30.0
        self._sun.bind()
        glBegin(GL_QUADS)
        glTexCoord2f(0.0, 0.0); glVertex3f(-sun_size, _CELESTIAL_RADIUS, -sun_size)
        glTexCoord2f(1.0, 0.0); glVertex3f( sun_size, _CELESTIAL_RADIUS, -sun_size)
        glTexCoord2f(1.0, 1.0); glVertex3f( sun_size, _CELESTIAL_RADIUS,  sun_size)
        glTexCoord2f(0.0, 1.0); glVertex3f(-sun_size, _CELESTIAL_RADIUS,  sun_size)
        glEnd()
        self._sun.unbind()

        moon_size = 20.0
        self._moon.bind()
        glBegin(GL_QUADS)
        glTexCoord2f(1.0, 1.0); glVertex3f(-moon_size, -_CELESTIAL_RADIUS,  moon_size)
        glTexCoord2f(0.0, 1.0); glVertex3f( moon_size, -_CELESTIAL_RADIUS,  moon_size)
        glTexCoord2f(0.0, 0.0); glVertex3f( moon_size, -_CELESTIAL_RADIUS, -moon_size)
        glTexCoord2f(1.0, 0.0); glVertex3f(-moon_size, -_CELESTIAL_RADIUS, -moon_size)
        glEnd()
        self._moon.unbind()

        glDisable(GL_TEXTURE_2D)
        star_b = world.get_star_brightness(partial_ticks)
        if star_b > 0.0:
            glColor4f(star_b, star_b, star_b, star_b)
            glBegin(GL_QUADS)
            for verts in self._stars:
                for vx, vy, vz in verts:
                    glVertex3f(vx, vy, vz)
            glEnd()

        glColor4f(1.0, 1.0, 1.0, 1.0)
        glDisable(GL_BLEND)
        glEnable(GL_ALPHA_TEST)
        glPopMatrix()

        glEnable(GL_FOG)
        glColor3f(
            sky_r * 0.2 + 0.04,
            sky_g * 0.2 + 0.04,
            sky_b * 0.6 + 0.1,
        )
        glDisable(GL_TEXTURE_2D)
        self._draw_sky_plane(-16.0)

        glMatrixMode(GL_MODELVIEW)

        if fluid_mode is None:
            glFogi(GL_FOG_MODE, GL_LINEAR)
            glFogf(GL_FOG_START, terrain_far * 0.25)
            glFogf(GL_FOG_END, terrain_far)
        glFogfv(GL_FOG_COLOR, (fog_r, fog_g, fog_b, 1.0))

        glEnable(GL_TEXTURE_2D)
        glDepthMask(True)
        glEnable(GL_CULL_FACE)
        glEnable(GL_FOG)
        glDisable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glColor4f(1.0, 1.0, 1.0, 1.0)

    @staticmethod
    def _draw_sunrise(rgba, celestial_angle):
        red, green, blue, alpha = rgba
        glDisable(GL_TEXTURE_2D)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glShadeModel(GL_SMOOTH)
        glPushMatrix()
        glRotatef(90.0, 1.0, 0.0, 0.0)
        glRotatef(
            180.0 if float(celestial_angle) > 0.5 else 0.0,
            0.0, 0.0, 1.0,
        )
        glBegin(GL_TRIANGLE_FAN)
        glColor4f(red, green, blue, alpha)
        glVertex3f(0.0, 100.0, 0.0)
        glColor4f(red, green, blue, 0.0)
        for vertex in sunrise_fan_vertices(alpha)[1:]:
            glVertex3f(*vertex)
        glEnd()
        glPopMatrix()
        glShadeModel(GL_FLAT)
        glEnable(GL_TEXTURE_2D)

    @staticmethod
    def _draw_sky_plane(y):
        tile = 64
        extent = 384
        glBegin(GL_QUADS)
        if y >= 0:
            for x in range(-extent, extent + tile, tile):
                for z in range(-extent, extent + tile, tile):
                    glVertex3f(x, y, z)
                    glVertex3f(x + tile, y, z)
                    glVertex3f(x + tile, y, z + tile)
                    glVertex3f(x, y, z + tile)
        else:
            for x in range(-extent, extent + tile, tile):
                for z in range(-extent, extent + tile, tile):
                    glVertex3f(x + tile, y, z)
                    glVertex3f(x, y, z)
                    glVertex3f(x, y, z + tile)
                    glVertex3f(x + tile, y, z + tile)
        glEnd()
