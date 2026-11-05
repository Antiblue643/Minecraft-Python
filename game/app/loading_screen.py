
from OpenGL.GL import *

from engine.textures import Texture
from game.app.scaling import gui_scale

MOJANG_TEXTURE_PATH = "game/textures/title/mojang.png"
LOGO_SIZE = 256

_texture = None


def _java_half(value):
    return int(value / 2)


def logo_position(width, height):
    x = _java_half(width // 2 - LOGO_SIZE)
    y = _java_half(height // 2 - LOGO_SIZE)
    return x, y


def _get_texture():
    global _texture
    if _texture is None:
        _texture = Texture(MOJANG_TEXTURE_PATH, repeat_mode=GL_CLAMP)
    return _texture


def draw_mojang_loading_screen(display):
    tex = _get_texture()
    w, h = display.width, display.height
    scale = gui_scale(w, h)

    glClearColor(0.0, 0.0, 0.0, 0.0)
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
    display.setup_2d_projection()

    glDisable(GL_LIGHTING)
    glDisable(GL_FOG)
    glDisable(GL_BLEND)
    glDisable(GL_ALPHA_TEST)
    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, tex.texture_id)
    glColor4f(1.0, 1.0, 1.0, 1.0)

    u = 0.5 / max(tex.width, 1)
    v = 1.0 - 0.5 / max(tex.height, 1)
    glBegin(GL_QUADS)
    glTexCoord2f(u, v); glVertex2f(0, h)
    glTexCoord2f(u, v); glVertex2f(w, h)
    glTexCoord2f(u, v); glVertex2f(w, 0)
    glTexCoord2f(u, v); glVertex2f(0, 0)
    glEnd()

    x, y = logo_position(w, h)
    glPushMatrix()
    glScalef(scale, scale, 1.0)
    glBegin(GL_QUADS)
    glTexCoord2f(0.0, 0.0); glVertex2f(x, y + LOGO_SIZE)
    glTexCoord2f(1.0, 0.0); glVertex2f(x + LOGO_SIZE, y + LOGO_SIZE)
    glTexCoord2f(1.0, 1.0); glVertex2f(x + LOGO_SIZE, y)
    glTexCoord2f(0.0, 1.0); glVertex2f(x, y)
    glEnd()
    glPopMatrix()

    glBindTexture(GL_TEXTURE_2D, 0)
    glDisable(GL_TEXTURE_2D)
    display.update()
