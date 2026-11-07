
from __future__ import annotations

import base64
import json
import math
import re
import zlib
from typing import Any, Optional

PROTO_VERSION = 11
DEFAULT_PORT = 25565
MAX_LINE = 512 * 1024
MAX_PENDING_BYTES = 8 << 20
MAX_CHUNK_BYTES = 256 * 1024
_ITEM_KEY = re.compile(r"^[a-z0-9_./-]{1,64}$")
INVENTORY_MAIN_SIZE = 36
INVENTORY_ARMOR_SIZE = 4
MAX_ENTITY_COUNT = 256
MAX_TILE_ENTITY_COUNT = 256
_TILE_ENTITY_TYPES = frozenset({"chest", "furnace", "dispenser", "sign", "mob_spawner", "note_block"})
_MOB_ENTITY_KINDS = frozenset({
    "pig", "cow", "sheep", "chicken", "zombie", "skeleton",
    "creeper", "spider", "slime", "giant", "mob", "monster", "zombie_pigman", "ghast", "squid"
})
_ENTITY_KINDS = frozenset({
    "item", "tnt", "arrow", "snowball", "egg", "painting", "boat", "minecart",
    "falling_block", "fireball", "fishing_bobber"
}) | _MOB_ENTITY_KINDS
_PAINTING_ARTS = frozenset({
    "Kebab", "Aztec", "Alban", "Aztec2", "Bomb", "Plant", "Wasteland",
    "Pool", "Courbet", "Sea", "Sunset", "Wanderer", "Match", "Bust",
    "Stage", "Void", "SkullAndRoses", "Fighters", "Pointer", "Creebet", "Graham",
    "DonkeyKong", "Skeleton", "Pigscene"
})
_ENTITY_SPAWN_REQUEST_KINDS = frozenset({
    "arrow", "snowball", "egg", "painting", "boat", "minecart", "fireball"
})


