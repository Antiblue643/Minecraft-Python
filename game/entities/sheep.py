from __future__ import annotations

from .animal import Animal
from .entity import Entity


class Sheep(Animal):
    WIDTH = 0.9
    HEIGHT = 1.3
    HEALTH = 10
    MOVE_SPEED = 0.7
    DROP_ITEM = None
    LIVING_SOUND = "mob.sheep"
    HURT_SOUND = "mob.sheep"
    DEATH_SOUND = "mob.sheep"

    FUR_COLORS = (
        (1.0, 1.0, 1.0), (0.95, 0.7, 0.2), (0.9, 0.5, 0.85),
        (0.6, 0.7, 0.95), (0.9, 0.9, 0.2), (0.5, 0.8, 0.1),
        (0.95, 0.7, 0.8), (0.3, 0.3, 0.3), (0.6, 0.6, 0.6),
        (0.3, 0.6, 0.7), (0.7, 0.4, 0.9), (0.2, 0.4, 0.8),
        (0.5, 0.4, 0.3), (0.4, 0.5, 0.2), (0.8, 0.3, 0.3),
        (0.1, 0.1, 0.1),
    )

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.size = (self.WIDTH, self.HEIGHT, self.WIDTH)
        self.skin_path = "mob/sheep.png"
        self.health = self.HEALTH
        self.prev_health = self.HEALTH
        self.sheared = False
        self.fleece_color = 0
        self._drops_spawned = False

    @staticmethod
    def random_fleece_color(rng) -> int:
        next_int = getattr(rng, "next_int", None)
        roll = int(next_int(100)) if callable(next_int) else int(rng.randrange(100))
        if roll < 5:
            return 15
        if roll < 10:
            return 7
        if roll < 15:
            return 8
        return 0

    def set_fleece_color(self, color: int) -> None:
        self.fleece_color = int(color) & 15

    def draw(self):
        from .mob_models import draw_living_with_model

        if self.sheared:
            draw_living_with_model(self, "sheep")
        else:
            draw_living_with_model(
                self,
                "sheep",
                extra_pass_kind="sheep_fur",
                extra_skin_path="mob/sheep_fur.png",
            )

    def interact(self, player):
        if self.dead or self.health <= 0 or self.sheared or player is None:
            return False
        if getattr(player, "health", 1) <= 0:
            return False
        inventory = getattr(player, "inventory", None)
        get_active = getattr(inventory, "get_active_item", None)
        held = get_active() if callable(get_active) else None
        if (held is None or getattr(held, "id", None) != "dye"
                or int(getattr(held, "count", 0)) <= 0):
            return False
        fleece_color = (~int(getattr(held, "damage", 0))) & 15
        if fleece_color == self.fleece_color:
            return False

        world = Entity.world
        if (bool(getattr(world, "_multiplayer_client_world", False))
                and bool(getattr(self, "_mp_remote_entity", False))):
            request = getattr(world, "_network_entity_interact_callback", None)
            return bool(request(self, "dye_sheep")) if callable(request) else False

        self.set_fleece_color(fleece_color)
        if int(getattr(player, "gamemode", 0)) != 1:
            consume = getattr(inventory, "consume_current", None)
            if callable(consume):
                consume(1)
            else:
                held.count -= 1
                if held.count <= 0:
                    slot = getattr(inventory, "active_idx", getattr(inventory, "current_item", 0))
                    inventory.set_stack(slot, None)
        return True

    def _shear_for_living_attacker(self, attacker) -> None:
        is_living = bool(getattr(attacker, "is_living_entity", False)) or (
            attacker is not None and type(attacker).__name__ == "Player"
        )
        world = Entity.world
        client_world = bool(getattr(world, "_multiplayer_client_world", False))
        if self.sheared or attacker is None or not is_living or client_world:
            return

        self.sheared = True
        count = 1 + self._rand_int(3)
        for _ in range(count):
            item = self.drop_item(
                "wool", 1, y_offset=1.0, damage=self.fleece_color,
            )
            if item is not None:
                try:
                    item.vel[1] += self._rng.random() * 0.05
                    item.vel[0] += (self._rng.random() - self._rng.random()) * 0.1
                    item.vel[2] += (self._rng.random() - self._rng.random()) * 0.1
                except Exception:
                    pass

    def attack_entity_from(self, attacker=None, amount=1) -> bool:
        self._shear_for_living_attacker(attacker)
        return super().attack_entity_from(attacker, amount)

    def on_fishing_hook_hit(self, attacker=None) -> bool:
        self._shear_for_living_attacker(attacker)
        return super().on_fishing_hook_hit(attacker)


EntitySheep = Sheep
