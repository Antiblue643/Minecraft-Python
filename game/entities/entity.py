import math
import random

from engine.lighting import brightness_from_level

_SOLID_BY_TYPE = {}
_COLLISION_BOXES_BY_KEY = {}
_LIQUID_BY_TYPE = {}
_SLIPPERINESS_BY_TYPE = {}
_WATER_TYPES = frozenset(("water", "flowing_water"))
_LAVA_TYPES = frozenset(("lava", "flowing_lava"))
_UNCACHED_COLLISION_TYPES = frozenset(("wooden_door", "iron_door"))

TICKS_PER_SECOND = 20.0
MAX_DT = 0.1


class Entity:
    GRAVITY = 0.08
    DRAG_Y = 0.98
    DRAG_XZ = 0.91
    GROUND_FRICTION = 0.6
    GROUND_CONTACT_IN_WATER = False
    GROUND_BOUNCE_FACTOR = None
    ACTIVATE_REDSTONE_ON_GROUND = True
    JUMP_MOTION = 0.42
    STEP_HEIGHT = 0.5

    WATER_GRAVITY = 0.02
    WATER_DRAG = 0.8
    LAVA_DRAG = 0.5
    WATER_CONTACT_SHRINK = 0.4
    LAVA_CONTACT_SHRINK = 0.4
    WATER_GRAVITY_BEFORE_MOVE = False
    USE_AIR_DRAG_IN_WATER = False
    USE_AIR_DRAG_IN_LAVA = False
    WATER_SWIM_ACCEL = 0.04
    WATER_FLOW_ACCEL = 0.004
    LADDER_CLIMB_SPEED = 0.2
    LADDER_FALL_SPEED = -0.15

    world = None
    renderer = None
    sound_player = None
    _next_entity_id = 0

    def __init__(self, pos, size=(0.6, 1.8, 0.6)):
        self.entity_id = Entity._next_entity_id
        Entity._next_entity_id += 1
        self.pos = list(pos)
        self.vel = [0.0, 0.0, 0.0]
        self.size = size
        self.dead = False
        self.portal_time = 0.0
        self.prev_portal_time = 0.0
        self.portal_cooldown = 0.0
        self.in_portal = False
        self.portal_requires_exit = False
        self.on_ground = False
        self.in_water = False
        self.body_in_water = False
        self.in_lava = False
        self.submerged = False
        self.water_surface_y = None
        self.water_flow = (0.0, 0.0, 0.0)
        self._living_fluid_physics = False
        self.on_ladder = False
        self.ladder_contact_normal = (0.0, 0.0, 0.0)
        self.climb_requested = False
        self.y_size = 0.0
        self.render_y_offset = 0.0
        self.horizontal_collision = False
        self.vertical_collision = False
        self._blocked_down = False
        self._jump_airborne = False
        self.jump_requested = False

    def can_be_pushed(self):
        return False

    def apply_entity_collision(self, other):
        if other is None or not hasattr(other, "pos") or not hasattr(other, "vel"):
            return
        dx = float(other.pos[0]) - float(self.pos[0])
        dz = float(other.pos[2]) - float(self.pos[2])
        max_abs = max(abs(dx), abs(dz))
        if max_abs < 0.01:
            return
        length = math.sqrt(max_abs)
        if length <= 1e-9:
            return
        dx /= length
        dz /= length
        inverse = min(1.0, 1.0 / length)
        reduction = max(0.0, min(1.0, 1.0 - float(getattr(self, "entity_collision_reduction", 0.0))))
        dx *= inverse * 0.05 * reduction
        dz *= inverse * 0.05 * reduction
        self.vel[0] -= dx
        self.vel[2] -= dz
        other.vel[0] += dx
        other.vel[2] += dz

    def _apply_entity_collisions(self):
        world = Entity.world
        if world is None:
            return
        own_bounds = self._entity_collision_aabb(self)
        if own_bounds is None:
            return
        player = getattr(world, "player", None)

        extras = []
        if player is not None and player is not self:
            extras.append(player)
        for remote in getattr(world, "_mp_remote_players", ()) or ():
            if remote is not self and not any(item is remote for item in extras):
                extras.append(remote)
        extra_ids = {id(item) for item in extras}

        own_x = 0.5 * (own_bounds[0] + own_bounds[3])
        own_y = own_bounds[1]
        own_z = 0.5 * (own_bounds[2] + own_bounds[5])
        own_h = own_bounds[4] - own_bounds[1]
        sync_spatial = getattr(world, "_sync_entity_spatial_index", None)
        if callable(sync_spatial):
            sync_spatial(self)
        reach_xz = 0.5 * max(own_bounds[3] - own_bounds[0], own_bounds[5] - own_bounds[2]) + 0.2 + 4.0
        reach_below = 12.0
        self_riding = getattr(self, "riding_entity", None)
        client_world = bool(getattr(world, "_multiplayer_client_world", False))

        def _check(other):
            if other is self or getattr(other, "dead", False):
                return
            opos = getattr(other, "pos", None)
            if opos is None:
                return
            if (abs(opos[0] - own_x) > reach_xz or abs(opos[2] - own_z) > reach_xz):
                return
            dyv = opos[1] - own_y
            if dyv >= own_h or dyv <= -reach_below:
                return
            if (other is self_riding
                    or getattr(other, "rider", None) is self
                    or getattr(other, "riding_entity", None) is not None):
                return
            pushed = getattr(other, "can_be_pushed", None)
            if callable(pushed):
                if not pushed():
                    return
            elif other is player:
                collidable = getattr(other, "can_be_collided_with", None)
                if not callable(collidable) or not collidable():
                    return
            else:
                return
            if getattr(other, "vel", None) is None:
                return
            other_bounds = self._entity_collision_aabb(other)
            if other_bounds is None:
                return
            if (own_bounds[0] - 0.2 < other_bounds[3]
                    and own_bounds[3] + 0.2 > other_bounds[0]
                    and own_bounds[2] - 0.2 < other_bounds[5]
                    and own_bounds[5] + 0.2 > other_bounds[2]
                    and own_bounds[1] < other_bounds[4]
                    and own_bounds[4] > other_bounds[1]):
                apply = getattr(other, "apply_entity_collision", None)
                if callable(apply):
                    apply(self)
                else:
                    self.apply_entity_collision(other)
                if (self is player
                        and client_world
                        and bool(getattr(other, "_mp_remote_entity", False))):
                    request_push = getattr(world, "_network_entity_push_callback", None)
                    if callable(request_push):
                        request_push(other)

        iter_nearby = getattr(world, "iter_entities_in_bounds", None)
        if callable(iter_nearby):
            candidates = iter_nearby(
                own_x - reach_xz, own_y - reach_below, own_z - reach_xz,
                own_x + reach_xz, own_y + own_h, own_z + reach_xz,
            )
        else:
            candidates = getattr(world, "entities", ())
        for other in candidates:
            if id(other) in extra_ids:
                continue
            _check(other)
        for other in extras:
            _check(other)

    def get_entity_brightness(self):
        world = self.world
        if world is None:
            return 1.0
        try:
            x = int(math.floor(self.pos[0] + 0.5))
            y = int(math.floor(self.pos[1] + self.size[1] * 0.66 + 0.5))
            z = int(math.floor(self.pos[2] + 0.5))
            light = world.get_light(x, y, z)
            if light <= 0:
                best = 0
                for nx, ny, nz in (
                    (x, y + 1, z),
                    (x + 1, y, z), (x - 1, y, z),
                    (x, y, z + 1), (x, y, z - 1),
                ):
                    n = world.get_light(nx, ny, nz, use_neighbor_max=False)
                    if n > best:
                        best = n
                if best > 0:
                    light = best
            return brightness_from_level(light, hell=bool(getattr(world, "is_hell_world", False)))
        except Exception:
            return 1.0

    def _half_width(self):
        return self.size[0] * 0.5

    def _half_depth(self):
        return self.size[2] * 0.5

    def get_bounding_box(self, position=None):
        px, py, pz = self.pos if position is None else position
        half_width = float(self.size[0]) * 0.5
        half_depth = float(self.size[2]) * 0.5
        height = float(self.size[1])
        return (
            float(px) - half_width, float(py), float(pz) - half_depth,
            float(px) + half_width, float(py) + height, float(pz) + half_depth,
        )

    def _aabb(self, px, py, pz):
        hw = self._half_width()
        hd = self._half_depth()
        h = self.size[1]
        return px - hw, px + hw, py, py + h, pz - hd, pz + hd

    def _block_positions_for_aabb(self, px, py, pz):
        hw = self._half_width()
        hd = self._half_depth()
        h = self.size[1]
        x0 = math.floor(px - hw + 0.5)
        x1 = math.floor(px + hw + 0.5)
        y0 = math.floor(py + 0.5) - 1
        y1 = math.floor(py + h + 0.5)
        z0 = math.floor(pz - hd + 0.5)
        z1 = math.floor(pz + hd + 0.5)
        return [
            (bx, by, bz)
            for bx in range(x0, x1 + 1)
            for by in range(y0, y1 + 1)
            for bz in range(z0, z1 + 1)
        ]

    def _solid_at(self, bx, by, bz):
        world = Entity.world
        if world is None:
            return False
        block_type = world.get_block_type(bx, by, bz)
        if block_type is None:
            return False
        solid = _SOLID_BY_TYPE.get(block_type)
        if solid is not None:
            return solid
        try:
            from game.block import Block
            Block._load_defs()
            definition = Block._block_defs.get(block_type) or {}
        except Exception:
            definition = {}
        collision = definition.get("collision")
        if collision is False:
            solid = False
        elif collision is True:
            solid = True
        elif isinstance(collision, (list, tuple)):
            solid = True
        else:
            solid = True
        _SOLID_BY_TYPE[block_type] = solid
        return solid

    def _is_opaque_at(self, bx, by, bz):
        if Entity.world is None:
            return False
        block_type = Entity.world.get_block_type(bx, by, bz)
        if block_type is None:
            return False
        try:
            from game.block import Block
            Block._load_defs()
            definition = Block._block_defs.get(block_type) or {}
        except Exception:
            definition = {}
        if definition.get("opaque") is False:
            return False
        model = definition.get("model", "cube")
        if model not in ("cube", None):
            return False
        if definition.get("transparent", False):
            return False
        n_cull = definition.get("nCull", "all")
        if n_cull not in ("all", None):
            return False
        return self._solid_at(bx, by, bz)

    def _push_out_of_blocks(self):
        import random

        cx = self.pos[0]
        cy = self.pos[1] + self.size[1] * 0.5
        cz = self.pos[2]
        bx = math.floor(cx + 0.5)
        by = math.floor(cy + 0.5)
        bz = math.floor(cz + 0.5)
        if not self._is_opaque_at(bx, by, bz):
            return False

        fx = cx - (bx - 0.5)
        fy = cy - (by - 0.5)
        fz = cz - (bz - 0.5)
        options = (
            (not self._is_opaque_at(bx - 1, by, bz), fx, 0),
            (not self._is_opaque_at(bx + 1, by, bz), 1.0 - fx, 1),
            (not self._is_opaque_at(bx, by - 1, bz), fy, 2),
            (not self._is_opaque_at(bx, by + 1, bz), 1.0 - fy, 3),
            (not self._is_opaque_at(bx, by, bz - 1), fz, 4),
            (not self._is_opaque_at(bx, by, bz + 1), 1.0 - fz, 5),
        )
        best, best_dist = -1, 9999.0
        for is_open, dist, index in options:
            if is_open and dist < best_dist:
                best, best_dist = index, dist
        if best < 0:
            return False

        speed = random.random() * 0.2 + 0.1
        eps = 0.002
        hw, hd, h = self._half_width(), self._half_depth(), self.size[1]
        if best == 0:
            self.vel[0] = -speed
            self.pos[0] = bx - 0.5 - hw - eps
        elif best == 1:
            self.vel[0] = speed
            self.pos[0] = bx + 0.5 + hw + eps
        elif best == 2:
            self.vel[1] = -speed
            self.pos[1] = by - 0.5 - h - eps
        elif best == 3:
            self.vel[1] = speed
            self.pos[1] = by + 0.5 + eps
        elif best == 4:
            self.vel[2] = -speed
            self.pos[2] = bz - 0.5 - hd - eps
        else:
            self.vel[2] = speed
            self.pos[2] = bz + 0.5 + hd + eps
        return True

    def _block_collision_aabbs(self, bx, by, bz):
        world = Entity.world
        if world is None:
            return None
        block_type = world.get_block_type(bx, by, bz)
        if block_type is None:
            return None
        get_meta = getattr(world, "get_metadata", None)
        if get_meta is not None and block_type not in _UNCACHED_COLLISION_TYPES:
            key = (block_type, int(get_meta(bx, by, bz) or 0) & 15)
            locals_ = _COLLISION_BOXES_BY_KEY.get(key)
            if locals_ is None:
                block = world.get_block(bx, by, bz)
                if block is None:
                    return None
                raw = (block.get_collision_boxes() if hasattr(block, "get_collision_boxes")
                       else [block.get_collision_bounds()])
                locals_ = tuple(tuple(box) for box in (raw or ()) if box is not None)
                _COLLISION_BOXES_BY_KEY[key] = locals_
            if not locals_:
                return None
            return [(
                bx + local[0], by + local[1], bz + local[2],
                bx + local[3], by + local[4], bz + local[5],
            ) for local in locals_]
        block = world.get_block(bx, by, bz)
        if block is None:
            return None
        locals_ = block.get_collision_boxes() if hasattr(block, "get_collision_boxes") else [block.get_collision_bounds()]
        if not locals_:
            return None
        return [(
            bx + local[0], by + local[1], bz + local[2],
            bx + local[3], by + local[4], bz + local[5],
        ) for local in locals_ if local is not None]

    def _block_collision_aabb(self, bx, by, bz):
        boxes = self._block_collision_aabbs(bx, by, bz)
        return boxes[0] if boxes else None

    def _liquid_at(self, bx, by, bz):
        world = Entity.world
        if world is None:
            return False
        block_type = world.get_block_type(bx, by, bz)
        if block_type is None:
            return False
        is_liquid = _LIQUID_BY_TYPE.get(block_type)
        if is_liquid is not None:
            return is_liquid
        if block_type in _WATER_TYPES or block_type in _LAVA_TYPES:
            is_liquid = True
        else:
            try:
                from game.block import Block
                Block._load_defs()
                definition = Block._block_defs.get(block_type) or {}
                is_liquid = definition.get("model") == "liquid"
            except Exception:
                is_liquid = False
        _LIQUID_BY_TYPE[block_type] = is_liquid
        return is_liquid

    def _overlaps_aabb_at(self, px, py, pz, aabb):
        hw = self._half_width()
        hd = self._half_depth()
        h = self.size[1]
        return (
            px - hw < aabb[3] and px + hw > aabb[0]
            and py < aabb[4] and py + h > aabb[1]
            and pz - hd < aabb[5] and pz + hd > aabb[2]
        )

    def _overlaps_block(self, px, py, pz, bx, by, bz):
        if Entity.world is not None and Entity.world.get_block_type(bx, by, bz) is None:
            return False
        aabbs = self._block_collision_aabbs(bx, by, bz)
        if not aabbs:
            block = Entity.world.get_block(bx, by, bz) if Entity.world else None
            if block is None:
                return False
            local = block.get_selection_bounds()
            aabbs = [(
                bx + local[0], by + local[1], bz + local[2],
                bx + local[3], by + local[4], bz + local[5],
            )]
        return any(self._overlaps_aabb_at(px, py, pz, aabb) for aabb in aabbs)

    def _collides(self, px, py, pz):
        for bx, by, bz in self._block_positions_for_aabb(px, py, pz):
            if self._solid_at(bx, by, bz) and self._overlaps_block(px, py, pz, bx, by, bz):
                return True
        return False

    @staticmethod
    def _entity_collision_aabb(entity):
        get_box = getattr(entity, "get_bounding_box", None)
        if callable(get_box):
            try:
                box = get_box()
                if box is not None and len(box) == 6:
                    return tuple(float(value) for value in box)
            except (TypeError, ValueError, AttributeError):
                pass
        pos = getattr(entity, "pos", None)
        size = getattr(entity, "size", None)
        if pos is None or size is None or len(pos) < 3 or len(size) < 3:
            return None
        try:
            px, py, pz = (float(value) for value in pos[:3])
            width, height, depth = (float(value) for value in size[:3])
        except (TypeError, ValueError):
            return None
        return (px - width * 0.5, py, pz - depth * 0.5,
                px + width * 0.5, py + height, pz + depth * 0.5)

    def _entity_collision_boxes_for_move(self, axis, delta):
        world = Entity.world
        if world is None:
            return ()
        self_is_minecart = callable(getattr(self, "get_collision_box", None))
        if self_is_minecart:
            return ()
        entities = getattr(world, "entities", None) or ()
        cache = getattr(world, "_vehicle_cache", None)
        if cache is None or cache[0] is not entities or cache[1] != len(entities):
            cache = (entities, len(entities), [
                entity for entity in entities
                if callable(getattr(entity, "get_collision_box", None))
            ])
            try:
                world._vehicle_cache = cache
            except Exception:
                pass
        others = cache[2]
        if not others:
            return ()

        own = self._entity_collision_aabb(self)
        if own is None:
            return ()
        end = list(own)
        end[axis] += float(delta)
        end[axis + 3] += float(delta)
        query = (
            min(own[0], end[0]) - 0.25,
            min(own[1], end[1]) - 0.25,
            min(own[2], end[2]) - 0.25,
            max(own[3], end[3]) + 0.25,
            max(own[4], end[4]) + 0.25,
            max(own[5], end[5]) + 0.25,
        )
        boxes = []
        seen = set()
        for other in others:
            if other is self or getattr(other, "dead", False):
                continue
            if (other is getattr(self, "riding_entity", None)
                    or getattr(other, "rider", None) is self
                    or other is getattr(self, "rider", None)
                    or getattr(other, "riding_entity", None) is self):
                continue
            other_is_minecart = callable(getattr(other, "get_collision_box", None))
            if not self_is_minecart and not other_is_minecart:
                continue
            box = self._entity_collision_aabb(other)
            if box is None:
                continue
            if not (query[0] < box[3] and query[3] > box[0]
                    and query[1] < box[4] and query[4] > box[1]
                    and query[2] < box[5] and query[5] > box[2]):
                continue
            if box not in seen:
                boxes.append(box)
                seen.add(box)
            get_collision_box = getattr(self, "get_collision_box", None)
            if callable(get_collision_box):
                try:
                    collision_box = get_collision_box(other)
                except TypeError:
                    collision_box = None
                if collision_box is not None and len(collision_box) == 6:
                    collision_box = tuple(float(value) for value in collision_box)
                    if collision_box not in seen:
                        boxes.append(collision_box)
                        seen.add(collision_box)
        return tuple(boxes)

    def _clip_axis_against_entity_box(self, axis, delta, obstacle):
        if delta == 0.0:
            return 0.0
        own = self._entity_collision_aabb(self)
        if own is None:
            return delta
        if axis == 0:
            orthogonal_overlap = (
                obstacle[1] < own[4] and obstacle[4] > own[1]
                and obstacle[2] < own[5] and obstacle[5] > own[2]
            )
            own_min, own_max, obstacle_min, obstacle_max = own[0], own[3], obstacle[0], obstacle[3]
        elif axis == 1:
            orthogonal_overlap = (
                obstacle[0] < own[3] and obstacle[3] > own[0]
                and obstacle[2] < own[5] and obstacle[5] > own[2]
            )
            own_min, own_max, obstacle_min, obstacle_max = own[1], own[4], obstacle[1], obstacle[4]
        else:
            orthogonal_overlap = (
                obstacle[0] < own[3] and obstacle[3] > own[0]
                and obstacle[1] < own[4] and obstacle[4] > own[1]
            )
            own_min, own_max, obstacle_min, obstacle_max = own[2], own[5], obstacle[2], obstacle[5]
        if not orthogonal_overlap:
            return delta
        if delta > 0.0 and own_max <= obstacle_min:
            return min(delta, obstacle_min - own_max)
        if delta < 0.0 and own_min >= obstacle_max:
            return max(delta, obstacle_max - own_min)
        return delta

    def _block_type_at(self, bx, by, bz):
        return Entity.world.get_block_type(bx, by, bz) if Entity.world is not None else None

    def _is_water_block(self, bx, by, bz):
        return self._block_type_at(bx, by, bz) in ("water", "flowing_water")

    def _is_lava_block(self, bx, by, bz):
        return self._block_type_at(bx, by, bz) in ("lava", "flowing_lava")

    def _liquid_level(self, bx, by, bz):
        if Entity.world is None or not hasattr(Entity.world, "get_metadata"):
            return 0
        try:
            meta = int(Entity.world.get_metadata(bx, by, bz)) & 15
        except Exception:
            meta = 0
        level = meta & 7
        return 0 if (meta & 8) else level

    def _liquid_surface_y(self, bx, by, bz):
        level = self._liquid_level(bx, by, bz)
        return by + 0.5 - ((level + 1) / 9.0)

    def _water_flow_at(self, bx, by, bz):
        from game.world.fluid_physics import fluid_flow_vector
        return fluid_flow_vector(Entity.world, bx, by, bz, "water")

    def _apply_water_current(self, ticks=1.0):
        amount = self.WATER_FLOW_ACCEL * max(0.0, float(ticks))
        if self.in_water and amount:
            self.vel[0] += self.water_flow[0] * amount
            self.vel[1] += self.water_flow[1] * amount
            self.vel[2] += self.water_flow[2] * amount

    def touches_water_java(self, px=None, py=None, pz=None):
        if Entity.world is None:
            return False
        if px is None:
            px, py, pz = self.pos
        hw, hd, height = self._half_width(), self._half_depth(), self.size[1]
        min_x, max_x = px + 0.5 - hw, px + 0.5 + hw
        min_z, max_z = pz + 0.5 - hd, pz + 0.5 + hd
        min_y, max_y = py + 0.5 + 0.4, py + 0.5 + height - 0.4
        for bx in range(math.floor(min_x), math.floor(max_x + 1.0)):
            for by in range(math.floor(min_y), math.floor(max_y + 1.0)):
                for bz in range(math.floor(min_z), math.floor(max_z + 1.0)):
                    if self._is_water_block(bx, by, bz):
                        return True
        return False

    def _check_water(self):
        if Entity.world is None:
            self.in_water = self.body_in_water = self.in_lava = self.submerged = False
            self.water_surface_y = None
            self.water_flow = (0.0, 0.0, 0.0)
            return

        px, py, pz = self.pos
        hw, hd, height = self._half_width(), self._half_depth(), self.size[1]
        self.in_water = False
        self.body_in_water = False
        self.in_lava = False
        self.submerged = False
        self.water_surface_y = None
        flow_x = flow_y = flow_z = 0.0
        max_shrink = max(0.0, height * 0.5 - 1e-3)
        water_shrink = min(
            max(0.0, float(self.WATER_CONTACT_SHRINK)), max_shrink
        )
        lava_shrink = max(0.0, float(self.LAVA_CONTACT_SHRINK))
        water_min_y = py + water_shrink
        water_max_y = py + height - water_shrink
        lava_min_y = py + lava_shrink
        lava_max_y = py + height - lava_shrink
        eye_y = py + float(getattr(
            self, "WATER_SUBMERSION_HEIGHT",
            getattr(self, "EYE_HEIGHT", height * 0.9),
        ))

        get_block_type = Entity.world.get_block_type
        for bx, by, bz in self._block_positions_for_aabb(px, py, pz):
            block_type = get_block_type(bx, by, bz)
            if block_type is None:
                continue
            is_water = block_type in _WATER_TYPES
            is_lava = block_type in _LAVA_TYPES
            if not (is_water or is_lava):
                continue
            horizontal_overlap = (
                px - hw < bx + 0.5 and px + hw > bx - 0.5
                and pz - hd < bz + 0.5 and pz + hd > bz - 0.5
            )
            if not horizontal_overlap:
                continue
            surface_y = self._liquid_surface_y(bx, by, bz)
            lower_y = by - 0.5
            upper_y = by + 0.5
            if is_water and py < surface_y and py + height > lower_y:
                self.body_in_water = True
                if self.water_surface_y is None or surface_y > self.water_surface_y:
                    self.water_surface_y = surface_y

            water_cell_overlap = (
                water_min_y < upper_y and water_max_y > lower_y
            )
            lava_cell_overlap = (
                lava_min_y < upper_y and lava_max_y > lower_y
            )
            if is_water and water_cell_overlap:
                self.in_water = True
                fx, fy, fz = self._water_flow_at(bx, by, bz)
                flow_x += fx
                flow_y += fy
                flow_z += fz
            elif is_lava and lava_cell_overlap:
                self.in_lava = True

        ebx = math.floor(px + 0.5)
        eby = math.floor(eye_y + 0.5)
        ebz = math.floor(pz + 0.5)
        if self._is_water_block(ebx, eby, ebz):
            level = self._liquid_level(ebx, eby, ebz)
            if eye_y < (eby + 0.5) - (level / 9.0):
                self.submerged = True

        length = math.sqrt(flow_x * flow_x + flow_y * flow_y + flow_z * flow_z)
        if length > 1e-8:
            self.water_flow = (flow_x / length, flow_y / length, flow_z / length)
        else:
            self.water_flow = (0.0, 0.0, 0.0)

    def _overlaps_any_liquid(self, px, py, pz):
        for bx, by, bz in self._block_positions_for_aabb(px, py, pz):
            if not (self._is_water_block(bx, by, bz) or self._is_lava_block(bx, by, bz)):
                continue
            liquid_box = (bx - 0.5, by - 0.5, bz - 0.5,
                          bx + 0.5, by + 0.5, bz + 0.5)
            if self._overlaps_aabb_at(px, py, pz, liquid_box):
                return True
        return False

    def _check_ladder(self):
        self.on_ladder = False
        self.ladder_contact_normal = (0.0, 0.0, 0.0)
        if Entity.world is None:
            return False

        px, py, pz = self.pos
        for bx, by, bz in self._block_positions_for_aabb(px, py, pz):
            if self._block_type_at(bx, by, bz) != "ladder":
                continue
            cell_box = (bx - 0.5, by - 0.5, bz - 0.5,
                        bx + 0.5, by + 0.5, bz + 0.5)
            if self._overlaps_aabb_at(px, py, pz, cell_box):
                self.on_ladder = True
                return True
        return False

    def _move_axis(self, axis, delta):
        if delta == 0.0:
            return 0.0

        entity_boxes = self._entity_collision_boxes_for_move(axis, delta)
        new_pos = list(self.pos)
        new_pos[axis] += delta

        if not self._collides(*new_pos):
            for obstacle in entity_boxes:
                delta = self._clip_axis_against_entity_box(axis, delta, obstacle)
                if delta == 0.0:
                    break
            self.pos[axis] += delta
            return delta

        hw = self._half_width()
        hd = self._half_depth()
        h = self.size[1]
        px, py, pz = self.pos
        already_stuck = self._collides(px, py, pz)

        for bx, by, bz in self._block_positions_for_aabb(*new_pos):
            if not self._solid_at(bx, by, bz):
                continue
            if not self._overlaps_block(*new_pos, bx, by, bz):
                continue

            aabbs = self._block_collision_aabbs(bx, by, bz)
            overlapping = [
                box for box in (aabbs or ())
                if self._overlaps_aabb_at(*new_pos, box)
            ]
            if not overlapping:
                continue
            if axis == 0:
                aabb = min(overlapping, key=lambda box: box[0]) if delta > 0 else max(overlapping, key=lambda box: box[3])
            elif axis == 1:
                aabb = min(overlapping, key=lambda box: box[1]) if delta > 0 else max(overlapping, key=lambda box: box[4])
            else:
                aabb = min(overlapping, key=lambda box: box[2]) if delta > 0 else max(overlapping, key=lambda box: box[5])

            if axis == 0:  
                if delta > 0:
                    allowed = aabb[0] - hw - px
                    if not already_stuck or allowed >= 0.0:
                        delta = min(delta, max(0.0, allowed))
                else:
                    allowed = aabb[3] + hw - px
                    if not already_stuck or allowed <= 0.0:
                        delta = max(delta, min(0.0, allowed))
            elif axis == 1:  
                if delta > 0:
                    allowed = aabb[1] - h - py
                    if not already_stuck or allowed >= 0.0:
                        delta = min(delta, max(0.0, allowed))
                else:
                    allowed = aabb[4] - py
                    is_farmland_height_overlap = (
                        already_stuck and delta < 0.0
                        and 0.0 < allowed <= (1.0 / 16.0) + 1.0e-7
                        and self._block_type_at(bx, by, bz) == "dirt"
                    )
                    if is_farmland_height_overlap:
                        delta = max(delta, allowed)
                    elif not already_stuck or allowed <= 0.0:
                        delta = max(delta, min(0.0, allowed))
            else:  
                if delta > 0:
                    allowed = aabb[2] - hd - pz
                    if not already_stuck or allowed >= 0.0:
                        delta = min(delta, max(0.0, allowed))
                else:
                    allowed = aabb[5] + hd - pz
                    if not already_stuck or allowed <= 0.0:
                        delta = max(delta, min(0.0, allowed))

        for obstacle in entity_boxes:
            delta = self._clip_axis_against_entity_box(axis, delta, obstacle)
            if delta == 0.0:
                break

        self.pos[axis] += delta
        return delta

    def _has_support_below_offset(self, ox, oz):
        px = self.pos[0] + ox
        py = self.pos[1] - 1.0
        pz = self.pos[2] + oz
        return self._collides(px, py, pz)

    def move(self, dx, dy, dz):
        self.horizontal_collision = False
        self.vertical_collision = False

        if self.on_ground and getattr(self, "is_sneaking", False):
            step = 0.05
            orig_dx, orig_dz = dx, dz
            while dx != 0.0 and not self._has_support_below_offset(dx, 0.0):
                if abs(dx) < step:
                    dx = 0.0
                elif dx > 0.0:
                    dx -= step
                else:
                    dx += step
            while dz != 0.0 and not self._has_support_below_offset(0.0, dz):
                if abs(dz) < step:
                    dz = 0.0
                elif dz > 0.0:
                    dz -= step
                else:
                    dz += step
            if (dx != 0.0 or dz != 0.0) and not self._has_support_below_offset(dx, dz):
                if orig_dx != 0.0 and orig_dz != 0.0:
                    if abs(dx) >= abs(dz):
                        dz = 0.0
                    else:
                        dx = 0.0

        ay = self._move_axis(1, dy)
        if abs(ay - dy) > 1e-8:
            self.vertical_collision = True
            self.vel[1] = 0.0

        blocked_down = abs(ay - dy) > 1e-8 and dy < 0
        self._blocked_down = blocked_down
        can_step = (
            (self.on_ground or blocked_down)
            and dy <= 1e-8
            and self.vel[1] <= 1e-6
            and self.y_size < 0.05
            and not self._jump_airborne
            and not self.on_ladder
        )

        origin = list(self.pos)
        ax = self._move_axis(0, dx)
        az = self._move_axis(2, dz)
        flat_blocked = abs(ax - dx) > 1e-8 or abs(az - dz) > 1e-8

        if flat_blocked and can_step and (dx != 0.0 or dz != 0.0):
            flat_pos = list(self.pos)
            flat_dist = (flat_pos[0] - origin[0]) ** 2 + (flat_pos[2] - origin[2]) ** 2

            self.pos = list(origin)
            self._move_axis(1, self.STEP_HEIGHT)
            self._move_axis(0, dx)
            self._move_axis(2, dz)
            step_dist = (self.pos[0] - origin[0]) ** 2 + (self.pos[2] - origin[2]) ** 2

            if step_dist > flat_dist + 1e-8:
                settle_requested = -self.STEP_HEIGHT - 0.1
                settle_actual = self._move_axis(1, settle_requested)
                if settle_actual != settle_requested and settle_requested < 0.0:
                    self._blocked_down = True
                raised_by = max(0.0, self.pos[1] - origin[1])
                self.y_size = min(self.STEP_HEIGHT, self.y_size + raised_by)
            else:
                self.pos = flat_pos

            ax = self.pos[0] - origin[0]
            az = self.pos[2] - origin[2]
            flat_blocked = abs(ax - dx) > 1e-8 or abs(az - dz) > 1e-8

        if flat_blocked:
            self.horizontal_collision = True

    def _ground_slipperiness(self):
        world = Entity.world
        if world is None or not hasattr(world, "get_block_type"):
            return self.GROUND_FRICTION
        block_y = math.floor(self.pos[1] + 0.5) - 1
        bx = math.floor(self.pos[0] + 0.5)
        bz = math.floor(self.pos[2] + 0.5)
        block_type = world.get_block_type(bx, block_y, bz)
        if not block_type:
            return self.GROUND_FRICTION
        cached = _SLIPPERINESS_BY_TYPE.get(block_type)
        if cached is not None:
            return self.GROUND_FRICTION if cached < 0.0 else cached
        try:
            from game.block import Block
            Block._load_defs()
            definition = Block._block_defs.get(block_type) or {}
        except Exception:
            return self.GROUND_FRICTION
        slip = definition.get("slipperiness")
        _SLIPPERINESS_BY_TYPE[block_type] = -1.0 if slip is None else float(slip)
        if slip is None:
            return self.GROUND_FRICTION
        return float(slip)

    def _activate_redstone_ore_underfoot(self):
        world = Entity.world
        if world is None:
            return
        block_y = math.floor(self.pos[1] + 0.5) - 1
        half_width = self._half_width()
        half_depth = self._half_depth()
        min_x = math.floor(self.pos[0] - half_width + 0.5)
        max_x = math.floor(self.pos[0] + half_width + 0.5)
        min_z = math.floor(self.pos[2] - half_depth + 0.5)
        max_z = math.floor(self.pos[2] + half_depth + 0.5)
        from game.block.redstone_ore import activate_redstone_ore
        for bx in range(min_x, max_x + 1):
            for bz in range(min_z, max_z + 1):
                if world.get_block_type(bx, block_y, bz) in (
                    "redstone_ore", "redstone_ore_lit",
                ):
                    activate_redstone_ore(world, bx, block_y, bz)

    def _apply_block_contact_effects(self, ticks):
        if Entity.world is None:
            return
        px, py, pz = self.pos
        in_portal = False
        slowed_by_soul_sand = False
        for bx, by, bz in self._block_positions_for_aabb(px, py, pz):
            block_type = self._block_type_at(bx, by, bz)
            if block_type == "portal":
                cell_box = (bx - 0.5, by - 0.5, bz - 0.5,
                            bx + 0.5, by + 0.5, bz + 0.5)
                if self._overlaps_aabb_at(px, py, pz, cell_box):
                    in_portal = True
            if block_type != "soul_sand" or slowed_by_soul_sand:
                continue
            cell_box = (bx - 0.5, by - 0.5, bz - 0.5,
                        bx + 0.5, by + 0.5, bz + 0.5)
            if self._overlaps_aabb_at(px, py, pz, cell_box):
                factor = 0.4 ** max(0.0, float(ticks))
                self.vel[0] *= factor
                self.vel[2] *= factor
                slowed_by_soul_sand = True
        was_in_portal = bool(self.in_portal)
        previous_portal_time = float(self.portal_time)
        self.in_portal = bool(in_portal)
        if not in_portal:
            self.portal_requires_exit = False
        self.prev_portal_time = self.portal_time

        if (in_portal and not was_in_portal and previous_portal_time <= 0.0
                and self.__class__.__name__ == "Player"):
            world = Entity.world
            sound_player = getattr(world, "sound_player", None) if world is not None else None
            if not callable(sound_player):
                sound_player = getattr(self, "sound_player", None)
            if callable(sound_player):
                sound_player(
                    "portal.trigger", volume=1.0,
                    pitch=0.8 + random.random() * 0.4,
                )

        if in_portal:
            self.portal_time = min(1.0, self.portal_time + 0.0125 * max(0.0, float(ticks)))
        else:
            self.portal_time = max(0.0, self.portal_time - 0.05 * max(0.0, float(ticks)))

    def update(self, dt):
        if Entity.world is None:
            return

        dt = min(dt, MAX_DT)
        ticks = dt * TICKS_PER_SECOND
        self.portal_cooldown = max(0.0, self.portal_cooldown - ticks)

        heading_ax, heading_az = getattr(self, "_heading_accel", (0.0, 0.0))
        resting = (
            self.on_ground
            and not self.in_water
            and not self.in_lava
            and not self.on_ladder
            and not self.in_portal
            and abs(self.vel[0]) < 1e-4
            and abs(self.vel[1]) < 1e-4
            and abs(self.vel[2]) < 1e-4
            and abs(heading_ax) < 1e-8
            and abs(heading_az) < 1e-8
            and not self.jump_requested
            and not self.climb_requested
        )
        if resting:
            self.vel[0] = 0.0
            self.vel[1] = 0.0
            self.vel[2] = 0.0
            self.horizontal_collision = False
            self.vertical_collision = False
            self._blocked_down = True
            return

        previously_in_water = bool(self.in_water)
        self._check_water()
        self._check_ladder()
        was_in_water = self.in_water
        was_in_lava = self.in_lava

        if self.in_water:
            self._apply_water_current(ticks)
            if self.WATER_GRAVITY_BEFORE_MOVE:
                self.vel[1] -= self.GRAVITY * ticks
                self._apply_water_current(ticks)
        elif not self.in_lava and not self._living_fluid_physics:
            self.vel[1] -= self.GRAVITY * ticks

        if self.on_ladder:
            self.vel[1] = max(self.LADDER_FALL_SPEED, self.vel[1])
            if self.horizontal_collision:
                self.vel[1] = self.LADDER_CLIMB_SPEED

        heading_ax, heading_az = getattr(self, "_heading_accel", (0.0, 0.0))
        movement_lead = 0.5 * (1.0 - ticks)
        dx = (self.vel[0] + heading_ax * movement_lead) * ticks
        dy = self.vel[1] * ticks
        dz = (self.vel[2] + heading_az * movement_lead) * ticks
        start_pos = tuple(self.pos)
        self.move(dx, dy, dz)
        self._apply_block_contact_effects(ticks)

        if self.horizontal_collision:
            if abs(dx) > 1e-8 and self._collides(
                self.pos[0] + math.copysign(0.02, dx), self.pos[1], self.pos[2]
            ):
                self.vel[0] = 0.0
            if abs(dz) > 1e-8 and self._collides(
                self.pos[0], self.pos[1], self.pos[2] + math.copysign(0.02, dz)
            ):
                self.vel[2] = 0.0

        self._check_water()
        self._apply_water_current(ticks)

        out_ticks = getattr(self, "_ticks_out_of_water", 1000.0)
        if self.in_water:
            self._ticks_out_of_water = 0.0
        else:
            self._ticks_out_of_water = out_ticks + ticks

        if (not previously_in_water and self.in_water
                and out_ticks >= 4.0
                and not hasattr(self, "_splash_cooldown")):
            entry_speed = math.sqrt(
                self.vel[0] * self.vel[0] * 0.2
                + self.vel[1] * self.vel[1]
                + self.vel[2] * self.vel[2] * 0.2
            )
            volume = min(0.9, max(0.08, entry_speed * 1.5))
            callback = None
            world = Entity.world
            if world is not None:
                callback = getattr(world, "sound_player", None)
            if not callable(callback):
                callback = getattr(Entity, "sound_player", None)
            if not callable(callback):
                callback = getattr(self, "sound_player", None)
            if callable(callback):
                callback(
                    "liquid.splash",
                    volume=volume,
                    pitch=1.0,
                    position=tuple(self.pos),
                )
            if world is not None and hasattr(world, "spawn_particle"):
                from game.entities.particles import spawn_water_entry_particles
                spawn_water_entry_particles(world, self.pos, self.vel, self.size[0])

        self.on_ground = (
            (not self.in_water or self.GROUND_CONTACT_IN_WATER)
            and self._blocked_down
        )
        if self.on_ground:
            self._jump_airborne = False
            if self.ACTIVATE_REDSTONE_ON_GROUND:
                self._activate_redstone_ore_underfoot()

        liquid_drag = (
            (was_in_water and not self.USE_AIR_DRAG_IN_WATER)
            or (was_in_lava and not self.USE_AIR_DRAG_IN_LAVA)
        )
        if liquid_drag:
            drag_factor = self.LAVA_DRAG if was_in_lava and not was_in_water else self.WATER_DRAG
            drag = drag_factor ** ticks
            self.vel[0] *= drag
            self.vel[1] *= drag
            self.vel[2] *= drag
            self.vel[1] -= self.WATER_GRAVITY * ticks
        else:
            self.vel[1] *= self.DRAG_Y ** ticks
            if self._living_fluid_physics:
                self.vel[1] -= self.GRAVITY * ticks
            if self.on_ground:
                friction = self._ground_slipperiness() * self.DRAG_XZ
            else:
                friction = self.DRAG_XZ
            drag = friction ** ticks
            self.vel[0] *= drag
            self.vel[2] *= drag

        if self.on_ground and self.GROUND_BOUNCE_FACTOR is not None:
            self.vel[1] *= float(self.GROUND_BOUNCE_FACTOR)

        if (was_in_water or was_in_lava) and self.horizontal_collision:
            old_y = start_pos[1]
            offset_y = self.vel[1] + 0.6 - (self.pos[1] - old_y)
            escape_pos = (
                self.pos[0] + self.vel[0] * ticks,
                self.pos[1] + offset_y,
                self.pos[2] + self.vel[2] * ticks,
            )
            free_air = (
                not self._collides(*escape_pos)
                and not self._overlaps_any_liquid(*escape_pos)
            )
            if free_air:
                self.vel[1] = 0.3

        if self.on_ladder:
            if self.horizontal_collision:
                self.vel[1] = self.LADDER_CLIMB_SPEED
            else:
                self.vel[1] = max(self.LADDER_FALL_SPEED, self.vel[1])

        self.render_y_offset = self.y_size
        self.y_size *= 0.4 ** ticks
        self.climb_requested = False
        self.jump_requested = False
