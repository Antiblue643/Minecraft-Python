import math

import numpy as np
from numba import njit


_ALPHA_FAR_CLIPS = (256.0, 128.0, 64.0, 32.0)
_ALPHA_CHUNK_RADII = (13, 8, 4, 2)


def alpha_render_distance_far_clip(index):
    index = max(0, min(3, int(index)))
    return _ALPHA_FAR_CLIPS[index]


def alpha_render_distance_chunk_radius(index):
    index = max(0, min(3, int(index)))
    return _ALPHA_CHUNK_RADII[index]


def alpha_far_clip_for_chunk_radius(radius):
    radius = int(radius)
    try:
        return _ALPHA_FAR_CLIPS[_ALPHA_CHUNK_RADII.index(radius)]
    except ValueError:
        return max(32.0, float(radius) * 16.0)



def alpha_fog_brightness_target(light_level, render_distance_index):
    from engine.lighting import brightness_from_level

    index = max(0, min(3, int(render_distance_index)))
    distance_floor = (3.0 - float(index)) / 3.0
    local_brightness = brightness_from_level(light_level)
    return local_brightness * (1.0 - distance_floor) + distance_floor


def alpha_scale_fog_color(rgb, brightness):
    value = max(0.0, float(brightness))
    return tuple(float(channel) * value for channel in rgb[:3])

def alpha_sky_fog_color(sky_rgb, fog_rgb, render_distance_index):
    index = max(0, min(3, int(render_distance_index)))
    blend = 1.0 - (1.0 / float(4 - index)) ** 0.25
    sky = tuple(float(value) for value in sky_rgb[:3])
    fog = tuple(float(value) for value in fog_rgb[:3])
    return tuple(fog[i] + (sky[i] - fog[i]) * blend for i in range(3))


def chunk_load_radius(view_distance):
    return max(1, int(view_distance)) + 2


def chunk_in_active_view(cx, cz, center_cx, center_cz, view_distance):
    dx, dz = int(cx) - int(center_cx), int(cz) - int(center_cz)
    radius = max(0, int(view_distance))
    return abs(dx) <= radius and abs(dz) <= radius


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _normalize(v):
    length = math.sqrt(_dot(v, v))
    if length < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / length, v[1] / length, v[2] / length)



class CameraFrustum:
    def __init__(self, camera_pos, planes, *, forward, right, up, tan_h, tan_v, near_clip, far_clip):
        self.camera_pos = tuple(float(v) for v in camera_pos)
        self.planes = tuple(tuple(float(v) for v in p) for p in planes)
        self.forward = tuple(forward)
        self.right = tuple(right)
        self.up = tuple(up)
        self.tan_h = float(tan_h)
        self.tan_v = float(tan_v)
        self.near_clip = float(near_clip)
        self.far_clip = float(far_clip)

    @classmethod
    def from_camera(
        cls,
        camera_x,
        camera_y,
        camera_z,
        yaw_degrees,
        pitch_degrees,
        vertical_fov_degrees,
        aspect,
        near_clip=0.1,
        far_clip=256.0,
    ):
        yaw = math.radians(float(yaw_degrees))
        pitch = math.radians(float(pitch_degrees))
        fov = min(175.0, max(1.0, float(vertical_fov_degrees)))
        aspect = max(0.05, float(aspect))
        tan_v = math.tan(math.radians(fov) * 0.5)
        tan_h = tan_v * aspect

        cp, sp = math.cos(pitch), math.sin(pitch)
        sy, cy = math.sin(yaw), math.cos(yaw)
        forward = _normalize((-sy * cp, sp, -cy * cp))
        right = _normalize((cy, 0.0, -sy))
        up = _normalize((sy * sp, cp, cy * sp))
        camera = (float(camera_x), float(camera_y), float(camera_z))

        side_normals = (
            _normalize((right[0] + forward[0] * tan_h,
                        right[1] + forward[1] * tan_h,
                        right[2] + forward[2] * tan_h)),       
            _normalize((-right[0] + forward[0] * tan_h,
                        -right[1] + forward[1] * tan_h,
                        -right[2] + forward[2] * tan_h)),      
            _normalize((up[0] + forward[0] * tan_v,
                        up[1] + forward[1] * tan_v,
                        up[2] + forward[2] * tan_v)),          
            _normalize((-up[0] + forward[0] * tan_v,
                        -up[1] + forward[1] * tan_v,
                        -up[2] + forward[2] * tan_v)),         
        )
        planes = []
        for normal in side_normals:
            planes.append((*normal, -_dot(normal, camera)))

        near_clip = max(0.001, float(near_clip))
        far_clip = max(near_clip + 0.01, float(far_clip))
        planes.append((*forward, -_dot(forward, camera) - near_clip))
        back = (-forward[0], -forward[1], -forward[2])
        planes.append((*back, _dot(forward, camera) + far_clip))
        return cls(
            camera, planes,
            forward=forward, right=right, up=up,
            tan_h=tan_h, tan_v=tan_v,
            near_clip=near_clip, far_clip=far_clip,
        )

    def horizontal_bounds_in_y(self, min_y, max_y):
        min_y, max_y = float(min_y), float(max_y)
        camera = self.camera_pos
        corners = {}
        signs = (-1.0, 1.0)
        for depth_name, depth in (("near", self.near_clip), ("far", self.far_clip)):
            for hs in signs:
                for vs in signs:
                    point = tuple(
                        camera[i]
                        + self.forward[i] * depth
                        + self.right[i] * hs * depth * self.tan_h
                        + self.up[i] * vs * depth * self.tan_v
                        for i in range(3)
                    )
                    corners[(depth_name, hs, vs)] = point

        edges = []
        for depth_name in ("near", "far"):
            for vs in signs:
                edges.append((corners[(depth_name, -1.0, vs)], corners[(depth_name, 1.0, vs)]))
            for hs in signs:
                edges.append((corners[(depth_name, hs, -1.0)], corners[(depth_name, hs, 1.0)]))
        for hs in signs:
            for vs in signs:
                edges.append((corners[("near", hs, vs)], corners[("far", hs, vs)]))

        points = []
        for point in corners.values():
            if min_y <= point[1] <= max_y:
                points.append(point)
        for a, b in edges:
            dy = b[1] - a[1]
            if abs(dy) < 1e-12:
                continue
            for boundary in (min_y, max_y):
                t = (boundary - a[1]) / dy
                if 0.0 < t < 1.0:
                    points.append(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))

        if not points:
            return None
        return (
            min(point[0] for point in points),
            min(point[2] for point in points),
            max(point[0] for point in points),
            max(point[2] for point in points),
        )

    def intersects_aabb(self, min_x, min_y, min_z, max_x, max_y, max_z, padding=0.0):
        p = max(0.0, float(padding))
        min_x, min_y, min_z = min_x - p, min_y - p, min_z - p
        max_x, max_y, max_z = max_x + p, max_y + p, max_z + p
        for nx, ny, nz, d in self.planes:
            px = max_x if nx >= 0.0 else min_x
            py = max_y if ny >= 0.0 else min_y
            pz = max_z if nz >= 0.0 else min_z
            if nx * px + ny * py + nz * pz + d < 0.0:
                return False
        return True


