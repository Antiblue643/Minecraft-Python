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

    TOOL_DURABILITY  = {"wood": 59, "stone": 131, "iron": 250, "diamond": 1561, "gold": 32}
    SWORD_DURABILITY = {"wood": 59, "stone": 131, "iron": 250, "diamond": 1561, "gold": 32}
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

    @classmethod
    def has_subtypes(cls, item_id):
        return bool(cls.definition(item_id).get("subtypes", False))

    @classmethod
    def subtype_key(cls, item_id, damage=0):
        damage = int(damage or 0)
        if damage and cls.has_subtypes(item_id):
            return f"{item_id}:{damage}"
        return item_id

    @classmethod
    def parse_subtype_key(cls, key):
        item_id, separator, damage = str(key).partition(":")
        if separator and damage.lstrip("-").isdigit():
            return item_id, int(damage)
        return str(key), 0

    @classmethod
    def icon_texture(cls, item_id, damage=0):
        atlas = cls.item_defs.get(item_id, {}).get("texture", 0)
        if isinstance(atlas, list):
            atlas = atlas[0]
        if item_id == "dye":
            d = int(damage or 0) & 15
            atlas = int(atlas) + d % 8 * 16 + d // 8
        return atlas

    @classmethod
    def block_faces(cls, item_id, damage=0, default=None):
        definition = cls.block_defs.get(item_id) or {}
        meta = int(damage or 0) & 15
        if item_id == "leaves":
            try:
                from game.block import Block
                base = 52 if bool(getattr(Block, "_fancy_graphics", True)) else 53
            except Exception:
                base = 52
            return base + (80 if (meta & 3) == 1 else 0)
        variants = definition.get("metaFaces")
        if variants and meta:
            found = variants.get(str(meta))
            if found is not None:
                return found
        return definition.get("faces", 0) if default is None else default

    @classmethod
    def translation_key(cls, item_id):
        is_block = cls.is_block(item_id)
        namespace = "tile" if is_block else "item"
        definition = (
            cls.block_defs.get(item_id, {}) if is_block
            else cls.item_defs.get(item_id, {})
        )
        java_name = definition.get("javaName", item_id)
        return f"{namespace}.{java_name}"

    DYE_COLORS = (
        "black", "red", "green", "brown", "blue", "purple", "cyan", "silver",
        "gray", "pink", "lime", "yellow", "lightBlue", "magenta", "orange", "white",
    )

    @classmethod
    def stack_translation_key(cls, item_id, damage=0):
        damage = int(damage or 0)
        if item_id == "wool":
            return f"tile.cloth.{cls.DYE_COLORS[~damage & 15]}"
        if item_id == "dye":
            return f"item.dyePowder.{cls.DYE_COLORS[damage & 15]}"
        if item_id == "coal" and damage == 1:
            return "item.charcoal"
        return cls.translation_key(item_id)

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
    def display_name(cls, item_id, damage=0):
        from game.localization import translate

        mod_name = cls._mod_translation(item_id, "name")
        if mod_name is not None:
            return mod_name
        return translate(cls.stack_translation_key(item_id, damage) + ".name", default="")

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

    @property
    def subtype_key(self):
        return Item.subtype_key(self.id, self.damage)

    def stacks_with(self, other):
        return (other is not None and self.id == other.id
                and int(self.damage) == int(other.damage))

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
            stack.id != "cake"
            and stack.id in Item.block_defs
            and block_def.get("model", "cube") == "cube"
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