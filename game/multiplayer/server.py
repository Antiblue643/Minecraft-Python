
from __future__ import annotations

import ipaddress
import math
import re
import select
import socket
import threading
import time
from typing import Callable, Optional

from game.multiplayer import protocol as P
from game.multiplayer.netutils import normalize_ip_text


_BLOCK_NAME = re.compile(r"^[a-z0-9_./-]{1,64}$")
_MAX_COORD = 30_000_000


def _number(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return number if math.isfinite(number) else float(default)


class ClientConn:
    __slots__ = (
        "sock", "addr", "buf", "outbuf", "send_lock", "player_id", "name",
        "x", "y", "z", "yaw", "pitch", "alive", "close_after_flush",
        "inventory_state", "inventory_initialized", "inventory_revision", "last_inventory_tx",
        "break_target", "break_progress", "player_state",
        "respawn_pending", "event_window_start", "event_window_count", "last_profile_event",
        "denied_bed_key",
    )

    def __init__(self, sock: socket.socket, addr):
        self.sock = sock
        self.addr = addr
        self.buf = b""
        self.outbuf = bytearray()
        self.send_lock = threading.Lock()
        self.player_id = 0
        self.name = ""
        self.x = self.y = self.z = 0.0
        self.yaw = self.pitch = 0.0
        self.alive = True
        self.close_after_flush = False
        self.inventory_state = P.normalize_inventory_state({})
        self.inventory_initialized = False
        self.inventory_revision = 0
        self.last_inventory_tx = 0
        self.break_target = None
        self.break_progress = 0.0
        self.player_state = P.normalize_player_effect_state({})
        self.respawn_pending = False
        self.event_window_start = time.monotonic()
        self.event_window_count = 0
        self.last_profile_event = 0.0
        self.denied_bed_key = None

    def send_bytes(self, data: bytes) -> None:
        if not self.alive:
            return
        with self.send_lock:
            if len(self.outbuf) + len(data) > P.MAX_PENDING_BYTES:
                self.alive = False
                return
            self.outbuf.extend(data)

    def send(self, msg: dict) -> None:
        if not self.alive:
            return
        try:
            data = P.encode(msg)
        except (TypeError, ValueError, OverflowError):
            return
        self.send_bytes(data)

    def has_pending(self) -> bool:
        with self.send_lock:
            return bool(self.outbuf)

    def flush(self) -> None:
        if not self.alive:
            return
        with self.send_lock:
            if not self.outbuf:
                return
            try:
                sent = self.sock.send(self.outbuf)
            except BlockingIOError:
                return
            except OSError:
                self.alive = False
                return
            if sent == 0:
                self.alive = False
                return
            del self.outbuf[:sent]


class MultiplayerServer:

    def __init__(
        self,
        seed: int,
        host_name: str,
        spawn: tuple[float, float, float],
        port: int = P.DEFAULT_PORT,
        on_log: Optional[Callable[[str], None]] = None,
        snow_covered: bool = False,
        world_time: int = 0,
        accurate_generation: bool = False,
        debug_world: bool = False,
        banned_names=None,
        operator_names=None,
        player_profiles=None,
        whitelist_enabled: bool = False,
        whitelist_names=None,
        ip_bans=None,
        host_player_visible: bool = True,
    ):
        self.seed = int(seed)
        self.snow_covered = bool(snow_covered)
        self.world_time = max(0, int(world_time))
        self.accurate_generation = bool(accurate_generation)
        self.debug_world = bool(debug_world)
        self.host_name = str(host_name)[:16] or "Host"
        self.spawn = (float(spawn[0]), float(spawn[1]), float(spawn[2]))
        self.port = int(port)
        self.on_log = on_log or (lambda _m: None)
        self.banned_names = {str(name).strip().casefold() for name in (banned_names or ()) if str(name).strip()}
        self.operator_names = {str(name).strip().casefold() for name in (operator_names or ()) if str(name).strip()}
        self.player_profiles = player_profiles if isinstance(player_profiles, dict) else {}
        self.ban_reasons: dict[str, str] = {}
        self.whitelist_enabled = bool(whitelist_enabled)
        self.whitelist_names = {
            str(name).strip().casefold() for name in (whitelist_names or ()) if str(name).strip()
        }
        self.ip_bans: dict[str, str] = {}
        for network_text, record in (ip_bans or {}).items():
            try:
                network = ipaddress.ip_network(str(network_text), strict=False)
                mapped = network.network_address.ipv4_mapped if isinstance(network, ipaddress.IPv6Network) else None
                if mapped is not None and network.prefixlen >= 96:
                    network = ipaddress.ip_network((mapped, network.prefixlen - 96), strict=False)
                network = str(network)
            except (TypeError, ValueError):
                continue
            reason = record.get("reason") if isinstance(record, dict) else record
            self.ip_bans[network] = str(reason or "IP banned by host")[:200]
        self.host_player_visible = bool(host_player_visible)

        self._sock: Optional[socket.socket] = None
        self.bind_host = "0.0.0.0"
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._clients: dict[int, ClientConn] = {}
        self._next_id = 2  
        self._events: list[dict] = []
        self.host_inventory = P.normalize_inventory_state({})
        self.host_break_target = None
        self.host_break_progress = 0.0

        self.host_id = 1
        self.host_x, self.host_y, self.host_z = self.spawn
        self.host_yaw = 0.0
        self.host_pitch = 0.0
        self.host_player_state = P.normalize_player_effect_state({})
        self._bed_claims: dict[tuple[int, int, int], int] = {}

    @property
    def bound_port(self) -> int:
        sock = self._sock
        if sock is None:
            return self.port
        try:
            return int(sock.getsockname()[1])
        except OSError:
            return self.port

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        sock = None
        bind_host = "0.0.0.0"
        if getattr(socket, "has_ipv6", False) and hasattr(socket, "IPV6_V6ONLY"):
            candidate = None
            try:
                candidate = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                candidate.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                candidate.bind(("::", self.port))
                candidate.listen(8)
                candidate.setblocking(False)
                sock = candidate
                bind_host = "::"
            except OSError:
                if candidate is not None:
                    candidate.close()

        if sock is None:
            candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                candidate.bind(("0.0.0.0", self.port))
                candidate.listen(8)
                candidate.setblocking(False)
                sock = candidate
            except Exception:
                candidate.close()
                raise

        self._sock = sock
        self.bind_host = bind_host
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mp-server", daemon=True)
        self._thread.start()
        op_count = len(self.operator_names)
        ban_count = len(self.banned_names) + len(self.ip_bans)
        self.on_log(
            f"[MP] Server listening on {bind_host}:{self.port} "
            f"(ops={op_count}, bans={ban_count}, players_cap via backlog=8)"
        )

    def stop(self) -> None:
        self._stop.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
            self._bed_claims.clear()
        for client in clients:
            client.alive = False
            try:
                client.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client.sock.close()
            except OSError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def set_host_state(
        self, x: float, y: float, z: float, yaw: float, pitch: float, state=None,
    ) -> None:
        values = tuple(_number(v) for v in (x, y, z, yaw, pitch))
        normalized_state = P.normalize_player_effect_state(state or {})
        updates = []
        with self._lock:
            normalized_state, updates, _denied = self._reconcile_bed_claim_locked(
                self.host_id, normalized_state,
            )
            self.host_x, self.host_y, self.host_z, self.host_yaw, self.host_pitch = values
            self.host_player_state = normalized_state
        self._publish_bed_occupancy_updates(updates)

    def set_client_state(
        self, player_id: int, x: float, y: float, z: float,
        yaw: float, pitch: float, state=None,
    ) -> bool:
        values = tuple(_number(v) for v in (x, y, z, yaw, pitch))
        if max(abs(values[0]), abs(values[1]), abs(values[2])) > _MAX_COORD:
            return False
        normalized_state = P.normalize_player_effect_state(state or {})
        updates = []
        with self._lock:
            client = next((
                peer for peer in self._clients.values()
                if peer.player_id == int(player_id) and peer.alive
            ), None)
            if client is None:
                return False
            normalized_state, updates, _denied = self._reconcile_bed_claim_locked(
                client.player_id, normalized_state, client=client,
            )
            client.x, client.y, client.z, client.yaw, client.pitch = values
            client.player_state = normalized_state
        self._publish_bed_occupancy_updates(updates)
        return True

    @staticmethod
    def _bed_key_from_state(state) -> tuple[int, int, int] | None:
        if not isinstance(state, dict) or not bool(state.get("sleeping", False)):
            return None
        bed = state.get("bed_location")
        if not isinstance(bed, (list, tuple)) or len(bed) != 3:
            return None
        try:
            x, y, z = (int(value) for value in bed)
        except (TypeError, ValueError, OverflowError):
            return None
        if not (-30_000_000 <= x <= 30_000_000 and 0 <= y < 256
                and -30_000_000 <= z <= 30_000_000):
            return None
        return x, y, z

    def _reconcile_bed_claim_locked(self, player_id, state, *, client=None):
        pid = int(player_id)
        updates = []
        old_key = next((key for key, owner in self._bed_claims.items() if owner == pid), None)
        new_key = self._bed_key_from_state(state)

        if old_key is not None and old_key != new_key:
            self._bed_claims.pop(old_key, None)
            updates.append((old_key, False, pid))

        denied = None
        if new_key is not None:
            owner = self._bed_claims.get(new_key)
            if owner is not None and owner != pid:
                denied = new_key
                state = dict(state)
                state.update({
                    "sleeping": False, "sleep_timer": 0,
                    "bed_location": None, "bed_meta": 0,
                })
            elif owner is None:
                self._bed_claims[new_key] = pid
                updates.append((new_key, True, pid))

        if client is not None:
            if denied is not None:
                client.denied_bed_key = denied
            elif new_key is None:
                client.denied_bed_key = None
            elif self._bed_claims.get(new_key) == pid:
                client.denied_bed_key = None
        return state, updates, denied

    @staticmethod
    def _bed_occupancy_packet(key, occupied, player_id):
        return {
            "t": "bed_occupancy", "x": int(key[0]), "y": int(key[1]),
            "z": int(key[2]), "occupied": bool(occupied),
            "player_id": int(player_id),
        }

    def _publish_bed_occupancy_updates(self, updates) -> None:
        for key, occupied, player_id in updates or ():
            packet = self._bed_occupancy_packet(key, occupied, player_id)
            self._broadcast(packet)
            self._push(packet)

    def claim_bed(self, player_id: int, bed_location) -> bool:
        try:
            key = tuple(int(value) for value in bed_location)
        except (TypeError, ValueError, OverflowError):
            return False
        if len(key) != 3 or not (
            -30_000_000 <= key[0] <= 30_000_000 and 0 <= key[1] < 256
            and -30_000_000 <= key[2] <= 30_000_000
        ):
            return False
        pid = int(player_id)
        changed = False
        with self._lock:
            owner = self._bed_claims.get(key)
            if owner is not None and owner != pid:
                return False
            if owner is None:
                self._bed_claims[key] = pid
                changed = True
        if changed:
            self._publish_bed_occupancy_updates(((key, True, pid),))
        return True

    def release_bed(self, player_id: int, bed_location=None) -> bool:
        pid = int(player_id)
        wanted = None
        if bed_location is not None:
            try:
                wanted = tuple(int(value) for value in bed_location)
            except (TypeError, ValueError, OverflowError):
                wanted = None
        updates = []
        with self._lock:
            for key, owner in list(self._bed_claims.items()):
                if owner == pid and (wanted is None or wanted == key):
                    del self._bed_claims[key]
                    updates.append((key, False, pid))
        self._publish_bed_occupancy_updates(updates)
        return bool(updates)

    def set_world_metadata(self, world_time: int, snow_covered: bool) -> None:
        with self._lock:
            self.world_time = max(0, int(world_time))
            self.snow_covered = bool(snow_covered)

    def set_host_inventory(self, state: dict) -> None:
        normalized = P.normalize_inventory_state(state)
        with self._lock:
            if normalized == self.host_inventory:
                return
            self.host_inventory = normalized
        self._broadcast(P.inventory_packet(self.host_id, normalized))

    def set_client_inventory_state(self, player_id: int, state: dict) -> bool:
        normalized = P.normalize_inventory_state(state)
        with self._lock:
            client = next((c for c in self._clients.values()
                           if c.player_id == int(player_id) and c.alive), None)
            if client is None:
                return False
            client.inventory_state = normalized
            client.inventory_initialized = True
            client.inventory_revision += 1
            revision = client.inventory_revision
        self._broadcast(P.inventory_packet(int(player_id), normalized, revision))
        return True

    def client_inventory_revision(self, player_id: int) -> int | None:
        with self._lock:
            client = next((c for c in self._clients.values()
                           if c.player_id == int(player_id) and c.alive), None)
            return int(client.inventory_revision) if client is not None else None

    def send_client_inventory_snapshot(self, player_id: int) -> bool:
        with self._lock:
            client = next((c for c in self._clients.values()
                           if c.player_id == int(player_id) and c.alive), None)
            if client is None:
                return False
            packet = P.inventory_packet(
                client.player_id, client.inventory_state, client.inventory_revision,
            )
            peer = client
        peer.send(packet)
        return peer.alive

    def set_host_break_progress(self, target, progress: float = 0.0) -> None:
        packet = P.break_progress_packet(self.host_id, target, progress)
        normalized_target = (packet.get("x"), packet.get("y"), packet.get("z")) if packet.get("active") else None
        normalized_progress = float(packet.get("progress", 0.0))
        with self._lock:
            self.host_break_target = normalized_target
            self.host_break_progress = normalized_progress
        self._broadcast(packet)

    def broadcast_animation(self, player_id: int, action: str = "swing") -> None:
        self._broadcast(P.animation_packet(player_id, action))

    def broadcast_chat(self, name: str, msg: str) -> None:
        packet = P.chat(name, msg)
        with self._lock:
            clients = [c for c in self._clients.values() if c.player_id > 0]
        for client in clients:
            client.send(packet)

    def send_to_player(self, player_id: int, packet: dict) -> bool:
        try:
            wanted = int(player_id)
        except (TypeError, ValueError, OverflowError):
            return False
        with self._lock:
            peer = self._clients.get(wanted)
            if peer is None or not peer.alive or peer.player_id <= 0:
                return False
            peer.send(packet)
            return peer.alive

    def find_player_id(self, name: str) -> int | None:
        wanted = str(name or "").strip().casefold()
        if not wanted:
            return None
        with self._lock:
            for peer in self._clients.values():
                if peer.player_id > 0 and peer.alive and peer.name.casefold() == wanted:
                    return peer.player_id
        return None

    def find_player_address(self, name: str) -> str | None:
        wanted = str(name or "").strip().casefold()
        if not wanted:
            return None
        with self._lock:
            for peer in self._clients.values():
                if peer.player_id > 0 and peer.alive and peer.name.casefold() == wanted:
                    try:
                        return str(self._normalized_peer_ip(peer.addr[0]))
                    except (IndexError, TypeError):
                        return None
        return None

    def add_ip_ban(self, address_or_network: str, reason: str = "IP banned by host") -> str | None:
        try:
            network = str(ipaddress.ip_network(str(address_or_network).strip(), strict=False))
        except (TypeError, ValueError):
            return None
        with self._lock:
            self.ip_bans[network] = str(reason or "IP banned by host")[:200]
            peers = [
                peer for peer in self._clients.values()
                if peer.player_id > 0 and peer.alive and self._ip_matches(peer.addr[0], network)
            ]
        for peer in peers:
            peer.send(P.kick("IP banned: " + self.ip_bans[network]))
            peer.close_after_flush = True
        return network

    def pardon_ip(self, address_or_network: str) -> bool:
        try:
            network = str(ipaddress.ip_network(str(address_or_network).strip(), strict=False))
        except (TypeError, ValueError):
            return False
        with self._lock:
            key = network
            if key not in self.ip_bans:
                try:
                    candidate = ipaddress.ip_network(network, strict=False)
                    if candidate.prefixlen == candidate.max_prefixlen:
                        matches = [
                            text for text in self.ip_bans
                            if candidate.network_address in ipaddress.ip_network(text, strict=False)
                        ]
                        if matches:
                            key = max(matches, key=lambda text: ipaddress.ip_network(text).prefixlen)
                except ValueError:
                    pass
            return self.ip_bans.pop(key, None) is not None

    @staticmethod
    def _normalized_peer_ip(address):
        try:
            parsed = ipaddress.ip_address(str(address).strip())
            if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
                return parsed.ipv4_mapped
            return parsed
        except ValueError:
            return None

    @classmethod
    def _ip_matches(cls, address: str, network_text: str) -> bool:
        try:
            parsed = cls._normalized_peer_ip(address)
            network = ipaddress.ip_network(network_text, strict=False)
            return parsed is not None and parsed.version == network.version and parsed in network
        except ValueError:
            return False

    def _ip_ban_reason(self, address: str) -> str | None:
        with self._lock:
            for network, reason in self.ip_bans.items():
                if self._ip_matches(address, network):
                    return reason
        return None

    def teleport_player(self, player_id: int, x: float, y: float, z: float) -> bool:
        try:
            wanted = int(player_id)
            values = tuple(float(value) for value in (x, y, z))
        except (TypeError, ValueError, OverflowError):
            return False
        if not all(math.isfinite(value) and abs(value) <= _MAX_COORD for value in values):
            return False
        with self._lock:
            peer = self._clients.get(wanted)
            if peer is None or not peer.alive or peer.player_id <= 0:
                return False
            peer.x, peer.y, peer.z = values
            yaw, pitch = peer.yaw, peer.pitch
            name = peer.name
        self.send_to_player(wanted, P.teleport(*values, yaw=yaw, pitch=pitch))
        self._broadcast(P.pos(wanted, *values, yaw, pitch), exclude=wanted)
        self._push({"t": "profile_update", "name": name, "x": values[0], "y": values[1], "z": values[2], "yaw": yaw, "pitch": pitch})
        return True

    def broadcast_block_update(self, x: int, y: int, z: int, block_type, meta: int = 0) -> None:
        packet = P.block_update(x, y, z, block_type, meta)
        with self._lock:
            clients = [c for c in self._clients.values() if c.player_id > 0]
        for client in clients:
            client.send(packet)

    def broadcast_tile_entity_update(self, state: dict) -> bool:
        packet = P.tile_entity_packet(state)
        if packet is None:
            return False
        self._broadcast(packet)
        return True

    def broadcast_entity_sync(self, packet: dict) -> None:
        if not isinstance(packet, dict) or packet.get("t") != "entity_sync":
            return
        self._broadcast(packet)

    def broadcast_world_event(self, event: dict) -> bool:
        packet = P.normalize_world_event(event, allow_authoritative=True)
        if packet is None:
            return False
        self._broadcast(packet)
        return True


    def send_player_damage(
        self, player_id: int, amount: int, attacker_id: int = 0, source: str = "monster",
        knockback=None,
    ) -> bool:
        packet = P.player_damage_packet(
            player_id, amount, attacker_id, source=source, knockback=knockback,
        )
        with self._lock:
            client = next((c for c in self._clients.values()
                           if c.player_id == int(player_id) and c.alive), None)
        if client is None:
            return False
        client.send(packet)
        return client.alive

    def send_player_respawn(self, player_id: int, x: float, y: float, z: float) -> bool:
        packet = P.player_respawn_packet(player_id, x, y, z)
        with self._lock:
            client = next((c for c in self._clients.values()
                           if c.player_id == int(player_id) and c.alive), None)
        if client is None:
            return False
        client.send(packet)
        return client.alive

    def send_chunk_packet(self, player_id: int, packet: dict) -> bool:
        with self._lock:
            client = next((c for c in self._clients.values()
                           if c.player_id == int(player_id) and c.alive), None)
        if client is None:
            return False
        client.send(packet)
        return client.alive

    def poll_events(self) -> list[dict]:
        with self._lock:
            events, self._events = self._events, []
        return events

    def _push(self, event: dict) -> None:
        with self._lock:
            self._events.append(event)

    def _broadcast(self, msg: dict, exclude: Optional[int] = None) -> None:
        with self._lock:
            clients = [c for c in self._clients.values() if c.player_id > 0]
        for client in clients:
            if exclude is None or client.player_id != exclude:
                client.send(msg)

    def _run(self) -> None:
        listen_sock = self._sock
        if listen_sock is None:
            return
        last_pos = 0.0
        while not self._stop.is_set():
            with self._lock:
                clients = list(self._clients.values())
            live = [c for c in clients if c.alive]
            readers = [listen_sock, *(c.sock for c in live)]
            writers = [c.sock for c in live if c.has_pending()]
            try:
                readable, writable, _ = select.select(readers, writers, [], 0.05)
            except (OSError, ValueError):
                if self._stop.is_set():
                    break
                time.sleep(0.01)
                continue

            if listen_sock in readable:
                while not self._stop.is_set():
                    try:
                        conn, addr = listen_sock.accept()
                    except BlockingIOError:
                        break
                    except OSError:
                        break
                    conn.setblocking(False)
                    self._handle_new(ClientConn(conn, addr))

            ready = set(readable)
            write_ready = set(writable)
            for client in live:
                if client.sock in ready:
                    self._read_client(client)
                if client.alive and client.sock in write_ready:
                    client.flush()
                if client.alive and client.close_after_flush and not client.has_pending():
                    client.alive = False

            now = time.monotonic()
            if now - last_pos >= 0.05:
                last_pos = now
                with self._lock:
                    host_state = (
                        self.host_x, self.host_y, self.host_z,
                        self.host_yaw, self.host_pitch, dict(self.host_player_state),
                    )
                    client_state = [
                        (c.player_id, c.x, c.y, c.z, c.yaw, c.pitch, dict(c.player_state))
                        for c in self._clients.values()
                        if c.player_id > 0 and c.alive
                    ]
                self._broadcast(P.pos(self.host_id, *host_state[:5], state=host_state[5]))
                for pid, x, y, z, yaw, pitch, state in client_state:
                    self._broadcast(P.pos(pid, x, y, z, yaw, pitch, state=state), exclude=pid)

            for client in live:
                if not client.alive:
                    self._drop(client)

        with self._lock:
            remaining = list(self._clients.values())
        for client in remaining:
            if self._stop.is_set():
                break
            client.alive = False
            self._drop(client)

    def _handle_new(self, client: ClientConn) -> None:
        with self._lock:
            self._clients[-id(client)] = client

    def _read_client(self, client: ClientConn) -> None:
        if not client.alive:
            return
        try:
            data = client.sock.recv(4096)
        except BlockingIOError:
            return
        except OSError:
            client.alive = False
            return
        if not data:
            client.alive = False
            return
        if not client.buf:
            first = data.lstrip()[:1]
            if first and first != b"{":
                client.send_bytes(P.legacy_java_kick())
                client.close_after_flush = True
                return
        client.buf += data
        while b"\n" in client.buf:
            line, client.buf = client.buf.split(b"\n", 1)
            if len(line) > P.MAX_LINE:
                client.send(P.kick("Message too large"))
                client.close_after_flush = True
                return
            msg = P.decode_line(line)
            if msg is not None:
                self._on_message(client, msg)
                if not client.alive or client.close_after_flush:
                    return
        if len(client.buf) > P.MAX_LINE:
            client.send(P.kick("Message too large"))
            client.close_after_flush = True

    def _on_message(self, client: ClientConn, msg: dict) -> None:
        msg_type = msg.get("t")
        if msg_type == "hello":
            if client.player_id > 0:
                return
            try:
                proto = int(msg.get("proto") or 0)
            except (TypeError, ValueError, OverflowError):
                proto = 0
            if proto != P.PROTO_VERSION:
                client.send(P.kick(f"Bad protocol {proto}, need {P.PROTO_VERSION}"))
                client.close_after_flush = True
                return
            source_ip = str(client.addr[0]) if isinstance(client.addr, (tuple, list)) and client.addr else ""
            ip_reason = self._ip_ban_reason(source_ip)
            if ip_reason:
                client.send(P.kick("IP banned: " + ip_reason))
                client.close_after_flush = True
                return
            name = str(msg.get("name") or "Player").strip()[:16] or "Player"
            if self.whitelist_enabled and name.casefold() not in self.whitelist_names:
                client.send(P.kick("You are not whitelisted on this server"))
                client.close_after_flush = True
                return
            if name.casefold() in self.banned_names:
                client.send(P.kick("Banned: " + str(self.ban_reasons.get(name.casefold()) or "Banned by host")))
                client.close_after_flush = True
                return
            profile = self.player_profiles.get(name.casefold(), {})
            with self._lock:
                if name.lower() == self.host_name.lower() or any(
                    c is not client and c.player_id > 0 and c.name.lower() == name.lower()
                    for c in self._clients.values()
                ):
                    client.send(P.kick("Name taken"))
                    client.close_after_flush = True
                    return
                pid = self._next_id
                self._next_id += 1
                for key, other in list(self._clients.items()):
                    if other is client:
                        del self._clients[key]
                        break
                client.player_id = pid
                client.name = name
                last_pos = profile.get("pos") if isinstance(profile, dict) else None
                if isinstance(last_pos, (list, tuple)) and len(last_pos) >= 3:
                    try:
                        saved = tuple(float(value) for value in last_pos[:3])
                        if all(math.isfinite(value) and abs(value) <= _MAX_COORD for value in saved):
                            client.x, client.y, client.z = saved
                        else:
                            client.x, client.y, client.z = self.spawn
                    except (TypeError, ValueError, OverflowError):
                        client.x, client.y, client.z = self.spawn
                else:
                    client.x, client.y, client.z = self.spawn
                try:
                    client.yaw = float(profile.get("yaw", 0.0)) if isinstance(profile, dict) else 0.0
                    client.pitch = max(-90.0, min(90.0, float(profile.get("pitch", 0.0)))) if isinstance(profile, dict) else 0.0
                except (TypeError, ValueError, OverflowError):
                    client.yaw = client.pitch = 0.0
                if isinstance(profile, dict) and isinstance(profile.get("inventory"), dict):
                    client.inventory_state = P.normalize_inventory_state(profile["inventory"])
                    client.inventory_initialized = True
                    client.inventory_revision = 1
                others = []
                if self.host_player_visible:
                    others.append(P.player_snapshot(
                        self.host_id, self.host_name, self.host_x, self.host_y,
                        self.host_z, self.host_yaw, self.host_pitch,
                        inventory_state=self.host_inventory,
                        break_state={"target": self.host_break_target, "progress": self.host_break_progress},
                        state=self.host_player_state,
                    ))
                occupied_beds = [list(key) for key in self._bed_claims]
                world_time_snapshot = self.world_time
                snow_covered_snapshot = self.snow_covered
                others.extend(
                    P.player_snapshot(
                        c.player_id, c.name, c.x, c.y, c.z, c.yaw, c.pitch,
                        inventory_state=c.inventory_state,
                        break_state={"target": c.break_target, "progress": c.break_progress},
                        state=c.player_state,
                    )
                    for c in self._clients.values()
                    if c is not client and c.player_id > 0
                )
                self._clients[pid] = client
            client.send(P.welcome(
                pid, self.seed, client.x, client.y, client.z,
                yaw=client.yaw, pitch=client.pitch, players=others,
                snow_covered=snow_covered_snapshot, world_time=world_time_snapshot,
                operator=str(name).strip().casefold() in self.operator_names,
                inventory_state=client.inventory_state,
                inventory_revision=client.inventory_revision,
                accurate_generation=self.accurate_generation,
                debug_world=self.debug_world,
                occupied_beds=occupied_beds,
            ))
            self._broadcast(P.join(
                pid, name, client.x, client.y, client.z,
                yaw=client.yaw, pitch=client.pitch,
                inventory_state=client.inventory_state,
            ), exclude=pid)
            self._push({
                "t": "join", "id": pid, "name": name,
                "x": client.x, "y": client.y, "z": client.z,
                "yaw": client.yaw, "pitch": client.pitch,
                "inventory": client.inventory_state,
            })
            peer_ip = normalize_ip_text(client.addr[0] if client.addr else "")
            is_op = str(name).strip().casefold() in self.operator_names
            op_note = " (op)" if is_op else ""
            coord_note = f" at {client.x:.1f},{client.y:.1f},{client.z:.1f}"
            ip_note = f" from {peer_ip}" if peer_ip else ""
            self._push({"t": "log", "msg": f"{name}{op_note} joined the game{coord_note}{ip_note}."})
            self.on_log(f"[MP] {name}{op_note} joined id={pid}{coord_note}{ip_note}")
            return

        if client.player_id <= 0:
            return

        if msg_type == "pos":
            x, y, z = (_number(msg.get(key), default) for key, default in (("x", client.x), ("y", client.y), ("z", client.z)))
            yaw, pitch = (_number(msg.get(key), default) for key, default in (("yaw", client.yaw), ("pitch", client.pitch)))
            if max(abs(x), abs(y), abs(z)) > _MAX_COORD:
                return
            player_state = P.normalize_player_effect_state(msg)
            profile_event = None
            bed_updates = []
            bed_denied = None
            bed_denial_already_sent = False
            with self._lock:
                previous_denied = client.denied_bed_key
                player_state, bed_updates, bed_denied = self._reconcile_bed_claim_locked(
                    client.player_id, player_state, client=client,
                )
                if bed_denied is not None:
                    bed_denial_already_sent = previous_denied == bed_denied
                client.x, client.y, client.z = x, y, z
                client.yaw, client.pitch = yaw, max(-90.0, min(90.0, pitch))
                client.player_state = player_state
                if player_state["health"] > 0.0:
                    client.respawn_pending = False
                now = time.monotonic()
                if now - client.last_profile_event >= 2.0:
                    client.last_profile_event = now
                    profile_event = {
                        "t": "profile_update", "name": client.name,
                        "x": client.x, "y": client.y, "z": client.z,
                        "yaw": client.yaw, "pitch": client.pitch,
                    }
            self._publish_bed_occupancy_updates(bed_updates)
            if bed_denied is not None and not bed_denial_already_sent:
                client.send({
                    "t": "bed_claim_result", "granted": False,
                    "bed_location": list(bed_denied),
                })
            if profile_event is not None:
                self._push(profile_event)
        elif msg_type == "chat":
            text = str(msg.get("msg") or "").replace("\x00", "").strip()[:200]
            if text:
                packet = P.chat(client.name, text)
                self._broadcast(packet, exclude=client.player_id)
                self._push({"t": "chat", "from": client.name, "msg": text})
        elif msg_type == "command":
            command = str(msg.get("command") or "").replace("\x00", "").strip()[:200]
            if command.startswith("/"):
                self._push({"t": "command_request", "id": client.player_id, "name": client.name, "command": command})
        elif msg_type == "block":
            try:
                x, y, z = int(msg.get("x")), int(msg.get("y")), int(msg.get("z"))
                meta = int(msg.get("meta", 0)) & 15
            except (TypeError, ValueError, OverflowError):
                return
            block_type = msg.get("block")
            if block_type is not None and (
                not isinstance(block_type, str) or not _BLOCK_NAME.fullmatch(block_type)
            ):
                return
            if not (0 <= y < 128 and max(abs(x), abs(z)) <= _MAX_COORD):
                return
            with self._lock:
                dx = x - client.x
                dz = z - client.z
                dy = y - client.y
            if dx * dx + dz * dz > 20.0 * 20.0 or abs(dy) > 12.0:
                return
            event = P.block_update(x, y, z, block_type, meta)
            self._broadcast(event, exclude=client.player_id)
            self._push(event)
        elif msg_type == "tile_entity":
            state = P.normalize_tile_entity_state(msg)
            if state is None or state["type"] == "mob_spawner":
                return
            with self._lock:
                dx = state["x"] + 0.5 - client.x
                dy = state["y"] + 0.5 - client.y
                dz = state["z"] + 0.5 - client.z
            if dx * dx + dy * dy + dz * dz > 8.0 * 8.0:
                return
            now = time.monotonic()
            if now - client.event_window_start >= 1.0:
                client.event_window_start = now
                client.event_window_count = 0
            client.event_window_count += 1
            if client.event_window_count > 40:
                return
            self._push({"t": "tile_entity_request", "id": client.player_id, "state": state})
        elif msg_type == "attack":
            try:
                entity_id = int(msg.get("entity_id"))
                damage = int(msg.get("damage"))
            except (TypeError, ValueError, OverflowError):
                return
            if not (1 <= entity_id <= 2**31 - 1 and 1 <= damage <= 10):
                return
            self._push({
                "t": "attack_request", "id": client.player_id,
                "entity_id": entity_id, "damage": damage,
            })
        elif msg_type in {"entity_interact", "entity_push"}:
            try:
                entity_id = int(msg.get("entity_id"))
            except (TypeError, ValueError, OverflowError):
                return
            if not 1 <= entity_id <= 2**31 - 1:
                return
            if msg_type == "entity_interact":
                action = str(msg.get("action") or "")
                if action not in {"furnace_fuel", "milk_cow", "dye_sheep"}:
                    return
                limit = 20
                packet_type = "entity_interact_request"
                extra = {"action": action}
            else:
                limit = 30
                packet_type = "entity_push_request"
                extra = {}
            now = time.monotonic()
            if now - client.event_window_start >= 1.0:
                client.event_window_start = now
                client.event_window_count = 0
            client.event_window_count += 1
            if client.event_window_count > limit:
                return
            self._push({"t": packet_type, "id": client.player_id,
                        "entity_id": entity_id, **extra})
        elif msg_type == "boat_control":
            try:
                entity_id = int(msg.get("entity_id"))
                forward = _number(msg.get("forward", 0.0))
                strafe = _number(msg.get("strafe", 0.0))
                yaw = _number(msg.get("yaw", client.yaw))
            except (TypeError, ValueError, OverflowError):
                return
            if not (1 <= entity_id <= 2**31 - 1):
                return
            riding_value = msg.get("riding", False)
            if not isinstance(riding_value, bool):
                return
            riding = riding_value
            now = time.monotonic()
            if now - client.event_window_start >= 1.0:
                client.event_window_start = now
                client.event_window_count = 0
            client.event_window_count += 1
            if client.event_window_count > 60:
                return
            self._push({
                "t": "boat_control_request", "id": client.player_id,
                "entity_id": entity_id, "riding": riding,
                "forward": max(-1.0, min(1.0, forward)),
                "strafe": max(-1.0, min(1.0, strafe)),
                "yaw": max(-3600.0, min(3600.0, yaw)),
            })
        elif msg_type == "item_use":
            action = str(msg.get("action") or "")
            if action not in {"bonemeal", "cake_eat"}:
                return
            try:
                x, y, z = int(msg.get("x")), int(msg.get("y")), int(msg.get("z"))
            except (TypeError, ValueError, OverflowError):
                return
            if not (0 <= y < 256 and max(abs(x), abs(z)) <= _MAX_COORD):
                return
            with self._lock:
                dx, dy, dz = x + 0.5 - client.x, y + 0.5 - client.y, z + 0.5 - client.z
            if dx * dx + dy * dy + dz * dz > 8.0 * 8.0:
                return
            now = time.monotonic()
            if now - client.event_window_start >= 1.0:
                client.event_window_start = now
                client.event_window_count = 0
            client.event_window_count += 1
            if client.event_window_count > 6:
                return
            self._push({
                "t": "item_use_request", "id": client.player_id,
                "action": action, "x": x, "y": y, "z": z,
            })
        elif msg_type == "note_block_use":
            action = str(msg.get("action") or "")
            if action not in {"play", "pitch"}:
                return
            try:
                x, y, z = int(msg.get("x")), int(msg.get("y")), int(msg.get("z"))
            except (TypeError, ValueError, OverflowError):
                return
            if not (0 <= y < 256 and max(abs(x), abs(z)) <= _MAX_COORD):
                return
            with self._lock:
                dx, dy, dz = x + 0.5 - client.x, y + 0.5 - client.y, z + 0.5 - client.z
            if dx * dx + dy * dy + dz * dz > 8.0 * 8.0:
                return
            now = time.monotonic()
            if now - client.event_window_start >= 1.0:
                client.event_window_start = now
                client.event_window_count = 0
            client.event_window_count += 1
            if client.event_window_count > 20:
                return
            self._push({
                "t": "note_block_use_request", "id": client.player_id,
                "action": action, "x": x, "y": y, "z": z,
            })
        elif msg_type in {"bucket_use", "jukebox_use"}:
            action = str(msg.get("action") or "")
            allowed = {"bucket_use": {"pickup", "pour", "milk", "drink"},
                       "jukebox_use": {"insert", "eject"}}[msg_type]
            if action not in allowed:
                return
            if action != "drink":
                try:
                    x, y, z = int(msg.get("x")), int(msg.get("y")), int(msg.get("z"))
                except (TypeError, ValueError, OverflowError):
                    return
                if not (0 <= y < 256 and max(abs(x), abs(z)) <= _MAX_COORD):
                    return
                with self._lock:
                    dx, dy, dz = x + 0.5 - client.x, y + 0.5 - client.y, z + 0.5 - client.z
                if dx * dx + dy * dy + dz * dz > 8.0 * 8.0:
                    return
            now = time.monotonic()
            if now - client.event_window_start >= 1.0:
                client.event_window_start = now
                client.event_window_count = 0
            client.event_window_count += 1
            limit = 8 if msg_type == "bucket_use" else 6
            if client.event_window_count > limit:
                return
            request = {
                "t": f"{msg_type}_request", "id": client.player_id,
                "action": action,
            }
            if action != "drink":
                request.update({"x": x, "y": y, "z": z})
            self._push(request)
        elif msg_type == "player_attack":
            try:
                target_id = int(msg.get("target_id"))
            except (TypeError, ValueError, OverflowError):
                return
            if not (1 <= target_id <= 2**31 - 1) or target_id == client.player_id:
                return
            with self._lock:
                attacker_alive = float(client.player_state.get("health", 20.0)) > 0.0
            if attacker_alive:
                self._push({
                    "t": "player_attack_request",
                    "id": client.player_id, "target_id": target_id,
                })
        elif msg_type == "item_spawn":
            state = P.normalize_entity_state(msg.get("entity"))
            if state is None or state.get("kind") != "item":
                return
            with self._lock:
                dx, dy, dz = state["x"] - client.x, state["y"] - client.y, state["z"] - client.z
            if dx * dx + dz * dz > 8.0 * 8.0 or abs(dy) > 8.0:
                return
            self._push({"t": "item_spawn_request", "id": client.player_id, "entity": state})
        elif msg_type == "entity_spawn":
            packet = P.entity_spawn_request(msg.get("entity"))
            if packet is None:
                return
            state = packet["entity"]
            with self._lock:
                dx, dy, dz = state["x"] - client.x, state["y"] - client.y, state["z"] - client.z
            if dx * dx + dy * dy + dz * dz > 8.0 * 8.0:
                return
            now = time.monotonic()
            if now - client.event_window_start >= 1.0:
                client.event_window_start = now
                client.event_window_count = 0
            client.event_window_count += 1
            if client.event_window_count > 20:
                return
            self._push({"t": "entity_spawn_request", "id": client.player_id,
                        "entity": state})
        elif msg_type == "fishing_action":
            action = str(msg.get("action") or "")
            if action not in {"cast", "reel"}:
                return
            try:
                entity_id = int(msg.get("entity_id", 0))
            except (TypeError, ValueError, OverflowError):
                return
            if (action == "cast" and entity_id != 0) or (
                    action == "reel" and not 1 <= entity_id <= 2**31 - 1):
                return
            with self._lock:
                if float(client.player_state.get("health", 20.0)) <= 0.0:
                    return
            now = time.monotonic()
            if now - client.event_window_start >= 1.0:
                client.event_window_start = now
                client.event_window_count = 0
            client.event_window_count += 1
            if client.event_window_count > 8:
                return
            self._push({
                "t": "fishing_action_request", "id": client.player_id,
                "action": action, "entity_id": entity_id,
            })
        elif msg_type == "item_pickup":
            try:
                entity_id = int(msg.get("entity_id"))
            except (TypeError, ValueError, OverflowError):
                return
            if not (1 <= entity_id <= 2**31 - 1):
                return
            self._push({"t": "item_pickup_request", "id": client.player_id, "entity_id": entity_id})
        elif msg_type == "respawn":
            with self._lock:
                is_dead = float(client.player_state.get("health", 20.0)) <= 0.0
                already_pending = client.respawn_pending
                if is_dead and not already_pending:
                    client.respawn_pending = True
            if is_dead and not already_pending:
                self._push({"t": "respawn_request", "id": client.player_id})
        elif msg_type == "tnt_ignite_request":
            try:
                x, y, z = int(msg.get("x")), int(msg.get("y")), int(msg.get("z"))
            except (TypeError, ValueError, OverflowError):
                return
            if not (0 <= y < 128 and max(abs(x), abs(z)) <= _MAX_COORD):
                return
            with self._lock:
                dx, dy, dz = x + 0.5 - client.x, y + 0.5 - client.y, z + 0.5 - client.z
            if dx * dx + dy * dy + dz * dz > 6.0 * 6.0:
                return
            self._push({"t": "tnt_ignite_request", "id": client.player_id,
                        "x": x, "y": y, "z": z})
        elif msg_type == "world_event_request":
            packet = P.normalize_world_event(msg, allow_authoritative=False)
            if packet is None:
                return
            now = time.monotonic()
            if now - client.event_window_start >= 1.0:
                client.event_window_start = now
                client.event_window_count = 0
            client.event_window_count += 1
            if client.event_window_count > 40:
                return
            if packet.get("kind") == "sound":
                position = packet.get("position")
                if position is not None:
                    ex, ey, ez = position
                else:
                    ex, ey, ez = client.x, client.y, client.z
            else:
                ex, ey, ez = packet["x"], packet["y"], packet["z"]
            with self._lock:
                dx, dy, dz = ex - client.x, ey - client.y, ez - client.z
            if dx * dx + dy * dy + dz * dz > 64.0 * 64.0:
                return
            packet["source_id"] = client.player_id
            self._broadcast(packet, exclude=client.player_id)
            self._push(packet)
        elif msg_type == "chunk_request":
            try:
                cx, cz = int(msg.get("cx")), int(msg.get("cz"))
            except (TypeError, ValueError, OverflowError):
                return
            max_chunk = (_MAX_COORD // 16) + 2
            if abs(cx) > max_chunk or abs(cz) > max_chunk:
                return
            self._push({"t": "chunk_request", "id": client.player_id, "cx": cx, "cz": cz})
        elif msg_type == "hotbar_select":
            try:
                slot = int(msg.get("slot"))
            except (TypeError, ValueError, OverflowError):
                return
            if not 0 <= slot < 9:
                return
            now = time.monotonic()
            if now - client.event_window_start >= 1.0:
                client.event_window_start = now
                client.event_window_count = 0
            client.event_window_count += 1
            if client.event_window_count > 80:
                return
            self._push({
                "t": "hotbar_select_request", "id": client.player_id,
                "slot": slot,
            })
        elif msg_type == "inventory":
            state = P.normalize_inventory_state(msg)
            try:
                revision = int(msg.get("revision", client.inventory_revision))
            except (TypeError, ValueError, OverflowError):
                return
            with self._lock:
                initial = not client.inventory_initialized
                client.inventory_initialized = True
            self._push({
                "t": "inventory_sync_request", "id": client.player_id,
                "state": state, "initial": initial, "revision": max(0, revision),
            })
        elif msg_type == "inventory_drop":
            try:
                sequence = int(msg.get("seq"))
                slot = int(msg.get("slot"))
                count = int(msg.get("count"))
            except (TypeError, ValueError, OverflowError):
                return
            if sequence <= 0 or not 0 <= slot < (P.INVENTORY_MAIN_SIZE + P.INVENTORY_ARMOR_SIZE) or not 1 <= count <= 64:
                return
            with self._lock:
                if sequence <= client.last_inventory_tx:
                    return
                client.last_inventory_tx = sequence
                now = time.monotonic()
                if now - client.event_window_start >= 1.0:
                    client.event_window_start = now
                    client.event_window_count = 0
                client.event_window_count += 1
                if client.event_window_count > 80:
                    return
            self._push({
                "t": "inventory_drop_request", "id": client.player_id,
                "seq": sequence, "slot": slot, "count": count,
            })
        elif msg_type == "inventory_transaction":
            try:
                sequence = int(msg.get("seq"))
                target = str(msg.get("target") or "")
                slot = int(msg.get("slot"))
                button = int(msg.get("button"))
                if target in {"chest", "furnace", "dispenser"}:
                    x, y, z = int(msg.get("x")), int(msg.get("y")), int(msg.get("z"))
                elif target == "minecart":
                    entity_id = int(msg.get("entity_id"))
                else:
                    x = y = z = 0
            except (TypeError, ValueError, OverflowError):
                return
            if (sequence <= 0
                    or target not in {"player", "chest", "furnace", "dispenser", "minecart", "craft2", "craft3"}
                    or not 0 <= slot < 64 or button not in (0, 1)):
                return
            if target == "minecart" and not 1 <= entity_id <= 2**31 - 1:
                return
            if target in {"chest", "furnace", "dispenser"} and not (
                -30_000_000 <= x <= 30_000_000 and 0 <= y < 256
                and -30_000_000 <= z <= 30_000_000
            ):
                return
            with self._lock:
                if sequence <= client.last_inventory_tx:
                    return
                client.last_inventory_tx = sequence
                now = time.monotonic()
                if now - client.event_window_start >= 1.0:
                    client.event_window_start = now
                    client.event_window_count = 0
                client.event_window_count += 1
                if client.event_window_count > 80:
                    return
                if target in {"chest", "furnace", "dispenser"}:
                    dx, dy, dz = x + 0.5 - client.x, y + 0.5 - client.y, z + 0.5 - client.z
                    if dx * dx + dy * dy + dz * dz > 8.0 * 8.0:
                        return
            event = {
                "t": "inventory_transaction_request", "id": client.player_id,
                "seq": sequence, "target": target, "slot": slot, "button": button,
            }
            if target in {"chest", "furnace", "dispenser"}:
                event.update({"x": x, "y": y, "z": z})
            elif target == "minecart":
                event["entity_id"] = entity_id
            self._push(event)
        elif msg_type == "anim":
            action = str(msg.get("action") or "swing")
            if action not in {"swing"}:
                return
            event = P.animation_packet(client.player_id, action)
            self._broadcast(event, exclude=client.player_id)
            self._push(event)
        elif msg_type == "break":
            target = None
            progress = 0.0
            if bool(msg.get("active", False)):
                try:
                    x, y, z = int(msg.get("x")), int(msg.get("y")), int(msg.get("z"))
                    progress = _number(msg.get("progress"), 0.0)
                except (TypeError, ValueError, OverflowError):
                    return
                if not (0 <= y < 128 and max(abs(x), abs(z)) <= _MAX_COORD):
                    return
                with self._lock:
                    dx, dy, dz = x - client.x, y - client.y, z - client.z
                if dx * dx + dz * dz > 20.0 * 20.0 or abs(dy) > 12.0:
                    return
                target = (x, y, z)
                progress = max(0.0, min(0.99, progress))
            event = P.break_progress_packet(client.player_id, target, progress)
            normalized_target = (event["x"], event["y"], event["z"]) if event.get("active") else None
            with self._lock:
                client.break_target = normalized_target
                client.break_progress = float(event.get("progress", 0.0))
            self._broadcast(event, exclude=client.player_id)
            self._push(event)
        elif msg_type == "disconnect":
            client.alive = False

    def kick_player(self, name: str, reason: str = "Kicked by host") -> bool:
        wanted = str(name or "").strip().casefold()
        if not wanted:
            return False
        with self._lock:
            client = next((
                peer for peer in self._clients.values()
                if peer.player_id > 0 and peer.alive and peer.name.casefold() == wanted
            ), None)
            if client is None:
                return False
            client.send(P.kick(reason))
            client.close_after_flush = True
            return True

    def snapshot_players(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "id": c.player_id, "name": c.name,
                    "x": c.x, "y": c.y, "z": c.z,
                    "yaw": c.yaw, "pitch": c.pitch,
                    **c.player_state,
                    "inventory": c.inventory_state,
                    "break_target": c.break_target,
                    "break_progress": c.break_progress,
                }
                for c in self._clients.values()
                if c.player_id > 0 and c.alive
            ]

    def _drop(self, client: ClientConn) -> None:
        bed_updates = []
        with self._lock:
            for key, value in list(self._clients.items()):
                if value is client:
                    del self._clients[key]
                    break
            for key, owner in list(self._bed_claims.items()):
                if owner == int(client.player_id):
                    del self._bed_claims[key]
                    bed_updates.append((key, False, int(client.player_id)))
        try:
            client.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            client.sock.close()
        except OSError:
            pass
        client.alive = False
        self._publish_bed_occupancy_updates(bed_updates)
        if client.player_id > 0:
            self._push({
                "t": "profile_update", "name": client.name,
                "x": client.x, "y": client.y, "z": client.z,
                "yaw": client.yaw, "pitch": client.pitch,
                "inventory": client.inventory_state,
            })
            self._broadcast(P.leave(client.player_id))
            self._push({"t": "leave", "id": client.player_id})
            self.on_log(
                f"[MP] {client.name} left id={client.player_id} "
                f"at {client.x:.1f},{client.y:.1f},{client.z:.1f}"
            )