def _intersects_bounds(frustum, bounds, padding):
    return frustum.intersects_aabb(*bounds, padding=padding)


def chunk_in_frustum(
    frustum, cx, cz, chunk_size=16, world_height=128, padding=2.0,
    extra_bounds=(),
):
    x0 = int(cx) * int(chunk_size)
    z0 = int(cz) * int(chunk_size)
    if frustum.intersects_aabb(
        x0, 0, z0,
        x0 + int(chunk_size), int(world_height), z0 + int(chunk_size),
        padding=padding,
    ):
        return True
    return any(_intersects_bounds(frustum, bounds, padding)
               for bounds in extra_bounds if bounds is not None)


def visible_chunk_sections(
    frustum, cx, cz, *, chunk_size=16, world_height=128,
    section_height=16, padding=2.0, section_bounds=None,
):
    cs = int(chunk_size)
    wh = int(world_height)
    sh = int(section_height)
    x0 = int(cx) * cs
    z0 = int(cz) * cs
    x1 = x0 + cs
    z1 = z0 + cs
    count = (wh + sh - 1) // sh

    section_bounds = section_bounds or ()
    extra_bounds = [bounds for bounds in section_bounds if bounds is not None]
    if (not frustum.intersects_aabb(x0, 0, z0, x1, wh, z1, padding=padding)
            and not any(_intersects_bounds(frustum, bounds, padding)
                        for bounds in extra_bounds)):
        return ()
    if (not extra_bounds
            and _column_fully_inside(frustum, x0, 0, z0, x1, wh, z1, padding)):
        return tuple(range(count))

    result = []
    for index in range(count):
        y0 = index * sh
        y1 = min(wh, y0 + sh)
        bounds = section_bounds[index] if index < len(section_bounds) else None
        if (frustum.intersects_aabb(x0, y0, z0, x1, y1, z1, padding=padding)
                or (bounds is not None
                    and _intersects_bounds(frustum, bounds, padding))):
            result.append(index)
    return tuple(result)


