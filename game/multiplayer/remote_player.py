
from __future__ import annotations

import math

from game.entities.entity import Entity
from game.entities.player_cosmetics import initialize_cape_motion, advance_cape_motion


_REMOTE_ITEM_DEFS = None


def _remote_item_definition(item_id):
    global _REMOTE_ITEM_DEFS
    if _REMOTE_ITEM_DEFS is None:
        from game.content_data import load_item_definitions
        _REMOTE_ITEM_DEFS = load_item_definitions()
    return _REMOTE_ITEM_DEFS.get(str(item_id), {})


def _remote_item_max_stack(item_id):
    definition = _remote_item_definition(item_id)
    if "stack" in definition:
        return max(1, int(definition["stack"]))
    if definition.get("type") in {"tool", "melee_weapon", "food", "armor"}:
        return 1
    if item_id in {"wooden_door", "iron_door"}:
        return 1
    return 64


class RemoteItemStack:

    __slots__ = ("id", "count", "damage", "animations_to_go")

    def __init__(self, item_id, count=1, damage=0):
        self.id = str(item_id)
        self.count = int(count)
        self.damage = int(damage)
        self.animations_to_go = 0

    @property
    def max_stack_size(self):
        try:
            return _remote_item_max_stack(self.id)
        except Exception:
            return 64

    def split(self, amount):
        amount = max(0, min(int(amount), int(self.count)))
        self.count -= amount
        return RemoteItemStack(self.id, amount, self.damage)

    def copy(self):
        return RemoteItemStack(self.id, self.count, self.damage)


class RemoteInventory:

    stack_limit = 64
    MAIN_SIZE = 36
    ARMOR_SIZE = 4

    def __init__(self):
        self.main = [None] * 36
        self.armor = [None] * 4
        self.current_item = 0

    def get_stack(self, slot):
        slot = int(slot)
        if 0 <= slot < 36:
            return self.main[slot]
        if 36 <= slot < 40:
            return self.armor[slot - 36]
        return None

    def set_stack(self, slot, stack):
        slot = int(slot)
        if 0 <= slot < 36:
            self.main[slot] = stack
        elif 36 <= slot < 40:
            self.armor[slot - 36] = stack

    def get_active_item(self):
        if 0 <= self.current_item < len(self.main):
            return self.main[self.current_item]
        return None

    def add_stack(self, stack):
        if stack is None:
            return True
        item_id = str(getattr(stack, "id", ""))
        try:
            remaining = max(0, int(stack.count))
            damage = max(0, int(getattr(stack, "damage", 0)))
            limit = max(1, int(_remote_item_max_stack(item_id)))
        except (TypeError, ValueError, OverflowError):
            return False
        if not item_id or remaining <= 0:
            return True
        capacity = sum(
            max(0, limit - existing.count)
            for existing in self.main
            if existing is not None and existing.id == item_id
            and existing.damage == damage
        ) + sum(1 for existing in self.main if existing is None) * limit
        if capacity < remaining:
            return False
        for existing in self.main:
            if (existing is not None and existing.id == item_id
                    and existing.damage == damage and existing.count < limit):
                moved = min(remaining, limit - existing.count)
                existing.count += moved
                remaining -= moved
                if remaining <= 0:
                    return True
        while remaining > 0:
            slot = self.main.index(None)
            moved = min(remaining, limit)
            self.main[slot] = RemoteItemStack(item_id, moved, damage)
            remaining -= moved
        return True



class _RemoteRidePose:
    pass


_REMOTE_RIDE_POSE = _RemoteRidePose()



