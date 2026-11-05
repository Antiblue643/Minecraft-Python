
import pygame as pg
import ctypes
import ctypes.util
import os
import time
from array import array
import numpy as np
from ctypes import c_void_p
from OpenGL.GL import *
from OpenGL.GL import (
    GL_ARRAY_BUFFER, GL_STREAM_DRAW, GL_FLOAT,
    GL_VERTEX_ARRAY, GL_TEXTURE_COORD_ARRAY, GL_COLOR_ARRAY,
    glGenBuffers, glBindBuffer, glBufferData, glDrawArrays,
    glEnableClientState, glDisableClientState,
    glVertexPointer, glTexCoordPointer, glColorPointer,
)
from OpenGL.GLU import *

from engine.anaglyph import anaglyph_rgb, anaglyph_surface

NATIVE_WIDTH = 1067
NATIVE_HEIGHT = 600

_FONT_PATH = "game/textures/default.png"  
_GLYPH_PX = 8
_ATLAS_COLS = 16
_ATLAS_ROWS = 16

_CHAR_WIDTHS = [
    4, 8, 8, 7, 7, 7, 7, 6, 8, 7, 8, 8, 7, 8, 8, 8,  
    7, 7, 7, 7, 8, 8, 7, 8, 7, 7, 7, 7, 7, 8, 8, 8,  
    4, 1, 4, 5, 5, 6, 6, 2, 4, 4, 7, 5, 1, 5, 1, 5,  
    5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 1, 1, 4, 5, 4, 5,  
    6, 5, 5, 5, 5, 5, 5, 5, 5, 3, 5, 5, 5, 5, 5, 5,  
    5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 3, 5, 3, 5, 5,  
    2, 5, 5, 5, 5, 5, 4, 5, 5, 1, 5, 4, 2, 5, 5, 5,  
    5, 5, 5, 5, 3, 5, 5, 5, 5, 5, 5, 4, 1, 4, 6, 5,  
    6, 7, 6, 8, 7, 7, 7, 6, 8, 6, 6, 6, 7, 5, 7, 6,  
    6, 8, 7, 6, 6, 6, 7, 7, 6, 8, 6, 7, 7, 6, 8, 8,  
    7, 5, 6, 7, 6, 6, 7, 6, 6, 6, 6, 8, 8, 5, 8, 8,  
    7, 8, 8, 5, 5, 5, 7, 7, 5, 7, 7, 7, 7, 7, 5, 5,  
    8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8,  
    8, 8, 8, 8, 8, 8, 8, 8, 8, 5, 8, 8, 8, 4, 8, 8,  
    7, 6, 6, 7, 6, 7, 7, 7, 6, 7, 7, 6, 8, 8, 5, 6,  
    6, 6, 6, 6, 8, 5, 6, 7, 6, 5, 5, 8, 6, 5, 6, 4,  
]


_CP437_GRAPHIC_GLYPHS = (
    "\0☺☻♥♦♣♠•◘○◙♂♀♪♫☼"
    "►◄↕‼¶§▬↨↑↓→←∟↔▲▼"
)
_CP437_GRAPHIC_CODES = {
    char: code for code, char in enumerate(_CP437_GRAPHIC_GLYPHS) if code
}
_CP437_GRAPHIC_CODES["⌂"] = 0x7F

_SECTION_COLORS = {
    "0": (0x00, 0x00, 0x00),
    "1": (0x00, 0x00, 0xAA),
    "2": (0x00, 0xAA, 0x00),
    "3": (0x00, 0xAA, 0xAA),
    "4": (0xAA, 0x00, 0x00),
    "5": (0xAA, 0x00, 0xAA),
    "6": (0xFF, 0xAA, 0x00),
    "7": (0xAA, 0xAA, 0xAA),
    "8": (0x55, 0x55, 0x55),
    "9": (0x55, 0x55, 0xFF),
    "a": (0x55, 0xFF, 0x55),
    "b": (0x55, 0xFF, 0xFF),
    "c": (0xFF, 0x55, 0x55),
    "d": (0xFF, 0x55, 0xFF),
    "e": (0xFF, 0xFF, 0x55),
    "f": (0xFF, 0xFF, 0xFF),
}


def _font_glyph_code(char):
    codepoint = ord(char)
    if codepoint < 0x20 or 0x80 <= codepoint <= 0x9F or codepoint == 0x7F:
        return codepoint
    graphic_code = _CP437_GRAPHIC_CODES.get(char)
    if graphic_code is not None:
        return graphic_code
    try:
        return char.encode("cp437")[0]
    except UnicodeEncodeError:
        return None