def _column_fully_inside(frustum, min_x, min_y, min_z, max_x, max_y, max_z, padding=0.0):
    p = max(0.0, float(padding))
    min_x, min_y, min_z = min_x - p, min_y - p, min_z - p
    max_x, max_y, max_z = max_x + p, max_y + p, max_z + p
    for nx, ny, nz, d in frustum.planes:
        px = min_x if nx >= 0.0 else max_x
        py = min_y if ny >= 0.0 else max_y
        pz = min_z if nz >= 0.0 else max_z
        if nx * px + ny * py + nz * pz + d < 0.0:
            return False
    return True

def chunk_in_horizontal_view(
    cx,
    cz,
    camera_x,
    camera_z,
    yaw_degrees,
    vertical_fov_degrees,
    aspect,
    view_distance,
    chunk_size=16,
    frustum_slack_chunks=0.75,
):
    camera_y = 64.0
    frustum = CameraFrustum.from_camera(
        camera_x, camera_y, camera_z, yaw_degrees, 0.0,
        vertical_fov_degrees, aspect, near_clip=0.1,
        far_clip=max(float(view_distance) * chunk_size, chunk_size),
    )
    return chunk_in_frustum(frustum, cx, cz, chunk_size=chunk_size, world_height=128,
                            padding=chunk_size * max(0.0, float(frustum_slack_chunks)))


def cull_buried_sections_at_distance(sections, surface_min_y, distance_squared, section_height=16):
    sections = tuple(sections)
    distance_squared = float(distance_squared)
    if distance_squared < 64.0:
        return sections
    margin = 16 if distance_squared < 144.0 else 0
    cutoff = max(0, int(surface_min_y) - margin)
    section_height = max(1, int(section_height))
    return tuple(
        index for index in sections
        if (int(index) + 1) * section_height > cutoff
    )


MAX_MASKED_SECTIONS = 62


@njit(cache=True, nogil=True, fastmath=False)
def _aabb_in_frustum(planes, min_x, min_y, min_z, max_x, max_y, max_z):
    for k in range(planes.shape[0]):
        nx = planes[k, 0]
        ny = planes[k, 1]
        nz = planes[k, 2]
        px = max_x if nx >= 0.0 else min_x
        py = max_y if ny >= 0.0 else min_y
        pz = max_z if nz >= 0.0 else min_z
        if nx * px + ny * py + nz * pz + planes[k, 3] < 0.0:
            return False
    return True


@njit(cache=True, nogil=True, fastmath=False)
def cull_section_masks(
    planes, chunk_x0, chunk_z0, bounds, has_bounds,
    section_count, section_height, world_height, chunk_size, padding, masks,
):
    p = max(0.0, padding)
    for c in range(chunk_x0.shape[0]):
        x0 = chunk_x0[c] - p
        x1 = chunk_x0[c] + chunk_size + p
        z0 = chunk_z0[c] - p
        z1 = chunk_z0[c] + chunk_size + p

        column_visible = _aabb_in_frustum(planes, x0, 0.0 - p, z0, x1, world_height + p, z1)
        if not column_visible:
            any_extra = False
            for s in range(section_count):
                if has_bounds[c, s] and _aabb_in_frustum(
                    planes,
                    bounds[c, s, 0] - p, bounds[c, s, 1] - p, bounds[c, s, 2] - p,
                    bounds[c, s, 3] + p, bounds[c, s, 4] + p, bounds[c, s, 5] + p,
                ):
                    any_extra = True
                    break
            if not any_extra:
                masks[c] = 0
                continue

        mask = 0
        for s in range(section_count):
            y0 = s * section_height
            y1 = min(world_height, y0 + section_height)
            visible = _aabb_in_frustum(planes, x0, y0 - p, z0, x1, y1 + p, z1)
            if not visible and has_bounds[c, s]:
                visible = _aabb_in_frustum(
                    planes,
                    bounds[c, s, 0] - p, bounds[c, s, 1] - p, bounds[c, s, 2] - p,
                    bounds[c, s, 3] + p, bounds[c, s, 4] + p, bounds[c, s, 5] + p,
                )
            if visible:
                mask |= (1 << s)
        masks[c] = mask


_SECTION_TUPLE_CACHE = {}


def sections_from_mask(mask, section_count):
    key = (mask, section_count)
    result = _SECTION_TUPLE_CACHE.get(key)
    if result is None:
        result = tuple(i for i in range(section_count) if (mask >> i) & 1)
        if len(_SECTION_TUPLE_CACHE) < 4096:
            _SECTION_TUPLE_CACHE[key] = result
    return result


def _warm_up_cull_kernel():
    planes = np.zeros((6, 4), dtype=np.float64)
    masks = np.zeros(1, dtype=np.int64)
    cull_section_masks(
        planes, np.zeros(1, dtype=np.float64), np.zeros(1, dtype=np.float64),
        np.zeros((1, 1, 6), dtype=np.float64), np.zeros((1, 1), dtype=np.bool_),
        1, 4, 128, 16, 2.0, masks,
    )


_warm_up_cull_kernel()
