
from __future__ import annotations

import math
import random
from pathlib import Path

from game.entities.entity import Entity

HOAX_SCREENSHOT_SEED = 478868574082066804
SPAWN_CHANCE_DENOMINATOR = 512

_NEAR_DISTANCE_SQ = 8.0 * 8.0
_CREAKING_NEAR_DISTANCE_SQ = 4.0 * 4.0  
_MAX_CHARGE_DISTANCE_SQ = 64.0 * 64.0
_MAX_JUMPSCARE_DISTANCE_SQ = 22.0 * 22.0
_MAX_CREAKING_DISTANCE_SQ = 48.0 * 48.0
_LOOK_DOT_THRESHOLD = 0.72
_LOOK_THREAT_DOT = 0.38
_CHARGE_LOOK_DOT = 0.90  
_CHARGE_LOOK_TICKS = 14  
_NULL_SKIN_CHANCE_DENOMINATOR = 8
_JUMPSCARE_STOP_DISTANCE = 2.6
_JUMPSCARE_APPROACH_SPEED = 0.55
_JUMPSCARE_DODGE_SPEED = 1.8
_CHARGE_SPEED = 7.5
_CREAKING_SPEED = 0.85
_MUSIC_FADE_DISTANCE = 28.0
_RESPAWN_COOLDOWN_TICKS = 600
_RETRY_SPAWN_TICKS = 100
_INTERACT_INTERVAL_TICKS = 35
_LEAF_STRIP_INTERVAL_TICKS = 55
_MODES = ("stalk", "jumpscare", "charge", "creaking")

DEFAULT_CONFIG = {
    "force_enable": False,       
    "spawn_odds": 512,           
    "spawn_cooldown": 30,        
    "mode_stalk": True,
    "mode_jumpscare": True,
    "mode_charge": True,
    "mode_creaking": True,
    "act_doors": True,           
    "act_leaves": True,          
    "act_footsteps": True,
    "act_music_fade": True,
    "act_glowing_eyes": True,
    "skin_null": True,           
    "skin_copy_player": True,    
    "prop_torch": True,          
    "prop_sign": True,
    "prop_jukebox": True,
    "prop_damage": True,
}
_NUMERIC_LIMITS = {"spawn_odds": (1, 2048), "spawn_cooldown": (0, 600)}
CONFIG = dict(DEFAULT_CONFIG)
_CONFIG_REVISION = 0


def configure(values=None):
    global _CONFIG_REVISION
    values = values if isinstance(values, dict) else {}
    cleaned = dict(DEFAULT_CONFIG)
    for key, default in DEFAULT_CONFIG.items():
        value = values.get(key, default)
        if key in _NUMERIC_LIMITS:
            low, high = _NUMERIC_LIMITS[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value):
                value = default
            cleaned[key] = int(round(max(low, min(high, value))))
        else:
            cleaned[key] = value if isinstance(value, bool) else default
    CONFIG.clear()
    CONFIG.update(cleaned)
    _CONFIG_REVISION += 1
    return CONFIG


_OWN_SKIN_PATH = Path(__file__).resolve().parent / "char.png"
_own_skin_texture = None


def _get_own_skin_texture():
    global _own_skin_texture
    if _own_skin_texture is None:
        try:
            import pygame as pg
            from engine.textures import Texture

            _own_skin_texture = Texture(pg.image.load(str(_OWN_SKIN_PATH)).convert_alpha())
        except Exception as error:
            print(f"Herobrine: unable to load own char.png ({error}); using shared skin")
            return None
    return _own_skin_texture

def _respawn_cooldown_ticks() -> int:
    return int(CONFIG["spawn_cooldown"]) * 20

_FLUIDS = frozenset({"water", "flowing_water", "lava", "flowing_lava"})
_NON_SOLID = frozenset({
    "air", "water", "flowing_water", "lava", "flowing_lava", "torch",
    "redstone_torch", "unlit_redstone_torch", "ladder", "redstone_wire",
    "tall_grass", "dead_bush", "flower", "yellow_flower", "red_flower",
    "rose", "dandelion", "snow", "fire", "sign", "wall_sign",
})
_INTERACTABLES = frozenset({"wooden_door", "iron_door", "lever", "stone_button"})

_SIGN_MESSAGES = (
    ("He is watching", "", "", ""),
    ("Herobrine", "", "", ""),
    ("I'm here", "", "", ""),
    ("Stop", "", "", ""),
    ("You cannot", "hide", "", ""),
    ("null", "null", "null", "null"),
    ("Wake up", "", "", ""),
    ("err.type =", "null.hello", "", ""),
    ("", "?", "", ""),
)


def _block_defs():
    try:
        from game.block import Block
        Block._load_defs()
        return Block._block_defs
    except Exception:
        return {}


def _is_solid(definitions, block_type) -> bool:
    if block_type is None or block_type in _NON_SOLID:
        return False
    definition = definitions.get(block_type, {})
    return bool(definition.get("collision", True))


def _can_see_sky(world, x, y, z) -> bool:
    try:
        from game.block.support_rules import can_see_sky
        return bool(can_see_sky(world, x, y, z))
    except Exception:
        return True


def _light_at(world, x, y, z) -> int:
    get_light = getattr(world, "get_light", None)
    if not callable(get_light):
        return 15
    try:
        return int(get_light(x, y, z))
    except Exception:
        return 15


