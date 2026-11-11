
from __future__ import annotations

import argparse
import os
import queue
import signal
import sys
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

from game.app.paths import server_data_root, server_saves_root
from game.multiplayer.netutils import format_host_port


class ServerAnchorPlayer:

    EYE_HEIGHT = 1.62

    def __init__(self, position):
        self.pos = [float(value) for value in position[:3]]
        self.yaw = 0.0
        self.pitch = 0.0
        self.health = 20
        self.hurt_time = 0
        self.max_hurt_time = 10
        self.death_time = 0
        self._fire_ticks = 0
        self.render_yaw_offset = 0.0
        self.attacked_at_yaw = 0.0
        self.riding_entity = None
        self.inventory = SimpleNamespace(
            main=[None] * 36, armor=[None] * 4, current_item=0,
        )
        self._digging = False
        self._dig_target = None
        self._destroy_progress = 0.0
        self.dead = False
        self.size = (0.6, 1.8, 0.6)
        self.vel = [0.0, 0.0, 0.0]


class DedicatedServerGame:

    def __init__(self, world, name: str, data_root: Path):
        from game.multiplayer.session import MultiplayerSession

        self.world = world
        self.player = ServerAnchorPlayer((world.spawn_x + 0.5, world.spawn_y, world.spawn_z + 0.5))
        self.world.player = None
        self.renderer = None
        self.settings = SimpleNamespace(username=str(name)[:16] or "Server")
        self.user_data_root = Path(data_root)
        self.running = True
        self._dedicated_server_mode = True
        self._mp_suppress_world_events = False
        self._autosave_enabled = True
        self._ban_list = set()
        self._op_list = set()
        self._chat_lock = threading.Lock()
        self.mp_session = MultiplayerSession(self)

    def add_chat_message(self, message: str) -> None:
        with self._chat_lock:
            stamp = time.strftime("%H:%M:%S")
            print(f"[{stamp}] {message}", flush=True)

    def play_sound(self, *args, **kwargs) -> None:
        return None

    def save_world(self) -> bool:
        success = bool(self.world.save_world(None))
        session_config = getattr(self.mp_session, "server_config", None)
        if session_config is not None:
            success = bool(session_config.save()) and success
        print("World saved" if success else "World save failed", flush=True)
        return success

    def stop(self) -> None:
        self.running = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dedicated_server.py",
        description="Run a headless Minecraft Python multiplayer server.",
    )
    parser.add_argument("--world", help="World name under the server saves directory, or an explicit directory")
    parser.add_argument(
        "--saves-dir",
        help="Directory containing server worlds (default: server_saves next to the server; "
             "separate from the singleplayer 'saves' folder)",
    )
    parser.add_argument("--create", action="store_true", help="Create the world directory if it does not exist")
    parser.add_argument("--seed", type=int, help="World seed when creating a new world")
    parser.add_argument("--name", default="Server", help="Server identity shown in logs (default: Server)")
    parser.add_argument("--port", type=int, default=25565, help="TCP port (default: 25565)")
    parser.add_argument("--view-distance", type=int, default=5, help="Server simulation/view radius (2–16 chunks)")
    parser.add_argument("--initial-radius", type=int, default=2, help="Initial chunk radius loaded during startup")
    parser.add_argument("--list-worlds", action="store_true", help="List saved worlds and exit")
    parser.add_argument("--no-console", action="store_true", help="Disable interactive stdin commands")
    parser.add_argument("--autosave-interval", type=float, default=300.0, help="Autosave interval in seconds; 0 disables autosave")
    return parser


