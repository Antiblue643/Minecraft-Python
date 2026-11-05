from __future__ import annotations

import math

from .animal import Animal


class Pig(Animal):
    WIDTH = 0.9
    HEIGHT = 0.9
    HEALTH = 10
    MOVE_SPEED = 0.7
    DROP_ITEM = "raw_porkchop"
    LIVING_SOUND = "mob.pig"
    HURT_SOUND = "mob.pig"
    DEATH_SOUND = "mob.pigdeath"

    def __init__(self, pos, *, yaw=None, rng=None):
        super().__init__(pos, yaw=yaw, rng=rng)
        self.size = (self.WIDTH, self.HEIGHT, self.WIDTH)
        self.skin_path = "mob/pig.png"
        self.health = self.HEALTH
        self.prev_health = self.HEALTH
        self.saddled = False
        self.rider = None
        self._drops_spawned = False

    def saddle(self):
        if self.dead or self.saddled:
            return False
        self.saddled = True
        return True

    def interact(self, player):
        if self.dead or player is None:
            return False
        if getattr(player, "health", 1) <= 0:
            return False
        inventory = getattr(player, "inventory", None)
        get_active_item = getattr(inventory, "get_active_item", None)
        held = get_active_item() if callable(get_active_item) else None
        if (not self.saddled and held is not None
                and getattr(held, "id", None) == "saddle"):
            if not self.saddle():
                return False
            if getattr(player, "gamemode", 0) != 1:
                remove_item = getattr(inventory, "remove_item", None)
                if callable(remove_item):
                    slot = getattr(
                        inventory, "active_idx",
                        getattr(inventory, "current_item", 0),
                    )
                    remove_item(slot)
                else:
                    consume_current = getattr(inventory, "consume_current", None)
                    if callable(consume_current):
                        consume_current(1)
            return True

        if not self.saddled:
            return False
        if getattr(player, "riding_entity", None) is self:
            self.dismount()
            return True
        old_mount = getattr(player, "riding_entity", None)
        if old_mount is not None:
            dismount = getattr(old_mount, "dismount", None)
            if callable(dismount):
                dismount()
            else:
                player.riding_entity = None
        self.rider = player
        player.riding_entity = self
        self._position_rider()
        return True

    def dismount(self):
        rider = self.rider
        if rider is None:
            return
        if getattr(rider, "riding_entity", None) is self:
            rider.riding_entity = None
        yaw = math.radians(float(getattr(self, "yaw", 0.0)))
        rider.pos[0] = self.pos[0] + 0.9 * math.sin(yaw)
        rider.pos[1] = self.pos[1] + self.size[1]
        rider.pos[2] = self.pos[2] + 0.9 * math.cos(yaw)
        if hasattr(rider, "vel"):
            rider.vel[:] = self.vel
        sync = getattr(rider, "sync_camera", None)
        if callable(sync):
            sync()
        self.rider = None

    def _position_rider(self):
        rider = getattr(self, "rider", None)
        if rider is None:
            return
        rider.pos[0] = self.pos[0]
        rider.pos[1] = self.pos[1] + self.size[1] * 0.25
        rider.pos[2] = self.pos[2]
        if hasattr(rider, "vel"):
            rider.vel[:] = self.vel
        sync = getattr(rider, "sync_camera", None)
        if callable(sync):
            sync()

    def update(self, dt):
        if self.dead:
            self.dismount()
            return
        rider = getattr(self, "rider", None)
        if rider is not None and getattr(rider, "health", 1) <= 0:
            self.dismount()
        super().update(dt)
        self._position_rider()

    def draw(self):
        from .mob_models import draw_living_with_model

        if self.saddled:
            draw_living_with_model(
                self, "pig", extra_pass_kind="pig_saddle",
                extra_skin_path="mob/saddle.png",
            )
        else:
            draw_living_with_model(self, "pig")


EntityPig = Pig
