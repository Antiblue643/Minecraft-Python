from __future__ import annotations

from OpenGL.GL import *

from game.rendering.sign_geometry import SIGN_BOARD_BOX, SIGN_STICK_BOX, WALL_SIGN_BOARD_BOX

_SIGN_TEX_LEGACY_PATH = "game/textures/item/sign.png"
_TEX_W = 64.0
_TEX_H = 32.0
_sign_tex_id = None
_sign_tex_pack = None
_sign_tex_path = None


def _bind_sign_texture():
    global _sign_tex_id, _sign_tex_pack, _sign_tex_path

    from game.app.texturepacks import get_active_texture_pack, resolve_texture_path

    active_pack = get_active_texture_pack()
    resolved_path = str(resolve_texture_path(_SIGN_TEX_LEGACY_PATH))
    if (
        _sign_tex_id is None
        or active_pack != _sign_tex_pack
        or resolved_path != _sign_tex_path
    ):
        old_texture = _sign_tex_id
        _sign_tex_id = 0
        _sign_tex_pack = active_pack
        _sign_tex_path = resolved_path
        if old_texture:
            try:
                glDeleteTextures([int(old_texture)])
            except Exception:
                pass
        try:
            import pygame as pg

            surf = pg.image.load(resolved_path).convert_alpha()
            data = pg.image.tostring(surf, "RGBA", True)
            w, h = surf.get_size()
            _sign_tex_id = int(glGenTextures(1))
            glBindTexture(GL_TEXTURE_2D, _sign_tex_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP)
            glTexImage2D(
                GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, data
            )
        except Exception:
            _sign_tex_id = 0
    if _sign_tex_id:
        glBindTexture(GL_TEXTURE_2D, _sign_tex_id)
    return bool(_sign_tex_id)


def _uv_px(u0, v0, u1, v1):
    return (u0 / _TEX_W, 1.0 - v0 / _TEX_H, u1 / _TEX_W, 1.0 - v1 / _TEX_H)


def _emit_face(
    x0, y0, z0, x1, y1, z1, x2, y2, z2, x3, y3, z3,
    u0, v0, u1, v1, *, reverse_winding=False,
):
    vertices = (
        ((x0, y0, z0), (u0, v0)),
        ((x1, y1, z1), (u1, v0)),
        ((x2, y2, z2), (u1, v1)),
        ((x3, y3, z3), (u0, v1)),
    )
    if reverse_winding:
        vertices = (vertices[0], vertices[3], vertices[2], vertices[1])
    for (x, y, z), (u, v) in vertices:
        glTexCoord2f(u, v)
        glVertex3f(x, y, z)


def _draw_model_box(x, y, z, size_x, size_y, size_z, tex_u, tex_v, brightness=1.0):
    x0, y0, z0 = x, y, z
    x1, y1, z1 = x + size_x, y + size_y, z + size_z
    sx, sy, sz = float(size_x), float(size_y), float(size_z)
    tu, tv = float(tex_u), float(tex_v)

    def face_uv(pu, pv, pw, ph):
        return _uv_px(pu, pv, pu + pw, pv + ph)

    b = max(0.0, min(1.0, float(brightness)))
    def shade(factor):
        level = b * factor
        glColor4f(level, level, level, 1.0)

    glBegin(GL_QUADS)
    shade(1.0)
    u0, v0, u1, v1 = face_uv(tu + sz, tv, sx, sz)
    _emit_face(x0, y1, z1, x1, y1, z1, x1, y1, z0, x0, y1, z0, u0, v0, u1, v1)
    shade(0.5)
    u0, v0, u1, v1 = face_uv(tu + sz, tv + sz, sx, sz)
    _emit_face(x0, y0, z0, x1, y0, z0, x1, y0, z1, x0, y0, z1, u0, v0, u1, v1)
    shade(0.6)
    u0, v0, u1, v1 = face_uv(tu, tv + sz, sz, sy)
    _emit_face(
        x0, y0, z1, x0, y0, z0, x0, y1, z0, x0, y1, z1,
        u0, v0, u1, v1, reverse_winding=True,
    )
    shade(0.8)
    u0, v0, u1, v1 = face_uv(tu + sz, tv + sz, sx, sy)
    _emit_face(
        x0, y0, z0, x1, y0, z0, x1, y1, z0, x0, y1, z0,
        u0, v0, u1, v1, reverse_winding=True,
    )
    shade(0.6)
    u0, v0, u1, v1 = face_uv(tu + sz + sx, tv + sz, sz, sy)
    _emit_face(
        x1, y0, z0, x1, y0, z1, x1, y1, z1, x1, y1, z0,
        u0, v0, u1, v1, reverse_winding=True,
    )
    shade(0.8)
    u0, v0, u1, v1 = face_uv(tu + sz + sx + sz, tv + sz, sx, sy)
    _emit_face(
        x1, y0, z1, x0, y0, z1, x0, y1, z1, x1, y1, z1,
        u0, v0, u1, v1, reverse_winding=True,
    )
    glEnd()


