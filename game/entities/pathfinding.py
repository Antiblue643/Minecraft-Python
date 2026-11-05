from __future__ import annotations

import math

MAX_EXPANDED_NODES = 1500

_NON_SOLID = frozenset({"torch", "fire", "ladder", "rail"})
_LIQUIDS = frozenset({"water", "lava"})


_SOLID_TYPE_CACHE = {}


def _is_solid_type(block_type) -> bool:
    if block_type is None or block_type in _NON_SOLID or block_type in _LIQUIDS:
        return False
    cached = _SOLID_TYPE_CACHE.get(block_type)
    if cached is not None:
        return cached
    try:
        from game.block.material import material_for_block
        result = bool(material_for_block(block_type).is_solid)
    except Exception:
        return block_type not in {
            "sapling", "yellow_flower", "red_flower", "brown_mushroom",
            "red_mushroom", "crops",
        }
    _SOLID_TYPE_CACHE[block_type] = result
    return result


class PathPoint:
    __slots__ = (
        "x", "y", "z", "index", "total_path_distance", "distance_to_next",
        "distance_to_target", "previous", "is_first",
    )

    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z
        self.index = -1
        self.total_path_distance = 0.0
        self.distance_to_next = 0.0
        self.distance_to_target = 0.0
        self.previous = None
        self.is_first = False

    def distance_to(self, other) -> float:
        dx = other.x - self.x
        dy = other.y - self.y
        dz = other.z - self.z
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def is_assigned(self) -> bool:
        return self.index >= 0

    def same_cell(self, other) -> bool:
        return self.x == other.x and self.y == other.y and self.z == other.z


class PathHeap:
    def __init__(self):
        self._points = []

    def clear(self):
        self._points = []

    def is_empty(self) -> bool:
        return not self._points

    def add(self, point):
        if point.index >= 0:
            raise RuntimeError("PathPoint is already in the heap")
        point.index = len(self._points)
        self._points.append(point)
        self._sort_back(point.index)
        return point

    def dequeue(self):
        points = self._points
        first = points[0]
        last = points.pop()
        if points:
            points[0] = last
            last.index = 0
            self._sort_forward(0)
        first.index = -1
        return first

    def change_distance(self, point, distance):
        old = point.distance_to_target
        point.distance_to_target = distance
        if distance < old:
            self._sort_back(point.index)
        else:
            self._sort_forward(point.index)

    def _sort_back(self, i):
        points = self._points
        point = points[i]
        key = point.distance_to_target
        while i > 0:
            parent = (i - 1) >> 1
            other = points[parent]
            if key >= other.distance_to_target:
                break
            points[i] = other
            other.index = i
            i = parent
        points[i] = point
        point.index = i

    def _sort_forward(self, i):
        points = self._points
        count = len(points)
        point = points[i]
        key = point.distance_to_target
        while True:
            left = 1 + (i << 1)
            right = left + 1
            if left >= count:
                break
            left_point = points[left]
            left_key = left_point.distance_to_target
            if right >= count:
                right_point = None
                right_key = math.inf
            else:
                right_point = points[right]
                right_key = right_point.distance_to_target
            if left_key < right_key:
                if left_key >= key:
                    break
                points[i] = left_point
                left_point.index = i
                i = left
            else:
                if right_key >= key:
                    break
                points[i] = right_point
                right_point.index = i
                i = right
        points[i] = point
        point.index = i


class PathEntity:
    def __init__(self, points):
        self.points = list(points)
        self.path_length = len(self.points)
        self.path_index = 0

    def increment_path_index(self):
        self.path_index += 1

    def is_finished(self) -> bool:
        return self.path_index >= len(self.points)

    def get_position(self, entity):
        point = self.points[self.path_index]
        offset = int(float(entity.size[0]) + 1.0) * 0.5 - 0.5
        return point.x + offset, point.y, point.z + offset