class RemoteCraftInventory:

    stack_limit = 64

    def __init__(self, width=2, height=2):
        self.width = int(width)
        self.height = int(height)
        self.stacks = [None] * (self.width * self.height)

    @property
    def size(self):
        return len(self.stacks)

    def get_stack(self, slot):
        slot = int(slot)
        if 0 <= slot < len(self.stacks):
            return self.stacks[slot]
        return None

    def set_stack(self, slot, stack):
        slot = int(slot)
        if 0 <= slot < len(self.stacks):
            self.stacks[slot] = stack

    def cells(self):
        cells = [None] * 9
        for y in range(self.height):
            for x in range(self.width):
                stack = self.stacks[x + y * self.width]
                if stack is not None and int(getattr(stack, "count", 0)) > 0:
                    cells[x + y * 3] = stack.id
        return cells

    def consume_one_each(self):
        for i, stack in enumerate(self.stacks):
            if stack is not None:
                stack.count = int(stack.count) - 1
                if int(stack.count) <= 0:
                    self.stacks[i] = None

    def clear(self):
        self.stacks = [None] * (self.width * self.height)

    def snapshot(self):
        out = []
        for stack in self.stacks:
            if stack is None:
                out.append(None)
            else:
                out.append({
                    "id": str(stack.id),
                    "count": int(stack.count),
                    "damage": int(getattr(stack, "damage", 0)),
                })
        return out

    def load_snapshot(self, data):
        from game.multiplayer.remote_player import RemoteItemStack
        slots = list(data or ())
        for i in range(len(self.stacks)):
            entry = slots[i] if i < len(slots) else None
            if isinstance(entry, dict) and entry.get("id"):
                self.stacks[i] = RemoteItemStack(
                    entry["id"], entry.get("count", 1), entry.get("damage", 0),
                )
            else:
                self.stacks[i] = None


