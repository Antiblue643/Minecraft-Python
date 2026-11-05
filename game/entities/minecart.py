from __future__ import annotations

import math
import random

from .entity import Entity, MAX_DT, TICKS_PER_SECOND


_TRACK_PATHS = (
    ((0, 0, -1), (0, 0, 1)),
    ((-1, 0, 0), (1, 0, 0)),
    ((-1, -1, 0), (1, 0, 0)),
    ((-1, 0, 0), (1, -1, 0)),
    ((0, 0, -1), (0, -1, 1)),
    ((0, -1, -1), (0, 0, 1)),
    ((0, 0, 1), (1, 0, 0)),
    ((0, 0, 1), (-1, 0, 0)),
    ((0, 0, -1), (-1, 0, 0)),
    ((0, 0, -1), (1, 0, 0)),
)
_MAX_TRACK_SPEED = 0.4
_MAX_TRACK_SPEED_SQ = _MAX_TRACK_SPEED * _MAX_TRACK_SPEED
_CART_TEXTURE = None
_CART_MODEL = None
_CART_CARGO_TEXTURE = None


class MinecartEntity(Entity):
    STANDARD = 0
    CHEST = 1
    FURNACE = 2
    INVENTORY_SIZE = 27
    SIZE = INVENTORY_SIZE
    stack_limit = 64

    @classmethod
    def type_for_item(cls, item_id):
        aliases = {
            "minecart": cls.STANDARD,
            "chest_minecart": cls.CHEST,
            "minecart_chest": cls.CHEST,
            "furnace_minecart": cls.FURNACE,
            "minecart_furnace": cls.FURNACE,
            "328": cls.STANDARD,
            "342": cls.CHEST,
            "343": cls.FURNACE,
        }
        if isinstance(item_id, str):
            return aliases.get(item_id.strip().lower())
        try:
            numeric_id = int(item_id)
        except (TypeError, ValueError, OverflowError):
            return None
        return {328: cls.STANDARD, 342: cls.CHEST, 343: cls.FURNACE}.get(numeric_id)

    @staticmethod
    def _limit_horizontal_speed(entity):
        velocity = getattr(entity, "vel", None)
        if velocity is None or len(velocity) < 3:
            return
        vx, vz = float(velocity[0]), float(velocity[2])
        if not math.isfinite(vx) or not math.isfinite(vz):
            velocity[0] = velocity[2] = 0.0
            return
        speed_sq = vx * vx + vz * vz
        if speed_sq > _MAX_TRACK_SPEED_SQ:
            scale = _MAX_TRACK_SPEED / math.sqrt(speed_sq)
            velocity[0] = vx * scale
            velocity[2] = vz * scale

    @classmethod
    def try_place_from_item(cls, player, selected):
        if selected is None:
            return False
        cart_type = cls.type_for_item(getattr(selected, "id", None))
        if cart_type is None:
            return False
        world = getattr(player, "world", None)
        if world is None:
            return False
        raycast = getattr(player, "_raycast_blocks", None)
        if not callable(raycast):
            return False
        hit, _normal = raycast(include_intangible=True)
        if hit is None or world.get_block_type(*hit) != "rail":
            return False
        world.spawn_entity(cls(
            world, (float(hit[0]), float(hit[1]) + 0.44, float(hit[2])),
            cart_type=cart_type,
        ))
        if getattr(player, "gamemode", 0) != 1:
            inventory = getattr(player, "inventory", None)
            if inventory is not None:
                inventory.remove_item(inventory.active_idx)
        return True

    def __init__(self, world, pos, velocity=(0.0, 0.0, 0.0), cart_type=0):
        super().__init__(pos, size=(0.98, 0.7, 0.98))
        self.world_obj = world
        self.cart_type = max(self.STANDARD, min(self.FURNACE, int(cart_type)))
        self.prev_pos = list(self.pos)
        self.vel = [float(v) for v in velocity]
        self.rider = None
        self.damage_taken = 0
        self.time_since_hit = 0
        self.forward_direction = 1
        self._reverse = False
        self.prevent_entity_spawning = True
        self.yaw = self.prev_yaw = 0.0
        self._drop_handled = False
        self._last_rail_cell = None
        self.fuel = 0
        self.push_x = 0.0
        self.push_z = 0.0
        self.cargo_items = [None] * self.INVENTORY_SIZE

    def can_be_collided_with(self):
        return not self.dead

    def can_be_pushed(self):
        return not self.dead

    def get_interaction_bounds(self):
        x, y, z, yaw, _pitch = self._track_render_pose()
        angle = math.radians(yaw)
        half_x = 0.625 * abs(math.cos(angle)) + 0.5 * abs(math.sin(angle))
        half_z = 0.625 * abs(math.sin(angle)) + 0.5 * abs(math.cos(angle))
        return (
            x - half_x, y - 0.375, z - half_z,
            x + half_x, y + 0.375, z + half_z,
        )

    def interact(self, player):
        if self.dead or player is None or getattr(player, "health", 1) <= 0:
            return False
        if self.cart_type == self.CHEST:
            opener = getattr(player, "minecart_chest_opener", None)
            if callable(opener):
                opener(self)
            return True
        if self.cart_type == self.FURNACE:
            world = self.world_obj
            if (bool(getattr(world, "_multiplayer_client_world", False))
                    and bool(getattr(self, "_mp_remote_entity", False))):
                request = getattr(world, "_network_entity_interact_callback", None)
                if callable(request):
                    request(self, "furnace_fuel")
                return True
            inventory = getattr(player, "inventory", None)
            stack = inventory.get_active_item() if inventory is not None else None
            if stack is not None and stack.id == "coal" and stack.count > 0:
                stack.count -= 1
                inventory.clear_if_empty(inventory.active_idx)
                self.fuel += 1200
            self.push_x = float(self.pos[0]) - float(player.pos[0])
            self.push_z = float(self.pos[2]) - float(player.pos[2])
            return True
        old_mount = getattr(player, "riding_entity", None)
        if old_mount is self:
            self.dismount()
            return True
        if old_mount is not None:
            old_mount.dismount()
        self.rider = player
        player.riding_entity = self
        self._reset_passenger_motion(player)
        self._position_rider()
        return True

    def get_stack(self, slot):
        slot = int(slot)
        return self.cargo_items[slot] if 0 <= slot < self.INVENTORY_SIZE else None

    def set_stack(self, slot, stack):
        slot = int(slot)
        if 0 <= slot < self.INVENTORY_SIZE:
            if stack is not None:
                stack.count = min(int(stack.count), 64)
            self.cargo_items[slot] = stack

    def get_size_inventory(self):
        return self.INVENTORY_SIZE

    def get_inventory_stack_limit(self):
        return 64

    def get_inv_name(self):
        return "Minecart"

    def decr_stack_size(self, slot, amount):
        slot = int(slot)
        amount = max(0, int(amount))
        stack = self.get_stack(slot)
        if stack is None or amount <= 0:
            return None
        if stack.count <= amount:
            self.cargo_items[slot] = None
            return stack
        return stack.split(amount)

    def on_inventory_changed(self):
        pass

    @staticmethod
    def _reset_passenger_motion(rider):
        velocity = getattr(rider, "vel", None)
        if velocity is not None:
            velocity[:] = [0.0, 0.0, 0.0]
        if hasattr(rider, "on_ground"):
            rider.on_ground = False
        if hasattr(rider, "_fall_distance"):
            rider._fall_distance = 0.0
        if hasattr(rider, "_fall_pending"):
            rider._fall_pending = 0.0
        if hasattr(rider, "distance_walked_modified"):
            rider.distance_walked_modified = 0.0
        if hasattr(rider, "distance_walked_on_step_modified"):
            rider.distance_walked_on_step_modified = 0.0
        if hasattr(rider, "next_step_distance"):
            rider.next_step_distance = getattr(rider, "STEP_DISTANCE", 1)
        for field in ("_frame_distance_accum", "_frame_dx_accum", "_frame_dz_accum"):
            if hasattr(rider, field):
                setattr(rider, field, 0.0)

    def dismount(self):
        rider = self.rider
        if rider is not None:
            if getattr(rider, "riding_entity", None) is self:
                rider.riding_entity = None
            anchor_x, _anchor_y, anchor_z, yaw = self._rider_anchor()
            on_rail = self._rail_cell() is not None
            side_x = anchor_x + 0.9 * math.sin(math.radians(yaw))
            side_z = anchor_z + 0.9 * math.cos(math.radians(yaw))
            rider.pos[0] = side_x
            rider.pos[1] = self.pos[1] - 0.3 if on_rail else self.pos[1]
            rider.pos[2] = side_z
            self._reset_passenger_motion(rider)
            if hasattr(rider, "_mounted_visual_pos"):
                rider._mounted_visual_pos = None
            sync_camera = getattr(rider, "sync_camera", None)
            if callable(sync_camera):
                sync_camera()
        self.rider = None

    def _rider_anchor(self):
        x, render_y, z, yaw, _pitch = self._track_render_pose()
        return x, render_y, z, yaw

    def _position_rider(self):
        if self.rider is None:
            return
        x, cart_y, z, _yaw = self._rider_anchor()
        self.rider.pos[0] = x
        self.rider.pos[1] = cart_y - 0.7
        self.rider.pos[2] = z
        self._reset_passenger_motion(self.rider)
        sync_camera = getattr(self.rider, "sync_camera", None)
        if callable(sync_camera):
            sync_camera()

    def _track_render_pose(self, position=None):
        px, py, pz = self.pos if position is None else position
        cell = self._rail_cell(position=(px, py, pz))
        if cell is None:
            dx = px - self.prev_pos[0]
            dz = pz - self.prev_pos[2]
            if math.hypot(dx, dz) <= 1.0e-4:
                dx, dz = self.vel[0], self.vel[2]
            yaw = math.degrees(math.atan2(-dz, dx))
            return px, py + 0.25, pz, yaw, 0.0

        x, y, z, metadata = cell
        start, end = _TRACK_PATHS[metadata]
        start_x, start_z = x + start[0] * 0.5, z + start[2] * 0.5
        axis_x, axis_z = end[0] - start[0], end[2] - start[2]
        axis_len_sq = axis_x * axis_x + axis_z * axis_z
        relative_x, relative_z = px - start_x, pz - start_z
        along = max(0.0, min(1.0, 2.0 * (
            relative_x * axis_x + relative_z * axis_z
        ) / axis_len_sq))
        render_x = start_x + axis_x * along * 0.5
        render_z = start_z + axis_z * along * 0.5
        slope_delta = end[1] - start[1]
        render_y = y + slope_delta * along + (1.0 if slope_delta < 0 else 0.0)
        yaw = math.degrees(math.atan2(-axis_z, axis_x))
        reverse_on_track = None
        previous_cell = self._last_rail_cell
        if previous_cell is not None and previous_cell[:3] != cell[:3]:
            previous_x, _previous_y, previous_z, _previous_meta = previous_cell
            entry_offset = (previous_x - x, previous_z - z)
            if entry_offset == (start[0], start[2]):
                reverse_on_track = False
            elif entry_offset == (end[0], end[2]):
                reverse_on_track = True

        if reverse_on_track is None:
            dx = px - self.prev_pos[0]
            dz = pz - self.prev_pos[2]
            direction_len = math.hypot(dx, dz)
            direction_dot = dx * axis_x + dz * axis_z
            if direction_len > 1.0e-4 and abs(direction_dot) > direction_len * 0.05:
                reverse_on_track = direction_dot < 0.0
            else:
                dx, dz = self.vel[0], self.vel[2]
                direction_len = math.hypot(dx, dz)
                if direction_len > 1.0e-4:
                    reverse_on_track = dx * axis_x + dz * axis_z < 0.0
                else:
                    reverse_on_track = None

        if reverse_on_track is None:
            direct_delta = (yaw - self.yaw + 180.0) % 360.0 - 180.0
            reverse_delta = (yaw + 180.0 - self.yaw + 180.0) % 360.0 - 180.0
            reverse_on_track = abs(reverse_delta) < abs(direct_delta)
        if reverse_on_track:
            yaw += 180.0
        horizontal_length = math.hypot(axis_x, axis_z)
        slope = 2.0 * slope_delta / horizontal_length
        pitch = math.degrees(math.atan2(
            slope, math.sqrt(1.0 + slope * slope)
        )) * 73.0
        if reverse_on_track:
            pitch = -pitch
        return render_x, render_y, render_z, yaw, pitch

    def attack_entity_from(self, attacker, damage):
        if self.dead:
            return False
        self.forward_direction = -self.forward_direction
        self.time_since_hit = 10
        self.damage_taken += max(0, int(damage)) * 10
        if self.damage_taken > 40:
            self._drop_cart()
            self.dead = True
        return True

    def _drop_cart(self):
        if self._drop_handled:
            return
        self._drop_handled = True
        self.dismount()
        from game.entities.item import ItemEntity, ItemStack
        self.world_obj.spawn_entity(ItemEntity(
            ItemStack("minecart"), tuple(self.pos), pickup_delay=10,
        ))
        extra = "chest" if self.cart_type == self.CHEST else (
            "furnace" if self.cart_type == self.FURNACE else None
        )
        if extra is not None:
            self.world_obj.spawn_entity(ItemEntity(
                ItemStack(extra), tuple(self.pos), pickup_delay=10,
            ))
        self._drop_cargo_items()

    def _drop_cargo_items(self):
        if self.cart_type != self.CHEST or not any(self.cargo_items):
            return
        from game.entities.item import ItemEntity, ItemStack
        for slot, stack in enumerate(self.cargo_items):
            if stack is None or int(getattr(stack, "count", 0)) <= 0:
                self.cargo_items[slot] = None
                continue
            remaining = int(stack.count)
            while remaining > 0:
                count = min(remaining, random.randint(10, 30))
                remaining -= count
                px = self.pos[0] + random.random() * 0.8 + 0.1
                py = self.pos[1] + random.random() * 0.8 + 0.1
                pz = self.pos[2] + random.random() * 0.8 + 0.1
                velocity = (
                    random.gauss(0.0, 1.0) * 0.05,
                    random.gauss(0.0, 1.0) * 0.05 + 0.2,
                    random.gauss(0.0, 1.0) * 0.05,
                )
                self.world_obj.spawn_entity(ItemEntity(
                    ItemStack(stack.id, count, stack.damage),
                    (px, py, pz), velocity=velocity,
                ))
            self.cargo_items[slot] = None

    def set_dead(self):
        self.dismount()
        self._drop_cargo_items()
        self.dead = True

    def get_bounding_box(self, position=None):
        px, py, pz = self.pos if position is None else position
        rail_cell = self._rail_cell(position=(px, py, pz))
        if rail_cell is not None:
            px, rail_y, pz, _yaw, _pitch = self._track_render_pose((px, py, pz))
            py = rail_y - float(self.size[1]) * 0.5
        half_x = float(self.size[0]) * 0.5
        half_z = float(self.size[2]) * 0.5
        return (
            px - half_x, py, pz - half_z,
            px + half_x, py + float(self.size[1]), pz + half_z,
        )

    def get_collision_box(self, other=None):
        if other is None:
            return self.get_bounding_box()
        get_box = getattr(other, "get_bounding_box", None)
        if callable(get_box):
            return get_box()
        if not hasattr(other, "pos") or not hasattr(other, "size"):
            return None
        px, py, pz = other.pos
        half_x = float(other.size[0]) * 0.5
        half_z = float(other.size[2]) * 0.5
        return (px - half_x, py, pz - half_z,
                px + half_x, py + float(other.size[1]), pz + half_z)

    def apply_entity_collision(self, other):
        if (other is self or other is self.rider
                or getattr(other, "riding_entity", None) is self
                or getattr(other, "dead", False)
                or not hasattr(other, "pos") or not hasattr(other, "vel")):
            return
        dx = float(other.pos[0]) - self.pos[0]
        dz = float(other.pos[2]) - self.pos[2]
        length_sq = dx * dx + dz * dz
        if length_sq < 1.0e-4:
            return
        length = math.sqrt(length_sq)
        dx /= length
        dz /= length
        inverse_distance = min(1.0, 1.0 / length)
        reduction = max(0.0, min(1.0, 1.0 - float(
            getattr(self, "entity_collision_reduction", 0.0)
        )))
        impulse = inverse_distance * 0.1 * reduction * 0.5
        dx *= impulse
        dz *= impulse
        if isinstance(other, MinecartEntity):
            if other.cart_type == self.FURNACE and self.cart_type != self.FURNACE:
                self.vel[0] *= 0.2
                self.vel[2] *= 0.2
                self.vel[0] += other.vel[0] - dx
                self.vel[2] += other.vel[2] - dz
                other.vel[0] *= 0.7
                other.vel[2] *= 0.7
            elif other.cart_type != self.FURNACE and self.cart_type == self.FURNACE:
                other.vel[0] *= 0.2
                other.vel[2] *= 0.2
                other.vel[0] += self.vel[0] + dx
                other.vel[2] += self.vel[2] + dz
                self.vel[0] *= 0.7
                self.vel[2] *= 0.7
            else:
                vx = (self.vel[0] + other.vel[0]) * 0.5
                vz = (self.vel[2] + other.vel[2]) * 0.5
                self.vel[0] *= 0.2
                self.vel[2] *= 0.2
                self.vel[0] += vx - dx
                self.vel[2] += vz - dz
                other.vel[0] *= 0.2
                other.vel[2] *= 0.2
                other.vel[0] += vx + dx
                other.vel[2] += vz + dz
        else:
            is_player = other.__class__.__name__ == "Player"
            if (self.cart_type == self.STANDARD and not is_player
                    and bool(getattr(other, "is_living_entity", False))
                    and self.vel[0] ** 2 + self.vel[2] ** 2 > 0.01
                    and self.rider is None
                    and getattr(other, "riding_entity", None) is None):
                self.rider = other
                other.riding_entity = self
                self._position_rider()
                return
            self.vel[0] -= dx
            self.vel[2] -= dz
            other.vel[0] += dx * 0.25
            other.vel[2] += dz * 0.25

        self._limit_horizontal_speed(self)
        if isinstance(other, MinecartEntity):
            self._limit_horizontal_speed(other)

    def _overlaps_entity_for_cart_collision(self, other):
        if not hasattr(other, "pos") or not hasattr(other, "size"):
            return False
        own = self.get_bounding_box()
        theirs = self.get_collision_box(other)
        if theirs is None:
            return False
        return (
            own[0] - 0.2 < theirs[3] and own[3] + 0.2 > theirs[0]
            and own[1] < theirs[4] and own[4] > theirs[1]
            and own[2] - 0.2 < theirs[5] and own[5] + 0.2 > theirs[2]
        )

    def _rail_cell(self, position=None):
        world = self.world_obj
        px, py, pz = self.pos if position is None else position
        x, y, z = (math.floor(v + 0.5) for v in (px, py, pz))
        if world.get_block_type(x, y, z) != "rail" and world.get_block_type(x, y - 1, z) == "rail":
            y -= 1
        if world.get_block_type(x, y, z) != "rail":
            return None
        return x, y, z, max(0, min(9, int(world.get_metadata(x, y, z))))

    def _move_on_rail(self, ticks, cell, previous_cell=None):
        x, y, z, metadata = cell
        path = _TRACK_PATHS[metadata]
        start, end = path
        sx, sz = start[0] * 0.5, start[2] * 0.5
        ex, ez = end[0] * 0.5, end[2] * 0.5
        axis_x, axis_z = ex - sx, ez - sz
        axis_len_sq = axis_x * axis_x + axis_z * axis_z
        if axis_len_sq <= 1.0e-12:
            return
        track_axis_x, track_axis_z = axis_x, axis_z

        if metadata == 2:
            self.vel[0] -= 1.0 / 128.0 * ticks
        elif metadata == 3:
            self.vel[0] += 1.0 / 128.0 * ticks
        elif metadata == 4:
            self.vel[2] += 1.0 / 128.0 * ticks
        elif metadata == 5:
            self.vel[2] -= 1.0 / 128.0 * ticks

        start_x, start_z = x + sx, z + sz
        relative_x, relative_z = self.pos[0] - start_x, self.pos[2] - start_z
        along = (
            relative_x * track_axis_x + relative_z * track_axis_z
        ) / axis_len_sq

        self._limit_horizontal_speed(self)
        speed = math.hypot(self.vel[0], self.vel[2])
        dot = self.vel[0] * track_axis_x + self.vel[2] * track_axis_z
        direction = None

        if previous_cell is not None:
            previous_x, _previous_y, previous_z, _previous_meta = previous_cell
            entry_offset = (previous_x - x, previous_z - z)
            if entry_offset == (start[0], start[2]):
                direction = 1.0
            elif entry_offset == (end[0], end[2]):
                direction = -1.0

        if direction is None:
            dot_epsilon = max(1.0e-8, speed * math.sqrt(axis_len_sq) * 0.05)
            if dot > dot_epsilon:
                direction = 1.0
            elif dot < -dot_epsilon:
                direction = -1.0
            else:
                direction = 1.0 if along <= 0.5 else -1.0

        axis_x = track_axis_x * direction
        axis_z = track_axis_z * direction
        axis_length = math.sqrt(axis_len_sq)
        self.vel[0] = speed * axis_x / axis_length
        self.vel[2] = speed * axis_z / axis_length

        self.pos[0] = start_x + track_axis_x * along
        self.pos[2] = start_z + track_axis_z * along

        slope_delta = end[1] - start[1]
        rail_y = y + (0.9375 if slope_delta else 0.4375)
        self.pos[1] = rail_y + (along - 0.5) * slope_delta

        dx = max(-_MAX_TRACK_SPEED, min(_MAX_TRACK_SPEED, self.vel[0])) * ticks
        dz = max(-_MAX_TRACK_SPEED, min(_MAX_TRACK_SPEED, self.vel[2])) * ticks
        old_cell = (x, y, z)
        self.move(dx, 0.0, dz)
        relative_x, relative_z = self.pos[0] - start_x, self.pos[2] - start_z
        track_along = (
            relative_x * track_axis_x + relative_z * track_axis_z
        ) / axis_len_sq
        self.pos[1] = rail_y + (track_along - 0.5) * slope_delta

        next_cell = self._rail_cell()
        if next_cell is not None and (next_cell[0], next_cell[1], next_cell[2]) != old_cell:
            nx, ny, nz, _ = next_cell
            if ny > y:
                self.pos[1] = max(self.pos[1], ny + 0.4375)
            elif ny < y:
                self.pos[1] = min(self.pos[1], ny + 0.4375)

        powered = False
        if self.rider is not None:
            friction = 0.997
        elif self.cart_type == self.FURNACE:
            push_length = math.hypot(self.push_x, self.push_z)
            if push_length > 0.01:
                powered = True
                self.push_x /= push_length
                self.push_z /= push_length
                self.vel[0] *= 0.8
                self.vel[2] *= 0.8
                self.vel[0] += self.push_x * 0.04
                self.vel[2] += self.push_z * 0.04
            else:
                self.vel[0] *= 0.9
                self.vel[2] *= 0.9
            friction = 0.96
        else:
            friction = 0.96
        self.vel[0] *= friction
        self.vel[1] = 0.0
        self.vel[2] *= friction

        if self.cart_type == self.FURNACE:
            push_length = math.hypot(self.push_x, self.push_z)
            horizontal_speed_sq = self.vel[0] ** 2 + self.vel[2] ** 2
            if push_length > 0.01 and horizontal_speed_sq > 0.001:
                self.push_x /= push_length
                self.push_z /= push_length
                if self.push_x * self.vel[0] + self.push_z * self.vel[2] < 0.0:
                    self.push_x = self.push_z = 0.0
                else:
                    self.push_x, self.push_z = self.vel[0], self.vel[2]

        self._limit_horizontal_speed(self)

        if powered and random.randrange(4) == 0:
            self.fuel -= 1
            if self.fuel < 0:
                self.push_x = self.push_z = 0.0
            spawn_particle = getattr(self.world_obj, "spawn_particle", None)
            if callable(spawn_particle):
                spawn_particle("largesmoke", self.pos[0], self.pos[1] + 0.8,
                               self.pos[2], 0.0, 0.0, 0.0)

    def _move_off_rail(self, ticks):
        self.vel[1] -= 0.04 * ticks
        self._limit_horizontal_speed(self)
        self.vel[1] = max(-_MAX_TRACK_SPEED, min(_MAX_TRACK_SPEED, self.vel[1]))
        self.move(self.vel[0] * ticks, self.vel[1] * ticks, self.vel[2] * ticks)
        if self.on_ground:
            self.vel[0] *= 0.5
            self.vel[1] *= 0.5
            self.vel[2] *= 0.5
        else:
            self.vel[0] *= 0.95
            self.vel[1] *= 0.95
            self.vel[2] *= 0.95

    def update(self, dt):
        if self.dead:
            return
        self.prev_pos[:] = self.pos
        ticks = min(max(float(dt), 0.0), MAX_DT) * TICKS_PER_SECOND
        self.prev_yaw = self.yaw
        if self.time_since_hit > 0:
            self.time_since_hit = max(0, self.time_since_hit - ticks)
        if self.damage_taken > 0:
            self.damage_taken = max(0, self.damage_taken - ticks)

        cell = self._rail_cell()
        if cell is not None:
            previous_cell = self._last_rail_cell
            if (previous_cell is not None
                    and previous_cell[:3] == cell[:3]):
                previous_cell = None
            self._move_on_rail(ticks, cell, previous_cell=previous_cell)
        else:
            self._move_off_rail(ticks)

        self._last_rail_cell = cell

        dx = self.prev_pos[0] - self.pos[0]
        dz = self.prev_pos[2] - self.pos[2]
        if dx * dx + dz * dz > 0.001:
            yaw = math.degrees(math.atan2(dz, dx))
            if self._reverse:
                yaw += 180.0
            while yaw - self.prev_yaw < -180.0:
                yaw += 360.0
            while yaw - self.prev_yaw >= 180.0:
                yaw -= 360.0
            self.yaw = yaw
        delta = self.yaw - self.prev_yaw
        while delta >= 180.0:
            delta -= 360.0
        while delta < -180.0:
            delta += 360.0
        if delta < -170.0 or delta >= 170.0:
            self.yaw += 180.0
            self._reverse = not self._reverse

        rider = self.rider
        if rider is not None and (rider.dead or getattr(rider, "health", 1) <= 0):
            self.dismount()
        else:
            self._position_rider()

        for other in getattr(self.world_obj, "entities", ()):
            if (other is self or other is self.rider or getattr(other, "dead", False)
                    or not isinstance(other, MinecartEntity)):
                continue
            can_be_pushed = getattr(other, "can_be_pushed", None)
            if not callable(can_be_pushed) or not can_be_pushed():
                continue
            if self._overlaps_entity_for_cart_collision(other):
                other.apply_entity_collision(self)

    def draw(self):
        if self.dead:
            return
        try:
            from OpenGL.GL import (
                GL_ALPHA_TEST, GL_COLOR_MATERIAL, GL_CULL_FACE, GL_LIGHT0,
                GL_LIGHT1, GL_LIGHTING, GL_NORMALIZE, GL_TEXTURE_2D,
                glColor3f, glDisable, glEnable, glIsEnabled, glPopMatrix,
                glPushMatrix, glRotatef, glScalef, glTranslatef,
            )
        except Exception:
            return
        from engine.textures import Texture
        from game.entities.item import Item
        from game.entities.player_model import ModelPart

        global _CART_TEXTURE, _CART_MODEL, _CART_CARGO_TEXTURE
        if _CART_TEXTURE is None:
            _CART_TEXTURE = Texture("game/textures/item/cart.png")
        if _CART_MODEL is None:
            _CART_MODEL = _create_cart_model(ModelPart)

        renderer = getattr(self.world_obj, "renderer", None) or Entity.renderer
        partial = 0.0
        if renderer is not None:
            partial = max(0.0, min(1.0, float(self.world_obj.partial_ticks())))
        position = tuple(
            self.prev_pos[index] + (self.pos[index] - self.prev_pos[index]) * partial
            for index in range(3)
        )
        x, y, z, yaw, pitch = self._track_render_pose(position)
        if renderer is not None:
            ox, oy, oz = renderer.camera_relative_offset(x, y, z)
        else:
            ox, oy, oz = x, y, z
        cull_enabled = bool(glIsEnabled(GL_CULL_FACE))
        lighting_enabled = bool(glIsEnabled(GL_LIGHTING))
        light0_enabled = bool(glIsEnabled(GL_LIGHT0))
        light1_enabled = bool(glIsEnabled(GL_LIGHT1))
        color_material_enabled = bool(glIsEnabled(GL_COLOR_MATERIAL))
        normalize_enabled = bool(glIsEnabled(GL_NORMALIZE))
        texture_enabled = bool(glIsEnabled(GL_TEXTURE_2D))
        alpha_enabled = bool(glIsEnabled(GL_ALPHA_TEST))
        standard_lighting_setup = False
        glPushMatrix()
        try:
            if not lighting_enabled:
                from engine.legacy_lighting import enable_standard_item_lighting
                enable_standard_item_lighting()
                standard_lighting_setup = True
            glEnable(GL_NORMALIZE)
            glTranslatef(float(ox), float(oy), float(oz))
            glRotatef(float(yaw), 0.0, 1.0, 0.0)
            glRotatef(float(pitch), 0.0, 0.0, 1.0)
            hit_time = max(0.0, self.time_since_hit - partial)
            damage = max(0.0, self.damage_taken - partial)
            if hit_time > 0.0:
                angle = math.sin(hit_time) * hit_time * damage / 10.0 * self.forward_direction
                glRotatef(float(angle), 1.0, 0.0, 0.0)
            glEnable(GL_TEXTURE_2D)
            brightness = self.get_entity_brightness()
            glColor3f(brightness, brightness, brightness)
            if self.cart_type in (self.CHEST, self.FURNACE):
                from game.block import Block
                if _CART_CARGO_TEXTURE is None:
                    _CART_CARGO_TEXTURE = Block._get_texture()
                _CART_CARGO_TEXTURE.bind()
                glPushMatrix()
                try:
                    glScalef(0.75, 0.75, 0.75)
                    glTranslatef(0.0, 5.0 / 16.0, 0.0)
                    glRotatef(90.0, 0.0, 1.0, 0.0)
                    if self.cart_type == self.CHEST:
                        _draw_block_cube((26, 26, 25, 25, 27, 26))
                    else:
                        _draw_block_cube((45, 45, 1, 1, 44, 45))
                finally:
                    glPopMatrix()
                _CART_CARGO_TEXTURE.unbind()
            _CART_TEXTURE.bind()
            glScalef(-1.0, -1.0, 1.0)
            for part in _CART_MODEL:
                part.render(1.0 / 16.0)
        finally:
            glColor3f(1.0, 1.0, 1.0)
            _CART_TEXTURE.unbind()
            (glEnable if cull_enabled else glDisable)(GL_CULL_FACE)
            if standard_lighting_setup:
                from engine.legacy_lighting import disable_standard_item_lighting
                disable_standard_item_lighting()
            (glEnable if lighting_enabled else glDisable)(GL_LIGHTING)
            (glEnable if light0_enabled else glDisable)(GL_LIGHT0)
            (glEnable if light1_enabled else glDisable)(GL_LIGHT1)
            (glEnable if color_material_enabled else glDisable)(GL_COLOR_MATERIAL)
            (glEnable if normalize_enabled else glDisable)(GL_NORMALIZE)
            (glEnable if texture_enabled else glDisable)(GL_TEXTURE_2D)
            (glEnable if alpha_enabled else glDisable)(GL_ALPHA_TEST)
            glPopMatrix()


