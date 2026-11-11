
import math


def _basis(normal):
    nx, ny, nz = (float(value) for value in normal)
    length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    normal = (nx / length, ny / length, nz / length)
    reference = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.85 else (0.0, 0.0, 1.0)
    ux = normal[1] * reference[2] - normal[2] * reference[1]
    uy = normal[2] * reference[0] - normal[0] * reference[2]
    uz = normal[0] * reference[1] - normal[1] * reference[0]
    ulen = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
    u = (ux / ulen, uy / ulen, uz / ulen)
    v = (
        normal[1] * u[2] - normal[2] * u[1],
        normal[2] * u[0] - normal[0] * u[2],
        normal[0] * u[1] - normal[1] * u[0],
    )
    return u, v


def _camera_basis(center, camera):
    forward = tuple(float(center[i]) - float(camera[i]) for i in range(3))
    length = math.sqrt(sum(component * component for component in forward)) or 1.0
    forward = tuple(component / length for component in forward)
    world_up = (0.0, 1.0, 0.0)
    right = (
        forward[1] * world_up[2] - forward[2] * world_up[1],
        forward[2] * world_up[0] - forward[0] * world_up[2],
        forward[0] * world_up[1] - forward[1] * world_up[0],
    )
    rlen = math.sqrt(sum(component * component for component in right))
    if rlen < 1.0e-6:
        right = (1.0, 0.0, 0.0)
    else:
        right = tuple(component / rlen for component in right)
    up = (
        right[1] * forward[2] - right[2] * forward[1],
        right[2] * forward[0] - right[0] * forward[2],
        right[0] * forward[1] - right[1] * forward[0],
    )
    return right, up


def _vertex(center, u, v, radius, angle, cam):
    return (
        center[0] + radius * (math.cos(angle) * u[0] + math.sin(angle) * v[0]) - cam[0],
        center[1] + radius * (math.cos(angle) * u[1] + math.sin(angle) * v[1]) - cam[1],
        center[2] + radius * (math.cos(angle) * u[2] + math.sin(angle) * v[2]) - cam[2],
    )


def _draw_billboard_glow(center, camera, radius, strength, phase,
                         glBegin, glEnd, glColor4f, glVertex3f, triangle_fan,
                         cam, *, hot=False):
    right, up = _camera_basis(center, camera)
    point = (center[0] - cam[0], center[1] - cam[1], center[2] - cam[2])
    segments = 24
    glBegin(triangle_fan)
    if hot:
        glColor4f(1.0, 0.98, 0.72, 0.78 * strength)
    else:
        glColor4f(1.0, 0.55, 0.16, 0.34 * strength)
    glVertex3f(*point)
    for index in range(segments + 1):
        angle = 2.0 * math.pi * index / segments
        wobble = 1.0 + 0.035 * math.sin(angle * 5.0 + phase)
        edge = _vertex(center, right, up, radius * wobble, angle, cam)
        if hot:
            glColor4f(1.0, 0.28 + 0.11 * math.sin(angle * 3.0 + phase),
                      0.025, 0.012 * strength)
        else:
            glColor4f(1.0, 0.24, 0.035, 0.004 * strength)
        glVertex3f(*edge)
    glEnd()


def _draw_plasma_ribbon(start, end, normal, camera, radius, life,
                        glBegin, glEnd, glColor4f, glVertex3f, triangle_strip):
    normal_length = math.sqrt(sum(float(value) ** 2 for value in normal)) or 1.0
    n = tuple(float(value) / normal_length for value in normal)
    a = tuple(float(start[i]) + n[i] * 0.022 for i in range(3))
    b = tuple(float(end[i]) + n[i] * 0.022 for i in range(3))
    tangent = tuple(b[i] - a[i] for i in range(3))
    length = math.sqrt(sum(value * value for value in tangent))
    if length < 1.0e-5:
        return False
    tangent = tuple(value / length for value in tangent)
    midpoint = tuple((a[i] + b[i]) * 0.5 for i in range(3))
    view = tuple(float(camera[i]) - midpoint[i] for i in range(3))
    view_length = math.sqrt(sum(value * value for value in view)) or 1.0
    view = tuple(value / view_length for value in view)
    side = (
        tangent[1] * view[2] - tangent[2] * view[1],
        tangent[2] * view[0] - tangent[0] * view[2],
        tangent[0] * view[1] - tangent[1] * view[0],
    )
    side_length = math.sqrt(sum(value * value for value in side))
    if side_length < 1.0e-5:
        u, _v = _basis(n)
        side = u
    else:
        side = tuple(value / side_length for value in side)

    def quad(width, rgba):
        half = width * 0.5
        vertices = (
            tuple(a[i] + side[i] * half - camera[i] for i in range(3)),
            tuple(a[i] - side[i] * half - camera[i] for i in range(3)),
            tuple(b[i] + side[i] * half - camera[i] for i in range(3)),
            tuple(b[i] - side[i] * half - camera[i] for i in range(3)),
        )
        glBegin(triangle_strip)
        for vertex in vertices:
            glColor4f(rgba[0], rgba[1], rgba[2], rgba[3] * life)
            glVertex3f(*vertex)
        glEnd()

    base = max(0.035, float(radius))
    quad(base * 3.4, (1.0, 0.20, 0.025, 0.22))
    quad(base * 1.8, (1.0, 0.48, 0.055, 0.46))
    quad(base * 0.62, (1.0, 0.90, 0.38, 0.78))
    return True