def _iter_formatted_chars(text, base_color):
    color = base_color
    index = 0
    while index < len(text):
        if text[index] == "§" and index + 1 < len(text):
            code = text[index + 1].lower()
            if code in _SECTION_COLORS:
                color = _SECTION_COLORS[code]
                index += 2
                continue
            if code == "r":
                color = base_color
                index += 2
                continue
        yield text[index], color
        index += 1


class Display:
    def __init__(self):
        pg.init()
        self.width = 1067
        self.height = 600
        try:
            icon = pg.image.load("engine/resources/mcpyicon.png")
            pg.display.set_icon(icon)
        except Exception:
            pass
        pg.display.set_caption("Minecraft Python")
        self.fullscreen = False
        try:
            pg.display.gl_set_attribute(pg.GL_SWAP_CONTROL, 0)
        except Exception:
            pass
        try:
            self.surface = pg.display.set_mode(
                (self.width, self.height),
                pg.OPENGL | pg.DOUBLEBUF | pg.RESIZABLE,
                vsync=0,
            )
        except TypeError:
            self.surface = pg.display.set_mode(
                (self.width, self.height), pg.OPENGL | pg.DOUBLEBUF | pg.RESIZABLE
            )
        self.vsync_active = False
        self._swap_interval_available = self.set_vsync(True)

        glViewport(0, 0, self.width, self.height)
        glEnable(GL_DEPTH_TEST)
        self.clock = pg.time.Clock()
        self.frame = 0
        self._last_flip_ms = 0.0
        self._last_clock_tick_ms = 0.0

        self.font = pg.font.SysFont("monospace", 12)

        self._font_tex = None
        self._text_vbo = None
        self._font_tex_w = 128
        self._font_tex_h = 128
        self._anaglyph_textures = None
        self._anaglyph_size = None
        self._anaglyph_enabled = False
        self._load_bitmap_font()

    def set_vsync(self, enabled):
        interval = 1 if enabled else 0
        candidates = []
        try:
            libname = ctypes.util.find_library("SDL2")
            if libname:
                candidates.append(libname)
        except Exception:
            pass
        try:
            candidates.append(pg.base.__file__)
        except Exception:
            pass
        package_dir = os.path.dirname(getattr(pg, "__file__", ""))
        for filename in ("SDL2.dll", "libSDL2-2.0.so.0", "libSDL2.dylib"):
            candidate = os.path.join(package_dir, filename)
            if os.path.exists(candidate):
                candidates.append(candidate)

        for candidate in candidates:
            try:
                library = ctypes.CDLL(candidate)
                setter = library.SDL_GL_SetSwapInterval
                setter.argtypes = [ctypes.c_int]
                setter.restype = ctypes.c_int
                if setter(interval) == 0:
                    self.vsync_active = bool(enabled)
                    self._swap_interval_available = True
                    return True
            except Exception:
                continue

        self.vsync_active = False
        self._swap_interval_available = False
        return False

    def _load_bitmap_font(self):
        from game.app.texturepacks import resolve_texture_path

        resolved_path = resolve_texture_path(_FONT_PATH)
        try:
            surf = pg.image.load(resolved_path).convert_alpha()
        except Exception as e:
            print(f"Warning: could not load bitmap font {resolved_path}: {e}")
            return False

        font_width, font_height = surf.get_size()
        if self._anaglyph_enabled:
            surf = anaglyph_surface(surf)
        data = pg.image.tostring(surf, "RGBA", True)
        old_font_tex = self._font_tex

        new_font_tex = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, new_font_tex)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexImage2D(
            GL_TEXTURE_2D, 0, GL_RGBA,
            font_width, font_height, 0,
            GL_RGBA, GL_UNSIGNED_BYTE, data,
        )
        glBindTexture(GL_TEXTURE_2D, 0)
        self._font_tex = new_font_tex
        self._font_tex_w, self._font_tex_h = font_width, font_height
        if old_font_tex:
            glDeleteTextures([old_font_tex])
        return True

    def reload_bitmap_font(self):
        return self._load_bitmap_font()

    def set_anaglyph_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled == self._anaglyph_enabled:
            return False
        self._anaglyph_enabled = enabled
        return self._load_bitmap_font()

    def setup_2d_projection(self):
        glViewport(0, 0, self.width, self.height)
        glDisable(GL_DEPTH_TEST)
        glDisable(GL_CULL_FACE)
        glDisable(GL_FOG)

        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluOrtho2D(0, self.width, self.height, 0)

        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

    @property
    def aspect(self):
        if self.height <= 0:
            return NATIVE_WIDTH / NATIVE_HEIGHT
        return self.width / self.height

    def get_width(self):
        return self.width

    def get_height(self):
        return self.height

    def draw_pixel(self, pos, color):
        glBegin(GL_POINTS)
        glColor3f(*color)
        glVertex2f(*pos)
        glEnd()

    def draw_line(self, start, end, color):
        glBegin(GL_LINES)
        glColor3f(*color)
        glVertex2f(*start)
        glVertex2f(*end)
        glEnd()

    def draw_rect(self, pos, size, color):
        x, y = pos
        w, h = size
        glBegin(GL_QUADS)
        glColor3f(*color)
        glVertex2f(x, y)
        glVertex2f(x + w, y)
        glVertex2f(x + w, y + h)
        glVertex2f(x, y + h)
        glEnd()

    def draw_poly(self, verts, color):
        glBegin(GL_POLYGON)
        glColor3f(*color)
        for vert in verts:
            glVertex2f(*vert)
        glEnd()

    def _norm_rgb(self, color):
        r, g, b = color[0], color[1], color[2]
        if max(r, g, b) > 1.0:
            if getattr(self, "_anaglyph_enabled", False):
                r, g, b = anaglyph_rgb(int(r), int(g), int(b))
                return int(r) / 255.0, int(g) / 255.0, int(b) / 255.0
            return r / 255.0, g / 255.0, b / 255.0
        if getattr(self, "_anaglyph_enabled", False):
            return anaglyph_rgb(float(r), float(g), float(b))
        return float(r), float(g), float(b)

    def _draw_glyph_quad(self, x, y, size, col, row):
        u0 = col / _ATLAS_COLS
        u1 = (col + 1) / _ATLAS_COLS
        v1 = 1.0 - (row / _ATLAS_ROWS)
        v0 = 1.0 - ((row + 1) / _ATLAS_ROWS)

        glTexCoord2f(u0, v0); glVertex2f(x, y + size)
        glTexCoord2f(u1, v0); glVertex2f(x + size, y + size)
        glTexCoord2f(u1, v1); glVertex2f(x + size, y)
        glTexCoord2f(u0, v1); glVertex2f(x, y)

    def draw_string(self, text, pos, color, scale=1.0, shadow=True, alpha=1.0):
        if not text:
            return

        alpha = max(0.0, min(1.0, float(alpha)))
        if self._font_tex is None:
            self._draw_string_ttf(text, pos, color, scale * 0.5, alpha=alpha)
            return

        glyph_size = _GLYPH_PX * scale
        x0, y0 = pos

        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, self._font_tex)
        glEnable(GL_ALPHA_TEST)
        glAlphaFunc(GL_GREATER, 0.1)

        def _emit_string(pen_x, pen_y, is_shadow=False):
            cx = pen_x
            for ch, char_color in _iter_formatted_chars(text, color):
                if is_shadow:
                    glColor4f(0.15, 0.15, 0.15, alpha)
                else:
                    r, g, b = self._norm_rgb(char_color)
                    glColor4f(r, g, b, alpha)
                code = _font_glyph_code(ch)
                if code is None:
                    continue
                col = code % _ATLAS_COLS
                row = code // _ATLAS_COLS
                self._draw_glyph_quad(cx, pen_y, glyph_size, col, row)
                advance = (_CHAR_WIDTHS[code] + 1) * scale
                cx += advance

        if shadow:
            glColor4f(0.15, 0.15, 0.15, alpha)
            glBegin(GL_QUADS)
            _emit_string(x0 + scale, y0 + scale, is_shadow=True)
            glEnd()

        glBegin(GL_QUADS)
        _emit_string(x0, y0)
        glEnd()

        glDisable(GL_ALPHA_TEST)
        glBindTexture(GL_TEXTURE_2D, 0)
        glDisable(GL_TEXTURE_2D)
        glDisable(GL_BLEND)
        glColor4f(1.0, 1.0, 1.0, 1.0)

    def draw_strings(self, entries, shadow=True):
        entries = [
            (str(text), (float(pos[0]), float(pos[1])), color, float(scale))
            for text, pos, color, scale in entries if text
        ]
        if not entries:
            return
        if self._font_tex is None:
            for text, pos, color, scale in entries:
                self._draw_string_ttf(text, pos, color, scale * 0.5)
            return

        vertices = array("f")

        def append_pass(offset):
            for text, (x, y), color, scale in entries:
                glyph_size = _GLYPH_PX * scale
                pen_x = x + (scale if offset else 0.0)
                pen_y = y + (scale if offset else 0.0)
                for ch, char_color in _iter_formatted_chars(text, color):
                    r, g, b = (
                        (0.15, 0.15, 0.15)
                        if offset else self._norm_rgb(char_color)
                    )
                    code = _font_glyph_code(ch)
                    if code is None:
                        continue
                    col = code % _ATLAS_COLS
                    row = code // _ATLAS_COLS
                    u0 = col / _ATLAS_COLS
                    u1 = (col + 1) / _ATLAS_COLS
                    v1 = 1.0 - row / _ATLAS_ROWS
                    v0 = 1.0 - (row + 1) / _ATLAS_ROWS
                    vertices.extend((
                        pen_x, pen_y + glyph_size, u0, v0, r, g, b,
                        pen_x + glyph_size, pen_y + glyph_size, u1, v0, r, g, b,
                        pen_x + glyph_size, pen_y, u1, v1, r, g, b,
                        pen_x, pen_y, u0, v1, r, g, b,
                    ))
                    pen_x += (_CHAR_WIDTHS[code] + 1) * scale

        if shadow:
            append_pass(1)
        append_pass(0)
        if not vertices:
            return

        if not getattr(self, "_text_vbo", None):
            self._text_vbo = int(glGenBuffers(1))

        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, self._font_tex)
        glEnable(GL_ALPHA_TEST)
        glAlphaFunc(GL_GREATER, 0.1)
        glBindBuffer(GL_ARRAY_BUFFER, self._text_vbo)
        vertex_data = np.frombuffer(vertices, dtype=np.float32)
        glBufferData(
            GL_ARRAY_BUFFER, vertex_data.nbytes, vertex_data, GL_STREAM_DRAW,
        )
        glEnableClientState(GL_VERTEX_ARRAY)
        glEnableClientState(GL_TEXTURE_COORD_ARRAY)
        glEnableClientState(GL_COLOR_ARRAY)
        try:
            glVertexPointer(2, GL_FLOAT, 28, c_void_p(0))
            glTexCoordPointer(2, GL_FLOAT, 28, c_void_p(8))
            glColorPointer(3, GL_FLOAT, 28, c_void_p(16))
            glDrawArrays(GL_QUADS, 0, len(vertices) // 7)
        finally:
            glDisableClientState(GL_COLOR_ARRAY)
            glDisableClientState(GL_TEXTURE_COORD_ARRAY)
            glDisableClientState(GL_VERTEX_ARRAY)
            glBindBuffer(GL_ARRAY_BUFFER, 0)
            glDisable(GL_ALPHA_TEST)
            glBindTexture(GL_TEXTURE_2D, 0)
            glDisable(GL_TEXTURE_2D)
            glDisable(GL_BLEND)
            glColor4f(1.0, 1.0, 1.0, 1.0)

    def string_width(self, text, scale=1.0):
        if not text:
            return 0
        total = sum(
            _CHAR_WIDTHS[code] + 1
            for ch, _color in _iter_formatted_chars(text, (255, 255, 255))
            if (code := _font_glyph_code(ch)) is not None
        )
        if not total:
            return 0
        total -= 1
        return int(total * scale)

    def _draw_string_ttf(self, text, pos, color, scale=0.5, alpha=1.0):
        runs = []
        run_text = []
        run_color = color
        for ch, char_color in _iter_formatted_chars(text, color):
            if run_text and char_color != run_color:
                runs.append(("".join(run_text), run_color))
                run_text = []
            run_color = char_color
            run_text.append(ch)
        if run_text:
            runs.append(("".join(run_text), run_color))
        x, y = pos
        for run, run_color in runs:
            self._draw_ttf_run(run, (x, y), run_color, scale, alpha)
            x += self.font.size(run)[0] * scale

    def _draw_ttf_run(self, text, pos, color, scale, alpha):
        color = tuple(int(channel * 255) for channel in self._norm_rgb(color))
        surf = self.font.render(
            text, False,
            (int(color[0]), int(color[1]), int(color[2])),
        )
        w, h = surf.get_size()
        text_data = pg.image.tostring(surf, "RGBA", True)

        glPushMatrix()
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_TEXTURE_2D)
        tex = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, tex)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexImage2D(
            GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0,
            GL_RGBA, GL_UNSIGNED_BYTE, text_data,
        )
        x, y = pos
        W = w * scale
        H = h * -scale
        glColor4f(1.0, 1.0, 1.0, max(0.0, min(1.0, float(alpha))))
        glBegin(GL_QUADS)
        glTexCoord2f(0, 0); glVertex2f(x, y)
        glTexCoord2f(1, 0); glVertex2f(x + W, y)
        glTexCoord2f(1, 1); glVertex2f(x + W, y + H)
        glTexCoord2f(0, 1); glVertex2f(x, y + H)
        glEnd()
        glDeleteTextures([tex])
        glDisable(GL_TEXTURE_2D)
        glDisable(GL_BLEND)
        glColor4f(1.0, 1.0, 1.0, 1.0)
        glPopMatrix()

    def clear(self, color=None):
        if color is None:
            r, g, b = 0.6, 0.8, 1.0
        else:
            r, g, b = color
        glClearColor(float(r), float(g), float(b), 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

    def _ensure_anaglyph_textures(self):
        size = (self.width, self.height)
        if self._anaglyph_textures is None:
            self._anaglyph_textures = [
                int(glGenTextures(1)), int(glGenTextures(1)),
            ]
        if self._anaglyph_size == size:
            return

        for texture in self._anaglyph_textures:
            glBindTexture(GL_TEXTURE_2D, texture)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
            glTexImage2D(
                GL_TEXTURE_2D, 0, GL_RGBA, self.width, self.height, 0,
                GL_RGBA, GL_UNSIGNED_BYTE, None,
            )
        glBindTexture(GL_TEXTURE_2D, 0)
        self._anaglyph_size = size

    def capture_anaglyph_eye(self, eye):
        self._ensure_anaglyph_textures()
        glReadBuffer(GL_BACK)
        glBindTexture(GL_TEXTURE_2D, self._anaglyph_textures[eye])
        glCopyTexSubImage2D(
            GL_TEXTURE_2D, 0, 0, 0, 0, 0, self.width, self.height,
        )
        glBindTexture(GL_TEXTURE_2D, 0)

    def composite_anaglyph(self):
        self._ensure_anaglyph_textures()
        glViewport(0, 0, self.width, self.height)
        glDisable(GL_DEPTH_TEST)
        glDisable(GL_CULL_FACE)
        glDisable(GL_BLEND)
        glDisable(GL_ALPHA_TEST)
        glDisable(GL_FOG)

        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

        for texture, mask in (
            (self._anaglyph_textures[0], (True, False, False, True)),
            (self._anaglyph_textures[1], (False, True, True, True)),
        ):
            glColorMask(*mask)
            glEnable(GL_TEXTURE_2D)
            glBindTexture(GL_TEXTURE_2D, texture)
            glColor4f(1.0, 1.0, 1.0, 1.0)
            glBegin(GL_QUADS)
            glTexCoord2f(0.0, 0.0); glVertex2f(-1.0, -1.0)
            glTexCoord2f(1.0, 0.0); glVertex2f(1.0, -1.0)
            glTexCoord2f(1.0, 1.0); glVertex2f(1.0, 1.0)
            glTexCoord2f(0.0, 1.0); glVertex2f(-1.0, 1.0)
            glEnd()
            glBindTexture(GL_TEXTURE_2D, 0)
            glDisable(GL_TEXTURE_2D)

        glColorMask(True, True, True, True)

    def update(self):
        started = time.perf_counter()
        pg.display.flip()
        self._last_flip_ms = (time.perf_counter() - started) * 1000.0
        started = time.perf_counter()
        self.clock.tick()
        self._last_clock_tick_ms = (time.perf_counter() - started) * 1000.0
        self.frame += 1

    def toggle_fullscreen(self):
        if not pg.display.toggle_fullscreen():
            raise RuntimeError("Could not toggle fullscreen mode")
        self.fullscreen = not self.fullscreen
        self._set_size(pg.display.get_window_size())

    def handle_resize(self, event):
        self._set_size(event.size)

    def _set_size(self, size):
        self.width, self.height = size
        if self.width < 1:
            self.width = 1
        if self.height < 1:
            self.height = 1
        glViewport(0, 0, self.width, self.height)