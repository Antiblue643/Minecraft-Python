from __future__ import annotations

from game.content_ids import legacy_entity_id

import random

from game.entities.item import Item, ItemStack


_BURN_TIMES = {
    "planks": 300,
    "log": 300,
    "stick": 100,
    "sapling": 100,
    "coal": 1600,
    "wooden_door": 200,
    "crafting_table": 300,
    "chest": 300,
    "bookshelf": 300,
    "stairs": 300,
    "fence": 300,
    "ladder": 300,
    "sign": 200,
    "wooden_pressure_plate": 300,
    "trapdoor": 300,
    "jukebox": 300,
    "note_block": 300,
    "wooden_slab": 150,
}


def get_item_burn_time(item_id):
    if item_id is None:
        return 0
    if item_id in _BURN_TIMES:
        return _BURN_TIMES[item_id]
    defn = Item.definition(item_id)
    if defn.get("material") == "wood" and defn.get("type") in ("tool", "melee_weapon"):
        return 200
    if item_id.endswith("_planks") or item_id == "planks":
        return 300
    return 0


class TileEntity:
    def __init__(self, world, x, y, z):
        self.world = world
        self.x = int(x)
        self.y = int(y)
        self.z = int(z)

    def update(self):
        pass

    def on_removed(self):
        pass

    def to_dict(self):
        return {"x": self.x, "y": self.y, "z": self.z}

    @classmethod
    def from_dict(cls, world, data):
        raise NotImplementedError


class TileEntityChest(TileEntity):

    SIZE = 27
    stack_limit = 64

    def __init__(self, world, x, y, z):
        super().__init__(world, x, y, z)
        self.contents = [None] * self.SIZE

    def get_stack(self, slot):
        if 0 <= slot < self.SIZE:
            return self.contents[slot]
        return None

    def set_stack(self, slot, stack):
        if 0 <= slot < self.SIZE:
            self.contents[slot] = stack

    def on_removed(self):
        if self.world is None:
            return
        if not bool(getattr(self.world, "_multiplayer_client_world", False)):
            for stack in self.contents:
                if stack is not None and stack.count > 0:
                    self.world.spawn_item_stack(self.x, self.y, self.z, stack)
        self.contents = [None] * self.SIZE

    def to_dict(self):
        items = []
        for i, stack in enumerate(self.contents):
            if stack is not None and stack.count > 0:
                items.append({"slot": i, "id": stack.id, "count": stack.count, "damage": stack.damage})
        return {"type": "chest", "x": self.x, "y": self.y, "z": self.z, "items": items}

    @classmethod
    def from_dict(cls, world, data):
        te = cls(world, data["x"], data["y"], data["z"])
        for entry in data.get("items", []):
            slot = int(entry.get("slot", 0))
            if 0 <= slot < cls.SIZE:
                te.contents[slot] = ItemStack(entry["id"], int(entry.get("count", 1)), int(entry.get("damage", 0)))
        return te


