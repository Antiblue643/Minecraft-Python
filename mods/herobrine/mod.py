
from game.app.mods import load_mod_config

from . import herobrine as _herobrine

_DEFAULTS = _herobrine.DEFAULT_CONFIG


def _toggle(key, label):
    return {"key": key, "label": label, "type": "toggle", "default": _DEFAULTS[key]}


SETTINGS = (
    _toggle("force_enable", "Force spawn"),
    {"key": "spawn_odds", "label": "World odds (1 in N)", "type": "slider",
     "min": 1, "max": 2048, "default": _DEFAULTS["spawn_odds"]},
    {"key": "spawn_cooldown", "label": "Cooldown (sec)", "type": "slider",
     "min": 0, "max": 600, "default": _DEFAULTS["spawn_cooldown"]},
    _toggle("mode_stalk", "Stalk mode"),
    _toggle("mode_jumpscare", "Jumpscare mode"),
    _toggle("mode_charge", "Charge mode"),
    _toggle("mode_creaking", "Creaking mode"),
    _toggle("act_doors", "Toggle doors/levers"),
    _toggle("act_leaves", "Strip leaves"),
    _toggle("act_footsteps", "Footsteps"),
    _toggle("act_music_fade", "Fade music"),
    _toggle("act_glowing_eyes", "Glowing eyes"),
    _toggle("skin_null", "Null skin"),
    _toggle("skin_copy_player", "Copy player skin"),
    _toggle("prop_torch", "Leave torch"),
    _toggle("prop_sign", "Leave sign"),
    _toggle("prop_jukebox", "Leave jukebox"),
    _toggle("prop_damage", "Deal damage"),
)


def initialize(game):
    _herobrine.configure(load_mod_config("herobrine", dict(_DEFAULTS)))


def on_config_changed(game, config):
    _herobrine.configure(config)


def on_world_tick(game, world, player, dt):
    _herobrine.update_herobrine(
        world, player,
        enabled=not bool(getattr(game, "_is_multiplayer_client_world", False)),
        third_person=bool(getattr(game, "third_person_view", False)),
    )
