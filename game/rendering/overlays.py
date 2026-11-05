from OpenGL.GL import *
from OpenGL.GLU import *

from game.block import Block
from game.block.mesh_rules import destroy_stage_tile_uv


def draw_block_outline(pos, world, renderer):
    x, y, z = pos
    pad = 0.002
    selection_boxes = [(-0.5, -0.5, -0.5, 0.5, 0.5, 0.5)]

    if world is not None:
        block = world.get_block(x, y, z)
        if block is not None:
            if hasattr(block, "get_selection_boxes"):
                selection_boxes = block.get_selection_boxes() or []
            else:
                selection_boxes = [block.get_selection_bounds()]

    edges = (
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    )
    wire_vertices = []
    for bounds in selection_boxes:
        minx, miny, minz, maxx, maxy, maxz = (
            bounds[0] - pad, bounds[1] - pad, bounds[2] - pad,
            bounds[3] + pad, bounds[4] + pad, bounds[5] + pad,
        )
        corners = [
            (minx, miny, minz), (maxx, miny, minz),
            (maxx, maxy, minz), (minx, maxy, minz),
            (minx, miny, maxz), (maxx, miny, maxz),
            (maxx, maxy, maxz), (minx, maxy, maxz),
        ]
        for a, b in edges:
            wire_vertices.extend((corners[a], corners[b]))

    if not wire_vertices:
        return

    glPushAttrib(GL_ALL_ATTRIB_BITS)
    try:
        glDisable(GL_TEXTURE_2D)
        glDisable(GL_ALPHA_TEST)
        glDisable(GL_LIGHTING)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_LINE_SMOOTH)
        glHint(GL_LINE_SMOOTH_HINT, GL_NICEST)
        glLineWidth(2.0)
        glDepthMask(False)

        ox, oy, oz = renderer.camera_relative_offset(x, y, z)
        glPushMatrix()
        try:
            glTranslatef(ox, oy, oz)
            glColor4f(0.0, 0.0, 0.0, 0.6)
            glBegin(GL_LINES)
            for vertex in wire_vertices:
                glVertex3f(*vertex)
            glEnd()
        finally:
            glPopMatrix()
    finally:
        glPopAttrib()



def _rail_destroy_plate_verts(meta):
    meta = int(meta) & 15
    if meta in (1, 2, 3, 7):
        corners = ((1, 1), (0, 1), (0, 0), (1, 0))
    elif meta == 8:
        corners = ((0, 1), (0, 0), (1, 0), (1, 1))
    elif meta == 9:
        corners = ((0, 0), (1, 0), (1, 1), (0, 1))
    else:
        corners = ((1, 0), (1, 1), (0, 1), (0, 0))
    heights = [1.0 / 16.0] * 4
    if meta in (2, 4):
        heights[0] += 1.0
        heights[3] += 1.0
    elif meta in (3, 5):
        heights[1] += 1.0
        heights[2] += 1.0
    return [
        (cx - 0.5, h - 0.5, cz - 0.5)
        for (cx, cz), h in zip(corners, heights)
    ]



def _destroy_stage_atlas_uvs(atlas_idx):
    col = int(atlas_idx) % 16
    row = int(atlas_idx) // 16
    tile = 1.0 / 16.0
    inset = tile * (15.99 / 16.0)
    u0 = col * tile
    u1 = u0 + inset
    v_top = 1.0 - row * tile
    v_bot = v_top - inset
    return ((u1, v_top), (u1, v_bot), (u0, v_bot), (u0, v_top))



def _sign_destroy_face_data(box):
    x0, y0, z0, sx, sy, sz = (float(value) for value in box[:6])
    x1, y1, z1 = x0 + sx, y0 + sy, z0 + sz

    return (
        ("-y", ((x0, y1, z1), (x1, y1, z1), (x1, y1, z0), (x0, y1, z0))),
        ("+y", ((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1))),
        ("-x", ((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0))),
        ("+x", ((x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1))),
        ("+z", ((x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0))),
        ("-z", ((x0, y0, z1), (x0, y1, z1), (x1, y1, z1), (x1, y0, z1))),
    )