class TileEntityDispenser(TileEntity):

    SIZE = 9
    stack_limit = 64
    TICK_DELAY = 4
    _DIRECTIONS = {
        2: (0, 0, -1), 3: (0, 0, 1),
        4: (-1, 0, 0), 5: (1, 0, 0),
    }

    def __init__(self, world, x, y, z):
        super().__init__(world, x, y, z)
        self.contents = [None] * self.SIZE
        self._was_powered = False
        self._scheduled_tick = None
        self._pending_ticks = []
        self._power_signature_cache = None

    def get_stack(self, slot):
        return self.contents[slot] if 0 <= int(slot) < self.SIZE else None

    def set_stack(self, slot, stack):
        if 0 <= int(slot) < self.SIZE:
            self.contents[int(slot)] = stack

    def get_inv_name(self):
        return "Dispenser"

    def shift_world_time(self, delta):
        self._pending_ticks = [due + int(delta) for due in self._pending_ticks]

    def can_interact_with(self, player):
        if self.world is None or self.world.get_tile_entity(self.x, self.y, self.z) is not self:
            return False
        dx = float(player.pos[0]) - (self.x + 0.5)
        dy = float(player.pos[1]) - (self.y + 0.5)
        dz = float(player.pos[2]) - (self.z + 0.5)
        return dx * dx + dy * dy + dz * dz <= 64.0

    def on_removed(self):
        if self.world is not None and not bool(getattr(self.world, "_multiplayer_client_world", False)):
            for stack in self.contents:
                if stack is not None and stack.count > 0:
                    self.world.spawn_item_stack(self.x, self.y, self.z, stack)
        self.contents = [None] * self.SIZE

    def _rng(self):
        return getattr(self.world, "_tick_rng", None) or random

    def _play_dispense_click(self, pitch: float) -> None:
        player = getattr(self.world, "sound_player", None)
        if callable(player):
            try:
                player(
                    "random.click", volume=1.0, pitch=float(pitch),
                    position=(self.x + 0.5, self.y + 0.5, self.z + 0.5),
                )
            except Exception:
                pass

    def dispense_one(self):
        if self.world is None or bool(getattr(self.world, "_multiplayer_client_world", False)):
            return False
        occupied = [i for i, stack in enumerate(self.contents)
                    if stack is not None and int(getattr(stack, "count", 0)) > 0]
        if not occupied:
            self._play_dispense_click(1.2)
            return False

        rng = self._rng()
        slot = rng.choice(occupied)
        stack = self.contents[slot]
        emitted = ItemStack(stack.id, 1, int(getattr(stack, "damage", 0)))
        stack.count -= 1
        if stack.count <= 0:
            self.contents[slot] = None

        meta = int(self.world.get_metadata(self.x, self.y, self.z)) & 7
        dx, dy, dz = self._DIRECTIONS.get(meta, (0, 0, 1))
        face_offset = 0.51
        spawn_pos = (
            self.x + dx * face_offset,
            float(self.y),
            self.z + dz * face_offset,
        )
        gaussian = getattr(rng, "gauss", lambda _mean, _stddev: 0.0)
        projectile = emitted.id in {"arrow", "egg", "snowball"}
        try:
            if projectile:
                norm = (dx * dx + 0.1 * 0.1 + dz * dz) ** 0.5
                heading = (
                    dx / norm + gaussian(0.0, 0.045),
                    0.1 / norm + gaussian(0.0, 0.045),
                    dz / norm + gaussian(0.0, 0.045),
                )
                velocity = tuple(component * 1.1 for component in heading)
                if emitted.id == "arrow":
                    from game.entities.arrow import ArrowEntity
                    entity = ArrowEntity(self.world, spawn_pos, velocity=velocity)
                    sound, pitch = "random.bow", 1.2
                elif emitted.id == "egg":
                    from game.entities.egg import EggEntity
                    entity = EggEntity(self.world, spawn_pos, velocity=velocity)
                    sound, pitch = "random.bow", 1.2
                else:
                    from game.entities.snowball import SnowballEntity
                    entity = SnowballEntity(self.world, spawn_pos, velocity=velocity)
                    sound, pitch = "random.bow", 1.2
            else:
                from game.entities.item import ItemEntity
                speed = rng.random() * 0.1 + 0.2
                velocity = (
                    dx * speed + gaussian(0.0, 0.045),
                    0.2 + gaussian(0.0, 0.045),
                    dz * speed + gaussian(0.0, 0.045),
                )
                item_pos = (spawn_pos[0], spawn_pos[1] - 0.3, spawn_pos[2])
                entity = ItemEntity(emitted, item_pos, velocity=velocity, pickup_delay=10)
                sound, pitch = "random.click", 1.0
            self.world.spawn_entity(entity)
        except Exception:
            stack.count += 1
            self.contents[slot] = stack
            return False

        self._play_dispense_click(pitch) if sound == "random.click" else self._play_projectile_sound(sound, pitch)
        particle = getattr(self.world, "spawn_particle", None)
        if callable(particle):
            for _ in range(10):
                spread = rng.random() * 0.2 + 0.01
                px = spawn_pos[0] + dx * 0.01 + (rng.random() - 0.5) * dz * 0.5
                py = spawn_pos[1] + (rng.random() - 0.5) * 0.5
                pz = spawn_pos[2] + dz * 0.01 + (rng.random() - 0.5) * dx * 0.5
                particle(
                    "smoke", px, py, pz,
                    dx * spread + gaussian(0.0, 0.01),
                    -0.03 + gaussian(0.0, 0.01),
                    dz * spread + gaussian(0.0, 0.01),
                )
        return True

    def _play_projectile_sound(self, name: str, pitch: float) -> None:
        player = getattr(self.world, "sound_player", None)
        if callable(player):
            try:
                player(name, volume=1.0, pitch=float(pitch),
                       position=(self.x, self.y, self.z))
            except Exception:
                pass

    def update(self):
        if self.world is None or bool(getattr(self.world, "_multiplayer_client_world", False)):
            return False
        redstone = getattr(self.world, "_redstone_system", None)
        if redstone is None:
            return False
        powered = redstone.is_block_powered(self.x, self.y, self.z) or redstone.is_block_powered(
            self.x, self.y + 1, self.z,
        )
        now = int(getattr(self.world, "world_time", 0) or 0)
        pending = self._pending_ticks

        if powered:
            signature = self._power_signature()
            if not self._was_powered or signature != self._power_signature_cache:
                due = now + self.TICK_DELAY
                if due not in pending:
                    pending.append(due)
                    pending.sort()
            self._was_powered = True
            self._power_signature_cache = signature
        else:
            self._was_powered = False
            self._power_signature_cache = None

        fired = False
        while pending and pending[0] <= now:
            pending.pop(0)
            if powered and self.dispense_one():
                fired = True
        return fired

    def _power_signature(self):
        world = self.world
        get_type = getattr(world, "get_block_type", None)
        get_meta = getattr(world, "get_metadata", None)
        if not callable(get_type):
            return ()
        from game.block.redstone import COMPONENTS, DOORS
        providers = COMPONENTS - DOORS
        seen = set()
        signature = []
        for base_y in (self.y, self.y + 1):
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    reach = 2 - abs(dx) - abs(dy)
                    for dz in range(-reach, reach + 1):
                        pos = (self.x + dx, base_y + dy, self.z + dz)
                        if pos in seen or pos == (self.x, self.y, self.z):
                            continue
                        seen.add(pos)
                        block_type = get_type(*pos)
                        if block_type in providers:
                            meta = int(get_meta(*pos)) if callable(get_meta) else 0
                            signature.append((pos, block_type, meta))
        return tuple(sorted(signature))

    def to_dict(self):
        items = []
        for i, stack in enumerate(self.contents):
            if stack is not None and stack.count > 0:
                items.append({"slot": i, "id": stack.id, "count": stack.count,
                              "damage": stack.damage})
        return {"type": "dispenser", "x": self.x, "y": self.y, "z": self.z,
                "items": items}

    @classmethod
    def from_dict(cls, world, data):
        te = cls(world, data["x"], data["y"], data["z"])
        for entry in data.get("items", []):
            slot = int(entry.get("slot", -1))
            if 0 <= slot < cls.SIZE:
                te.contents[slot] = ItemStack(entry["id"],
                    int(entry.get("count", 1)), int(entry.get("damage", 0)))
        return te


