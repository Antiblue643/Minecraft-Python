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
        result_id, result_count = self._smelt_result_id(inp.id)
        if result_id is None:
            return False
        out = self.furnace_item_stacks[self.SLOT_OUTPUT]
        if out is None:
            return True
        if out.id != result_id:
            return False
        return out.count + result_count <= Item.max_stack_size(result_id)

    def smelt_item(self):
        if not self.can_smelt():
            return
        inp = self.furnace_item_stacks[self.SLOT_INPUT]
        result_id, result_count = self._smelt_result_id(inp.id)
        out = self.furnace_item_stacks[self.SLOT_OUTPUT]
        if out is None:
            self.furnace_item_stacks[self.SLOT_OUTPUT] = ItemStack(result_id, result_count)
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
    if block_type == "chest":
        return TileEntityChest(world, x, y, z)
    if block_type in ("furnace", "furnace_lit"):
        return TileEntityFurnace(world, x, y, z)
    if block_type in ("sign", "wall_sign"):
        return TileEntitySign(world, x, y, z)
    if block_type == "mob_spawner":
        return TileEntityMobSpawner(world, x, y, z)
    return None
