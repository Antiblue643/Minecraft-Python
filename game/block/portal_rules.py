
from collections import deque


MIN_WIDTH = 2
MAX_WIDTH = 21
MIN_HEIGHT = 3
MAX_HEIGHT = 21
_DIRECTIONS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
               (0, -1, 0), (0, 0, 1), (0, 0, -1))


def _block(world, x, y, z):
    return world.get_block_type(int(x), int(y), int(z))


def _frame_matches(world, base, width, height, axis, interior_types):
    x, y, z = base
    dx, dz = (1, 0) if axis == "x" else (0, 1)

    for offset in range(width):
        for frame_y in (y - 1, y + height):
            if _block(world, x + dx * offset, frame_y, z + dz * offset) != "obsidian":
                return False
    for frame_x in (-1, width):
        for offset_y in range(height):
            if _block(
                world, x + dx * frame_x, y + offset_y, z + dz * frame_x
            ) != "obsidian":
                return False

    return all(
        _block(world, x + dx * offset, y + offset_y, z + dz * offset)
        in interior_types
        for offset in range(width)
        for offset_y in range(height)
    )


def _find_ignitable_frame(world, x, y, z):
    for axis in ("x", "z"):
        for width in range(MIN_WIDTH, MAX_WIDTH + 1):
            for height in range(MIN_HEIGHT, MAX_HEIGHT + 1):
                for horizontal_offset in range(width):
                    for vertical_offset in range(height):
                        base = (
                            x - horizontal_offset if axis == "x" else x,
                            y - vertical_offset,
                            z - horizontal_offset if axis == "z" else z,
                        )
                        if _frame_matches(
                            world, base, width, height, axis,
                            {None, "fire"},
                        ):
                            return base, width, height, axis
    return None


def ignite_portal(world, x, y, z):
    frame = _find_ignitable_frame(world, int(x), int(y), int(z))
    if frame is None:
        return False

    base, width, height, axis = frame
    world._portal_update_depth = getattr(world, "_portal_update_depth", 0) + 1
    try:
        dx, dz = (1, 0) if axis == "x" else (0, 1)
        for offset in range(width):
            for vertical_offset in range(height):
                world.set_block(
                    base[0] + dx * offset,
                    base[1] + vertical_offset,
                    base[2] + dz * offset,
                    "portal",
                    meta=0,
                )
    finally:
        world._portal_update_depth -= 1
    return True


def portal_component(world, start):
    pending = deque([start])
    visited = {start}
    while pending:
        x, y, z = pending.popleft()
        for dx, dy, dz in _DIRECTIONS:
            neighbor = (x + dx, y + dy, z + dz)
            if neighbor not in visited and _block(world, *neighbor) == "portal":
                visited.add(neighbor)
                pending.append(neighbor)
    return visited


def _component_has_frame(world, component):
    xs = [pos[0] for pos in component]
    ys = [pos[1] for pos in component]
    zs = [pos[2] for pos in component]
    if len(set(xs)) == 1 and len(set(zs)) > 1:
        axis = "z"
        base = (xs[0], min(ys), min(zs))
        width = max(zs) - min(zs) + 1
    elif len(set(zs)) == 1 and len(set(xs)) > 1:
        axis = "x"
        base = (min(xs), min(ys), zs[0])
        width = max(xs) - min(xs) + 1
    else:
        return False
    height = max(ys) - min(ys) + 1
    if not (MIN_WIDTH <= width <= MAX_WIDTH
            and MIN_HEIGHT <= height <= MAX_HEIGHT
            and len(component) == width * height):
        return False
    return _frame_matches(world, base, width, height, axis, {"portal"})


def _remove_component(world, component):
    world._portal_update_depth = getattr(world, "_portal_update_depth", 0) + 1
    try:
        for x, y, z in component:
            effects = getattr(world, "effects", None)
            if effects is not None:
                effects.add_block_destroy_effects(
                    world, x, y, z, "portal", meta=0
                )
            world.remove_block(x, y, z)
    finally:
        world._portal_update_depth -= 1
    sound_player = getattr(world, "sound_player", None)
    if callable(sound_player):
        sound_player(
            "random.glass",
            volume=1.0,
            pitch=0.8,
            position=tuple(
                (min(pos[axis] for pos in component)
                 + max(pos[axis] for pos in component)) / 2.0 + 0.5
                for axis in range(3)
            ),
        )


def on_block_changed(world, x, y, z, old_type, new_type):
    if getattr(world, "_portal_update_depth", 0):
        return
    if new_type == "fire" and ignite_portal(world, x, y, z):
        return

    candidates = {
        (int(x) + dx, int(y) + dy, int(z) + dz)
        for dx, dy, dz in _DIRECTIONS
        if _block(world, x + dx, y + dy, z + dz) == "portal"
    }
    if new_type == "portal":
        candidates.add((int(x), int(y), int(z)))
    checked = set()
    for candidate in candidates:
        if candidate in checked or _block(world, *candidate) != "portal":
            continue
        component = portal_component(world, candidate)
        checked.update(component)
        if not _component_has_frame(world, component):
            _remove_component(world, component)