class TileEntityFurnace(TileEntity):

    SLOT_INPUT = 0
    SLOT_FUEL = 1
    SLOT_OUTPUT = 2
    COOK_TIME_TOTAL = 200
    stack_limit = 64

    def __init__(self, world, x, y, z):
        super().__init__(world, x, y, z)
        self.furnace_item_stacks = [None, None, None]
        self.furnace_burn_time = 0
        self.current_item_burn_time = 0
        self.furnace_cook_time = 0

    def get_stack(self, slot):
        if 0 <= slot < 3:
            return self.furnace_item_stacks[slot]
        return None

    def set_stack(self, slot, stack):
        if 0 <= slot < 3:
            self.furnace_item_stacks[slot] = stack

    def is_burning(self):
        return self.furnace_burn_time > 0

    def get_cook_progress_scaled(self, pixels):
        return self.furnace_cook_time * pixels // self.COOK_TIME_TOTAL

    def get_burn_time_remaining_scaled(self, pixels):
        total = self.current_item_burn_time or 200
        return self.furnace_burn_time * pixels // total

    def _smelt_result_id(self, item_id):
        from game.gameplay.crafting import CraftingManager
        mgr = CraftingManager.get_instance()
        for recipe in mgr.furnace_recipes:
            if isinstance(recipe, tuple):
                if recipe[0] == item_id:
                    return recipe[1], recipe[2] if len(recipe) > 2 else 1
            elif getattr(recipe, "input_id", None) == item_id:
                return recipe.result_id, getattr(recipe, "count", 1)
        return None, 0

    def can_smelt(self):
        inp = self.furnace_item_stacks[self.SLOT_INPUT]
        if inp is None:
            return False
        result_key, result_count = self._smelt_result_id(inp.subtype_key)
        if result_key is None:
            return False
        result_id, result_damage = Item.parse_subtype_key(result_key)
        out = self.furnace_item_stacks[self.SLOT_OUTPUT]
        if out is None:
            return True
        if out.id != result_id or int(out.damage) != result_damage:
            return False
        return out.count + result_count <= Item.max_stack_size(result_id)

    def smelt_item(self):
        if not self.can_smelt():
            return
        inp = self.furnace_item_stacks[self.SLOT_INPUT]
        result_key, result_count = self._smelt_result_id(inp.subtype_key)
        result_id, result_damage = Item.parse_subtype_key(result_key)
        out = self.furnace_item_stacks[self.SLOT_OUTPUT]
        if out is None:
            self.furnace_item_stacks[self.SLOT_OUTPUT] = ItemStack(
                result_id, result_count, result_damage,
            )
        else:
            out.count += result_count
        inp.count -= 1
        if inp.count <= 0:
            self.furnace_item_stacks[self.SLOT_INPUT] = None

    def update(self):
        was_burning = self.furnace_burn_time > 0
        dirty = False

        if self.furnace_burn_time > 0:
            self.furnace_burn_time -= 1
            dirty = True

        if self.furnace_burn_time == 0 and self.can_smelt():
            fuel = self.furnace_item_stacks[self.SLOT_FUEL]
            burn = get_item_burn_time(fuel.id if fuel else None)
            if burn > 0:
                self.current_item_burn_time = self.furnace_burn_time = burn
                dirty = True
                if fuel is not None:
                    fuel.count -= 1
                    if fuel.count <= 0:
                        self.furnace_item_stacks[self.SLOT_FUEL] = None

        if self.is_burning() and self.can_smelt():
            self.furnace_cook_time += 1
            if self.furnace_cook_time >= self.COOK_TIME_TOTAL:
                self.furnace_cook_time = 0
                self.smelt_item()
                dirty = True
            else:
                dirty = True
        else:
            if self.furnace_cook_time != 0:
                self.furnace_cook_time = 0
                dirty = True

        if was_burning != self.is_burning():
            dirty = True
            self._update_furnace_block_state(self.is_burning())

        return dirty

    def _update_furnace_block_state(self, lit):
        if self.world is None:
            return
        current = self.world.get_block_type(self.x, self.y, self.z)
        if current not in ("furnace", "furnace_lit"):
            return
        meta = self.world.get_metadata(self.x, self.y, self.z)
        target = "furnace_lit" if lit else "furnace"
        if current != target:
            self.world.set_block(self.x, self.y, self.z, target, meta=meta, immediate_mesh=True)

    def on_removed(self):
        if self.world is None:
            return
        if not bool(getattr(self.world, "_multiplayer_client_world", False)):
            for stack in self.furnace_item_stacks:
                if stack is not None and stack.count > 0:
                    self.world.spawn_item_stack(self.x, self.y, self.z, stack)
        self.furnace_item_stacks = [None, None, None]

    def to_dict(self):
        items = []
        for i, stack in enumerate(self.furnace_item_stacks):
            if stack is not None and stack.count > 0:
                items.append({"slot": i, "id": stack.id, "count": stack.count, "damage": stack.damage})
        return {
            "type": "furnace",
            "x": self.x, "y": self.y, "z": self.z,
            "burn": self.furnace_burn_time,
            "cook": self.furnace_cook_time,
            "burn_total": self.current_item_burn_time,
            "items": items,
        }

    @classmethod
    def from_dict(cls, world, data):
        te = cls(world, data["x"], data["y"], data["z"])
        te.furnace_burn_time = int(data.get("burn", 0))
        te.furnace_cook_time = int(data.get("cook", 0))
        te.current_item_burn_time = int(data.get("burn_total", 0))
        for entry in data.get("items", []):
            slot = int(entry.get("slot", 0))
            if 0 <= slot < 3:
                te.furnace_item_stacks[slot] = ItemStack(
                    entry["id"], int(entry.get("count", 1)), int(entry.get("damage", 0))
                )
        return te


