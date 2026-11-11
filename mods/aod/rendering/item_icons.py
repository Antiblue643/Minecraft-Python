from ..common import MOD_DIR, ITEM_ID

_ITEM_TEXTURE = None

def _item_png_path():
    return MOD_DIR / "item.png"

def get_item_texture():
    global _ITEM_TEXTURE
    if _ITEM_TEXTURE is not None:
        return _ITEM_TEXTURE if getattr(_ITEM_TEXTURE, "texture_id", None) else None
    path = _item_png_path()
    if not path.is_file():
        return None
    try:
        from engine.textures import Texture
        from OpenGL.GL import GL_CLAMP_TO_EDGE
        _ITEM_TEXTURE = Texture(str(path), repeat_mode=GL_CLAMP_TO_EDGE)
    except Exception:
        _ITEM_TEXTURE = None
        return None
    return _ITEM_TEXTURE if getattr(_ITEM_TEXTURE, "texture_id", None) else None

def _draw_custom_item_quad(x, y, size, texture):
    from OpenGL.GL import (
        GL_QUADS, GL_TEXTURE_2D, glBegin, glEnd, glEnable, glTexCoord2f, glVertex2f,
        glColor4f,
    )
    glEnable(GL_TEXTURE_2D)
    texture.bind()
    glColor4f(1.0, 1.0, 1.0, 1.0)
    glBegin(GL_QUADS)
    glTexCoord2f(0.0, 0.0); glVertex2f(x, y + size)
    glTexCoord2f(1.0, 0.0); glVertex2f(x + size, y + size)
    glTexCoord2f(1.0, 1.0); glVertex2f(x + size, y)
    glTexCoord2f(0.0, 1.0); glVertex2f(x, y)
    glEnd()
    texture.unbind()

def patch_gui_item_icons():
    try:
        from game.gameplay import gui as gui_mod
    except Exception:
        return
    target = None
    for name in dir(gui_mod):
        obj = getattr(gui_mod, name)
        if isinstance(obj, type) and hasattr(obj, "_draw_item_icon_impl"):
            target = obj
            break
    if target is None:
        return
    if getattr(target._draw_item_icon_impl, "_corruptor_patched", False):
        return
    original = target._draw_item_icon_impl

    def _draw_item_icon_impl(self, item, x, y, size=16):
        if item is not None and getattr(item, "id", None) == ITEM_ID:
            tex = get_item_texture()
            if tex is not None:
                from OpenGL.GL import (
                    GL_BLEND, GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_TEXTURE_2D,
                    GL_ALPHA_TEST, GL_GREATER, glEnable, glBlendFunc, glAlphaFunc,
                    glColor4f,
                )
                glEnable(GL_BLEND)
                glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
                glEnable(GL_TEXTURE_2D)
                glEnable(GL_ALPHA_TEST)
                glAlphaFunc(GL_GREATER, 0.01)
                glColor4f(1.0, 1.0, 1.0, 1.0)
                _draw_custom_item_quad(x, y, size, tex)
                draw_bar = getattr(self, "_draw_durability_bar", None)
                if callable(draw_bar):
                    draw_bar(item, x, y, size)
                return
        return original(self, item, x, y, size)

    _draw_item_icon_impl._corruptor_patched = True
    target._draw_item_icon_impl = _draw_item_icon_impl

def patch_held_item_draw():
    try:
        import game.entities.player_model as pm
    except Exception:
        return
    original = getattr(pm, "_draw_held_sprite", None)
    if original is None or getattr(original, "_corruptor_patched", False):
        return

    def _draw_held_sprite(held, brightness=1.0):
        if held is not None and getattr(held, "id", None) == ITEM_ID:
            tex = get_item_texture()
            if tex is not None:
                try:
                    from OpenGL.GL import (
                        GL_TEXTURE_2D, GL_ALPHA_TEST, GL_GREATER,
                        glEnable, glAlphaFunc, glColor4f, glTranslatef, glScalef,
                        glRotatef,
                    )
                    glEnable(GL_TEXTURE_2D)
                    tex.bind()
                    glEnable(GL_ALPHA_TEST)
                    glAlphaFunc(GL_GREATER, 0.1)
                    glColor4f(brightness, brightness, brightness, 1.0)
                    glTranslatef(-0.0, -0.3, 0.0)
                    glScalef(1.5, 1.5, 1.5)
                    glRotatef(50.0, 0.0, 1.0, 0.0)
                    glRotatef(335.0, 0.0, 0.0, 1.0)
                    glTranslatef(-(15.0 / 16.0), -(1.0 / 16.0), 0.0)
                    pm._draw_item_sprite(0.0, 0.0, 1.0, 1.0)
                    tex.unbind()
                    return
                except Exception:
                    pass
        return original(held, brightness)

    _draw_held_sprite._corruptor_patched = True
    pm._draw_held_sprite = _draw_held_sprite

def patch_ground_item_billboard():
    try:
        from game.entities.item_model import ItemRendererMixin
    except Exception:
        return
    if not hasattr(ItemRendererMixin, "_draw_billboard"):
        return
    if getattr(ItemRendererMixin._draw_billboard, "_corruptor_patched", False):
        return
    original = ItemRendererMixin._draw_billboard

    def _draw_billboard(self, x, y, z, copies, rng):
        item = getattr(self, "item", None)
        if item is not None and getattr(item, "id", None) == ITEM_ID:
            tex = get_item_texture()
            if tex is not None:
                try:
                    from OpenGL.GL import (
                        GL_TEXTURE_2D, GL_QUADS, glEnable, glDisable, glPushMatrix,
                        glPopMatrix, glTranslatef, glRotatef, glBegin, glEnd,
                        glTexCoord2f, glVertex3f, glNormal3f, glColor3f,
                    )
                    from game.entities.entity import Entity
                    s = 0.25
                    renderer = Entity.renderer
                    angle = float(getattr(renderer, "yaw", 0.0)) if renderer else 0.0
                    b = float(getattr(self, "_draw_brightness", 1.0))
                    tex.bind()
                    glEnable(GL_TEXTURE_2D)
                    glColor3f(b, b, b)
                    for copy in range(copies):
                        glPushMatrix()
                        if renderer is not None:
                            ox, oy, oz = renderer.camera_relative_offset(x, y, z)
                        else:
                            ox, oy, oz = float(x), float(y), float(z)
                        glTranslatef(ox, oy, oz)
                        if copy > 0:
                            glTranslatef(
                                (rng.random() * 2.0 - 1.0) * 0.15,
                                (rng.random() * 2.0 - 1.0) * 0.15,
                                (rng.random() * 2.0 - 1.0) * 0.15,
                            )
                        glRotatef(angle, 0, 1, 0)
                        glBegin(GL_QUADS)
                        glNormal3f(0, 0, 1)
                        glTexCoord2f(0.0, 0.0); glVertex3f(-s, 0, 0)
                        glTexCoord2f(1.0, 0.0); glVertex3f(s, 0, 0)
                        glTexCoord2f(1.0, 1.0); glVertex3f(s, s * 2, 0)
                        glTexCoord2f(0.0, 1.0); glVertex3f(-s, s * 2, 0)
                        glEnd()
                        glPopMatrix()
                    glDisable(GL_TEXTURE_2D)
                    tex.unbind()
                    return
                except Exception:
                    pass
        return original(self, x, y, z, copies, rng)

    _draw_billboard._corruptor_patched = True
    ItemRendererMixin._draw_billboard = _draw_billboard

def initialize():
    patch_gui_item_icons()
    patch_held_item_draw()
    patch_ground_item_billboard()
