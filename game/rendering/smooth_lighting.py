
from __future__ import annotations

from engine.lighting import brightness_from_level, face_shade
_AO_MATERIAL_OVERRIDES = {
    "glass": True,          
    "leaves": True,         
    "cactus": True,         
    "portal": True,         
    "snow_block": True,     
    "fire": False,          
    "torch": False,         
    "ladder": False,        
    "rail": False,          
    "stone_pressure_plate": True,  
    "wooden_pressure_plate": True, 
}
_AO_NON_OCCLUDING_MATERIALS = frozenset(("plant", "circuits", "snow"))


def block_ao_occludes(block_type, definition=None):
    if block_type in _AO_MATERIAL_OVERRIDES:
        return _AO_MATERIAL_OVERRIDES[block_type]
    material_name = (definition or {}).get("material")
    if material_name is not None:
        return str(material_name).lower() not in _AO_NON_OCCLUDING_MATERIALS
    return block_type not in (None, "air")


def is_full_voxel_face(direction, vertices, *, epsilon=1e-6):
    direction = tuple(int(value) for value in direction)
    if len(vertices) != 4 or direction not in (
        (1, 0, 0), (-1, 0, 0), (0, 1, 0),
        (0, -1, 0), (0, 0, 1), (0, 0, -1),
    ):
        return False

    normal_axis = next(axis for axis, value in enumerate(direction) if value)
    expected_plane = 0.5 if direction[normal_axis] > 0 else -0.5
    if not all(abs(float(vertex[normal_axis]) - expected_plane) <= epsilon
               for vertex in vertices):
        return False

    tangent_axes = tuple(axis for axis in range(3) if axis != normal_axis)
    expected_corners = {(-0.5, -0.5), (-0.5, 0.5),
                        (0.5, -0.5), (0.5, 0.5)}
    actual_corners = set()
    for vertex in vertices:
        corner = tuple(float(vertex[axis]) for axis in tangent_axes)
        if not all(any(abs(corner[index] - expected) <= epsilon
                       for expected in (-0.5, 0.5))
                   for index in range(2)):
            return False
        actual_corners.add(tuple(-0.5 if abs(value + 0.5) <= epsilon else 0.5
                                 for value in corner))
    return actual_corners == expected_corners


def smooth_face_colors(
    direction,
    vertices,
    block_position,
    sample_light,
    is_ao_occluder,
    *,
    hell=False,
):
    direction = tuple(int(value) for value in direction)
    if direction not in (
        (1, 0, 0), (-1, 0, 0), (0, 1, 0),
        (0, -1, 0), (0, 0, 1), (0, 0, -1),
    ):
        raise ValueError("smooth lighting requires an axis-aligned face")
    if len(vertices) != 4:
        raise ValueError("smooth lighting requires four face vertices")

    tangent_axes = tuple(axis for axis, value in enumerate(direction) if value == 0)
    face_origin = tuple(
        int(block_position[axis]) + direction[axis] for axis in range(3)
    )
    shade = face_shade(direction)
    colors = []

    for vertex in vertices:
        signs = tuple(-1 if float(vertex[axis]) < 0.5 else 1 for axis in tangent_axes)
        side_a = list(face_origin)
        side_b = list(face_origin)
        side_a[tangent_axes[0]] += signs[0]
        side_b[tangent_axes[1]] += signs[1]
        diagonal = [
            face_origin[axis] +
            (signs[0] if axis == tangent_axes[0] else 0) +
            (signs[1] if axis == tangent_axes[1] else 0)
            for axis in range(3)
        ]

        face_light = int(sample_light(face_origin))
        side_a_light = int(sample_light(tuple(side_a)))
        side_b_light = int(sample_light(tuple(side_b)))
        if is_ao_occluder(tuple(side_a)) and is_ao_occluder(tuple(side_b)):
            diagonal_light = side_a_light
        else:
            diagonal_light = int(sample_light(tuple(diagonal)))

        brightness = sum(
            brightness_from_level(level, hell=hell)
            for level in (face_light, side_a_light, side_b_light, diagonal_light)
        ) * 0.25
        value = shade * brightness
        colors.append((value, value, value))

    return tuple(colors)
