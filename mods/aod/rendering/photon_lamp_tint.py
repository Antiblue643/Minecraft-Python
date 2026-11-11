
from array import array
import math
import struct

from ..common import PHOTON_LAMP_LIT_VARIANT

_FLOATS_PER_VERTEX = 8
_VERTICES_PER_FACE = 6
_FLOATS_PER_FACE = _FLOATS_PER_VERTEX * _VERTICES_PER_FACE
_FACE_EPSILON = 1.0e-4


def signal_wavelength_nm(signal):
    try:
        value = max(1, min(15, int(signal)))
    except (TypeError, ValueError, OverflowError):
        value = 1
    return 700.0 - (value - 1) * (300.0 / 14.0)


def wavelength_tint(signal):
    wavelength = signal_wavelength_nm(signal)
    if wavelength < 440.0:
        red = -(wavelength - 440.0) / (440.0 - 380.0)
        green = 0.0
        blue = 1.0
    elif wavelength < 490.0:
        red = 0.0
        green = (wavelength - 440.0) / (490.0 - 440.0)
        blue = 1.0
    elif wavelength < 510.0:
        red = 0.0
        green = 1.0
        blue = -(wavelength - 510.0) / (510.0 - 490.0)
    elif wavelength < 580.0:
        red = (wavelength - 510.0) / (580.0 - 510.0)
        green = 1.0
        blue = 0.0
    elif wavelength < 645.0:
        red = 1.0
        green = -(wavelength - 645.0) / (645.0 - 580.0)
        blue = 0.0
    else:
        red, green, blue = 1.0, 0.0, 0.0

    if wavelength < 420.0:
        factor = 0.3 + 0.7 * (wavelength - 380.0) / (420.0 - 380.0)
    elif wavelength > 700.0:
        factor = 0.3 + 0.7 * (780.0 - wavelength) / (780.0 - 700.0)
    else:
        factor = 1.0

    if wavelength > 645.0:
        red *= 0.72 + 0.28 * (700.0 - wavelength) / (700.0 - 645.0)

    gamma = 0.8
    return tuple(
        max(0.0, min(1.0, (component * factor) ** gamma))
        for component in (red, green, blue)
    )


def _face_block_cell(values, base):
    p0 = values[base:base + 3]
    p1 = values[base + _FLOATS_PER_VERTEX:base + _FLOATS_PER_VERTEX + 3]
    p2 = values[base + 2 * _FLOATS_PER_VERTEX:base + 2 * _FLOATS_PER_VERTEX + 3]
    e1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
    e2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
    normal = (
        e1[1] * e2[2] - e1[2] * e2[1],
        e1[2] * e2[0] - e1[0] * e2[2],
        e1[0] * e2[1] - e1[1] * e2[0],
    )
    axis = max(range(3), key=lambda index: abs(normal[index]))
    magnitude = abs(normal[axis])
    if magnitude <= 1.0e-10:
        return None
    if any(abs(normal[index]) > magnitude * 1.0e-3
           for index in range(3) if index != axis):
        return None

    center = [0.0, 0.0, 0.0]
    for vertex in range(_VERTICES_PER_FACE):
        offset = base + vertex * _FLOATS_PER_VERTEX
        for component in range(3):
            center[component] += values[offset + component] / _VERTICES_PER_FACE
    direction = [0.0, 0.0, 0.0]
    direction[axis] = 1.0 if normal[axis] > 0.0 else -1.0
    return tuple(
        int(math.floor(center[index] - direction[index] * _FACE_EPSILON + 0.5))
        for index in range(3)
    )