def _sign_destroy_face_uv(atlas_idx, point, face):
    return destroy_stage_tile_uv(atlas_idx, point, face)


def _draw_sign_destroy_progress(atlas_idx, metadata, block_type):
    from game.rendering.sign_geometry import SIGN_BOARD_BOX, SIGN_STICK_BOX

    wall_sign = block_type == "wall_sign"
    meta = int(metadata) & 15
    if wall_sign:
        yaw = {2: 180.0, 3: 0.0, 4: 90.0, 5: -90.0}.get(meta, 180.0)
        model_boxes = (SIGN_BOARD_BOX,)
    else:
        yaw = meta * 360.0 / 16.0
        model_boxes = (SIGN_BOARD_BOX, SIGN_STICK_BOX)

    glPushMatrix()
    uv_offset = (0.0, -5.0 / 16.0, -7.0 / 16.0) if wall_sign else (0.0, 0.0, 0.0)
    try:
        glRotatef(-yaw, 0.0, 1.0, 0.0)
        if wall_sign:
            glTranslatef(0.0, -5.0 / 16.0, -7.0 / 16.0)

        glBegin(GL_QUADS)
        for box in model_boxes:
            for face, model_vertices in _sign_destroy_face_data(box):
                normal = {
                    "+x": (1.0, 0.0, 0.0), "-x": (-1.0, 0.0, 0.0),
                    "+y": (0.0, 1.0, 0.0), "-y": (0.0, -1.0, 0.0),
                    "+z": (0.0, 0.0, 1.0), "-z": (0.0, 0.0, -1.0),
                }[face]
                for mx, my, mz in model_vertices:
                    point = (mx / 24.0, -my / 24.0, -mz / 24.0)
                    local = (point[0] + uv_offset[0], point[1] + uv_offset[1],
                             point[2] + uv_offset[2])
                    pad = 0.0035
                    glTexCoord2f(*_sign_destroy_face_uv(atlas_idx, local, face))
                    glVertex3f(
                        point[0] + normal[0] * pad,
                        point[1] + normal[1] * pad,
                        point[2] + normal[2] * pad,
                    )
        glEnd()
    finally:
        glPopMatrix()


def destroy_overlay_is_entity_pass(pos, world):
    if world is None or pos is None:
        return False
    try:
        return world.get_block_type(*(int(v) for v in pos)) in ("sign", "wall_sign")
    except Exception:
        return False


