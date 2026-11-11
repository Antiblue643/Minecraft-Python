
from __future__ import annotations

import time
import math
from collections import deque
from pathlib import Path
from typing import Optional

from game.multiplayer import protocol as P
from game.multiplayer.client import MultiplayerClient
from game.multiplayer.remote_player import RemotePlayer
from game.multiplayer.server import MultiplayerServer
from game.multiplayer.server_config import ServerConfig, player_key, normalize_player_name


_MP_ITEM_DEFINITIONS = None
_MP_BLOCK_DEFINITIONS = None
_MP_ENTITY_KIND_BY_CLASS = {
    ("game.entities.item", "ItemEntity"): "item",
    ("game.entities.tnt", "PrimedTNT"): "tnt",
    ("game.entities.pig", "Pig"): "pig",
    ("game.entities.cow", "Cow"): "cow",
    ("game.entities.sheep", "Sheep"): "sheep",
    ("game.entities.chicken", "Chicken"): "chicken",
    ("game.entities.zombie", "Zombie"): "zombie",
    ("game.entities.pigzombie", "ZombiePigman"): "zombie_pigman",
    ("game.entities.skeleton", "Skeleton"): "skeleton",
    ("game.entities.creeper", "Creeper"): "creeper",
    ("game.entities.spider", "Spider"): "spider",
    ("game.entities.slime", "Slime"): "slime",
    ("game.entities.giant", "Giant"): "giant",
    ("game.entities.mob", "Mob"): "mob",
    ("game.entities.monster", "Monster"): "monster",
    ("game.entities.arrow", "ArrowEntity"): "arrow",
    ("game.entities.snowball", "SnowballEntity"): "snowball",
    ("game.entities.egg", "EggEntity"): "egg",
    ("game.entities.painting", "PaintingEntity"): "painting",
    ("game.entities.boat", "BoatEntity"): "boat",
    ("game.entities.minecart", "MinecartEntity"): "minecart",
    ("game.entities.falling_block", "FallingBlockEntity"): "falling_block",
    ("game.entities.fireball", "FireballEntity"): "fireball",
    ("game.entities.fishing_bobber", "FishingBobberEntity"): "fishing_bobber",
    ("game.entities.pigzombie", "ZombiePigman"): "zombie_pigman",
    ("game.entities.ghast", "Ghast"): "ghast",
    ("game.entities.squid", "Squid"): "squid",
}


def _mp_inventory_item_definition(item_id: str) -> dict:
    global _MP_ITEM_DEFINITIONS
    if _MP_ITEM_DEFINITIONS is None:
        from game.content_data import load_item_definitions
        _MP_ITEM_DEFINITIONS = load_item_definitions()
    return _MP_ITEM_DEFINITIONS.get(str(item_id), {})


def _mp_item_max_stack(item_id: str) -> int:
    definition = _mp_inventory_item_definition(item_id)
    if "stack" in definition:
        return max(1, int(definition["stack"]))
    if definition.get("type") in {"tool", "melee_weapon", "food", "armor"}:
        return 1
    if item_id in {"wooden_door", "iron_door"}:
        return 1
    return 64


def _mp_item_max_damage(item_id: str) -> int:
    definition = _mp_inventory_item_definition(item_id)
    if "durability" in definition:
        return max(0, int(definition["durability"]))
    kind = definition.get("type")
    material = definition.get("material")
    if kind == "tool":
        return {"wood": 32, "stone": 64, "iron": 128, "diamond": 1025, "gold": 32}.get(material, 0)
    if kind == "melee_weapon":
        return {"wood": 32, "stone": 64, "iron": 128, "diamond": 256, "gold": 32}.get(material, 0)
    if kind == "armor":
        base = (11, 16, 15, 13)
        slot = int(definition.get("slot", 0))
        level = int(definition.get("armor_level", 0))
        if 0 <= slot < len(base):
            return (base[slot] * 3) << max(0, level)
    return 0


def _mp_item_armor_slot(item_id: str):
    definition = _mp_inventory_item_definition(item_id)
    return int(definition.get("slot", 0)) if definition.get("type") == "armor" else None


def _mp_item_exists(item_id: str) -> bool:
    _mp_inventory_item_definition(item_id)  
    from game.content_data import load_block_definitions
    global _MP_BLOCK_DEFINITIONS
    if _MP_BLOCK_DEFINITIONS is None:
        _MP_BLOCK_DEFINITIONS = load_block_definitions()
    return str(item_id) in _MP_ITEM_DEFINITIONS or str(item_id) in _MP_BLOCK_DEFINITIONS


def multiplayer_name_tag_scale() -> float:
    return (1.0 / 60.0) * 1.6


def multiplayer_name_tag_glyph_scale(scale: float) -> tuple[float, float, float]:
    scale = float(scale)
    return scale, -scale, scale


def alpha_name_tag_billboard_rotation(camera_yaw: float, camera_pitch: float) -> tuple[float, float]:
    return float(camera_yaw), float(camera_pitch)


def remote_player_in_render_view(remote, world, renderer, camera_player=None) -> bool:
    if remote is None or not bool(getattr(remote, "should_render", True)):
        return False
    if world is None or renderer is None:
        return True
    try:
        px, py, pz = (float(value) for value in remote.pos[:3])
        sleeping = bool(getattr(remote, "is_sleeping", False))
        sleep_bounds = None
        if sleeping:
            try:
                from game.block.bed import bed_sleep_render_offset
                meta = int(getattr(remote, "_bed_sleep_meta", 0) or 0) & 3
                offset_x, offset_z = bed_sleep_render_offset(meta)
                px += float(offset_x)
                pz += float(offset_z)
                width, depth = (0.7, 1.9) if meta in (0, 2) else (1.9, 0.7)
                sleep_bounds = (
                    px - width * 0.5, py - 0.12, pz - depth * 0.5,
                    px + width * 0.5, py + 0.62, pz + depth * 0.5,
                )
            except Exception:
                sleep_bounds = None
        fallback = getattr(camera_player, "pos", (
            getattr(renderer, "camX", 0.0),
            getattr(renderer, "camY", 64.0),
            getattr(renderer, "camZ", 0.0),
        ))
        cx = float(getattr(renderer, "view_x", fallback[0]))
        cy = float(getattr(renderer, "view_y", fallback[1]))
        cz = float(getattr(renderer, "view_z", fallback[2]))
        max_distance = (max(2, int(getattr(world, "view_distance", 5))) + 1) * float(
            getattr(world, "CHUNK_SIZE", 16)
        )
        dx, dy, dz = px - cx, py - cy, pz - cz
        if dx * dx + dy * dy + dz * dz > max_distance * max_distance:
            return False

        if camera_player is not None:
            from game.rendering.view_culling import CameraFrustum

            yaw = float(getattr(camera_player, "yaw", 0.0))
            pitch = float(getattr(camera_player, "pitch", 0.0))
            aspect = float(getattr(world, "_draw_aspect", 16.0 / 9.0) or (16.0 / 9.0))
            frustum = CameraFrustum.from_camera(
                cx, cy, cz, yaw, pitch,
                float(getattr(renderer, "fov", 70.0)), aspect,
                near_clip=float(getattr(renderer, "near_clip", 0.1)),
                far_clip=min(max_distance, float(getattr(renderer, "far_clip", max_distance))),
            )
        else:
            make_frustum = getattr(world, "_camera_frustum", None)
            frustum = make_frustum() if callable(make_frustum) else None
        if frustum is not None:
            bounds = sleep_bounds
            if bounds is None:
                bounds_getter = getattr(remote, "get_interaction_bounds", None)
                bounds = bounds_getter() if callable(bounds_getter) else None
            if bounds is None:
                width, height, depth = getattr(remote, "size", (0.6, 1.8, 0.6))
                bounds = (px - float(width) * 0.5, py, pz - float(depth) * 0.5,
                          px + float(width) * 0.5, py + float(height), pz + float(depth) * 0.5)
            if not frustum.intersects_aabb(*bounds, padding=0.25):
                return False
        return True
    except Exception:
        return True


