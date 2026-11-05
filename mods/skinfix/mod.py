
from __future__ import annotations

import functools
import json
import logging
import threading
import time
from io import BytesIO
from urllib.request import urlopen

from OpenGL.GL import (
    GL_BLEND, GL_ONE_MINUS_SRC_ALPHA, GL_SRC_ALPHA,
    glBlendFunc, glDisable, glEnable, glIsEnabled,
)

import pygame as pg

from engine.textures import Texture
from game.app.texturepacks import resolve_texture_path
from game.entities import player_model as pm

_LOG = logging.getLogger(__name__)
MOD_ID = "skinfix"
SKIN_SIZE = 64

LAYER_LABELS = {
    "hat": "Hat",
    "jacket": "Jacket",
    "left_sleeve": "Left sleeve",
    "right_sleeve": "Right sleeve",
    "left_pants": "Left pants leg",
    "right_pants": "Right pants leg",
}
LAYER_KEYS = tuple(LAYER_LABELS)

SETTINGS = tuple(
    {"key": key, "label": f"{label}", "type": "toggle", "default": True}
    for key, label in LAYER_LABELS.items()
)



def _copy_flipped(surf, sx, sy, w, h, dx, dy):
    for j in range(h):
        for i in range(w):
            surf.set_at((dx + w - 1 - i, dy + j), surf.get_at((sx + i, sy + j)))


def _mirror_limb(surf, src_u, src_v, dst_u, dst_v, w=4, h=12, d=4):
    _copy_flipped(surf, src_u + d, src_v, w, d, dst_u + d, dst_v)            
    _copy_flipped(surf, src_u + d + w, src_v, w, d, dst_u + d + w, dst_v)    
    _copy_flipped(surf, src_u, src_v + d, d, h, dst_u + d + w, dst_v + d)    
    _copy_flipped(surf, src_u + d, src_v + d, w, h, dst_u + d, dst_v + d)    
    _copy_flipped(surf, src_u + d + w, src_v + d, d, h, dst_u, dst_v + d)    
    _copy_flipped(surf, src_u + d + w + d, src_v + d, w, h,
                  dst_u + d + w + d, dst_v + d)                              


