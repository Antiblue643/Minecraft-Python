
from __future__ import annotations

import base64
import json
import math
from urllib.parse import urlsplit, urlunsplit

_TICK_SECONDS = 0.05
_MAX_CATCHUP_TICKS = 4


def initialize_cape_motion(entity) -> None:
    position = [float(value) for value in getattr(entity, "pos", (0.0, 0.0, 0.0))[:3]]
    if len(position) != 3:
        position = [0.0, 0.0, 0.0]
    entity.cape_pos = position[:]
    entity.prev_cape_pos = position[:]
    entity._cape_tick_accum = 0.0
    entity.cape_walk_speed = 0.0
    entity.prev_cape_walk_speed = 0.0
    entity._cape_last_observed_pos = position[:]
    entity._cape_tick_player_pos = position[:]
    entity._cape_pending_delta = [0.0, 0.0, 0.0]
    entity._cape_was_riding = False


def _sync_cape_to_rider(entity, position) -> None:
    current = [float(value) for value in position[:3]]
    previous = [float(value) for value in getattr(entity, "prev_pos", current)[:3]]
    if len(previous) != 3:
        previous = current[:]
    entity.prev_cape_pos[:] = previous
    entity.cape_pos[:] = current
    entity._cape_last_observed_pos[:] = current
    entity._cape_tick_player_pos[:] = current
    entity._cape_pending_delta[:] = [0.0, 0.0, 0.0]
    entity._cape_tick_accum = 0.0
    entity.prev_cape_walk_speed = 0.0
    entity.cape_walk_speed = 0.0
    entity._cape_was_riding = True


def cape_body_attachment_pitch(model, is_sneaking: bool) -> float:
    if not is_sneaking:
        return 0.0
    body = getattr(model, "biped_body", None)
    try:
        return float(getattr(body, "rotate_angle_x", 0.0))
    except (TypeError, ValueError, OverflowError):
        return 0.0


def advance_cape_motion(entity, dt: float) -> None:
    if not hasattr(entity, "cape_pos") or not hasattr(entity, "prev_cape_pos"):
        initialize_cape_motion(entity)

    position = [float(value) for value in getattr(entity, "pos", (0.0, 0.0, 0.0))[:3]]
    if len(position) != 3:
        position = [0.0, 0.0, 0.0]

    if getattr(entity, "riding_entity", None) is not None or bool(getattr(entity, "is_riding", False)):
        _sync_cape_to_rider(entity, position)
        return

    if bool(getattr(entity, "_cape_was_riding", False)):
        initialize_cape_motion(entity)

    try:
        elapsed = max(0.0, min(float(dt), 0.25))
    except (TypeError, ValueError, OverflowError):
        elapsed = 0.0

    last_observed = getattr(entity, "_cape_last_observed_pos", position)
    frame_delta = [position[i] - float(last_observed[i]) for i in range(3)]
    entity._cape_last_observed_pos = position[:]

    if any(abs(delta) > 10.0 for delta in frame_delta):
        entity.cape_pos[:] = position
        entity.prev_cape_pos[:] = position
        entity._cape_tick_player_pos[:] = position
        entity._cape_pending_delta[:] = [0.0, 0.0, 0.0]
        entity._cape_tick_accum = 0.0
        entity.cape_walk_speed = 0.0
        entity.prev_cape_walk_speed = 0.0
        return

    pending_delta = getattr(entity, "_cape_pending_delta", [0.0, 0.0, 0.0])
    for axis in range(3):
        pending_delta[axis] += frame_delta[axis]
    entity._cape_pending_delta = pending_delta

    total_time = max(0.0, float(getattr(entity, "_cape_tick_accum", 0.0))) + elapsed
    ticks = min(int(total_time / _TICK_SECONDS), _MAX_CATCHUP_TICKS)
    tick_player_pos = getattr(entity, "_cape_tick_player_pos", position[:])

    if ticks <= 0:
        entity._cape_tick_accum = total_time
        return

    remaining_time = total_time
    remaining_delta = pending_delta[:]
    for _ in range(ticks):
        fraction = min(1.0, _TICK_SECONDS / max(remaining_time, _TICK_SECONDS))
        step_delta = [value * fraction for value in remaining_delta]
        for axis in range(3):
            tick_player_pos[axis] += step_delta[axis]
            remaining_delta[axis] -= step_delta[axis]
        remaining_time = max(0.0, remaining_time - _TICK_SECONDS)

        entity.prev_cape_pos[:] = entity.cape_pos
        for axis in range(3):
            delta = tick_player_pos[axis] - float(entity.cape_pos[axis])
            entity.cape_pos[axis] += delta * 0.25

        target_walk_speed = math.hypot(step_delta[0], step_delta[2])
        is_remote = bool(getattr(entity, "_mp_remote_player", False))
        if not is_remote and (
            not bool(getattr(entity, "on_ground", False))
            or float(getattr(entity, "health", 20.0)) <= 0.0
        ):
            target_walk_speed = 0.0
        target_walk_speed = min(0.1, max(0.0, target_walk_speed))
        entity.prev_cape_walk_speed = float(getattr(entity, "cape_walk_speed", 0.0))
        entity.cape_walk_speed = entity.prev_cape_walk_speed + (
            target_walk_speed - entity.prev_cape_walk_speed
        ) * 0.4

    entity._cape_tick_player_pos[:] = tick_player_pos
    entity._cape_pending_delta[:] = remaining_delta
    entity._cape_tick_accum = remaining_time



