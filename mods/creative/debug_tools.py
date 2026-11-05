
from __future__ import annotations

import json
import logging
import math
import os
from types import SimpleNamespace

_LOG = logging.getLogger(__name__)

DAY_LENGTH = 24000.0
NOON = 6000.0
MIDNIGHT = 18000.0

SCRUB_MIN_RATE = 100.0
SCRUB_DOUBLING_SECONDS = 1.0
SCRUB_MAX_RATE = 12000.0


def scrub_rate(held_seconds: float) -> float:
    exponent = min(max(float(held_seconds), 0.0) / SCRUB_DOUBLING_SECONDS, 30.0)
    return min(SCRUB_MAX_RATE, SCRUB_MIN_RATE * (2.0 ** exponent))


class SkyClock:

    def __init__(self):
        self.reset()

    def reset(self):
        self.offset = 0.0
        self.frozen = None
        self._held = 0.0
        self._direction = 0

    @property
    def neutral(self) -> bool:
        return self.frozen is None and self.offset % DAY_LENGTH == 0.0

    @property
    def scrubbing(self) -> bool:
        return self._direction != 0

    def phase(self, world_time, partial=0.0) -> float:
        if self.frozen is not None:
            return self.frozen % DAY_LENGTH
        return (float(world_time) + self.offset + float(partial)) % DAY_LENGTH

    def set_time_of_day(self, world_time, target, partial=0.0):
        target = float(target) % DAY_LENGTH
        if self.frozen is not None:
            self.frozen = target
        else:
            self.offset = (target - float(world_time) - float(partial)) % DAY_LENGTH

    def toggle_pause(self, world_time, partial=0.0) -> bool:
        if self.frozen is None:
            self.frozen = self.phase(world_time, partial)
        else:
            self.offset = (self.frozen - float(world_time) - float(partial)) % DAY_LENGTH
            self.frozen = None
        return self.frozen is not None

    def scrub(self, direction, dt):
        direction = 1 if direction > 0 else -1
        if direction != self._direction:
            self._direction = direction
            self._held = 0.0
        dt = min(max(float(dt), 0.0), 0.1)
        self._held += dt
        delta = direction * scrub_rate(self._held) * dt
        if self.frozen is not None:
            self.frozen = (self.frozen + delta) % DAY_LENGTH
        else:
            self.offset = (self.offset + delta) % DAY_LENGTH

    def stop_scrub(self) -> bool:
        was = self._direction != 0
        self._direction = 0
        self._held = 0.0
        return was

    def to_dict(self) -> dict:
        return {"offset": self.offset, "frozen": self.frozen}

    def load_dict(self, data):
        self.reset()
        if not isinstance(data, dict):
            return
        try:
            offset = float(data.get("offset", 0.0))
            frozen = data.get("frozen")
            frozen = None if frozen is None else float(frozen)
        except (TypeError, ValueError):
            return
        if not math.isfinite(offset) or (frozen is not None and not math.isfinite(frozen)):
            return
        self.offset = offset % DAY_LENGTH
        self.frozen = None if frozen is None else frozen % DAY_LENGTH


CLOCK = SkyClock()


def wrap_celestial_angle(original, clock=CLOCK):
    def get_celestial_angle(world, partial_ticks=0.0):
        if clock.neutral:
            return original(world, partial_ticks)
        if clock.frozen is not None:
            base_time, partial = clock.frozen, 0.0
        else:
            base_time, partial = float(world.world_time) + clock.offset, float(partial_ticks)
        whole = math.floor(base_time)
        stand_in = SimpleNamespace(
            is_hell_world=getattr(world, "is_hell_world", False),
            world_time=whole,
        )
        return original(stand_in, partial + (base_time - whole))
    get_celestial_angle.__wrapped__ = original
    return get_celestial_angle



def _store_path():
    from game.app.mods import mod_config_path
    return mod_config_path("creative").parent / "sky_time.json"


def _world_key(game, world):
    name = getattr(game, "_active_world_name", None)
    if not name:
        return None
    return f"{name}:{getattr(world, 'seed', '')}"