class Pathfinder:
    def __init__(self, world):
        self.world = world
        self.heap = PathHeap()
        self.point_map = {}
        self._type_cache = {}
        self.expanded = 0

    def _block_type(self, x, y, z):
        key = (x, y, z)
        cache = self._type_cache
        if key in cache:
            return cache[key]
        getter = getattr(self.world, "get_block_type", None)
        value = getter(x, y, z) if getter is not None else None
        cache[key] = value
        return value

    def create_path_to_entity(self, entity, target, max_distance):
        tx = float(target.pos[0]) + 0.5
        ty = float(target.pos[1]) + 0.5
        tz = float(target.pos[2]) + 0.5
        return self._create_path(entity, tx, ty, tz, max_distance)

    def create_path_to_xyz(self, entity, x, y, z, max_distance):
        return self._create_path(entity, x + 0.5, y + 0.5, z + 0.5, max_distance)

    def _create_path(self, entity, tx, ty, tz, max_distance):
        self.heap.clear()
        self.point_map.clear()
        self._type_cache.clear()
        self.expanded = 0
        width = float(entity.size[0])
        height = float(entity.size[1])
        half_w = width / 2.0
        start = self._open(
            math.floor(entity.pos[0] - half_w + 0.5),
            math.floor(entity.pos[1] + 0.5 + 1e-4),
            math.floor(entity.pos[2] - float(entity.size[2]) / 2.0 + 0.5),
        )
        goal = self._open(
            math.floor(tx - half_w), math.floor(ty), math.floor(tz - half_w),
        )
        size = PathPoint(
            math.floor(width + 1.0), math.floor(height + 1.0), math.floor(width + 1.0),
        )
        return self._add_to_path(start, goal, size, max_distance)

    def _add_to_path(self, start, goal, size, max_distance):
        start.total_path_distance = 0.0
        start.distance_to_next = start.distance_to(goal)
        start.distance_to_target = start.distance_to_next
        self.heap.clear()
        self.heap.add(start)
        best = start
        heap = self.heap
        while not heap.is_empty():
            current = heap.dequeue()
            if current.same_cell(goal):
                return self._build(start, goal)
            if current.distance_to(goal) < best.distance_to(goal):
                best = current
            current.is_first = True
            self.expanded += 1
            if self.expanded > MAX_EXPANDED_NODES:
                break
            for option in self._find_options(current, size, goal, max_distance):
                total = current.total_path_distance + current.distance_to(option)
                if not option.is_assigned() or total < option.total_path_distance:
                    option.previous = current
                    option.total_path_distance = total
                    option.distance_to_next = option.distance_to(goal)
                    if option.is_assigned():
                        heap.change_distance(
                            option, option.total_path_distance + option.distance_to_next,
                        )
                    else:
                        option.distance_to_target = (
                            option.total_path_distance + option.distance_to_next
                        )
                        heap.add(option)
        if best is start:
            return None
        return self._build(start, best)

    def _find_options(self, point, size, goal, max_distance):
        vertical = 0
        if self._vertical_offset(point.x, point.y + 1, point.z, size) > 0:
            vertical = 1
        options = []
        for dx, dz in ((0, 1), (-1, 0), (1, 0), (0, -1)):
            candidate = self._safe_point(point.x + dx, point.y, point.z + dz, size, vertical)
            if (candidate is not None and not candidate.is_first
                    and candidate.distance_to(goal) < max_distance):
                options.append(candidate)
        return options

    def _safe_point(self, x, y, z, size, vertical):
        if self._vertical_offset(x, y, z, size) > 0:
            result_y = y
        elif vertical > 0 and self._vertical_offset(x, y + vertical, z, size) > 0:
            result_y = y + vertical
        else:
            return None

        dropped = 0
        while result_y > 0:
            below = self._vertical_offset(x, result_y - 1, z, size)
            if below <= 0:
                break
            result_y -= 1
            dropped += 1
            if dropped >= 4:
                return None

        if result_y <= 0:
            return None
        under = self._block_type(x, result_y - 1, z)
        if under in _LIQUIDS:
            return None
        return self._open(x, result_y, z)

    def _open(self, x, y, z):
        key = (x, y, z)
        point = self.point_map.get(key)
        if point is None:
            point = PathPoint(x, y, z)
            self.point_map[key] = point
        return point

    def _vertical_offset(self, x, y, z, size):
        block_type = self._block_type(x, y, z)
        if _is_solid_type(block_type):
            return 0
        if block_type in _LIQUIDS:
            return -1
        return 1

    @staticmethod
    def _build(start, end):
        chain = [end]
        node = end
        while node.previous is not None:
            node = node.previous
            chain.append(node)
        chain.reverse()
        return PathEntity(chain)


def get_path_to_entity(world, entity, target, max_distance):
    return Pathfinder(world).create_path_to_entity(entity, target, max_distance)


def get_path_to_xyz(world, entity, x, y, z, max_distance):
    return Pathfinder(world).create_path_to_xyz(entity, x, y, z, max_distance)
