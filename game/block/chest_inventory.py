from __future__ import annotations


_HORIZONTAL = ((-1, 0), (1, 0), (0, -1), (0, 1))


def _adjacent_chests(world, x, y, z):
    if not hasattr(world, "get_block_type"):
        return []
    result = []
    for dx, dz in _HORIZONTAL:
        nx, nz = int(x) + dx, int(z) + dz
        if world.get_block_type(nx, int(y), nz) == "chest":
            result.append((nx, int(y), nz))
    return result


def find_chest_pair(world, x, y, z):
    position = (int(x), int(y), int(z))
    if world.get_block_type(*position) != "chest":
        return None
    neighbors = _adjacent_chests(world, *position)
    if len(neighbors) != 1:
        return None
    other = neighbors[0]
    if _adjacent_chests(world, *other) != [position]:
        return None
    if position[0] != other[0]:
        return tuple(sorted((position, other), key=lambda p: p[0]))
    return tuple(sorted((position, other), key=lambda p: p[2]))


class CombinedChestInventory:

    SIZE = 54
    stack_limit = 64
    is_large_chest = True

    def __init__(self, first, second):
        if first is second:
            raise ValueError("a double chest needs two distinct chest tile entities")
        self.chests = (first, second)
        self.world = getattr(first, "world", None)
        self.x, self.y, self.z = int(first.x), int(first.y), int(first.z)

    def get_stack(self, slot):
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            return None
        if not 0 <= slot < self.SIZE:
            return None
        chest_index, local_slot = divmod(slot, 27)
        return self.chests[chest_index].get_stack(local_slot)

    def set_stack(self, slot, stack):
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            return
        if not 0 <= slot < self.SIZE:
            return
        chest_index, local_slot = divmod(slot, 27)
        self.chests[chest_index].set_stack(local_slot, stack)