class TileEntitySign(TileEntity):
    MAX_LINE_LENGTH = 15
    LINE_COUNT = 4
    ALLOWED_CHARS = (
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789 ,.:-_'\"*!\"#%/()=+?[]{}<>"
    )

    def __init__(self, world, x, y, z):
        super().__init__(world, x, y, z)
        self.sign_text = ["", "", "", ""]
        self.line_being_edited = -1

    def on_removed(self):
        pass

    def to_dict(self):
        return {
            "type": "sign",
            "x": self.x, "y": self.y, "z": self.z,
            "text": list(self.sign_text),
        }

    @classmethod
    def from_dict(cls, world, data):
        te = cls(world, data["x"], data["y"], data["z"])
        lines = data.get("text") or []
        for i in range(4):
            line = str(lines[i]) if i < len(lines) else ""
            te.sign_text[i] = line[: cls.MAX_LINE_LENGTH]
        return te


class TileEntityMobSpawner(TileEntity):
    def __init__(self, world, x, y, z):
        super().__init__(world, x, y, z)
        self.delay = 20
        self.mob_id = legacy_entity_id("pig")
        self.yaw = 0.0
        self.prev_yaw = 0.0
        self._fallback_rng = random.Random()

    def _rng(self):
        return getattr(self.world, "_tick_rng", None) or self._fallback_rng

    def _next_int(self, bound):
        rng = self._rng()
        next_int = getattr(rng, "next_int", None)
        return next_int(bound) if callable(next_int) else rng.randrange(bound)

    def _update_delay(self):
        self.delay = 200 + self._next_int(600)

    def _player_in_range(self):
        player = getattr(self.world, "player", None)
        if player is None:
            return False
        dx = float(player.pos[0]) - self.x
        dy = float(player.pos[1]) - self.y
        dz = float(player.pos[2]) - self.z
        return dx * dx + dy * dy + dz * dz <= 256.0

    def _create_mob(self):
        from game.entities.creeper import Creeper
        from game.entities.pig import Pig
        from game.entities.sheep import Sheep
        from game.entities.cow import Cow
        from game.entities.chicken import Chicken
        from game.entities.skeleton import Skeleton
        from game.entities.spider import Spider
        from game.entities.zombie import Zombie
        from game.entities.pigzombie import ZombiePigman
        from game.entities.giant import Giant
        from game.entities.slime import Slime
        from game.entities.squid import Squid

        entities = {
            legacy_entity_id("creeper"): Creeper,
            legacy_entity_id("pig"): Pig,
            legacy_entity_id("sheep"): Sheep,
            legacy_entity_id("cow"): Cow,
            legacy_entity_id("chicken"): Chicken,
            legacy_entity_id("skeleton"): Skeleton,
            legacy_entity_id("spider"): Spider,
            legacy_entity_id("zombie"): Zombie,
            legacy_entity_id("zombie_pigman"): ZombiePigman,
            legacy_entity_id("giant"): Giant,
            legacy_entity_id("slime"): Slime,
            legacy_entity_id("squid"): Squid,
        }
        entity_type = entities.get(self.mob_id)
        if entity_type is None:
            return None
        return entity_type(
            (self.x + 0.5, self.y, self.z + 0.5),
            yaw=0.0,
            rng=self._rng(),
        )

    def _spawn_burst(self):
        spawn_particle = getattr(self.world, "spawn_particle", None)
        if not callable(spawn_particle):
            return
        rng = self._rng()
        for _ in range(20):
            px = self.x + (rng.random() - 0.5) * 2.0
            py = self.y + (rng.random() - 0.5) * 2.0
            pz = self.z + (rng.random() - 0.5) * 2.0
            spawn_particle("smoke", px, py, pz)
            spawn_particle("flame", px, py, pz)

    def update(self):
        if not self._player_in_range():
            return

        rng = self._rng()
        self.prev_yaw = self.yaw
        px = self.x - 0.5 + rng.random()
        py = self.y - 0.5 + rng.random()
        pz = self.z - 0.5 + rng.random()
        spawn_particle = getattr(self.world, "spawn_particle", None)
        if callable(spawn_particle):
            spawn_particle("smoke", px, py, pz)
            spawn_particle("flame", px, py, pz)
        self.yaw += 1000.0 / (self.delay + 200.0)
        while self.yaw > 360.0:
            self.yaw -= 360.0
            self.prev_yaw -= 360.0

        if self.delay == -1:
            self._update_delay()
        if self.delay > 0:
            self.delay -= 1
        else:
            for _ in range(4):
                mob = self._create_mob()
                if mob is None:
                    return
                nearby = sum(
                    1 for entity in self.world.entities
                    if isinstance(entity, type(mob)) and not entity.dead
                    and float(entity.pos[0]) + float(entity.size[0]) * 0.5 > self.x - 8.0
                    and float(entity.pos[0]) - float(entity.size[0]) * 0.5 < self.x + 9.0
                    and float(entity.pos[1]) + float(entity.size[1]) > self.y - 4.0
                    and float(entity.pos[1]) < self.y + 5.0
                    and float(entity.pos[2]) + float(entity.size[2]) * 0.5 > self.z - 8.0
                    and float(entity.pos[2]) - float(entity.size[2]) * 0.5 < self.z + 9.0
                )
                if nearby >= 6:
                    self._update_delay()
                    return

                x = self.x - 0.5 + (rng.random() - rng.random()) * 4.0
                y = self.y - 0.5 + self._next_int(3) - 1
                z = self.z - 0.5 + (rng.random() - rng.random()) * 4.0
                mob.yaw = rng.random() * 360.0
                if not mob.get_can_spawn_here(x, y, z):
                    continue
                self.world.spawn_entity(mob)
                self._spawn_burst()
                mob.spawn_explosion_particles()
                self._update_delay()
                return

    def to_dict(self):
        return {
            "type": "mob_spawner",
            "x": self.x, "y": self.y, "z": self.z,
            "entity_id": self.mob_id,
            "delay": self.delay,
        }

    @classmethod
    def from_dict(cls, world, data):
        te = cls(world, data["x"], data["y"], data["z"])
        te.mob_id = str(data.get("entity_id", legacy_entity_id("pig")) or legacy_entity_id("pig"))
        te.delay = int(data.get("delay", 20))
        return te


