from __future__ import annotations

import math
import random
import time
from typing import Dict, List, Optional, Tuple

from OpenGL.GL import *
from OpenGL.GLU import gluPerspective


_FONT_5: Dict[str, Tuple[str, ...]] = {
    "A": ("### ",
          "# # ",
          "### ",
          "# # ",
          "# # "),

    "B": ("## ",
          "# #",
          "## ",
          "# #",
          "## "),

    "C": ("###",
          "#  ",
          "#  ",
          "#  ",
          "###"),

    "D": ("## ",
          "# #",
          "# #",
          "# #",
          "## "),

    "E": ("###",
          "#   ",
          "##  ",
          "#   ",
          "###"),

    "F": ("###",
          "#  ",
          "## ",
          "#  ",
          "#  "),

    "G": ("###",
          "#   ",
          "# #",
          "# #",
          "###"),

    "H": ("#   ",
          "#   ",
          "### ",
          "#  #",
          "#  #"),

    "I": ("#",
          "#",
          "#",
          "#",
          "#"),

    "J": ("  #",
          "  #",
          "  #",
          "# #",
          "###"),

    "K": ("#  #",
          "# # ",
          "##  ",
          "# # ",
          "#  #"),

    "L": ("#  ",
          "#  ",
          "#  ",
          "#  ",
          "###"),

    "M": ("#   #",
          "## ##",
          "# # #",
          "#   #",
          "#   #"),

    "N": ("#   #",
          "##  #",
          "# # #",
          "#  ##",
          "#   #"),

    "O": ("###",
          "# #",
          "# #",
          "# #",
          "###"),

    "P": ("###",
          "# #",
          "###",
          "#  ",
          "#  "),

    "Q": ("###",
          "# #",
          "# #",
          "###",
          "  #"),

    "R": ("###",
          "# #",
          "## ",
          "# #",
          "# #"),

    "S": ("###",
          "#  ",
          "###",
          "  #",
          "###"),

    "T": ("###",
          " # ",
          " # ",
          " # ",
          " # "),

    "U": ("# #",
          "# #",
          "# #",
          "# #",
          "###"),

    "V": ("# #",
          "# #",
          "# #",
          "# #",
          " # "),

    "W": ("#   #",
          "#   #",
          "# # #",
          "## ##",
          "#   #"),

    "X": ("# #",
          "# #",
          " # ",
          "# #",
          "# #"),

    "Y": ("# #",
          "# #",
          " ##",
          "  #",
          " # "),

    "Z": ("###",
          "  #",
          " # ",
          "#  ",
          "###"),

    "0": (" # ",
          "# #",
          "# #",
          "# #",
          " # "),

    "1": (" # ",
          "## ",
          " # ",
          " # ",
          "###"),

    "2": ("####",
          "   #",
          "####",
          "#   ",
          "####"),

    "3": ("###",
          "  #",
          " ##",
          "  #",
          "###"),

    "4": ("#  #",
          "#  #",
          "####",
          "   #",
          "   #"),

    "5": ("###",
          "#  ",
          "###",
          "  #",
          "## "),

    "6": ("###",
          "#  ",
          "###",
          "# #",
          "###"),

    "7": ("###",
          "  #",
          " # ",
          "#  ",
          "#  "),

    "8": ("###",
          "# #",
          " # ",
          "# #",
          "###"),

    "9": ("###",
          "# #",
          "###",
          "  #",
          "###"),

    "_": ("    ", "    ", "    ", "    ", "###"),
    "-": ("   ", "   ", "###", "   ", "   "),
    " ": ("  ", "  ", "  ", "  ", "  "),
}

MINECRAFT_LOGO_LAYERS = (
    " *   * * *   * *** *** *** *** *** ***",
    " ** ** * **  * *   *   * * * * *    * ",
    " * * * * * * * **  *   **  *** **   * ",
    " *   * * *  ** *   *   * * * * *    * ",
    " *   * * *   * *** *** * * * * *    * ",
)


_SHADOW_TEXTURE = None


def _shadow_texture():
    global _SHADOW_TEXTURE
    if _SHADOW_TEXTURE is None:
        from engine.textures import Texture

        _SHADOW_TEXTURE = Texture("game/textures/title/black.png", repeat_mode=GL_CLAMP)
    return _SHADOW_TEXTURE


