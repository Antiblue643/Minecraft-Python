from __future__ import annotations

import math

import numpy as np

FLOATS_PER_VERTEX = 8
VERTICES_PER_QUAD = 6
FLOATS_PER_QUAD = FLOATS_PER_VERTEX * VERTICES_PER_QUAD


def build_sorted_transparent_vertices(
    meshes,
    camera_pos,
    camera_angles=(0.0, 0.0, 0.0),
    *,
    output_origin=None,
):
    cam_x, cam_y, cam_z = (float(v) for v in camera_pos)
    pitch, yaw, _roll = (float(v) for v in camera_angles)
    pitch_r = pitch * 0.017453292519943295
    yaw_r = yaw * 0.017453292519943295
    forward_x = -np.sin(yaw_r) * np.cos(pitch_r)
    forward_y = np.sin(pitch_r)
    forward_z = -np.cos(yaw_r) * np.cos(pitch_r)

    if output_origin is None:
        out_x, out_y, out_z = cam_x, cam_y, cam_z
    else:
        out_x, out_y, out_z = (float(v) for v in output_origin)

    face_batches = []
    depth_batches = []
    chunk_x_batches = []
    chunk_z_batches = []
    for raw_mesh, chunk_x, chunk_z in meshes:
        if not raw_mesh:
            continue
        float_count = len(raw_mesh) // np.dtype(np.float32).itemsize
        float_count -= float_count % FLOATS_PER_QUAD
        if float_count <= 0:
            continue
        values = np.frombuffer(raw_mesh, dtype=np.float32, count=float_count)
        faces = values.reshape((-1, VERTICES_PER_QUAD, FLOATS_PER_VERTEX))
        centers = faces[:, :, :3].mean(axis=1, dtype=np.float64)
        cx, cz = float(chunk_x), float(chunk_z)
        depths = (
            (centers[:, 0] + cx - cam_x) * forward_x
            + (centers[:, 1] - cam_y) * forward_y
            + (centers[:, 2] + cz - cam_z) * forward_z
        )
        face_batches.append(faces)
        depth_batches.append(depths)
        chunk_x_batches.append(np.full(len(faces), cx, dtype=np.float64))
        chunk_z_batches.append(np.full(len(faces), cz, dtype=np.float64))

    if not face_batches:
        return np.empty(0, dtype=np.float32)

    all_faces = np.concatenate(face_batches, axis=0)
    all_depths = np.concatenate(depth_batches)
    all_chunk_x = np.concatenate(chunk_x_batches)
    all_chunk_z = np.concatenate(chunk_z_batches)
    order = np.argsort(-all_depths, kind="mergesort")
    ordered = all_faces[order]
    offset_x = (all_chunk_x[order] - out_x).astype(np.float32)
    offset_y = np.float32(-out_y)
    offset_z = (all_chunk_z[order] - out_z).astype(np.float32)
    ordered[:, :, 0] += offset_x[:, None]
    ordered[:, :, 1] += offset_y
    ordered[:, :, 2] += offset_z[:, None]
    return np.ascontiguousarray(ordered.reshape(-1), dtype=np.float32)


class TransparentMeshCache:

    __slots__ = ("_key", "_sources", "_vertices", "_origin")

    def __init__(self):
        self._key = None
        self._sources = ()
        self._vertices = np.empty(0, dtype=np.float32)
        self._origin = (0.0, 0.0, 0.0)

    def clear(self):
        self._key = None
        self._sources = ()
        self._vertices = np.empty(0, dtype=np.float32)
        self._origin = (0.0, 0.0, 0.0)

    def get(self, meshes, camera_pos, camera_angles=(0.0, 0.0, 0.0), chunk_size=16):
        sources = tuple(
            (raw_mesh, int(chunk_x), int(chunk_z))
            for raw_mesh, chunk_x, chunk_z in meshes
            if raw_mesh
        )
        pitch, yaw, _roll = (float(v) for v in camera_angles)
        signature = tuple((id(data), cx, cz) for data, cx, cz in sources)
        key = (pitch, yaw, signature)
        if key == self._key:
            return self._vertices, self._origin, True

        cam_x, _cam_y, cam_z = (float(v) for v in camera_pos)
        size = max(1, int(chunk_size))
        origin_x = float(math.floor(cam_x / size) * size)
        origin_z = float(math.floor(cam_z / size) * size)
        origin = (origin_x, 0.0, origin_z)
        vertices = build_sorted_transparent_vertices(
            sources,
            camera_pos,
            camera_angles,
            output_origin=origin,
        )

        self._key = key
        self._sources = sources
        self._vertices = vertices
        self._origin = origin
        return vertices, origin, False