def normalize_skin(src):
    width, height = src.get_size()
    if width < SKIN_SIZE:
        return None
    step = width // SKIN_SIZE
    legacy = height < SKIN_SIZE * step
    if legacy and height < (SKIN_SIZE // 2) * step:
        return None
    out = pg.Surface((SKIN_SIZE, SKIN_SIZE), pg.SRCALPHA)
    out.fill((0, 0, 0, 0))
    rows = SKIN_SIZE // 2 if legacy else SKIN_SIZE
    for y in range(rows):
        for x in range(SKIN_SIZE):
            out.set_at((x, y), src.get_at((x * step, y * step)))
    if legacy:
        _mirror_limb(out, 40, 16, 32, 48)  
        _mirror_limb(out, 0, 16, 16, 48)   
    return out



class SkinModelPart(pm.ModelPart):

    def render(self, scale: float = pm._UNIT) -> None:
        blend_was_enabled = bool(glIsEnabled(GL_BLEND))
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        try:
            super().render(scale)
        finally:
            if not blend_was_enabled:
                glDisable(GL_BLEND)


class ModelBiped64(pm.ModelBiped):

    def __init__(self, inflate: float = 0.0, y_offset: float = 0.0):
        super().__init__(inflate, y_offset)
        self._inflate = float(inflate)
        self._y_offset = float(y_offset)
        self._texture_size = {"texture_width": SKIN_SIZE, "texture_height": SKIN_SIZE}

        def part(u, v, ox, oy, oz, w, h, d, pivot, extra=0.0):
            p = SkinModelPart(
                u, v, ox, oy, oz, w, h, d,
                inflate=inflate + extra, **self._texture_size,
            )
            p.set_rotation_point(pivot[0], pivot[1] + y_offset, pivot[2])
            return p

        self._part = part
        origin = (0.0, 0.0, 0.0)
        self.deadmau5_ears = part(24, 0, -3, -6, -1, 6, 6, 1, origin)
        self.biped_head = part(0, 0, -4, -8, -4, 8, 8, 8, origin)
        self.biped_headwear = part(32, 0, -4, -8, -4, 8, 8, 8, origin, 0.5)
        self.biped_body = part(16, 16, -4, 0, -2, 8, 12, 4, origin)
        self.jacket = part(16, 32, -4, 0, -2, 8, 12, 4, origin, 0.25)
        self.biped_right_leg = part(0, 16, -2, 0, -2, 4, 12, 4, (-2.0, 12.0, 0.0))
        self.biped_left_leg = part(16, 48, -2, 0, -2, 4, 12, 4, (2.0, 12.0, 0.0))
        self.right_pants = part(0, 32, -2, 0, -2, 4, 12, 4, (-2.0, 12.0, 0.0), 0.25)
        self.left_pants = part(0, 48, -2, 0, -2, 4, 12, 4, (2.0, 12.0, 0.0), 0.25)

        self.slim = None
        self._layer_parts = {
            "hat": self.biped_headwear,
            "jacket": self.jacket,
            "left_sleeve": None,
            "right_sleeve": None,
            "left_pants": self.left_pants,
            "right_pants": self.right_pants,
        }
        self.set_slim(False)
        self.parts = []
        self._refresh_parts()

    def _refresh_parts(self):
        self._overlay_pairs = (
            (self.biped_body, self.jacket),
            (self.biped_right_arm, self.right_sleeve),
            (self.biped_left_arm, self.left_sleeve),
            (self.biped_right_leg, self.right_pants),
            (self.biped_left_leg, self.left_pants),
        )
        self._layer_parts.update({
            "left_sleeve": self.left_sleeve,
            "right_sleeve": self.right_sleeve,
        })
        self.parts = [
            self.biped_head, self.biped_body, self.biped_right_arm,
            self.biped_left_arm, self.biped_right_leg, self.biped_left_leg,
            self.biped_headwear, self.jacket, self.right_sleeve,
            self.left_sleeve, self.right_pants, self.left_pants,
        ]

    def set_slim(self, slim: bool) -> None:
        slim = bool(slim)
        if getattr(self, "slim", None) == slim and hasattr(self, "biped_right_arm"):
            return
        self.slim = slim
        width = 3 if slim else 4
        right_x = -2.5 if slim else -3.0
        left_x = -0.5 if slim else -1.0
        part = self._part
        arm_r, arm_l = (-5.0, 2.0, 0.0), (5.0, 2.0, 0.0)
        self.biped_right_arm = part(40, 16, right_x, -2, -2, width, 12, 4, arm_r)
        self.biped_left_arm = part(32, 48, left_x, -2, -2, width, 12, 4, arm_l)
        self.right_sleeve = part(40, 32, right_x, -2, -2, width, 12, 4, arm_r, 0.25)
        self.left_sleeve = part(48, 48, left_x, -2, -2, width, 12, 4, arm_l, 0.25)
        self._refresh_parts()

    def set_layers(self, layers) -> None:
        for key, part in self._layer_parts.items():
            part.show_model = bool(layers.get(key, True))

    def _sync_overlays(self) -> None:
        for base, overlay in self._overlay_pairs:
            overlay.rotation_point = base.rotation_point
            overlay.rotate_angle_x = base.rotate_angle_x
            overlay.rotate_angle_y = base.rotate_angle_y
            overlay.rotate_angle_z = base.rotate_angle_z

    def set_rotation_angles(self, *args, **kwargs) -> None:
        super().set_rotation_angles(*args, **kwargs)
        self._sync_overlays()

    def render_right_arm(self, scale: float = pm._UNIT) -> None:
        self._sync_overlays()
        self.biped_right_arm.render(scale)
        self.right_sleeve.render(scale)



_state = {
    "installed": False,
    "game": None,
    "depth": 0,
    "layers": {key: True for key in LAYER_KEYS},
    "orig": {},
    "multiplayer_installed": False,
}
_modern_surfaces: dict = {}   
_modern_textures: dict = {}   
_generic_textures: dict = {}  
_skin_slim_by_username: dict[str, bool] = {}
_skin_model_lookup_pending: set[str] = set()
_skin_model_retry_after: dict[str, float] = {}
_skin_model_lock = threading.Lock()
_skin_lookup_context = threading.local()


def _skin_model_cache_path():
    from game.app.mods import mod_config_path
    return mod_config_path(MOD_ID).with_name("skin_models.json")


def _load_skin_model_cache() -> None:
    try:
        saved = json.loads(_skin_model_cache_path().read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            with _skin_model_lock:
                _skin_slim_by_username.update({
                    str(name).casefold(): bool(value)
                    for name, value in saved.items()
                    if isinstance(name, str) and isinstance(value, bool)
                })
    except (OSError, ValueError, TypeError):
        pass


def _save_skin_model_cache() -> None:
    try:
        path = _skin_model_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.stem}.{threading.get_ident()}.tmp")
        with _skin_model_lock:
            temporary.write_text(
                json.dumps(_skin_slim_by_username, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
    except OSError as error:
        _LOG.debug("Could not save Skin Fix model metadata: %s", error)


def _profile_skin_url(uuid: str) -> str | None:
    import base64

    url = f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid}"
    with urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    for prop in payload.get("properties") or ():
        if str(prop.get("name") or "") != "textures":
            continue
        raw = base64.b64decode(str(prop.get("value") or ""))
        textures = json.loads(raw.decode("utf-8"))
        skin = (textures.get("textures") or {}).get("SKIN") or {}
        skin_url = str(skin.get("url") or "").strip()
        username = getattr(_skin_lookup_context, "username", None)
        if username:
            metadata = skin.get("metadata") or {}
            with _skin_model_lock:
                _skin_slim_by_username[str(username).casefold()] = (
                    str(metadata.get("model") or "").casefold() == "slim"
                )
                _skin_model_retry_after.pop(str(username).casefold(), None)
            _save_skin_model_cache()
        if skin_url:
            return skin_url
    return None


def _lookup_skin_model_worker(username: str) -> None:
    key = str(username or "").strip().casefold()
    succeeded = False
    try:
        uuid = pm._mojang_username_to_uuid(username)
        if uuid:
            _skin_lookup_context.username = str(username or "").strip()
            _profile_skin_url(uuid)
            with _skin_model_lock:
                succeeded = key in _skin_slim_by_username
    except Exception as error:
        _LOG.debug("Could not look up skin model for %r: %s", username, error)
    finally:
        _skin_lookup_context.username = None
        with _skin_model_lock:
            _skin_model_lookup_pending.discard(key)
            if not succeeded:
                _skin_model_retry_after[key] = time.monotonic() + 300.0


def _ensure_skin_model_lookup(username) -> None:
    key = str(username or "").strip().casefold()
    if not key:
        return
    with _skin_model_lock:
        if (key in _skin_slim_by_username or key in _skin_model_lookup_pending
                or time.monotonic() < _skin_model_retry_after.get(key, 0.0)):
            return
        _skin_model_lookup_pending.add(key)
    threading.Thread(
        target=_lookup_skin_model_worker,
        args=(str(username).strip(),),
        name=f"skin-model-{key[:16]}",
        daemon=True,
    ).start()


def _download_skin_bytes(username):
    _skin_lookup_context.username = str(username or "").strip()
    try:
        return _state["orig"]["download_skin_bytes"](username)
    finally:
        _skin_lookup_context.username = None


def _decode(pending):
    if isinstance(pending, (bytes, bytearray)):
        return pg.image.load(BytesIO(pending), "skin.png")
    return pending


def _intercept_pending(name) -> None:
    with pm._skin_fetch_lock:
        pending = pm._pending_skin_images.pop(name, None)
    if pending is None:
        return
    restored = pending
    try:
        surface = _decode(pending)
        modern = normalize_skin(surface)
        if modern is not None:
            _modern_surfaces[name] = modern
            stale = _modern_textures.pop(name, None)
            if stale is not None:
                try:
                    stale.delete()
                except Exception:
                    pass
        restored = surface
    except Exception as error:
        _LOG.warning("skinfix could not read skin for %r: %s", name, error)
    with pm._skin_fetch_lock:
        pm._pending_skin_images[name] = restored


def _surface_of(texture):
    surface = getattr(texture, "_surface_source", None)
    if surface is not None:
        return surface
    path = getattr(texture, "_source_path", None)
    if path:
        return pg.image.load(resolve_texture_path(path))
    return None


def _convert_texture(base):
    signature = (base.texture_id, base.width, base.height)
    entry = _generic_textures.get(id(base))
    if entry is not None and entry[0] is base and entry[1] == signature:
        return entry[2]
    surface = _surface_of(base)
    modern = normalize_skin(surface) if surface is not None else None
    if modern is None:
        return base
    texture = Texture(modern)
    if entry is not None:
        try:
            entry[2].delete()
        except Exception:
            pass
    _generic_textures[id(base)] = (base, signature, texture)
    return texture


def _username_for(entity):
    if entity is not None:
        if getattr(entity, "skin_texture", None) is not None:
            return None
        username = getattr(entity, "skin_username", None)
        if isinstance(username, str) and username.strip():
            return username.strip()
        rel_path = getattr(entity, "skin_path", None)
        if isinstance(rel_path, str) and rel_path:
            return None
    default = pm._skin_username
    return default.strip() if isinstance(default, str) and default.strip() else None


def _skin_for_username(name):
    texture = _modern_textures.get(name)
    if texture is None:
        texture = Texture(_modern_surfaces[name])
        _modern_textures[name] = texture
    return texture


def _get_skin_texture(username=None):
    name = pm._skin_username if username is None else username.strip()
    if name:
        _intercept_pending(name)
    return _state["orig"]["get_skin_texture"](username)


def _resolve_skin_texture(entity=None):
    base = _state["orig"]["resolve_skin_texture"](entity)
    if not _state["depth"]:
        return base  
    try:
        name = _username_for(entity)
        if name and name in _modern_surfaces:
            return _skin_for_username(name)
        return _convert_texture(base)
    except Exception as error:
        _LOG.warning("skinfix texture conversion failed: %s", error)
        return base



def _apply_layers(player) -> None:
    model = pm._get_main_model()
    if not isinstance(model, ModelBiped64):
        return
    local = player is not None and player is getattr(_state["game"], "player", None)
    if local:
        layers = _state["layers"]
    elif bool(getattr(player, "_mp_remote_player", False)):
        layers = getattr(player, "skin_layers", {})
    else:
        layers = {}
    username = _username_for(player)
    _ensure_skin_model_lookup(username)
    slim = _skin_slim_by_username.get(str(username or "").casefold(), False)
    model.set_slim(bool(slim))
    model.set_layers(layers)


def get_network_state():
    return {key: bool(_state["layers"].get(key, True)) for key in LAYER_KEYS}


def _install_multiplayer_hooks() -> None:
    if _state["multiplayer_installed"]:
        return

    from game.multiplayer import protocol as protocol
    from game.multiplayer.remote_player import RemotePlayer
    from game.multiplayer.session import MultiplayerSession

    orig = _state["orig"]
    orig["normalize_player_effect_state"] = protocol.normalize_player_effect_state

    @functools.wraps(orig["normalize_player_effect_state"])
    def normalize_player_effect_state(data):
        normalized = orig["normalize_player_effect_state"](data)
        raw = data.get("skin_layers", {}) if isinstance(data, dict) else {}
        if not isinstance(raw, dict):
            raw = {}
        normalized["skin_layers"] = {
            key: (raw.get(key) if isinstance(raw.get(key), bool) else True)
            for key in LAYER_KEYS
        }
        return normalized

    protocol.normalize_player_effect_state = normalize_player_effect_state

    orig["session_player_render_state"] = MultiplayerSession._player_render_state

    @functools.wraps(orig["session_player_render_state"])
    def player_render_state(player):
        state = dict(orig["session_player_render_state"](player))
        state["skin_layers"] = get_network_state()
        return state

    MultiplayerSession._player_render_state = staticmethod(player_render_state)

    orig["remote_player_apply_network_state"] = RemotePlayer.apply_network_state

    @functools.wraps(orig["remote_player_apply_network_state"])
    def remote_apply_network_state(player, state):
        orig["remote_player_apply_network_state"](player, state)
        normalized = protocol.normalize_player_effect_state(state)
        player.skin_layers = dict(normalized["skin_layers"])

    RemotePlayer.apply_network_state = remote_apply_network_state
    _state["multiplayer_installed"] = True


def _wrap_player_render(original):
    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        player = args[0] if args else kwargs.get("player")
        _state["depth"] += 1
        try:
            _apply_layers(player)
            return original(*args, **kwargs)
        finally:
            _state["depth"] -= 1
    return wrapper


def _apply_config(config) -> None:
    for key in LAYER_KEYS:
        _state["layers"][key] = bool(config.get(key, True))


def initialize(game):
    _state["game"] = game
    try:
        from game.app.mods import load_mod_config
        _apply_config(load_mod_config(MOD_ID, {key: True for key in LAYER_KEYS}))
    except Exception:
        _LOG.exception("skinfix could not load its settings")
    _load_skin_model_cache()
    if _state["installed"]:
        _install_multiplayer_hooks()
        return
    orig = _state["orig"]
    orig["get_skin_texture"] = pm.get_skin_texture
    orig["resolve_skin_texture"] = pm.resolve_skin_texture
    orig["download_skin_bytes"] = pm._download_skin_bytes
    orig["mojang_uuid_to_skin_url"] = pm._mojang_uuid_to_skin_url
    pm._download_skin_bytes = _download_skin_bytes
    pm._mojang_uuid_to_skin_url = _profile_skin_url
    pm.get_skin_texture = _get_skin_texture
    pm.resolve_skin_texture = _resolve_skin_texture
    for name in ("render_player_model", "render_first_person_hand",
                 "render_inventory_player"):
        orig[name] = getattr(pm, name)
        setattr(pm, name, _wrap_player_render(orig[name]))
    pm._model_main = ModelBiped64(0.0)
    _state["installed"] = True
    _install_multiplayer_hooks()


def on_config_changed(game_or_config, config=None):
    if config is None and isinstance(game_or_config, dict):
        config = game_or_config
    if isinstance(config, dict):
        _apply_config(config)