class TileEntityNote(TileEntity):

    def __init__(self, world, x, y, z):
        super().__init__(world, x, y, z)
        self.note = 0
        self.previous_redstone_state = False

    def change_pitch(self):
        self.note = (int(self.note) + 1) % 25
        return self.note

    def instrument_for_block_below(self) -> int:
        from game.block.material import material_for_block

        below = self.world.get_block_type(self.x, self.y - 1, self.z)
        material = material_for_block(below).name
        return {
            "rock": 1,       
            "sand": 2, "gravel": 2,  
            "glass": 3,      
            "wood": 4,       
        }.get(material, 0)

    def trigger_note(self) -> bool:
        if self.world is None:
            return False
        if self.world.get_block_type(self.x, self.y + 1, self.z) is not None:
            return False

        instrument = self.instrument_for_block_below()
        sounds = {
            0: "note.harp",
            1: "note.bd",
            2: "note.snare",
            3: "note.hat",
            4: "note.bassattack",
        }
        pitch = 2.0 ** ((int(self.note) - 12) / 12.0)
        position = (self.x + 0.5, self.y + 0.5, self.z + 0.5)
        play_sound = getattr(self.world, "sound_player", None)
        if callable(play_sound):
            play_sound(sounds[instrument], volume=3.0, pitch=pitch, position=position)
        spawn_particle = getattr(self.world, "spawn_particle", None)
        if callable(spawn_particle):
            spawn_particle(
                "note", self.x, self.y + 1.2, self.z,
                float(self.note) / 24.0, 0.0, 0.0,
            )
        return True

    def update_redstone_state(self, powered: bool) -> bool:
        powered = bool(powered)
        changed = powered != bool(self.previous_redstone_state)
        if changed and powered:
            self.trigger_note()
        self.previous_redstone_state = powered
        return changed

    def to_dict(self):
        return {
            "type": "note_block", "x": self.x, "y": self.y, "z": self.z,
            "note": max(0, min(24, int(self.note))),
        }

    @classmethod
    def from_dict(cls, world, data):
        te = cls(world, data["x"], data["y"], data["z"])
        try:
            te.note = max(0, min(24, int(data.get("note", 0))))
        except (TypeError, ValueError, OverflowError):
            te.note = 0
        return te