def resolve_world_path(value: str | Path, saves_root: str | Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    if str(value).startswith(("./", ".\\", "../", "..\\")) or (raw.parts and raw.parts[0] == ".."):
        return raw.resolve()
    return (Path(saves_root) / raw).resolve()


def resolve_saves_root(args) -> Path:
    override = getattr(args, "saves_dir", None)
    if override:
        return Path(override).expanduser().resolve()
    return server_saves_root()


def list_saved_worlds(saves_root: str | Path) -> list[Path]:
    root = Path(saves_root)
    if not root.is_dir():
        return []
    return sorted(
        (path for path in root.iterdir() if path.is_dir() and (
            (path / "level.dat").is_file() or (path / "level.dat_old").is_file()
        )),
        key=lambda path: path.name.casefold(),
    )


def _load_world(world_path: Path, args):
    from game.world import World
    from game.entities.entity import Entity

    progress_start = time.monotonic()

    def report(_fraction, status):
        status = str(status)
        if status and status.strip().casefold() != "ready":
            print(status, flush=True)

    world = World(
        renderer=None,
        seed=args.seed,
        view_distance=max(2, min(16, int(args.view_distance))),
        initial_radius=max(1, min(4, int(args.initial_radius))),
        chunks_per_update=2,
        mesh_builds_per_frame=1,
        progress_callback=report,
        save_dir=str(world_path),
        headless=True,
    )
    Entity.world = world
    world.player = None
    world.sound_player = None
    world._mp_remote_players = ()
    world._mp_remote_player_positions = []
    world._mp_chunk_stream_keys = set()
    spawn = world.find_spawn_position()
    world._dedicated_spawn = tuple(float(value) for value in spawn)
    print(f"World ready: seed={world.seed}, spawn={world._dedicated_spawn}, startup={time.monotonic() - progress_start:.1f}s", flush=True)
    return world


def _console_worker(command_queue: queue.Queue, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            line = sys.stdin.readline()
        except (OSError, ValueError):
            return
        if line == "":
            return
        command_queue.put(line.rstrip("\r\n"))


def _dispatch_console_command(game, text: str) -> None:
    text = str(text or "").strip()
    if not text:
        return
    if not text.startswith("/"):
        game.add_chat_message("Console commands begin with '/'. Use /help.")
        return
    from game.multiplayer.commands import CommandContext, dispatch

    def reply(message: str) -> None:
        game.add_chat_message(message)

    context = CommandContext(
        game, None, game.world, game.settings.username, reply,
        session=game.mp_session, sender_id=0, is_operator=True, is_host=True,
    )
    dispatch(text, context)


def run_server(args) -> int:
    data_root = server_data_root()
    saves_root = resolve_saves_root(args)
    if not args.world:
        print("Error: --world is required. Use --list-worlds to see saved worlds.", file=sys.stderr)
        return 2

    world_path = resolve_world_path(args.world, saves_root)
    level_exists = (world_path / "level.dat").is_file() or (world_path / "level.dat_old").is_file()
    if not world_path.exists() and not args.create:
        print(f"Error: world directory does not exist: {world_path}\nUse --create to create it.", file=sys.stderr)
        return 2
    if world_path.exists() and not level_exists and not args.create:
        print(f"Error: no level.dat found in {world_path}. Use --create for a new world.", file=sys.stderr)
        return 2
    if args.port < 1 or args.port > 65535:
        print("Error: --port must be between 1 and 65535", file=sys.stderr)
        return 2
    if args.autosave_interval < 0:
        print("Error: --autosave-interval cannot be negative", file=sys.stderr)
        return 2

    world_path.mkdir(parents=True, exist_ok=True)
    print(f"Saves directory: {saves_root}", flush=True)
    try:
        from game.world.savegame import convert_world_to_mcregion, world_needs_conversion
        if world_needs_conversion(world_path):
            print("Converting world to McRegion...", flush=True)
            last_percent = [-10]

            def report_conversion(done, total):
                percent = int(100 * done / total) if total else 100
                if percent >= last_percent[0] + 10 or done == total:
                    last_percent[0] = percent
                    print(f"Converting world... {percent}%", flush=True)

            convert_world_to_mcregion(world_path, report_conversion)
    except Exception as exc:
        print(f"Could not convert world: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    try:
        world = _load_world(world_path, args)
    except Exception as exc:
        print(f"Could not load world: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

    game = DedicatedServerGame(world, args.name, data_root)
    game.player.pos[:] = list(getattr(world, "_dedicated_spawn", game.player.pos))
    if not game.mp_session.start_host(port=args.port):
        world.shutdown(wait=True)
        print("Could not start the dedicated multiplayer server.", file=sys.stderr)
        return 1

    server = game.mp_session.server
    print("\nMinecraft Python dedicated server", flush=True)
    print(f"World: {world_path}", flush=True)
    print(f"Listening: {format_host_port(server.bind_host if server else '0.0.0.0', server.bound_port if server else args.port)}", flush=True)
    print(f"Protocol: {__import__('game.multiplayer.protocol', fromlist=['PROTO_VERSION']).PROTO_VERSION}", flush=True)
    print("Commands: /help, /stop. Press Ctrl+C to shut down.", flush=True)

    command_queue: queue.Queue[str] = queue.Queue()
    console_stop = threading.Event()
    console_thread = None
    if not args.no_console and sys.stdin is not None and sys.stdin.isatty():
        console_thread = threading.Thread(
            target=_console_worker, args=(command_queue, console_stop),
            name="server-console", daemon=True,
        )
        console_thread.start()

    last_tick = time.monotonic()
    next_tick = last_tick
    last_save = last_tick
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: game.stop())
    tick_interval = 0.05
    try:
        while game.running:
            now = time.monotonic()
            dt = max(0.0, min(now - last_tick, 0.25))
            last_tick = now

            while True:
                try:
                    command = command_queue.get_nowait()
                except queue.Empty:
                    break
                _dispatch_console_command(game, command)

            world.update(game.player.pos, dt)
            world.update_entities(dt)
            game.mp_session.update(dt)

            current_server = game.mp_session.server
            if current_server is not None:
                for snapshot in current_server.snapshot_players():
                    position = (snapshot["x"], snapshot["y"], snapshot["z"])
                    world.update(position, 0.0)

            if game._autosave_enabled and args.autosave_interval > 0 and now - last_save >= args.autosave_interval:
                last_save = now
                game.add_chat_message("Autosaving world...")
                game.save_world()

            next_tick += tick_interval
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(min(sleep_for, 0.05))
            elif sleep_for < -0.5:
                next_tick = time.monotonic()
    except KeyboardInterrupt:
        pass
    finally:
        console_stop.set()
        game.mp_session.stop()
        game.save_world()
        world.shutdown(wait=True)
        if world.save_store is not None:
            world.save_store.close(wait_for_completion=True)
        Entity = __import__("game.entities.entity", fromlist=["Entity"]).Entity
        Entity.world = None
        signal.signal(signal.SIGTERM, previous_sigterm)
        print("Dedicated server stopped.", flush=True)
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(sys, "frozen", False):
        if args.saves_dir:
            args.saves_dir = str(Path(args.saves_dir).expanduser().resolve())
        if args.world and str(args.world).startswith(("./", ".\\", "../", "..\\")):
            args.world = str(Path(args.world).resolve())
        os.chdir(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    if args.list_worlds:
        saves_root = resolve_saves_root(args)
        worlds = list_saved_worlds(saves_root)
        if not worlds:
            print(f"No saved worlds found in {saves_root}.")
        else:
            for world in worlds:
                print(world.name)
        return 0
    return run_server(args)


if __name__ == "__main__":
    raise SystemExit(main())