class MultiplayerSession:
    def __init__(self, game):
        self.game = game
        self.mode: Optional[str] = None  
        self.server: Optional[MultiplayerServer] = None
        self.client: Optional[MultiplayerClient] = None
        self.remote_players: dict[int, RemotePlayer] = {}
        self.local_id = 1
        self._pos_accum = 0.0
        self._break_accum = 0.0
        self._last_inventory_signature = None
        self._inventory_tx_sequence = 0
        self._last_break_signature = None
        self._last_tile_entity_signatures = {}
        self._tile_entity_sync_accum = 0.0
        self._status = ""
        self._attached_world = None
        self._attached_inventory = None
        self._host_chunk_requests = deque()
        self._host_chunk_request_keys = set()
        self._client_chunk_pending = set()
        self._client_chunk_request_accum = 0.0
        self._client_chunk_sync_deltas = {}
        self._client_chunk_sync_tile_entity_deltas = {}
        self._client_chunk_retry_after = {}
        self._client_chunk_snapshots_waiting = deque()
        self._client_chunk_snapshot_waiting_keys = set()
        self.remote_entities: dict[int, object] = {}
        self._entity_sync_accum = 0.0
        self._next_entity_net_id = 1
        self._respawn_request_pending = False
        self._time_sync_accum = 0.0
        self._boat_control_accum = 0.0
        self._last_boat_control_entity_id = None
        self._jukebox_audio_states = {}
        self._authoritative_bed_occupancy: set[tuple[int, int, int]] = set()
        self.server_config: Optional[ServerConfig] = None

    @property
    def active(self) -> bool:
        return self.mode is not None

    def connected_player_names(self) -> list[str]:
        if self.mode == "host":
            server = self.server
            local_name = (getattr(server, "host_name", None) if server is not None else None)
            local_name = local_name or getattr(getattr(self.game, "settings", None), "username", None) or "Host"
            snapshots = server.snapshot_players() if server is not None else []
            names = [] if bool(getattr(self.game, "_dedicated_server_mode", False)) else [local_name]
            names.extend(str(item.get("name") or "Player") for item in snapshots)
        elif self.mode == "client":
            client = self.client
            local_name = (getattr(client, "name", None) if client is not None else None)
            local_name = local_name or getattr(getattr(self.game, "settings", None), "username", None) or "Player"
            names = [local_name]
            names.extend(str(player.username) for player in self.remote_players.values())
        else:
            names = [getattr(getattr(self.game, "settings", None), "username", None) or "Player"]

        result = []
        seen = set()
        for value in names:
            name = str(value).strip()[:16] or "Player"
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                result.append(name)
        return result

    def log(self, msg: str) -> None:
        self._status = str(msg)
        if self._status:
            print(self._status)

    def notify(self, msg: str) -> None:
        add = getattr(self.game, "add_chat_message", None)
        if callable(add):
            add(str(msg))

    def start_host(self, port: int = P.DEFAULT_PORT) -> bool:
        game = self.game
        if game.world is None or game.player is None:
            return False
        if int(getattr(game.world, "dimension_id", 0)) != 0:
            self.notify("Worlds can only be opened to LAN from the Overworld. Leave the Nether first.")
            return False
        self.stop()
        spawn = (
            float(game.player.pos[0]),
            float(game.player.pos[1]),
            float(game.player.pos[2]),
        )
        name = getattr(game.settings, "username", "Host") or "Host"
        save_store = getattr(game.world, "save_store", None)
        save_root = getattr(save_store, "root", None)
        if save_root is not None:
            config_path = Path(save_root) / "minecraftpy_multiplayer.json"
        else:
            user_root = Path(getattr(game, "user_data_root", Path.cwd()))
            config_path = user_root / "multiplayer" / f"world_{int(game.world.seed)}.json"
        self.server_config = ServerConfig(config_path)
        game._ban_list = set(self.server_config.ban_names())
        game._op_list = set(self.server_config.operators)
        banned_names = set(self.server_config.bans)
        ban_reasons = {key: value.get("reason", "Banned by host") for key, value in self.server_config.bans.items()}
        self.server = MultiplayerServer(
            seed=int(game.world.seed),
            host_name=name,
            spawn=spawn,
            port=port,
            on_log=self.log,
            snow_covered=bool(getattr(game.world, "snow_covered", False)),
            world_time=int(getattr(game.world, "world_time", 0)),
            accurate_generation=bool(getattr(game.world, "accurate_generation", False)),
            debug_world=bool(getattr(game.world, "debug_world", False)),
            banned_names=banned_names,
            operator_names=set(self.server_config.operators),
            player_profiles=self.server_config.profiles,
            whitelist_enabled=self.server_config.whitelist_enabled,
            whitelist_names=set(self.server_config.whitelist),
            ip_bans=self.server_config.ip_bans,
            host_player_visible=not bool(getattr(game, "_dedicated_server_mode", False)),
        )
        self.server.ban_reasons = ban_reasons
        try:
            self.server.start()
        except OSError as exc:
            self.log(f"Could not start server: {exc}")
            self.server = None
            return False
        self.mode = "host"
        self.local_id = 1
        self._last_inventory_signature = None
        self._inventory_tx_sequence = 0
        self._last_break_signature = None
        self._last_tile_entity_signatures.clear()
        self._tile_entity_sync_accum = 0.0
        self._reset_chunk_sync_state()
        self.remote_entities.clear()
        self._authoritative_bed_occupancy.clear()
        self._entity_sync_accum = 0.0
        self._next_entity_net_id = 1
        self.attach_world(game.world)
        if not bool(getattr(game, "_dedicated_server_mode", False)):
            self._announce_lan_open()
        for entity in tuple(getattr(game.world, "entities", ())):
            try:
                delattr(entity, "_mp_net_id")
            except AttributeError:
                pass
        return True

    def _announce_lan_open(self) -> None:
        server = self.server
        if server is None:
            return
        port = int(server.bound_port)
        self.log(f"LAN world open on port {port}")
        try:
            from game.multiplayer.netutils import format_host_port, local_ip_addresses
            addresses = local_ip_addresses()
        except Exception:
            addresses = []
        if addresses:
            joins = ", ".join(format_host_port(address, port) for address in addresses[:2])
            self.log(f"Others on your network can join at {joins}, or 127.0.0.1:25565")

    def start_client(self, host: str, port: int, name: str) -> MultiplayerClient:
        self.stop()
        self.client = MultiplayerClient(host, port, name=name, on_log=self.log)
        self.mode = "client"
        self._last_inventory_signature = None
        self._inventory_tx_sequence = 0
        self._last_break_signature = None
        self._last_tile_entity_signatures.clear()
        self._tile_entity_sync_accum = 0.0
        self._reset_chunk_sync_state()
        self.remote_entities.clear()
        self._authoritative_bed_occupancy.clear()
        self._entity_sync_accum = 0.0
        self._next_entity_net_id = 1
        return self.client

    def _reset_chunk_sync_state(self) -> None:
        self._host_chunk_requests.clear()
        self._host_chunk_request_keys.clear()
        self._client_chunk_pending.clear()
        self._client_chunk_request_accum = 0.0
        self._client_chunk_sync_deltas.clear()
        self._client_chunk_sync_tile_entity_deltas.clear()
        self._client_chunk_retry_after.clear()
        self._client_chunk_snapshots_waiting.clear()
        self._client_chunk_snapshot_waiting_keys.clear()

    def finish_client_join(self, welcome: dict) -> None:
        self.local_id = int(welcome.get("id") or 0)
        if self.client is not None:
            try:
                self.client.inventory_revision = max(0, int(welcome.get("inventory_revision", 0)))
                self.client.inventory_send_revision = self.client.inventory_revision
            except (TypeError, ValueError, OverflowError):
                self.client.inventory_revision = 0
                self.client.inventory_send_revision = 0
        self.attach_world(getattr(self.game, "world", None))
        world = getattr(self.game, "world", None)
        if world is not None:
            self._apply_authoritative_world_time(world, welcome.get("world_time", 0))
        if isinstance(welcome.get("inventory"), dict):
            self._apply_local_inventory_state(welcome["inventory"])
        for p in welcome.get("players") or []:
            self._spawn_remote(p)
        if world is not None:
            try:
                from game.block.bed import clear_loaded_bed_occupancy
                clear_loaded_bed_occupancy(world)
            except Exception:
                pass
            occupied = set()
            for bed in welcome.get("occupied_beds") or []:
                if isinstance(bed, (list, tuple)) and len(bed) == 3:
                    try:
                        x, y, z = (int(value) for value in bed)
                        if (-30_000_000 <= x <= 30_000_000 and 0 <= y < 256
                                and -30_000_000 <= z <= 30_000_000):
                            occupied.add((x, y, z))
                    except (TypeError, ValueError, OverflowError):
                        pass
            if "occupied_beds" not in welcome:
                for peer in welcome.get("players") or []:
                    if not bool(peer.get("sleeping", False)):
                        continue
                    bed = peer.get("bed_location")
                    if isinstance(bed, (list, tuple)) and len(bed) == 3:
                        try:
                            occupied.add(tuple(int(value) for value in bed))
                        except (TypeError, ValueError, OverflowError):
                            pass
            self._authoritative_bed_occupancy = occupied
            self._restore_live_bed_occupancy(world)
            world._mp_remote_players = tuple(self.remote_players.values())

    @staticmethod
    def _apply_authoritative_world_time(world, value) -> None:
        try:
            new_time = max(0, int(value))
        except (TypeError, ValueError, OverflowError):
            return
        old_time = int(getattr(world, "world_time", 0))
        if old_time == new_time:
            return
        world.world_time = new_time
        updater = getattr(world, "_update_skylight_subtracted", None)
        if callable(updater):
            updater()
        world._cloud_tick_pending = 0
        clouds = getattr(world, "_cloud_renderer", None)
        if clouds is not None:
            clouds.cloud_tick = new_time

    def _on_local_bed_claim(self, action: str, bed_location, _bed_meta=0):
        if self.mode != "host" or self.server is None:
            return None
        if action == "claim":
            return self.server.claim_bed(self.local_id, bed_location)
        if action == "release":
            self.server.release_bed(self.local_id, bed_location)
            return True
        return False

    def _apply_bed_occupancy(self, event: dict) -> None:
        try:
            key = (int(event.get("x")), int(event.get("y")), int(event.get("z")))
        except (TypeError, ValueError, OverflowError):
            return
        occupied = bool(event.get("occupied", False))
        if occupied:
            self._authoritative_bed_occupancy.add(key)
        else:
            self._authoritative_bed_occupancy.discard(key)
        world = getattr(self.game, "world", None)
        if world is None:
            return
        try:
            from game.block.bed import bed_set_occupied
            bed_set_occupied(world, *key, occupied)
        except Exception:
            return

    def _restore_live_bed_occupancy(self, world) -> None:
        if world is None:
            return
        try:
            from game.block.bed import bed_set_occupied
        except Exception:
            return
        for bed in tuple(self._authoritative_bed_occupancy):
            try:
                bed_set_occupied(world, int(bed[0]), int(bed[1]), int(bed[2]), True)
            except (TypeError, ValueError, OverflowError):
                continue
        players = [getattr(world, "player", None), *self.remote_players.values()]
        for player in players:
            if player is None or not bool(getattr(player, "is_sleeping", False)):
                continue
            bed = getattr(player, "bed_location", None)
            if not isinstance(bed, (list, tuple)) or len(bed) != 3:
                continue
            try:
                bed_set_occupied(
                    world, int(bed[0]), int(bed[1]), int(bed[2]), True,
                )
            except (TypeError, ValueError, OverflowError):
                continue

    def _attach_local_inventory(self) -> None:
        player = getattr(self.game, "player", None)
        inventory = getattr(player, "inventory", None)
        if self._attached_inventory is inventory:
            return
        previous = self._attached_inventory
        if previous is not None:
            callback = getattr(previous, "_hotbar_selection_callback", None)
            if getattr(callback, "__self__", None) is self:
                previous._hotbar_selection_callback = None
        self._attached_inventory = inventory
        if inventory is not None:
            inventory._hotbar_selection_callback = self._on_local_hotbar_selection

    def _on_local_hotbar_selection(self, slot: int) -> None:
        try:
            selected = int(slot)
        except (TypeError, ValueError, OverflowError):
            return
        if not 0 <= selected < 9:
            return
        if self.mode == "client" and self.client is not None:
            self.client.send_hotbar_selection(selected)
        elif self.mode == "host" and self.server is not None:
            player = getattr(self.game, "player", None)
            if player is not None:
                self.server.set_host_inventory(self._inventory_snapshot(player))

    def _approve_remote_hotbar_selection(self, event: dict) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            player_id = int(event.get("id"))
            slot = int(event.get("slot"))
        except (TypeError, ValueError, OverflowError):
            return
        if not 0 <= slot < 9:
            return
        remote = self.remote_players.get(player_id)
        if remote is None or not self._is_remote_player_connected(player_id):
            return
        inventory = getattr(remote, "inventory", None)
        if inventory is None:
            return
        inventory.current_item = slot
        state = self._inventory_snapshot(remote)
        self.server.set_client_inventory_state(player_id, state)
        if self.server_config is not None:
            self.server_config.update_inventory(remote.username, state)

    def attach_world(self, world) -> None:
        self._attach_local_inventory()
        previous = self._attached_world
        if previous is not None and previous is not world:
            previous._network_block_callback = None
            previous._network_world_event_callback = None
            previous._network_sleep_cycle_callback = None
            previous._network_bed_claim_callback = None
            previous._network_bucket_callback = None
            previous._network_item_use_callback = None
            previous._network_jukebox_callback = None
            previous._network_note_block_callback = None
            previous._mp_suppress_world_events = False
            previous._network_animation_callback = None
            previous._network_entity_spawn_callback = None
            previous._network_fishing_callback = None
            previous._network_attack_callback = None
            previous._network_entity_interact_callback = None
            previous._network_entity_push_callback = None
            previous._network_player_attack_callback = None
            previous._network_edit_depth = 0
            previous._multiplayer_client_world = False
            previous._multiplayer_world = False
            previous._mp_remote_player_positions = []
            previous._mp_remote_players = ()
        self._attached_world = world
        if previous is not world:
            self._jukebox_audio_states.clear()
            if world is not None:
                try:
                    from game.block.bed import clear_loaded_bed_occupancy
                    clear_loaded_bed_occupancy(world)
                except Exception:
                    pass
        if world is not None:
            world._multiplayer_world = True
            world._multiplayer_client_world = self.mode == "client"
            world._network_block_callback = self._on_local_block_change
            world._network_world_event_callback = self.publish_world_event
            world._network_bed_claim_callback = (
                self._on_local_bed_claim if self.mode == "host" else None
            )
            world._network_sleep_cycle_callback = (
                self._sync_woken_remote_players if self.mode == "host" else None
            )
            world._network_bucket_callback = (
                self._on_local_bucket_use if self.mode == "client" else None
            )
            world._network_item_use_callback = (
                self._on_local_item_use if self.mode == "client" else None
            )
            world._network_jukebox_callback = (
                self._on_local_jukebox_use if self.mode == "client" else None
            )
            world._network_note_block_callback = (
                self._on_local_note_block_use if self.mode == "client" else None
            )
            world._mp_suppress_world_events = False
            world._network_animation_callback = self._on_local_animation
            world._network_entity_spawn_callback = (
                self._on_local_entity_spawn if self.mode == "client" else None
            )
            world._network_fishing_callback = (
                self._on_local_fishing_action if self.mode == "client" else None
            )
            world._network_attack_callback = (
                self._on_local_attack if self.mode == "client" else None
            )
            world._network_entity_interact_callback = (
                self._on_local_entity_interact if self.mode == "client" else None
            )
            world._network_entity_push_callback = (
                self._on_local_entity_push if self.mode == "client" else None
            )
            world._network_player_attack_callback = (
                self._on_local_player_attack if self.mode == "client" else None
            )
            world._mp_remote_players = tuple(self.remote_players.values())
            for remote in self.remote_players.values():
                remote.world = world
                remote.renderer = getattr(self.game, "renderer", None)
            if self.mode != "host":
                world._mp_remote_player_positions = []

    def publish_world_event(self, event: dict) -> bool:
        packet = P.normalize_world_event(
            event, allow_authoritative=self.mode == "host"
        )
        if packet is None or not self.active:
            return False
        if self.mode == "host" and self.server is not None:
            return self.server.broadcast_world_event(packet)
        if self.mode == "client" and self.client is not None:
            self.client.send_world_event(packet)
            return True
        return False

    def _apply_world_event(self, event: dict) -> None:
        packet = P.normalize_world_event(event, allow_authoritative=True)
        if packet is None:
            return
        kind = packet["kind"]
        game = self.game
        world = getattr(game, "world", None)
        if kind == "time":
            if world is None:
                return
            self._apply_authoritative_world_time(world, packet["world_time"])
            return

        old_game_suppress = bool(getattr(game, "_mp_suppress_world_events", False))
        old_world_suppress = bool(getattr(world, "_mp_suppress_world_events", False)) if world is not None else False
        game._mp_suppress_world_events = True
        if world is not None:
            world._mp_suppress_world_events = True
        try:
            if kind == "sound":
                position = packet.get("position")
                game.play_sound(
                    packet["name"], volume=packet["volume"], pitch=packet["pitch"],
                    position=tuple(position) if position is not None else None,
                )
            elif kind == "particle" and world is not None:
                name = packet["name"]
                if name == "block_hit":
                    world.effects.add_block_hit_effects(
                        world, packet["x"], packet["y"], packet["z"],
                        packet.get("face", 0), packet["block_type"], packet.get("meta", 0),
                    )
                elif name == "block_destroy":
                    world.effects.add_block_destroy_effects(
                        world, packet["x"], packet["y"], packet["z"],
                        packet["block_type"], packet.get("meta", 0),
                    )
                else:
                    world.spawn_particle(
                        name, packet["x"], packet["y"], packet["z"],
                        packet["mx"], packet["my"], packet["mz"],
                    )
            elif kind == "explosion" and world is not None:
                from game.entities.tnt import spawn_explosion_particles
                import random
                spawn_explosion_particles(
                    world, packet["x"], packet["y"], packet["z"],
                    packet["power"], random.Random(packet.get("seed", 0)),
                )
        finally:
            game._mp_suppress_world_events = old_game_suppress
            if world is not None:
                world._mp_suppress_world_events = old_world_suppress

    def _on_local_block_change(self, x: int, y: int, z: int, block_type, meta: int) -> None:
        packet = P.block_update(x, y, z, block_type, meta)
        if self.mode == "host" and self.server is not None:
            self.server.broadcast_block_update(x, y, z, block_type, meta)
        elif self.mode == "client" and self.client is not None:
            self._remember_chunk_sync_delta(packet)
            self.client.send(packet)

    def _on_local_bucket_use(self, action: str, x: int, y: int, z: int) -> bool:
        if self.mode != "client" or self.client is None:
            return False
        self.client.send_bucket_use(action, x, y, z)
        return True

    def _on_local_item_use(self, action: str, x: int, y: int, z: int) -> bool:
        if self.mode != "client" or self.client is None:
            return False
        self.client.send_item_use(action, x, y, z)
        return True

    def _on_local_jukebox_use(self, action: str, x: int, y: int, z: int) -> bool:
        if self.mode != "client" or self.client is None:
            return False
        self.client.send_jukebox_use(action, x, y, z)
        return True

    def _on_local_note_block_use(self, action: str, x: int, y: int, z: int) -> bool:
        if self.mode != "client" or self.client is None:
            return False
        self.client.send_note_block_use(action, x, y, z)
        return True

    @staticmethod
    def _boat_input_axes(player):
        keys = getattr(player, "_current_keys", None)
        if keys is None:
            return 0.0, 0.0
        try:
            import pygame as pg
        except Exception:
            return 0.0, 0.0

        def pressed(name, default):
            key_fn = getattr(player, "_key", None)
            try:
                code = key_fn(name, default) if callable(key_fn) else default
                return bool(keys[code])
            except Exception:
                return False

        forward = float(pressed("forward", pg.K_w)) - float(pressed("back", pg.K_s))
        strafe = float(pressed("right", pg.K_d)) - float(pressed("left", pg.K_a))
        axes = getattr(keys, "movement_axes", None)
        if axes is not None:
            try:
                analog_strafe, analog_forward = axes
                forward += max(-1.0, min(1.0, float(analog_forward)))
                strafe += max(-1.0, min(1.0, float(analog_strafe)))
            except (TypeError, ValueError, OverflowError):
                pass
        return max(-1.0, min(1.0, forward)), max(-1.0, min(1.0, strafe))

    def _sync_local_boat_control(self, player, dt: float) -> None:
        if self.mode != "client" or self.client is None:
            return
        self._boat_control_accum += max(0.0, min(float(dt), 0.25))
        mount = getattr(player, "riding_entity", None)
        kind = self._entity_kind(mount) if mount is not None else None
        try:
            current_id = int(getattr(mount, "_mp_net_id", 0)) if kind == "boat" else 0
        except (TypeError, ValueError, OverflowError):
            current_id = 0
        if current_id <= 0:
            previous_id = self._last_boat_control_entity_id
            if previous_id is not None:
                self.client.send_boat_control(previous_id, False)
                self._last_boat_control_entity_id = None
            self._boat_control_accum = 0.0
            return
        if self._boat_control_accum < 0.05:
            return
        self._boat_control_accum %= 0.05
        forward, strafe = self._boat_input_axes(player)
        self.client.send_boat_control(
            current_id, True, forward=forward, strafe=strafe,
            yaw=float(getattr(player, "yaw", getattr(mount, "yaw", 0.0))),
        )
        self._last_boat_control_entity_id = current_id

    @staticmethod
    def _sync_jukebox_audio_state(
        world, state_cache, x, y, z, block_type, meta, *,
        player=None, announce=False,
    ):
        key = (int(x), int(y), int(z))
        new_state = (str(block_type) if block_type is not None else None, int(meta) & 15)
        old_state = state_cache.get(key)
        state_cache[key] = new_state
        old_record = old_state[1] if old_state and old_state[0] == "jukebox" else 0
        new_record = new_state[1] if new_state[0] == "jukebox" else 0
        if old_record == new_record:
            return
        music = getattr(world, "record_music_player", None)
        position = (float(x) + 0.5, float(y) + 0.5, float(z) + 0.5)
        if old_record > 0:
            stop = getattr(music, "stop_record", None)
            if callable(stop):
                stop(position)
        if new_record in (1, 2):
            record_name = "13" if new_record == 1 else "cat"
            play = getattr(music, "play_record", None)
            if callable(play):
                play(record_name, position)
            show_message = getattr(player, "_show_actionbar_message", None)
            if announce and callable(show_message):
                from game.localization import translate
                show_message(translate(
                    "mcpy.chat.nowPlaying", record_name,
                ))

    @staticmethod
    def _inventory_snapshot(player) -> dict:
        inventory = getattr(player, "inventory", None)
        if inventory is None:
            return P.normalize_inventory_state({})

        def encode_stack(stack):
            if stack is None:
                return None
            try:
                return {
                    "id": str(stack.id),
                    "count": int(stack.count),
                    "damage": int(stack.damage),
                }
            except (AttributeError, TypeError, ValueError, OverflowError):
                return None

        return P.normalize_inventory_state({
            "main": [encode_stack(stack) for stack in getattr(inventory, "main", [])],
            "armor": [encode_stack(stack) for stack in getattr(inventory, "armor", [])],
            "selected": getattr(inventory, "current_item", 0),
            "cursor": encode_stack(getattr(player, "cursor_stack", None)),
        })

    @staticmethod
    def _validate_inventory_snapshot(state: dict) -> bool:
        try:
            for field in ("main", "armor"):
                for index, stack in enumerate(state.get(field, ())):
                    if not isinstance(stack, dict):
                        continue
                    item_id = str(stack.get("id") or "")
                    if not _mp_item_exists(item_id):
                        return False
                    count = int(stack.get("count", 0))
                    if count <= 0 or count > _mp_item_max_stack(item_id):
                        return False
                    damage = int(stack.get("damage", 0))
                    max_damage = _mp_item_max_damage(item_id)
                    if damage < 0 or (max_damage <= 0 and damage != 0) or (max_damage > 0 and damage > max_damage):
                        return False
                    if field == "armor" and _mp_item_armor_slot(item_id) != index:
                        return False
            cursor = state.get("cursor")
            if isinstance(cursor, dict):
                item_id = str(cursor.get("id") or "")
                if not _mp_item_exists(item_id):
                    return False
                count = int(cursor.get("count", 0))
                if count <= 0 or count > _mp_item_max_stack(item_id):
                    return False
                damage = int(cursor.get("damage", 0))
                max_damage = _mp_item_max_damage(item_id)
                if damage < 0 or (max_damage <= 0 and damage != 0) or (max_damage > 0 and damage > max_damage):
                    return False
        except (TypeError, ValueError, OverflowError):
            return False
        return True

    @staticmethod
    def _inventory_item_totals(state: dict) -> dict:
        totals = {}
        for field in ("main", "armor"):
            for stack in state.get(field, ()):
                if not isinstance(stack, dict):
                    continue
                item_id = str(stack.get("id") or "")
                try:
                    count = max(0, int(stack.get("count", 0)))
                except (TypeError, ValueError, OverflowError):
                    continue
                if item_id and count:
                    totals[item_id] = totals.get(item_id, 0) + count
        cursor = state.get("cursor")
        if isinstance(cursor, dict):
            item_id = str(cursor.get("id") or "")
            try:
                count = max(0, int(cursor.get("count", 0)))
            except (TypeError, ValueError, OverflowError):
                count = 0
            if item_id and count:
                totals[item_id] = totals.get(item_id, 0) + count
        return totals

    @staticmethod
    def _inventory_durability_totals(state: dict) -> dict:
        totals = {}
        for field in ("main", "armor"):
            for stack in state.get(field, ()):
                if not isinstance(stack, dict):
                    continue
                item_id = str(stack.get("id") or "")
                try:
                    totals[item_id] = totals.get(item_id, 0) + (
                        max(0, int(stack.get("count", 0)))
                        * max(0, int(stack.get("damage", 0)))
                    )
                except (TypeError, ValueError, OverflowError):
                    continue
        return totals

    def request_inventory_click(self, screen, slot, left_click: bool) -> bool:
        if self.mode != "client" or self.client is None:
            return False
        player = getattr(self.game, "player", None)
        target = None
        position = None
        entity_id = None
        if player is not None and getattr(slot, "inventory", None) is getattr(player, "inventory", None):
            target = "player"
        else:
            grid = getattr(screen, "grid", None)
            result_inv = getattr(screen, "result_inv", None)
            slot_inv = getattr(slot, "inventory", None)
            if grid is not None and slot_inv is grid:
                width = int(getattr(grid, "width", 2) or 2)
                target = "craft3" if width >= 3 else "craft2"
            elif result_inv is not None and slot_inv is result_inv:
                width = int(getattr(grid, "width", 2) or 2) if grid is not None else 2
                target = "craft3" if width >= 3 else "craft2"
            if target is None:
                tile_entity = getattr(screen, "tile_entity", None)
                if tile_entity is not None and slot_inv is tile_entity:
                    class_name = type(tile_entity).__name__.lower()
                    from game.entities.minecart import MinecartEntity
                    if isinstance(tile_entity, MinecartEntity):
                        if tile_entity.cart_type == MinecartEntity.CHEST:
                            target = "minecart"
                            entity_id = int(getattr(tile_entity, "_mp_net_id", 0) or 0)
                    elif "chest" in class_name:
                        target = "chest"
                    elif "furnace" in class_name:
                        target = "furnace"
                    elif "dispenser" in class_name:
                        target = "dispenser"
                    if target in {"chest", "furnace", "dispenser"}:
                        position = (tile_entity.x, tile_entity.y, tile_entity.z)
        if target == "minecart" and entity_id <= 0:
            return True
        if target is None:
            return True  
        slot_index = int(slot.index)
        if target in {"craft2", "craft3"}:
            grid = getattr(screen, "grid", None)
            result_inv = getattr(screen, "result_inv", None)
            if result_inv is not None and getattr(slot, "inventory", None) is result_inv:
                width = 3 if target == "craft3" else 2
                slot_index = width * width  
        self._inventory_tx_sequence += 1
        self.client.send_inventory_transaction(
            self._inventory_tx_sequence, target, slot_index,
            0 if left_click else 1, position=position,
            entity_id=entity_id if target == "minecart" else None,
        )
        return True

    def _approve_remote_inventory_sync(self, event: dict) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            player_id = int(event.get("id"))
        except (TypeError, ValueError, OverflowError):
            return
        remote = self.remote_players.get(player_id)
        if remote is None or not self._is_remote_player_connected(player_id):
            return
        candidate = P.normalize_inventory_state(event.get("state"))
        revision_getter = getattr(self.server, "client_inventory_revision", None)
        if callable(revision_getter):
            current_revision = revision_getter(player_id)
            if current_revision is None:
                return
            try:
                base_revision = int(event.get("revision", current_revision))
            except (TypeError, ValueError, OverflowError):
                return
            if base_revision != current_revision:
                resend = getattr(self.server, "send_client_inventory_snapshot", None)
                if callable(resend):
                    resend(player_id)
                return
        current = self._inventory_snapshot(remote)
        if not self._validate_inventory_snapshot(candidate):
            self.server.set_client_inventory_state(player_id, current)
            return
        candidate["cursor"] = current.get("cursor")
        candidate["selected"] = current.get("selected", 0)
        if not bool(event.get("initial", False)):
            before = self._inventory_item_totals(current)
            after = self._inventory_item_totals(candidate)
            durability_before = self._inventory_durability_totals(current)
            durability_after = self._inventory_durability_totals(candidate)
            minted = any(count > before.get(item_id, 0) for item_id, count in after.items())
            repaired = any(
                after.get(item_id, 0) >= before.get(item_id, 0)
                and durability_after.get(item_id, 0) < durability_before.get(item_id, 0)
                for item_id in set(before) | set(after)
            )
            if minted or repaired:
                self.server.set_client_inventory_state(player_id, current)
                return
        remote.apply_inventory_state(candidate)
        authoritative = self._inventory_snapshot(remote)
        self.server.set_client_inventory_state(player_id, authoritative)
        if self.server_config is not None:
            self.server_config.update_inventory(remote.username, authoritative)

    @staticmethod
    def _stack_from_data(data):
        if not isinstance(data, dict):
            return None
        from game.multiplayer.remote_player import RemoteItemStack
        try:
            return RemoteItemStack(data["id"], data["count"], data["damage"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _slot_valid_for_transaction(target: str, index: int, held, tile_entity=None) -> bool:
        if held is None:
            return True
        if target == "player" and index >= 36:
            try:
                return _mp_item_armor_slot(held.id) == index - 36
            except Exception:
                return False
        if target == "furnace":
            if index == 2:
                return False  
            if index == 1:
                try:
                    from game.content_data import load_item_definitions
                    burn_times = {
                        "planks": 300, "log": 300, "stick": 100, "sapling": 100,
                        "coal": 1600, "wooden_door": 200, "crafting_table": 300,
                        "chest": 300, "bookshelf": 300, "stairs": 300, "fence": 300,
                        "ladder": 300, "sign": 200, "wooden_pressure_plate": 300,
                        "trapdoor": 300, "jukebox": 300, "note_block": 300, "wooden_slab": 150,
                    }
                    if held.id in burn_times or held.id.endswith("_planks"):
                        return True
                    definition = _mp_inventory_item_definition(held.id)
                    return definition.get("material") == "wood" and definition.get("type") in {"tool", "melee_weapon"}
                except Exception:
                    return False
        return True

    @staticmethod
    def _refresh_craft_result(remote, matrix) -> None:
        from game.gameplay.crafting import CraftingManager
        from game.multiplayer.remote_player import RemoteItemStack
        recipe_stack = CraftingManager.get_instance().find_matching_recipe(matrix.cells())
        if recipe_stack is None:
            remote.craft_result = None
        else:
            remote.craft_result = RemoteItemStack(
                recipe_stack.id, recipe_stack.count, getattr(recipe_stack, "damage", 0),
            )

    def _apply_craft_result_click(self, remote, matrix, button: int) -> bool:
        from game.multiplayer.remote_player import RemoteItemStack
        result = getattr(remote, "craft_result", None)
        if result is None:
            return False
        held = getattr(remote, "cursor_stack", None)
        left_click = int(button) == 0
        if held is None:
            remote.cursor_stack = result.copy()
            matrix.consume_one_each()
            return True
        if (held.id == result.id and int(held.damage) == int(getattr(result, "damage", 0))
                and int(held.count) + int(result.count) <= int(getattr(held, "max_stack_size", 64))):
            held.count = int(held.count) + int(result.count)
            matrix.consume_one_each()
            return True
        return False

    @staticmethod
    def _apply_inventory_click(remote, target_inventory, target: str, index: int, button: int) -> bool:
        from game.multiplayer.remote_player import RemoteItemStack
        try:
            stack = target_inventory.get_stack(index)
        except Exception:
            return False
        held = getattr(remote, "cursor_stack", None)
        left_click = int(button) == 0
        if held is None:
            if stack is None:
                return False
            if left_click:
                remote.cursor_stack = stack
                target_inventory.set_stack(index, None)
            else:
                amount = (int(stack.count) + 1) // 2
                remote.cursor_stack = stack.split(amount)
                if int(stack.count) <= 0:
                    target_inventory.set_stack(index, None)
            return True

        if not MultiplayerSession._slot_valid_for_transaction(target, index, held):
            return False
        if stack is None:
            if left_click:
                target_inventory.set_stack(index, held)
                remote.cursor_stack = None
            else:
                target_inventory.set_stack(index, held.split(1))
                if int(held.count) <= 0:
                    remote.cursor_stack = None
            return True

        if stack.id == held.id and int(stack.damage) == int(held.damage):
            try:
                stack_limit = min(
                    int(getattr(stack, "max_stack_size", 64)),
                    int(getattr(target_inventory, "stack_limit", 64)),
                )
            except (TypeError, ValueError, OverflowError):
                stack_limit = 64
            room = stack_limit - int(stack.count)
            moved = min(int(held.count), room) if left_click else min(1, room)
            if moved <= 0:
                return False
            stack.count += moved
            held.count -= moved
            if held.count <= 0:
                remote.cursor_stack = None
            return True

        if left_click and MultiplayerSession._slot_valid_for_transaction(target, index, held):
            target_inventory.set_stack(index, held)
            remote.cursor_stack = stack
            return True
        return False

    def _approve_remote_inventory_drop(self, event: dict) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            player_id = int(event.get("id"))
            slot = int(event.get("slot"))
            count = int(event.get("count"))
        except (TypeError, ValueError, OverflowError):
            return
        remote = self.remote_players.get(player_id)
        world = getattr(self.game, "world", None)
        if (remote is None or world is None or not self._is_remote_player_connected(player_id)
                or not 0 <= slot < 40 or not 1 <= count <= 64):
            return
        inventory = remote.inventory
        if slot < 36:
            stack = inventory.main[slot]
        else:
            armor_slot = slot - 36
            stack = inventory.armor[armor_slot]
        if stack is None or int(stack.count) < count or count > int(getattr(stack, "max_stack_size", 64)):
            return

        from game.entities.item import ItemEntity, ItemStack
        from game.multiplayer.remote_player import RemoteItemStack
        dropped = ItemStack(stack.id, count, stack.damage)
        remaining = int(stack.count) - count
        if slot < 36:
            inventory.main[slot] = (RemoteItemStack(stack.id, remaining, stack.damage) if remaining > 0 else None)
        else:
            inventory.armor[slot - 36] = (RemoteItemStack(stack.id, remaining, stack.damage) if remaining > 0 else None)

        yaw = math.radians(float(getattr(remote, "yaw", 0.0)))
        pitch = math.radians(float(getattr(remote, "pitch", 0.0)))
        power = 0.3
        velocity = (
            -math.sin(yaw) * math.cos(pitch) * power,
            math.sin(pitch) * power + 0.1,
            -math.cos(yaw) * math.cos(pitch) * power,
        )
        position = (float(remote.pos[0]), float(remote.pos[1]) + 1.3, float(remote.pos[2]))
        try:
            world.spawn_entity(ItemEntity(dropped, position, velocity=velocity, pickup_delay=40))
        except Exception:
            if slot < 36:
                inventory.main[slot] = stack
            else:
                inventory.armor[slot - 36] = stack
            return
        self.server.set_client_inventory_state(player_id, self._inventory_snapshot(remote))
        if self.server_config is not None:
            self.server_config.update_inventory(remote.username, self._inventory_snapshot(remote))

    def _approve_remote_inventory_transaction(self, event: dict) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            player_id = int(event.get("id"))
            target = str(event.get("target") or "")
            index = int(event.get("slot"))
            button = int(event.get("button"))
        except (TypeError, ValueError, OverflowError):
            return
        remote = self.remote_players.get(player_id)
        world = getattr(self.game, "world", None)
        if remote is None or world is None or not self._is_remote_player_connected(player_id):
            return
        target_inventory = remote.inventory
        tile_entity = None
        if target == "player":
            if not 0 <= index < 40:
                return
        elif target in {"chest", "furnace", "dispenser"}:
            try:
                x, y, z = int(event.get("x")), int(event.get("y")), int(event.get("z"))
            except (TypeError, ValueError, OverflowError):
                return
            dx = x + 0.5 - float(remote.pos[0])
            dy = y + 0.5 - float(remote.pos[1])
            dz = z + 0.5 - float(remote.pos[2])
            if dx * dx + dy * dy + dz * dz > 6.0 * 6.0:
                return
            tile_entity = getattr(world, "tile_entities", {}).get((x, y, z))
            expected = {"chest": "TileEntityChest", "furnace": "TileEntityFurnace",
                        "dispenser": "TileEntityDispenser"}[target]
            block_type = world.get_block_type(x, y, z)
            valid_blocks = ({"chest"} if target == "chest" else
                            {"furnace", "furnace_lit"} if target == "furnace" else {"dispenser"})
            if (tile_entity is None or type(tile_entity).__name__ != expected
                    or block_type not in valid_blocks):
                return
            if not 0 <= index < ({"chest": 27, "furnace": 3, "dispenser": 9}[target]):
                return
            target_inventory = tile_entity
        elif target == "minecart":
            try:
                entity_id = int(event.get("entity_id"))
            except (TypeError, ValueError, OverflowError):
                return
            from game.entities.minecart import MinecartEntity
            cart = next((entity for entity in getattr(world, "entities", ())
                         if int(getattr(entity, "_mp_net_id", 0) or 0) == entity_id), None)
            if (not isinstance(cart, MinecartEntity) or cart.dead
                    or cart.cart_type != MinecartEntity.CHEST
                    or not 0 <= index < cart.INVENTORY_SIZE):
                return
            dx = float(cart.pos[0]) - float(remote.pos[0])
            dy = float(cart.pos[1]) - float(remote.pos[1])
            dz = float(cart.pos[2]) - float(remote.pos[2])
            if dx * dx + dy * dy + dz * dz > 6.0 * 6.0:
                return
            target_inventory = cart
        elif target in {"craft2", "craft3"}:
            matrix = remote.craft3 if target == "craft3" else remote.craft2
            grid_size = matrix.size
            if index == grid_size:
                changed = self._apply_craft_result_click(remote, matrix, button)
                self._refresh_craft_result(remote, matrix)
            elif 0 <= index < grid_size:
                changed = self._apply_inventory_click(remote, matrix, "player", index, button)
                self._refresh_craft_result(remote, matrix)
            else:
                return
            state = self._inventory_snapshot(remote)
            state["craft"] = {
                "size": 3 if target == "craft3" else 2,
                "slots": matrix.snapshot(),
                "result": (
                    {"id": remote.craft_result.id, "count": remote.craft_result.count,
                     "damage": int(getattr(remote.craft_result, "damage", 0))}
                    if remote.craft_result is not None else None
                ),
            }
            self.server.set_client_inventory_state(player_id, state)
            if self.server_config is not None:
                self.server_config.update_inventory(remote.username, state)
            return
        else:
            return

        changed = self._apply_inventory_click(remote, target_inventory, target, index, button)
        state = self._inventory_snapshot(remote)
        self.server.set_client_inventory_state(player_id, state)
        if self.server_config is not None:
            self.server_config.update_inventory(remote.username, state)
        if changed and tile_entity is not None:
            normalized = P.normalize_tile_entity_state(tile_entity.to_dict())
            if normalized is not None:
                self._last_tile_entity_signatures[(normalized["x"], normalized["y"], normalized["z"])] = self._tile_entity_signature(normalized)
                self.server.broadcast_tile_entity_update(normalized)

    def _apply_local_inventory_state(self, state: dict, *, keep_local_selection: bool = False) -> None:
        player = getattr(self.game, "player", None)
        inventory = getattr(player, "inventory", None)
        if inventory is None:
            return
        local_selected = getattr(inventory, "current_item", 0)
        normalized = P.normalize_inventory_state(state)
        from game.entities.item import ItemStack
        for field, target in (("main", inventory.main), ("armor", inventory.armor)):
            for index, data in enumerate(normalized[field]):
                target[index] = (
                    ItemStack(data["id"], data["count"], data["damage"])
                    if isinstance(data, dict) else None
                )
        inventory.current_item = normalized["selected"]
        cursor = normalized.get("cursor")
        player._mp_cursor_stack = (
            ItemStack(cursor["id"], cursor["count"], cursor["damage"])
            if isinstance(cursor, dict) else None
        )
        current = getattr(self.game, "current_screen", None)
        if hasattr(current, "dragged"):
            current.dragged = player._mp_cursor_stack.copy() if player._mp_cursor_stack is not None else None
        craft = state.get("craft") if isinstance(state, dict) else None
        if isinstance(craft, dict) and current is not None:
            grid = getattr(current, "grid", None)
            result_inv = getattr(current, "result_inv", None)
            slots = craft.get("slots") or ()
            if grid is not None and hasattr(grid, "set_stack"):
                for i in range(min(len(slots), grid.size)):
                    entry = slots[i]
                    if isinstance(entry, dict) and entry.get("id"):
                        grid.set_stack(
                            i, ItemStack(entry["id"], entry.get("count", 1), entry.get("damage", 0)),
                        )
                    else:
                        grid.set_stack(i, None)
            result = craft.get("result")
            if result_inv is not None and hasattr(result_inv, "set_stack"):
                if isinstance(result, dict) and result.get("id"):
                    result_inv.set_stack(
                        0, ItemStack(result["id"], result.get("count", 1), result.get("damage", 0)),
                    )
                else:
                    result_inv.set_stack(0, None)
        self._last_inventory_signature = repr(self._inventory_snapshot(player))
        if keep_local_selection:
            inventory.current_item = local_selected

    def _on_local_animation(self, action: str = "swing") -> None:
        if self.mode == "host" and self.server is not None:
            self.server.broadcast_animation(self.local_id, action)
        elif self.mode == "client" and self.client is not None:
            self.client.send_animation(action)

    def _on_local_attack(self, entity, damage: int) -> bool:
        if self.mode != "client" or self.client is None:
            return False
        try:
            entity_id = int(getattr(entity, "_mp_net_id", 0))
            damage = int(damage)
        except (TypeError, ValueError, OverflowError):
            return False
        if entity_id <= 0 or not 1 <= damage <= 10:
            return False
        self.client.send_attack(entity_id, damage)
        return True

    def _on_local_entity_interact(self, entity, action: str) -> bool:
        if self.mode != "client" or self.client is None:
            return False
        try:
            entity_id = int(getattr(entity, "_mp_net_id", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            return False
        if entity_id <= 0:
            return False
        self.client.send_entity_interact(entity_id, action)
        return True

    def _on_local_entity_push(self, entity) -> bool:
        if self.mode != "client" or self.client is None:
            return False
        try:
            entity_id = int(getattr(entity, "_mp_net_id", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            return False
        if entity_id <= 0:
            return False
        now = time.monotonic()
        last = getattr(entity, "_mp_last_push_request", 0.0)
        if now - float(last) < 0.18:
            return True
        entity._mp_last_push_request = now
        self.client.send_entity_push(entity_id)
        return True

    def _on_local_player_attack(self, target: RemotePlayer, damage: int) -> bool:
        if self.mode != "client" or self.client is None:
            return False
        try:
            target_id = int(getattr(target, "network_id", 0))
            damage = int(damage)
        except (TypeError, ValueError, OverflowError):
            return False
        if target_id <= 0 or target_id == self.local_id or not 1 <= damage <= 10:
            return False
        self.client.send_player_attack(target_id)
        return True

    def _send_remote_player_damage(
        self, player: RemotePlayer, amount: int, attacker, *, knockback=None, source=None,
    ) -> None:
        if self.mode != "host" or self.server is None:
            return
        is_player_attacker = bool(getattr(attacker, "_mp_remote_player", False))
        if attacker is getattr(self.game, "player", None):
            attacker_id = int(self.local_id)
            is_player_attacker = True
        else:
            try:
                attacker_id = int(
                    getattr(attacker, "network_id", 0)
                    or getattr(attacker, "_mp_net_id", 0) or 0
                )
            except (TypeError, ValueError, OverflowError):
                attacker_id = 0
        if source is None:
            source = "player" if is_player_attacker else "monster"
        self.server.send_player_damage(
            player.network_id, amount, attacker_id, source=source, knockback=knockback,
        )

    @staticmethod
    def _player_render_state(player) -> dict:
        return P.normalize_player_effect_state({
            "health": getattr(player, "health", 20),
            "hurt_time": getattr(player, "hurt_time", 0),
            "max_hurt_time": getattr(player, "max_hurt_time", 10),
            "death_time": getattr(player, "death_time", 0),
            "fire_ticks": getattr(player, "_fire_ticks", 0),
            "attacked_at_yaw": getattr(player, "attacked_at_yaw", 0.0),
            "body_yaw": getattr(player, "render_yaw_offset", getattr(player, "yaw", 0.0)),
            "riding": getattr(player, "riding_entity", None) is not None,
            "limb_swing": getattr(player, "limb_swing", 0.0),
            "limb_swing_amount": getattr(player, "limb_swing_amount", 0.0),
            "sneaking": bool(getattr(player, "is_sneaking", False)),
            "sleeping": bool(getattr(player, "is_sleeping", False)),
            "sleep_timer": getattr(player, "sleep_timer", 0),
            "ticks_existed": getattr(player, "ticks_existed", 0),
            "bed_location": getattr(player, "bed_location", None),
            "bed_meta": getattr(player, "_bed_sleep_meta", 0) or 0,
            "pre_sleep_yaw": getattr(player, "_pre_sleep_yaw", getattr(player, "yaw", 0.0)),
            "pre_sleep_pitch": getattr(player, "_pre_sleep_pitch", getattr(player, "pitch", 0.0)),
            "pre_sleep_body_yaw": getattr(
                player, "_pre_sleep_render_yaw_offset",
                getattr(player, "render_yaw_offset", getattr(player, "yaw", 0.0)),
            ),
        })

    def _sync_woken_remote_players(self, players) -> None:
        if self.mode != "host" or self.server is None:
            return
        for remote in tuple(players or ()):
            try:
                self.server.set_client_state(
                    remote.network_id,
                    float(remote.pos[0]), float(remote.pos[1]), float(remote.pos[2]),
                    float(remote.yaw), float(remote.pitch),
                    state=self._player_render_state(remote),
                )
            except (AttributeError, TypeError, ValueError, OverflowError):
                continue

    @staticmethod
    def _entity_kind(entity):
        for entity_class in type(entity).__mro__:
            kind = _MP_ENTITY_KIND_BY_CLASS.get((entity_class.__module__, entity_class.__name__))
            if kind is not None:
                return kind
        return None

    def _entity_state(self, entity, entity_id: int) -> Optional[dict]:
        kind = self._entity_kind(entity)
        if kind is None or getattr(entity, "dead", False):
            return None
        x, y, z = (float(value) for value in entity.pos)
        vx, vy, vz = (float(value) for value in getattr(entity, "vel", (0.0, 0.0, 0.0)))
        common = {
            "id": int(entity_id), "kind": kind,
            "x": x, "y": y, "z": z, "vx": vx, "vy": vy, "vz": vz,
            "yaw": getattr(entity, "yaw", 0.0),
            "pitch": getattr(entity, "pitch", 0.0),
            "health": getattr(entity, "health", 1.0),
            "max_health": (
                int(getattr(entity, "slime_size", 1)) ** 2 if kind == "slime"
                else getattr(type(entity), "HEALTH", getattr(entity, "health", 10.0))
            ),
            "hurt_time": getattr(entity, "hurt_time", 0),
            "death_time": getattr(entity, "death_time", 0),
            "fire_ticks": getattr(entity, "_fire_ticks", 0),
            "age": getattr(entity, "age", getattr(entity, "entity_age", 0)),
            "ticks_existed": getattr(entity, "ticks_existed", 0),
            "limb_swing": getattr(entity, "limb_swing", 0.0),
            "limb_swing_amount": getattr(entity, "limb_swing_amount", 0.0),
            "rotation_pitch": getattr(entity, "rotation_pitch", 0.0),
            "attacked_at_yaw": getattr(entity, "attacked_at_yaw", 0.0),
            "slime_size": getattr(entity, "slime_size", 1),
            "saddled": getattr(entity, "saddled", False),
            "sheared": getattr(entity, "sheared", False),
            "creeper_state": getattr(entity, "creeper_state", -1),
            "time_since_ignited": getattr(entity, "time_since_ignited", 0),
            "wing_rotation": getattr(entity, "wing_rotation", 0.0),
            "prev_wing_rotation": getattr(entity, "prev_wing_rotation", getattr(entity, "wing_rotation", 0.0)),
            "dest_pos": getattr(entity, "dest_pos", 0.0),
            "prev_dest_pos": getattr(entity, "prev_dest_pos", getattr(entity, "dest_pos", 0.0)),
        }
        if kind == "sheep":
            common["fleece_color"] = int(getattr(entity, "fleece_color", 0)) & 15
        if kind == "squid":
            common.update({
                "swim_phase": getattr(entity, "swim_phase", 0.0),
                "prev_swim_phase": getattr(entity, "prev_swim_phase", 0.0),
                "body_yaw_animation": getattr(entity, "body_yaw_animation", 0.0),
                "prev_body_yaw_animation": getattr(entity, "prev_body_yaw_animation", 0.0),
                "tentacle_angle": getattr(entity, "tentacle_angle", 0.0),
                "prev_tentacle_angle": getattr(entity, "prev_tentacle_angle", 0.0),
            })
        if kind == "tnt":
            common["fuse"] = float(getattr(entity, "fuse", 80.0))
        if kind == "item":
            stack = getattr(entity, "item", None)
            if stack is None:
                return None
            common["item"] = {
                "id": str(getattr(stack, "id", "")),
                "count": int(getattr(stack, "count", 1)),
                "damage": int(getattr(stack, "damage", 0)),
            }
            common["pickup_delay"] = getattr(entity, "pickup_delay", 0.0)
            common["hover_start"] = getattr(entity, "hover_start", 0.0)
            common["pickup"] = getattr(entity, "pickup_ticks", None) is not None
        elif kind == "arrow":
            common.update({
                "in_ground": getattr(entity, "in_ground", False),
                "x_tile": getattr(entity, "x_tile", -1),
                "y_tile": getattr(entity, "y_tile", -1),
                "z_tile": getattr(entity, "z_tile", -1),
                "in_tile": getattr(entity, "in_tile", None),
                "arrow_shake": getattr(entity, "arrow_shake", 0.0),
                "ticks_in_ground": getattr(entity, "ticks_in_ground", 0.0),
                "ticks_in_air": getattr(entity, "ticks_in_air", 0.0),
                "fire": getattr(entity, "fire", 0.0),
            })
        elif kind in {"snowball", "egg"}:
            common["ticks_in_air"] = getattr(entity, "ticks_in_air", 0.0)
        elif kind == "painting":
            common.update({
                "tile_x": entity.tile_x, "tile_y": entity.tile_y,
                "tile_z": entity.tile_z, "direction": entity.direction,
                "art": getattr(getattr(entity, "art", None), "title", "Kebab"),
            })
        elif kind == "boat":
            common.update({
                "damage_taken": getattr(entity, "damage_taken", 0),
                "time_since_hit": getattr(entity, "time_since_hit", 0),
                "forward_direction": getattr(entity, "forward_direction", 1),
            })
        elif kind == "minecart":
            cargo = []
            for slot, stack in enumerate(getattr(entity, "cargo_items", ())):
                if stack is None:
                    continue
                cargo.append({
                    "slot": slot, "id": str(getattr(stack, "id", "")),
                    "count": int(getattr(stack, "count", 1)),
                    "damage": int(getattr(stack, "damage", 0)),
                })
            common.update({
                "cart_type": getattr(entity, "cart_type", 0),
                "damage_taken": getattr(entity, "damage_taken", 0),
                "time_since_hit": getattr(entity, "time_since_hit", 0),
                "forward_direction": getattr(entity, "forward_direction", 1),
                "reverse": getattr(entity, "_reverse", False),
                "fuel": getattr(entity, "fuel", 0),
                "push_x": getattr(entity, "push_x", 0.0),
                "push_z": getattr(entity, "push_z", 0.0),
                "cargo": cargo,
            })
        elif kind == "falling_block":
            common.update({
                "block_type": entity.block_type,
                "meta": getattr(entity, "meta", 0),
                "fall_time": getattr(entity, "fall_time", 0.0),
            })
        elif kind == "ghast":
            common.update({
                "charge": getattr(entity, "charge", 0),
                "prev_charge": getattr(entity, "prev_charge", getattr(entity, "charge", 0)),
            })
        elif kind == "fireball":
            acceleration = getattr(entity, "acceleration", (0.0, 0.0, 0.0))
            common.update({
                "ax": acceleration[0], "ay": acceleration[1], "az": acceleration[2],
                "ticks_in_air": getattr(entity, "ticks_in_air", 0.0),
                "in_ground": getattr(entity, "in_ground", False),
                "in_water": getattr(entity, "in_water", False),
                "been_attacked": getattr(entity, "been_attacked", False),
                "x_tile": getattr(entity, "x_tile", -1),
                "y_tile": getattr(entity, "y_tile", -1),
                "z_tile": getattr(entity, "z_tile", -1),
                "in_tile": getattr(entity, "in_tile", None),
            })
        elif kind == "fishing_bobber":
            angler = getattr(entity, "angler", None)
            if angler is getattr(self.game, "player", None):
                angler_id = int(self.local_id)
            else:
                try:
                    angler_id = int(getattr(angler, "network_id", 0) or 0)
                except (TypeError, ValueError, OverflowError):
                    angler_id = 0
            common.update({
                "angler_id": angler_id,
                "bite_ticks": getattr(entity, "bite_ticks", 0),
                "in_ground": getattr(entity, "in_ground", False),
                "x_tile": getattr(entity, "x_tile", -1),
                "y_tile": getattr(entity, "y_tile", -1),
                "z_tile": getattr(entity, "z_tile", -1),
                "in_tile": getattr(entity, "in_tile", None),
                "ticks_in_air": getattr(entity, "ticks_in_air", 0.0),
                "ticks_in_ground": getattr(entity, "ticks_in_ground", 0.0),
            })
        return P.normalize_entity_state(common)

    def _on_local_entity_spawn(self, entity) -> None:
        if self.mode != "client" or self.client is None:
            return
        kind = self._entity_kind(entity)
        if kind == "tnt":
            x = int(round(float(entity.pos[0])))
            y = int(round(float(entity.pos[1]) + 0.49))
            z = int(round(float(entity.pos[2])))
            self.client.send_tnt_ignite(x, y, z)
            return
        if kind in {"arrow", "snowball", "egg", "painting", "boat", "minecart"}:
            state = self._entity_state(entity, 1)
            if state is not None:
                self.client.send_entity_spawn(state)
            return
        if kind != "item":
            return
        inventory_drop = getattr(entity, "_mp_inventory_drop", None)
        if isinstance(inventory_drop, dict):
            try:
                slot = int(inventory_drop.get("slot"))
                count = int(inventory_drop.get("count"))
            except (TypeError, ValueError, OverflowError):
                return
            self._inventory_tx_sequence += 1
            self.client.send_inventory_drop(self._inventory_tx_sequence, slot, count)
            entity.dead = True
            return
        state = self._entity_state(entity, 1)
        if state is not None:
            self.client.send_item_spawn(state)

    def _on_local_fishing_action(self, action: str, entity_id: int = 0) -> None:
        if self.mode != "client" or self.client is None:
            return
        try:
            self.client.send_fishing_action(action, entity_id=entity_id)
        except (TypeError, ValueError, OverflowError):
            return

    def _on_remote_item_pickup(self, entity) -> None:
        if self.mode != "client" or self.client is None:
            return
        try:
            entity_id = int(getattr(entity, "_mp_net_id", 0))
        except (TypeError, ValueError, OverflowError):
            entity_id = 0
        if entity_id > 0:
            now = time.monotonic()
            previous = float(getattr(entity, "_mp_pickup_request_time", 0.0) or 0.0)
            if now - previous < 0.3:
                return
            entity._mp_pickup_request_time = now
            self.client.send_item_pickup(entity_id)

    def _collect_host_entity_states(self) -> list[dict]:
        world = getattr(self.game, "world", None)
        if world is None or not getattr(world, "entities", None):
            return []
        candidates = []
        points = []
        player = getattr(self.game, "player", None)
        if player is not None:
            points.append(tuple(float(v) for v in player.pos))
        if self.server is not None:
            for peer in self.server.snapshot_players():
                points.append((float(peer["x"]), float(peer["y"]), float(peer["z"])))
        radius = max(48.0, float(getattr(world, "view_distance", 4) + 2) * 16.0)
        radius_sq = radius * radius
        for entity in tuple(getattr(world, "entities", ())):
            if entity.dead:
                continue
            kind = self._entity_kind(entity)
            if kind is None:
                continue
            x, y, z = (float(v) for v in entity.pos)
            distance_sq = min(
                ((x - point[0]) ** 2 + (y - point[1]) ** 2 + (z - point[2]) ** 2
                 for point in points),
                default=0.0,
            )
            if points and distance_sq > radius_sq:
                continue
            net_id = getattr(entity, "_mp_net_id", None)
            if net_id is None:
                net_id = self._next_entity_net_id
                self._next_entity_net_id += 1
                entity._mp_net_id = net_id
            state = self._entity_state(entity, int(net_id))
            if state is not None:
                candidates.append((distance_sq, state))
        candidates.sort(key=lambda item: item[0])
        return [state for _, state in candidates[:P.MAX_ENTITY_COUNT]]

    def _sync_world_entities(self, dt: float) -> None:
        if self.mode != "host" or self.server is None:
            return
        self._entity_sync_accum += max(0.0, min(float(dt), 0.25))
        if self._entity_sync_accum < 0.1:
            return
        self._entity_sync_accum %= 0.1
        packet = P.entity_sync_packet(self._collect_host_entity_states())
        self.server.broadcast_entity_sync(packet)

    @staticmethod
    def _remote_entity_class(kind: str):
        from game.entities.mob import Mob
        from game.entities.monster import Monster
        from game.entities.pig import Pig
        from game.entities.cow import Cow
        from game.entities.sheep import Sheep
        from game.entities.chicken import Chicken
        from game.entities.zombie import Zombie
        from game.entities.pigzombie import ZombiePigman
        from game.entities.skeleton import Skeleton
        from game.entities.creeper import Creeper
        from game.entities.spider import Spider
        from game.entities.slime import Slime
        from game.entities.giant import Giant
        from game.entities.ghast import Ghast
        from game.entities.squid import Squid
        return {
            "zombie_pigman": ZombiePigman,
            "pig": Pig, "cow": Cow, "sheep": Sheep, "chicken": Chicken,
            "zombie": Zombie, "skeleton": Skeleton, "creeper": Creeper,
            "spider": Spider, "slime": Slime, "giant": Giant,
            "mob": Mob, "monster": Monster, "ghast": Ghast, "squid": Squid,
        }.get(kind)

    def _create_remote_entity(self, state):
        from game.entities.entity import Entity
        from game.entities.item import ItemEntity, ItemStack
        from game.entities.tnt import PrimedTNT

        world = getattr(self.game, "world", None)
        if world is None:
            return None
        pos = (state["x"], state["y"], state["z"])
        if state["kind"] == "tnt":
            entity = PrimedTNT(pos, fuse=state.get("fuse", 80.0))
        elif state["kind"] == "fireball":
            from game.entities.fireball import FireballEntity
            entity = FireballEntity(
                world, pos,
                acceleration=(state.get("ax", 0.0), state.get("ay", 0.0), state.get("az", 0.0)),
            )
            entity.age = float(state.get("age", 0.0))
            entity.ticks_in_air = float(state.get("ticks_in_air", 0.0))
            entity.in_ground = bool(state.get("in_ground", False))
            entity.in_water = bool(state.get("in_water", False))
            entity.been_attacked = bool(state.get("been_attacked", False))
            entity.x_tile, entity.y_tile, entity.z_tile = (
                state.get("x_tile", -1), state.get("y_tile", -1), state.get("z_tile", -1),
            )
            entity.in_tile = state.get("in_tile")
        elif state["kind"] == "fishing_bobber":
            from game.entities.fishing_bobber import FishingBobberEntity
            angler_id = int(state.get("angler_id", 0))
            angler = (
                getattr(self.game, "player", None) if angler_id == self.local_id
                else self.remote_players.get(angler_id)
            )
            entity = FishingBobberEntity(
                world, pos, velocity=(state["vx"], state["vy"], state["vz"]),
                angler=angler,
            )
            entity.bite_ticks = int(state.get("bite_ticks", 0))
            entity.in_ground = bool(state.get("in_ground", False))
            entity.x_tile, entity.y_tile, entity.z_tile = (
                state.get("x_tile", -1), state.get("y_tile", -1), state.get("z_tile", -1),
            )
            entity.in_tile = state.get("in_tile")
            entity.ticks_in_air = float(state.get("ticks_in_air", 0.0))
            entity.ticks_in_ground = float(state.get("ticks_in_ground", 0.0))
        elif state["kind"] == "item":
            stack_data = state["item"]
            stack = ItemStack(stack_data["id"], stack_data["count"], stack_data["damage"])
            entity = ItemEntity(
                stack, pos, velocity=(state["vx"], state["vy"], state["vz"]),
                pickup_delay=state.get("pickup_delay", 0.0),
            )
            entity.age = float(state.get("age", 0.0))
            entity.hover_start = float(state.get("hover_start", 0.0))
        elif state["kind"] == "arrow":
            from game.entities.arrow import ArrowEntity
            entity = ArrowEntity(world, pos, velocity=(state["vx"], state["vy"], state["vz"]))
            entity.in_ground = state.get("in_ground", False)
            entity.x_tile, entity.y_tile, entity.z_tile = (
                state.get("x_tile", -1), state.get("y_tile", -1), state.get("z_tile", -1),
            )
            entity.in_tile = state.get("in_tile")
            entity.arrow_shake = state.get("arrow_shake", 0.0)
            entity.ticks_in_ground = state.get("ticks_in_ground", 0.0)
            entity.ticks_in_air = state.get("ticks_in_air", 0.0)
            entity.fire = state.get("fire", 0.0)
        elif state["kind"] == "snowball":
            from game.entities.snowball import SnowballEntity
            entity = SnowballEntity(world, pos, velocity=(state["vx"], state["vy"], state["vz"]))
            entity.ticks_in_air = state.get("ticks_in_air", 0.0)
            entity.age = state.get("age", 0.0)
        elif state["kind"] == "egg":
            from game.entities.egg import EggEntity
            entity = EggEntity(world, pos, velocity=(state["vx"], state["vy"], state["vz"]))
            entity.ticks_in_air = state.get("ticks_in_air", 0.0)
            entity.age = state.get("age", 0.0)
        elif state["kind"] == "painting":
            from game.entities.painting import ART_TYPES, PaintingEntity
            art = next((art for art in ART_TYPES if art.title == state.get("art")), ART_TYPES[0])
            entity = PaintingEntity(
                world, state["tile_x"], state["tile_y"], state["tile_z"],
                state["direction"], art=art,
            )
        elif state["kind"] == "boat":
            from game.entities.boat import BoatEntity
            entity = BoatEntity(world, pos, velocity=(state["vx"], state["vy"], state["vz"]))
            entity.damage_taken = state.get("damage_taken", 0)
            entity.time_since_hit = state.get("time_since_hit", 0)
            entity.forward_direction = state.get("forward_direction", 1)
        elif state["kind"] == "minecart":
            from game.entities.minecart import MinecartEntity
            from game.entities.item import ItemStack
            entity = MinecartEntity(
                world, pos, velocity=(state["vx"], state["vy"], state["vz"]),
                cart_type=state.get("cart_type", 0),
            )
            entity.damage_taken = state.get("damage_taken", 0)
            entity.time_since_hit = state.get("time_since_hit", 0)
            entity.forward_direction = state.get("forward_direction", 1)
            entity._reverse = state.get("reverse", False)
            entity.fuel = state.get("fuel", 0)
            entity.push_x = state.get("push_x", 0.0)
            entity.push_z = state.get("push_z", 0.0)
            for entry in state.get("cargo", ()):
                entity.set_stack(
                    entry["slot"], ItemStack(entry["id"], entry["count"], entry["damage"]),
                )
        elif state["kind"] == "falling_block":
            from game.entities.falling_block import FallingBlockEntity
            entity = FallingBlockEntity(
                state["block_type"], pos,
                vel=(state["vx"], state["vy"], state["vz"]),
                meta=state.get("meta", 0), fall_time=state.get("fall_time", 0.0),
            )
        else:
            cls = self._remote_entity_class(state["kind"])
            if cls is None:
                return None
            try:
                entity = cls(pos, yaw=state["yaw"])
            except Exception:
                return None
            if state["kind"] == "slime":
                setter = getattr(entity, "set_slime_size", None)
                if callable(setter):
                    setter(int(state.get("slime_size", 1)))
            if state["kind"] == "sheep":
                entity.set_fleece_color(int(state.get("fleece_color", 0)))
        entity._mp_remote_entity = True
        entity._mp_remote_item = state["kind"] == "item"
        entity._mp_kind = state["kind"]
        entity._mp_net_id = int(state["id"])
        entity._mp_target_pos = [float(state["x"]), float(state["y"]), float(state["z"])]
        entity._mp_target_vel = [float(state["vx"]), float(state["vy"]), float(state["vz"])]
        entity._mp_target_yaw = float(state["yaw"])
        entity._mp_target_pitch = float(state.get("rotation_pitch", state.get("pitch", 0.0)))
        entity.prev_pos = list(entity.pos)
        if hasattr(entity, "renderer"):
            entity.renderer = getattr(self.game, "renderer", None)
        if hasattr(entity, "set_slime_size"):
            entity.set_slime_size(int(state.get("slime_size", getattr(entity, "slime_size", 1))))
        world.spawn_entity(entity)
        self.remote_entities[int(state["id"])] = entity
        return entity

    def _reconcile_local_item_copy(self, state) -> None:
        from game.entities.item import ItemEntity

        world = getattr(self.game, "world", None)
        if world is None:
            return
        stack = state.get("item") or {}
        for entity in tuple(getattr(world, "entities", ())):
            if (not isinstance(entity, ItemEntity) or entity.dead
                    or getattr(entity, "_mp_remote_entity", False)):
                continue
            local_stack = getattr(entity, "item", None)
            if local_stack is None or str(getattr(local_stack, "id", "")) != stack.get("id"):
                continue
            if int(getattr(local_stack, "count", 0)) != int(stack.get("count", 0)):
                continue
            dx = float(entity.pos[0]) - float(state["x"])
            dy = float(entity.pos[1]) - float(state["y"])
            dz = float(entity.pos[2]) - float(state["z"])
            if dx * dx + dy * dy + dz * dz <= 6.25:
                entity.dead = True
                return

    def _apply_remote_entity_sync(self, event) -> None:
        if self.mode != "client":
            return
        world = getattr(self.game, "world", None)
        if world is None:
            return
        states = event.get("entities")
        if not isinstance(states, list):
            return
        seen = set()
        for raw in states[:P.MAX_ENTITY_COUNT]:
            state = P.normalize_entity_state(raw)
            if state is None:
                continue
            entity_id = state["id"]
            seen.add(entity_id)
            entity = self.remote_entities.get(entity_id)
            if entity is not None and getattr(entity, "dead", False):
                self.remote_entities.pop(entity_id, None)
                entity = None
            if entity is not None and self._entity_kind(entity) != state["kind"]:
                entity.dead = True
                self.remote_entities.pop(entity_id, None)
                entity = None
            if entity is None:
                if state["kind"] == "item":
                    self._reconcile_local_item_copy(state)
                entity = self._create_remote_entity(state)
                if entity is None:
                    continue
            entity._mp_kind = state["kind"]
            entity._mp_target_pos = [state["x"], state["y"], state["z"]]
            entity._mp_target_vel = [state["vx"], state["vy"], state["vz"]]
            entity._mp_target_yaw = state["yaw"]
            entity._mp_target_pitch = state.get("rotation_pitch", state.get("pitch", 0.0))
            entity.vel[:] = [state["vx"], state["vy"], state["vz"]]
            entity.health = state["health"]
            if state["kind"] == "tnt":
                entity.fuse = state.get("fuse", entity.fuse)
            entity.hurt_time = state["hurt_time"]
            entity.death_time = state["death_time"]
            entity._fire_ticks = state["fire_ticks"]
            entity.ticks_existed = state["ticks_existed"]
            entity._mp_target_limb_swing = state["limb_swing"]
            entity._mp_target_limb_swing_amount = state["limb_swing_amount"]
            entity.prev_rotation_pitch = getattr(
                entity, "rotation_pitch", state["rotation_pitch"]
            )
            entity.attacked_at_yaw = state["attacked_at_yaw"]
            entity.rotation_pitch = state["rotation_pitch"]
            if hasattr(entity, "prev_health"):
                entity.prev_health = entity.health
            if state["kind"] == "item":
                entity.pickup_delay = state["pickup_delay"]
                entity._mp_pickup_callback = self._on_remote_item_pickup
                if state.get("pickup", False):
                    if entity.pickup_ticks is None:
                        entity.pickup_ticks = 0.0
                        entity._pickup_from = tuple(entity.pos)
                        entity._collector = getattr(self.game, "player", None)
                elif entity.pickup_ticks is not None:
                    entity.pickup_ticks = None
                    entity._pickup_from = None
                    entity._collector = None
                    entity.dead = False
            elif state["kind"] in P._MOB_ENTITY_KINDS:
                entity.slime_size = state["slime_size"] if state["kind"] == "slime" else getattr(entity, "slime_size", 1)
                if state["kind"] == "slime" and callable(getattr(entity, "set_slime_size", None)):
                    entity.set_slime_size(state["slime_size"])
                    entity.health = state["health"]
                    entity.prev_health = entity.health
                entity.saddled = state["saddled"]
                entity.sheared = state["sheared"]
                if state["kind"] == "sheep":
                    entity.set_fleece_color(state.get("fleece_color", 0))
                elif state["kind"] == "squid":
                    entity.swim_phase = state["swim_phase"]
                    entity.prev_swim_phase = state["prev_swim_phase"]
                    entity.body_yaw_animation = state["body_yaw_animation"]
                    entity.prev_body_yaw_animation = state["prev_body_yaw_animation"]
                    entity.tentacle_angle = state["tentacle_angle"]
                    entity.prev_tentacle_angle = state["prev_tentacle_angle"]
                entity.creeper_state = state["creeper_state"]
                entity.time_since_ignited = state["time_since_ignited"]
                entity.wing_rotation = state["wing_rotation"]
                entity.prev_wing_rotation = state["prev_wing_rotation"]
                entity.dest_pos = state["dest_pos"]
                entity.prev_dest_pos = state["prev_dest_pos"]
            if state["kind"] == "arrow":
                entity.in_ground = state["in_ground"]
                entity.x_tile, entity.y_tile, entity.z_tile = (
                    state["x_tile"], state["y_tile"], state["z_tile"],
                )
                entity.in_tile = state["in_tile"]
                entity.arrow_shake = state["arrow_shake"]
                entity.ticks_in_ground = state["ticks_in_ground"]
                entity.ticks_in_air = state["ticks_in_air"]
                entity.fire = state["fire"]
            elif state["kind"] in {"snowball", "egg"}:
                entity.ticks_in_air = state["ticks_in_air"]
                entity.age = state["age"]
            elif state["kind"] == "boat":
                entity.damage_taken = state["damage_taken"]
                entity.time_since_hit = state["time_since_hit"]
                entity.forward_direction = state["forward_direction"]
            elif state["kind"] == "minecart":
                from game.entities.item import ItemStack
                entity.cart_type = state["cart_type"]
                entity.damage_taken = state["damage_taken"]
                entity.time_since_hit = state["time_since_hit"]
                entity.forward_direction = state["forward_direction"]
                entity._reverse = state["reverse"]
                entity.fuel = state["fuel"]
                entity.push_x = state["push_x"]
                entity.push_z = state["push_z"]
                wanted_cargo = {entry["slot"]: entry for entry in state["cargo"]}
                for slot in range(entity.INVENTORY_SIZE):
                    entry = wanted_cargo.get(slot)
                    entity.cargo_items[slot] = (
                        ItemStack(entry["id"], entry["count"], entry["damage"])
                        if entry is not None else None
                    )
            elif state["kind"] == "falling_block":
                entity.block_type = state["block_type"]
                entity.meta = state["meta"]
                entity.fall_time = state["fall_time"]
            elif state["kind"] == "ghast":
                entity.charge = state["charge"]
                entity.prev_charge = state["prev_charge"]
                entity.skin_path = "mob/ghast_fire.png" if entity.charge > 10 else "mob/ghast.png"
            elif state["kind"] == "fireball":
                entity.acceleration[:] = [state["ax"], state["ay"], state["az"]]
                entity.age = state["age"]
                entity.ticks_in_air = state["ticks_in_air"]
                entity.in_ground = state["in_ground"]
                entity.in_water = state["in_water"]
                entity.been_attacked = state["been_attacked"]
                entity.x_tile, entity.y_tile, entity.z_tile = (
                    state["x_tile"], state["y_tile"], state["z_tile"],
                )
                entity.in_tile = state["in_tile"]
            elif state["kind"] == "fishing_bobber":
                angler_id = state["angler_id"]
                angler = (
                    getattr(self.game, "player", None) if angler_id == self.local_id
                    else self.remote_players.get(angler_id)
                )
                previous_angler = getattr(entity, "angler", None)
                if previous_angler is not angler:
                    detach = getattr(entity, "_detach_from_owner", None)
                    if callable(detach):
                        detach()
                    entity.angler = angler
                    entity.shooting_entity = angler
                if angler is not None:
                    angler.fishing_hook = entity
                entity.bite_ticks = state["bite_ticks"]
                entity.in_ground = state["in_ground"]
                entity.x_tile, entity.y_tile, entity.z_tile = (
                    state["x_tile"], state["y_tile"], state["z_tile"],
                )
                entity.in_tile = state["in_tile"]
                entity.ticks_in_air = state["ticks_in_air"]
                entity.ticks_in_ground = state["ticks_in_ground"]
            if hasattr(entity, "pitch"):
                entity.pitch = state["pitch"]
        for entity_id, entity in list(self.remote_entities.items()):
            if entity_id not in seen:
                detach = getattr(entity, "_detach_from_owner", None)
                if callable(detach):
                    detach()
                entity.dead = True
                self.remote_entities.pop(entity_id, None)
        world.entities = [entity for entity in world.entities if not (entity.dead and getattr(entity, "_mp_remote_entity", False))]

    def _update_remote_entities(self, dt: float) -> None:
        dt = max(0.0, min(float(dt), 0.25))
        if not self.remote_entities:
            return
        factor = min(1.0, dt * 12.0)
        for entity in tuple(self.remote_entities.values()):
            if entity.dead:
                continue
            entity.prev_pos = list(entity.pos)
            target = getattr(entity, "_mp_target_pos", entity.pos)
            for index in range(3):
                entity.pos[index] += (target[index] - entity.pos[index]) * factor
            if hasattr(entity, "yaw"):
                entity.prev_yaw = entity.yaw
                target_yaw = float(getattr(entity, "_mp_target_yaw", entity.yaw))
                yaw_delta = (target_yaw - entity.yaw + 180.0) % 360.0 - 180.0
                entity.yaw += yaw_delta * factor
                entity.prev_render_yaw_offset = getattr(entity, "render_yaw_offset", entity.yaw)
                if getattr(entity, "_mp_kind", None) == "ghast":
                    target_body_yaw = 180.0 - target_yaw
                    body_delta = (
                        target_body_yaw - entity.render_yaw_offset + 180.0
                    ) % 360.0 - 180.0
                    entity.render_yaw_offset += body_delta * factor
                else:
                    entity.render_yaw_offset = entity.yaw
            if getattr(entity, "_mp_remote_entity", False) and hasattr(entity, "limb_swing"):
                current_amount = float(getattr(entity, "limb_swing_amount", 0.0))
                target_amount = max(0.0, min(1.0, float(
                    getattr(entity, "_mp_target_limb_swing_amount", current_amount)
                )))
                entity.prev_limb_swing_amount = current_amount
                entity.limb_swing_amount = current_amount + (target_amount - current_amount) * min(1.0, dt * 10.0)
                entity.prev_limb_swing = float(getattr(entity, "limb_swing", 0.0))
                predicted = entity.prev_limb_swing + entity.limb_swing_amount * dt * 20.0
                period = (2.0 * math.pi) / 0.6662
                target_phase = float(getattr(entity, "_mp_target_limb_swing", predicted))
                phase_error = (target_phase - predicted + period * 0.5) % period - period * 0.5
                entity.limb_swing = predicted + phase_error * min(1.0, dt * 3.0)
            if getattr(entity, "_mp_remote_item", False):
                entity.age = max(0.0, float(getattr(entity, "age", 0.0)) + dt * 20.0)
                if entity.pickup_ticks is not None:
                    entity.pickup_ticks += dt * 20.0
                    if entity.pickup_ticks >= 3.0:
                        entity.dead = True
            elif getattr(entity, "_mp_remote_entity", False):
                position_rider = getattr(entity, "_position_rider", None)
                if callable(position_rider) and getattr(entity, "rider", None) is not None:
                    try:
                        position_rider()
                    except TypeError:
                        position_rider(1.0)
                    except Exception:
                        pass

    def _sync_local_inventory(self, player) -> None:
        state = self._inventory_snapshot(player)
        signature = repr(state)
        if signature == self._last_inventory_signature:
            return
        if self.mode == "host" and self.server is not None:
            self.server.set_host_inventory(state)
        elif self.mode == "client" and self.client is not None and self.client.player_id > 0:
            self.client.send_inventory(state)
        else:
            return
        self._last_inventory_signature = signature

    def _sync_local_break_progress(self, player, dt: float) -> None:
        self._break_accum += max(0.0, min(float(dt), 0.25))
        digging = bool(getattr(player, "_digging", False))
        target = getattr(player, "_dig_target", None) if digging else None
        if target is not None:
            try:
                target = tuple(int(v) for v in target)
            except (TypeError, ValueError, OverflowError):
                target = None
        try:
            progress = max(0.0, min(0.99, float(getattr(player, "_destroy_progress", 0.0))))
        except (TypeError, ValueError, OverflowError):
            progress = 0.0
        stage = int(progress * 10.0) if target is not None else 0
        signature = (target, stage)
        if signature == self._last_break_signature:
            return
        visual_progress = (stage / 10.0) if stage > 0 else (0.01 if target is not None else 0.0)
        if self.mode == "host" and self.server is not None:
            self.server.set_host_break_progress(target, visual_progress)
        elif self.mode == "client" and self.client is not None and self.client.player_id > 0:
            self.client.send_break_progress(target, visual_progress)
        else:
            return
        self._last_break_signature = signature

    @staticmethod
    def _tile_entity_signature(state: dict) -> str:
        return repr(tuple(sorted(state.items())))

    @staticmethod
    def _tile_entity_states_for_chunk(world, cx: int, cz: int) -> list[dict]:
        states = []
        for (x, _y, z), tile_entity in list(getattr(world, "tile_entities", {}).items()):
            if tile_entity is None or world._get_chunk_index(x, z) != (int(cx), int(cz)):
                continue
            try:
                state = P.normalize_tile_entity_state(tile_entity.to_dict())
            except Exception:
                state = None
            if state is not None:
                states.append(state)
        states.sort(key=lambda state: (state["x"], state["y"], state["z"]))
        return states[:P.MAX_TILE_ENTITY_COUNT]

    def _apply_tile_entity_state(self, state: dict) -> bool:
        world = getattr(self.game, "world", None)
        normalized = P.normalize_tile_entity_state(state)
        if world is None or normalized is None:
            return False
        x, y, z = normalized["x"], normalized["y"], normalized["z"]
        te_type = normalized["type"]
        block_type = world.get_block_type(x, y, z)
        valid_blocks = {
            "chest": {"chest"},
            "furnace": {"furnace", "furnace_lit"},
            "dispenser": {"dispenser"},
            "sign": {"sign", "wall_sign"},
            "mob_spawner": {"mob_spawner"},
            "note_block": {"note_block"},
        }
        if block_type not in valid_blocks[te_type]:
            return False

        from game.block.tile_entity import (
            TileEntityChest, TileEntityDispenser, TileEntityFurnace,
            TileEntityMobSpawner, TileEntitySign, TileEntityNote,
        )
        from game.entities.item import ItemStack
        expected_cls = {
            "chest": TileEntityChest,
            "furnace": TileEntityFurnace,
            "dispenser": TileEntityDispenser,
            "sign": TileEntitySign,
            "mob_spawner": TileEntityMobSpawner,
            "note_block": TileEntityNote,
        }[te_type]
        key = (x, y, z)
        te = getattr(world, "tile_entities", {}).get(key)
        if not isinstance(te, expected_cls):
            te = expected_cls(world, x, y, z)
            world.tile_entities[key] = te

        if te_type == "chest":
            slots = [None] * te.SIZE
            for entry in normalized["items"]:
                slots[entry["slot"]] = ItemStack(
                    entry["id"], entry["count"], entry["damage"],
                )
            te.contents = slots
        elif te_type == "furnace":
            slots = [None] * 3
            for entry in normalized["items"]:
                slots[entry["slot"]] = ItemStack(
                    entry["id"], entry["count"], entry["damage"],
                )
            te.furnace_item_stacks = slots
            te.furnace_burn_time = normalized["burn"]
            te.furnace_cook_time = normalized["cook"]
            te.current_item_burn_time = normalized["burn_total"]
        elif te_type == "dispenser":
            slots = [None] * 9
            for entry in normalized["items"]:
                slots[entry["slot"]] = ItemStack(
                    entry["id"], entry["count"], entry["damage"],
                )
            te.contents = slots
        elif te_type == "sign":
            allowed = set(te.ALLOWED_CHARS)
            te.sign_text = [
                "".join(char for char in line if char in allowed)[:te.MAX_LINE_LENGTH]
                for line in normalized["text"]
            ]
        elif te_type == "note_block":
            te.note = normalized["note"]
        else:
            te.mob_id = normalized["entity_id"]
            te.delay = normalized["delay"]

        self._last_tile_entity_signatures[key] = self._tile_entity_signature(normalized)
        return True

    def _remember_chunk_sync_tile_entity_delta(self, state: dict) -> None:
        if self.mode != "client" or not self._client_chunk_pending:
            return
        world = getattr(self.game, "world", None)
        normalized = P.normalize_tile_entity_state(state)
        if world is None or normalized is None:
            return
        try:
            key = world._get_chunk_index(normalized["x"], normalized["z"])
        except (AttributeError, TypeError, ValueError):
            return
        if key in self._client_chunk_pending:
            self._client_chunk_sync_tile_entity_deltas.setdefault(key, {})[
                (normalized["x"], normalized["y"], normalized["z"])
            ] = normalized

    def _approve_remote_tile_entity_request(self, event: dict) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            player_id = int(event.get("id"))
        except (TypeError, ValueError, OverflowError):
            return
        if not self._is_remote_player_connected(player_id):
            return
        state = P.normalize_tile_entity_state(event.get("state"))
        if state is None or state["type"] != "sign":
            return
        player = self.remote_players.get(player_id)
        world = getattr(self.game, "world", None)
        if player is None or world is None:
            return
        x, y, z = state["x"], state["y"], state["z"]
        dx = x + 0.5 - float(player.pos[0])
        dy = y + 0.5 - float(player.pos[1])
        dz = z + 0.5 - float(player.pos[2])
        if dx * dx + dy * dy + dz * dz > 6.0 * 6.0:
            return
        block_type = world.get_block_type(x, y, z)
        expected_blocks = {
            "chest": {"chest"},
            "furnace": {"furnace", "furnace_lit"},
            "dispenser": {"dispenser"},
            "sign": {"sign", "wall_sign"},
        }
        if block_type not in expected_blocks[state["type"]]:
            return

        key = (x, y, z)
        existing = getattr(world, "tile_entities", {}).get(key)
        if existing is None:
            from game.block.tile_entity import (
                TileEntityChest, TileEntityFurnace, TileEntitySign,
            )
            expected_cls = {
                "chest": TileEntityChest,
                "furnace": TileEntityFurnace,
                "sign": TileEntitySign,
            }[state["type"]]
            existing = expected_cls(world, x, y, z)
            world.tile_entities[key] = existing

        from game.entities.item import ItemStack
        if state["type"] == "chest":
            slots = [None] * 27
            for entry in state["items"]:
                slots[entry["slot"]] = ItemStack(
                    entry["id"], entry["count"], entry["damage"],
                )
            existing.contents = slots
        elif state["type"] == "furnace":
            slots = [None] * 3
            for entry in state["items"]:
                slots[entry["slot"]] = ItemStack(
                    entry["id"], entry["count"], entry["damage"],
                )
            existing.furnace_item_stacks = slots
        elif state["type"] == "sign":
            allowed = set(existing.ALLOWED_CHARS)
            existing.sign_text = [
                "".join(char for char in line if char in allowed)[:15]
                for line in state["text"]
            ]
        else:
            allowed = set(existing.ALLOWED_CHARS)
            existing.sign_text = [
                "".join(char for char in line if char in allowed)[:15]
                for line in state["text"]
            ]

        authoritative = P.normalize_tile_entity_state(existing.to_dict())
        if authoritative is None:
            return
        self._last_tile_entity_signatures[key] = self._tile_entity_signature(authoritative)
        self.server.broadcast_tile_entity_update(authoritative)

    def _sync_tile_entities(self, dt: float) -> None:
        world = getattr(self.game, "world", None)
        if world is None:
            return
        self._tile_entity_sync_accum += max(0.0, min(float(dt), 0.25))
        if self._tile_entity_sync_accum < 0.1:
            return
        self._tile_entity_sync_accum %= 0.1

        for key, te in list(getattr(world, "tile_entities", {}).items()):
            if te is None:
                continue
            try:
                state = P.normalize_tile_entity_state(te.to_dict())
            except Exception:
                state = None
            if state is None:
                continue
            signature = self._tile_entity_signature(state)
            if signature == self._last_tile_entity_signatures.get(key):
                continue
            if self.mode == "host" and self.server is not None:
                self.server.broadcast_tile_entity_update(state)
                self._last_tile_entity_signatures[key] = signature
            elif self.mode == "client" and self.client is not None:
                try:
                    chunk_key = world._get_chunk_index(key[0], key[2])
                    chunk = world.chunks.get(chunk_key)
                except (AttributeError, KeyError, TypeError, ValueError):
                    chunk = None
                if chunk is None or not bool(getattr(chunk, "_mp_snapshot_applied", False)):
                    continue
                if state["type"] == "sign":
                    self.client.send_tile_entity(state)
                    self._last_tile_entity_signatures[key] = signature

    def _apply_remote_block(self, event: dict) -> None:
        world = getattr(self.game, "world", None)
        if world is None:
            return
        try:
            x, y, z = int(event["x"]), int(event["y"]), int(event["z"])
            block_type = event.get("block")
            if block_type is not None:
                block_type = str(block_type)
            meta = max(0, min(15, int(event.get("meta", 0))))
            if 0 <= y < int(getattr(world, "CHUNK_HEIGHT", 128)):
                old_type = world.get_block_type(x, y, z)
                old_meta = int(world.get_metadata(x, y, z)) & 15
                world.set_block(x, y, z, block_type, meta=meta, immediate_mesh=True)
                if (self.mode == "host" and block_type == "stone_button"
                        and (meta & 8) and (old_type != "stone_button" or not (old_meta & 8))):
                    redstone = getattr(world, "_redstone_system", None)
                    if redstone is not None:
                        redstone.schedule_button_release(x, y, z, 20)
                if self.mode == "client":
                    self._sync_jukebox_audio_state(
                        world, self._jukebox_audio_states, x, y, z,
                        block_type, meta,
                        player=getattr(self.game, "player", None),
                        announce=True,
                    )
        except (KeyError, TypeError, ValueError, OverflowError):
            return

    def _sync_jukebox_audio_for_chunk(self, chunk) -> None:
        world = getattr(self.game, "world", None)
        if self.mode != "client" or world is None or chunk is None:
            return
        try:
            cx, cz = int(chunk.chunk_x), int(chunk.chunk_z)
        except (AttributeError, TypeError, ValueError):
            try:
                cx, cz = world._get_chunk_index(chunk.start_x, chunk.start_z)
            except Exception:
                return
        height = int(getattr(chunk, "height", 128))
        depth = int(getattr(chunk, "depth", 16))
        palette = getattr(chunk, "_id_to_block_type", ())
        found = set()
        for index, block_id in enumerate(getattr(chunk, "blocks", ())):
            if not block_id or block_id >= len(palette) or palette[block_id] != "jukebox":
                continue
            y = index % height
            column = index // height
            lz = column % depth
            lx = column // depth
            x, z = int(chunk.start_x) + lx, int(chunk.start_z) + lz
            meta = int(chunk.metadata[index]) & 15
            found.add((x, y, z))
            self._sync_jukebox_audio_state(
                world, self._jukebox_audio_states, x, y, z, "jukebox", meta,
            )
        for key in tuple(self._jukebox_audio_states):
            try:
                key_cx, key_cz = world._get_chunk_index(key[0], key[2])
            except Exception:
                continue
            if (key_cx, key_cz) == (cx, cz) and key not in found:
                self._sync_jukebox_audio_state(
                    world, self._jukebox_audio_states, *key, None, 0,
                )

    def _remember_chunk_sync_delta(self, event: dict) -> None:
        if self.mode != "client" or not self._client_chunk_pending:
            return
        world = getattr(self.game, "world", None)
        if world is None:
            return
        try:
            x, y, z = int(event["x"]), int(event["y"]), int(event["z"])
            key = world._get_chunk_index(x, z)
        except (KeyError, TypeError, ValueError, OverflowError):
            return
        if key not in self._client_chunk_pending:
            return
        delta = dict(event)
        self._client_chunk_sync_deltas.setdefault(key, {})[(x, y, z)] = delta

    def _request_client_chunk_snapshots(self, dt: float) -> None:
        client = self.client
        world = getattr(self.game, "world", None)
        player = getattr(self.game, "player", None)
        if client is None or not client.connected or world is None or player is None:
            return
        self._client_chunk_request_accum += max(0.0, min(float(dt), 0.25))
        if self._client_chunk_request_accum < 0.05:
            return
        self._client_chunk_request_accum %= 0.05
        try:
            center_cx, center_cz = world._get_chunk_index(player.pos[0], player.pos[2])
            radius = int(world._chunk_load_radius())
        except (AttributeError, TypeError, ValueError):
            return
        radius_sq = radius * radius
        now = time.monotonic()
        candidates = []
        for key, chunk in tuple(getattr(world, "chunks", {}).items()):
            cx, cz = key
            distance_sq = (cx - center_cx) ** 2 + (cz - center_cz) ** 2
            if distance_sq > radius_sq:
                continue
            if bool(getattr(chunk, "_mp_snapshot_applied", False)):
                continue
            if key in self._client_chunk_pending:
                continue
            if now < self._client_chunk_retry_after.get(key, 0.0):
                continue
            candidates.append((distance_sq, cx, cz))
        candidates.sort()
        available = max(0, 64 - len(self._client_chunk_pending))
        for _distance_sq, cx, cz in candidates[:min(4, available)]:
            key = (cx, cz)
            self._client_chunk_pending.add(key)
            client.request_chunk(cx, cz)

    def _queue_host_chunk_request(self, event: dict) -> None:
        try:
            player_id, cx, cz = int(event.get("id")), int(event.get("cx")), int(event.get("cz"))
        except (TypeError, ValueError, OverflowError):
            return
        key = (player_id, cx, cz)
        if key in self._host_chunk_request_keys or len(self._host_chunk_requests) >= 4096:
            return
        self._host_chunk_request_keys.add(key)
        self._host_chunk_requests.append(key)

    @staticmethod
    def _remove_from_queue(queue, key) -> None:
        if queue is None:
            return
        try:
            while True:
                queue.remove(key)
        except (ValueError, AttributeError):
            return

    @staticmethod
    def _chunk_neighborhood(cx: int, cz: int):
        return ((cx, cz), (cx + 1, cz), (cx, cz + 1), (cx + 1, cz + 1))

    def _refresh_host_chunk_stream_keys(self, world) -> None:
        needed = set()
        for _player_id, cx, cz in self._host_chunk_requests:
            needed.update(self._chunk_neighborhood(cx, cz))

        server = self.server
        if server is not None:
            get_chunk = getattr(world, "_get_chunk_index", None)
            if callable(get_chunk):
                for player in server.snapshot_players():
                    try:
                        cx, cz = get_chunk(float(player["x"]), float(player["z"]))
                    except (KeyError, TypeError, ValueError, OverflowError):
                        continue
                    radius = 4
                    for dx in range(-radius, radius + 1):
                        for dz in range(-radius, radius + 1):
                            if dx * dx + dz * dz <= radius * radius:
                                needed.add((cx + dx, cz + dz))
        world._mp_chunk_stream_keys = needed

    def _host_chunk_for_snapshot(self, world, cx: int, cz: int):
        key = (cx, cz)
        neighborhood = self._chunk_neighborhood(cx, cz)
        stream_keys = getattr(world, "_mp_chunk_stream_keys", None)
        if not isinstance(stream_keys, set):
            stream_keys = set()
            world._mp_chunk_stream_keys = stream_keys
        stream_keys.update(neighborhood)

        if getattr(world, "_population_future", None) is not None:
            return None

        existing = getattr(world, "chunks", {}).get(key)
        if (existing is not None
                and bool(getattr(existing, "terrain_populated", False))
                and bool(getattr(existing, "light_ready", False))):
            return existing

        missing = [
            nkey for nkey in neighborhood
            if nkey not in world.chunks and nkey not in getattr(world, "_terrain_futures", {})
        ]
        if missing:
            request_terrain = getattr(world, "_request_chunk_terrain", None)
            if callable(request_terrain):
                request_terrain(missing)
            return None
        if any(nkey not in world.chunks for nkey in neighborhood):
            return None

        chunk = world.chunks[key]
        if not bool(getattr(chunk, "terrain_populated", False)):
            if key in getattr(world, "_populated_chunks", set()):
                chunk.terrain_populated = True
            else:
                start_population = getattr(world, "_start_population", None)
                if callable(start_population):
                    start_population(key)
                return None

        if not bool(getattr(chunk, "light_ready", False)):
            lighting_queue = getattr(world, "_chunks_needing_lighting", None)
            if lighting_queue is not None and key not in lighting_queue:
                lighting_queue.append(key)
            return None
        return chunk

    def _service_host_chunk_requests(self, max_chunks: int = 1) -> None:
        server = self.server
        world = getattr(self.game, "world", None)
        if server is None or world is None:
            return
        for _ in range(max(0, int(max_chunks))):
            if not self._host_chunk_requests:
                break
            request = self._host_chunk_requests.popleft()
            self._host_chunk_request_keys.discard(request)
            player_id, cx, cz = request
            try:
                chunk = self._host_chunk_for_snapshot(world, cx, cz)
                if chunk is None:
                    self._host_chunk_requests.appendleft(request)
                    self._host_chunk_request_keys.add(request)
                    self._refresh_host_chunk_stream_keys(world)
                    world._mp_chunk_stream_keys.update(self._chunk_neighborhood(cx, cz))
                    break
                packet = P.chunk_snapshot_packet(
                    cx, cz, chunk.blocks, chunk.metadata, chunk._id_to_block_type,
                    width=chunk.width, height=chunk.height, depth=chunk.depth,
                    populated=bool(getattr(chunk, "terrain_populated", True)),
                    skylight=chunk.skylight, blocklight=chunk.blocklight,
                    heightmap=chunk.heightmap,
                    tile_entities=self._tile_entity_states_for_chunk(world, cx, cz),
                )
                server.send_chunk_packet(player_id, packet)
                self._refresh_host_chunk_stream_keys(world)
            except Exception as exc:
                server.send_chunk_packet(player_id, {
                    "t": "chunk_error", "cx": cx, "cz": cz,
                    "reason": f"Could not synchronize chunk ({cx}, {cz}): {type(exc).__name__}",
                })
                print(f"mp chunk snapshot error for {(cx, cz)}: {exc}")
                self._refresh_host_chunk_stream_keys(world)

    def _flush_client_chunk_snapshots(self, max_chunks: int = 2) -> None:
        world = getattr(self.game, "world", None)
        if world is None or getattr(world, "_population_future", None) is not None:
            return
        for _ in range(max(0, int(max_chunks))):
            if not self._client_chunk_snapshots_waiting:
                break
            event = self._client_chunk_snapshots_waiting.popleft()
            try:
                key = (int(event.get("cx")), int(event.get("cz")))
            except (TypeError, ValueError, OverflowError):
                key = None
            if key is not None:
                self._client_chunk_snapshot_waiting_keys.discard(key)
            self._apply_chunk_snapshot_event(event, allow_defer=False)

    def _apply_chunk_snapshot_event(self, event: dict, *, allow_defer: bool = True) -> None:
        try:
            snapshot = P.decode_chunk_snapshot(event)
            world = getattr(self.game, "world", None)
            if world is None:
                raise ValueError("World is not ready")
            if allow_defer and getattr(world, "_population_future", None) is not None:
                key = (snapshot["cx"], snapshot["cz"])
                if key not in self._client_chunk_snapshot_waiting_keys:
                    self._client_chunk_snapshots_waiting.append(dict(event))
                    self._client_chunk_snapshot_waiting_keys.add(key)
                return
            if (snapshot["width"] != int(world.CHUNK_SIZE)
                    or snapshot["depth"] != int(world.CHUNK_SIZE)
                    or snapshot["height"] != int(world.CHUNK_HEIGHT)):
                raise ValueError("Server chunk dimensions do not match this build")
            cx, cz = snapshot["cx"], snapshot["cz"]
            key = (cx, cz)
            chunk = world.chunks.get(key)
            if chunk is None:
                empty = getattr(world, "_create_mp_chunk_shell", None)
                if callable(empty):
                    chunk = empty(cx, cz)
                else:
                    chunk = world._generate_chunk(cx, cz)

            source_palette = snapshot["palette"]
            mapped_palette = [0] * len(source_palette)
            for source_id, name in enumerate(source_palette):
                if source_id == 0 or name is None:
                    mapped_palette[source_id] = 0
                else:
                    mapped_palette[source_id] = world._ensure_block_type_id(name)
            source_ids = snapshot["blocks"]
            remapped = bytearray(len(source_ids))
            for index, source_id in enumerate(source_ids):
                remapped[index] = mapped_palette[source_id]

            for te_key in list(getattr(world, "tile_entities", {})):
                try:
                    tx, _ty, tz = te_key
                except (TypeError, ValueError):
                    continue
                if world._get_chunk_index(tx, tz) == key:
                    world.tile_entities.pop(te_key, None)

            chunk.blocks[:] = remapped
            index_fire_chunk = getattr(world, "_index_fire_chunk", None)
            if callable(index_fire_chunk):
                index_fire_chunk(chunk)
            invalidate_air_cache = getattr(chunk, "_invalidate_mesh_air_cache", None)
            if callable(invalidate_air_cache):
                invalidate_air_cache()
            chunk.metadata[:] = snapshot["metadata"]
            chunk.skylight[:] = snapshot["skylight"]
            chunk.blocklight[:] = snapshot["blocklight"]
            chunk.heightmap[:] = snapshot["heightmap"]
            chunk.light_ready = True
            chunk.terrain_populated = bool(snapshot["populated"])
            chunk._mcpy_block_names.clear()
            chunk._source_block_ids = None
            chunk._source_internal_blocks = None
            chunk._source_metadata = None
            chunk._saved_fingerprint = None
            if chunk.terrain_populated:
                world._populated_chunks.add(key)
            else:
                world._populated_chunks.discard(key)

            tile_entity_types = {"chest", "furnace", "furnace_lit", "dispenser", "sign", "wall_sign", "mob_spawner"}
            used_block_names = {
                chunk._id_to_block_type[block_id] for block_id in set(chunk.blocks)
                if block_id and block_id < len(chunk._id_to_block_type)
            }
            if used_block_names & tile_entity_types:
                from game.block.tile_entity import create_tile_entity
                for index, block_id in enumerate(chunk.blocks):
                    name = chunk._id_to_block_type[block_id] if block_id else None
                    if name not in tile_entity_types:
                        continue
                    y = index % chunk.height
                    column = index // chunk.height
                    lz = column % chunk.depth
                    lx = column // chunk.depth
                    x, z = chunk.start_x + lx, chunk.start_z + lz
                    tile_entity = create_tile_entity(world, name, x, y, z)
                    if tile_entity is not None:
                        world.tile_entities[(x, y, z)] = tile_entity

            for tile_state in snapshot.get("tile_entities", ()):
                self._apply_tile_entity_state(tile_state)
            for tile_state in self._client_chunk_sync_tile_entity_deltas.pop(key, {}).values():
                self._apply_tile_entity_state(tile_state)

            redstone = getattr(world, "_redstone_system", None)
            if redstone is not None:
                redstone.on_chunk_loaded(chunk)
            fluid_scheduler = getattr(world, "_fluid_scheduler", None)
            if fluid_scheduler is not None:
                fluid_scheduler.on_chunk_loaded(chunk)
            chunk.mark_all_dirty()
            for nkey, edge in (
                ((cx - 1, cz), "east"), ((cx + 1, cz), "west"),
                ((cx, cz - 1), "south"), ((cx, cz + 1), "north"),
            ):
                neighbor = world.chunks.get(nkey)
                if neighbor is not None:
                    world._mark_chunk_boundary_dirty(neighbor, edge)
            for queue_name in ("_chunks_needing_decoration", "_chunks_needing_lighting"):
                queue = getattr(world, queue_name, None)
                self._remove_from_queue(queue, key)

            self._client_chunk_pending.discard(key)
            chunk._mp_snapshot_applied = True
            self._client_chunk_retry_after.pop(key, None)
            self._client_chunk_snapshot_waiting_keys.discard(key)
            deltas = self._client_chunk_sync_deltas.pop(key, {})
            for delta in deltas.values():
                self._apply_remote_block(delta)
            self._sync_jukebox_audio_for_chunk(chunk)
        except Exception as exc:
            try:
                key = (int(event.get("cx")), int(event.get("cz")))
            except (TypeError, ValueError, OverflowError):
                key = None
            if key is not None:
                self._client_chunk_pending.discard(key)
                self._client_chunk_retry_after[key] = time.monotonic() + 3.0
            self.log(f"Chunk sync failed: {type(exc).__name__}: {exc}")

    def stop(self) -> None:
        local_player = getattr(self.game, "player", None)
        if local_player is not None and bool(getattr(local_player, "is_sleeping", False)):
            try:
                local_player.wake_up_player(reset_timer=True)
            except Exception:
                pass
        if self._attached_inventory is not None:
            callback = getattr(self._attached_inventory, "_hotbar_selection_callback", None)
            if getattr(callback, "__self__", None) is self:
                self._attached_inventory._hotbar_selection_callback = None
            self._attached_inventory = None
        if self._attached_world is not None:
            music = getattr(self._attached_world, "record_music_player", None)
            stop_record = getattr(music, "stop_record", None)
            if callable(stop_record):
                for (x, y, z), (block_type, meta) in tuple(self._jukebox_audio_states.items()):
                    if block_type == "jukebox" and int(meta) > 0:
                        stop_record((float(x) + 0.5, float(y) + 0.5, float(z) + 0.5))
            self._jukebox_audio_states.clear()
            self._attached_world._network_block_callback = None
            self._attached_world._network_world_event_callback = None
            self._attached_world._network_sleep_cycle_callback = None
            self._attached_world._network_bed_claim_callback = None
            self._attached_world._network_bucket_callback = None
            self._attached_world._network_item_use_callback = None
            self._attached_world._network_jukebox_callback = None
            self._attached_world._network_note_block_callback = None
            self._attached_world._mp_suppress_world_events = False
            self._attached_world._network_animation_callback = None
            self._attached_world._network_entity_spawn_callback = None
            self._attached_world._network_fishing_callback = None
            self._attached_world._network_attack_callback = None
            self._attached_world._network_entity_interact_callback = None
            self._attached_world._network_entity_push_callback = None
            self._attached_world._network_player_attack_callback = None
            self._attached_world._network_edit_depth = 0
            self._attached_world._multiplayer_client_world = False
            self._attached_world._mp_remote_player_positions = []
            self._attached_world._mp_remote_players = ()
            try:
                from game.block.bed import clear_loaded_bed_occupancy
                clear_loaded_bed_occupancy(self._attached_world)
            except Exception:
                pass
            entities = getattr(self._attached_world, "entities", None)
            if entities is not None:
                self._attached_world.entities = [e for e in entities
                                                 if not getattr(e, "_mp_remote_entity", False)]
            self._attached_world = None
        self.remote_entities.clear()
        self._authoritative_bed_occupancy.clear()
        if self.server is not None:
            if self.server_config is not None:
                for snapshot in self.server.snapshot_players():
                    self.server_config.update_position(
                        snapshot.get("name"),
                        (snapshot.get("x"), snapshot.get("y"), snapshot.get("z")),
                        snapshot.get("yaw", 0.0), snapshot.get("pitch", 0.0),
                    )
                    if isinstance(snapshot.get("inventory"), dict):
                        self.server_config.update_inventory(
                            snapshot.get("name"), snapshot["inventory"],
                        )
            self.server.stop()
            self.server = None
        if self.client is not None:
            self.client.disconnect()
            self.client = None
        self.remote_players.clear()
        self._respawn_request_pending = False
        self._time_sync_accum = 0.0
        self._last_inventory_signature = None
        self._inventory_tx_sequence = 0
        self._last_break_signature = None
        self._last_tile_entity_signatures.clear()
        self._tile_entity_sync_accum = 0.0
        self._break_accum = 0.0
        if self.mode == "host":
            world = getattr(self.game, "world", None)
            if world is not None:
                world._mp_chunk_stream_keys = set()
        self._reset_chunk_sync_state()
        self.mode = None

    def _spawn_remote(self, data: dict) -> None:
        pid = int(data.get("id") or 0)
        if pid <= 0 or pid == self.local_id:
            return
        if pid in self.remote_players:
            return
        name = str(data.get("name") or "Player")
        pos = [float(data.get("x", 0.0)), float(data.get("y", 64.0)), float(data.get("z", 0.0))]
        rp = RemotePlayer(pid, name, pos, float(data.get("yaw", 0.0)), float(data.get("pitch", 0.0)))
        rp.skin_username = name.strip() or "Player"
        rp.world = getattr(self.game, "world", None)
        rp.renderer = getattr(self.game, "renderer", None)
        if self.mode == "host":
            rp.damage_callback = (
                lambda amount, attacker, knockback=None, source=None, target=rp:
                    self._send_remote_player_damage(
                        target, amount, attacker, knockback=knockback, source=source
                    )
            )
        elif self.mode == "client":
            rp.pvp_attack_callback = self._on_local_player_attack
        inventory_state = data.get("inventory")
        rp.apply_network_state(data)
        if inventory_state is not None:
            rp.apply_inventory_state(inventory_state)
        break_target = data.get("break_target")
        if isinstance(break_target, (list, tuple)) and len(break_target) == 3:
            try:
                rp.break_target = tuple(int(v) for v in break_target)
                rp.break_progress = max(0.0, min(0.99, float(data.get("break_progress", 0.0))))
            except (TypeError, ValueError, OverflowError):
                rp.break_target = None
                rp.break_progress = 0.0
        self.remote_players[pid] = rp

    def _remove_remote(self, pid: int) -> None:
        self.remote_players.pop(int(pid), None)

    def update(self, dt: float, paused: bool = False) -> None:
        if not self.active:
            return
        game = self.game
        player = game.player
        if player is None:
            return
        self._attach_local_inventory()

        dedicated_server = bool(getattr(game, "_dedicated_server_mode", False))
        try:
            if not dedicated_server:
                self._sync_local_boat_control(player, dt)
            if not dedicated_server:
                self._sync_local_inventory(player)
                self._sync_local_break_progress(player, dt)
            self._pos_accum += dt
            if self._pos_accum >= 0.05:
                self._pos_accum = 0.0
                x, y, z = float(player.pos[0]), float(player.pos[1]), float(player.pos[2])
                yaw = float(getattr(player, "yaw", 0.0))
                pitch = float(getattr(player, "pitch", 0.0))
                state = self._player_render_state(player)
                if self.mode == "host" and self.server is not None:
                    if not dedicated_server:
                        self.server.set_host_state(x, y, z, yaw, pitch, state=state)
                elif self.mode == "client" and self.client is not None:
                    self.client.send_pos(x, y, z, yaw, pitch, state=state)

            events: list[dict] = []
            if self.mode == "host" and self.server is not None:
                world = getattr(game, "world", None)
                if world is not None:
                    current_world_time = int(getattr(world, "world_time", 0))
                    self.server.set_world_metadata(
                        current_world_time,
                        bool(getattr(world, "snow_covered", False)),
                    )
                    self._time_sync_accum += max(0.0, min(float(dt), 0.25))
                    if self._time_sync_accum >= 1.0:
                        self._time_sync_accum %= 1.0
                        self.server.broadcast_world_event(
                            P.world_event("time", world_time=current_world_time)
                        )
                events.extend(self.server.poll_events())
                peer_snapshots = self.server.snapshot_players()
                if world is not None:
                    world._mp_remote_player_positions = [
                        (float(snap["x"]), float(snap["y"]), float(snap["z"]))
                        for snap in peer_snapshots
                    ]
                    self._refresh_host_chunk_stream_keys(world)
                for snap in peer_snapshots:
                    pid = int(snap["id"])
                    if pid not in self.remote_players:
                        self._spawn_remote(snap)
                    else:
                        self.remote_players[pid].apply_network_pos(
                            snap["x"], snap["y"], snap["z"], snap["yaw"], snap["pitch"]
                        )
                        self.remote_players[pid].apply_network_state(snap)
            if self.mode == "client" and self.client is not None:
                events.extend(self.client.poll_events())

            for ev in events:
                self._handle_event(ev)
                if ev.get("t") in ("kick", "disconnect"):
                    break

            if self.mode == "host" and self.server is not None:
                self._service_host_chunk_requests(max_chunks=1)
                self._sync_world_entities(dt)
                self._sync_tile_entities(dt)
            elif self.mode == "client" and self.client is not None:
                self._flush_client_chunk_snapshots(max_chunks=2)
                self._request_client_chunk_snapshots(dt)
                self._sync_tile_entities(dt)

            for rp in self.remote_players.values():
                if rp.renderer is None:
                    rp.renderer = getattr(game, "renderer", None)
                rp.tick_interpolate(dt)
            world = getattr(game, "world", None)
            if world is not None:
                world._mp_remote_players = tuple(self.remote_players.values())
                self._restore_live_bed_occupancy(world)
                if self.mode == "host":
                    updater = getattr(world, "update_all_players_sleeping_flag", None)
                    if callable(updater):
                        updater()
                    try_skip = getattr(world, "_try_skip_night", None)
                    if callable(try_skip):
                        try_skip()
            self._update_remote_entities(dt)
        except Exception as exc:
            print(f"mp session update error: {exc}")
            import traceback
            traceback.print_exc()

    def _return_to_title_after_disconnect(self, reason: str = "Connection closed") -> None:
        return_to_title = getattr(self.game, "quit_to_title", None)
        if callable(return_to_title):
            try:
                return_to_title(disconnect_reason=str(reason)[:200])
            except TypeError:
                return_to_title()
        else:
            self.stop()

    def _handle_event(self, ev: dict) -> None:
        t = ev.get("t")
        if t == "bed_occupancy":
            self._apply_bed_occupancy(ev)
        elif t == "bed_claim_result":
            if self.mode == "client" and not bool(ev.get("granted", False)):
                player = getattr(self.game, "player", None)
                bed = ev.get("bed_location")
                current_bed = getattr(player, "bed_location", None) if player is not None else None
                try:
                    same_bed = (
                        isinstance(bed, (list, tuple)) and len(bed) == 3
                        and current_bed is not None
                        and tuple(int(v) for v in bed) == tuple(int(v) for v in current_bed)
                    )
                except (TypeError, ValueError, OverflowError):
                    same_bed = False
                if (player is not None and bool(getattr(player, "is_sleeping", False))
                        and same_bed):
                    player.wake_up_player(reset_timer=True)
                    try:
                        from game.block.bed import bed_set_occupied
                        bed_set_occupied(
                            self.game.world, int(bed[0]), int(bed[1]), int(bed[2]), True,
                        )
                    except Exception:
                        pass
                    from game.localization import translate
                    self.game.add_chat_message(translate("tile.bed.occupied"))
        elif t == "command_request":
            self._handle_command_request(ev)
        elif t == "inventory_sync_request":
            self._approve_remote_inventory_sync(ev)
        elif t == "hotbar_select_request":
            self._approve_remote_hotbar_selection(ev)
        elif t == "inventory_transaction_request":
            self._approve_remote_inventory_transaction(ev)
        elif t == "inventory_drop_request":
            self._approve_remote_inventory_drop(ev)
        elif t == "tile_entity_request":
            self._approve_remote_tile_entity_request(ev)
        elif t == "tile_entity":
            if self.mode == "client":
                self._remember_chunk_sync_tile_entity_delta(ev)
            self._apply_tile_entity_state(ev)
        elif t == "profile_update":
            self._save_profile_event(ev)
        elif t == "private_message":
            sender = str(ev.get("from") or "?")
            target = str(ev.get("to") or "me")
            message = str(ev.get("msg") or "")
            from game.localization import translate
            self.game.add_chat_message(translate(
                "mcpy.chat.privateMessage", sender, target, message,
            ))
        elif t == "command_reply":
            self.game.add_chat_message(str(ev.get("msg") or ""))
        elif t == "teleport":
            if self.mode == "client":
                player = getattr(self.game, "player", None)
                if player is not None:
                    try:
                        position = (float(ev.get("x")), float(ev.get("y")), float(ev.get("z")))
                        if hasattr(player, "set_position"):
                            player.set_position(*position)
                        else:
                            player.pos[:3] = list(position)
                            if hasattr(player, "prev_pos"):
                                player.prev_pos = list(player.pos)
                        if "yaw" in ev:
                            player.yaw = float(ev.get("yaw", player.yaw))
                        if "pitch" in ev:
                            player.pitch = float(ev.get("pitch", player.pitch))
                    except (TypeError, ValueError, OverflowError):
                        pass
        elif t == "respawn_request":
            self._approve_remote_respawn(ev)
        elif t == "tnt_ignite_request":
            if self.mode == "host":
                self._approve_remote_tnt_ignite(ev)
        elif t == "respawn":
            if self.mode == "client" and int(ev.get("id") or 0) == self.local_id:
                try:
                    spawn = (
                        float(ev.get("x", 0.5)),
                        float(ev.get("y", 64.0)),
                        float(ev.get("z", 0.5)),
                    )
                except (TypeError, ValueError, OverflowError):
                    return
                self._respawn_request_pending = False
                respawn = getattr(self.game, "respawn_player", None)
                if callable(respawn):
                    respawn(_network_authorized=True, _spawn_override=spawn)
        elif t == "join":
            self._spawn_remote(ev)
            name = ev.get("name", "?")
            self.notify(f"{name} joined the game")
        elif t == "leave":
            pid = int(ev.get("id") or 0)
            rp = self.remote_players.get(pid)
            name = rp.username if rp else str(pid)
            self._clear_remote_boat_control(pid)
            self._remove_remote(pid)
            self.notify(f"{name} left the game")
        elif t == "pos":
            pid = int(ev.get("id") or 0)
            if pid == self.local_id:
                return
            rp = self.remote_players.get(pid)
            if rp is None:
                return
            rp.apply_network_pos(
                float(ev.get("x", rp.pos[0])),
                float(ev.get("y", rp.pos[1])),
                float(ev.get("z", rp.pos[2])),
                float(ev.get("yaw", rp.yaw)),
                float(ev.get("pitch", rp.pitch)),
            )
            rp.apply_network_state(ev)
        elif t == "entity_sync":
            self._apply_remote_entity_sync(ev)
        elif t == "bucket_use_request":
            self._handle_bucket_use_request(ev)
        elif t == "item_use_request":
            self._handle_item_use_request(ev)
        elif t == "jukebox_use_request":
            self._handle_jukebox_use_request(ev)
        elif t == "note_block_use_request":
            self._handle_note_block_use_request(ev)
        elif t == "boat_control_request":
            self._handle_boat_control_request(ev)
        elif t == "attack_request":
            self._handle_attack_request(ev)
        elif t == "entity_interact_request":
            self._handle_entity_interact_request(ev)
        elif t == "entity_push_request":
            self._handle_entity_push_request(ev)
        elif t == "player_attack_request":
            self._handle_player_attack_request(ev)
        elif t == "player_damage":
            if self.mode == "client" and int(ev.get("id") or 0) == self.local_id:
                player = getattr(self.game, "player", None)
                if player is not None:
                    source = str(ev.get("source") or "monster")
                    try:
                        amount = max(1, min(
                            P.KILL_DAMAGE if source == "kill" else 20,
                            int(ev.get("amount", 1)),
                        ))
                        attacker_id = int(ev.get("attacker_id") or 0)
                    except (TypeError, ValueError, OverflowError):
                        return
                    if source == "kill":
                        player.damage_player(
                            amount, source="kill", bypass_armor=True,
                            force=True, bypass_gamemode=True,
                        )
                        return
                    if source == "player":
                        attacker = self.remote_players.get(attacker_id) if attacker_id > 0 else None
                    elif source == "explosion":
                        attacker = None
                    else:
                        attacker = self.remote_entities.get(attacker_id) if attacker_id > 0 else None
                    player.damage_player(amount, source=source, attacker=attacker)
                    impulse = ev.get("knockback")
                    if isinstance(impulse, (list, tuple)) and len(impulse) == 3:
                        try:
                            for axis, value in enumerate(impulse):
                                player.vel[axis] += max(-8.0, min(8.0, float(value)))
                        except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
                            pass
        elif t == "item_spawn_request":
            self._handle_item_spawn_request(ev)
        elif t == "entity_spawn_request":
            self._handle_entity_spawn_request(ev)
        elif t == "fishing_action_request":
            self._handle_fishing_action_request(ev)
        elif t == "item_pickup_request":
            self._handle_item_pickup_request(ev)
        elif t == "block":
            if self.mode == "client":
                self._remember_chunk_sync_delta(ev)
            self._apply_remote_block(ev)
        elif t == "chunk_request":
            self._queue_host_chunk_request(ev)
        elif t == "chunk":
            self._apply_chunk_snapshot_event(ev)
        elif t == "chunk_error":
            try:
                key = (int(ev.get("cx")), int(ev.get("cz")))
            except (TypeError, ValueError, OverflowError):
                key = None
            if key is not None:
                self._client_chunk_pending.discard(key)
                self._client_chunk_retry_after[key] = time.monotonic() + 3.0
            reason = str(ev.get("reason") or "Chunk synchronization failed")[:120]
            self._status = reason
        elif t == "inventory":
            pid = int(ev.get("id") or 0)
            if pid == self.local_id and self.mode == "client":
                if self.client is not None:
                    try:
                        revision = max(0, int(ev.get("revision", 0)))
                        self.client.inventory_revision = revision
                        self.client.inventory_send_revision = revision
                    except (TypeError, ValueError, OverflowError):
                        pass
                self._apply_local_inventory_state(ev, keep_local_selection=True)
                return
            rp = self.remote_players.get(pid)
            if rp is not None:
                rp.apply_inventory_state(ev)
        elif t == "anim":
            pid = int(ev.get("id") or 0)
            if pid == self.local_id:
                return
            rp = self.remote_players.get(pid)
            if rp is not None:
                rp.trigger_animation(str(ev.get("action") or "swing"))
        elif t == "break":
            pid = int(ev.get("id") or 0)
            if pid == self.local_id:
                return
            rp = self.remote_players.get(pid)
            if rp is not None:
                rp.apply_break_progress(ev)
        elif t == "world_event":
            self._apply_world_event(ev)
        elif t == "chat":
            name = str(ev.get("from") or "?")
            msg = str(ev.get("msg") or "")
            line = f"<{name}> {msg}"
            print(line)
            self.game.add_chat_message(line)
        elif t == "log":
            if self.mode != "host":
                self.log(str(ev.get("msg") or ""))
        elif t == "kick":
            reason = str(ev.get("reason") or "Kicked from server")[:200]
            self.log(f"Kicked: {reason}")
            self.notify(f"Kicked: {reason}")
            self._return_to_title_after_disconnect(reason)
        elif t == "disconnect":
            reason = str(ev.get("reason") or "Connection closed")[:200]
            self.log(f"Disconnected: {reason}")
            self.notify(f"Disconnected: {reason}")
            self._return_to_title_after_disconnect(reason)
        elif t == "welcome":
            self.finish_client_join(ev)

    def _approve_remote_tnt_ignite(self, event: dict) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            player_id = int(event.get("id"))
            x, y, z = int(event.get("x")), int(event.get("y")), int(event.get("z"))
        except (TypeError, ValueError, OverflowError):
            return
        if not self._is_remote_player_connected(player_id):
            return
        world = getattr(self.game, "world", None)
        if world is None or world.get_block_type(x, y, z) != "tnt":
            return
        player = self.remote_players.get(player_id)
        if player is None:
            return
        dx, dy, dz = x + 0.5 - player.pos[0], y + 0.5 - player.pos[1], z + 0.5 - player.pos[2]
        if dx * dx + dy * dy + dz * dz > 6.0 * 6.0:
            return
        from game.entities.tnt import ignite_tnt
        if world.get_block_type(x, y, z) != "tnt":
            return
        network_depth = int(getattr(world, "_network_edit_depth", 0))
        world._network_edit_depth = network_depth + 1
        try:
            world.remove_block(x, y, z, immediate_mesh=True)
        finally:
            world._network_edit_depth = network_depth
        ignite_tnt(world, x, y, z, play_sound=False)

    def _remote_action_in_reach(self, player, x: int, y: int, z: int, reach: float = 6.0) -> bool:
        dx = float(x) + 0.5 - float(player.pos[0])
        dy = float(y) + 0.5 - float(player.pos[1])
        dz = float(z) + 0.5 - float(player.pos[2])
        return dx * dx + dy * dy + dz * dz <= float(reach) ** 2

    def _remote_player_is_creative(self, remote) -> bool:
        for candidate in (remote, getattr(self.game, "player", None)):
            mode = getattr(candidate, "gamemode", None)
            if mode is not None:
                try:
                    return int(mode) == 1
                except (TypeError, ValueError, OverflowError):
                    return False
        return False

    def _save_remote_inventory(self, player_id: int, remote) -> None:
        state = self._inventory_snapshot(remote)
        if self.server is not None:
            self.server.set_client_inventory_state(int(player_id), state)
        if self.server_config is not None:
            self.server_config.update_inventory(remote.username, state)

    def _handle_bucket_use_request(self, event: dict) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            player_id = int(event.get("id"))
        except (TypeError, ValueError, OverflowError):
            return
        remote = self.remote_players.get(player_id)
        world = getattr(self.game, "world", None)
        action = str(event.get("action") or "")
        if remote is None or not self._is_remote_player_connected(player_id):
            return
        inventory = remote.inventory
        slot = int(getattr(inventory, "current_item", 0))
        held = inventory.get_active_item()
        held_id = str(getattr(held, "id", "")) if held is not None else ""
        from game.multiplayer.remote_player import RemoteItemStack
        if action == "drink":
            if (held_id != "milk_bucket" or int(getattr(held, "count", 0)) <= 0
                    or float(getattr(remote, "health", 20.0)) <= 0.0):
                return
            inventory.set_stack(slot, RemoteItemStack("bucket", 1, 0))
            self._save_remote_inventory(player_id, remote)
            return

        try:
            x, y, z = int(event.get("x")), int(event.get("y")), int(event.get("z"))
        except (TypeError, ValueError, OverflowError):
            return
        if world is None or not self._remote_action_in_reach(remote, x, y, z):
            return
        from game.multiplayer.bucket_rules import bucket_result_for_source, bucket_target_is_solid
        if action == "milk":
            if held_id != "milk_bucket" or world.get_block_type(x, y, z) is None:
                return
            inventory.set_stack(slot, RemoteItemStack("bucket", 1, 0))
        elif action == "pickup":
            if held_id != "bucket":
                return
            block_type = world.get_block_type(x, y, z)
            filled = bucket_result_for_source(block_type, world.get_metadata(x, y, z))
            if filled is None:
                return
            world.set_block(x, y, z, None, immediate_mesh=True)
            inventory.set_stack(slot, RemoteItemStack(filled, 1, 0))
            self.server.broadcast_block_update(x, y, z, None, 0)
        elif action == "pour":
            fluid = {"water_bucket": "water", "lava_bucket": "lava"}.get(held_id)
            if fluid is None or bucket_target_is_solid(world.get_block_type(x, y, z)):
                return
            world.set_block(x, y, z, fluid, meta=0, immediate_mesh=True)
            inventory.set_stack(slot, RemoteItemStack("bucket", 1, 0))
            self.server.broadcast_block_update(x, y, z, fluid, 0)
        else:
            return
        self._save_remote_inventory(player_id, remote)

    def _handle_item_use_request(self, event: dict) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            player_id = int(event.get("id"))
            x, y, z = int(event.get("x")), int(event.get("y")), int(event.get("z"))
        except (TypeError, ValueError, OverflowError):
            return
        remote = self.remote_players.get(player_id)
        world = getattr(self.game, "world", None)
        action = str(event.get("action") or "")
        if (action not in {"bonemeal", "cake_eat"} or remote is None or world is None
                or not self._is_remote_player_connected(player_id)
                or not self._remote_action_in_reach(remote, x, y, z)):
            return
        if action == "cake_eat":
            if world.get_block_type(x, y, z) != "cake" or int(getattr(remote, "health", 0)) <= 0:
                return
            if int(getattr(remote, "health", 0)) >= 20:
                return
            from game.block.cake import eat_cake_slice
            network_depth = int(getattr(world, "_network_edit_depth", 0))
            world._network_edit_depth = network_depth + 1
            try:
                eat_cake_slice(world, remote, x, y, z)
            finally:
                world._network_edit_depth = network_depth
            return
        held = remote.inventory.get_active_item()
        if (held is None or str(getattr(held, "id", "")) != "dye"
                or int(getattr(held, "damage", 0)) != 15
                or int(getattr(held, "count", 0)) <= 0):
            return
        if world.get_block_type(x, y, z) not in ("crops", "sapling"):
            return
        from game.block.bonemeal import apply_bonemeal
        network_depth = int(getattr(world, "_network_edit_depth", 0))
        world._network_edit_depth = network_depth + 1
        try:
            used = apply_bonemeal(world, x, y, z)
        finally:
            world._network_edit_depth = network_depth
        if not used:
            return
        if not self._remote_player_is_creative(remote):
            slot = int(getattr(remote.inventory, "current_item", 0))
            held.count -= 1
            if held.count <= 0:
                remote.inventory.set_stack(slot, None)
            self._save_remote_inventory(player_id, remote)

    def _handle_jukebox_use_request(self, event: dict) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            player_id = int(event.get("id"))
            x, y, z = int(event.get("x")), int(event.get("y")), int(event.get("z"))
        except (TypeError, ValueError, OverflowError):
            return
        remote = self.remote_players.get(player_id)
        world = getattr(self.game, "world", None)
        action = str(event.get("action") or "")
        if (remote is None or world is None or not self._is_remote_player_connected(player_id)
                or not self._remote_action_in_reach(remote, x, y, z)
                or world.get_block_type(x, y, z) != "jukebox"):
            return
        from game.entities.item import ItemEntity, ItemStack
        from game.multiplayer.remote_player import RemoteItemStack
        meta = int(world.get_metadata(x, y, z)) & 15
        position = (float(x) + 0.5, float(y) + 0.5, float(z) + 0.5)
        music = getattr(world, "record_music_player", None)
        if action == "eject":
            record_id = {1: "record_13", 2: "record_cat"}.get(meta)
            if record_id is None:
                return
            world.set_block(x, y, z, "jukebox", meta=0, immediate_mesh=True)
            stop = getattr(music, "stop_record", None)
            if callable(stop):
                stop(position)
            item_pos = (
                float(x) + 0.5, float(y) + 0.66, float(z) + 0.5,
            )
            world.spawn_entity(ItemEntity(ItemStack(record_id, 1), item_pos, pickup_delay=10))
            self.server.broadcast_block_update(x, y, z, "jukebox", 0)
        elif action == "insert":
            if meta != 0:
                return
            held = remote.inventory.get_active_item()
            record_id = str(getattr(held, "id", "")) if held is not None else ""
            record_meta = {"record_13": 1, "record_cat": 2}.get(record_id)
            if record_meta is None:
                return
            slot = int(getattr(remote.inventory, "current_item", 0))
            if held.count <= 1:
                remote.inventory.set_stack(slot, None)
            else:
                held.count -= 1
            world.set_block(x, y, z, "jukebox", meta=record_meta, immediate_mesh=True)
            play = getattr(music, "play_record", None)
            if callable(play):
                play("13" if record_meta == 1 else "cat", position)
            self.server.broadcast_block_update(x, y, z, "jukebox", record_meta)
        else:
            return
        self._save_remote_inventory(player_id, remote)

    def _handle_note_block_use_request(self, event: dict) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            player_id = int(event.get("id"))
            x, y, z = int(event.get("x")), int(event.get("y")), int(event.get("z"))
        except (TypeError, ValueError, OverflowError):
            return
        remote = self.remote_players.get(player_id)
        world = getattr(self.game, "world", None)
        action = str(event.get("action") or "")
        if (remote is None or world is None or action not in {"play", "pitch"}
                or not self._is_remote_player_connected(player_id)
                or not self._remote_action_in_reach(remote, x, y, z)
                or world.get_block_type(x, y, z) != "note_block"):
            return
        tile_entity = world.get_tile_entity(x, y, z)
        if tile_entity is None:
            return
        if action == "pitch":
            tile_entity.change_pitch()
        tile_entity.trigger_note()

    def _handle_boat_control_request(self, event: dict) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            player_id = int(event.get("id"))
            entity_id = int(event.get("entity_id"))
            forward = max(-1.0, min(1.0, float(event.get("forward", 0.0))))
            strafe = max(-1.0, min(1.0, float(event.get("strafe", 0.0))))
            yaw = float(event.get("yaw", 0.0))
        except (TypeError, ValueError, OverflowError):
            return
        remote = self.remote_players.get(player_id)
        world = getattr(self.game, "world", None)
        if remote is None or world is None or not self._is_remote_player_connected(player_id):
            return
        boat = next((entity for entity in getattr(world, "entities", ())
                     if not getattr(entity, "dead", False)
                     and self._entity_kind(entity) == "boat"
                     and int(getattr(entity, "_mp_net_id", 0) or 0) == entity_id), None)
        if boat is None:
            return
        owner = int(getattr(boat, "_mp_remote_rider_id", 0) or 0)
        if not bool(event.get("riding", False)):
            if owner == player_id:
                boat._mp_remote_rider_id = 0
                boat._mp_remote_control = None
            return
        dx = float(boat.pos[0]) - float(remote.pos[0])
        dy = float(boat.pos[1]) - float(remote.pos[1])
        dz = float(boat.pos[2]) - float(remote.pos[2])
        if dx * dx + dy * dy + dz * dz > 7.0 * 7.0:
            return
        if owner not in (0, player_id) or getattr(boat, "rider", None) is not None:
            return
        boat._mp_remote_rider_id = player_id
        boat._mp_remote_control = {
            "forward": forward, "strafe": strafe,
            "yaw": max(-3600.0, min(3600.0, yaw)),
            "updated_at": time.monotonic(),
        }

    def _clear_remote_boat_control(self, player_id: int) -> None:
        world = getattr(self.game, "world", None)
        if world is None:
            return
        for entity in tuple(getattr(world, "entities", ())):
            if int(getattr(entity, "_mp_remote_rider_id", 0) or 0) == int(player_id):
                entity._mp_remote_rider_id = 0
                entity._mp_remote_control = None

    def _handle_attack_request(self, event) -> None:
        if self.mode != "host":
            return
        from game.entities.mob import Mob

        world = getattr(self.game, "world", None)
        if world is None:
            return
        try:
            player_id = int(event.get("id"))
            entity_id = int(event.get("entity_id"))
            damage = int(event.get("damage"))
        except (TypeError, ValueError, OverflowError):
            return
        if player_id <= 1 or entity_id <= 0 or not 1 <= damage <= 10:
            return
        attacker = self.remote_players.get(player_id)
        if attacker is None or not self._is_remote_player_connected(player_id):
            return
        target = next((candidate for candidate in getattr(world, "entities", ())
                       if int(getattr(candidate, "_mp_net_id", 0) or 0) == entity_id), None)
        target_kind = self._entity_kind(target) if target is not None else None
        if (target is None or target.dead
                or not (isinstance(target, Mob)
                        or target_kind in {"boat", "minecart", "painting"})
                or (isinstance(target, Mob) and float(getattr(target, "health", 0)) <= 0)):
            return

        bounds_getter = getattr(target, "get_interaction_bounds", None)
        if callable(bounds_getter):
            try:
                min_x, min_y, min_z, max_x, max_y, max_z = bounds_getter()
            except Exception:
                return
        else:
            width, height, depth = target.size
            min_x, min_y, min_z = (
                target.pos[0] - width * 0.5, target.pos[1], target.pos[2] - depth * 0.5
            )
            max_x, max_y, max_z = (
                target.pos[0] + width * 0.5, target.pos[1] + height, target.pos[2] + depth * 0.5
            )
        eye_x = float(attacker.pos[0])
        eye_y = float(attacker.pos[1]) + 1.62
        eye_z = float(attacker.pos[2])
        near_x = min(max(eye_x, min_x), max_x)
        near_y = min(max(eye_y, min_y), max_y)
        near_z = min(max(eye_z, min_z), max_z)
        reach_sq = (4.5 + float(getattr(target, "collision_border_size", 0.1))) ** 2
        dx, dy, dz = eye_x - near_x, eye_y - near_y, eye_z - near_z
        if dx * dx + dy * dy + dz * dz > reach_sq:
            return
        can_see = getattr(target, "can_entity_be_seen", None)
        if callable(can_see):
            try:
                if not can_see(attacker):
                    return
            except Exception:
                return
        target.attack_entity_from(attacker, damage)

    def _handle_entity_interact_request(self, event: dict) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            player_id = int(event.get("id"))
            entity_id = int(event.get("entity_id"))
        except (TypeError, ValueError, OverflowError):
            return
        if not self._is_remote_player_connected(player_id):
            return
        remote = self.remote_players.get(player_id)
        world = getattr(self.game, "world", None)
        action = str(event.get("action") or "")
        if remote is None or world is None:
            return
        target = next((candidate for candidate in getattr(world, "entities", ())
                       if int(getattr(candidate, "_mp_net_id", 0) or 0) == entity_id), None)
        if action == "milk_cow":
            from game.entities.cow import Cow
            if (float(getattr(remote, "health", 20.0)) <= 0.0
                    or not isinstance(target, Cow) or target.dead
                    or float(getattr(target, "health", 0.0)) <= 0.0):
                return
            inventory = getattr(remote, "inventory", None)
            held = inventory.get_active_item() if inventory is not None else None
            if (held is None or str(getattr(held, "id", "")) != "bucket"
                    or int(getattr(held, "count", 0)) <= 0):
                return
            bounds_getter = getattr(target, "get_interaction_bounds", None)
            if callable(bounds_getter):
                try:
                    min_x, min_y, min_z, max_x, max_y, max_z = bounds_getter()
                except Exception:
                    return
            else:
                width, height, depth = target.size
                min_x, min_y, min_z = (
                    target.pos[0] - width * 0.5, target.pos[1], target.pos[2] - depth * 0.5
                )
                max_x, max_y, max_z = (
                    target.pos[0] + width * 0.5, target.pos[1] + height,
                    target.pos[2] + depth * 0.5,
                )
            eye_x = float(remote.pos[0])
            eye_y = float(remote.pos[1]) + 1.62
            eye_z = float(remote.pos[2])
            near_x = min(max(eye_x, min_x), max_x)
            near_y = min(max(eye_y, min_y), max_y)
            near_z = min(max(eye_z, min_z), max_z)
            dx, dy, dz = eye_x - near_x, eye_y - near_y, eye_z - near_z
            reach = 4.5 + float(getattr(target, "collision_border_size", 0.1))
            if dx * dx + dy * dy + dz * dz > reach * reach:
                return
            from game.multiplayer.remote_player import RemoteItemStack
            slot = int(getattr(inventory, "current_item", 0))
            inventory.set_stack(slot, RemoteItemStack("milk_bucket", 1, 0))
            self._save_remote_inventory(player_id, remote)
            return
        if action == "dye_sheep":
            from game.entities.sheep import Sheep
            if (float(getattr(remote, "health", 20.0)) <= 0.0
                    or not isinstance(target, Sheep) or target.dead
                    or float(getattr(target, "health", 0.0)) <= 0.0
                    or target.sheared):
                return
            inventory = getattr(remote, "inventory", None)
            held = inventory.get_active_item() if inventory is not None else None
            if (held is None or str(getattr(held, "id", "")) != "dye"
                    or not 0 <= int(getattr(held, "damage", 0)) <= 15
                    or int(getattr(held, "count", 0)) <= 0):
                return
            bounds_getter = getattr(target, "get_interaction_bounds", None)
            if callable(bounds_getter):
                try:
                    min_x, min_y, min_z, max_x, max_y, max_z = bounds_getter()
                except Exception:
                    return
            else:
                width, height, depth = target.size
                min_x, min_y, min_z = (
                    target.pos[0] - width * 0.5, target.pos[1], target.pos[2] - depth * 0.5
                )
                max_x, max_y, max_z = (
                    target.pos[0] + width * 0.5, target.pos[1] + height,
                    target.pos[2] + depth * 0.5,
                )
            eye_x = float(remote.pos[0])
            eye_y = float(remote.pos[1]) + 1.62
            eye_z = float(remote.pos[2])
            near_x = min(max(eye_x, min_x), max_x)
            near_y = min(max(eye_y, min_y), max_y)
            near_z = min(max(eye_z, min_z), max_z)
            dx, dy, dz = eye_x - near_x, eye_y - near_y, eye_z - near_z
            reach = 4.5 + float(getattr(target, "collision_border_size", 0.1))
            if dx * dx + dy * dy + dz * dz > reach * reach:
                return
            can_see = getattr(target, "can_entity_be_seen", None)
            if callable(can_see):
                try:
                    if not can_see(remote):
                        return
                except Exception:
                    return
            fleece_color = (~int(getattr(held, "damage", 0))) & 15
            if fleece_color == target.fleece_color:
                return
            target.set_fleece_color(fleece_color)
            if not self._remote_player_is_creative(remote):
                slot = int(getattr(inventory, "current_item", 0))
                held.count -= 1
                if held.count <= 0:
                    inventory.set_stack(slot, None)
                self._save_remote_inventory(player_id, remote)
            return
        if action != "furnace_fuel":
            return
        from game.entities.minecart import MinecartEntity
        cart = target
        if (not isinstance(cart, MinecartEntity) or cart.dead
                or cart.cart_type != MinecartEntity.FURNACE):
            return
        dx = float(cart.pos[0]) - float(remote.pos[0])
        dy = float(cart.pos[1]) - float(remote.pos[1])
        dz = float(cart.pos[2]) - float(remote.pos[2])
        if dx * dx + dy * dy + dz * dz > 6.0 * 6.0:
            return
        inventory = getattr(remote, "inventory", None)
        get_active = getattr(inventory, "get_active_item", None)
        stack = get_active() if callable(get_active) else None
        if stack is not None and str(getattr(stack, "id", "")) == "coal" and int(stack.count) > 0:
            stack.count -= 1
            slot = int(getattr(inventory, "active_idx", getattr(inventory, "current_item", 0)))
            if stack.count <= 0:
                setter = getattr(inventory, "set_stack", None)
                if callable(setter):
                    setter(slot, None)
                elif 0 <= slot < len(getattr(inventory, "main", ())):
                    inventory.main[slot] = None
            cart.fuel = min(12000, int(getattr(cart, "fuel", 0)) + 1200)
            state = self._inventory_snapshot(remote)
            self.server.set_client_inventory_state(player_id, state)
            if self.server_config is not None:
                self.server_config.update_inventory(remote.username, state)
        cart.push_x = float(cart.pos[0]) - float(remote.pos[0])
        cart.push_z = float(cart.pos[2]) - float(remote.pos[2])

    def _handle_entity_push_request(self, event: dict) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            player_id = int(event.get("id"))
            entity_id = int(event.get("entity_id"))
        except (TypeError, ValueError, OverflowError):
            return
        if not self._is_remote_player_connected(player_id):
            return
        remote = self.remote_players.get(player_id)
        world = getattr(self.game, "world", None)
        if remote is None or world is None:
            return
        target = next((candidate for candidate in getattr(world, "entities", ())
                       if int(getattr(candidate, "_mp_net_id", 0) or 0) == entity_id), None)
        if target is None or target.dead:
            return
        can_push = getattr(target, "can_be_pushed", None)
        if callable(can_push) and not can_push():
            return
        dx = float(target.pos[0]) - float(remote.pos[0])
        dy = float(target.pos[1]) - float(remote.pos[1])
        dz = float(target.pos[2]) - float(remote.pos[2])
        if dx * dx + dz * dz > 3.0 * 3.0 or abs(dy) > 2.5:
            return
        collision = getattr(target, "apply_entity_collision", None)
        if callable(collision):
            collision(remote)

    @staticmethod
    def _player_melee_damage(attacker) -> int:
        global _MP_ITEM_DEFINITIONS
        inventory = getattr(attacker, "inventory", None)
        get_active = getattr(inventory, "get_active_item", None)
        held = get_active() if callable(get_active) else None
        if held is None:
            return 1
        try:
            if _MP_ITEM_DEFINITIONS is None:
                from game.content_data import load_item_definitions
                _MP_ITEM_DEFINITIONS = load_item_definitions()
            from game.entities.combat import item_damage_vs_entity
            definition = _MP_ITEM_DEFINITIONS.get(str(held.id), {})
            return max(1, min(10, int(item_damage_vs_entity(definition))))
        except Exception:
            return 1

    @staticmethod
    def _melee_target_bounds(target):
        getter = getattr(target, "get_interaction_bounds", None)
        if callable(getter):
            return tuple(float(value) for value in getter())
        width, height, depth = getattr(target, "size", (0.6, 1.8, 0.6))
        x, y, z = target.pos
        return (
            float(x) - float(width) * 0.5, float(y), float(z) - float(depth) * 0.5,
            float(x) + float(width) * 0.5, float(y) + float(height),
            float(z) + float(depth) * 0.5,
        )

    @staticmethod
    def _player_melee_ray_hit(attacker, target):
        start = (
            float(attacker.pos[0]),
            float(attacker.pos[1]) + float(getattr(attacker, "EYE_HEIGHT", 1.62)),
            float(attacker.pos[2]),
        )
        yaw = math.radians(float(getattr(attacker, "yaw", 0.0)))
        pitch = math.radians(float(getattr(attacker, "pitch", 0.0)))
        direction = (
            -math.sin(yaw) * math.cos(pitch),
            math.sin(pitch),
            -math.cos(yaw) * math.cos(pitch),
        )
        bounds = MultiplayerSession._melee_target_bounds(target)
        border = float(getattr(target, "collision_border_size", 0.1))
        t_min, t_max = -1.0e30, 1.0e30
        for origin, vector, minimum, maximum in (
            (start[0], direction[0], bounds[0] - border, bounds[3] + border),
            (start[1], direction[1], bounds[1] - border, bounds[4] + border),
            (start[2], direction[2], bounds[2] - border, bounds[5] + border),
        ):
            if abs(vector) < 1.0e-12:
                if origin < minimum or origin > maximum:
                    return None
                continue
            inverse = 1.0 / vector
            near = (minimum - origin) * inverse
            far = (maximum - origin) * inverse
            if near > far:
                near, far = far, near
            t_min = max(t_min, near)
            t_max = min(t_max, far)
            if t_min > t_max:
                return None
        if t_max < 0.0:
            return None
        distance = max(0.0, t_min)
        if distance > 4.5:
            return None
        end = tuple(start[i] + direction[i] * distance for i in range(3))
        return start, end

    @staticmethod
    def _has_clear_melee_line(attacker, ray_segment) -> bool:
        start, end = ray_segment
        dx, dy, dz = (end[i] - start[i] for i in range(3))
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        samples = max(2, int(math.ceil(distance * 10.0)))
        solid_at = getattr(attacker, "_solid_at", None)
        if not callable(solid_at):
            return True
        for index in range(1, samples):
            t = index / samples
            bx = math.floor(start[0] + dx * t + 0.5)
            by = math.floor(start[1] + dy * t + 0.5)
            bz = math.floor(start[2] + dz * t + 0.5)
            if solid_at(bx, by, bz):
                return False
        return True

    def _handle_player_attack_request(self, event) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            attacker_id = int(event.get("id"))
            target_id = int(event.get("target_id"))
        except (TypeError, ValueError, OverflowError):
            return
        if attacker_id <= 1 or target_id <= 0 or target_id == attacker_id:
            return
        attacker = self.remote_players.get(attacker_id)
        if (attacker is None or attacker.health <= 0
                or not self._is_remote_player_connected(attacker_id)):
            return

        if target_id == 1:
            if bool(getattr(self.game, "_dedicated_server_mode", False)):
                return
            target = getattr(self.game, "player", None)
        else:
            target = self.remote_players.get(target_id)
            if target is None or not self._is_remote_player_connected(target_id):
                return
        if (target is None or getattr(target, "dead", False)
                or float(getattr(target, "health", 0.0)) <= 0.0):
            return

        ray_segment = self._player_melee_ray_hit(attacker, target)
        if ray_segment is None or not self._has_clear_melee_line(attacker, ray_segment):
            return

        damage = self._player_melee_damage(attacker)
        if target_id == 1:
            take_damage = getattr(target, "damage_player", None)
            if callable(take_damage):
                take_damage(damage, source="player", attacker=attacker)
        else:
            target.attack_entity_from(attacker, damage)

    def _is_remote_player_connected(self, player_id: int) -> bool:
        if self.server is None:
            return False
        return any(int(snapshot.get("id", 0)) == int(player_id)
                   for snapshot in self.server.snapshot_players())

    def _handle_item_spawn_request(self, event) -> None:
        if self.mode != "host":
            return
        from game.entities.item import Item, ItemEntity, ItemStack

        world = getattr(self.game, "world", None)
        if world is None:
            return
        state = P.normalize_entity_state(event.get("entity"))
        if state is None or state.get("kind") != "item":
            return
        stack_data = state["item"]
        if not Item.exists(stack_data["id"]):
            return
        pid = int(event.get("id") or 0)
        peer = next((p for p in (self.server.snapshot_players() if self.server else []) if int(p["id"]) == pid), None)
        if peer is None:
            return
        dx = state["x"] - float(peer["x"])
        dy = state["y"] - float(peer["y"])
        dz = state["z"] - float(peer["z"])
        if dx * dx + dz * dz > 64.0 or abs(dy) > 8.0:
            return
        try:
            item = ItemEntity(
                ItemStack(stack_data["id"], stack_data["count"], stack_data["damage"]),
                (state["x"], state["y"], state["z"]),
                velocity=(state["vx"], state["vy"], state["vz"]),
                pickup_delay=max(10.0, state.get("pickup_delay", 10.0)),
            )
        except Exception:
            return
        world.spawn_entity(item)

    def _handle_entity_spawn_request(self, event) -> None:
        if self.mode != "host" or self.server is None:
            return
        world = getattr(self.game, "world", None)
        if world is None:
            return
        state = P.normalize_entity_state(event.get("entity"))
        if state is None or state["kind"] not in {
            "arrow", "snowball", "egg", "painting", "boat", "minecart",
        }:
            return
        try:
            player_id = int(event.get("id"))
        except (TypeError, ValueError, OverflowError):
            return
        remote = self.remote_players.get(player_id)
        peer = next((p for p in self.server.snapshot_players()
                     if int(p.get("id", 0)) == player_id), None)
        if remote is None or peer is None:
            return
        dx = state["x"] - float(peer["x"])
        dy = state["y"] - float(peer["y"])
        dz = state["z"] - float(peer["z"])
        if dx * dx + dy * dy + dz * dz > 8.0 * 8.0:
            return
        speed_sq = state["vx"] ** 2 + state["vy"] ** 2 + state["vz"] ** 2
        if speed_sq > 3.0 ** 2:
            return

        inventory = getattr(remote, "inventory", None)
        if inventory is None:
            return
        selected = inventory.get_active_item()
        selected_id = str(getattr(selected, "id", "")) if selected is not None else ""

        def consume_item(item_id: str, *, selected_only: bool = False) -> bool:
            slots = [inventory.current_item] if selected_only else range(len(inventory.main))
            for slot in slots:
                if not 0 <= int(slot) < len(inventory.main):
                    continue
                stack = inventory.main[int(slot)]
                if stack is None or str(getattr(stack, "id", "")) != item_id:
                    continue
                stack.count -= 1
                if stack.count <= 0:
                    inventory.main[int(slot)] = None
                return True
            return False

        kind = state["kind"]
        entity = None
        if kind == "arrow":
            if selected_id != "bow" or not consume_item("arrow"):
                return
            from game.entities.arrow import ArrowEntity
            entity = ArrowEntity(
                world, (state["x"], state["y"], state["z"]),
                velocity=(state["vx"], state["vy"], state["vz"]),
                shooting_entity=remote,
            )
        elif kind == "snowball":
            if selected_id != "snowball" or not consume_item("snowball", selected_only=True):
                return
            from game.entities.snowball import SnowballEntity
            entity = SnowballEntity(
                world, (state["x"], state["y"], state["z"]),
                velocity=(state["vx"], state["vy"], state["vz"]),
                shooting_entity=remote,
            )
        elif kind == "egg":
            if selected_id != "egg" or not consume_item("egg", selected_only=True):
                return
            from game.entities.egg import EggEntity
            entity = EggEntity(
                world, (state["x"], state["y"], state["z"]),
                velocity=(state["vx"], state["vy"], state["vz"]),
                shooting_entity=remote,
            )
        elif kind == "painting":
            if selected_id != "painting":
                return
            from game.entities.painting import ART_TYPES, PaintingEntity
            art = next((art for art in ART_TYPES if art.title == state["art"]), ART_TYPES[0])
            entity = PaintingEntity(
                world, state["tile_x"], state["tile_y"], state["tile_z"],
                state["direction"], art=art,
            )
            if not entity.on_valid_surface():
                return
            if not consume_item("painting", selected_only=True):
                return
        elif kind == "boat":
            if selected_id != "boat":
                return
            bx, by, bz = int(round(state["x"])), int(math.floor(state["y"])), int(round(state["z"]))
            water = any(world.get_block_type(bx, sample_y, bz) in ("water", "flowing_water")
                        for sample_y in (by, by - 1, by + 1))
            if not water or not consume_item("boat", selected_only=True):
                return
            from game.entities.boat import BoatEntity
            entity = BoatEntity(
                world, (state["x"], state["y"], state["z"]),
                velocity=(state["vx"], state["vy"], state["vz"]),
            )
        elif kind == "minecart":
            from game.entities.minecart import MinecartEntity
            if (selected is None
                    or MinecartEntity.type_for_item(selected_id) != state["cart_type"]):
                return
            bx, by, bz = int(round(state["x"])), int(round(state["y"] - 0.44)), int(round(state["z"]))
            if world.get_block_type(bx, by, bz) != "rail":
                return
            if not consume_item(selected_id, selected_only=True):
                return
            entity = MinecartEntity(
                world, (state["x"], state["y"], state["z"]),
                velocity=(state["vx"], state["vy"], state["vz"]),
                cart_type=state["cart_type"],
            )

        if entity is None:
            return
        world.spawn_entity(entity)
        self.server.set_client_inventory_state(player_id, self._inventory_snapshot(remote))

    def _handle_fishing_action_request(self, event: dict) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            player_id = int(event.get("id"))
            entity_id = int(event.get("entity_id", 0))
        except (TypeError, ValueError, OverflowError):
            return
        action = str(event.get("action") or "")
        if action not in {"cast", "reel"} or not self._is_remote_player_connected(player_id):
            return
        remote = self.remote_players.get(player_id)
        world = getattr(self.game, "world", None)
        inventory = getattr(remote, "inventory", None)
        if remote is None or world is None or inventory is None:
            return
        rod_slot = int(getattr(inventory, "current_item", 0))
        selected = inventory.get_active_item()
        if (selected is None or str(getattr(selected, "id", "")) != "fishing_rod"
                or float(getattr(remote, "health", 0.0)) <= 0.0):
            return

        hook = getattr(remote, "fishing_hook", None)
        if action == "cast":
            if hook is not None and not getattr(hook, "dead", True):
                return
            from game.entities.fishing_bobber import FishingBobberEntity
            hook = FishingBobberEntity.from_player(world, remote)
            remote.fishing_hook = hook
            world.spawn_entity(hook)
            return

        if (hook is None or getattr(hook, "dead", True)
                or int(getattr(hook, "_mp_net_id", 0) or 0) != entity_id
                or getattr(hook, "angler", None) is not remote
                or not callable(getattr(hook, "_owner_is_valid", None))
                or not hook._owner_is_valid()):
            return
        result = int(hook.reel_in())
        if result > 0:
            maximum = _mp_item_max_damage("fishing_rod")
            if maximum > 0:
                selected.damage = int(getattr(selected, "damage", 0)) + result
                if selected.damage > maximum:
                    selected.count = max(0, int(selected.count) - 1)
                    selected.damage = 0
                    if selected.count <= 0:
                        inventory.set_stack(rod_slot, None)
        self.server.set_client_inventory_state(player_id, self._inventory_snapshot(remote))

    def _handle_item_pickup_request(self, event) -> None:
        if self.mode != "host":
            return
        from game.entities.item import ItemEntity

        world = getattr(self.game, "world", None)
        if world is None:
            return
        try:
            pid = int(event.get("id"))
            net_id = int(event.get("entity_id"))
        except (TypeError, ValueError, OverflowError):
            return
        peer = self.remote_players.get(pid)
        entity = self.remote_entities.get(net_id)
        if entity is None:
            entity = next((candidate for candidate in world.entities
                           if isinstance(candidate, ItemEntity)
                           and int(getattr(candidate, "_mp_net_id", 0)) == net_id), None)
        if peer is None or not isinstance(entity, ItemEntity) or entity.dead:
            return
        if not entity.can_be_picked_up():
            return
        dx = float(entity.pos[0]) - float(peer.pos[0])
        dy = float(entity.pos[1]) - float(peer.pos[1])
        dz = float(entity.pos[2]) - float(peer.pos[2])
        if dx * dx + dz * dz > 9.0 or abs(dy) > 4.0:
            return
        awarded = getattr(peer, "inventory", None)
        if awarded is None or not callable(getattr(awarded, "add_stack", None)):
            return
        if not awarded.add_stack(entity.item.copy()):
            return
        entity.begin_pickup(peer)
        state = self._inventory_snapshot(peer)
        if self.server is not None:
            self.server.set_client_inventory_state(pid, state)
        if self.server_config is not None:
            self.server_config.update_inventory(peer.username, state)

    def kick_player(self, name: str, reason: str = "Kicked by host") -> bool:
        if self.mode != "host" or self.server is None:
            return False
        kick = getattr(self.server, "kick_player", None)
        if not callable(kick):
            return False
        return bool(kick(name, reason))

    def is_operator(self, name: str) -> bool:
        if self.mode != "host":
            return False
        host_name = getattr(self.server, "host_name", None) if self.server is not None else None
        if player_key(name) == player_key(host_name):
            return True
        return bool(self.server_config is not None and self.server_config.is_operator(name))

    def send_command(self, text: str) -> bool:
        if self.mode != "client" or self.client is None or not self.client.connected:
            return False
        self.client.send_command(text)
        return True

    def send_private_message(self, sender_name: str, target_name: str, message: str, *, sender_id: int = 0) -> bool:
        if self.mode != "host" or self.server is None:
            return False
        sender = normalize_player_name(sender_name) or "Player"
        target = normalize_player_name(target_name)
        text = str(message or "").strip()[:200]
        if not target or not text:
            return False
        host_name = normalize_player_name(self.server.host_name)
        packet = P.private_message(sender, target, text)
        target_is_host = player_key(target) == player_key(host_name)
        if target_is_host:
            from game.localization import translate
            self.game.add_chat_message(translate(
                "mcpy.chat.privateMessageToMe", sender, text,
            ))
        else:
            target_id = self.server.find_player_id(target)
            if target_id is None:
                return False
            if not self.server.send_to_player(target_id, packet):
                return False
            if sender_id > 0 and target_id == sender_id:
                return True
        if sender_id > 0:
            self.server.send_to_player(sender_id, packet)
        elif not target_is_host:
            from game.localization import translate
            self.game.add_chat_message(translate(
                "mcpy.chat.privateMessageSent", target, text,
            ))
        return True

    def ban_player(self, name: str, reason: str = "Banned by host") -> bool:
        if self.mode != "host" or self.server is None or self.server_config is None:
            return False
        target = normalize_player_name(name)
        if not target or player_key(target) == player_key(self.server.host_name):
            return False
        reason = str(reason or "Banned by host")[:200]
        persisted = self.server_config.ban(target, reason)
        self.server.banned_names.add(player_key(target))
        if not hasattr(self.server, "ban_reasons"):
            self.server.ban_reasons = {}
        self.server.ban_reasons[player_key(target)] = reason
        self.game._ban_list = set(self.server_config.ban_names())
        self.server.kick_player(target, "Banned: " + reason)
        if not persisted:
            from game.localization import translate
            self.game.add_chat_message(translate(
                "mcpy.chat.warningBanSave"
            ))
        return True

    def pardon_player(self, name: str) -> bool:
        if self.mode != "host" or self.server_config is None or self.server is None:
            return False
        target = normalize_player_name(name)
        if not target or not self.server_config.pardon(target):
            return False
        self.server.banned_names.discard(player_key(target))
        if hasattr(self.server, "ban_reasons"):
            self.server.ban_reasons.pop(player_key(target), None)
        self.game._ban_list = set(self.server_config.ban_names())
        return True

    def ip_ban(self, address_or_player: str, reason: str = "IP banned by host") -> str | None:
        if self.mode != "host" or self.server is None or self.server_config is None:
            return None
        from game.multiplayer.server_config import normalize_ip_network
        network = normalize_ip_network(address_or_player)
        if network is None:
            address = self.server.find_player_address(address_or_player)
            network = normalize_ip_network(address) if address else None
        if network is None:
            return None
        reason = str(reason or "IP banned by host")[:200]
        if not self.server_config.add_ip_ban(network, reason):
            from game.localization import translate
            self.game.add_chat_message(translate(
                "mcpy.chat.warningIpBanSave"
            ))
        live_network = self.server.add_ip_ban(network, reason)
        if live_network is None:
            return None
        return live_network

    def pardon_ip(self, address_or_network: str) -> bool:
        if self.mode != "host" or self.server is None or self.server_config is None:
            return False
        from game.multiplayer.server_config import normalize_ip_network
        network = normalize_ip_network(address_or_network)
        if network is None:
            return False
        persisted = self.server_config.pardon_ip(network)
        live = self.server.pardon_ip(network)
        return bool(persisted or live)

    def set_whitelist_enabled(self, enabled: bool) -> bool:
        if self.mode != "host" or self.server is None or self.server_config is None:
            return False
        persisted = self.server_config.set_whitelist_enabled(enabled)
        self.server.whitelist_enabled = bool(enabled)
        if not persisted:
            from game.localization import translate
            self.game.add_chat_message(translate(
                "mcpy.chat.warningWhitelistSave"
            ))
        if enabled:
            allowed = set(self.server.whitelist_names)
            for snapshot in self.server.snapshot_players():
                player_name = str(snapshot.get("name") or "")
                if player_key(player_name) not in allowed:
                    self.server.kick_player(player_name, "You are not whitelisted on this server")
        return True

    def whitelist_player(self, name: str, enabled: bool = True) -> bool:
        if self.mode != "host" or self.server is None or self.server_config is None:
            return False
        target = normalize_player_name(name)
        if not target:
            return False
        key = player_key(target)
        if enabled:
            persisted = self.server_config.add_whitelist(target)
            self.server.whitelist_names.add(key)
            if not persisted:
                from game.localization import translate
                self.game.add_chat_message(translate(
                    "mcpy.chat.warningWhitelistEntrySave",
                ))
            return True
        existed = key in self.server.whitelist_names
        persisted = self.server_config.remove_whitelist(target)
        self.server.whitelist_names.discard(key)
        if self.server.whitelist_enabled and existed:
            self.server.kick_player(target, "Removed from server whitelist")
        if existed and not persisted:
            from game.localization import translate
            self.game.add_chat_message(translate(
                "mcpy.chat.warningWhitelistRemoveSave"
            ))
        return existed

    def set_operator(self, name: str, enabled: bool) -> bool:
        if self.mode != "host" or self.server is None or self.server_config is None:
            return False
        target = normalize_player_name(name)
        if not target or player_key(target) == player_key(self.server.host_name):
            return False
        if enabled:
            persisted = self.server_config.add_operator(target)
            self.server.operator_names.add(player_key(target))
            if not persisted:
                from game.localization import translate
                self.game.add_chat_message(translate(
                    "mcpy.chat.warningOperatorSave",
                ))
        else:
            if not self.server_config.remove_operator(target):
                return False
            self.server.operator_names.discard(player_key(target))
        self.game._op_list = set(self.server_config.operators)
        find_player = getattr(self.server, "find_player_id", None)
        target_id = find_player(target) if callable(find_player) else None
        if target_id is not None:
            self.server.send_to_player(target_id, P.op_status(enabled))
        return True

    def _remote_player_position(self, player_id: int):
        if self.server is None:
            return None
        return next((item for item in self.server.snapshot_players() if int(item.get("id", 0)) == int(player_id)), None)

    def _is_local_host_name(self, name: str) -> bool:
        if self.mode != "host" or self.server is None:
            return False
        if bool(getattr(self.game, "_dedicated_server_mode", False)):
            return False
        return player_key(name) == player_key(self.server.host_name)

    def resolve_player(self, name: str):
        if self.mode != "host" or self.server is None:
            return None
        wanted = normalize_player_name(name)
        if not wanted:
            return None
        if self._is_local_host_name(wanted):
            player = getattr(self.game, "player", None)
            if player is None:
                return None
            return self.server.host_name, tuple(float(v) for v in player.pos[:3])
        player_id = self.server.find_player_id(wanted)
        if player_id is None:
            return None
        snapshot = self._remote_player_position(player_id)
        if snapshot is None:
            return None
        return str(snapshot.get("name") or wanted), (
            float(snapshot["x"]), float(snapshot["y"]), float(snapshot["z"]),
        )

    def teleport_named_player(self, name: str, position) -> bool:
        if self.mode != "host" or self.server is None:
            return False
        try:
            position = tuple(float(v) for v in position[:3])
        except (TypeError, ValueError, OverflowError):
            return False
        if not all(math.isfinite(v) for v in position):
            return False
        wanted = normalize_player_name(name)
        if not wanted:
            return False
        if self._is_local_host_name(wanted):
            player = getattr(self.game, "player", None)
            if player is None:
                return False
            self._move_local_player(player, position)
            return True
        player_id = self.server.find_player_id(wanted)
        if player_id is None:
            return False
        return self.server.teleport_player(player_id, *position)

    @staticmethod
    def _move_local_player(player, position) -> None:
        if hasattr(player, "set_position"):
            player.set_position(*position)
        else:
            player.pos[:3] = list(position)
            if hasattr(player, "prev_pos"):
                player.prev_pos = list(player.pos)

    def kill_player(self, player_id: int) -> bool:
        if self.mode != "host" or self.server is None or int(player_id) <= 0:
            return False
        return bool(self.server.send_player_damage(
            int(player_id), P.KILL_DAMAGE, 0, source="kill",
        ))

    def _handle_command_request(self, event: dict) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            sender_id = int(event.get("id") or 0)
        except (TypeError, ValueError, OverflowError):
            return
        name = normalize_player_name(event.get("name") or "Player")
        if not name or sender_id <= 0 or self.server.find_player_id(name) != sender_id:
            return
        command = str(event.get("command") or "")[:200]
        from game.multiplayer.commands import CommandContext, dispatch
        is_op = self.is_operator(name)
        if command.lstrip("/").split(" ", 1)[0].lower() not in ("msg", "w", "tell"):
            self.log(f"{name} issued command: {command}" + ("" if is_op else " (not an operator)"))

        def reply(message: str) -> None:
            self.server.send_to_player(sender_id, P.command_reply(message))

        context = CommandContext(
            self.game, None, getattr(self.game, "world", None), name, reply,
            session=self, sender_id=sender_id, is_operator=is_op, is_host=False,
        )
        dispatch(command, context)

    def _save_profile_event(self, event: dict) -> None:
        if self.mode != "host" or self.server_config is None:
            return
        name = normalize_player_name(event.get("name"))
        if not name:
            return
        try:
            position = (float(event.get("x")), float(event.get("y")), float(event.get("z")))
            yaw = float(event.get("yaw", 0.0))
            pitch = float(event.get("pitch", 0.0))
        except (TypeError, ValueError, OverflowError):
            return
        self.server_config.update_position(name, position, yaw, pitch)
        inventory = event.get("inventory")
        if isinstance(inventory, dict):
            self.server_config.update_inventory(name, inventory)

    def send_chat(self, text: str) -> bool:
        text = (text or "").strip()
        if not text or not self.active:
            return False
        name = getattr(getattr(self.game, "settings", None), "username", "Player") or "Player"
        if self.mode == "host" and self.server is not None:
            print(f"<{name}> {text}")
            self.server.broadcast_chat(name, text)
            return True
        if self.mode == "client" and self.client is not None:
            print(f"<{name}> {text}")
            self.client.send_chat(text)
            return True
        return False

    def request_respawn(self) -> bool:
        if self.mode != "client" or self.client is None:
            return False
        if not self._respawn_request_pending:
            self.client.request_respawn()
            self._respawn_request_pending = True
        return True

    def _approve_remote_respawn(self, event: dict) -> None:
        if self.mode != "host" or self.server is None:
            return
        try:
            player_id = int(event.get("id") or 0)
        except (TypeError, ValueError, OverflowError):
            return
        remote = self.remote_players.get(player_id)
        if remote is None or remote.health > 0:
            return

        game = self.game
        spawn = getattr(game, "spawn_pos", None)
        world = getattr(game, "world", None)
        if spawn is None and world is not None:
            find_spawn = getattr(world, "find_spawn_position", None)
            if callable(find_spawn):
                spawn = find_spawn()
        if spawn is None:
            spawn = (0.5, 64.0, 0.5)
        resolver = getattr(game, "_safe_spawn_position", None)
        if callable(resolver):
            try:
                spawn = resolver(spawn)
            except Exception:
                pass
        try:
            x, y, z = (float(value) for value in spawn[:3])
        except (TypeError, ValueError, OverflowError):
            return
        self.server.send_player_respawn(player_id, x, y, z)

    def _remote_player_visible(self, remote, world=None, renderer=None) -> bool:
        world = world or getattr(self.game, "world", None)
        renderer = renderer or getattr(self.game, "renderer", None)
        return remote_player_in_render_view(
            remote, world, renderer, getattr(self.game, "player", None),
        )

    def render_players(self, partial: float = 0.0) -> None:
        from game.entities import player_model

        world = getattr(self.game, "world", None)
        renderer = getattr(self.game, "renderer", None)
        for rp in self.remote_players.values():
            if not self._remote_player_visible(rp, world, renderer):
                continue
            if rp.renderer is None:
                rp.renderer = renderer
            rp.world = world
            if rp.renderer is None:
                continue
            prev = float(getattr(rp, "prev_render_yaw_offset", rp.render_yaw_offset))
            cur = float(getattr(rp, "render_yaw_offset", rp.yaw))
            diff = (cur - prev + 180.0) % 360.0 - 180.0
            body_yaw = prev + diff * partial
            if world is not None and renderer is not None:
                try:
                    from game.entities.entity_shadow import (
                        SHADOW_SIZE_LIVING, render_entity_shadow,
                        shadow_opacity_for_distance,
                    )
                    opacity = shadow_opacity_for_distance(
                        world, rp.pos[0], rp.pos[1], rp.pos[2],
                    )
                    if opacity > 0.0:
                        render_entity_shadow(
                            world, rp.pos[0], rp.pos[1], rp.pos[2],
                            SHADOW_SIZE_LIVING, opacity=opacity, renderer=renderer,
                        )
                except Exception:
                    pass
            try:
                crouch = float(getattr(rp, "render_y_offset", 0.0))
                render_x = float(rp.pos[0])
                render_y = float(rp.pos[1] - crouch)
                render_z = float(rp.pos[2])
                render_head_yaw = None
                render_head_pitch = None
                if bool(getattr(rp, "is_sleeping", False)):
                    from game.block.bed import (
                        bed_sleep_render_offset, bed_sleep_body_yaw,
                    )
                    sleep_meta = int(getattr(rp, "_bed_sleep_meta", 0) or 0)
                    offset_x, offset_z = bed_sleep_render_offset(sleep_meta)
                    render_x += offset_x
                    render_z += offset_z
                    body_yaw = bed_sleep_body_yaw(sleep_meta)
                    render_head_yaw = body_yaw
                    render_head_pitch = 0.0
                if render_head_yaw is None:
                    player_model.render_player_model(
                        rp, render_x, render_y, render_z, body_yaw, partial,
                    )
                else:
                    player_model.render_player_model(
                        rp, render_x, render_y, render_z, body_yaw, partial,
                        head_yaw=render_head_yaw, head_pitch=render_head_pitch,
                    )
                if int(getattr(rp, "_fire_ticks", 0)) > 0:
                    player_model.render_player_fire_effect(
                        rp, rp.pos[0], rp.pos[1] - crouch, rp.pos[2]
                    )
            except Exception as exc:
                print(f"mp render player error: {exc}")

    def render_break_progress(self, world=None, renderer=None, entity_pass=None) -> None:
        if world is None:
            world = getattr(self.game, "world", None)
        if renderer is None:
            renderer = getattr(self.game, "renderer", None)
        if world is None or renderer is None:
            return
        from game.rendering.overlays import (
            destroy_overlay_is_entity_pass, draw_block_destroy_progress,
        )
        for rp in self.remote_players.values():
            if (self._remote_player_visible(rp, world, renderer)
                    and rp.break_target is not None and rp.break_progress > 0.0):
                if (entity_pass is not None and
                        destroy_overlay_is_entity_pass(rp.break_target, world) != bool(entity_pass)):
                    continue
                draw_block_destroy_progress(rp.break_target, rp.break_progress, world, renderer)

    def render_name_tags(self, partial: float = 0.0) -> None:
        if not self.remote_players:
            return
        game = self.game
        display = getattr(game, "display", None)
        renderer = getattr(game, "renderer", None)
        local_player = getattr(game, "player", None)
        if display is None or renderer is None or local_player is None:
            return
        try:
            from OpenGL.GL import (
                GL_ALPHA_TEST, GL_ALPHA_TEST_FUNC, GL_ALPHA_TEST_REF,
                GL_BLEND, GL_BLEND_DST, GL_BLEND_SRC, GL_CULL_FACE,
                GL_DEPTH_TEST, GL_DEPTH_WRITEMASK, GL_LIGHTING,
                GL_ONE_MINUS_SRC_ALPHA, GL_SRC_ALPHA, GL_TEXTURE_2D,
                GL_TEXTURE_BINDING_2D, glAlphaFunc, glBegin, glBindTexture,
                glBlendFunc, glColor4f, glDepthMask, glDisable, glEnable,
                glEnd, glGetBooleanv, glGetFloatv, glGetIntegerv, glIsEnabled,
                glNormal3f, glPopMatrix, glPushMatrix, glRotatef, glScalef,
                glTexCoord2f, glTranslatef, glVertex3f, GL_QUADS,
                GL_GREATER,
            )
        except Exception:
            return

        state = {
            GL_ALPHA_TEST: bool(glIsEnabled(GL_ALPHA_TEST)),
            GL_BLEND: bool(glIsEnabled(GL_BLEND)),
            GL_CULL_FACE: bool(glIsEnabled(GL_CULL_FACE)),
            GL_DEPTH_TEST: bool(glIsEnabled(GL_DEPTH_TEST)),
            GL_LIGHTING: bool(glIsEnabled(GL_LIGHTING)),
            GL_TEXTURE_2D: bool(glIsEnabled(GL_TEXTURE_2D)),
        }
        try:
            depth_mask = bool(glGetBooleanv(GL_DEPTH_WRITEMASK))
            texture_binding = int(glGetIntegerv(GL_TEXTURE_BINDING_2D))
            alpha_func = int(glGetIntegerv(GL_ALPHA_TEST_FUNC))
            alpha_ref = float(glGetFloatv(GL_ALPHA_TEST_REF))
            blend_src = int(glGetIntegerv(GL_BLEND_SRC))
            blend_dst = int(glGetIntegerv(GL_BLEND_DST))
        except Exception:
            depth_mask, texture_binding = True, 0
            alpha_func, alpha_ref = GL_GREATER, 0.5
            blend_src, blend_dst = GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA

        view_yaw = float(getattr(local_player, "yaw", 0.0))
        view_pitch = float(getattr(local_player, "pitch", 0.0))
        billboard_yaw, billboard_pitch = alpha_name_tag_billboard_rotation(
            view_yaw, view_pitch,
        )
        try:
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glDisable(GL_LIGHTING)
            glDisable(GL_CULL_FACE)
            for remote in tuple(self.remote_players.values()):
                if not self._remote_player_visible(remote, getattr(game, "world", None), renderer):
                    continue
                name = str(getattr(remote, "username", "Player"))[:16]
                if not name:
                    continue
                sneaking = bool(getattr(remote, "is_sneaking", False))
                scale = multiplayer_name_tag_scale()
                text_width = float(display.string_width(name, scale=1.0))
                tag_height = 2.3 - float(getattr(remote, "render_y_offset", 0.0))
                if hasattr(renderer, "camera_relative_offset"):
                    x, y, z = renderer.camera_relative_offset(
                        float(remote.pos[0]), float(remote.pos[1]) + tag_height,
                        float(remote.pos[2]),
                    )
                else:
                    x = float(remote.pos[0]) - float(getattr(renderer, "camX", 0.0))
                    y = float(remote.pos[1]) + tag_height - float(getattr(renderer, "camY", 0.0))
                    z = float(remote.pos[2]) - float(getattr(renderer, "camZ", 0.0))

                glPushMatrix()
                try:
                    glTranslatef(x, y, z)
                    glNormal3f(0.0, 1.0, 0.0)
                    glRotatef(billboard_yaw, 0.0, 1.0, 0.0)
                    glRotatef(billboard_pitch, 1.0, 0.0, 0.0)
                    glScalef(*multiplayer_name_tag_glyph_scale(scale))

                    half = text_width * 0.5
                    if sneaking:
                        glEnable(GL_DEPTH_TEST)
                        glDepthMask(False)
                        glEnable(GL_BLEND)
                        glDisable(GL_TEXTURE_2D)
                        glColor4f(0.0, 0.0, 0.0, 0.15)
                        glBegin(GL_QUADS)
                        glVertex3f(-half - 1.0, -1.0, 0.0)
                        glVertex3f(-half - 1.0, 8.0, 0.0)
                        glVertex3f(half + 1.0, 8.0, 0.0)
                        glVertex3f(half + 1.0, -1.0, 0.0)
                        glEnd()
                        display.draw_string(
                            name, (-half, 0.0), (255, 255, 255),
                            scale=1.0, shadow=False, alpha=0.4,
                        )
                        glDepthMask(True)
                    else:
                        glDepthMask(False)
                        glDisable(GL_DEPTH_TEST)
                        glEnable(GL_BLEND)
                        glDisable(GL_TEXTURE_2D)
                        glColor4f(0.0, 0.0, 0.0, 0.25)
                        glBegin(GL_QUADS)
                        glVertex3f(-half - 1.0, -1.0, 0.0)
                        glVertex3f(-half - 1.0, 8.0, 0.0)
                        glVertex3f(half + 1.0, 8.0, 0.0)
                        glVertex3f(half + 1.0, -1.0, 0.0)
                        glEnd()

                        display.draw_string(
                            name, (-half, 0.0), (255, 255, 255),
                            scale=1.0, shadow=False, alpha=0.125,
                        )
                        glEnable(GL_DEPTH_TEST)
                        glDepthMask(True)
                        display.draw_string(
                            name, (-half, 0.0), (255, 255, 255),
                            scale=1.0, shadow=False,
                        )
                finally:
                    glPopMatrix()
        finally:
            glBindTexture(GL_TEXTURE_2D, texture_binding)
            glAlphaFunc(alpha_func, alpha_ref)
            glBlendFunc(blend_src, blend_dst)
            glDepthMask(depth_mask)
            for capability, enabled in state.items():
                (glEnable if enabled else glDisable)(capability)
            glColor4f(1.0, 1.0, 1.0, 1.0)