def _normalize_mojang_texture_url(value) -> str | None:
    raw_url = str(value or "").strip()
    try:
        parsed = urlsplit(raw_url)
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme.lower() not in ("http", "https")
            or hostname != "textures.minecraft.net"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or not parsed.path.startswith("/texture/")
            or parsed.query
            or parsed.fragment
        ):
            return None
    except (TypeError, ValueError):
        return None
    return urlunsplit(("https", "textures.minecraft.net", parsed.path, "", ""))


def cape_url_from_profile_payload(payload) -> str | None:
    if not isinstance(payload, dict):
        return None
    for prop in payload.get("properties") or ():
        if not isinstance(prop, dict) or str(prop.get("name") or "") != "textures":
            continue
        try:
            raw = base64.b64decode(str(prop.get("value") or ""), validate=True)
            textures = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, TypeError):
            continue
        if not isinstance(textures, dict):
            continue
        texture_set = textures.get("textures") or {}
        if not isinstance(texture_set, dict):
            continue
        cape = texture_set.get("CAPE") or {}
        if not isinstance(cape, dict):
            continue
        cape_url = _normalize_mojang_texture_url(cape.get("url"))
        if cape_url:
            return cape_url
    return None


def _lerp(previous, current, partial: float) -> float:
    return float(previous) + (float(current) - float(previous)) * partial


def deadmau5_ear_anchor(side: float, head_yaw: float, head_pitch: float) -> tuple[float, float, float]:
    side_value = -1.0 if float(side) < 0.0 else 1.0
    x = side_value * 6.0
    y = -6.0
    z = 0.0

    cp, sp = math.cos(float(head_pitch)), math.sin(float(head_pitch))
    y, z = y * cp - z * sp, y * sp + z * cp
    cy, sy = math.cos(float(head_yaw)), math.sin(float(head_yaw))
    x, z = x * cy + z * sy, -x * sy + z * cy
    return x, y, z


def cape_projection_yaw_for_world(body_yaw: float) -> float:
    return 180.0 - float(body_yaw)


def cape_rotation_angles(entity, body_yaw: float, partial: float = 0.0) -> tuple[float, float, float]:
    try:
        amount = max(0.0, min(1.0, float(partial)))
    except (TypeError, ValueError, OverflowError):
        amount = 0.0

    current_pos = getattr(entity, "pos", (0.0, 0.0, 0.0))
    previous_pos = getattr(entity, "prev_pos", current_pos)
    current_cape = getattr(entity, "cape_pos", current_pos)
    previous_cape = getattr(entity, "prev_cape_pos", current_cape)

    dx = _lerp(previous_cape[0], current_cape[0], amount) - _lerp(previous_pos[0], current_pos[0], amount)
    dy = _lerp(previous_cape[1], current_cape[1], amount) - _lerp(previous_pos[1], current_pos[1], amount)
    dz = _lerp(previous_cape[2], current_cape[2], amount) - _lerp(previous_pos[2], current_pos[2], amount)

    yaw = math.radians(float(body_yaw))
    sin_yaw = math.sin(yaw)
    neg_cos_yaw = -math.cos(yaw)

    vertical = max(-6.0, min(32.0, dy * 10.0))
    forward = dx * sin_yaw + dz * neg_cos_yaw
    forward = max(0.0, forward * 100.0)
    sideways = (dx * neg_cos_yaw - dz * sin_yaw) * 100.0

    walk = _lerp(
        getattr(entity, "prev_distance_walked_modified", 0.0),
        getattr(entity, "distance_walked_modified", 0.0),
        amount,
    )
    walk_speed = _lerp(
        getattr(entity, "prev_cape_walk_speed", 0.0),
        getattr(entity, "cape_walk_speed", 0.0),
        amount,
    )
    vertical += math.sin(walk * 6.0) * 32.0 * walk_speed

    return 6.0 + forward / 2.0 + vertical, sideways / 2.0, -sideways / 2.0