def _draw_sign_geometry(brightness=1.0):
    _draw_model_box(*SIGN_BOARD_BOX, brightness=brightness)
    _draw_model_box(*SIGN_STICK_BOX, brightness=brightness)


def _draw_wall_sign_geometry(brightness=1.0):
    _draw_model_box(*WALL_SIGN_BOARD_BOX, brightness=brightness)


def _draw_sign_text(te, display=None):
    lines = getattr(te, "sign_text", None)
    if not lines or display is None:
        return
    font_tex = getattr(display, "_font_tex", None)
    if not font_tex:
        return

    from engine.display import _CHAR_WIDTHS, _font_glyph_code

    cull_was = bool(glIsEnabled(GL_CULL_FACE))
    glPushMatrix()
    try:
        scale = 1.0 / 90.0
        glTranslatef(0.0, 1.0 / 3.0, 7.0 / 150.0)
        glScalef(scale, -scale, scale)

        glEnable(GL_TEXTURE_2D)
        glDisable(GL_CULL_FACE)
        glBindTexture(GL_TEXTURE_2D, font_tex)
        glEnable(GL_ALPHA_TEST)
        glAlphaFunc(GL_GREATER, 0.1)
        glDepthMask(False)
        glColor4f(0.0, 0.0, 0.0, 1.0)

        glBegin(GL_QUADS)
        for i, line in enumerate(lines):
            text = str(line or "")
            if i == getattr(te, "line_being_edited", -1):
                text = "> " + text + " <"
            if not text:
                continue
            width = sum(
                _CHAR_WIDTHS[code] + 1
                for ch in text
                if (code := _font_glyph_code(ch)) is not None
            )
            pen_x = -width / 2.0
            pen_y = i * 10 - 20
            for ch in text:
                code = _font_glyph_code(ch)
                if code is None:
                    continue
                col = code % 16
                row = code // 16
                u0 = col / 16.0
                u1 = (col + 1) / 16.0
                v1 = 1.0 - row / 16.0
                v0 = 1.0 - (row + 1) / 16.0
                gs = 8.0
                z = -0.1
                glTexCoord2f(u0, v0)
                glVertex3f(pen_x, pen_y + gs, z)
                glTexCoord2f(u1, v0)
                glVertex3f(pen_x + gs, pen_y + gs, z)
                glTexCoord2f(u1, v1)
                glVertex3f(pen_x + gs, pen_y, z)
                glTexCoord2f(u0, v1)
                glVertex3f(pen_x, pen_y, z)
                pen_x += _CHAR_WIDTHS[code] + 1
        glEnd()
        glDepthMask(True)
        glColor4f(1.0, 1.0, 1.0, 1.0)
    finally:
        (glEnable if cull_was else glDisable)(GL_CULL_FACE)
        glPopMatrix()