def _player_in_cave(world, player) -> bool:
    px = int(math.floor(float(player.pos[0]) + 0.5))
    py = int(math.floor(float(player.pos[1]) + 0.5))
    pz = int(math.floor(float(player.pos[2]) + 0.5))
    eye_y = int(math.floor(float(player.pos[1]) + float(getattr(player, "EYE_HEIGHT", 1.62))))
    if _can_see_sky(world, px, eye_y, pz) or _can_see_sky(world, px, py + 1, pz):
        return False
    light = _light_at(world, px, eye_y, pz)
    if light <= 6:
        return True
    try:
        chunks = getattr(world, "chunks", None)
        get_index = getattr(world, "_get_chunk_index", None)
        if isinstance(chunks, dict) and callable(get_index):
            chunk = chunks.get(get_index(px, pz))
            if chunk is not None:
                lx = px - chunk.start_x
                lz = pz - chunk.start_z
                if 0 <= lx < chunk.width and 0 <= lz < chunk.depth:
                    top = int(chunk.heightmap[lz * chunk.width + lx])
                    if py <= top - 8 and light <= 10:
                        return True
    except Exception:
        pass
    return False


def _chunk_loaded(world, x, z) -> bool:
    chunks = getattr(world, "chunks", None)
    get_index = getattr(world, "_get_chunk_index", None)
    if not isinstance(chunks, dict) or not callable(get_index):
        return True
    return get_index(x, z) in chunks


def _render_edge_distance(world) -> float:
    view = max(2, int(getattr(world, "view_distance", 4) or 4))
    chunk = int(getattr(world, "CHUNK_SIZE", 16) or 16)
    return float(view * chunk)


def _standing_space(world, definitions, x, y_ground, z) -> bool:
    ground = world.get_block_type(x, y_ground, z)
    if not _is_solid(definitions, ground) or ground in _FLUIDS or ground == "fire":
        return False
    feet = world.get_block_type(x, y_ground + 1, z)
    head = world.get_block_type(x, y_ground + 2, z)
    if feet in _FLUIDS or head in _FLUIDS:
        return False
    if _is_solid(definitions, feet) or _is_solid(definitions, head):
        return False
    return True


def _occlusion_hits(world, definitions, ax, ay, az, bx, by, bz) -> int:
    dx, dy, dz = bx - ax, by - ay, bz - az
    steps = max(1, int(math.ceil(math.hypot(dx, dy, dz))))
    hits = 0
    for i in range(1, steps):
        t = i / steps
        x = int(math.floor(ax + dx * t + 0.5))
        y = int(math.floor(ay + dy * t + 0.5))
        z = int(math.floor(az + dz * t + 0.5))
        if _is_solid(definitions, world.get_block_type(x, y, z)):
            hits += 1
    return hits


def _adjacent_cover_score(world, definitions, x, y_feet, z) -> int:
    score = 0
    for ox, oz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        for dy in (0, 1, 2):
            if _is_solid(definitions, world.get_block_type(x + ox, y_feet + dy, z + oz)):
                score += 1
    return score


def _scan_ground_y(world, definitions, x, z, center_y, y_up=10, y_down=24):
    height = int(getattr(world, "CHUNK_HEIGHT", 128))
    y_start = min(height - 3, center_y + y_up)
    y_end = max(1, center_y - y_down)
    for y in range(y_start, y_end - 1, -1):
        if _standing_space(world, definitions, x, y, z):
            return y
    return None