def draw_block_destroy_progress(pos, progress, world, renderer):
    if progress <= 0.0:
        return
    stage = min(9, int(progress * 10.0))
    atlas_idx = 240 + stage
    x, y, z = pos

    block = None
    block_type = None
    meta = 0
    if world is not None:
        block = world.get_block(x, y, z)
        if block is not None:
            block_type = getattr(block, "block_type", None)
            meta = int(getattr(block, "meta", 0) or 0)
            if hasattr(world, "get_metadata"):
                try:
                    meta = int(world.get_metadata(x, y, z)) & 15
                except Exception:
                    pass

    tex = Block._get_texture()
    if tex is None:
        return

    ox, oy, oz = renderer.camera_relative_offset(x, y, z)
    glEnable(GL_TEXTURE_2D)
    lighting_was_enabled = bool(glIsEnabled(GL_LIGHTING))
    if lighting_was_enabled:
        glDisable(GL_LIGHTING)
    glEnable(GL_BLEND)
    glBlendFunc(GL_DST_COLOR, GL_SRC_COLOR)
    glEnable(GL_POLYGON_OFFSET_FILL)
    glPolygonOffset(-3.0, -3.0)
    glDepthMask(False)
    previous_depth_func = int(glGetIntegerv(GL_DEPTH_FUNC))
    if block_type in ("sign", "wall_sign"):
        glDepthFunc(GL_LEQUAL)
    depth_test_was_enabled = bool(glIsEnabled(GL_DEPTH_TEST))
    if not depth_test_was_enabled:
        glEnable(GL_DEPTH_TEST)
    GL_DEPTH_CLAMP = 0x864F
    depth_clamp_was_enabled = bool(glIsEnabled(GL_DEPTH_CLAMP))
    if not depth_clamp_was_enabled:
        try:
            glEnable(GL_DEPTH_CLAMP)
            depth_clamp_active = True
        except Exception:
            depth_clamp_active = False
    else:
        depth_clamp_active = True
    cull_was_enabled = bool(glIsEnabled(GL_CULL_FACE))
    if cull_was_enabled:
        glDisable(GL_CULL_FACE)

    tex.bind()
    glColor4f(1.0, 1.0, 1.0, 1.0)

    glPushMatrix()
    glTranslatef(ox, oy, oz)
    if block_type in ("sign", "wall_sign"):
        _draw_sign_destroy_progress(atlas_idx, meta, block_type)
    else:
        glBegin(GL_QUADS)
        if block_type == "rail":
            verts = _rail_destroy_plate_verts(meta)
            uvs = _destroy_stage_atlas_uvs(atlas_idx)
            for winding in (range(4), reversed(range(4))):
                for i in winding:
                    vx, vy, vz = verts[i]
                    uu, vv = uvs[i]
                    glTexCoord2f(uu, vv)
                    glVertex3f(vx, vy, vz)
        else:
            selection_boxes = [(-0.5, -0.5, -0.5, 0.5, 0.5, 0.5)]
            if block is not None:
                if hasattr(block, "get_destroy_boxes"):
                    selection_boxes = block.get_destroy_boxes() or selection_boxes
                elif hasattr(block, "get_selection_boxes"):
                    selection_boxes = block.get_selection_boxes() or selection_boxes
                else:
                    selection_boxes = [block.get_selection_bounds()]

            def face_quad(bounds, face):
                minx, miny, minz, maxx, maxy, maxz = bounds
                if face == "+y":
                    return ((minx, maxy, maxz), (maxx, maxy, maxz), (maxx, maxy, minz), (minx, maxy, minz))
                if face == "-y":
                    return ((minx, miny, minz), (maxx, miny, minz), (maxx, miny, maxz), (minx, miny, maxz))
                if face == "+z":
                    return ((minx, miny, maxz), (maxx, miny, maxz), (maxx, maxy, maxz), (minx, maxy, maxz))
                if face == "-z":
                    return ((maxx, miny, minz), (minx, miny, minz), (minx, maxy, minz), (maxx, maxy, minz))
                if face == "+x":
                    return ((maxx, miny, maxz), (maxx, miny, minz), (maxx, maxy, minz), (maxx, maxy, maxz))
                if face == "-x":
                    return ((minx, miny, minz), (minx, miny, maxz), (minx, maxy, maxz), (minx, maxy, minz))
                return ()

            pad = 0.002
            expanded = []
            for bounds in selection_boxes:
                minx, miny, minz, maxx, maxy, maxz = bounds
                expanded.append((
                    minx - pad, miny - pad, minz - pad,
                    maxx + pad, maxy + pad, maxz + pad,
                ))

            view_x, view_y, view_z = -ox, -oy, -oz

            face_normals = {
                "+y": (0.0, 1.0, 0.0),
                "-y": (0.0, -1.0, 0.0),
                "+z": (0.0, 0.0, 1.0),
                "-z": (0.0, 0.0, -1.0),
                "+x": (1.0, 0.0, 0.0),
                "-x": (-1.0, 0.0, 0.0),
            }

            for bounds in expanded:
                for face in ("+y", "-y", "+z", "-z", "+x", "-x"):
                    nx, ny, nz = face_normals[face]
                    if nx * view_x + ny * view_y + nz * view_z <= 0.0:
                        continue
                    verts = face_quad(bounds, face)
                    if not verts:
                        continue
                    for vx, vy, vz in verts:
                        glTexCoord2f(
                            *destroy_stage_tile_uv(atlas_idx, (vx, vy, vz), face)
                        )
                        glVertex3f(vx, vy, vz)

        glEnd()
    glPopMatrix()

    glDepthFunc(previous_depth_func)
    tex.unbind()
    glDepthMask(True)
    glDisable(GL_POLYGON_OFFSET_FILL)
    if depth_clamp_active and not depth_clamp_was_enabled:
        try:
            glDisable(GL_DEPTH_CLAMP)
        except Exception:
            pass
    if not depth_test_was_enabled:
        glDisable(GL_DEPTH_TEST)
    if cull_was_enabled:
        glEnable(GL_CULL_FACE)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glDisable(GL_BLEND)
    if lighting_was_enabled:
        glEnable(GL_LIGHTING)
    glDisable(GL_TEXTURE_2D)