def _draw_sign_model_and_text(
    te, display, yaw_degrees=0.0, brightness=1.0, reverse_front_face=False,
    wall_sign=False,
):
    glRotatef(-yaw_degrees, 0.0, 1.0, 0.0)
    if wall_sign:
        glTranslatef(0.0, -5.0 / 16.0, -7.0 / 16.0)

    texture_was = bool(glIsEnabled(GL_TEXTURE_2D))
    cull_was = bool(glIsEnabled(GL_CULL_FACE))
    alpha_was = bool(glIsEnabled(GL_ALPHA_TEST))
    front_face = int(glGetIntegerv(GL_FRONT_FACE))

    glEnable(GL_TEXTURE_2D)
    if reverse_front_face:
        glFrontFace(GL_CW if front_face == GL_CCW else GL_CCW)
    glEnable(GL_CULL_FACE)
    glEnable(GL_ALPHA_TEST)
    glAlphaFunc(GL_GREATER, 0.1)
    b = max(0.0, min(1.0, float(brightness)))
    glColor4f(b, b, b, 1.0)

    if _bind_sign_texture():
        glPushMatrix()
        try:
            glScalef(1.0 / 24.0, -1.0 / 24.0, -1.0 / 24.0)
            if wall_sign:
                _draw_wall_sign_geometry(b)
            else:
                _draw_sign_geometry(b)
        finally:
            glPopMatrix()

    glColor4f(1.0, 1.0, 1.0, 1.0)
    _draw_sign_text(te, display=display)

    glColor4f(1.0, 1.0, 1.0, 1.0)
    glFrontFace(front_face)
    (glEnable if alpha_was else glDisable)(GL_ALPHA_TEST)
    (glEnable if cull_was else glDisable)(GL_CULL_FACE)
    (glEnable if texture_was else glDisable)(GL_TEXTURE_2D)


def render_standing_sign(world, te, partial=0.0, *, display=None):
    if te is None:
        return
    x, y, z = int(te.x), int(te.y), int(te.z)
    meta = 0
    try:
        if world is not None:
            meta = int(world.get_metadata(x, y, z)) & 15
    except Exception:
        meta = 0
    yaw = meta * 360.0 / 16.0

    renderer = getattr(world, "renderer", None) if world is not None else None
    world_x = float(x)
    world_y = float(y)
    world_z = float(z)
    if renderer is not None and hasattr(renderer, "camera_relative_offset"):
        ox, oy, oz = renderer.camera_relative_offset(world_x, world_y, world_z)
    else:
        ox, oy, oz = world_x, world_y, world_z

    disp = display
    if disp is None and renderer is not None:
        disp = getattr(renderer, "display", None)

    brightness = 1.0
    if world is not None:
        try:
            from engine.lighting import brightness_from_level
            level = world.get_light(x, y, z)
            brightness = float(brightness_from_level(level, hell=bool(getattr(world, "is_hell_world", False))))
        except Exception:
            brightness = 1.0

    glMatrixMode(GL_MODELVIEW)
    glPushMatrix()
    try:
        glTranslatef(ox, oy, oz)
        _draw_sign_model_and_text(te, disp, yaw_degrees=yaw, brightness=brightness)
    finally:
        glPopMatrix()


def render_wall_sign(world, te, partial=0.0, *, display=None):
    del partial
    if te is None:
        return
    x, y, z = int(te.x), int(te.y), int(te.z)
    try:
        meta = int(world.get_metadata(x, y, z)) & 15
    except Exception:
        meta = 2
    yaw = {2: 180.0, 3: 0.0, 4: 90.0, 5: -90.0}.get(meta, 180.0)

    renderer = getattr(world, "renderer", None) if world is not None else None
    world_x = float(x)
    world_y = float(y)
    world_z = float(z)
    if renderer is not None and hasattr(renderer, "camera_relative_offset"):
        ox, oy, oz = renderer.camera_relative_offset(world_x, world_y, world_z)
    else:
        ox, oy, oz = world_x, world_y, world_z
    disp = display if display is not None else getattr(renderer, "display", None)

    brightness = 1.0
    if world is not None:
        try:
            from engine.lighting import brightness_from_level
            brightness = float(brightness_from_level(world.get_light(x, y, z), hell=bool(getattr(world, "is_hell_world", False))))
        except Exception:
            pass

    glMatrixMode(GL_MODELVIEW)
    glPushMatrix()
    try:
        glTranslatef(ox, oy, oz)
        _draw_sign_model_and_text(
            te, disp, yaw_degrees=yaw, brightness=brightness, wall_sign=True,
        )
    finally:
        glPopMatrix()