class Herobrine(Entity):
    is_living_entity = True
    EYE_HEIGHT = 1.62
    JUMP_MOTION = 0.42

    def __init__(self, world, pos, mode: str = "stalk"):
        super().__init__(list(pos), size=(0.6, 1.8, 0.6))
        self.world = world
        self.renderer = getattr(world, "renderer", None)
        self._living_fluid_physics = True
        self.skin_path = "char.png"
        self.skin_username = None
        self.skin_texture = _get_own_skin_texture()
        self._own_skin = self.skin_texture is not None
        self.username = "Herobrine"
        self.yaw = 0.0
        self.pitch = 0.0
        self.prev_yaw = 0.0
        self.prev_pitch = 0.0
        self.render_yaw_offset = 0.0
        self.prev_render_yaw_offset = 0.0
        self.limb_swing = 0.0
        self.prev_limb_swing = 0.0
        self.limb_swing_amount = 0.0
        self.prev_limb_swing_amount = 0.0
        self.hurt_time = 0.0
        self.death_time = 0.0
        self.health = 20
        self.ticks_existed = 0
        self._cave_sound_played = False
        self._look_stare_ticks = 0
        self._step_distance = 0.0
        self._next_interact_tick = 20
        self._next_leaf_tick = 40
        self._charging = False
        self._charge_look_ticks = 0
        self._music_suppressed = False
        mode_name = str(mode or "stalk").strip().lower()
        self.mode = mode_name if mode_name in _MODES else "stalk"
        self._maybe_copy_player_skin()
        skin = str(getattr(self, "skin_path", "") or "").replace("\\", "/").lower()
        self._glowing_eyes = bool(CONFIG["act_glowing_eyes"]) and (self._own_skin or (
            self.skin_texture is None and not self.skin_username
            and skin.endswith("char.png")
        ))
        print(f"at {self.pos[0]}, {self.pos[1]}, {self.pos[2]} using {self.mode}")


    def _maybe_copy_player_skin(self):
        player = getattr(self.world, "player", None)
        if player is None:
            return
        rng = random.Random(
            (int(getattr(self.world, "seed", 0)) ^ int(getattr(self.world, "world_time", 0)))
            & 0xFFFFFFFF
        )
        if rng.randrange(_NULL_SKIN_CHANCE_DENOMINATOR) == 0 and CONFIG["skin_null"]:
            self.skin_path = "title/black.png"
            self.skin_texture = None
            self._own_skin = False
            return
        if rng.randrange(4) != 0 or not CONFIG["skin_copy_player"]:
            return
        username = getattr(player, "skin_username", None) or getattr(player, "username", None)
        path = getattr(player, "skin_path", None)
        if username:
            self.skin_username = str(username)
        if path:
            self.skin_path = str(path)
        self.skin_texture = None
        self._own_skin = False

    def attack_entity_from(self, attacker=None, amount=1) -> bool:
        return False

    def damage(self, *_args, **_kwargs) -> bool:
        return False

    def get_entity_brightness(self):
        return super().get_entity_brightness()

    def _look_dot(self, player, dx, dy, dz, distance_sq) -> float:
        if distance_sq < 1e-6:
            return 1.0
        inv = 1.0 / math.sqrt(distance_sq)
        to_x, to_y, to_z = -dx * inv, -dy * inv, -dz * inv
        yaw = math.radians(float(getattr(player, "yaw", 0.0)))
        pitch = math.radians(float(getattr(player, "pitch", 0.0)))
        look_x = -math.sin(yaw) * math.cos(pitch)
        look_y = -math.sin(pitch)
        look_z = -math.cos(yaw) * math.cos(pitch)
        return look_x * to_x + look_y * to_y + look_z * to_z

    def _player_looking_at_me(self, player, dx, dy, dz, distance_sq) -> bool:
        return self._look_dot(player, dx, dy, dz, distance_sq) >= _LOOK_DOT_THRESHOLD

    def _play_sound(self, name, volume=1.0, pitch=1.0, position=None):
        sound_player = getattr(self.world, "sound_player", None)
        if not callable(sound_player):
            return
        try:
            sound_player(
                name, volume=volume, pitch=pitch,
                position=tuple(position if position is not None else self.pos),
            )
        except Exception:
            pass

    def _play_footstep(self):
        world = self.world
        if world is None:
            return
        bx = int(math.floor(float(self.pos[0]) + 0.5))
        by = int(math.floor(float(self.pos[1]) - 0.2 + 0.5))
        bz = int(math.floor(float(self.pos[2]) + 0.5))
        block_type = world.get_block_type(bx, by, bz)
        if block_type is None:
            return
        try:
            from game.block.material import material_for_block, step_sound_for_block
            material = material_for_block(block_type)
            if material.is_liquid:
                return
            step_sound = step_sound_for_block(block_type)
            self._play_sound(
                f"step.{step_sound.name}",
                volume=step_sound.volume * 0.15,
                pitch=step_sound.pitch,
            )
        except Exception:
            self._play_sound("step.grass", volume=0.15, pitch=1.0)

    def _update_music_fade(self, distance: float, *, active: bool):
        active = active and bool(CONFIG["act_music_fade"])
        music = getattr(self.world, "record_music_player", None)
        if music is None:
            return
        if not active:
            if self._music_suppressed:
                music.ambient_gain = 1.0
                self._music_suppressed = False
            return
        if distance >= _MUSIC_FADE_DISTANCE:
            gain = 1.0
        else:
            gain = max(0.0, distance / _MUSIC_FADE_DISTANCE) ** 1.5
        music.ambient_gain = gain
        self._music_suppressed = gain < 0.99

    def _leave_prop(self, world, player=None, rng=None):
        rng = rng or random.Random()
        x = int(math.floor(float(self.pos[0]) + 0.5))
        y = int(math.floor(float(self.pos[1]) + 0.5))
        z = int(math.floor(float(self.pos[2]) + 0.5))
        height = int(getattr(world, "CHUNK_HEIGHT", 128))
        if not (0 <= y < height):
            return
        ground_y = y - 1
        for _ in range(4):
            below = world.get_block_type(x, ground_y, z)
            if below is not None and below not in _NON_SOLID:
                break
            ground_y -= 1
            if ground_y < 1:
                return

        weights = [
            (name, weight) for name, weight in (
                ("nothing", 4), ("torch", 3), ("sign", 2), ("jukebox", 2), ("damage", 2),
            ) if name == "nothing" or CONFIG[f"prop_{name}"]
        ]
        total = sum(w for _, w in weights)
        pick = rng.randrange(total)
        choice = "nothing"
        for name, weight in weights:
            pick -= weight
            if pick < 0:
                choice = name
                break

        try:
            if choice == "torch":
                cell = world.get_block_type(x, ground_y + 1, z)
                if cell in (None, "air", "tall_grass", "dead_bush", "snow"):
                    world.set_block(
                        x, ground_y + 1, z, "redstone_torch", meta=5, immediate_mesh=True,
                    )
            elif choice == "sign":
                cell = world.get_block_type(x, ground_y + 1, z)
                if cell in (None, "air", "tall_grass", "dead_bush", "snow"):
                    meta = int(math.floor(
                        (float(self.yaw) + 180.0) * 16.0 / 360.0 - 0.5
                    )) & 15
                    world.set_block(
                        x, ground_y + 1, z, "sign", meta=meta, immediate_mesh=True,
                    )
                    from game.block.tile_entity import TileEntitySign
                    te = TileEntitySign(world, x, ground_y + 1, z)
                    te.sign_text = list(rng.choice(_SIGN_MESSAGES))
                    world.set_tile_entity(x, ground_y + 1, z, te)
            elif choice == "jukebox":
                cell = world.get_block_type(x, ground_y + 1, z)
                if cell in (None, "air", "tall_grass", "dead_bush", "snow"):
                    world.set_block(
                        x, ground_y + 1, z, "jukebox", meta=1, immediate_mesh=True,
                    )
                    music = getattr(world, "record_music_player", None)
                    play_record = getattr(music, "play_record", None) if music else None
                    if callable(play_record):
                        play_record(
                            "13",
                            (float(x) + 0.5, float(ground_y + 1) + 0.5, float(z) + 0.5),
                        )
            elif choice == "damage" and player is not None:
                damage = getattr(player, "damage_player", None)
                if callable(damage):
                    damage(1, source="herobrine", bypass_armor=True)
        except Exception:
            pass

    def _despawn(self, world, player=None, *, play_cave_sound: bool = False, leave_prop: bool = True):
        self._update_music_fade(999.0, active=False)
        if leave_prop:
            try:
                self._leave_prop(world, player=player)
            except Exception:
                pass
        self.dead = True
        world._herobrine_respawn_time = max(
            int(getattr(world, "_herobrine_respawn_time", 0)),
            int(getattr(world, "world_time", 0)) + _respawn_cooldown_ticks(),
        )
        if play_cave_sound and not self._cave_sound_played:
            self._cave_sound_played = True
            self._play_sound("ambient.cave", volume=1.0, pitch=1.0)

    def _face_player(self, dx, dy, dz):
        self.prev_yaw = self.yaw
        self.prev_pitch = self.pitch
        horizontal = math.hypot(dx, dz)
        if horizontal > 1e-6:
            self.yaw = math.degrees(math.atan2(-dx, -dz))
        if horizontal > 1e-6 or abs(dy) > 1e-6:
            self.pitch = math.degrees(math.atan2(dy, max(horizontal, 1e-6)))
        self.prev_render_yaw_offset = self.render_yaw_offset
        self.render_yaw_offset = self.yaw

    def _cell_blocked(self, world, definitions, bx, feet_y, bz, *, headroom=2) -> bool:
        for dy in range(headroom):
            if _is_solid(definitions, world.get_block_type(bx, feet_y + dy, bz)):
                return True
        return False

    def _apply_horizontal_move(self, world, mx, mz, dt):
        if abs(mx) < 1e-8 and abs(mz) < 1e-8:
            return 0.0
        definitions = _block_defs()
        new_x = float(self.pos[0]) + mx * dt
        new_z = float(self.pos[2]) + mz * dt
        feet_y = int(math.floor(float(self.pos[1]) + 0.5))
        moved_dist = 0.0
        for trial_x, trial_z in ((new_x, new_z), (new_x, self.pos[2]), (self.pos[0], new_z)):
            bx = int(math.floor(trial_x + 0.5))
            bz = int(math.floor(trial_z + 0.5))
            if not self._cell_blocked(world, definitions, bx, feet_y, bz):
                moved_dist = math.hypot(trial_x - self.pos[0], trial_z - self.pos[2])
                self.pos[0] = trial_x
                self.pos[2] = trial_z
                break
            if (
                self._cell_blocked(world, definitions, bx, feet_y, bz, headroom=1)
                and not self._cell_blocked(world, definitions, bx, feet_y + 1, bz, headroom=2)
                and _is_solid(definitions, world.get_block_type(bx, feet_y, bz))
            ):
                if bool(getattr(self, "on_ground", False)) and float(self.vel[1]) <= 1e-4:
                    self.vel[1] = self.JUMP_MOTION
                    self.on_ground = False
                moved_dist = math.hypot(trial_x - self.pos[0], trial_z - self.pos[2])
                self.pos[0] = trial_x
                self.pos[1] = max(float(self.pos[1]), float(feet_y + 1))
                self.pos[2] = trial_z
                break
        else:
            if bool(getattr(self, "on_ground", False)) and float(self.vel[1]) <= 1e-4:
                self.vel[1] = self.JUMP_MOTION
                self.on_ground = False

        self.prev_limb_swing = self.limb_swing
        self.prev_limb_swing_amount = self.limb_swing_amount
        if moved_dist > 1e-5:
            self.limb_swing_amount = min(1.0, 0.4 + moved_dist * 4.0)
            self.limb_swing += self.limb_swing_amount * max(dt, 0.0) * 8.0
            self._step_distance += moved_dist
            if self._step_distance >= 0.6 and bool(getattr(self, "on_ground", True)):
                self._step_distance = 0.0
                if CONFIG["act_footsteps"]:
                    self._play_footstep()
        else:
            self.limb_swing_amount *= 0.6
        return moved_dist

    def _prank_interactables(self, world, rng):
        px = int(math.floor(float(self.pos[0]) + 0.5))
        py = int(math.floor(float(self.pos[1]) + 0.5))
        pz = int(math.floor(float(self.pos[2]) + 0.5))
        found = []
        for dx in range(-6, 7):
            for dy in range(-2, 4):
                for dz in range(-6, 7):
                    x, y, z = px + dx, py + dy, pz + dz
                    bt = world.get_block_type(x, y, z)
                    if bt in _INTERACTABLES:
                        found.append((x, y, z, bt))
        if not found:
            return
        rng.shuffle(found)
        for x, y, z, bt in found[:3]:
            try:
                if bt == "wooden_door":
                    from game.block.placement import toggle_door_metadata
                    door_y = y
                    meta = int(world.get_metadata(x, y, z))
                    if meta & 8:
                        door_y = y - 1
                    if world.get_block_type(x, door_y, z) != "wooden_door":
                        continue
                    was_open, new_meta = toggle_door_metadata(world, x, door_y, z)
                    if new_meta is None:
                        continue
                    world.set_block(x, door_y, z, "wooden_door", meta=new_meta)
                    world.set_block(
                        x, door_y + 1, z, "wooden_door",
                        meta=new_meta | 8, immediate_mesh=True,
                    )
                    self._play_sound(
                        "random.door_close" if was_open else "random.door_open",
                        volume=1.0, pitch=1.0,
                        position=(float(x), float(door_y), float(z)),
                    )
                else:
                    redstone = getattr(world, "_redstone_system", None)
                    if redstone is None:
                        continue
                    if bt == "lever":
                        redstone.toggle_lever(x, y, z)
                    else:
                        redstone.press_button(x, y, z)
            except Exception:
                continue

    def _strip_nearby_leaves(self, world, rng):
        px = int(math.floor(float(self.pos[0]) + 0.5))
        py = int(math.floor(float(self.pos[1]) + 0.5))
        pz = int(math.floor(float(self.pos[2]) + 0.5))
        leaves = []
        for dx in range(-5, 6):
            for dy in range(-1, 8):
                for dz in range(-5, 6):
                    x, y, z = px + dx, py + dy, pz + dz
                    if world.get_block_type(x, y, z) == "leaves":
                        leaves.append((x, y, z))
        if not leaves:
            return
        rng.shuffle(leaves)
        for x, y, z in leaves[: rng.randint(4, 12)]:
            try:
                world.set_block(x, y, z, None, immediate_mesh=True)
            except Exception:
                try:
                    world.set_block(x, y, z, "air", immediate_mesh=True)
                except Exception:
                    pass

    def _update_jumpscare(self, world, player, dx, dy, dz, distance_sq, dt):
        distance = math.sqrt(max(distance_sq, 1e-8))
        look = self._look_dot(player, dx, dy, dz, distance_sq)
        if distance <= _JUMPSCARE_STOP_DISTANCE:
            self._despawn(world, player, play_cave_sound=True, leave_prop=True)
            return
        if look >= _LOOK_DOT_THRESHOLD and self.ticks_existed >= 6:
            self._look_stare_ticks += 1
            if self._look_stare_ticks >= 4:
                self._despawn(world, player, play_cave_sound=True, leave_prop=True)
                return
        else:
            self._look_stare_ticks = 0

        inv_h = 1.0 / max(math.hypot(dx, dz), 1e-6)
        toward_x, toward_z = dx * inv_h, dz * inv_h
        side_x, side_z = -toward_z, toward_x
        if look >= _LOOK_THREAT_DOT:
            yaw = math.radians(float(getattr(player, "yaw", 0.0)))
            look_x, look_z = -math.sin(yaw), -math.cos(yaw)
            if look_x * side_x + look_z * side_z > 0:
                side_x, side_z = -side_x, -side_z
            speed = _JUMPSCARE_DODGE_SPEED
            mx = -toward_x * speed * 0.55 + side_x * speed
            mz = -toward_z * speed * 0.55 + side_z * speed
        else:
            speed = _JUMPSCARE_APPROACH_SPEED
            if distance < _JUMPSCARE_STOP_DISTANCE + 0.8:
                speed *= 0.35
            mx, mz = toward_x * speed, toward_z * speed
        self._apply_horizontal_move(world, mx, mz, dt)

    def _has_line_of_sight_to_player(self, world, player) -> bool:
        try:
            px, py, pz = (float(value) for value in player.pos)
            hx, hy, hz = (float(value) for value in self.pos)
            player_eye_y = py + float(getattr(player, "EYE_HEIGHT", 1.62))
            herobrine_eye_y = hy + self.EYE_HEIGHT
            return _occlusion_hits(
                world, _block_defs(),
                px, player_eye_y, pz,
                hx, herobrine_eye_y, hz,
            ) == 0
        except Exception:
            return True

    def _update_charge(self, world, player, dx, dy, dz, distance_sq, dt):
        distance = math.sqrt(max(distance_sq, 1e-8))
        look = self._look_dot(player, dx, dy, dz, distance_sq)
        being_watched = (
            look >= _CHARGE_LOOK_DOT
            and self.ticks_existed >= 10
            and self._has_line_of_sight_to_player(world, player)
        )
        if not self._charging:
            if being_watched:
                self._charge_look_ticks += 1
            else:
                self._charge_look_ticks = max(0, self._charge_look_ticks - 2)
            if self._charge_look_ticks >= _CHARGE_LOOK_TICKS:
                self._charging = True
                self._play_sound("ambient.cave", volume=0.6, pitch=1.2)

        if distance <= _JUMPSCARE_STOP_DISTANCE:
            self._despawn(world, player, play_cave_sound=True, leave_prop=True)
            return

        if self._charging:
            inv_h = 1.0 / max(math.hypot(dx, dz), 1e-6)
            self._apply_horizontal_move(
                world, dx * inv_h * _CHARGE_SPEED, dz * inv_h * _CHARGE_SPEED, dt,
            )

    def _update_creaking(self, world, player, dx, dy, dz, distance_sq, dt):
        distance = math.sqrt(max(distance_sq, 1e-8))
        look = self._look_dot(player, dx, dy, dz, distance_sq)
        if distance_sq <= _CREAKING_NEAR_DISTANCE_SQ:
            self._despawn(world, player, play_cave_sound=True, leave_prop=True)
            return
        if look >= _LOOK_DOT_THRESHOLD and self._has_line_of_sight_to_player(world, player):
            self.limb_swing_amount *= 0.5
            return
        inv_h = 1.0 / max(math.hypot(dx, dz), 1e-6)
        self._apply_horizontal_move(
            world, dx * inv_h * _CREAKING_SPEED, dz * inv_h * _CREAKING_SPEED, dt,
        )

    def update(self, dt):
        if self.dead:
            return
        world = self.world
        player = getattr(world, "player", None)
        if player is None or getattr(player, "health", 20) <= 0:
            self._update_music_fade(999.0, active=False)
            self.dead = True
            return

        if Entity.world is world:
            super().update(dt)
        else:
            previous_world = Entity.world
            Entity.world = world
            try:
                super().update(dt)
            finally:
                Entity.world = previous_world
        if self.dead:
            return

        dx = float(player.pos[0]) - float(self.pos[0])
        player_eye_y = float(player.pos[1]) + float(getattr(player, "EYE_HEIGHT", 1.62))
        self_eye_y = float(self.pos[1]) + self.EYE_HEIGHT
        dy = player_eye_y - self_eye_y
        dz = float(player.pos[2]) - float(self.pos[2])
        distance_sq = dx * dx + dy * dy + dz * dz
        distance = math.sqrt(max(distance_sq, 1e-8))

        edge = _render_edge_distance(world)
        max_dist_sq = (edge + 24.0) ** 2
        if self.mode == "jumpscare":
            max_dist_sq = _MAX_JUMPSCARE_DISTANCE_SQ
        elif self.mode == "charge":
            max_dist_sq = max(_MAX_CHARGE_DISTANCE_SQ, (edge + 16.0) ** 2)
        elif self.mode == "creaking":
            max_dist_sq = _MAX_CREAKING_DISTANCE_SQ
        if distance_sq > max_dist_sq:
            self._despawn(world, player, leave_prop=False)
            return

        self._face_player(dx, dy, dz)
        self.ticks_existed += 1
        self._update_music_fade(distance, active=True)

        rng = random.Random(
            (int(getattr(world, "seed", 0)) ^ self.ticks_existed ^ hash(self.mode))
            & 0xFFFFFFFF
        )
        if self.ticks_existed >= self._next_interact_tick:
            self._next_interact_tick = self.ticks_existed + _INTERACT_INTERVAL_TICKS + rng.randrange(20)
            if CONFIG["act_doors"]:
                self._prank_interactables(world, rng)
        if self.ticks_existed >= self._next_leaf_tick:
            self._next_leaf_tick = self.ticks_existed + _LEAF_STRIP_INTERVAL_TICKS + rng.randrange(30)
            if CONFIG["act_leaves"]:
                self._strip_nearby_leaves(world, rng)

        if self.mode == "jumpscare":
            self._update_jumpscare(world, player, dx, dy, dz, distance_sq, dt)
        elif self.mode == "charge":
            self._update_charge(world, player, dx, dy, dz, distance_sq, dt)
        elif self.mode == "creaking":
            self._update_creaking(world, player, dx, dy, dz, distance_sq, dt)
        else:
            if distance_sq <= _NEAR_DISTANCE_SQ:
                self._despawn(world, player, play_cave_sound=True, leave_prop=True)

    def draw(self):
        renderer = getattr(self, "renderer", None) or getattr(self.world, "renderer", None)
        if renderer is None:
            return
        from game.entities import player_model

        world = self.world
        partial = 0.0
        partial_ticks = getattr(world, "partial_ticks", None)
        if callable(partial_ticks):
            try:
                partial = max(0.0, min(1.0, float(partial_ticks())))
            except Exception:
                partial = 0.0
        yaw_delta = (float(self.yaw) - float(self.prev_yaw) + 180.0) % 360.0 - 180.0
        interpolated_yaw = float(self.prev_yaw) + yaw_delta * partial
        pitch_delta = float(self.pitch) - float(self.prev_pitch)
        interpolated_pitch = float(self.prev_pitch) + pitch_delta * partial
        body_delta = (
            float(self.render_yaw_offset) - float(self.prev_render_yaw_offset) + 180.0
        ) % 360.0 - 180.0
        interpolated_body_yaw = float(self.prev_render_yaw_offset) + body_delta * partial
        player_model.render_player_model(
            self, float(self.pos[0]), float(self.pos[1]), float(self.pos[2]),
            interpolated_body_yaw, partial, head_yaw=interpolated_yaw,
            head_pitch=interpolated_pitch,
        )
        if self._glowing_eyes:
            self._draw_glowing_eyes(
                renderer, interpolated_body_yaw, interpolated_yaw, interpolated_pitch,
            )

    def _draw_glowing_eyes(self, renderer, body_yaw, head_yaw, head_pitch):
        try:
            from OpenGL.GL import (
                glDisable, glEnable, glBegin, glEnd, glVertex3f, glColor4f,
                glPushMatrix, glPopMatrix, glTranslatef, glRotatef, glScalef,
                GL_LIGHTING, GL_TEXTURE_2D, GL_BLEND, GL_ALPHA_TEST, GL_CULL_FACE,
                GL_DEPTH_TEST, GL_QUADS, glBlendFunc, GL_SRC_ALPHA, GL_ONE,
                GL_ONE_MINUS_SRC_ALPHA, glDepthMask, glDepthFunc, GL_LEQUAL,
            )
            from game.entities.player_model import _UNIT, _PLAYER_SCALE
        except Exception:
            return
        try:
            dx, dy, dz = renderer.camera_relative_offset(
                float(self.pos[0]), float(self.pos[1]), float(self.pos[2]),
            )
        except Exception:
            return

        def _uv_to_model(u, v):
            return (-4.0 + (u - 8.0), -8.0 + (v - 8.0))

        left_x0, left_y = _uv_to_model(9.0, 12.0)
        left_x1, _ = _uv_to_model(11.0, 12.0)
        right_x0, right_y = _uv_to_model(13.0, 12.0)
        right_x1, _ = _uv_to_model(15.0, 12.0)
        z_front = -4.05
        y0, y1 = left_y, left_y + 1.0

        net_head = float(body_yaw) - float(head_yaw)
        while net_head < -180.0:
            net_head += 360.0
        while net_head >= 180.0:
            net_head -= 360.0
        pitch = -float(head_pitch)

        glPushMatrix()
        try:
            glTranslatef(dx, dy, dz)
            glRotatef(float(body_yaw), 0.0, 1.0, 0.0)
            glScalef(-1.0, -1.0, 1.0)
            glScalef(_PLAYER_SCALE, _PLAYER_SCALE, _PLAYER_SCALE)
            glTranslatef(0.0, -24.0 * _UNIT - 0.0078125, 0.0)
            glRotatef(net_head, 0.0, 1.0, 0.0)
            glRotatef(pitch, 1.0, 0.0, 0.0)

            glDisable(GL_LIGHTING)
            glDisable(GL_TEXTURE_2D)
            glDisable(GL_ALPHA_TEST)
            glDisable(GL_CULL_FACE)
            glEnable(GL_DEPTH_TEST)
            glDepthFunc(GL_LEQUAL)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE)
            glDepthMask(False)
            glColor4f(1.0, 1.0, 1.0, 1.0)

            scale = float(_UNIT)
            glBegin(GL_QUADS)
            for x0, x1 in ((left_x0, left_x1), (right_x0, right_x1)):
                glVertex3f(x0 * scale, y0 * scale, z_front * scale)
                glVertex3f(x1 * scale, y0 * scale, z_front * scale)
                glVertex3f(x1 * scale, y1 * scale, z_front * scale)
                glVertex3f(x0 * scale, y1 * scale, z_front * scale)
            glEnd()
        finally:
            glDepthMask(True)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glDisable(GL_BLEND)
            glEnable(GL_ALPHA_TEST)
            glEnable(GL_TEXTURE_2D)
            glEnable(GL_CULL_FACE)
            glColor4f(1.0, 1.0, 1.0, 1.0)
            glPopMatrix()


