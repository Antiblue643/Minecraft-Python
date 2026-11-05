FLOOR = 1
WEST = 2
EAST = 4
NORTH = 8
SOUTH = 16
CEILING = 32
SIDES = WEST | EAST | NORTH | SOUTH
ATTACHED = SIDES | CEILING
ALT_ROW = 64
MIRROR_U = 128


def is_normal_cube(definition):
    if not definition:
        return False
    return (
        definition.get("collision", True) is not False
        and definition.get("model", "cube") == "cube"
        and not definition.get("transparent", False)
        and definition.get("nCull", "all") == "all"
    )


def can_catch_fire(definition):
    if not definition:
        return False
    return int(definition.get("encourageFire", 0) or 0) > 0


def fire_render_flags(x, y, z, below, west, east, north, south, above):
    flags = 0
    if (x + y + z) & 1:
        flags |= ALT_ROW
    if (int(x / 2) + int(y / 2) + int(z / 2)) & 1:
        flags |= MIRROR_U

    if is_normal_cube(below) or can_catch_fire(below):
        flags |= FLOOR
    else:
        if can_catch_fire(west):
            flags |= WEST
        if can_catch_fire(east):
            flags |= EAST
        if can_catch_fire(north):
            flags |= NORTH
        if can_catch_fire(south):
            flags |= SOUTH
        if can_catch_fire(above):
            flags |= CEILING
        if not flags & ATTACHED:
            flags |= FLOOR
    return flags