def draw_chunk_borders(world, player, renderer):
    import math
    px, pz = player.pos[0], player.pos[2]
    chunk_index = getattr(world, "_get_chunk_index", None)
    if callable(chunk_index):
        cx, cz = chunk_index(px, pz)
    else:
        cx = math.floor((px + 0.5) / world.CHUNK_SIZE)
        cz = math.floor((pz + 0.5) / world.CHUNK_SIZE)
    size = world.CHUNK_SIZE
    height = world.CHUNK_HEIGHT

    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glLineWidth(1.5)
    glDepthMask(False)
    glDisable(GL_TEXTURE_2D)

    for dx, dz in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
        ncx, ncz = cx + dx, cz + dz
        if (ncx, ncz) not in world.chunks:
            continue
        x0 = ncx * size - 0.5
        z0 = ncz * size - 0.5
        x1 = x0 + size
        z1 = z0 + size
        if dx == 0 and dz == 0:
            glColor4f(1.0, 1.0, 0.2, 0.7)
        else:
            glColor4f(0.2, 0.9, 1.0, 0.45)

        ox, oy, oz = renderer.camera_relative_offset(0, 0, 0)
        glPushMatrix()
        glTranslatef(ox, oy, oz)
        glBegin(GL_LINES)
        for x, z in ((x0, z0), (x1, z0), (x0, z1), (x1, z1)):
            glVertex3f(x, 0.0, z)
            glVertex3f(x, float(height), z)
        for y in (0.0, float(height)):
            glVertex3f(x0, y, z0); glVertex3f(x1, y, z0)
            glVertex3f(x1, y, z0); glVertex3f(x1, y, z1)
            glVertex3f(x1, y, z1); glVertex3f(x0, y, z1)
            glVertex3f(x0, y, z1); glVertex3f(x0, y, z0)
        glColor4f(1.0, 0.5, 0.1, 0.35)
        for sy in range(16, height, 16):
            y = float(sy)
            glVertex3f(x0, y, z0); glVertex3f(x1, y, z0)
            glVertex3f(x1, y, z0); glVertex3f(x1, y, z1)
            glVertex3f(x1, y, z1); glVertex3f(x0, y, z1)
            glVertex3f(x0, y, z1); glVertex3f(x0, y, z0)
        if dx == 0 and dz == 0:
            glColor4f(1.0, 1.0, 0.2, 0.7)
        else:
            glColor4f(0.2, 0.9, 1.0, 0.45)
        glEnd()
        glPopMatrix()

    glDepthMask(True)
    glDisable(GL_BLEND)



def check_gl_error():
    error_code = glGetError()
    if error_code != GL_NO_ERROR:
        error_string = gluErrorString(error_code)
        print(f"OpenGL Error: {error_string.decode()}")