def _tint_opaque_mesh_bytes_with_index(mesh_bytes, chunk, section_index):
    if not mesh_bytes:
        return mesh_bytes, 0, {}
    values = array("f")
    values.frombytes(mesh_bytes)
    complete_faces = len(values) // _FLOATS_PER_FACE
    if complete_faces <= 0:
        return mesh_bytes, 0, {}

    blocks = getattr(chunk, "blocks", ())
    metadata = getattr(chunk, "metadata", ())
    id_to_type = getattr(chunk, "_id_to_block_type", ())
    width = int(getattr(chunk, "width", 0) or 0)
    depth = int(getattr(chunk, "depth", 0) or 0)
    height = int(getattr(chunk, "height", 0) or 0)
    if width <= 0 or depth <= 0 or height <= 0:
        return mesh_bytes, 0, {}

    start_x = int(getattr(chunk, "start_x", 0))
    start_z = int(getattr(chunk, "start_z", 0))
    indexed = {}
    tinted_faces = 0
    for face_index in range(complete_faces):
        base = face_index * _FLOATS_PER_FACE
        cell = _face_block_cell(values, base)
        if cell is None:
            continue
        local_x, y, local_z = cell
        if not (0 <= local_x < width and 0 <= local_z < depth and 0 <= y < height):
            continue
        block_index = (local_x * depth + local_z) * height + y
        if block_index >= len(blocks) or block_index >= len(metadata):
            continue
        block_id = int(blocks[block_index])
        if block_id <= 0 or block_id >= len(id_to_type):
            continue
        if id_to_type[block_id] != PHOTON_LAMP_LIT_VARIANT:
            continue
        signal = int(metadata[block_index]) & 15
        if signal <= 0:
            continue
        tint = wavelength_tint(signal)
        base_colors = []
        for vertex in range(_VERTICES_PER_FACE):
            offset = base + vertex * _FLOATS_PER_VERTEX + 5
            original = (
                float(values[offset]), float(values[offset + 1]),
                float(values[offset + 2]),
            )
            base_colors.extend(original)
            values[offset] = original[0] * tint[0]
            values[offset + 1] = original[1] * tint[1]
            values[offset + 2] = original[2] * tint[2]
        world_position = (start_x + local_x, y, start_z + local_z)
        indexed.setdefault(world_position, []).append((
            int(section_index), face_index, tuple(base_colors),
        ))
        tinted_faces += 1

    if tinted_faces <= 0:
        return mesh_bytes, 0, {}
    return values.tobytes(), tinted_faces, indexed


def tint_opaque_mesh_bytes(mesh_bytes, chunk):
    tinted, count, _index = _tint_opaque_mesh_bytes_with_index(
        mesh_bytes, chunk, 0,
    )
    return tinted, count


def _set_lamp_metadata_fast(world, position, signal):
    try:
        get_chunk_index = getattr(world, "_get_chunk_index", None)
        if not callable(get_chunk_index):
            return False
        chunk_key = get_chunk_index(position[0], position[2])
        chunk = (getattr(world, "chunks", {}) or {}).get(chunk_key)
        if chunk is None or not getattr(chunk, "_has_mesh", False):
            return False
        face_index = getattr(chunk, "_aod_photon_tint_face_index", {})
        if position not in face_index:
            return False
        if chunk.get_block_type(*position) != PHOTON_LAMP_LIT_VARIANT:
            return False
        if int(chunk.get_metadata(*position)) == int(signal):
            return True
        chunk.set_metadata(*position, int(signal))
        world._aod_photon_radiation_revision = int(
            getattr(world, "_aod_photon_radiation_revision", 0)
        ) + 1
        overrides = getattr(world, "block_overrides", None)
        if isinstance(overrides, dict):
            overrides.setdefault(chunk_key, {})[tuple(position)] = (
                PHOTON_LAMP_LIT_VARIANT, int(signal) & 15,
            )
        world._player_edit_revision = int(
            getattr(world, "_player_edit_revision", 0)
        ) + 1
        pending = getattr(chunk, "_aod_photon_pending_tints", None)
        if not isinstance(pending, dict):
            pending = {}
            chunk._aod_photon_pending_tints = pending
        pending[tuple(position)] = int(signal) & 15
        return True
    except Exception:
        return False