def encode(msg: dict) -> bytes:
    return (json.dumps(msg, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def decode_line(line: bytes) -> Optional[dict]:
    line = line.strip()
    if not line or len(line) > MAX_LINE:
        return None
    try:
        data = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("t"), str):
        return None
    return data


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return result if math.isfinite(result) else float(default)


_EVENT_SOUND_NAME = re.compile(r"^[a-z0-9_./-]{1,64}$")
_EVENT_PARTICLES = frozenset({
    "smoke", "largesmoke", "explode", "flame", "bubble", "splash",
    "rain", "lava", "reddust", "snow", "snowballpoof", "slime",
    "block_hit", "block_destroy", "note",
})

def normalize_world_event(data: Any, *, allow_authoritative: bool = True) -> Optional[dict]:
    if not isinstance(data, dict):
        return None
    kind = str(data.get("kind") or "")
    if kind == "sound":
        name = str(data.get("name") or "")[:64]
        if not _EVENT_SOUND_NAME.fullmatch(name) or name == "random.click":
            return None
        result = {
            "t": "world_event", "kind": "sound", "name": name,
            "volume": max(0.0, min(8.0, _finite_float(data.get("volume", 1.0), 1.0))),
            "pitch": max(0.25, min(4.0, _finite_float(data.get("pitch", 1.0), 1.0))),
        }
        position = data.get("position")
        if position is not None:
            if not isinstance(position, (list, tuple)) or len(position) != 3:
                return None
            result["position"] = [
                _finite_float(position[0]), _finite_float(position[1], 64.0),
                _finite_float(position[2]),
            ]
        return result
    if kind == "particle":
        name = str(data.get("name") or "")[:32]
        if name not in _EVENT_PARTICLES:
            return None
        result = {"t": "world_event", "kind": "particle", "name": name}
        for axis, default in (("x", 0.0), ("y", 64.0), ("z", 0.0),
                              ("mx", 0.0), ("my", 0.0), ("mz", 0.0)):
            value = _finite_float(data.get(axis, default), default)
            limit = 30_000_000.0 if axis in ("x", "z") else (4096.0 if axis == "y" else 20.0)
            result[axis] = max(-limit, min(limit, value))
        if name in {"block_hit", "block_destroy"}:
            block_type = data.get("block_type")
            if not isinstance(block_type, str) or not _ITEM_KEY.fullmatch(block_type):
                return None
            result["block_type"] = block_type
            try:
                result["meta"] = int(data.get("meta", 0)) & 15
                result["face"] = max(0, min(5, int(data.get("face", 0))))
            except (TypeError, ValueError, OverflowError):
                return None
        return result
    if allow_authoritative and kind == "explosion":
        result = {"t": "world_event", "kind": "explosion"}
        for axis, default in (("x", 0.0), ("y", 64.0), ("z", 0.0)):
            result[axis] = _finite_float(data.get(axis, default), default)
        result["power"] = max(0.1, min(8.0, _finite_float(data.get("power", 4.0), 4.0)))
        try:
            result["seed"] = int(data.get("seed", 0)) & 0x7FFFFFFF
        except (TypeError, ValueError, OverflowError):
            result["seed"] = 0
        return result
    if allow_authoritative and kind == "time":
        try:
            world_time = int(data.get("world_time", 0))
        except (TypeError, ValueError, OverflowError):
            return None
        return {"t": "world_event", "kind": "time",
                "world_time": max(0, min(2**63 - 1, world_time))}
    return None

def world_event(kind: str, **values) -> Optional[dict]:
    return normalize_world_event({"kind": kind, **values}, allow_authoritative=True)




def legacy_java_kick(reason: str = "Minecraft Java Edition is not supported by this Python server.") -> bytes:
    text = str(reason)[:200]
    encoded = text.encode("ascii", errors="replace")
    if len(encoded) > 65535:
        encoded = encoded[:65535]
    return b"\xff" + len(encoded).to_bytes(2, "big") + encoded


def _normalize_stack(stack: Any) -> Optional[dict]:
    if not isinstance(stack, dict):
        return None
    item_id = stack.get("id")
    if not isinstance(item_id, str) or not _ITEM_KEY.fullmatch(item_id):
        return None
    try:
        count = int(stack.get("count", 1))
        damage = int(stack.get("damage", 0))
    except (TypeError, ValueError, OverflowError):
        return None
    if count <= 0:
        return None
    return {"id": item_id, "count": min(64, count), "damage": max(0, min(1_000_000, damage))}


def normalize_tile_entity_state(data: Any) -> Optional[dict]:
    if not isinstance(data, dict):
        return None
    te_type = data.get("type")
    if te_type not in _TILE_ENTITY_TYPES:
        return None
    try:
        x, y, z = int(data.get("x")), int(data.get("y")), int(data.get("z"))
    except (TypeError, ValueError, OverflowError):
        return None
    if not (-30_000_000 <= x <= 30_000_000
            and 0 <= y < 256
            and -30_000_000 <= z <= 30_000_000):
        return None

    result = {"type": te_type, "x": x, "y": y, "z": z}
    if te_type in {"chest", "furnace", "dispenser"}:
        slot_count = {"chest": 27, "furnace": 3, "dispenser": 9}[te_type]
        raw_items = data.get("items") if isinstance(data.get("items"), list) else []
        items = []
        seen_slots = set()
        for entry in raw_items[:slot_count * 2]:
            if not isinstance(entry, dict):
                continue
            try:
                slot = int(entry.get("slot", -1))
            except (TypeError, ValueError, OverflowError):
                continue
            if not (0 <= slot < slot_count) or slot in seen_slots:
                continue
            stack = _normalize_stack(entry)
            if stack is None:
                continue
            seen_slots.add(slot)
            items.append({"slot": slot, **stack})
        items.sort(key=lambda entry: entry["slot"])
        result["items"] = items
        if te_type == "furnace":
            try:
                result["burn"] = max(0, min(32767, int(data.get("burn", 0))))
                result["cook"] = max(0, min(200, int(data.get("cook", 0))))
                result["burn_total"] = max(0, min(32767, int(data.get("burn_total", 0))))
            except (TypeError, ValueError, OverflowError):
                return None
    elif te_type == "sign":
        raw_lines = data.get("text") if isinstance(data.get("text"), list) else []
        lines = []
        for index in range(4):
            value = raw_lines[index] if index < len(raw_lines) else ""
            if not isinstance(value, str):
                value = str(value or "")
            value = "".join(char for char in value if char >= " " and char not in "\r\n\x7f")
            lines.append(value[:15])
        result["text"] = lines
    elif te_type == "note_block":
        try:
            result["note"] = max(0, min(24, int(data.get("note", 0))))
        except (TypeError, ValueError, OverflowError):
            return None
    elif te_type == "mob_spawner":
        entity_id = str(data.get("entity_id") or "")[:32]
        if not re.fullmatch(r"[A-Za-z0-9_./-]{1,32}", entity_id):
            return None
        try:
            delay = max(-1, min(32767, int(data.get("delay", 20))))
        except (TypeError, ValueError, OverflowError):
            return None
        result.update({"entity_id": entity_id, "delay": delay})
    return result


def tile_entity_packet(data: Any) -> Optional[dict]:
    state = normalize_tile_entity_state(data)
    if state is None:
        return None
    return {"t": "tile_entity", **state}


def normalize_inventory_state(data: Any) -> dict:
    if not isinstance(data, dict):
        data = {}
    main_in = data.get("main") if isinstance(data.get("main"), list) else []
    armor_in = data.get("armor") if isinstance(data.get("armor"), list) else []
    main = [_normalize_stack(stack) for stack in main_in[:INVENTORY_MAIN_SIZE]]
    armor = [_normalize_stack(stack) for stack in armor_in[:INVENTORY_ARMOR_SIZE]]
    main.extend([None] * (INVENTORY_MAIN_SIZE - len(main)))
    armor.extend([None] * (INVENTORY_ARMOR_SIZE - len(armor)))
    try:
        selected = int(data.get("selected", 0))
    except (TypeError, ValueError, OverflowError):
        selected = 0
    result = {
        "main": main,
        "armor": armor,
        "selected": max(0, min(8, selected)),
        "cursor": _normalize_stack(data.get("cursor")),
    }
    craft = data.get("craft")
    if isinstance(craft, dict):
        try:
            size = int(craft.get("size", 2))
        except (TypeError, ValueError, OverflowError):
            size = 2
        size = 3 if size >= 3 else 2
        slots_in = craft.get("slots") if isinstance(craft.get("slots"), list) else []
        slots = [_normalize_stack(stack) for stack in slots_in[: size * size]]
        slots.extend([None] * (size * size - len(slots)))
        result["craft"] = {
            "size": size,
            "slots": slots,
            "result": _normalize_stack(craft.get("result")),
        }
    return result


def inventory_packet(player_id: int, state: Any, revision: int | None = None) -> dict:
    normalized = normalize_inventory_state(state)
    packet = {"t": "inventory", "id": int(player_id), **normalized}
    if revision is not None:
        packet["revision"] = max(0, int(revision))
    return packet


def inventory_transaction_request(
    sequence: int, target: str, slot: int, button: int, *, position=None,
    entity_id: int | None = None,
) -> dict:
    target = str(target)
    if target not in {"player", "chest", "furnace", "dispenser", "minecart", "craft2", "craft3"}:
        raise ValueError("unsupported inventory transaction target")
    sequence = int(sequence)
    slot = int(slot)
    button = int(button)
    if sequence <= 0 or not 0 <= slot < 64 or button not in (0, 1):
        raise ValueError("invalid inventory transaction")
    packet = {"t": "inventory_transaction", "seq": sequence,
              "target": target, "slot": slot, "button": button}
    if target in {"chest", "furnace", "dispenser"}:
        if not isinstance(position, (tuple, list)) or len(position) != 3:
            raise ValueError("container transaction requires a position")
        x, y, z = (int(value) for value in position)
        if not (-30_000_000 <= x <= 30_000_000 and 0 <= y < 256
                and -30_000_000 <= z <= 30_000_000):
            raise ValueError("invalid container position")
        packet.update({"x": x, "y": y, "z": z})
    elif target == "minecart":
        entity_id = int(entity_id or 0)
        if not 1 <= entity_id <= 2**31 - 1:
            raise ValueError("minecart transaction requires a valid entity ID")
        packet["entity_id"] = entity_id
    return packet


def entity_interact_request(entity_id: int, action: str) -> dict:
    entity_id = int(entity_id)
    action = str(action or "")
    if not 1 <= entity_id <= 2**31 - 1 or action not in {"furnace_fuel", "milk_cow", "milk_squid", "dye_sheep"}:
        raise ValueError("invalid entity interaction")
    return {"t": "entity_interact", "entity_id": entity_id, "action": action}


def entity_push_request(entity_id: int) -> dict:
    entity_id = int(entity_id)
    if not 1 <= entity_id <= 2**31 - 1:
        raise ValueError("invalid entity ID")
    return {"t": "entity_push", "entity_id": entity_id}


def inventory_drop_request(sequence: int, slot: int, count: int) -> dict:
    sequence = int(sequence)
    slot = int(slot)
    count = int(count)
    if sequence <= 0 or not 0 <= slot < INVENTORY_MAIN_SIZE + INVENTORY_ARMOR_SIZE:
        raise ValueError("invalid inventory drop slot or sequence")
    if not 1 <= count <= 64:
        raise ValueError("invalid inventory drop count")
    return {"t": "inventory_drop", "seq": sequence, "slot": slot, "count": count}




def bucket_use_request(action: str, x: int, y: int, z: int) -> dict:
    action = str(action or "")
    if action == "drink":
        return {"t": "bucket_use", "action": "drink"}
    if action not in {"pickup", "pour", "milk"}:
        raise ValueError("invalid bucket action")
    x, y, z = int(x), int(y), int(z)
    if not (-30_000_000 <= x <= 30_000_000 and 0 <= y < 256
            and -30_000_000 <= z <= 30_000_000):
        raise ValueError("invalid bucket target")
    return {"t": "bucket_use", "action": action, "x": x, "y": y, "z": z}


def item_use_request(action: str, x: int, y: int, z: int) -> dict:
    action = str(action or "")
    if action not in {"bonemeal", "cake_eat"}:
        raise ValueError("invalid item use action")
    x, y, z = int(x), int(y), int(z)
    if not (-30_000_000 <= x <= 30_000_000 and 0 <= y < 256
            and -30_000_000 <= z <= 30_000_000):
        raise ValueError("invalid item use target")
    return {"t": "item_use", "action": action, "x": x, "y": y, "z": z}


def jukebox_use_request(action: str, x: int, y: int, z: int) -> dict:
    action = str(action or "")
    if action not in {"insert", "eject"}:
        raise ValueError("invalid jukebox action")
    x, y, z = int(x), int(y), int(z)
    if not (-30_000_000 <= x <= 30_000_000 and 0 <= y < 256
            and -30_000_000 <= z <= 30_000_000):
        raise ValueError("invalid jukebox target")
    return {"t": "jukebox_use", "action": action, "x": x, "y": y, "z": z}


def note_block_use_request(action: str, x: int, y: int, z: int) -> dict:
    action = str(action or "")
    if action not in {"play", "pitch"}:
        raise ValueError("invalid note-block action")
    x, y, z = int(x), int(y), int(z)
    if not (-30_000_000 <= x <= 30_000_000 and 0 <= y < 256
            and -30_000_000 <= z <= 30_000_000):
        raise ValueError("invalid note-block target")
    return {"t": "note_block_use", "action": action, "x": x, "y": y, "z": z}


def boat_control_request(
    entity_id: int, riding: bool, forward: float = 0.0, strafe: float = 0.0, yaw: float = 0.0,
) -> dict:
    entity_id = int(entity_id)
    if not 1 <= entity_id <= 2**31 - 1:
        raise ValueError("invalid boat entity ID")
    return {
        "t": "boat_control", "entity_id": entity_id, "riding": bool(riding),
        "forward": max(-1.0, min(1.0, _finite_float(forward))),
        "strafe": max(-1.0, min(1.0, _finite_float(strafe))),
        "yaw": max(-3600.0, min(3600.0, _finite_float(yaw))),
    }


def animation_packet(player_id: int, action: str = "swing") -> dict:
    action = str(action or "swing")
    if action not in {"swing"}:
        action = "swing"
    return {"t": "anim", "id": int(player_id), "action": action}


def break_progress_packet(
    player_id: int, target: Optional[tuple[int, int, int]], progress: float = 0.0,
) -> dict:
    if target is None:
        return {"t": "break", "id": int(player_id), "active": False, "progress": 0.0}
    try:
        x, y, z = (int(value) for value in target)
    except (TypeError, ValueError, OverflowError):
        return {"t": "break", "id": int(player_id), "active": False, "progress": 0.0}
    value = _finite_float(progress)
    return {
        "t": "break", "id": int(player_id), "active": True,
        "x": x, "y": y, "z": z, "progress": max(0.0, min(0.99, value)),
    }


def hello(name: str) -> dict:
    return {"t": "hello", "proto": PROTO_VERSION, "name": str(name)[:16]}


def welcome(
    player_id: int,
    seed: int,
    x: float,
    y: float,
    z: float,
    yaw: float = 0.0,
    pitch: float = 0.0,
    players: Optional[list] = None,
    snow_covered: bool = False,
    world_time: int = 0,
    operator: bool = False,
    inventory_state: Any = None,
    inventory_revision: int = 0,
    accurate_generation: bool = True,
    debug_world: bool = False,
) -> dict:
    packet = {
        "t": "welcome",
        "id": int(player_id),
        "seed": int(seed),
        "op": bool(operator),
        "snow_covered": bool(snow_covered),
        "accurate_generation": bool(accurate_generation),
        "debug_world": bool(debug_world),
        "world_time": max(0, int(world_time)),
        "x": _finite_float(x),
        "y": _finite_float(y, 64.0),
        "z": _finite_float(z),
        "yaw": _finite_float(yaw),
        "pitch": _finite_float(pitch),
        "players": list(players or []),
    }
    if inventory_state is not None:
        packet["inventory"] = normalize_inventory_state(inventory_state)
        packet["inventory_revision"] = max(0, int(inventory_revision))
    return packet


def join(
    player_id: int, name: str, x: float, y: float, z: float,
    yaw: float = 0.0, pitch: float = 0.0, inventory_state: Any = None,
) -> dict:
    packet = {
        "t": "join",
        "id": int(player_id),
        "name": str(name)[:16],
        "x": _finite_float(x),
        "y": _finite_float(y, 64.0),
        "z": _finite_float(z),
        "yaw": _finite_float(yaw),
        "pitch": _finite_float(pitch),
    }
    if inventory_state is not None:
        packet["inventory"] = normalize_inventory_state(inventory_state)
    return packet


def leave(player_id: int) -> dict:
    return {"t": "leave", "id": int(player_id)}


def normalize_player_effect_state(data: Any) -> dict:
    if not isinstance(data, dict):
        data = {}

    def number(name, default, low, high):
        return max(low, min(high, _finite_float(data.get(name, default), default)))

    try:
        riding = bool(data.get("riding", False))
    except Exception:
        riding = False
    try:
        sneaking = bool(data.get("sneaking", False))
    except Exception:
        sneaking = False
    return {
        "health": number("health", 20.0, 0.0, 20.0),
        "hurt_time": number("hurt_time", 0.0, 0.0, 20.0),
        "max_hurt_time": number("max_hurt_time", 10.0, 1.0, 20.0),
        "death_time": number("death_time", 0.0, 0.0, 200.0),
        "fire_ticks": int(number("fire_ticks", 0.0, -20.0, 600.0)),
        "attacked_at_yaw": number("attacked_at_yaw", 0.0, -3600.0, 3600.0),
        "body_yaw": number("body_yaw", data.get("yaw", 0.0), -3600.0, 3600.0),
        "limb_swing": number("limb_swing", 0.0, -1_000_000.0, 1_000_000.0),
        "limb_swing_amount": number("limb_swing_amount", 0.0, 0.0, 1.0),
        "riding": riding,
        "sneaking": sneaking,
    }


def pos(
    player_id: int, x: float, y: float, z: float, yaw: float, pitch: float,
    state: Any = None,
) -> dict:
    packet = {
        "t": "pos",
        "id": int(player_id),
        "x": _finite_float(x),
        "y": _finite_float(y, 64.0),
        "z": _finite_float(z),
        "yaw": _finite_float(yaw),
        "pitch": _finite_float(pitch),
    }
    if state is not None:
        packet.update(normalize_player_effect_state(state))
    return packet


def chat(name: str, msg: str) -> dict:
    return {"t": "chat", "from": str(name)[:16], "msg": str(msg)[:200]}


def command_request(text: str) -> dict:
    return {"t": "command", "command": str(text or "")[:200]}


def private_message(sender: str, target: str, message: str) -> dict:
    return {
        "t": "private_message",
        "from": str(sender)[:16],
        "to": str(target)[:16],
        "msg": str(message)[:200],
    }


def command_reply(message: str) -> dict:
    return {"t": "command_reply", "msg": str(message)[:200]}


def teleport(x: float, y: float, z: float, yaw: float = 0.0, pitch: float = 0.0) -> dict:
    return {
        "t": "teleport",
        "x": _finite_float(x), "y": _finite_float(y, 64.0), "z": _finite_float(z),
        "yaw": _finite_float(yaw), "pitch": max(-90.0, min(90.0, _finite_float(pitch))),
    }


def op_status(enabled: bool) -> dict:
    return {"t": "op_status", "op": bool(enabled)}


def kick(reason: str) -> dict:
    return {"t": "kick", "reason": str(reason)[:200]}


def disconnect(reason: str = "Client disconnected") -> dict:
    return {"t": "disconnect", "reason": str(reason)[:200]}


def attack_request(entity_id: int, damage: int) -> dict:
    return {"t": "attack", "entity_id": int(entity_id), "damage": int(damage)}


def player_attack_request(target_player_id: int) -> dict:
    return {"t": "player_attack", "target_id": int(target_player_id)}


KILL_DAMAGE = 1000
_PLAYER_DAMAGE_SOURCES = {"monster", "player", "arrow", "explosion", "generic", "kill"}


def player_damage_packet(
    player_id: int, amount: int, attacker_id: int = 0, source: str = "monster",
    knockback=None,
) -> dict:
    source = str(source or "monster")
    if source not in _PLAYER_DAMAGE_SOURCES:
        source = "generic"
    packet = {
        "t": "player_damage",
        "id": int(player_id),
        "amount": max(1, min(KILL_DAMAGE if source == "kill" else 20, int(amount))),
        "attacker_id": max(0, int(attacker_id)),
    }
    if source != "monster":
        packet["source"] = source
    if isinstance(knockback, (list, tuple)) and len(knockback) == 3:
        packet["knockback"] = [
            max(-8.0, min(8.0, _finite_float(value))) for value in knockback
        ]
    return packet


def tnt_ignite_request(x: int, y: int, z: int) -> dict:
    return {"t": "tnt_ignite_request", "x": int(x), "y": int(y), "z": int(z)}


def respawn_request() -> dict:
    return {"t": "respawn"}


def player_respawn_packet(player_id: int, x: float, y: float, z: float) -> dict:
    return {
        "t": "respawn",
        "id": int(player_id),
        "x": _finite_float(x),
        "y": _finite_float(y, 64.0),
        "z": _finite_float(z),
    }


def block_update(x: int, y: int, z: int, block_type: Optional[str], meta: int = 0) -> dict:
    return {
        "t": "block",
        "x": int(x),
        "y": int(y),
        "z": int(z),
        "block": None if block_type is None else str(block_type)[:64],
        "meta": int(meta) & 15,
    }



def chunk_request(cx: int, cz: int) -> dict:
    return {"t": "chunk_request", "cx": int(cx), "cz": int(cz)}


def chunk_snapshot_packet(
    cx: int, cz: int, blocks, metadata, palette,
    width: int = 16, height: int = 128, depth: int = 16,
    populated: bool = True, skylight=None, blocklight=None, heightmap=None,
    tile_entities=None,
) -> dict:
    width, height, depth = int(width), int(height), int(depth)
    if not (1 <= width <= 32 and 1 <= depth <= 32 and 1 <= height <= 256):
        raise ValueError("Invalid chunk dimensions")
    volume = width * height * depth
    block_bytes = bytes(blocks)
    meta_bytes = bytes(metadata)
    sky_bytes = bytes(skylight) if skylight is not None else bytes(volume)
    blocklight_bytes = bytes(blocklight) if blocklight is not None else bytes(volume)
    height_bytes = bytes(heightmap) if heightmap is not None else bytes(width * depth)
    if (len(block_bytes) != volume or len(meta_bytes) != volume
            or len(sky_bytes) != volume or len(blocklight_bytes) != volume
            or len(height_bytes) != width * depth):
        raise ValueError("Chunk state buffer has an invalid size")
    if len(palette) > 256:
        raise ValueError("Chunk palette exceeds compact block ID limit")
    safe_palette = []
    for name in palette:
        if name is None:
            safe_palette.append(None)
            continue
        value = str(name)
        if not _ITEM_KEY.fullmatch(value):
            raise ValueError("Invalid block name in chunk palette")
        safe_palette.append(value)
    raw = block_bytes + meta_bytes + sky_bytes + blocklight_bytes + height_bytes
    if len(raw) > MAX_CHUNK_BYTES:
        raise ValueError("Chunk payload exceeds transfer limit")
    compressed = zlib.compress(raw, level=3)
    normalized_tile_entities = []
    for entry in tile_entities or ():
        state = normalize_tile_entity_state(entry)
        if state is None:
            continue
        if (math.floor((state["x"] + 0.5) / width) != int(cx)
                or math.floor((state["z"] + 0.5) / depth) != int(cz)):
            continue
        normalized_tile_entities.append(state)
        if len(normalized_tile_entities) >= MAX_TILE_ENTITY_COUNT:
            break
    return {
        "t": "chunk", "cx": int(cx), "cz": int(cz),
        "w": width, "h": height, "d": depth,
        "palette": safe_palette,
        "data": base64.b64encode(compressed).decode("ascii"),
        "raw_len": len(raw),
        "populated": bool(populated),
        "tile_entities": normalized_tile_entities,
    }


def decode_chunk_snapshot(packet: dict, max_raw_bytes: int = MAX_CHUNK_BYTES):
    if not isinstance(packet, dict) or packet.get("t") != "chunk":
        raise ValueError("Not a chunk snapshot")
    try:
        cx, cz = int(packet["cx"]), int(packet["cz"])
        width, height, depth = int(packet["w"]), int(packet["h"]), int(packet["d"])
        raw_len = int(packet["raw_len"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid chunk snapshot header") from exc
    if not (1 <= width <= 32 and 1 <= depth <= 32 and 1 <= height <= 256):
        raise ValueError("Invalid chunk dimensions")
    volume = width * height * depth
    expected = volume * 4 + width * depth
    if expected > int(max_raw_bytes) or raw_len != expected:
        raise ValueError("Invalid chunk payload length")
    palette = packet.get("palette")
    if not isinstance(palette, list) or not 1 <= len(palette) <= 256 or palette[0] is not None:
        raise ValueError("Invalid chunk palette")
    safe_palette = []
    for name in palette:
        if name is None:
            safe_palette.append(None)
        elif isinstance(name, str) and _ITEM_KEY.fullmatch(name):
            safe_palette.append(name)
        else:
            raise ValueError("Invalid block name in chunk palette")
    payload = packet.get("data")
    if not isinstance(payload, str) or len(payload) > 450_000:
        raise ValueError("Invalid chunk data field")
    try:
        compressed = base64.b64decode(payload.encode("ascii"), validate=True)
        inflater = zlib.decompressobj()
        raw = inflater.decompress(compressed, expected + 1)
    except (UnicodeEncodeError, ValueError, zlib.error) as exc:
        raise ValueError("Invalid compressed chunk data") from exc
    if (len(raw) != expected or not inflater.eof
            or inflater.unconsumed_tail or inflater.unused_data):
        raise ValueError("Chunk decompressed size mismatch")
    blocks = raw[:volume]
    metadata = raw[volume:volume * 2]
    skylight = raw[volume * 2:volume * 3]
    blocklight = raw[volume * 3:volume * 4]
    heightmap = raw[volume * 4:]
    if any(meta > 15 for meta in metadata):
        raise ValueError("Invalid block metadata value")
    if any(value > 15 for value in skylight) or any(value > 15 for value in blocklight):
        raise ValueError("Invalid chunk light value")
    if any(block_id >= len(safe_palette) for block_id in blocks):
        raise ValueError("Chunk references a missing palette entry")
    raw_tile_entities = packet.get("tile_entities", [])
    if not isinstance(raw_tile_entities, list) or len(raw_tile_entities) > MAX_TILE_ENTITY_COUNT:
        raise ValueError("Invalid chunk tile-entity list")
    tile_entities = []
    for raw_state in raw_tile_entities:
        state = normalize_tile_entity_state(raw_state)
        if state is None:
            raise ValueError("Invalid tile entity in chunk snapshot")
        if (math.floor((state["x"] + 0.5) / width) != cx
                or math.floor((state["z"] + 0.5) / depth) != cz):
            raise ValueError("Chunk tile entity is outside its chunk")
        tile_entities.append(state)
    return {
        "cx": cx, "cz": cz, "width": width, "height": height, "depth": depth,
        "palette": safe_palette, "blocks": blocks, "metadata": metadata,
        "skylight": skylight, "blocklight": blocklight, "heightmap": heightmap,
        "populated": bool(packet.get("populated", True)),
        "tile_entities": tile_entities,
    }

def normalize_entity_state(data: Any) -> Optional[dict]:
    if not isinstance(data, dict):
        return None
    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in _ENTITY_KINDS:
        return None
    try:
        entity_id = int(data.get("id"))
    except (TypeError, ValueError, OverflowError):
        return None
    if entity_id <= 0 or entity_id > 2**31 - 1:
        return None

    def number(name, default=0.0, low=-30_000_000.0, high=30_000_000.0):
        return max(low, min(high, _finite_float(data.get(name, default), default)))

    def integer(name, default=0, low=-30_000_000, high=30_000_000):
        try:
            value = int(data.get(name, default))
        except (TypeError, ValueError, OverflowError):
            value = int(default)
        return max(low, min(high, value))

    result = {
        "id": entity_id,
        "kind": kind,
        "x": number("x"), "y": number("y", 64.0, -1024.0, 4096.0), "z": number("z"),
        "vx": number("vx", 0.0, -20.0, 20.0),
        "vy": number("vy", 0.0, -20.0, 20.0),
        "vz": number("vz", 0.0, -20.0, 20.0),
        "yaw": number("yaw", 0.0, -3600.0, 3600.0),
        "pitch": number("pitch", 0.0, -180.0, 180.0),
        "health": number("health", 10.0, 0.0, 1000.0),
        "max_health": number("max_health", 10.0, 1.0, 1000.0),
        "hurt_time": number("hurt_time", 0.0, 0.0, 40.0),
        "death_time": number("death_time", 0.0, 0.0, 200.0),
        "fire_ticks": int(number("fire_ticks", 0.0, -20.0, 600.0)),
        "age": number("age", 0.0, 0.0, 1_000_000.0),
        "ticks_existed": int(number("ticks_existed", 0.0, 0.0, 2_000_000.0)),
        "limb_swing": number("limb_swing", 0.0, -1_000_000.0, 1_000_000.0),
        "limb_swing_amount": number("limb_swing_amount", 0.0, 0.0, 1.0),
        "rotation_pitch": number("rotation_pitch", 0.0, -180.0, 180.0),
        "attacked_at_yaw": number("attacked_at_yaw", 0.0, -3600.0, 3600.0),
    }
    if kind == "tnt":
        result["fuse"] = number("fuse", 80.0, 0.0, 1200.0)
    elif kind == "item":
        stack = _normalize_stack(data.get("item"))
        if stack is None:
            return None
        result["item"] = stack
        result["pickup_delay"] = number("pickup_delay", 0.0, 0.0, 1200.0)
        result["hover_start"] = number("hover_start", 0.0, -10000.0, 10000.0)
        result["pickup"] = bool(data.get("pickup", False))
    elif kind in _MOB_ENTITY_KINDS:
        result["slime_size"] = integer("slime_size", 1, 1, 4)
        result["saddled"] = bool(data.get("saddled", False))
        result["sheared"] = bool(data.get("sheared", False))
        if kind == "sheep":
            result["fleece_color"] = integer("fleece_color", 0, 0, 15)
        result["creeper_state"] = integer("creeper_state", -1, -1, 2)
        result["time_since_ignited"] = integer("time_since_ignited", 0, 0, 100)
        result["wing_rotation"] = number("wing_rotation", 0.0, -10000.0, 10000.0)
        result["prev_wing_rotation"] = number(
            "prev_wing_rotation", result["wing_rotation"], -10000.0, 10000.0,
        )
        result["dest_pos"] = number("dest_pos", 0.0, 0.0, 1.0)
        result["prev_dest_pos"] = number("prev_dest_pos", result["dest_pos"], 0.0, 1.0)
        if kind == "ghast":
            result["charge"] = integer("charge", 0, -40, 20)
            result["prev_charge"] = integer("prev_charge", result["charge"], -40, 20)
        elif kind == "squid":
            result["swim_phase"] = number("swim_phase", 0.0, -10000.0, 10000.0)
            result["prev_swim_phase"] = number("prev_swim_phase", result["swim_phase"], -10000.0, 10000.0)
            result["body_yaw_animation"] = number("body_yaw_animation", 0.0, -100000.0, 100000.0)
            result["prev_body_yaw_animation"] = number("prev_body_yaw_animation", result["body_yaw_animation"], -100000.0, 100000.0)
            result["tentacle_angle"] = number("tentacle_angle", 0.0, -10.0, 10.0)
            result["prev_tentacle_angle"] = number("prev_tentacle_angle", result["tentacle_angle"], -10.0, 10.0)
    elif kind == "arrow":
        result.update({
            "in_ground": bool(data.get("in_ground", False)),
            "x_tile": integer("x_tile", -1),
            "y_tile": integer("y_tile", -1),
            "z_tile": integer("z_tile", -1),
            "in_tile": (
                data.get("in_tile") if isinstance(data.get("in_tile"), str)
                and _ITEM_KEY.fullmatch(data.get("in_tile")) else None
            ),
            "arrow_shake": number("arrow_shake", 0.0, 0.0, 1200.0),
            "ticks_in_ground": number("ticks_in_ground", 0.0, 0.0, 1_000_000.0),
            "ticks_in_air": number("ticks_in_air", 0.0, 0.0, 1_000_000.0),
            "fire": number("fire", 0.0, 0.0, 1200.0),
        })
    elif kind in {"snowball", "egg"}:
        result["ticks_in_air"] = number("ticks_in_air", 0.0, 0.0, 1200.0)
    elif kind == "painting":
        art = data.get("art", "Kebab")
        if not isinstance(art, str) or art not in _PAINTING_ARTS:
            return None
        result.update({
            "tile_x": integer("tile_x"),
            "tile_y": integer("tile_y"),
            "tile_z": integer("tile_z"),
            "direction": integer("direction", 0, 0, 3),
            "art": art,
        })
    elif kind == "boat":
        result.update({
            "damage_taken": number("damage_taken", 0.0, 0.0, 1000.0),
            "time_since_hit": number("time_since_hit", 0.0, 0.0, 40.0),
            "forward_direction": integer("forward_direction", 1, -1, 1),
        })
    elif kind == "minecart":
        result.update({
            "cart_type": integer("cart_type", 0, 0, 2),
            "damage_taken": number("damage_taken", 0.0, 0.0, 1000.0),
            "time_since_hit": number("time_since_hit", 0.0, 0.0, 40.0),
            "forward_direction": integer("forward_direction", 1, -1, 1),
            "reverse": bool(data.get("reverse", False)),
            "fuel": integer("fuel", 0, 0, 12000),
            "push_x": number("push_x", 0.0, -20.0, 20.0),
            "push_z": number("push_z", 0.0, -20.0, 20.0),
        })
        cargo = data.get("cargo", [])
        if not isinstance(cargo, list) or len(cargo) > 27:
            return None
        safe_cargo = []
        used_slots = set()
        for entry in cargo:
            if not isinstance(entry, dict):
                return None
            try:
                slot = int(entry.get("slot"))
            except (TypeError, ValueError, OverflowError):
                return None
            stack = _normalize_stack(entry)
            if stack is None or not 0 <= slot < 27 or slot in used_slots:
                return None
            used_slots.add(slot)
            safe_cargo.append({"slot": slot, **stack})
        result["cargo"] = safe_cargo
    elif kind == "falling_block":
        block_type = data.get("block_type")
        if not isinstance(block_type, str) or not _ITEM_KEY.fullmatch(block_type):
            return None
        result.update({
            "block_type": block_type,
            "meta": integer("meta", 0, 0, 15),
            "fall_time": number("fall_time", 0.0, 0.0, 10000.0),
        })
    elif kind == "fireball":
        result.update({
            "ax": number("ax", 0.0, -1.0, 1.0),
            "ay": number("ay", 0.0, -1.0, 1.0),
            "az": number("az", 0.0, -1.0, 1.0),
            "ticks_in_air": number("ticks_in_air", 0.0, 0.0, 1_000_000.0),
            "in_ground": bool(data.get("in_ground", False)),
            "in_water": bool(data.get("in_water", False)),
            "been_attacked": bool(data.get("been_attacked", False)),
            "x_tile": integer("x_tile", -1),
            "y_tile": integer("y_tile", -1),
            "z_tile": integer("z_tile", -1),
            "in_tile": (
                data.get("in_tile") if isinstance(data.get("in_tile"), str)
                and _ITEM_KEY.fullmatch(data.get("in_tile")) else None
            ),
        })
    elif kind == "fishing_bobber":
        result.update({
            "angler_id": integer("angler_id", 0, 0, 2**31 - 1),
            "bite_ticks": integer("bite_ticks", 0, 0, 100),
            "in_ground": bool(data.get("in_ground", False)),
            "x_tile": integer("x_tile", -1),
            "y_tile": integer("y_tile", -1),
            "z_tile": integer("z_tile", -1),
            "in_tile": (
                data.get("in_tile") if isinstance(data.get("in_tile"), str)
                and _ITEM_KEY.fullmatch(data.get("in_tile")) else None
            ),
            "ticks_in_air": number("ticks_in_air", 0.0, 0.0, 1_000_000.0),
            "ticks_in_ground": number("ticks_in_ground", 0.0, 0.0, 1_000_000.0),
        })
    return result


def entity_spawn_request(entity_state: Any) -> Optional[dict]:
    state = normalize_entity_state(entity_state)
    if state is None or state["kind"] not in _ENTITY_SPAWN_REQUEST_KINDS:
        return None
    return {"t": "entity_spawn", "entity": state}


def entity_sync_packet(entities: Any) -> dict:
    if not isinstance(entities, (list, tuple)):
        entities = []
    normalized = []
    encoded_size = len(b'{"t":"entity_sync","entities":[]}') - 2
    for entry in entities:
        state = normalize_entity_state(entry)
        if state is None:
            continue
        entry_size = len(json.dumps(
            state, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8"))
        candidate_size = encoded_size + entry_size + (1 if normalized else 0)
        if candidate_size > MAX_LINE - 1:
            break
        normalized.append(state)
        encoded_size = candidate_size
        if len(normalized) >= MAX_ENTITY_COUNT:
            break
    return {"t": "entity_sync", "entities": normalized}


def player_snapshot(
    player_id: int, name: str, x: float, y: float, z: float, yaw: float, pitch: float,
    inventory_state: Any = None, break_state: Any = None, state: Any = None,
) -> dict:
    packet = {
        "id": int(player_id),
        "name": str(name)[:16],
        "x": _finite_float(x),
        "y": _finite_float(y, 64.0),
        "z": _finite_float(z),
        "yaw": _finite_float(yaw),
        "pitch": _finite_float(pitch),
    }
    if inventory_state is not None:
        packet["inventory"] = normalize_inventory_state(inventory_state)
    if state is not None:
        packet.update(normalize_player_effect_state(state))
    if isinstance(break_state, dict):
        packet["break_target"] = break_state.get("target")
        packet["break_progress"] = _finite_float(break_state.get("progress", 0.0))
    return packet