def _create_cart_model(ModelPart):
    side = ModelPart(0, 10, -10, -8, -1, 20, 16, 2)
    side.set_rotation_point(0.0, 4.0, 0.0)
    side.rotate_angle_x = math.pi / 2.0

    floor = ModelPart(44, 10, -9, -7, -1, 18, 14, 1)
    floor.set_rotation_point(0.0, 4.1, 0.0)
    floor.rotate_angle_x = -math.pi / 2.0

    walls = []
    for pivot, angle in (
        ((-9.0, 4.0, 0.0), 3.0 * math.pi / 2.0),
        ((9.0, 4.0, 0.0), math.pi / 2.0),
        ((0.0, 4.0, -7.0), math.pi),
        ((0.0, 4.0, 7.0), 0.0),
    ):
        part = ModelPart(0, 0, -8, -9, -1, 16, 8, 2)
        part.set_rotation_point(*pivot)
        part.rotate_angle_y = angle
        walls.append(part)
    return [side, *walls, floor]


def _draw_block_cube(faces):
    from OpenGL.GL import (
        GL_CULL_FACE, GL_QUADS, glBegin, glDisable, glEnable, glEnd,
        glIsEnabled, glNormal3f, glTexCoord2f, glVertex3f,
    )

    geometry = (
        (((0.5, -0.5, -0.5), (0.5, -0.5, 0.5), (0.5, 0.5, 0.5), (0.5, 0.5, -0.5)), (1, 0, 0)),
        (((-0.5, -0.5, 0.5), (-0.5, -0.5, -0.5), (-0.5, 0.5, -0.5), (-0.5, 0.5, 0.5)), (-1, 0, 0)),
        (((-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, -0.5, 0.5), (-0.5, -0.5, 0.5)), (0, -1, 0)),
        (((-0.5, 0.5, 0.5), (0.5, 0.5, 0.5), (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5)), (0, 1, 0)),
        (((0.5, -0.5, 0.5), (-0.5, -0.5, 0.5), (-0.5, 0.5, 0.5), (0.5, 0.5, 0.5)), (0, 0, 1)),
        (((-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5)), (0, 0, -1)),
    )
    tile = 1.0 / 16.0
    cull_enabled = bool(glIsEnabled(GL_CULL_FACE))
    glDisable(GL_CULL_FACE)
    try:
        glBegin(GL_QUADS)
        for index, (vertices, normal) in zip(faces, geometry):
            col, row = index % 16, index // 16
            u0, v0 = col * tile, 1.0 - (row + 1) * tile
            glNormal3f(*normal)
            for uv, vertex in zip(
                ((u0, v0), (u0 + tile, v0),
                 (u0 + tile, v0 + tile), (u0, v0 + tile)),
                vertices,
            ):
                glTexCoord2f(*uv)
                glVertex3f(*vertex)
        glEnd()
    finally:
        (glEnable if cull_enabled else glDisable)(GL_CULL_FACE)
