
from __future__ import annotations

from .animal import Animal


class Cow(Animal):
    WIDTH = 0.9
    HEIGHT = 1.3
    HEALTH = 10
    MOVE_SPEED = 0.7
    DROP_ITEM = "leather"
    LIVING_SOUND = "mob.cow"
    HURT_SOUND = "mob.cowhurt"
    DEATH_SOUND = "mob.cowhurt"

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.size = (self.WIDTH, self.HEIGHT, self.WIDTH)
        self.skin_path = "mob/cow.png"
        self.health = self.HEALTH
        self.prev_health = self.HEALTH
        self._drops_spawned = False

    def interact(self, player):
        if self.dead or player is None or getattr(player, "health", 1) <= 0:
            return False

        inventory = getattr(player, "inventory", None)
        get_active_item = getattr(inventory, "get_active_item", None)
        held = get_active_item() if callable(get_active_item) else None
        if getattr(held, "id", None) != "bucket":
            return False

        from .entity import Entity
        world = Entity.world
        if (bool(getattr(world, "_multiplayer_client_world", False))
                and bool(getattr(self, "_mp_remote_entity", False))):
            request = getattr(world, "_network_entity_interact_callback", None)
            return bool(request(self, "milk_cow")) if callable(request) else False

        set_stack = getattr(inventory, "set_stack", None)
        if not callable(set_stack):
            return False
        slot = getattr(
            inventory, "active_idx",
            getattr(inventory, "current_item", 0),
        )
        from .item import ItemStack
        set_stack(slot, ItemStack("milk_bucket"))
        return True

    def draw(self):
        from .mob_models import draw_living_with_model

        draw_living_with_model(self, "cow")


EntityCow = Cow