def _find_edge_stalk_position(
    world, player, rng, *, prefer_cover: bool = True,
    radius_range: tuple[float, float] | None = None,
):
    px, py, pz = (float(v) for v in player.pos)
    center_y = int(math.floor(py + 0.5))
    edge = _render_edge_distance(world)
    if radius_range is None:
        radii = (
            edge + rng.uniform(2.0, 6.0),
            edge + rng.uniform(6.0, 12.0),
            edge + rng.uniform(1.0, 4.0),
        )
    else:
        inner, outer = (float(value) for value in radius_range)
        inner = max(4.0, inner)
        outer = max(inner, outer)
        radii = tuple(rng.uniform(inner, outer) for _ in range(3))
    definitions = _block_defs()
    player_eye = (px, py + float(getattr(player, "EYE_HEIGHT", 1.62)), pz)
    candidates = []
    for radius in radii:
        for index in range(24):
            angle = (2.0 * math.pi * index / 24.0) + rng.uniform(-0.08, 0.08)
            x = int(math.floor(px + radius * math.cos(angle) + 0.5))
            z = int(math.floor(pz + radius * math.sin(angle) + 0.5))
            if not _chunk_loaded(world, x, z):
                continue
            ground_y = _scan_ground_y(world, definitions, x, z, center_y, y_up=8, y_down=20)
            if ground_y is None:
                continue
            feet_y = ground_y + 1
            try:
                chunk = world.chunks.get(world._get_chunk_index(x, z))
                if chunk is not None:
                    lx = x - chunk.start_x
                    lz = z - chunk.start_z
                    if 0 <= lx < chunk.width and 0 <= lz < chunk.depth:
                        top = int(chunk.heightmap[lz * chunk.width + lx])
                        if feet_y < top - 6:
                            continue
            except Exception:
                pass
            if not _can_see_sky(world, x, feet_y + 1, z) and _light_at(world, x, feet_y + 1, z) < 8:
                continue
            cover = _adjacent_cover_score(world, definitions, x, feet_y, z)
            occl = _occlusion_hits(
                world, definitions,
                player_eye[0], player_eye[1], player_eye[2],
                float(x), float(feet_y) + 1.0, float(z),
            )
            if prefer_cover and cover < 1 and occl < 1:
                continue
            score = cover * 3 + occl * 4 + rng.random()
            candidates.append((score, float(x), float(feet_y), float(z)))
    if not candidates:
        if prefer_cover:
            return _find_edge_stalk_position(
                world, player, rng, prefer_cover=False, radius_range=radius_range,
            )
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)
    top = candidates[: max(1, min(6, len(candidates)))]
    _, x, y, z = top[rng.randrange(len(top))]
    return (x, y, z)


