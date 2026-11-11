
TESLA_GLOW_LINE_WIDTH = 3.2
TESLA_CORE_LINE_WIDTH = 1.25


def tesla_line_widths(_distance=None):
    return TESLA_GLOW_LINE_WIDTH, TESLA_CORE_LINE_WIDTH


def draw_tesla_arcs(world, camera_pos, *, partial=0.0):
    arcs = getattr(world, "_aod_tesla_arcs", None)
    if not isinstance(arcs, list) or not arcs:
        return 0

    try:
        from OpenGL.GL import (
            GL_ALPHA_TEST, GL_BLEND, GL_BLEND_DST, GL_BLEND_SRC,
            GL_DEPTH_TEST, GL_DEPTH_WRITEMASK, GL_LIGHTING, GL_LINE_SMOOTH,
            GL_LINES, GL_LINE_WIDTH, GL_ONE, GL_SRC_ALPHA, GL_TEXTURE_2D,
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
    alpha_test_was = bool(glIsEnabled(GL_ALPHA_TEST))
    try:
        depth_mask_was = bool(glGetBooleanv(GL_DEPTH_WRITEMASK))
    except Exception:
        depth_mask_was = True
    try:
        old_width = float(glGetFloatv(GL_LINE_WIDTH))
    except Exception:
        old_width = 1.0
    try:
        old_blend_src = int(glGetIntegerv(GL_BLEND_SRC))
        old_blend_dst = int(glGetIntegerv(GL_BLEND_DST))
    except Exception:
        old_blend_src, old_blend_dst = GL_SRC_ALPHA, GL_ONE

    rendered = 0
    glow_width, core_width = tesla_line_widths()
    try:
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)
        glEnable(GL_DEPTH_TEST)
        glDepthMask(False)
        glDisable(GL_LIGHTING)
        glDisable(GL_TEXTURE_2D)
        glDisable(GL_ALPHA_TEST)
        glEnable(GL_LINE_SMOOTH)

        glLineWidth(glow_width)
        glBegin(GL_LINES)
        for arc in arcs:
            points = arc.get("points", ())
            if len(points) < 2:
                continue
            life = max(0.0, min(1.0, float(arc.get("remaining", 0.0))
                                / max(1.0e-6, float(arc.get("lifetime", 0.2)))))
            if life <= 0.0:
                continue
            alpha = 0.42 * life
            kind = str(arc.get("kind", ""))
            if kind.startswith("zapper"):
                color = (0.38, 0.68, 1.0)
            elif kind == "entity":
                color = (0.22, 0.78, 1.0)
            elif kind == "conductor":
                color = (0.12, 0.48, 1.0)
            else:
                color = (0.45, 0.82, 1.0)
            glColor4f(color[0], color[1], color[2], alpha)
            for start, end in zip(points, points[1:]):
                glVertex3f(float(start[0]) - cam[0], float(start[1]) - cam[1], float(start[2]) - cam[2])
                glVertex3f(float(end[0]) - cam[0], float(end[1]) - cam[1], float(end[2]) - cam[2])
            rendered += 1
        glEnd()

        glLineWidth(core_width)
        glBegin(GL_LINES)
        for arc in arcs:
            points = arc.get("points", ())
            if len(points) < 2:
                continue
            life = max(0.0, min(1.0, float(arc.get("remaining", 0.0))
                                / max(1.0e-6, float(arc.get("lifetime", 0.2)))))
            if life <= 0.0:
                continue
            kind = str(arc.get("kind", ""))
            if kind.startswith("zapper"):
                glColor4f(0.78, 0.91, 1.0, 0.98 * life)
            else:
                glColor4f(0.88, 0.97, 1.0, 0.95 * life)
            for start, end in zip(points, points[1:]):
                glVertex3f(float(start[0]) - cam[0], float(start[1]) - cam[1], float(start[2]) - cam[2])
                glVertex3f(float(end[0]) - cam[0], float(end[1]) - cam[1], float(end[2]) - cam[2])
        glEnd()
    finally:
        glLineWidth(old_width)
        glDepthMask(depth_mask_was)
        glBlendFunc(old_blend_src, old_blend_dst)
        if alpha_test_was:
            glEnable(GL_ALPHA_TEST)
        else:
            glDisable(GL_ALPHA_TEST)
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