def render_sign(world, te, partial=0.0, *, display=None):
    try:
        block_type = world.get_block_type(int(te.x), int(te.y), int(te.z))
    except Exception:
        block_type = "sign"
    if block_type == "wall_sign":
        return render_wall_sign(world, te, partial, display=display)
    return render_standing_sign(world, te, partial, display=display)


def draw_all_signs(world, max_dist=64.0):
    renderer = getattr(world, "renderer", None)
    if renderer is None:
        return
    tile_entities = getattr(world, "tile_entities", None)
    if not tile_entities:
        return
    display = getattr(renderer, "display", None)
    cx = float(getattr(renderer, "camX", 0.0))
    cy = float(getattr(renderer, "camY", 0.0))
    cz = float(getattr(renderer, "camZ", 0.0))
    md2 = max_dist * max_dist
    try:
        frustum = world._camera_frustum()
    except Exception:
        frustum = None
    for te in list(tile_entities.values()):
        if not hasattr(te, "sign_text"):
            continue
        try:
            chunk_key = world._get_chunk_index(te.x, te.z)
            if chunk_key not in world.chunks:
                continue
            in_active_view = getattr(world, "_chunk_in_active_view", None)
            if callable(in_active_view) and not in_active_view(*chunk_key):
                continue
            if world.get_block_type(te.x, te.y, te.z) not in ("sign", "wall_sign"):
                continue
        except Exception:
            continue
        dx = float(te.x) - cx
        dy = float(te.y) - cy
        dz = float(te.z) - cz
        if dx * dx + dy * dy + dz * dz > md2:
            continue
        if frustum is not None:
            try:
                if not frustum.intersects_aabb(
                    float(te.x) - 0.5, float(te.y) - 0.5, float(te.z) - 0.5,
                    float(te.x) + 0.5, float(te.y) + 0.5, float(te.z) + 0.5,
                    padding=0.25,
                ):
                    continue
            except Exception:
                pass
        try:
            render_sign(world, te, display=display)
        except Exception:
            pass


def render_sign_in_gui(te, display, width, height, update_counter, edit_line):
    if te is None:
        return
    if (update_counter // 6) % 2 == 0:
        te.line_being_edited = edit_line
    else:
        te.line_being_edited = -1

    glMatrixMode(GL_PROJECTION)
    glPushMatrix()
    glLoadIdentity()
    glOrtho(0.0, float(width), float(height), 0.0, 1000.0, 3000.0)

    glMatrixMode(GL_MODELVIEW)
    glPushMatrix()
    try:
        glLoadIdentity()
        glTranslatef(0.0, 0.0, -2000.0)

        glEnable(GL_DEPTH_TEST)
        glClear(GL_DEPTH_BUFFER_BIT)

        glTranslatef(width * 0.5, height * 0.5, 50.0)
        glScalef(-60.0, -60.0, -60.0)
        glRotatef(180.0, 0.0, 1.0, 0.0)
        glTranslatef(0.0, 12.0 / 16.0 - 0.75, 0.0)
        _draw_sign_model_and_text(
            te, display, yaw_degrees=0.0, reverse_front_face=True
        )

        glDisable(GL_DEPTH_TEST)
        glColor4f(1.0, 1.0, 1.0, 1.0)
    finally:
        glPopMatrix()
        glMatrixMode(GL_PROJECTION)
        glPopMatrix()
        glMatrixMode(GL_MODELVIEW)

    te.line_being_edited = -1
