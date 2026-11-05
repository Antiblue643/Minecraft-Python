from __future__ import annotations

from collections import deque

from game.world.chunk_indexing import find_chunk_block_indices

WIRE = "redstone_wire"
LEVER = "lever"
BUTTON = "stone_button"
PLATES = frozenset({"stone_pressure_plate", "wooden_pressure_plate"})
TORCH_ON = "redstone_torch"
TORCH_OFF = "unlit_redstone_torch"
DOORS = frozenset({"wooden_door", "iron_door"})
COMPONENTS = frozenset({WIRE, LEVER, BUTTON, *PLATES, TORCH_ON, TORCH_OFF, *DOORS})
TORCHES = frozenset({TORCH_ON, TORCH_OFF})

TORCH_TICK_RATE = 2
TORCH_BURNOUT_WINDOW = 100
TORCH_BURNOUT_THRESHOLD = 8

_DIRS = ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0), (0, -1, 0))
_HORIZONTAL = ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1))
_TORCH_SUPPORT = {
    1: (-1, 0, 0), 2: (1, 0, 0), 3: (0, 0, -1),
    4: (0, 0, 1), 5: (0, -1, 0),
}


def torch_support_position(world, pos):
    x, y, z = pos
    meta = int(world.get_metadata(x, y, z)) & 7
    dx, dy, dz = _TORCH_SUPPORT.get(meta, (0, -1, 0))
    return x + dx, y + dy, z + dz


def torch_powers_position(world, torch_pos, target_pos) -> bool:
    tx, ty, tz = (int(v) for v in torch_pos)
    x, y, z = (int(v) for v in target_pos)
    if abs(x - tx) + abs(y - ty) + abs(z - tz) != 1:
        return False
    if torch_support_position(world, (tx, ty, tz)) == (x, y, z):
        return False
    return True