def _flush_pending_tints(chunk):
    pending = getattr(chunk, "_aod_photon_pending_tints", None)
    if not isinstance(pending, dict) or not pending:
        return 0
    face_index = getattr(chunk, "_aod_photon_tint_face_index", {})
    by_section = {}
    consumed = set()
    for position, signal in tuple(pending.items()):
        faces = face_index.get(position, ())
        if not faces:
            continue
        tint = wavelength_tint(signal)
        for section_index, face_number, base_colors in faces:
            by_section.setdefault(section_index, []).append(
                (int(face_number), base_colors, tint)
            )
        consumed.add(position)

    if not by_section:
        return 0
    vbo = getattr(chunk, "_vbo_opaque", None)
    if not vbo:
        return 0
    try:
        from OpenGL.GL import GL_ARRAY_BUFFER, glBindBuffer, glBufferSubData
    except Exception:
        return 0

    uploaded_sections = 0
    try:
        glBindBuffer(GL_ARRAY_BUFFER, int(vbo))
        face_bytes = _FLOATS_PER_FACE * 4
        vertex_bytes = _FLOATS_PER_VERTEX * 4
        for section_index, raw_updates in by_section.items():
            if not (0 <= int(section_index) < len(chunk._sections)):
                continue
            section = chunk._sections[int(section_index)]
            source = section.get("mesh_opaque", b"")
            if not source:
                continue
            data = source if isinstance(source, bytearray) else bytearray(source)
            section["mesh_opaque"] = data
            updates = sorted(raw_updates, key=lambda item: item[0])
            for face_number, base_colors, tint in updates:
                face_base = face_number * _FLOATS_PER_FACE
                for vertex in range(_VERTICES_PER_FACE):
                    color_base = vertex * 3
                    byte_offset = (face_base + vertex * _FLOATS_PER_VERTEX + 5) * 4
                    struct.pack_into(
                        "<fff", data, byte_offset,
                        base_colors[color_base] * tint[0],
                        base_colors[color_base + 1] * tint[1],
                        base_colors[color_base + 2] * tint[2],
                    )

            runs = []
            for face_number, _base_colors, _tint in updates:
                if runs and face_number - runs[-1][1] - 1 <= 2:
                    runs[-1] = (runs[-1][0], face_number)
                else:
                    runs.append((face_number, face_number))
            total_bytes = len(data)
            covered_bytes = sum((end - begin + 1) * face_bytes for begin, end in runs)
            dense = (
                len(runs) > 12
                or (total_bytes > 0 and covered_bytes >= total_bytes * 0.65)
            )
            first_opaque = int(section.get("first_opaque", 0))
            if dense:
                glBufferSubData(GL_ARRAY_BUFFER, first_opaque * vertex_bytes,
                                len(data), data)
            else:
                for begin, end in runs:
                    start_byte = begin * face_bytes
                    stop_byte = (end + 1) * face_bytes
                    payload = bytes(data[start_byte:stop_byte])
                    buffer_offset = first_opaque * vertex_bytes + start_byte
                    glBufferSubData(
                        GL_ARRAY_BUFFER, buffer_offset, len(payload), payload,
                    )
            uploaded_sections += 1
    except Exception:
        return 0
    finally:
        try:
            glBindBuffer(GL_ARRAY_BUFFER, 0)
        except Exception:
            pass
    for position in consumed:
        pending.pop(position, None)
    return uploaded_sections


def _section_contains_lit_lamp(chunk, section_index):
    blocks = getattr(chunk, "blocks", ())
    id_to_type = getattr(chunk, "_id_to_block_type", ())
    width = int(getattr(chunk, "width", 0) or 0)
    depth = int(getattr(chunk, "depth", 0) or 0)
    height = int(getattr(chunk, "height", 0) or 0)
    section_height = int(getattr(chunk, "section_height", 4) or 4)
    y0 = max(0, int(section_index) * section_height)
    y1 = min(height, y0 + section_height)
    for local_x in range(width):
        for local_z in range(depth):
            base = (local_x * depth + local_z) * height
            for y in range(y0, y1):
                index = base + y
                if index >= len(blocks):
                    continue
                block_id = int(blocks[index])
                if (0 < block_id < len(id_to_type)
                        and id_to_type[block_id] == PHOTON_LAMP_LIT_VARIANT):
                    return True
    return False


def _reupload_opaque_vbo(chunk):
    if not getattr(chunk, "_has_mesh", False):
        return
    opaque_data = b"".join(
        section.get("mesh_opaque", b"")
        for section in getattr(chunk, "_sections", ())
    )
    if opaque_data:
        chunk._vbo_opaque = chunk._upload_vbo(
            getattr(chunk, "_vbo_opaque", None), opaque_data,
        )


def initialize():
    _install_chunk_tint_hook()
    _install_glass_break_sound_hook()