def _find_stalker_position(world, player, rng):
    return _find_edge_stalk_position(
        world, player, rng, radius_range=(6.0, 8.0),
    )


def _mode_spawn_radius_range(world, mode: str) -> tuple[float, float]:
    edge = _render_edge_distance(world)
    if mode == "charge":
        outer = min(edge - 4.0, edge * 0.82)
    elif mode == "creaking":
        outer = min(edge - 4.0, math.sqrt(_MAX_CREAKING_DISTANCE_SQ) - 4.0)
    else:
        return (edge + 2.0, edge + 12.0)
    outer = max(8.0, outer)
    inner = max(6.0, outer - 8.0)
    return inner, outer


def _find_jumpscare_position(world, player, rng):
    px, py, pz = (float(v) for v in player.pos)
    yaw = math.radians(float(getattr(player, "yaw", 0.0)))
    back_x, back_z = math.sin(yaw), math.cos(yaw)
    definitions = _block_defs()
    center_y = int(math.floor(py + 0.5))
    for _ in range(12):
        distance = rng.uniform(5.0, 10.0)
        lateral = rng.uniform(-1.5, 1.5)
        x = int(math.floor(px + back_x * distance - back_z * lateral + 0.5))
        z = int(math.floor(pz + back_z * distance + back_x * lateral + 0.5))
        if not _chunk_loaded(world, x, z):
            continue
        ground_y = _scan_ground_y(world, definitions, x, z, center_y, y_up=3, y_down=5)
        if ground_y is None:
            continue
        feet_y = ground_y + 1
        try:
            chunk = world.chunks.get(world._get_chunk_index(x, z))
            if chunk is not None:
                lx = x - chunk.start_x
                lz = z - chunk.start_z
                if 0 <= lx < chunk.width and 0 <= lz < chunk.depth:
                    top = int(chunk.heightmap[lz * chunk.width + lx])
                    if feet_y < top - 6:
                        continue
        except Exception:
            pass
        if not _can_see_sky(world, x, feet_y + 1, z) and _light_at(world, x, feet_y + 1, z) < 8:
            continue
        return (float(x), float(feet_y), float(z))
    return None


