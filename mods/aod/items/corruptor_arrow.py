import random

from game.entities.arrow import ArrowEntity
from game.entities.entity import Entity
from ..common import MOD_ID, MOD_DIR, ITEM_ID, DEFAULT_RADIUS
from ..systems.corruption import corrupt_around

def register_item():
    from game.entities.item import Item
    Item.item_defs[ITEM_ID] = {
        "name": "Corruptor Arrow",
        "mod_id": MOD_ID,
        "texture_path": str(MOD_DIR / "item.png"),
        "type": "generic",
        "hold": "generic",
        "stack": 64,
        "id": 9001,
    }

class CorruptorArrowEntity(ArrowEntity):
    is_corruptor_arrow = True

    def _handle_block_hit(self, start, end, ticks, block_hit):
        super()._handle_block_hit(start, end, ticks, block_hit)
        cell = (int(self.x_tile), int(self.y_tile), int(self.z_tile))
        rng = random.Random(
            (cell[0] * 734287 + cell[1] * 912391 + cell[2] * 1934963)
            ^ int(getattr(self.world_obj, "world_time", 0) or 0)
            ^ random.getrandbits(16)
        )
        corrupt_around(
            self.world_obj, cell[0], cell[1], cell[2],
            DEFAULT_RADIUS, rng,
        )
        self.dead = True

    def _try_pickup(self):
        player = self.shooting_entity
        if not self.in_ground or self.arrow_shake > 0 or player is None:
            return False
        if getattr(player, "dead", False) or getattr(player, "health", 20) <= 0:
            return False
        player_bounds = self._entity_bounds(player, 0.0)
        pickup_bounds = (
            player_bounds[0] - 1.0, player_bounds[1], player_bounds[2] - 1.0,
            player_bounds[3] + 1.0, player_bounds[4], player_bounds[5] + 1.0,
        )
        half = 0.25
        arrow_bounds = (
            self.pos[0] - half, self.pos[1] - half, self.pos[2] - half,
            self.pos[0] + half, self.pos[1] + half, self.pos[2] + half,
        )
        if not all((
            arrow_bounds[0] < pickup_bounds[3],
            arrow_bounds[3] > pickup_bounds[0],
            arrow_bounds[1] < pickup_bounds[4],
            arrow_bounds[4] > pickup_bounds[1],
            arrow_bounds[2] < pickup_bounds[5],
            arrow_bounds[5] > pickup_bounds[2],
        )):
            return False
        try:
            from game.entities.item import ItemStack
            if not player.inventory.add_stack(ItemStack(ITEM_ID, 1)):
                return False
        except Exception:
            return False
        self._play_sound(
            "random.pop", volume=0.2,
            pitch=((random.random() - random.random()) * 0.7 + 1.0) * 2.0,
        )
        self.dead = True
        return True

def patch_bow(player_cls):
    original = player_cls._use_bow
    if getattr(original, "_aod_corruptor_arrow_patched", False):
        return

    def _use_bow(self, selected):
        if Entity.world is None:
            return False
        inv = self.inventory
        use_corruptor = inv.consume_inventory_item(ITEM_ID, 1)
        if not use_corruptor:
            if not inv.consume_inventory_item("arrow", 1):
                return False
        self._play_world_sound(
            "random.bow", volume=1.0,
            pitch=1.0 / (random.random() * 0.4 + 0.8),
            position=tuple(self.pos),
        )
        if use_corruptor:
            arrow = CorruptorArrowEntity.from_shooter(Entity.world, self)
        else:
            arrow = ArrowEntity.from_shooter(Entity.world, self)
        Entity.world.spawn_entity(arrow)
        return True

    _use_bow._aod_corruptor_arrow_patched = True
    player_cls._use_bow = _use_bow