def get_chest_inventory(world, x, y, z):
    from game.block.chest_inventory import CombinedChestInventory, find_chest_pair

    x, y, z = int(x), int(y), int(z)

    def get_or_create(cx, cy, cz):
        te = world.get_tile_entity(cx, cy, cz)
        if not isinstance(te, TileEntityChest):
            te = TileEntityChest(world, cx, cy, cz)
            world.set_tile_entity(cx, cy, cz, te)
        return te

    first = get_or_create(x, y, z)
    pair = find_chest_pair(world, x, y, z)
    if pair is None:
        return first
    first_pos, second_pos = pair
    first_te = get_or_create(*first_pos)
    second_te = get_or_create(*second_pos)
    return CombinedChestInventory(first_te, second_te)


def create_tile_entity(world, block_type, x, y, z):
    if block_type == "note_block":
        return TileEntityNote(world, x, y, z)
    if block_type == "dispenser":
        return TileEntityDispenser(world, x, y, z)
    if block_type == "chest":
        return TileEntityChest(world, x, y, z)
    if block_type in ("furnace", "furnace_lit"):
        return TileEntityFurnace(world, x, y, z)
    if block_type in ("sign", "wall_sign"):
        return TileEntitySign(world, x, y, z)
    if block_type == "mob_spawner":
        return TileEntityMobSpawner(world, x, y, z)
    return None
