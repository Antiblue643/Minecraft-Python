
DOOR_TYPES = ("wooden_door", "iron_door")


def _partner_map(world):
    partners = getattr(world, "door_partners", None)
    if partners is None:
        partners = {}
        world.door_partners = partners
    return partners


def is_door_mirrored(world, x, y, z):
    return bool(getattr(world, "door_hinges", {}).get((int(x), int(y), int(z)), False))


def unregister_door_hinge(world, x, y, z):
    key = (int(x), int(y), int(z))
    getattr(world, "door_hinges", {}).pop(key, None)
    partners = _partner_map(world)
    mate = partners.pop(key, None)
    if mate is not None:
        partners.pop(mate, None)


def register_door_hinges(world, x, y, z, meta):
    x, y, z = int(x), int(y), int(z)
    meta = int(meta) & 15
    if meta & 8:
        y -= 1
    state = (meta - 1) & 3
    key = (x, y, z)
    hinges = getattr(world, "door_hinges", None)
    if hinges is None:
        hinges = {}
        world.door_hinges = hinges
    hinges.setdefault(key, False)
    partners = _partner_map(world)
    own_type = world.get_block_type(x, y, z)
    if own_type not in DOOR_TYPES:
        own_type = None

    if key in partners:
        return

    if state in (0, 2):
        right_dx = -1 if state == 0 else 1
        candidates = ((x - right_dx, y, z, True),
                      (x + right_dx, y, z, False))
    else:
        right_dz = -1 if state == 1 else 1
        candidates = ((x, y, z - right_dz, True),
                      (x, y, z + right_dz, False))

    for nx, ny, nz, this_is_right in candidates:
        neighbor_key = (nx, ny, nz)
        if neighbor_key in partners:
            continue
        neighbor_type = world.get_block_type(nx, ny, nz)
        if neighbor_type not in DOOR_TYPES:
            continue
        if own_type is not None and neighbor_type != own_type:
            continue
        neighbor_meta = int(world.get_metadata(nx, ny, nz)) & 7
        if ((neighbor_meta - 1) & 3) != state:
            continue
        hinges[key] = this_is_right
        hinges[neighbor_key] = not this_is_right
        partners[key] = neighbor_key
        partners[neighbor_key] = key
        return