def update_herobrine(world, player, *, enabled=True, third_person: bool = False):
    all_existing = [e for e in getattr(world, "entities", ()) if isinstance(e, Herobrine)]
    if not enabled or bool(getattr(world, "_multiplayer_world", False)):
        for entity in all_existing:
            entity._update_music_fade(999.0, active=False)
            entity.dead = True
        if all_existing:
            world.entities = [e for e in world.entities if not getattr(e, "dead", False)]
        return
    if player is None or getattr(player, "health", 20) <= 0:
        return

    existing = [e for e in all_existing if not e.dead]
    if existing:
        return
    if all_existing:
        for entity in all_existing:
            entity._update_music_fade(999.0, active=False)
        world.entities = [
            e for e in world.entities
            if not isinstance(e, Herobrine) and not getattr(e, "dead", False)
        ]

    if _player_in_cave(world, player):
        return

    seed = int(getattr(world, "seed", 0))
    if getattr(world, "_herobrine_config_revision", _CONFIG_REVISION) != _CONFIG_REVISION:
        world._herobrine_spawn_checked = False
    if not getattr(world, "_herobrine_spawn_checked", False):
        forced = seed == HOAX_SCREENSHOT_SEED or bool(CONFIG["force_enable"])
        rng = random.Random((seed & 0xFFFFFFFFFFFFFFFF) ^ 0x4845524F4252494E)
        world._herobrine_spawn_eligible = (
            forced or rng.randrange(int(CONFIG["spawn_odds"])) == 0
        )
        world._herobrine_spawn_checked = True
        world._herobrine_config_revision = _CONFIG_REVISION
    if not bool(getattr(world, "_herobrine_spawn_eligible", False)):
        return

    world_time = int(getattr(world, "world_time", 0))
    if world_time < int(getattr(world, "_herobrine_respawn_time", 0)):
        return
    if world_time < int(getattr(world, "_herobrine_next_attempt_time", 0)):
        return

    rng = random.Random(
        (seed & 0xFFFFFFFFFFFFFFFF) ^ 0x4845524F4252494E ^ (world_time // _RETRY_SPAWN_TICKS)
    )

    roll = rng.randrange(12)
    if third_person:
        weights = (("stalk", 7), ("creaking", 5))
    else:
        weights = (("stalk", 4), ("charge", 3), ("creaking", 3), ("jumpscare", 2))
    weights = tuple((m, w) for m, w in weights if CONFIG[f"mode_{m}"])
    total = sum(w for _, w in weights)
    if total <= 0:
        world._herobrine_next_attempt_time = world_time + _RETRY_SPAWN_TICKS
        return
    pick = roll * total // 12
    mode = weights[-1][0]
    for name, weight in weights:
        pick -= weight
        if pick < 0:
            mode = name
            break

    if mode == "stalk":
        pos = _find_edge_stalk_position(world, player, rng)
    elif mode == "jumpscare":
        pos = _find_jumpscare_position(world, player, rng)
        if pos is None and CONFIG["mode_stalk"]:
            mode = "stalk"
            pos = _find_edge_stalk_position(world, player, rng)
    else:
        pos = _find_edge_stalk_position(
            world, player, rng, radius_range=_mode_spawn_radius_range(world, mode),
        )

    if pos is None:
        world._herobrine_next_attempt_time = world_time + _RETRY_SPAWN_TICKS
        return

    world.spawn_entity(Herobrine(world, pos, mode=mode))
    world._herobrine_spawned = True
    world._herobrine_next_attempt_time = world_time + _RETRY_SPAWN_TICKS
