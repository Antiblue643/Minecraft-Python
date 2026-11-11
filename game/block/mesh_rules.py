def face_neighbor_direction(block_type, model_name, direction):
    if model_name == "liquid" or block_type in ("water", "lava"):
        return (0, 1, 0)
    if model_name == "rail" or block_type == "rail":
        return (0, 1, 0)
    return tuple(direction)


PARTIAL_MODELS = frozenset({"slab", "stairs", "farmland", "redstone_wire", "redstone_repeater", "snow_layer"})


def quad_on_voxel_boundary(direction, vertices, epsilon=1e-6):
    dx, dy, dz = direction
    if dx:
        axis, boundary = 0, (0.5 if dx > 0 else -0.5)
    elif dy:
        axis, boundary = 1, (0.5 if dy > 0 else -0.5)
    elif dz:
        axis, boundary = 2, (0.5 if dz > 0 else -0.5)
    else:
        return False
    return all(abs(float(vertex[axis]) - boundary) < epsilon for vertex in vertices)


def definition_for_id(defs_by_id, block_id):
    if block_id <= 0 or block_id >= len(defs_by_id):
        return None
    return defs_by_id[block_id]


def face_is_hidden(n_cull, block_id, neighbor_id, defs_by_id, is_transparent=False):
    if neighbor_id == 0 or n_cull == "none":
        return False
    if n_cull == "liquid":
        return True
    neighbor_def = definition_for_id(defs_by_id, neighbor_id)
    if not neighbor_def:
        return False
    if neighbor_def.get("model") in PARTIAL_MODELS or neighbor_def.get("partial", False):
        return False

    if n_cull == "type":
        if block_id == neighbor_id:
            return True
        return neighbor_def.get("nCull", "all") == "all"
    if is_transparent and neighbor_def.get("nCull", "all") == "all":
        return True
    return neighbor_def.get("nCull", "all") == "all"


def destroy_stage_tile_uv(atlas_index, point, face):
    x, y, z = (max(0.0, min(1.0, float(c) + 0.5)) for c in point)
    col = int(atlas_index) % 16
    row = int(atlas_index) // 16
    tile = 1.0 / 16.0
    u0 = col * tile
    v0 = 1.0 - (row + 1) * tile
    if face == "+x":
        uf, vf = 1.0 - z, y
    elif face == "-x":
        uf, vf = z, y
    elif face == "+z":
        uf, vf = x, y
    elif face == "-z":
        uf, vf = 1.0 - x, y
    elif face == "+y":
        uf, vf = x, 1.0 - z
    else:
        uf, vf = x, z
    return u0 + uf * tile, v0 + vf * tile