class _BlockEffect:

    __slots__ = ("height", "prev_height", "drop_speed", "removing", "dead")

    def __init__(self, col: int, row: int, rng: random.Random, *, start_high: bool = True):
        if start_high:
            self.height = self.prev_height = (10 + row) + rng.random() * 32.0 + col
            self.drop_speed = 0.0
        else:
            self.height = self.prev_height = 0.0
            self.drop_speed = 0.0
        self.removing = False
        self.dead = False

    def update(self) -> None:
        self.prev_height = self.height
        if self.removing:
            self.drop_speed += 0.8
            self.height += self.drop_speed
            self.drop_speed *= 0.98
            if self.height > 40.0:
                self.dead = True
            return
        if self.height > 0.0:
            self.drop_speed -= 0.6
        self.height += self.drop_speed
        self.drop_speed *= 0.9
        if self.height < 0.0:
            self.height = 0.0
            self.drop_speed = 0.0

    def start_remove(self, rng: random.Random) -> None:
        if not self.removing:
            self.removing = True
            self.drop_speed = max(self.drop_speed, 0.0) + 1.0 + rng.random() * 3.5


class AnimatedBlockText:

    def __init__(
        self,
        *,
        material: str = "stone",
        layers: Optional[Tuple[str, ...]] = None,
        rng: Optional[random.Random] = None,
    ):
        self.material = material
        self.fixed_layers = layers
        self.rng = rng or random.Random()
        self._effects: Dict[Tuple[int, int], _BlockEffect] = {}
        self._layers: List[str] = list(layers) if layers else [" "]
        self._text = ""
        self._last_update = time.perf_counter()
        self._atlas_idx: Optional[int] = None
        self.show_caret = False
        self.caret_visible = True


    @staticmethod
    def _glyph(ch: str) -> Tuple[str, ...]:
        return _FONT_5.get(ch.upper(), _FONT_5[" "])

    def _compose_layers(self, text: str) -> List[str]:
        if not text:
            return [" "]
        glyphs = []
        for ch in text:
            glyph = self._glyph(ch)
            width = max((len(row) for row in glyph), default=0)
            glyph = tuple(row.ljust(width) for row in glyph)
            occupied = [
                col
                for col in range(width)
                if any(row[col] != " " for row in glyph)
            ]
            if occupied:
                left, right = occupied[0], occupied[-1] + 1
                glyph = tuple(row[left:right] for row in glyph)
            glyphs.append(glyph)
        glyph_widths = [max((len(row) for row in glyph), default=0) for glyph in glyphs]
        rows = 5
        out = []
        for r in range(rows):
            parts = []
            for i, (glyph, width) in enumerate(zip(glyphs, glyph_widths)):
                if i:
                    parts.append(" ")  
                parts.append(glyph[r].ljust(width))
            out.append("".join(parts))
        return out

    def set_text(self, text: str, *, animate: bool = True) -> None:
        if self.fixed_layers is not None:
            return
        text = text or ""
        if text == self._text and self._effects:
            return
        new_layers = self._compose_layers(text)
        new_cells = {
            (c, r)
            for r, row in enumerate(new_layers)
            for c, ch in enumerate(row)
            if ch not in (" ",)
        }
        old_cells = set(self._effects.keys())

        for key in old_cells - new_cells:
            effect = self._effects.get(key)
            if effect is not None and not effect.removing:
                if animate:
                    effect.start_remove(self.rng)
                else:
                    effect.dead = True

        for col, row in new_cells:
            existing = self._effects.get((col, row))
            if existing is not None and not existing.removing and not existing.dead:
                continue
            self._effects[(col, row)] = _BlockEffect(
                col, row, self.rng, start_high=animate,
            )
            if not animate:
                self._effects[(col, row)].height = 0.0
                self._effects[(col, row)].prev_height = 0.0

        self._layers = new_layers
        self._text = text

    def set_layers(self, layers: Tuple[str, ...], *, animate: bool = True) -> None:
        self.fixed_layers = layers
        new_layers = list(layers)
        new_cells = {
            (c, r)
            for r, row in enumerate(new_layers)
            for c, ch in enumerate(row)
            if ch not in (" ",)
        }
        old_cells = set(self._effects.keys())
        for key in old_cells - new_cells:
            effect = self._effects.get(key)
            if effect is not None:
                if animate:
                    effect.start_remove(self.rng)
                else:
                    effect.dead = True
        for col, row in new_cells - old_cells:
            self._effects[(col, row)] = _BlockEffect(
                col, row, self.rng, start_high=animate,
            )
            if not animate:
                self._effects[(col, row)].height = 0.0
                self._effects[(col, row)].prev_height = 0.0
        self._layers = new_layers


    def update(self) -> None:
        dead = []
        for key, effect in self._effects.items():
            effect.update()
            if effect.dead:
                dead.append(key)
        for key in dead:
            del self._effects[key]
        self._last_update = time.perf_counter()

    def partial(self) -> float:
        return min(1.0, max(0.0, (time.perf_counter() - self._last_update) / 0.05))


    def _resolve_atlas_index(self) -> int:
        if self._atlas_idx is not None:
            return self._atlas_idx
        from game.block import Block

        Block._load_defs()
        faces = Block._block_defs.get(self.material, {}).get("faces", 1)
        if isinstance(faces, (list, tuple)) and faces:
            self._atlas_idx = int(faces[0] if not isinstance(faces[0], (list, tuple)) else faces[0][0])
        else:
            self._atlas_idx = int(faces) if isinstance(faces, int) else 1
        return self._atlas_idx

    _FACE_SHADE_Y_NEG = 1.0
    _FACE_SHADE_Y_POS = 0.5
    _FACE_SHADE_Z = 0.8
    _FACE_SHADE_X = 0.6

    @classmethod
    def _cube_faces(cls):
        return (
            (cls._FACE_SHADE_Y_NEG, (
                (-0.5, -0.5, -0.5), (0.5, -0.5, -0.5),
                (0.5, -0.5, 0.5), (-0.5, -0.5, 0.5),
            )),
            (cls._FACE_SHADE_Y_POS, (
                (-0.5, 0.5, 0.5), (0.5, 0.5, 0.5),
                (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5),
            )),
            (cls._FACE_SHADE_Z, (
                (0.5, -0.5, -0.5), (-0.5, -0.5, -0.5),
                (-0.5, 0.5, -0.5), (0.5, 0.5, -0.5),
            )),
            (cls._FACE_SHADE_Z, (
                (-0.5, -0.5, 0.5), (0.5, -0.5, 0.5),
                (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5),
            )),
            (cls._FACE_SHADE_X, (
                (-0.5, -0.5, -0.5), (-0.5, -0.5, 0.5),
                (-0.5, 0.5, 0.5), (-0.5, 0.5, -0.5),
            )),
            (cls._FACE_SHADE_X, (
                (0.5, -0.5, 0.5), (0.5, -0.5, -0.5),
                (0.5, 0.5, -0.5), (0.5, 0.5, 0.5),
            )),
        )

    def _draw_cube(self, brightness: float = 1.0, alpha: float = 1.0) -> None:
        from game.block import Block

        Block._load_defs()
        tex = Block._get_texture()
        atlas_idx = self._resolve_atlas_index()
        cols = 16
        tile = 1.0 / cols
        col = atlas_idx % cols
        row = atlas_idx // cols
        u0 = col * tile
        u1 = u0 + tile
        v1 = 1.0 - row * tile
        v0 = v1 - tile
        pad = tile * 0.01
        u0 += pad
        u1 -= pad
        v0 += pad
        v1 -= pad
        corner_uvs = ((u0, v1), (u1, v1), (u1, v0), (u0, v0))

        glEnable(GL_TEXTURE_2D)
        tex.bind()
        b = max(0.0, min(1.0, float(brightness)))
        a = max(0.0, min(1.0, float(alpha)))
        glBegin(GL_QUADS)
        for shade, verts in self._cube_faces():
            glColor4f(b * shade, b * shade, b * shade, a)
            for (vx, vy, vz), (uu, vv) in zip(verts, corner_uvs):
                glTexCoord2f(uu, vv)
                glVertex3f(vx, vy, vz)
        glEnd()
        tex.unbind()

    def _draw_shadow_cube(self, alpha: float = 1.0) -> None:
        tex = _shadow_texture()
        a = max(0.0, min(1.0, float(alpha)))
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, tex.texture_id)
        glEnable(GL_ALPHA_TEST)
        glAlphaFunc(GL_GREATER, 0.1)
        corner_uvs = ((0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0))
        glBegin(GL_QUADS)
        for shade, verts in self._cube_faces():
            glColor4f(shade, shade, shade, a)
            for (vx, vy, vz), (uu, vv) in zip(verts, corner_uvs):
                glTexCoord2f(uu, vv)
                glVertex3f(vx, vy, vz)
        glEnd()
        glDisable(GL_ALPHA_TEST)
        glBindTexture(GL_TEXTURE_2D, 0)

    def draw(
        self,
        display,
        *,
        partial: Optional[float] = None,
        logo_viewport_fraction: float = 120.0,
        extra_scale: float = 1.0,
        gui_width: Optional[int] = None,
        gui_height: Optional[int] = None,
        vertical_center: bool = False,
    ) -> None:
        if not self._effects and not self.show_caret:
            return

        if partial is None:
            partial = self.partial()

        display_w = max(1, int(display.width))
        display_h = max(1, int(display.height))
        gui_w = max(1, int(gui_width or display_w))
        gui_h = max(1, int(gui_height or gui_w))
        scale_factor = max(1, int(round(display_w / max(gui_w, 1))))
        logo_h = min(display_h, int(logo_viewport_fraction * scale_factor))

        n_cols = max((len(row) for row in self._layers), default=1)
        n_rows = max(len(self._layers), 1)
        caret_col = n_cols + 1 if self._text.strip() else 0
        field_cols = max(n_cols, caret_col + (1 if self.show_caret else 0))
        field_rows = max(n_rows, 5)

        ref_w, ref_h = 39.0, 5.0
        width_fit = 0.95 * (ref_w / max(field_cols, 1))
        height_fit = 1.0 * (ref_h / max(field_rows, 1))
        fit = min(width_fit, height_fit) * extra_scale

        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        prev_viewport = glGetIntegerv(GL_VIEWPORT)

        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(70.0, float(display_w) / float(max(logo_h, 1)), 0.05, 100.0)
        if vertical_center:
            y0 = max(0, (display_h - logo_h) // 2)
        else:
            y0 = display_h - logo_h
        glViewport(0, y0, display_w, logo_h)

        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        glDisable(GL_CULL_FACE)
        glEnable(GL_DEPTH_TEST)
        glDepthMask(True)

        for pass_idx in range(3):
            glPushMatrix()
            glTranslatef(0.4, 0.6, -12.0)
            if pass_idx == 0:
                glClear(GL_DEPTH_BUFFER_BIT)
                glTranslatef(0.0, -0.4, 0.0)
                glScalef(0.98, 1.0, 1.0)
                glEnable(GL_BLEND)
                glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            elif pass_idx == 1:
                glDisable(GL_BLEND)
                glClear(GL_DEPTH_BUFFER_BIT)
            else:
                glEnable(GL_BLEND)
                glBlendFunc(GL_SRC_COLOR, GL_ONE)

            glScalef(1.0, -1.0, 1.0)
            glRotatef(15.0, 1.0, 0.0, 0.0)
            glScalef(fit, fit, fit * 0.45)
            glTranslatef(-field_cols * 0.5, -field_rows * 0.5, 0.0)

            if pass_idx != 0:
                from game.block import Block
                Block._load_defs()
                Block._get_texture().bind()

            for (col, row), effect in list(self._effects.items()):
                drop = effect.prev_height + (effect.height - effect.prev_height) * partial
                scale = 1.0
                brightness = 1.0
                z = drop
                if pass_idx == 0:
                    scale = abs(drop) * 0.04 + 1.0
                    brightness = 1.0 / max(scale, 1e-4)
                    z = 0.0
                glPushMatrix()
                glTranslatef(float(col), float(row), float(z))
                glScalef(scale, scale, scale)
                if pass_idx == 0:
                    self._draw_shadow_cube(brightness)
                else:
                    self._draw_cube(brightness)
                glPopMatrix()

            if self.show_caret and self.caret_visible:
                for row in range(field_rows):
                    glPushMatrix()
                    glTranslatef(float(caret_col), float(row), 0.0)
                    if pass_idx == 0:
                        self._draw_shadow_cube(1.0)
                    else:
                        self._draw_cube(1.0)
                    glPopMatrix()

            glPopMatrix()

        glDisable(GL_BLEND)
        glDisable(GL_DEPTH_TEST)
        glDepthMask(True)
        glEnable(GL_CULL_FACE)
        glColor4f(1.0, 1.0, 1.0, 1.0)

        glMatrixMode(GL_PROJECTION)
        glPopMatrix()
        glMatrixMode(GL_MODELVIEW)
        glPopMatrix()
        glViewport(
            int(prev_viewport[0]),
            int(prev_viewport[1]),
            int(prev_viewport[2]),
            int(prev_viewport[3]),
        )