def draw_solar_spots(world, camera_pos, *, partial=0.0):
    spots = getattr(world, "_aod_solar_spots", None)
    trail = getattr(world, "_aod_solar_plasma_trail", None)
    spots = spots if isinstance(spots, list) else []
    trail = trail if isinstance(trail, list) else []
    if not spots and not trail:
        return 0
    try:
        from OpenGL.GL import (
            GL_BLEND, GL_BLEND_DST, GL_BLEND_SRC, GL_DEPTH_TEST,
            GL_DEPTH_WRITEMASK, GL_LIGHTING, GL_LINE_LOOP, GL_LINE_SMOOTH,
            GL_LINE_WIDTH, GL_ONE, GL_SRC_ALPHA, GL_TEXTURE_2D, GL_TRIANGLE_FAN,
            GL_TRIANGLE_STRIP,
            glBegin, glBlendFunc, glColor4f, glDepthMask, glDisable, glEnable,
            glEnd, glGetBooleanv, glGetFloatv, glGetIntegerv, glIsEnabled,
            glLineWidth, glVertex3f,
        )
    except Exception:
        return 0

    try:
        cam = tuple(float(value) for value in camera_pos[:3])
    except (TypeError, ValueError):
        cam = (0.0, 0.0, 0.0)

    blend_was = bool(glIsEnabled(GL_BLEND))
    depth_was = bool(glIsEnabled(GL_DEPTH_TEST))
    lighting_was = bool(glIsEnabled(GL_LIGHTING))
    texture_was = bool(glIsEnabled(GL_TEXTURE_2D))
    smooth_was = bool(glIsEnabled(GL_LINE_SMOOTH))
    try:
        depth_mask_was = bool(glGetBooleanv(GL_DEPTH_WRITEMASK))
    except Exception:
        depth_mask_was = True
    try:
        old_src = int(glGetIntegerv(GL_BLEND_SRC))
        old_dst = int(glGetIntegerv(GL_BLEND_DST))
    except Exception:
        old_src, old_dst = GL_SRC_ALPHA, GL_ONE
    try:
        old_line_width = float(glGetFloatv(GL_LINE_WIDTH))
    except Exception:
        old_line_width = 1.0

    rendered = 0
    segments = 28
    try:
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)
        glEnable(GL_DEPTH_TEST)
        glDepthMask(False)
        glDisable(GL_LIGHTING)
        glDisable(GL_TEXTURE_2D)
        glEnable(GL_LINE_SMOOTH)

        for droplet in trail:
            life = max(0.0, min(1.0, float(droplet.get("remaining", 0.0))
                                / max(1.0e-6, float(droplet.get("lifetime", 1.45)))))
            if life <= 0.0:
                continue
            point = tuple(float(value) for value in droplet.get("center", ()))
            normal = tuple(float(value) for value in droplet.get("normal", (0, 1, 0)))
            if len(point) != 3 or len(normal) != 3:
                continue
            radius = max(0.018, float(droplet.get("radius", 0.075)))
            if droplet.get("start") is not None and droplet.get("end") is not None:
                _draw_plasma_ribbon(
                    droplet["start"], droplet["end"], normal, cam, radius, life,
                    glBegin, glEnd, glColor4f, glVertex3f, GL_TRIANGLE_STRIP,
                )
                end = tuple(float(value) for value in droplet["end"])
                glow_center = tuple(end[i] + normal[i] * 0.027 for i in range(3))
                phase = float(droplet.get("phase", 0.0)) + float(partial) * 5.5
                _draw_billboard_glow(
                    glow_center, cam, radius * 1.15, life * 0.8, phase,
                    glBegin, glEnd, glColor4f, glVertex3f, GL_TRIANGLE_FAN, cam,
                    hot=True,
                )
            else:
                glow_center = tuple(point[i] + normal[i] * 0.018
                                    for i in range(3))
                phase = float(droplet.get("phase", 0.0)) + float(partial) * 5.5
                _draw_billboard_glow(
                    glow_center, cam, radius * 2.5, life * 0.72, phase,
                    glBegin, glEnd, glColor4f, glVertex3f, GL_TRIANGLE_FAN, cam,
                )
                _draw_billboard_glow(
                    glow_center, cam, radius * 0.72, life * 0.88, phase,
                    glBegin, glEnd, glColor4f, glVertex3f, GL_TRIANGLE_FAN, cam,
                    hot=True,
                )
            rendered += 1

        for spot in spots:
            life = max(0.0, min(1.0, float(spot.get("remaining", 0.0))
                                / max(1.0e-6, float(spot.get("lifetime", 0.36)))))
            if life <= 0.0:
                continue
            center = spot.get("center")
            normal = spot.get("normal")
            if center is None or normal is None:
                continue
            center = tuple(float(value) for value in center)
            normal = tuple(float(value) for value in normal)
            u, v = _basis(normal)
            phase = float(spot.get("phase", 0.0)) + float(partial) * 7.0
            pulse = 0.94 + 0.06 * math.sin(phase)
            outer_radius = 0.17 * pulse
            alpha = 0.58 * life
            point = (center[0] - cam[0], center[1] - cam[1], center[2] - cam[2])

            glow_center = tuple(center[i] + normal[i] * 0.022 for i in range(3))
            _draw_billboard_glow(
                glow_center, cam, 0.48 * pulse, alpha, phase,
                glBegin, glEnd, glColor4f, glVertex3f, GL_TRIANGLE_FAN, cam,
            )
            _draw_billboard_glow(
                glow_center, cam, 0.25 * pulse, alpha * 1.2, phase,
                glBegin, glEnd, glColor4f, glVertex3f, GL_TRIANGLE_FAN, cam,
                hot=True,
            )

            glBegin(GL_TRIANGLE_FAN)
            glColor4f(1.0, 0.96, 0.70, 0.82 * alpha)
            glVertex3f(*point)
            for index in range(segments + 1):
                angle = 2.0 * math.pi * index / segments
                edge = _vertex(center, u, v, outer_radius, angle, cam)
                glColor4f(1.0, 0.34 + 0.12 * math.sin(angle * 3.0 + phase),
                          0.035, 0.22 * alpha)
                glVertex3f(*edge)
            glEnd()

            glBegin(GL_TRIANGLE_FAN)
            glColor4f(1.0, 1.0, 0.88, 0.95 * alpha)
            glVertex3f(*point)
            for index in range(segments + 1):
                angle = 2.0 * math.pi * index / segments
                edge = _vertex(center, u, v, 0.065 * pulse, angle, cam)
                glColor4f(1.0, 0.78, 0.18, 0.78 * alpha)
                glVertex3f(*edge)
            glEnd()

            glColor4f(1.0, 0.62, 0.16, 0.38 * alpha)
            glLineWidth(1.0)
            glBegin(GL_LINE_LOOP)
            for index in range(segments):
                angle = 2.0 * math.pi * index / segments
                radius = outer_radius * (1.0 + 0.045 * math.sin(angle * 5.0 + phase))
                glVertex3f(*_vertex(center, u, v, radius, angle, cam))
            glEnd()
            rendered += 1
    finally:
        glDepthMask(depth_mask_was)
        glBlendFunc(old_src, old_dst)
        glLineWidth(old_line_width)
        if blend_was:
            glEnable(GL_BLEND)
        else:
            glDisable(GL_BLEND)
        if depth_was:
            glEnable(GL_DEPTH_TEST)
        else:
            glDisable(GL_DEPTH_TEST)
        if lighting_was:
            glEnable(GL_LIGHTING)
        else:
            glDisable(GL_LIGHTING)
        if texture_was:
            glEnable(GL_TEXTURE_2D)
        else:
            glDisable(GL_TEXTURE_2D)
        if smooth_was:
            glEnable(GL_LINE_SMOOTH)
        else:
            glDisable(GL_LINE_SMOOTH)
        glColor4f(1.0, 1.0, 1.0, 1.0)
    return rendered
