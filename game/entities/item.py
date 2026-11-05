import math
import random

from .entity import Entity, TICKS_PER_SECOND, MAX_DT
from .item_model import (
    BOB_HEIGHT, BLOCK_ATLAS_COLS, BLOCK_ATLAS_TILE, BLOCK_SCALE,
    ITEM_ATLAS_COLS, ITEM_ATLAS_TILE, ITEM_SCALE, ItemRendererMixin,
    _stack_copies,
)

ITEM_MAX_AGE_TICKS = 6000
PICKUP_ANIMATION_TICKS = 3


class Item:

    block_defs    = {}
    item_defs     = {}
    block_texture = None
    item_texture  = None

    TOOL_DURABILITY  = {"wood": 32, "stone": 64, "iron": 128, "diamond": 1025, "gold": 32}
    SWORD_DURABILITY = {"wood": 32, "stone": 64, "iron": 128, "diamond": 256, "gold": 32}
    ARMOR_BASE_DURABILITY = (11, 16, 15, 13)

    SINGLE_STACK_TYPES = frozenset({"tool", "melee_weapon", "food", "armor"})
    HELMET_BLOCKS = frozenset({"pumpkin"})
    BLOCK_STACK_LIMITS = {"wooden_door": 1, "iron_door": 1}
    DEFAULT_STACK_LIMIT = 64

    @classmethod
    def definition(cls, item_id):
        return cls.item_defs.get(item_id) or cls.block_defs.get(item_id) or {}

    @classmethod
    def exists(cls, item_id):
        return item_id in cls.item_defs or item_id in cls.block_defs

    @classmethod
    def is_block(cls, item_id):
        return item_id in cls.block_defs

    @classmethod
    def placed_block(cls, item_id):
        if item_id in cls.block_defs:
            return item_id
        block = cls.item_defs.get(item_id, {}).get("block")
        return block if block in cls.block_defs else None

    JAVA_LANGUAGE_KEY_ALIASES = {
        "tile.grass_block": "tile.grass",
        "tile.cobblestone": "tile.stonebrick",
        "tile.planks": "tile.wood",
        "tile.stairs": "tile.stairsWood",
        "tile.cobblestone_stairs": "tile.stairsStone",
        "tile.gold_ore": "tile.oreGold",
        "tile.iron_ore": "tile.oreIron",
        "tile.coal_ore": "tile.oreCoal",
        "tile.wool": "tile.cloth",
        "tile.yellow_flower": "tile.flower",
        "tile.red_flower": "tile.rose",
        "tile.brown_mushroom": "tile.mushroom",
        "tile.red_mushroom": "tile.mushroom",
        "tile.gold_block": "tile.blockGold",
        "tile.iron_block": "tile.blockIron",
        "tile.double_stone_slab": "tile.stoneSlab",
        "tile.stone_slab": "tile.stoneSlab",
        "tile.bricks": "tile.brick",
        "tile.mossy_cobblestone": "tile.stoneMoss",
        "tile.mob_spawner": "tile.mobSpawner",
        "tile.diamond_ore": "tile.oreDiamond",
        "tile.diamond_block": "tile.blockDiamond",
        "tile.crafting_table": "tile.workbench",
        "tile.furnace_lit": "tile.furnace",
        "tile.wooden_door": "tile.doorWood",
        "tile.wall_sign": "tile.sign",
        "tile.redstone_ore": "tile.oreRedstone",
        "tile.redstone_ore_lit": "tile.oreRedstone",
        "tile.redstone_wire": "tile.redstoneDust",
        "tile.stone_pressure_plate": "tile.pressurePlate",
        "tile.wooden_pressure_plate": "tile.pressurePlate",
        "tile.iron_door": "tile.doorIron",
        "tile.unlit_redstone_torch": "tile.notGate",
        "tile.redstone_torch": "tile.notGate",
        "tile.stone_button": "tile.button",
        "tile.snow_block": "tile.snow",
        "tile.clay_block": "tile.clay",
        "tile.reed": "tile.reeds",
        "tile.netherrack": "tile.hellrock",
        "tile.soul_sand": "tile.hellsand",
        "tile.glowstone": "tile.lightgem",
        "tile.jack_o_lantern": "tile.pumpkin",
        "item.chainmail_boots": "item.bootsChain",
        "item.chainmail_chestplate": "item.chestplateChain",
        "item.chainmail_helmet": "item.helmetChain",
        "item.chainmail_leggings": "item.leggingsChain",
        "item.clay": "item.clay",
        "item.cooked_porkchop": "item.porkchopCooked",
        "item.diamond": "item.emerald",
        "item.diamond_axe": "item.hatchetDiamond",
        "item.diamond_boots": "item.bootsDiamond",
        "item.diamond_chestplate": "item.chestplateDiamond",
        "item.diamond_helmet": "item.helmetDiamond",
        "item.diamond_hoe": "item.hoeDiamond",
        "item.diamond_leggings": "item.leggingsDiamond",
        "item.diamond_pickaxe": "item.pickaxeDiamond",
        "item.diamond_shovel": "item.shovelDiamond",
        "item.diamond_sword": "item.swordDiamond",
        "item.flint_and_steel": "item.flintAndSteel",
        "item.gold_axe": "item.hatchetGold",
        "item.gold_boots": "item.bootsGold",
        "item.gold_chestplate": "item.chestplateGold",
        "item.gold_helmet": "item.helmetGold",
        "item.gold_hoe": "item.hoeGold",
        "item.gold_ingot": "item.ingotGold",
        "item.gold_leggings": "item.leggingsGold",
        "item.gold_pickaxe": "item.pickaxeGold",
        "item.gold_shovel": "item.shovelGold",
        "item.gold_sword": "item.swordGold",
        "item.golden_apple": "item.appleGold",
        "item.gunpowder": "item.sulphur",
        "item.iron_axe": "item.hatchetIron",
        "item.iron_boots": "item.bootsIron",
        "item.iron_chestplate": "item.chestplateIron",
        "item.iron_helmet": "item.helmetIron",
        "item.iron_hoe": "item.hoeIron",
        "item.iron_ingot": "item.ingotIron",
        "item.iron_leggings": "item.leggingsIron",
        "item.iron_pickaxe": "item.pickaxeIron",
        "item.iron_shovel": "item.shovelIron",
        "item.iron_sword": "item.swordIron",
        "item.lava_bucket": "item.bucketLava",
        "item.leather_boots": "item.bootsCloth",
        "item.leather_chestplate": "item.chestplateCloth",
        "item.leather_helmet": "item.helmetCloth",
        "item.leather_leggings": "item.leggingsCloth",
        "item.milk_bucket": "item.milk",
        "item.mushroom_stew": "item.mushroomStew",
        "item.raw_porkchop": "item.porkchopRaw",
        "item.stone_axe": "item.hatchetStone",
        "item.stone_hoe": "item.hoeStone",
        "item.stone_pickaxe": "item.pickaxeStone",
        "item.stone_shovel": "item.shovelStone",
        "item.stone_sword": "item.swordStone",
        "item.water_bucket": "item.bucketWater",
        "item.wooden_axe": "item.hatchetWood",
        "item.wooden_hoe": "item.hoeWood",
        "item.wooden_pickaxe": "item.pickaxeWood",
        "item.wooden_shovel": "item.shovelWood",
        "item.wooden_sword": "item.swordWood",
        "item.record_13": "item.record",
        "item.record_cat": "item.record",
        "item.chest_minecart": "item.minecartChest",
        "item.furnace_minecart": "item.minecartFurnace",
        "item.fishing_rod": "item.fishingRod",
        "item.glowstone_dust": "item.yellowDust",
        "item.raw_fish": "item.fishRaw",
        "item.cooked_fish": "item.fishCooked",
    }

    @classmethod
    def translation_key(cls, item_id):
        namespace = "tile" if cls.is_block(item_id) else "item"
        key = f"{namespace}.{item_id}"
        return cls.JAVA_LANGUAGE_KEY_ALIASES.get(key, key)

    @classmethod
    def _mod_translation(cls, item_id, suffix):
        definition = cls.definition(item_id)
        mod_id = definition.get("mod_id") if isinstance(definition, dict) else None
        if not mod_id:
            return None
        from game.localization import translate_mod
        namespace = "tile" if cls.is_block(item_id) else "item"
        default = definition.get("name", "") if suffix == "name" else ""
        return translate_mod(
            str(mod_id), f"{namespace}.{item_id}.{suffix}", default=default,
        )

    @classmethod
    def display_name(cls, item_id):
        from game.localization import translate

        mod_name = cls._mod_translation(item_id, "name")
        if mod_name is not None:
            return mod_name
        return translate(cls.translation_key(item_id) + ".name", default="")

    @classmethod
    def description(cls, item_id):
        from game.localization import translate

        mod_description = cls._mod_translation(item_id, "desc")
        if mod_description is not None:
            return mod_description
        return translate(cls.translation_key(item_id) + ".desc", default="")

    @classmethod
    def max_stack_size(cls, item_id):
        defn = cls.item_defs.get(item_id)
        if defn is None:
            return cls.BLOCK_STACK_LIMITS.get(item_id, cls.DEFAULT_STACK_LIMIT)
        if "stack" in defn:
            return int(defn["stack"])
        if defn.get("type") in cls.SINGLE_STACK_TYPES:
            return 1
        return cls.DEFAULT_STACK_LIMIT

    @classmethod
    def max_damage(cls, item_id):
        defn = cls.item_defs.get(item_id)
        if defn is None:
            return 0
        if "durability" in defn:
            return int(defn["durability"])
        kind, material = defn.get("type"), defn.get("material")
        if kind == "tool":
            return cls.TOOL_DURABILITY.get(material, 0)
        if kind == "melee_weapon":
            return cls.SWORD_DURABILITY.get(material, 0)
        if kind == "armor":
            slot = int(defn.get("slot", 0))
            return (cls.ARMOR_BASE_DURABILITY[slot] * 3) << int(defn.get("armor_level", 0))
        return 0

    @classmethod
    def armor_slot(cls, item_id):
        defn = cls.item_defs.get(item_id)
        if defn and defn.get("type") == "armor":
            return int(defn.get("slot", 0))
        return None