def draw_entity_hitboxes(world, renderer):
    if world is None or renderer is None:
        return
    from game.rendering.entity_debug import debug_hitbox_entities
    entities = debug_hitbox_entities(world)
    if not entities:
        return

    try:
        frustum = world._camera_frustum()
    except Exception:
        frustum = None

    blend_was = bool(glIsEnabled(GL_BLEND))
    texture_was = bool(glIsEnabled(GL_TEXTURE_2D))
    line_smooth_was = bool(glIsEnabled(GL_LINE_SMOOTH))
    try:
        old_width = float(glGetFloatv(GL_LINE_WIDTH))
    except Exception:
        old_width = 1.0

    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glDisable(GL_TEXTURE_2D)
    glEnable(GL_LINE_SMOOTH)
    glLineWidth(1.5)
    glDepthMask(False)
    try:
        for entity in entities:
            if getattr(entity, "dead", False):
                continue
            is_remote_player = bool(getattr(entity, "_mp_remote_player", False))
            if not is_remote_player:
                try:
                    if not world._entity_chunk_loaded(entity):
                        continue
                except Exception:
                    pass
            try:
                ex, ey, ez = (float(v) for v in entity.pos)
                get_box = getattr(entity, "get_bounding_box", None)
                bounds = get_box() if callable(get_box) else None
                if bounds is None or len(bounds) != 6:
                    size = getattr(entity, "size", (0.6, 1.8, 0.6))
                    half_x = max(0.025, float(size[0]) * 0.5)
                    height = max(0.05, float(size[1]))
                    half_z = max(0.025, float(size[2]) * 0.5)
                    bounds = (ex - half_x, ey, ez - half_z,
                              ex + half_x, ey + height, ez + half_z)
                else:
                    bounds = tuple(float(v) for v in bounds)
                if (not is_remote_player and frustum is not None
                        and not frustum.intersects_aabb(*bounds, padding=0.25)):
                    continue
                ox, oy, oz = renderer.camera_relative_offset(ex, ey, ez)
            except Exception:
                continue

            x0, y0, z0 = bounds[0] - ex, bounds[1] - ey, bounds[2] - ez
            x1, y1, z1 = bounds[3] - ex, bounds[4] - ey, bounds[5] - ez
            corners = (
                (x0, y0, z0), (x1, y0, z0),
                (x1, y0, z1), (x0, y0, z1),
                (x0, y1, z0), (x1, y1, z0),
                (x1, y1, z1), (x0, y1, z1),
            )
            edges = ((0, 1), (1, 2), (2, 3), (3, 0),
                     (4, 5), (5, 6), (6, 7), (7, 4),
                     (0, 4), (1, 5), (2, 6), (3, 7))
            if (entity.__class__.__name__ in ("Player", "EntityPlayer")
                    or is_remote_player):
                glColor4f(0.2, 1.0, 0.2, 0.9)
            else:
                glColor4f(0.2, 0.85, 1.0, 0.8)
            glPushMatrix()
            glTranslatef(ox, oy, oz)
            glBegin(GL_LINES)
            for a, b in edges:
                glVertex3f(*corners[a])
                glVertex3f(*corners[b])
            glEnd()
            glPopMatrix()
    finally:
        glDepthMask(True)
        glLineWidth(old_width)
        (glEnable if blend_was else glDisable)(GL_BLEND)
        (glEnable if texture_was else glDisable)(GL_TEXTURE_2D)
        (glEnable if line_smooth_was else glDisable)(GL_LINE_SMOOTH)


_SNOW_TEXTURE = None
_PRECIP_UPDATE_COUNT = 0


def tick_precipitation_renderer():
    global _PRECIP_UPDATE_COUNT
    _PRECIP_UPDATE_COUNT = (_PRECIP_UPDATE_COUNT + 1) & 0x7FFFFFFF


def _snow_texture():
    global _SNOW_TEXTURE
    from engine.textures import Texture
    if _SNOW_TEXTURE is None:
        _SNOW_TEXTURE = Texture("game/textures/snow.png")
    return _SNOW_TEXTURE


