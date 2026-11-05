from __future__ import annotations


def sign_selection_boxes(metadata, classic_bounds=None, *, wall=False):
    meta = int(metadata) & 15
    if wall:
        if meta == 2:
            bounds = (0.0, 9.0 / 32.0, 14.0 / 16.0,
                      1.0, 25.0 / 32.0, 1.0)
        elif meta == 3:
            bounds = (0.0, 9.0 / 32.0, 0.0,
                      1.0, 25.0 / 32.0, 2.0 / 16.0)
        elif meta == 4:
            bounds = (14.0 / 16.0, 9.0 / 32.0, 0.0,
                      1.0, 25.0 / 32.0, 1.0)
        elif meta == 5:
            bounds = (0.0, 9.0 / 32.0, 0.0,
                      2.0 / 16.0, 25.0 / 32.0, 1.0)
        else:
            bounds = (0.0, 9.0 / 32.0, 14.0 / 16.0,
                      1.0, 25.0 / 32.0, 1.0)
    else:
        if classic_bounds is None:
            classic_bounds = (0.25, 0.0, 0.25, 0.75, 1.0, 0.75)
        bounds = tuple(float(value) for value in classic_bounds)

    min_x, min_y, min_z, max_x, max_y, max_z = bounds
    return [(
        min_x - 0.5,
        min_y - 0.5,
        min_z - 0.5,
        max_x - 0.5,
        max_y - 0.5,
        max_z - 0.5,
    )]


def sign_destroy_boxes(metadata, *, wall=False):
    import math

    from game.rendering.sign_geometry import SIGN_BOARD_BOX, SIGN_STICK_BOX

    meta = int(metadata) & 15
    if wall:
        yaw = {2: 180.0, 3: 0.0, 4: 90.0, 5: -90.0}.get(meta, 180.0)
        model_boxes = (SIGN_BOARD_BOX,)
        offset_y, offset_z = -5.0 / 16.0, -7.0 / 16.0
    else:
        yaw = meta * 360.0 / 16.0
        model_boxes = (SIGN_BOARD_BOX, SIGN_STICK_BOX)
        offset_y = offset_z = 0.0

    theta = math.radians(-yaw)
    cosine, sine = math.cos(theta), math.sin(theta)
    result = []
    for box in model_boxes:
        x0, y0, z0, size_x, size_y, size_z = map(float, box[:6])
        x1, y1, z1 = x0 + size_x, y0 + size_y, z0 + size_z
        transformed = []
        for px in (x0, x1):
            for py in (y0, y1):
                for pz in (z0, z1):
                    sx = px / 24.0
                    sy = -py / 24.0 + offset_y
                    sz = -pz / 24.0 + offset_z
                    rx = cosine * sx + sine * sz
                    rz = -sine * sx + cosine * sz
                    transformed.append((rx, sy, rz))
        result.append((
            min(v[0] for v in transformed),
            min(v[1] for v in transformed),
            min(v[2] for v in transformed),
            max(v[0] for v in transformed),
            max(v[1] for v in transformed),
            max(v[2] for v in transformed),
        ))
    return result