class ItemStack:
    __slots__ = ("id", "count", "damage", "animations_to_go")

    def __init__(self, item_id, count=1, damage=0):
        if item_id == "reed" and "reeds" in Item.item_defs:
            item_id = "reeds"
        self.id = item_id
        self.count = count
        self.damage = damage
        self.animations_to_go = 0

    @property
    def max_stack_size(self):
        return Item.max_stack_size(self.id)

    @property
    def max_damage(self):
        return Item.max_damage(self.id)

    def copy(self):
        return ItemStack(self.id, self.count, self.damage)

    def split(self, amount):
        self.count -= amount
        return ItemStack(self.id, amount, self.damage)

    def damage_item(self, amount):
        limit = self.max_damage
        if limit <= 0:
            return False
        self.damage += amount
        if self.damage > limit:
            self.count = max(0, self.count - 1)
            self.damage = 0
            return self.count == 0
        return False

    def __repr__(self):
        extra = f", damage={self.damage}" if self.damage else ""
        return f"ItemStack({self.id!r} x{self.count}{extra})"


class ItemEntity(ItemRendererMixin, Entity):
    GRAVITY         = 0.04
    DRAG_Y          = 0.98
    DRAG_XZ         = 0.98
    GROUND_FRICTION = 0.7
    GROUND_CONTACT_IN_WATER = True
    GROUND_BOUNCE_FACTOR = -0.5
    ACTIVATE_REDSTONE_ON_GROUND = False
    WATER_CONTACT_SHRINK = 0.0
    WATER_GRAVITY_BEFORE_MOVE = True
    USE_AIR_DRAG_IN_WATER = True
    USE_AIR_DRAG_IN_LAVA = True

    sound_player = None

    def __init__(self, stack, pos, velocity=None, pickup_delay=0):
        super().__init__(pos, (0.25, 0.25, 0.25))
        self.item = stack
        self.hover_start = random.random() * math.pi * 2.0
        if velocity is None:
            velocity = (random.random() * 0.2 - 0.1, 0.2, random.random() * 0.2 - 0.1)
        self.vel = [float(velocity[0]), float(velocity[1]), float(velocity[2])]
        self.pickup_delay = float(pickup_delay)
        self.age = 0.0
        self.health = 5
        self._lava_ticks = 0.0
        self._fire_damage_ticks = 0.0
        self._burning_ticks = 0.0
        self.pickup_ticks = None
        self._pickup_from = None
        self._collector = None
        self._draw_brightness = 1.0

        block_def = Item.block_defs.get(stack.id, {})
        self._is_pressure_plate = (
            stack.id in Item.block_defs and block_def.get("model") == "pressure_plate"
        )
        self._is_button = (
            stack.id in Item.block_defs and block_def.get("model") == "button"
        )
        self._is_stairs = (
            stack.id in Item.block_defs and block_def.get("model") == "stairs"
        )
        self._is_cactus = (
            stack.id in Item.block_defs and block_def.get("model") == "cactus"
        )
        self._is_snow_layer = (
            stack.id in Item.block_defs and block_def.get("model") == "snow_layer"
        )
        self._is_fence = (
            stack.id in Item.block_defs and block_def.get("model") == "fence"
        )
        self._is_block = (
            stack.id in Item.block_defs and block_def.get("model", "cube") == "cube"
        )

    def can_be_picked_up(self):
        return (not self.dead and self.pickup_ticks is None
                and self.pickup_delay <= 0.0)

    def begin_pickup(self, collector):
        self.pickup_ticks = 0.0
        self._pickup_from = tuple(self.pos)
        self._collector = collector

    def update(self, dt):
        if self.dead or Entity.world is None:
            return
        ticks = min(dt, MAX_DT) * TICKS_PER_SECOND

        if self.pickup_ticks is not None:
            self.pickup_ticks += ticks
            if self.pickup_ticks >= PICKUP_ANIMATION_TICKS:
                self.dead = True
            return

        if self.pickup_delay > 0.0:
            self.pickup_delay = max(0.0, self.pickup_delay - ticks)

        if getattr(self, "_merge_cooldown", 0.0) > 0.0:
            self._merge_cooldown = max(0.0, self._merge_cooldown - ticks)

        self._update_lava(ticks)
        if self.dead:
            return
        self._update_fire_damage(ticks)
        if self.dead:
            return
        self._push_out_of_blocks()
        super().update(dt)

        self.age += ticks
        if self.age >= ITEM_MAX_AGE_TICKS:
            self.dead = True
            return

        self._try_merge_nearby()

    def _try_merge_nearby(self):
        if self.dead or self.item is None:
            return
        if self.item.count >= self.item.max_stack_size:
            return
        if Entity.world is None:
            return
        if getattr(self, "_merge_cooldown", 0.0) > 0.0:
            return
        if self.age < 5.0:
            return

        limit = self.item.max_stack_size
        px, py, pz = self.pos
        world = Entity.world
        sync_spatial = getattr(world, "_sync_entity_spatial_index", None)
        if callable(sync_spatial):
            sync_spatial(self)
        iter_nearby = getattr(world, "iter_entities_in_bounds", None)
        if callable(iter_nearby):
            candidates = iter_nearby(
                px - 0.75, py - 0.75, pz - 0.75,
                px + 0.75, py + 0.75, pz + 0.75,
            )
        else:
            candidates = world.entities
        for other in candidates:
            if other is self or other.dead:
                continue
            if not isinstance(other, ItemEntity):
                continue
            if other.pickup_ticks is not None or other.item is None:
                continue
            if getattr(other, "_merge_cooldown", 0.0) > 0.0:
                continue
            if other.age < 5.0:
                continue
            if other.age > self.age:
                continue
            if other.item.id != self.item.id or other.item.damage != self.item.damage:
                continue
            ox, oy, oz = other.pos
            if abs(ox - px) > 0.75 or abs(oy - py) > 0.75 or abs(oz - pz) > 0.75:
                continue
            room = limit - self.item.count
            if room <= 0:
                return
            moved = min(room, other.item.count)
            self.item.count += moved
            other.item.count -= moved
            self.pickup_delay = max(self.pickup_delay, other.pickup_delay)
            self._merge_cooldown = 10.0
            other._merge_cooldown = 10.0
            if other.item.count <= 0:
                other.dead = True
            if self.item.count >= limit:
                return

    def _update_lava(self, ticks):
        cx = math.floor(self.pos[0] + 0.5)
        cy = math.floor(self.pos[1] + self.size[1] * 0.5 + 0.5)
        cz = math.floor(self.pos[2] + 0.5)
        if not self._is_lava_block(cx, cy, cz):
            self._lava_ticks = 0.0
            return
        self._lava_ticks += ticks
        while self._lava_ticks >= 1.0 and not self.dead:
            self._lava_ticks -= 1.0
            self._burning_ticks = max(self._burning_ticks, 600.0)
            self.vel[1] = 0.2
            self.vel[0] = (random.random() - random.random()) * 0.2
            self.vel[2] = (random.random() - random.random()) * 0.2
            if ItemEntity.sound_player is not None:
                ItemEntity.sound_player(
                    "random.fizz", volume=0.4,
                    pitch=2.0 + random.random() * 0.4, position=tuple(self.pos),
                )
            self.health -= 4
            if self.health <= 0:
                self.dead = True

    def _update_fire_damage(self, ticks):
        ticks = max(0.0, float(ticks))
        cx = math.floor(self.pos[0] + 0.5)
        cy = math.floor(self.pos[1] + self.size[1] * 0.5 + 0.5)
        cz = math.floor(self.pos[2] + 0.5)
        touching_fire = self._block_type_at(cx, cy, cz) == "fire"
        touching_lava = self._is_lava_block(cx, cy, cz)

        if self.in_water and self._burning_ticks > 0.0:
            if ItemEntity.sound_player is not None:
                ItemEntity.sound_player(
                    "random.fizz", volume=0.7,
                    pitch=1.6 + random.uniform(-0.4, 0.4),
                    position=tuple(self.pos),
                )
            self._burning_ticks = 0.0
            self._fire_damage_ticks = 0.0
            return

        if touching_fire:
            self._burning_ticks = max(self._burning_ticks, 300.0)

        if self._burning_ticks <= 0.0:
            self._fire_damage_ticks = 0.0
            return

        self._fire_damage_ticks += ticks
        while self._fire_damage_ticks >= 4.0 and not self.dead:
            self._fire_damage_ticks -= 4.0
            self.health -= 1
            if self.health <= 0:
                self.dead = True

        if not touching_fire and not touching_lava:
            self._burning_ticks = max(0.0, self._burning_ticks - ticks)
        if self._burning_ticks <= 0.0:
            self._fire_damage_ticks = 0.0

    def _is_opaque_at(self, bx, by, bz):
        if Entity.world is not None and Entity.world.get_block(bx, by, bz) is not None:
            return super()._is_opaque_at(bx, by, bz)
        btype = self._block_type_at(bx, by, bz)
        if btype is None:
            return False
        d = Item.block_defs.get(btype, {})
        return (
            d.get("model", "cube") == "cube"
            and d.get("nCull", "all") == "all"
            and not d.get("intangible", False)
            and not d.get("transparent", False)
        )