def _install_chunk_tint_hook():
    try:
        from game.world.chunk import Chunk
    except Exception:
        return

    original_upload = Chunk._upload_vbo
    if not getattr(original_upload, "_aod_photon_patched", False):
        def upload_vbo(chunk, old_vbo, data):
            if (getattr(chunk, "_aod_photon_defer_opaque_upload", False)
                    and not getattr(chunk, "_aod_photon_opaque_upload_deferred", False)):
                chunk._aod_photon_opaque_upload_deferred = True
                chunk._aod_photon_pending_opaque = data
                return old_vbo
            return original_upload(chunk, old_vbo, data)
        upload_vbo._aod_photon_patched = True
        Chunk._upload_vbo = upload_vbo

    original_build = Chunk._build_mesh
    if getattr(original_build, "_aod_photon_patched", False):
        return

    def build_mesh(chunk, section_index=None, commit_partial=False):
        dirty_before = set(getattr(chunk, "dirty_sections", ()))
        tint_candidates = {
            index for index in dirty_before
            if _section_contains_lit_lamp(chunk, index)
        }
        defer_upload = bool(tint_candidates)
        if defer_upload:
            chunk._aod_photon_defer_opaque_upload = True
            chunk._aod_photon_opaque_upload_deferred = False
            chunk._aod_photon_pending_opaque = None
        try:
            result = original_build(
                chunk, section_index=section_index, commit_partial=commit_partial,
            )
        finally:
            if defer_upload:
                chunk._aod_photon_defer_opaque_upload = False

        rebuilt = dirty_before.difference(getattr(chunk, "dirty_sections", ()))
        tint_sections = rebuilt.intersection(tint_candidates)
        index_map = getattr(chunk, "_aod_photon_tint_face_index", None)
        if not isinstance(index_map, dict):
            index_map = {}
            chunk._aod_photon_tint_face_index = index_map
        if rebuilt:
            for position, entries in tuple(index_map.items()):
                kept = [entry for entry in entries if entry[0] not in rebuilt]
                if kept:
                    index_map[position] = kept
                else:
                    index_map.pop(position, None)

        tinted = False
        for index in sorted(tint_sections):
            section = chunk._sections[index]
            mesh_bytes, face_count, new_index = _tint_opaque_mesh_bytes_with_index(
                section.get("mesh_opaque", b""), chunk, index,
            )
            if face_count:
                section["mesh_opaque"] = mesh_bytes
                tinted = True
                for position, entries in new_index.items():
                    index_map.setdefault(position, []).extend(entries)

        pending = getattr(chunk, "_aod_photon_pending_tints", None)
        if isinstance(pending, dict) and pending:
            for position in tuple(pending):
                if any(entry[0] in rebuilt for entry in index_map.get(position, ())):
                    pending.pop(position, None)

        should_commit = (
            all(section.get("built", False) for section in chunk._sections)
            and (not getattr(chunk, "dirty_sections", ()) or commit_partial)
        )
        if defer_upload and should_commit:
            if tinted:
                opaque_data = b"".join(
                    section.get("mesh_opaque", b"")
                    for section in getattr(chunk, "_sections", ())
                )
            else:
                opaque_data = getattr(chunk, "_aod_photon_pending_opaque", None)
            if opaque_data:
                chunk._vbo_opaque = original_upload(
                    chunk, getattr(chunk, "_vbo_opaque", None), opaque_data,
                )
        return result


    build_mesh._aod_photon_patched = True
    Chunk._build_mesh = build_mesh

    original_draw = getattr(Chunk, "draw", None)
    if callable(original_draw) and not getattr(original_draw, "_aod_photon_patched", False):
        def draw(chunk, *args, **kwargs):
            _flush_pending_tints(chunk)
            return original_draw(chunk, *args, **kwargs)
        draw._aod_photon_patched = True
        Chunk.draw = draw


def _install_glass_break_sound_hook():
    try:
        from game.entities.player import Player
    except Exception:
        return
    original = Player._play_block_sound
    if getattr(original, "_aod_photon_patched", False):
        return

    def play_block_sound(player, block_type, position, *, breaking=False, digging=False):
        if block_type in ("photon_lamp", PHOTON_LAMP_LIT_VARIANT) and breaking and not digging:
            sound_player = getattr(player, "sound_player", None)
            if callable(sound_player):
                sound_player(
                    "random.glass", volume=1.0, pitch=0.8,
                    position=position,
                )
            return
        return original(
            player, block_type, position,
            breaking=breaking, digging=digging,
        )

    play_block_sound._aod_photon_patched = True
    Player._play_block_sound = play_block_sound