def render_precipitation(world, player, renderer, partial=0.0):
    if world is None or player is None or renderer is None:
        return
    if not bool(getattr(world, "snow_covered", False)):
        return

    import math
    import random as _random

    px = float(player.pos[0])
    py = float(player.pos[1])
    pz = float(player.pos[2])
    eye_y = py + float(getattr(player, "EYE_HEIGHT", 1.62))
    cx = int(math.floor(px))
    cy = int(math.floor(eye_y))
    cz = int(math.floor(pz))

    fancy = bool(getattr(getattr(world, "options", None), "fancy_graphics", True)
                 or getattr(world, "fancy_graphics", True))
    radius = 10 if fancy else 5

    chunks = getattr(world, "chunks", None)
    size = int(getattr(world, "CHUNK_SIZE", 16))
    update_count = int(getattr(world, "world_time", 0))

    texture = _snow_texture()
    cull_was = bool(glIsEnabled(GL_CULL_FACE))
    blend_was = bool(glIsEnabled(GL_BLEND))
    texture_was = bool(glIsEnabled(GL_TEXTURE_2D))
    depth_mask = True
    try:
        from OpenGL.GL import glGetBooleanv, GL_DEPTH_WRITEMASK
        depth_mask = bool(glGetBooleanv(GL_DEPTH_WRITEMASK))
    except Exception:
        pass

    glDisable(GL_CULL_FACE)
    glNormal3f(0.0, 1.0, 0.0)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glEnable(GL_TEXTURE_2D)
    glDepthMask(False)
    texture.bind()

    ox, oy, oz = renderer.camera_relative_offset(0.0, 0.0, 0.0)
    glPushMatrix()
    glTranslatef(ox, oy, oz)

    try:
        for bx in range(cx - radius, cx + radius + 1):
            for bz in range(cz - radius, cz + radius + 1):
                top = None
                if isinstance(chunks, dict):
                    chunk = chunks.get((bx // size, bz // size))
                    if chunk is not None:
                        lx = bx - chunk.start_x
                        lz = bz - chunk.start_z
                        if 0 <= lx < chunk.width and 0 <= lz < chunk.depth:
                            top = int(chunk.heightmap[lz * chunk.width + lx])
                if top is None:
                    continue

                y0 = cy - radius
                y1 = cy + radius
                if y0 < top:
                    y0 = top
                if y1 < top:
                    y1 = top
                if y0 == y1:
                    continue

                seed = (bx * bx * 3121 + bx * 45238971 + bz * bz * 418711 + bz * 13761) & 0xFFFFFFFF
                rng = _random.Random(seed)
                tick_f = float(update_count) + float(partial)
                v_scroll = ((update_count & 255) + float(partial)) / 256.0
                u_drift = rng.random() + tick_f * 0.01 * rng.gauss(0.0, 1.0)
                v_drift = rng.random() + tick_f * rng.gauss(0.0, 1.0) * 0.001

                dx = (bx + 0.5) - px
                dz = (bz + 0.5) - pz
                dist = math.sqrt(dx * dx + dz * dz) / float(radius)
                alpha = (1.0 - dist * dist) * 0.7
                if alpha <= 0.0:
                    continue

                brightness = 1.0
                glColor4f(brightness, brightness, brightness, alpha)

                uv_scale = 2.0
                v0 = y0 * uv_scale / 8.0 + v_scroll * uv_scale + v_drift
                v1 = y1 * uv_scale / 8.0 + v_scroll * uv_scale + v_drift
                u0 = 0.0 * uv_scale + u_drift
                u1 = 1.0 * uv_scale + u_drift

                glBegin(GL_QUADS)
                glTexCoord2f(u0, v0); glVertex3f(bx + 0.0, float(y0), bz + 0.0)
                glTexCoord2f(u1, v0); glVertex3f(bx + 1.0, float(y0), bz + 1.0)
                glTexCoord2f(u1, v1); glVertex3f(bx + 1.0, float(y1), bz + 1.0)
                glTexCoord2f(u0, v1); glVertex3f(bx + 0.0, float(y1), bz + 0.0)

                glTexCoord2f(u0, v0); glVertex3f(bx + 0.0, float(y0), bz + 1.0)
                glTexCoord2f(u1, v0); glVertex3f(bx + 1.0, float(y0), bz + 0.0)
                glTexCoord2f(u1, v1); glVertex3f(bx + 1.0, float(y1), bz + 0.0)
                glTexCoord2f(u0, v1); glVertex3f(bx + 0.0, float(y1), bz + 1.0)
                glEnd()
    finally:
        glPopMatrix()
        texture.unbind()
        glColor4f(1.0, 1.0, 1.0, 1.0)
        glDepthMask(depth_mask)
        (glEnable if cull_was else glDisable)(GL_CULL_FACE)
        (glEnable if blend_was else glDisable)(GL_BLEND)
        (glEnable if texture_was else glDisable)(GL_TEXTURE_2D)