class RemotePlayer(Entity):

    is_living_entity = True
    EYE_HEIGHT = 1.62

    def __init__(self, player_id: int, name: str, pos, yaw: float = 0.0, pitch: float = 0.0):
        super().__init__(list(pos), size=(0.6, 1.8, 0.6))
        self.network_id = int(player_id)
        self._mp_remote_player = True
        self.username = str(name)[:16] or "Player"
        self.skin_path = "char.png"
        self.skin_username = str(name).strip() or "Player"
        self.cape_path = None
        self.cape_texture = None
        self.yaw = float(yaw)
        self.pitch = float(pitch)
        self.prev_yaw = self.yaw
        self.prev_pitch = self.pitch
        self.prev_pos = list(self.pos)
        initialize_cape_motion(self)
        self.render_yaw_offset = self.yaw
        self.prev_render_yaw_offset = self.yaw
        self._target_body_yaw = self.yaw
        self.limb_swing = 0.0
        self.prev_limb_swing = 0.0
        self.limb_swing_amount = 0.0
        self.prev_limb_swing_amount = 0.0
        self._target_limb_swing = 0.0
        self._target_limb_swing_amount = 0.0
        self.hurt_time = 0.0
        self.death_time = 0.0
        self.health = 20
        self.max_hurt_time = 10.0
        self.attacked_at_yaw = 0.0
        self._fire_ticks = 0
        self.riding_entity = None
        self.is_sneaking = False
        self.is_sleeping = False
        self.sleep_timer = 0
        self.bed_location = None
        self._bed_sleep_meta = None
        self._pre_sleep_yaw = None
        self._pre_sleep_pitch = None
        self._pre_sleep_body_yaw = None
        self.ticks_existed = 0
        self._target_pos = list(self.pos)
        self._target_yaw = self.yaw
        self._target_pitch = self.pitch
        self._animation_tick_accum = 0.0
        self._animation_distance_accum = 0.0
        self._sleep_tick_accum = 0.0
        self.renderer = Entity.renderer
        self.world = Entity.world
        self.inventory = RemoteInventory()
        self.craft2 = RemoteCraftInventory(2, 2)
        self.craft3 = RemoteCraftInventory(3, 3)
        self.craft_result = None
        self.cursor_stack = None
        self.break_target = None
        self.break_progress = 0.0
        self._is_swing_in_progress = False
        self._swing_progress_int = 0
        self.fishing_hook = None
        self.swing_progress = 0.0
        self.prev_swing_progress = 0.0
        self.damage_callback = None
        self.pvp_attack_callback = None

    def can_be_collided_with(self) -> bool:
        return self.health > 0 and not self.dead

    def can_be_pushed(self) -> bool:
        return self.can_be_collided_with()

    def is_player_sleeping(self) -> bool:
        return bool(self.is_sleeping)

    def is_player_fully_asleep(self) -> bool:
        return self.is_player_sleeping() and int(self.sleep_timer) >= 100

    def wake_up_player(self, reset_timer: bool = True, update_world_flag: bool = True) -> None:
        world = getattr(self, "world", None) or Entity.world
        bed = self.bed_location
        if world is not None and bed is not None:
            bx, by, bz = (int(value) for value in bed)
            if world.get_block_type(bx, by, bz) == "bed":
                from game.block.bed import bed_set_occupied, bed_exit_position
                meta = int(world.get_metadata(bx, by, bz)) & 15
                bed_set_occupied(world, bx, by, bz, False)
                self.pos[:] = bed_exit_position(world, bx, by, bz, meta)
                self._target_pos = list(self.pos)
                self.prev_pos[:] = self.pos
        self.is_sleeping = False
        self.sleep_timer = 0 if reset_timer else 100
        self.bed_location = None
        self._bed_sleep_meta = None
        self.size = (0.6, 1.8, 0.6)
        if self._pre_sleep_yaw is not None:
            self.yaw = self.prev_yaw = self._target_yaw = float(self._pre_sleep_yaw)
        if self._pre_sleep_pitch is not None:
            self.pitch = self.prev_pitch = self._target_pitch = float(self._pre_sleep_pitch)
        if self._pre_sleep_body_yaw is not None:
            self.render_yaw_offset = self.prev_render_yaw_offset = float(
                self._pre_sleep_body_yaw
            )
            self._target_body_yaw = self.render_yaw_offset
        self._pre_sleep_yaw = self._pre_sleep_pitch = self._pre_sleep_body_yaw = None
        self._sleep_tick_accum = 0.0
        self._animation_tick_accum = 0.0
        self._animation_distance_accum = 0.0
        self.vel[:] = [0.0, 0.0, 0.0]
        self.prev_limb_swing_amount = self.limb_swing_amount = 0.0
        self.prev_limb_swing = self.limb_swing = 0.0
        self._target_limb_swing_amount = self._target_limb_swing = 0.0
        self._is_swing_in_progress = False
        self._swing_progress_int = 0
        self.prev_swing_progress = self.swing_progress = 0.0
        if update_world_flag and world is not None:
            updater = getattr(world, "update_all_players_sleeping_flag", None)
            if callable(updater):
                updater()

    def get_interaction_bounds(self):
        width, height, depth = self.size
        return (
            float(self.pos[0]) - float(width) * 0.5,
            float(self.pos[1]),
            float(self.pos[2]) - float(depth) * 0.5,
            float(self.pos[0]) + float(width) * 0.5,
            float(self.pos[1]) + float(height),
            float(self.pos[2]) + float(depth) * 0.5,
        )

    def request_pvp_attack(self, damage: int) -> bool:
        callback = self.pvp_attack_callback
        if not callable(callback) or self.health <= 0:
            return False
        try:
            return bool(callback(self, max(1, min(10, int(damage)))))
        except (TypeError, ValueError, OverflowError):
            return False

    def on_zero_damage_projectile_hit(self, attacker=None) -> bool:
        if self.health <= 0 or self.hurt_time > self.max_hurt_time / 2:
            return False
        self.hurt_time = self.max_hurt_time = 10
        self.limb_swing_amount = 1.5
        if attacker is not None and hasattr(attacker, "pos"):
            dx = float(attacker.pos[0]) - float(self.pos[0])
            dz = float(attacker.pos[2]) - float(self.pos[2])
            if dx * dx + dz * dz < 1.0e-4:
                dx, dz = 0.01, -0.01
            self.attacked_at_yaw = math.degrees(math.atan2(dz, dx)) - self.yaw
            length = math.sqrt(dx * dx + dz * dz) or 1.0
            self.vel[0] = self.vel[0] * 0.5 - dx / length * 0.4
            self.vel[1] = min(0.4, self.vel[1] * 0.5 + 0.4)
            self.vel[2] = self.vel[2] * 0.5 - dz / length * 0.4
        return True

    def on_fishing_hook_hit(self, attacker=None) -> bool:
        return self.on_zero_damage_projectile_hit(attacker)

    def attack_entity_from(self, attacker=None, amount=1, *, knockback=None, source=None) -> bool:
        try:
            amount = int(amount)
        except (TypeError, ValueError, OverflowError):
            return False
        if amount <= 0:
            return self.on_zero_damage_projectile_hit(attacker)
        amount = min(20, amount)
        callback = self.damage_callback
        if not callable(callback) or self.health <= 0 or self.hurt_time > 0:
            return False
        self.hurt_time = self.max_hurt_time
        try:
            callback(amount, attacker, knockback=knockback, source=source)
        except TypeError:
            callback(amount, attacker)
        return True

    @property
    def death_animation_finished(self) -> bool:
        return self.health <= 0 and self.death_time > 20.0

    @property
    def should_render(self) -> bool:
        return not self.death_animation_finished

    def apply_network_pos(self, x: float, y: float, z: float, yaw: float, pitch: float) -> None:
        self._target_pos = [float(x), float(y), float(z)]
        self._target_yaw = float(yaw)
        self._target_pitch = float(pitch)

    def tick_interpolate(self, dt: float) -> None:
        dt = min(0.25, max(0.0, float(dt)))
        if self.is_sleeping:
            self.prev_pos[:] = self.pos
            self.pos[:] = self._target_pos
            self.prev_yaw = self.yaw = self._target_yaw
            self.prev_pitch = self.pitch = self._target_pitch
            self.prev_render_yaw_offset = self.render_yaw_offset
            self.render_yaw_offset = self._target_body_yaw
            advance_cape_motion(self, dt)
            self._sleep_tick_accum += dt
            ticks = min(int(self._sleep_tick_accum / 0.05), 4)
            if ticks > 0:
                self._sleep_tick_accum -= ticks * 0.05
                self.ticks_existed += ticks
                self.sleep_timer = min(100, int(self.sleep_timer) + ticks)
            self._animation_tick_accum = 0.0
            self._animation_distance_accum = 0.0
            self.prev_limb_swing_amount = self.limb_swing_amount = 0.0
            self.prev_limb_swing = self.limb_swing = 0.0
            self._target_limb_swing_amount = self._target_limb_swing = 0.0
            self._is_swing_in_progress = False
            self._swing_progress_int = 0
            self.prev_swing_progress = self.swing_progress = 0.0
            return

        self._sleep_tick_accum = 0.0
        old_x, old_z = float(self.pos[0]), float(self.pos[2])
        self.prev_pos[:] = self.pos
        a = min(1.0, dt * 12.0)
        for i in range(3):
            self.pos[i] += (self._target_pos[i] - self.pos[i]) * a
        advance_cape_motion(self, dt)
        moved = ((float(self.pos[0]) - old_x) ** 2
                 + (float(self.pos[2]) - old_z) ** 2) ** 0.5
        self._animation_distance_accum += moved

        dyaw = (self._target_yaw - self.yaw + 180.0) % 360.0 - 180.0
        self.prev_yaw = self.yaw
        self.yaw += dyaw * a
        self.prev_pitch = self.pitch
        self.pitch += (self._target_pitch - self.pitch) * a
        self.prev_render_yaw_offset = self.render_yaw_offset
        body_delta = (
            self._target_body_yaw - self.render_yaw_offset + 180.0
        ) % 360.0 - 180.0
        self.render_yaw_offset += body_delta * a

        self._animation_tick_accum += dt
        ticks = min(int(self._animation_tick_accum / 0.05), 4)
        if ticks <= 0:
            return
        period = (2.0 * math.pi) / 0.6662
        target_amount = max(0.0, min(1.0, float(self._target_limb_swing_amount)))
        for _ in range(ticks):
            self._animation_tick_accum -= 0.05
            self.prev_limb_swing_amount = self.limb_swing_amount
            self.prev_limb_swing = self.limb_swing
            self.limb_swing_amount += (target_amount - self.limb_swing_amount) * 0.4
            predicted = self.limb_swing + self.limb_swing_amount
            phase_error = (self._target_limb_swing - predicted + period * 0.5) % period - period * 0.5
            self.limb_swing = predicted + phase_error * 0.2
            self._update_arm_swing()
            self.ticks_existed += 1

    def apply_network_state(self, state) -> None:
        from game.multiplayer import protocol as P

        normalized = P.normalize_player_effect_state(state)
        was_sleeping = bool(self.is_sleeping)
        is_sleeping = bool(normalized.get("sleeping", False))
        if is_sleeping and not was_sleeping:
            self._pre_sleep_yaw = float(normalized.get("pre_sleep_yaw", self.yaw))
            self._pre_sleep_pitch = float(normalized.get("pre_sleep_pitch", self.pitch))
            self._pre_sleep_body_yaw = float(
                normalized.get("pre_sleep_body_yaw", normalized.get("body_yaw", self.render_yaw_offset))
            )
            self.ticks_existed = int(normalized.get("ticks_existed", self.ticks_existed))
            self._sleep_tick_accum = 0.0
        self.is_sleeping = is_sleeping
        incoming_sleep_timer = int(normalized.get("sleep_timer", 0))
        self.sleep_timer = (
            max(int(self.sleep_timer), incoming_sleep_timer)
            if is_sleeping and was_sleeping else incoming_sleep_timer
        )
        raw_bed = normalized.get("bed_location")
        self.bed_location = tuple(raw_bed) if is_sleeping and raw_bed is not None else None
        self._bed_sleep_meta = int(normalized.get("bed_meta", 0)) if is_sleeping else None
        self._target_body_yaw = float(normalized["body_yaw"])
        if is_sleeping and self._bed_sleep_meta is not None:
            try:
                from game.block.bed import bed_sleep_body_yaw
                self._target_body_yaw = bed_sleep_body_yaw(self._bed_sleep_meta)
            except Exception:
                pass
        self._target_limb_swing = float(normalized.get("limb_swing", self._target_limb_swing))
        self._target_limb_swing_amount = float(
            normalized.get("limb_swing_amount", self._target_limb_swing_amount)
        )
        if is_sleeping:
            self.size = (0.2, 0.2, 0.2)
            self._target_limb_swing_amount = 0.0
            self._target_limb_swing = 0.0
            self._is_swing_in_progress = False
            self._swing_progress_int = 0
            self.prev_swing_progress = self.swing_progress = 0.0
            self.prev_limb_swing_amount = self.limb_swing_amount = 0.0
            self.prev_limb_swing = self.limb_swing = 0.0
            if not was_sleeping:
                self.pos[:] = self._target_pos
                self.prev_pos[:] = self.pos
                self.yaw = self.prev_yaw = self._target_yaw
                self.pitch = self.prev_pitch = self._target_pitch
                self.render_yaw_offset = self.prev_render_yaw_offset = self._target_body_yaw
                self._sleep_tick_accum = 0.0
                self._animation_tick_accum = 0.0
                self._animation_distance_accum = 0.0
        elif was_sleeping:
            self.size = (0.6, 1.8, 0.6)
            self.ticks_existed = int(normalized.get("ticks_existed", self.ticks_existed))
            self._sleep_tick_accum = 0.0
            self._animation_tick_accum = 0.0
            self._animation_distance_accum = 0.0
            self.bed_location = None
            self._bed_sleep_meta = None
            self.pos[:] = self._target_pos
            self.prev_pos[:] = self.pos
            self.yaw = self.prev_yaw = self._target_yaw
            self.pitch = self.prev_pitch = self._target_pitch
            self.render_yaw_offset = self.prev_render_yaw_offset = self._target_body_yaw
            self._pre_sleep_yaw = self._pre_sleep_pitch = self._pre_sleep_body_yaw = None
            self.prev_limb_swing_amount = self.limb_swing_amount = 0.0
            self.prev_limb_swing = self.limb_swing = 0.0
            self._is_swing_in_progress = False
            self._swing_progress_int = 0
            self.prev_swing_progress = self.swing_progress = 0.0
        self.health = normalized["health"]
        self.size = (
            (0.2, 0.2, 0.2)
            if self.health <= 0.0 or is_sleeping else (0.6, 1.8, 0.6)
        )
        self.hurt_time = normalized["hurt_time"]
        self.max_hurt_time = normalized["max_hurt_time"]
        self.death_time = normalized["death_time"]
        self._fire_ticks = normalized["fire_ticks"]
        self.attacked_at_yaw = normalized["attacked_at_yaw"]
        self.riding_entity = (
            _REMOTE_RIDE_POSE if normalized["riding"] and not is_sleeping else None
        )
        self.is_sneaking = bool(normalized.get("sneaking", False)) and not is_sleeping
        if is_sleeping:
            self.render_y_offset = 0.0
            self.y_size = 0.0
        else:
            target = 0.2 if self.is_sneaking else 0.0
            current = float(getattr(self, "render_y_offset", getattr(self, "y_size", 0.0)))
            blend = 1.0 - 0.4
            current += (target - current) * blend
            self.render_y_offset = current
            self.y_size = current

    def apply_inventory_state(self, state) -> None:
        from game.multiplayer import protocol as P

        normalized = P.normalize_inventory_state(state)
        for field, incoming in (("main", normalized["main"]), ("armor", normalized["armor"])):
            current = getattr(self.inventory, field)
            for index, stack_data in enumerate(incoming):
                if stack_data is None:
                    current[index] = None
                    continue
                try:
                    stack = RemoteItemStack(
                        str(stack_data["id"]),
                        int(stack_data["count"]),
                        int(stack_data["damage"]),
                    )
                except (KeyError, TypeError, ValueError, OverflowError):
                    stack = None
                current[index] = stack
        self.inventory.current_item = int(normalized["selected"])
        cursor = normalized.get("cursor")
        if cursor is None:
            self.cursor_stack = None
        else:
            self.cursor_stack = RemoteItemStack(
                cursor["id"], cursor["count"], cursor["damage"],
            )

    def apply_break_progress(self, event) -> None:
        if not bool(event.get("active", False)):
            self.break_target = None
            self.break_progress = 0.0
            return
        try:
            target = (int(event["x"]), int(event["y"]), int(event["z"]))
            progress = float(event.get("progress", 0.0))
        except (KeyError, TypeError, ValueError, OverflowError):
            self.break_target = None
            self.break_progress = 0.0
            return
        self.break_target = target
        self.break_progress = max(0.0, min(0.99, progress))

    def trigger_animation(self, action: str = "swing") -> None:
        if action == "swing" and not self.is_sleeping:
            self._swing_progress_int = -1
            self._is_swing_in_progress = True

    def _update_arm_swing(self) -> None:
        self.prev_swing_progress = self.swing_progress
        if self._is_swing_in_progress:
            self._swing_progress_int += 1
            if self._swing_progress_int >= 8:
                self._swing_progress_int = 0
                self._is_swing_in_progress = False
        else:
            self._swing_progress_int = 0
        self.swing_progress = self._swing_progress_int / 8.0

    def draw(self):
        pass