class RedstoneSystem:
    def __init__(self, world):
        self.world = world
        self.components: dict[tuple[int, int, int], str] = {}
        self.dirty = False
        self._updating = False
        self.door_power_state: dict[tuple[int, int, int], bool] = {}
        self.button_timers: dict[tuple[int, int, int], int] = {}
        self.torch_schedule: dict[tuple[int, int, int], int] = {}
        self.torch_updates: list[tuple[int, int, int, int]] = []

    def _play_sound(self, name, x, y, z, *, volume=1.0, pitch=1.0):
        player = getattr(self.world, "sound_player", None)
        if callable(player):
            try:
                player(name, volume=volume, pitch=pitch,
                       position=(float(x), float(y), float(z)))
            except TypeError:
                try:
                    player(name, volume, pitch, (float(x), float(y), float(z)))
                except Exception:
                    pass
            except Exception:
                pass

    def _world_time(self) -> int:
        return int(getattr(self.world, "world_time", 0) or 0)

    def _schedule_torch(self, pos):
        pos = (int(pos[0]), int(pos[1]), int(pos[2]))
        current = self.world.get_block_type(*pos)
        if current not in TORCHES:
            self.torch_schedule.pop(pos, None)
            return
        due = self._world_time() + TORCH_TICK_RATE
        existing = self.torch_schedule.get(pos)
        if existing is None or due < existing:
            self.torch_schedule[pos] = due
        schedule = getattr(self.world, "schedule_block_update", None)
        if callable(schedule):
            try:
                schedule(pos[0], pos[1], pos[2], current, TORCH_TICK_RATE)
            except Exception:
                pass

    def _check_torch_burnout(self, pos, record: bool) -> bool:
        world_time = self._world_time()
        while self.torch_updates and world_time - self.torch_updates[0][3] > TORCH_BURNOUT_WINDOW:
            self.torch_updates.pop(0)
        if record:
            self.torch_updates.append((pos[0], pos[1], pos[2], world_time))
        count = 0
        for ux, uy, uz, _t in self.torch_updates:
            if ux == pos[0] and uy == pos[1] and uz == pos[2]:
                count += 1
                if count >= TORCH_BURNOUT_THRESHOLD:
                    return True
        return False

    def _torch_support_powered(self, pos, wire_power) -> bool:
        support = torch_support_position(self.world, pos)
        return self._block_receives_power(support, wire_power, ignore_source=pos)

    def apply_torch_update(self, x, y, z):
        pos = (int(x), int(y), int(z))
        self.torch_schedule.pop(pos, None)
        world = self.world
        current = world.get_block_type(*pos)
        if current not in TORCHES:
            return False

        wire_positions = {
            p for p, typ in self.components.items()
            if typ == WIRE and world.get_block_type(*p) == WIRE
        }
        tx, ty, tz = pos
        for dx, dy, dz in _DIRS:
            npos = (tx + dx, ty + dy, tz + dz)
            if world.get_block_type(*npos) == WIRE:
                wire_positions.add(npos)
        wire_power = self._solve_wire_power(wire_positions)

        support_powered = self._torch_support_powered(pos, wire_power)
        active = current == TORCH_ON
        changed = False
        if active:
            if support_powered:
                burned = self._check_torch_burnout(pos, record=True)
                world.set_block(*pos, TORCH_OFF, meta=world.get_metadata(*pos) & 7)
                if burned:
                    self._play_sound(
                        "random.fizz", pos[0], pos[1], pos[2],
                        volume=0.5, pitch=2.6,
                    )
                changed = True
        else:
            if not support_powered and not self._check_torch_burnout(pos, record=False):
                world.set_block(*pos, TORCH_ON, meta=world.get_metadata(*pos) & 7)
                changed = True

        if changed:
            self.dirty = True
            if not self._updating:
                self.recompute()
        return changed

    def on_chunk_unloaded(self, cx, cz, chunk_size=16):
        min_x = int(cx) * int(chunk_size)
        max_x = min_x + int(chunk_size)
        min_z = int(cz) * int(chunk_size)
        max_z = min_z + int(chunk_size)
        in_chunk = lambda pos: min_x <= pos[0] < max_x and min_z <= pos[2] < max_z
        self.components = {pos: typ for pos, typ in self.components.items() if not in_chunk(pos)}
        self.door_power_state = {
            pos: state for pos, state in self.door_power_state.items() if not in_chunk(pos)
        }
        self.button_timers = {
            pos: ticks for pos, ticks in self.button_timers.items() if not in_chunk(pos)
        }
        self.torch_schedule = {
            pos: due for pos, due in self.torch_schedule.items() if not in_chunk(pos)
        }

    def on_chunk_loaded(self, chunk):
        min_x, max_x = int(chunk.start_x), int(chunk.start_x + chunk.width)
        min_z, max_z = int(chunk.start_z), int(chunk.start_z + chunk.depth)
        old_count = len(self.components)
        in_chunk = lambda pos: min_x <= pos[0] < max_x and min_z <= pos[2] < max_z
        self.components = {pos: typ for pos, typ in self.components.items() if not in_chunk(pos)}
        self.door_power_state = {
            pos: state for pos, state in self.door_power_state.items() if not in_chunk(pos)
        }
        self.button_timers = {pos: ticks for pos, ticks in self.button_timers.items() if not in_chunk(pos)}
        self.torch_schedule = {
            pos: due for pos, due in self.torch_schedule.items() if not in_chunk(pos)
        }
        if len(self.components) != old_count:
            self.dirty = True
        blocks = chunk.blocks
        id_to_type = getattr(chunk, "_id_to_block_type", ())
        height, depth, width = int(chunk.height), int(chunk.depth), int(chunk.width)
        for raw_index in find_chunk_block_indices(chunk, COMPONENTS):
            index = int(raw_index)
            internal_id = int(blocks[index])
            if internal_id >= len(id_to_type):
                continue
            block_type = id_to_type[internal_id]
            y = index % height
            column = index // height
            lz = column % depth
            lx = column // depth
            pos = (int(chunk.start_x + lx), int(y), int(chunk.start_z + lz))
            self.components[pos] = block_type
            if block_type == BUTTON and (int(chunk.metadata[index]) & 8):
                self.button_timers[pos] = 20
            self.dirty = True

    def on_block_changed(self, x, y, z, old_type, new_type):
        pos = (int(x), int(y), int(z))
        old_component = old_type in COMPONENTS
        new_component = new_type in COMPONENTS
        if old_component and not new_component:
            self.components.pop(pos, None)
            self.torch_schedule.pop(pos, None)
        elif new_component:
            self.components[pos] = new_type
        if old_type in DOORS and new_type != old_type:
            self.door_power_state.pop(pos, None)
        if new_type in TORCHES or old_type in TORCHES:
            if new_type in TORCHES:
                self._schedule_torch(pos)
            else:
                self.torch_schedule.pop(pos, None)
        for dx, dy, dz in _DIRS:
            npos = (pos[0] + dx, pos[1] + dy, pos[2] + dz)
            ntype = self.components.get(npos)
            if ntype in TORCHES or self.world.get_block_type(*npos) in TORCHES:
                self._schedule_torch(npos)
        touches_component = old_component or new_component or any(
            (pos[0] + dx, pos[1] + dy, pos[2] + dz) in self.components
            for dx, dy, dz in _DIRS
        )
        if touches_component and not self._updating:
            self.dirty = True

    def toggle_lever(self, x, y, z):
        world = self.world
        if world.get_block_type(x, y, z) != LEVER:
            return False
        meta = int(world.get_metadata(x, y, z)) & 15
        world.set_block(x, y, z, LEVER, meta=(meta ^ 8), immediate_mesh=True)
        self._play_sound("random.click", x, y, z, volume=0.3,
                         pitch=0.6 if not (meta & 8) else 0.5)
        self.dirty = True
        self.recompute()
        return True

    def press_button(self, x, y, z):
        world = self.world
        if world.get_block_type(x, y, z) != BUTTON:
            return False
        meta = int(world.get_metadata(x, y, z)) & 15
        if meta & 8:
            return True
        world.set_block(x, y, z, BUTTON, meta=(meta & 7) | 8, immediate_mesh=True)
        self.button_timers[(int(x), int(y), int(z))] = 20
        self._play_sound("random.click", x, y, z, volume=0.3, pitch=0.6)
        self.dirty = True
        self.recompute()
        return True

    toggle_button = press_button

    def schedule_button_release(self, x, y, z, ticks=20):
        pos = (int(x), int(y), int(z))
        if self.world.get_block_type(*pos) != BUTTON:
            return False
        if not (int(self.world.get_metadata(*pos)) & 8):
            return False
        self.button_timers[pos] = max(1, int(ticks))
        return True

    def _tick_buttons(self):
        changed = False
        for pos, ticks in list(self.button_timers.items()):
            x, y, z = pos
            if self.world.get_block_type(x, y, z) != BUTTON:
                self.button_timers.pop(pos, None)
                continue
            if not (int(self.world.get_metadata(x, y, z)) & 8):
                self.button_timers.pop(pos, None)
                continue
            ticks -= 1
            if ticks > 0:
                self.button_timers[pos] = ticks
                continue
            self.button_timers.pop(pos, None)
            meta = int(self.world.get_metadata(x, y, z)) & 7
            self.world.set_block(x, y, z, BUTTON, meta=meta, immediate_mesh=True)
            self._play_sound("random.click", x, y, z, volume=0.3, pitch=0.5)
            changed = True
        return changed

    def _tick_torches(self):
        world_time = self._world_time()
        due = [pos for pos, when in self.torch_schedule.items() if when <= world_time]
        if not due:
            return False
        changed = False
        for pos in due:
            if self.apply_torch_update(*pos):
                changed = True
        return changed

    def _reschedule_mismatched_torches(self):
        world = self.world
        wire_positions = {
            pos for pos, typ in self.components.items()
            if typ == WIRE and world.get_block_type(*pos) == WIRE
        }
        if not wire_positions and not any(
            typ in TORCHES for typ in self.components.values()
        ):
            return
        wire_power = self._solve_wire_power(wire_positions)
        for pos, tracked in list(self.components.items()):
            if tracked not in TORCHES:
                continue
            current = world.get_block_type(*pos)
            if current not in TORCHES:
                continue
            powered = self._torch_support_powered(pos, wire_power)
            desired = TORCH_OFF if powered else TORCH_ON
            if desired != current:
                self._schedule_torch(pos)

    def tick(self, player_pos=None):
        changed_button = self._tick_buttons()
        changed_plate = self._update_pressure_plates(player_pos)
        changed_wire = self._update_wire_collisions(player_pos)
        changed_torch = self._tick_torches()
        if changed_plate or changed_button or changed_wire or changed_torch:
            self.dirty = True
        if self.dirty:
            self.recompute()
        self._reschedule_mismatched_torches()

    def _is_source_active(self, pos, block_type=None):
        world = self.world
        x, y, z = pos
        block_type = block_type or world.get_block_type(x, y, z)
        if block_type == LEVER:
            return bool(int(world.get_metadata(x, y, z)) & 8)
        if block_type == BUTTON:
            return bool(int(world.get_metadata(x, y, z)) & 8)
        if block_type in PLATES:
            return bool(int(world.get_metadata(x, y, z)) & 1)
        return block_type == TORCH_ON

    def _is_full_cube(self, x, y, z):
        if y < 0:
            return True
        if y >= getattr(self.world, "CHUNK_HEIGHT", 128):
            return False
        btype = self.world.get_block_type(x, y, z)
        if btype is None:
            return False
        try:
            from game.block.fire_rules import is_normal_cube
            from game.block import Block
            Block._load_defs()
            return is_normal_cube(Block._block_defs.get(btype, {}))
        except Exception:
            block = self.world.get_block(x, y, z)
            return bool(block and getattr(block, "collision", False)
                        and not getattr(block, "transparent", False))

    def _wire_neighbors(self, pos, wire_positions):
        x, y, z = pos
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            same = (x + dx, y, z + dz)
            if same in wire_positions:
                yield same
                continue
            higher = (x + dx, y + 1, z + dz)
            if (higher in wire_positions
                    and self._is_full_cube(x + dx, y, z + dz)
                    and not self._is_full_cube(x, y + 1, z)):
                yield higher
            lower = (x + dx, y - 1, z + dz)
            if lower in wire_positions and not self._is_full_cube(x + dx, y, z + dz):
                yield lower

    def _source_can_power_wire(self, source_pos, wire_pos):
        world = self.world
        stype = world.get_block_type(*source_pos)
        if stype not in (LEVER, BUTTON, TORCH_ON, *PLATES):
            return False
        if not self._is_source_active(source_pos, stype):
            return False
        if stype == TORCH_ON:
            return torch_powers_position(world, source_pos, wire_pos)
        return True

    def _initial_wire_power(self, wire_pos):
        x, y, z = wire_pos
        world = self.world
        for dx, dy, dz in _DIRS:
            source_pos = (x + dx, y + dy, z + dz)
            if self._source_can_power_wire(source_pos, wire_pos):
                return 15
        for dx, dy, dz in _DIRS:
            cube_pos = (x + dx, y + dy, z + dz)
            if (self._is_full_cube(*cube_pos)
                    and self._block_receives_power(cube_pos, {})):
                return 15
        return 0

    def _solve_wire_power(self, wire_positions):
        power = {pos: 0 for pos in wire_positions}
        queue = deque()
        for pos in wire_positions:
            level = self._initial_wire_power(pos)
            if level:
                power[pos] = level
                queue.append(pos)
        while queue:
            pos = queue.popleft()
            next_level = power[pos] - 1
            if next_level <= 0:
                continue
            for neighbor in self._wire_neighbors(pos, wire_positions):
                if next_level > power[neighbor]:
                    power[neighbor] = next_level
                    queue.append(neighbor)
        return power

    def _block_receives_power(self, pos, wire_power, *, ignore_source=None):
        world = self.world
        x, y, z = pos
        for dx, dy, dz in _DIRS:
            source_pos = (x + dx, y + dy, z + dz)
            if source_pos == ignore_source:
                continue
            stype = world.get_block_type(*source_pos)
            if stype in (LEVER, BUTTON, *PLATES, TORCH_ON) and self._is_source_active(source_pos, stype):
                if stype == TORCH_ON and not torch_powers_position(world, source_pos, pos):
                    continue
                return True
            if stype == WIRE and wire_power.get(source_pos, 0) > 0:
                return True
        return False

    def _door_powered(self, door_pos, wire_power):
        world = self.world
        x, y, z = door_pos
        cells = (door_pos, (x, y + 1, z))
        for cell in cells:
            if self._block_receives_power(cell, wire_power):
                return True
            cx, cy, cz = cell
            for dx, dy, dz in _DIRS:
                neighbor = (cx + dx, cy + dy, cz + dz)
                if not self._is_full_cube(*neighbor):
                    continue
                if self._block_receives_power(neighbor, wire_power):
                    return True
        return False

    def _mark_block_mesh_dirty(self, x, y, z):
        world = self.world
        get_index = getattr(world, "_get_chunk_index", None)
        if not callable(get_index):
            return
        key = get_index(x, z)
        chunk = world.chunks.get(key) if hasattr(world, "chunks") else None
        if chunk is None:
            return
        mark = getattr(chunk, "mark_dirty_at", None)
        if callable(mark):
            mark(y)
        note = getattr(world, "note_chunk_mesh_dirty", None)
        if callable(note):
            note(key)

    def _set_wire_power_meta(self, pos, level):
        world = self.world
        x, y, z = pos
        if world.get_block_type(x, y, z) != WIRE:
            return False
        if int(world.get_metadata(x, y, z)) == int(level):
            return False
        get_index = getattr(world, "_get_chunk_index", None)
        chunks = getattr(world, "chunks", None)
        if callable(get_index) and chunks is not None:
            chunk = chunks.get(get_index(x, z))
            if chunk is not None and hasattr(chunk, "set_metadata"):
                chunk.set_metadata(x, y, z, level)
                self._mark_block_mesh_dirty(x, y, z)
                return True
        world.set_block(x, y, z, WIRE, meta=level)
        return True

    def _set_door_open_meta(self, pos, block_type, open_state):
        world = self.world
        x, y, z = pos
        if world.get_block_type(x, y, z) != block_type:
            return False
        meta = int(world.get_metadata(x, y, z)) & 15
        new_meta = (meta | 4) if open_state else (meta & ~4)
        if new_meta == meta:
            return False
        get_index = getattr(world, "_get_chunk_index", None)
        chunks = getattr(world, "chunks", None)
        if callable(get_index) and chunks is not None:
            chunk = chunks.get(get_index(x, z))
            if chunk is not None and hasattr(chunk, "set_metadata"):
                chunk.set_metadata(x, y, z, new_meta)
                self._mark_block_mesh_dirty(x, y, z)
                upper = (x, y + 1, z)
                if world.get_block_type(*upper) == block_type:
                    upper_meta = int(world.get_metadata(*upper)) & 15
                    upper_meta = (upper_meta | 4) if open_state else (upper_meta & ~4)
                    upper_meta |= 8
                    upper_chunk = chunks.get(get_index(x, z))
                    if upper_chunk is not None:
                        upper_chunk.set_metadata(*upper, upper_meta)
                        self._mark_block_mesh_dirty(*upper)
                return True
        world.set_block(x, y, z, block_type, meta=new_meta)
        if world.get_block_type(x, y + 1, z) == block_type:
            upper_meta = int(world.get_metadata(x, y + 1, z)) & 15
            upper_meta = (upper_meta | 4) if open_state else (upper_meta & ~4)
            upper_meta |= 8
            world.set_block(x, y + 1, z, block_type, meta=upper_meta)
        return True

    def recompute(self):
        if self._updating:
            self.dirty = True
            return
        self._updating = True
        self.dirty = False
        world = self.world
        try:
            for _iteration in range(8):
                changed = False
                wire_positions = {
                    pos for pos, typ in self.components.items()
                    if typ == WIRE and world.get_block_type(*pos) == WIRE
                }
                wire_power = self._solve_wire_power(wire_positions)
                for pos, level in wire_power.items():
                    if self._set_wire_power_meta(pos, level):
                        changed = True

                for pos, tracked_type in list(self.components.items()):
                    if tracked_type not in TORCHES:
                        continue
                    current_type = world.get_block_type(*pos)
                    if current_type not in TORCHES:
                        continue
                    powered = self._torch_support_powered(pos, wire_power)
                    desired = TORCH_OFF if powered else TORCH_ON
                    if desired != current_type:
                        self._schedule_torch(pos)

                for pos, tracked_type in list(self.components.items()):
                    if tracked_type not in DOORS or world.get_block_type(*pos) not in DOORS:
                        continue
                    meta = int(world.get_metadata(*pos)) & 15
                    if meta & 8:
                        continue
                    powered = self._door_powered(pos, wire_power)
                    currently_open = bool(meta & 4)
                    was_powered = self.door_power_state.get(pos, False)
                    self.door_power_state[pos] = powered
                    if tracked_type == "iron_door":
                        should_be_open = powered
                    elif powered:
                        should_be_open = True
                    elif was_powered:
                        should_be_open = False
                    else:
                        should_be_open = currently_open
                    if should_be_open != currently_open:
                        if self._set_door_open_meta(pos, tracked_type, should_be_open):
                            self._play_sound(
                                "random.door_open" if should_be_open else "random.door_close",
                                pos[0], pos[1], pos[2], volume=1.0, pitch=1.0,
                            )
                            changed = True
                if not changed:
                    break
            self.dirty = False
            self._refresh_rail_junctions_from_power()
        finally:
            self._updating = False

    def _refresh_rail_junctions_from_power(self):
        try:
            from game.block.placement import refresh_powered_rail_junctions
        except Exception:
            return
        seen = set()
        for pos, typ in list(self.components.items()):
            if typ not in (WIRE, LEVER, BUTTON, TORCH_ON, TORCH_OFF, *PLATES):
                continue
            key = (pos[0] >> 2, pos[2] >> 2)  
            if key in seen:
                continue
            seen.add(key)
            try:
                refresh_powered_rail_junctions(self.world, *pos)
            except Exception:
                continue

    def _update_pressure_plates(self, player_pos=None):
        world = self.world
        entities = []
        if player_pos is not None:
            try:
                entities.append((tuple(float(v) for v in player_pos), (0.6, 1.8, 0.6), "player"))
            except (TypeError, ValueError):
                pass
        for entity in getattr(world, "entities", ()):
            if getattr(entity, "dead", False) or not hasattr(entity, "pos"):
                continue
            is_item = hasattr(entity, "item")
            if not is_item and not (getattr(entity, "is_living_entity", False)
                                    or getattr(entity, "prevent_entity_spawning", False)):
                continue
            try:
                pos = tuple(float(v) for v in entity.pos)
                size = tuple(float(v) for v in getattr(entity, "size", (0.6, 1.8, 0.6)))
                if len(pos) != 3 or len(size) != 3:
                    continue
                entities.append((pos, size, "item" if is_item else "mob"))
            except (TypeError, ValueError):
                continue

        changed = False
        for (x, y, z), block_type in list(self.components.items()):
            if block_type not in PLATES or world.get_block_type(x, y, z) != block_type:
                continue
            plate_top = y - 0.5 + (0.03125 if (int(world.get_metadata(x, y, z)) & 1) else 0.0625)
            occupied = False
            for (ex, ey, ez), size, entity_kind in entities:
                if entity_kind == "item" and block_type != "wooden_pressure_plate":
                    continue
                half_width = max(0.0, size[0] * 0.5)
                half_depth = max(0.0, size[2] * 0.5)
                if ex + half_width <= x - 0.375 or ex - half_width >= x + 0.375:
                    continue
                if ez + half_depth <= z - 0.375 or ez - half_depth >= z + 0.375:
                    continue
                if ey > plate_top + 0.20 or ey + size[1] < y - 0.5:
                    continue
                occupied = True
                break

            meta = int(world.get_metadata(x, y, z)) & 1
            want = 1 if occupied else 0
            if meta != want:
                world.set_block(x, y, z, block_type, meta=want)
                self._play_sound("random.click", x, y, z, volume=0.3,
                                 pitch=0.6 if want else 0.5)
                changed = True
        return changed

    def _update_wire_collisions(self, player_pos=None):
        return False
