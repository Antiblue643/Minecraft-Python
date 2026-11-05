
from __future__ import annotations

import select
import socket
import threading
from typing import Callable, Optional

from game.multiplayer import protocol as P
from game.multiplayer.netutils import format_host_port, strip_host_brackets


class MultiplayerClient:
    def __init__(
        self,
        host: str,
        port: int = P.DEFAULT_PORT,
        name: str = "Player",
        on_log: Optional[Callable[[str], None]] = None,
    ):
        self.host = strip_host_brackets(host)
        self.port = int(port)
        self.name = str(name)[:16] or "Player"
        self.on_log = on_log or (lambda _m: None)

        self.sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._intentional_disconnect = threading.Event()
        self._terminal_packet = threading.Event()
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._events: list[dict] = []
        self._buf = b""
        self._outbuf = bytearray()
        self.connected = False
        self.player_id = 0
        self.inventory_revision = 0
        self.inventory_send_revision = 0
        self.seed: Optional[int] = None
        self.is_operator = False
        self.spawn = (0.0, 64.0, 0.0)
        self.error: Optional[str] = None

    def connect(self, timeout: float = 8.0) -> bool:
        if self.connected:
            return True
        try:
            sock = socket.create_connection((self.host, self.port), timeout=timeout)
            sock.setblocking(False)
        except OSError as exc:
            self.error = str(exc)
            self.on_log(f"Connect failed to {format_host_port(self.host, self.port)}: {exc}")
            return False
        self.sock = sock
        self.connected = True
        self._stop.clear()
        self._intentional_disconnect.clear()
        self._terminal_packet.clear()
        self._buf = b""
        with self._send_lock:
            self._outbuf.clear()
        self.send(P.hello(self.name))
        self._thread = threading.Thread(target=self._run, name="mp-client", daemon=True)
        self._thread.start()
        self.on_log(f"Connected to {format_host_port(self.host, self.port)}")
        return True

    def disconnect(self) -> None:
        self._intentional_disconnect.set()
        self._stop.set()
        was_connected = bool(self.connected)
        self.connected = False
        sock, self.sock = self.sock, None
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if sock is not None:
            if was_connected:
                try:
                    sock.settimeout(0.2)
                    sock.sendall(P.encode(P.disconnect("Client disconnected")))
                except OSError:
                    pass
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        with self._send_lock:
            self._outbuf.clear()

    def send(self, msg: dict) -> None:
        if self.sock is None or not self.connected:
            return
        try:
            data = P.encode(msg)
        except (TypeError, ValueError, OverflowError):
            return
        with self._send_lock:
            if len(self._outbuf) + len(data) > P.MAX_PENDING_BYTES:
                self.error = "Outgoing message queue full"
                self.connected = False
                return
            self._outbuf.extend(data)

    def _has_pending(self) -> bool:
        with self._send_lock:
            return bool(self._outbuf)

    def _flush(self, sock: socket.socket) -> None:
        with self._send_lock:
            if not self._outbuf:
                return
            try:
                sent = sock.send(self._outbuf)
            except BlockingIOError:
                return
            except OSError:
                self.connected = False
                return
            if sent <= 0:
                self.connected = False
                return
            del self._outbuf[:sent]

    def send_pos(self, x: float, y: float, z: float, yaw: float, pitch: float, state=None) -> None:
        if self.player_id <= 0:
            return
        self.send(P.pos(self.player_id, x, y, z, yaw, pitch, state=state))

    def send_chat(self, text: str) -> None:
        self.send({"t": "chat", "msg": str(text)[:200]})

    def send_command(self, text: str) -> None:
        self.send(P.command_request(text))

    def send_block_update(self, x: int, y: int, z: int, block_type, meta: int = 0) -> None:
        self.send(P.block_update(x, y, z, block_type, meta))

    def send_tile_entity(self, state: dict) -> None:
        if self.player_id <= 0:
            return
        normalized = P.normalize_tile_entity_state(state)
        if normalized is not None:
            self.send({"t": "tile_entity", **normalized})

    def request_chunk(self, cx: int, cz: int) -> None:
        if self.player_id > 0:
            self.send(P.chunk_request(cx, cz))

    def send_inventory(self, state: dict, revision: int | None = None) -> None:
        if self.player_id > 0:
            base_revision = (
                self.inventory_send_revision if revision is None else int(revision)
            )
            self.send(P.inventory_packet(self.player_id, state, base_revision))
            self.inventory_send_revision = max(self.inventory_send_revision, base_revision + 1)

    def send_inventory_transaction(
        self, sequence: int, target: str, slot: int, button: int, *, position=None,
        entity_id=None,
    ) -> None:
        if self.player_id <= 0:
            return
        try:
            packet = P.inventory_transaction_request(
                sequence, target, slot, button, position=position,
                entity_id=entity_id,
            )
        except (TypeError, ValueError, OverflowError):
            return
        self.send(packet)

    def send_inventory_drop(self, sequence: int, slot: int, count: int) -> None:
        if self.player_id <= 0:
            return
        try:
            packet = P.inventory_drop_request(sequence, slot, count)
        except (TypeError, ValueError, OverflowError):
            return
        self.send(packet)

    def send_animation(self, action: str = "swing") -> None:
        if self.player_id > 0:
            self.send(P.animation_packet(self.player_id, action))

    def send_break_progress(self, target, progress: float = 0.0) -> None:
        if self.player_id > 0:
            self.send(P.break_progress_packet(self.player_id, target, progress))

    def send_attack(self, entity_id: int, damage: int) -> None:
        if self.player_id > 0:
            try:
                self.send(P.attack_request(entity_id, damage))
            except (TypeError, ValueError, OverflowError):
                return

    def send_entity_interact(self, entity_id: int, action: str) -> None:
        if self.player_id > 0:
            try:
                self.send(P.entity_interact_request(entity_id, action))
            except (TypeError, ValueError, OverflowError):
                return

    def send_entity_push(self, entity_id: int) -> None:
        if self.player_id > 0:
            try:
                self.send(P.entity_push_request(entity_id))
            except (TypeError, ValueError, OverflowError):
                return

    def send_bucket_use(self, action: str, x: int, y: int, z: int) -> None:
        if self.player_id <= 0:
            return
        try:
            self.send(P.bucket_use_request(action, x, y, z))
        except (TypeError, ValueError, OverflowError):
            return

    def send_jukebox_use(self, action: str, x: int, y: int, z: int) -> None:
        if self.player_id <= 0:
            return
        try:
            self.send(P.jukebox_use_request(action, x, y, z))
        except (TypeError, ValueError, OverflowError):
            return

    def send_boat_control(
        self, entity_id: int, riding: bool, forward: float = 0.0,
        strafe: float = 0.0, yaw: float = 0.0,
    ) -> None:
        if self.player_id <= 0:
            return
        try:
            self.send(P.boat_control_request(
                entity_id, riding, forward=forward, strafe=strafe, yaw=yaw,
            ))
        except (TypeError, ValueError, OverflowError):
            return

    def send_player_attack(self, target_player_id: int) -> None:
        if self.player_id > 0:
            try:
                self.send(P.player_attack_request(target_player_id))
            except (TypeError, ValueError, OverflowError):
                return

    def send_world_event(self, event: dict) -> None:
        if self.player_id <= 0:
            return
        normalized = P.normalize_world_event(event, allow_authoritative=False)
        if normalized is None:
            return
        self.send({**normalized, "t": "world_event_request"})

    def send_item_spawn(self, entity_state: dict) -> None:
        if self.player_id > 0:
            self.send({"t": "item_spawn", "entity": entity_state})

    def send_entity_spawn(self, entity_state: dict) -> None:
        if self.player_id <= 0:
            return
        packet = P.entity_spawn_request(entity_state)
        if packet is not None:
            self.send(packet)

    def send_fishing_action(self, action: str, *, entity_id: int = 0) -> None:
        if self.player_id <= 0 or action not in {"cast", "reel"}:
            return
        try:
            entity_id = int(entity_id)
        except (TypeError, ValueError, OverflowError):
            return
        if action == "cast":
            entity_id = 0
        elif not 1 <= entity_id <= 2**31 - 1:
            return
        self.send({"t": "fishing_action", "action": action, "entity_id": entity_id})

    def send_item_pickup(self, entity_id: int) -> None:
        if self.player_id > 0:
            self.send({"t": "item_pickup", "entity_id": int(entity_id)})

    def send_tnt_ignite(self, x: int, y: int, z: int) -> None:
        if self.player_id > 0:
            self.send(P.tnt_ignite_request(x, y, z))


    def request_respawn(self) -> None:
        if self.player_id > 0:
            self.send(P.respawn_request())

    def poll_events(self) -> list[dict]:
        with self._lock:
            events, self._events = self._events, []
        return events

    def _push(self, event: dict) -> None:
        with self._lock:
            self._events.append(event)

    def _run(self) -> None:
        sock = self.sock
        if sock is None:
            return
        while not self._stop.is_set() and self.connected:
            writers = [sock] if self._has_pending() else []
            try:
                readable, writable, _ = select.select([sock], writers, [], 0.05)
            except (OSError, ValueError):
                break
            if sock in writable:
                self._flush(sock)
            if sock not in readable or not self.connected:
                continue
            try:
                data = sock.recv(4096)
            except BlockingIOError:
                continue
            except OSError:
                break
            if not data:
                break
            self._buf += data
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                if len(line) > P.MAX_LINE:
                    self.error = "Message too large"
                    self.connected = False
                    break
                msg = P.decode_line(line)
                if msg is not None:
                    self._on_message(msg)
                if not self.connected:
                    break
            if len(self._buf) > P.MAX_LINE:
                self.error = "Message too large"
                self.connected = False
        self.connected = False
        if not self._intentional_disconnect.is_set() and not self._terminal_packet.is_set():
            self._push({"t": "disconnect", "reason": self.error or "Connection closed"})

    def _on_message(self, msg: dict) -> None:
        msg_type = msg.get("t")
        if msg_type == "welcome":
            try:
                player_id = int(msg.get("id") or 0)
                seed = int(msg.get("seed") or 0)
                spawn = (
                    float(msg.get("x", 0.0)),
                    float(msg.get("y", 64.0)),
                    float(msg.get("z", 0.0)),
                )
            except (TypeError, ValueError, OverflowError):
                self.error = "Invalid welcome packet"
                self.connected = False
                self._terminal_packet.set()
                self._push({"t": "disconnect", "reason": self.error})
                return
            self.player_id = player_id
            self.seed = seed
            self.is_operator = bool(msg.get("op", False))
            self.spawn = spawn
            self._push(msg)
        elif msg_type == "op_status":
            self.is_operator = bool(msg.get("op", False))
        elif msg_type == "kick":
            self.error = str(msg.get("reason") or "Kicked")[:200]
            self.connected = False
            self._terminal_packet.set()
            self._push(msg)
        elif msg_type == "disconnect":
            self.error = str(msg.get("reason") or "Disconnected")[:200]
            self.connected = False
            self._terminal_packet.set()
            self._push({"t": "disconnect", "reason": self.error})
        elif msg_type in (
            "join", "leave", "pos", "chat", "block", "tile_entity", "inventory", "anim", "break",
            "chunk", "chunk_error", "entity_sync", "player_damage", "respawn", "world_event",
            "private_message", "command_reply", "teleport",
        ):
            self._push(msg)