def _read_store(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def persist(game, world, clock=CLOCK, path=None):
    key = _world_key(game, world)
    if key is None:
        return
    try:
        path = path or _store_path()
        store = _read_store(path)
        if clock.neutral:
            store.pop(key, None)
        else:
            store[key] = clock.to_dict()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        _LOG.warning("Could not save creative sky time: %s", exc)


def on_world_started(game, world, creative, clock=CLOCK, path=None):
    clock.reset()
    if not creative:
        return
    key = _world_key(game, world)
    if key is None:
        return
    try:
        clock.load_dict(_read_store(path or _store_path()).get(key))
    except OSError:
        clock.reset()



def _allowed(game) -> bool:
    from game.multiplayer.permissions import debug_tools_allowed
    return debug_tools_allowed(getattr(game, "mp_session", None))


def _say(game, text):
    add = getattr(game, "add_chat_message", None)
    if callable(add):
        add(text)


def _refresh_sky(world):
    update = getattr(world, "_update_skylight_subtracted", None)
    if callable(update):
        update()


def _partial(world):
    getter = getattr(world, "partial_ticks", None)
    try:
        return float(getter()) if callable(getter) else 0.0
    except (TypeError, ValueError):
        return 0.0


def handle_key(game, event, clock=CLOCK, path=None) -> bool:
    import pygame as pg

    world = getattr(game, "world", None)
    player = getattr(game, "player", None)
    if world is None or player is None or not _allowed(game):
        return False
    key = event.key
    mods = getattr(event, "mod", None)
    if mods is None:
        mods = pg.key.get_mods()

    if pg.key.get_pressed()[pg.K_F2]:
        if key == pg.K_1 or key == pg.K_5:
            midnight = key == pg.K_1
            clock.set_time_of_day(
                world.world_time, MIDNIGHT if midnight else NOON, _partial(world),
            )
            _refresh_sky(world)
            persist(game, world, clock, path)
            from game.localization import translate_mod
            _say(game, translate_mod(
                "creative", "debug.timeMidnight" if midnight else "debug.timeNoon",
            ))
            return True
        if key == pg.K_3:
            paused = clock.toggle_pause(world.world_time, _partial(world))
            _refresh_sky(world)
            persist(game, world, clock, path)
            from game.localization import translate_mod
            _say(game, translate_mod(
                "creative", "debug.dayCyclePaused" if paused else "debug.dayCycleResumed",
            ))
            return True
        if key == pg.K_2 or key == pg.K_4:
            return True  
        return False

    if key == pg.K_F4:
        game._creative_debug_f4_held = True
        return True

    if bool(getattr(game, "_creative_debug_f4_held", False)):
        if key == pg.K_BACKQUOTE:
            from game.entities.mob import Mob
            Mob.global_ai_paused = not Mob.global_ai_paused
            return True
        if key == pg.K_0:
            from game.entities.debug_spawning import spawn_debug_entity
            spawn_debug_entity(0, world, player)
            return True
        number = getattr(game, "num_keys", {}).get(key)
        if number is not None:
            from game.entities.debug_spawning import spawn_debug_entity
            bank = 10 if mods & pg.KMOD_CTRL else 1
            spawn_debug_entity(number + bank, world, player)
            return True
    return False


def release_held_key(game, event) -> bool:
    import pygame as pg

    if getattr(event, "key", None) == pg.K_F4:
        game._creative_debug_f4_held = False
        return True
    return False


def update(game, world, player, dt, clock=CLOCK, path=None):
    import pygame as pg

    active = (
        world is not None and player is not None
        and int(getattr(player, "gamemode", 0)) == 1
        and getattr(game, "current_screen", None) is None
        and _allowed(game)
    )
    if active:
        pressed = pg.key.get_pressed()
        if pressed[pg.K_F2]:
            forward, back = bool(pressed[pg.K_4]), bool(pressed[pg.K_2])
            if forward != back:
                clock.scrub(1 if forward else -1, dt)
                _refresh_sky(world)
                return
    if clock.stop_scrub() and world is not None:
        persist(game, world, clock, path)
