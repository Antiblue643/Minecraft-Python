from collections import deque
from pathlib import Path
import json
import math
import random
import time

import pygame as pg
from OpenGL.GL import *

from engine.audio import BackgroundMusicPlayer, SoundManager
from game.entities.entity import Entity
from game.entities.item import ItemEntity
from game.entities.player import Player
from game.world import World
from game.world.dimensions import scale_horizontal_coordinates, dimension_save_directory
from game.gameplay.gui import Gui
from game.gameplay.screens import (
    GuiMainMenu, GuiDisconnected, GuiCrafting,
    GuiChest, GuiFurnace, GuiDispenser, GuiEditSign, GuiGameOver, draw_centered_string, draw_dirt_background,
)
from game.gameplay.settings import GameSettings
from game.gameplay.performance import PerformanceEventLog
from game.gameplay.gpu_timer_queries import GpuTimerQueryProfiler
from game.app.paths import user_data_root
from game.app.runtime import screen, rndr
from game.app.scaling import gui_scale, scaled_dimensions, to_gui_coordinates
from game.app.input import GameInputMixin
from game.app.performance import GamePerformanceMixin
from game.app.loop import GameLoopMixin
from game.entities import player_model


class Game(GameInputMixin, GamePerformanceMixin, GameLoopMixin):

    def __init__(self, username=None):
        self.display = screen
        self.renderer = rndr
        self.user_data_root = user_data_root()
        self.user_data_root.mkdir(parents=True, exist_ok=True)
        self.settings = GameSettings()
        self._applied_smooth_lighting = bool(self.settings.smooth_lighting)
        from engine.textures import Texture
        from game.app.texturepacks import get_active_texture_pack, set_active_texture_pack
        previous_pack = get_active_texture_pack()
        selected_pack = set_active_texture_pack(
            getattr(self.settings, "texture_pack", "vanilla")
        )
        from game.localization import set_language
        set_language(pack_name=selected_pack)
        if selected_pack != previous_pack:
            Texture.refresh_all_for_active_pack()
            reload_font = getattr(self.display, "reload_bitmap_font", None)
            if callable(reload_font):
                reload_font()
        Texture.set_anaglyph_enabled(self.settings.anaglyph)
        set_anaglyph = getattr(self.display, "set_anaglyph_enabled", None)
        if callable(set_anaglyph):
            set_anaglyph(self.settings.anaglyph)
        if username is not None:
            self.settings.username = username.strip() or "Player"
            self.settings.username_set = True
            self.settings.save()
        player_model.set_skin_username(self.settings.username)
        from game.block import Block
        Block.set_fancy_graphics(self.settings.fancy_graphics)
        self.save_root = self.user_data_root / "saves"
        self._active_save_dir = None
        self._active_world_name = None
        if hasattr(self.display, "set_vsync"):
            self.display.set_vsync(self.settings.limit_framerate)
        Player.settings = self.settings
        self.gui = Gui(screen)
        self.gui._game = self
        self.clock = pg.time.Clock()

        self.running = True
        self.world = None
        self.player = None
        self.third_person_view = False
        self.third_person_front_view = False
        self.spawn_y = 0.0
        self.test_items = []
        self._autosave_elapsed = 0.0
        self._autosave_interval = 300.0  
        self._autosave_enabled = True
        self._autosave_job = None
        self._cave_ambient_ticks = 0

        from game.multiplayer.chat import ChatLog
        self.chat_log = ChatLog(
            measure=lambda t: (
                self.display.string_width(t)
                if hasattr(self.display, "string_width")
                else len(t) * 6
            )
        )
        self._ban_list = set()
        self._op_list = set()
        from game.multiplayer.session import MultiplayerSession
        self.mp_session = MultiplayerSession(self)

        self.current_screen = None
        self._screen_size = None
        self._screen_tick_accum = 0.0
        self._grabbed = False
        self.hide_gui = False
        self.audio = SoundManager("game/sound", pygame_module=pg)
        self.background_music = BackgroundMusicPlayer(
            Path(__file__).resolve().parents[1] / "music", pygame_module=pg,
        )
        ItemEntity.sound_player = self.play_sound
        self.performance_log = PerformanceEventLog(
            self.user_data_root / "logs" / "performance"
        )
        self.gpu_timer_profiler = GpuTimerQueryProfiler()
        self._gc_profile_started = {}
        self._gc_profile_events = deque(maxlen=64)
        self._gc_profile_callback = self._on_gc_profile_event
        self._gc_profile_registered = False
        self._last_frame_stage_metrics = {
            "frame_index": 0,
            "event_handling_ms": 0.0, "update_ms": 0.0, "render_ms": 0.0,
        }
        self._frame_index = 0
        self._last_frame_end_perf = time.perf_counter()

        self.num_keys = {
            pg.K_1: 0, pg.K_2: 1, pg.K_3: 2,
            pg.K_4: 3, pg.K_5: 4, pg.K_6: 5,
            pg.K_7: 6, pg.K_8: 7, pg.K_9: 8,
        }

        from game.app.mods import load_enabled_mods
        load_enabled_mods(self)
        self._draw_startup_loading_screen()
        self.display_gui_screen(GuiMainMenu())


    def _draw_startup_loading_screen(self):
        from game.app.loading_screen import draw_mojang_loading_screen

        draw_mojang_loading_screen(self.display)


    def scaled_resolution(self):
        return scaled_dimensions(self.display.width, self.display.height)


    def to_scaled(self, pos):
        s = gui_scale(self.display.width, self.display.height)
        return to_gui_coordinates(pos[0], pos[1], s)


    def display_gui_screen(self, new_screen):
        if new_screen is not None and bool(getattr(self.player, "in_portal", False)):
            screen_name = type(new_screen).__name__.lower()
            if ("ingamemenu" in screen_name or "pause" in screen_name
                    or "inventory" in screen_name):
                return
        if self.world is None:
            music_player = getattr(self, "background_music", None)
            stop_all = getattr(music_player, "stop_all", None)
            if callable(stop_all):
                stop_all()
        if self.current_screen is not None:
            self.current_screen.on_gui_closed()
        if new_screen is None and self.world is None:
            new_screen = GuiMainMenu()
        self.current_screen = new_screen
        if (
            new_screen is None
            and self.player is not None
            and getattr(self.player, "is_player_sleeping", lambda: False)()
        ):
            try:
                from game.gameplay.screens import GuiSleep
                self.current_screen = GuiSleep()
            except Exception:
                pass
        self._screen_tick_accum = 0.0
        if new_screen is not None:
            pg.key.start_text_input()
            self._init_current_screen()
        else:
            pg.key.stop_text_input()
        self._sync_mouse_grab()


    def _init_current_screen(self):
        _, sw, sh = self.scaled_resolution()
        self.current_screen.set_world_and_resolution(self, sw, sh)
        self._screen_size = (sw, sh)


    def _sync_mouse_grab(self):
        grab = self.world is not None and self.current_screen is None
        if grab == self._grabbed:
            return
        self._grabbed = grab
        pg.mouse.set_visible(not grab)
        pg.event.set_grab(grab)
        if grab:
            pg.mouse.get_rel()

    def add_chat_message(self, text: str) -> None:
        if self.chat_log is not None:
            self.chat_log.add(str(text))

    def send_chat_message(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        from game.multiplayer.commands import CommandContext, dispatch

        name = getattr(self.settings, "username", None) or "Player"
        session = getattr(self, "mp_session", None)

        if text.startswith("/") and session is not None and getattr(session, "mode", None) == "client":
            if not session.send_command(text):
                from game.localization import translate
                self.add_chat_message(translate(
                    "mcpy.chat.couldNotSend",
                ))
            return

        def reply(msg: str) -> None:
            self.add_chat_message(msg)

        ctx = CommandContext(
            self, self.player, self.world, name, reply,
            session=session, sender_id=0, is_operator=True, is_host=True,
        )
        if dispatch(text, ctx):
            return
        from game.localization import translate
        self.add_chat_message(translate(
            "mcpy.chat.playerMessage", name, text,
        ))
        if session is not None and session.active:
            session.send_chat(text)

    def multiplayer_mod_block_reason(self) -> str:
        try:
            from game.app.mods import multiplayer_blocked_by_mods
            blocked, reason = multiplayer_blocked_by_mods()
            return reason if blocked else ""
        except Exception:
            return ""

    def saved_world_dimension(self, world_name: str) -> int:
        marker = self.save_root / world_name / ".mcpy_dimension"
        try:
            dimension = int(marker.read_text(encoding="ascii").strip()) if marker.is_file() else 0
        except (OSError, ValueError):
            return 0
        return dimension if dimension in (0, -1) else 0

    def multiplayer_host_block_reason(self) -> str:
        world = getattr(self, "world", None)
        if world is not None and int(getattr(world, "dimension_id", 0)) != 0:
            from game.localization import translate
            return translate(
                "mcpy.multiplayer.overworldRequired",
            )
        return self.multiplayer_mod_block_reason()

    def start_host_multiplayer(self, port: int = 25565) -> bool:
        if self.mp_session is None:
            return False
        reason = self.multiplayer_host_block_reason()
        if reason:
            return False
        ok = self.mp_session.start_host(port=port)
        if ok:
            self.display_gui_screen(None)
        return ok

    def begin_multiplayer_connect(self, host: str, port: int, name: str):
        reason = self.multiplayer_mod_block_reason()
        if reason:
            self.mp_session.stop()
            return None
        client = self.mp_session.start_client(host, port, name)
        if not client.connect():
            self.mp_session.stop()
            return client
        return client

    def join_multiplayer_world(self, welcome: dict) -> None:
        self._is_multiplayer_client_world = True
        seed = int(welcome.get("seed") or 0)
        x = float(welcome.get("x", 0.0))
        y = float(welcome.get("y", 64.0))
        z = float(welcome.get("z", 0.0))
        from game.localization import translate
        self.draw_loading(
            translate("connect.connecting"),
            translate("mcpy.loading.generatingLevel"),
            0.0,
        )
        from game.entities.entity import Entity
        from game.world import World
        from game.entities.player import Player

        self.world = World(
            renderer=self.renderer,
            seed=seed,
            view_distance=self.settings.render_chunks,
            progress_callback=self._world_loading_progress,
            save_dir=None,
            snow_covered=bool(welcome.get("snow_covered", False)),
            debug_world=bool(welcome.get("debug_world", False)),
            accurate_generation=bool(welcome.get("accurate_generation", False)),
        )
        self.world.smooth_lighting_enabled = bool(self.settings.smooth_lighting)
        self.world._ephemeral_multiplayer_world = True
        self.world._multiplayer_client_world = True
        self.world._multiplayer_world = True
        self.world.world_time = max(0, int(welcome.get("world_time", 0) or 0))
        Entity.world = self.world
        self.world.sound_player = self.play_sound
        self.world.record_music_player = self.background_music
        self.player = Player(pos=[x, y, z], gamemode=0, username=self.settings.username)
        self._bind_player(self.player)
        self.player.yaw = float(welcome.get("yaw", 0.0))
        self.player.pitch = float(welcome.get("pitch", 0.0))
        self.player.sound_player = self.play_sound
        self.world.player = self.player
        self.player.workbench_opener = self.open_workbench
        self.player.chest_opener = self.open_chest
        self.player.furnace_opener = self.open_furnace
        self.player.dispenser_opener = self.open_dispenser
        self.player.minecart_chest_opener = self.open_minecart_chest
        self.player.sign_opener = self.open_sign
        self.player.on_death_callback = self.on_player_death
        if self.mp_session is not None:
            self.mp_session.finish_client_join(welcome)
        self.display_gui_screen(None)
        from game.localization import translate
        self.add_chat_message(translate(
            "mcpy.chat.joinedWorld"
        ))

    def on_options_changed(self):
        from game.localization import set_language
        from game.app.texturepacks import get_active_texture_pack
        set_language(pack_name=get_active_texture_pack())
        if hasattr(self.display, "set_vsync"):
            self.display.set_vsync(self.settings.limit_framerate)
        from engine.textures import Texture
        Texture.set_anaglyph_enabled(self.settings.anaglyph)
        set_anaglyph = getattr(self.display, "set_anaglyph_enabled", None)
        if callable(set_anaglyph):
            set_anaglyph(self.settings.anaglyph)
        from game.block import Block
        was_fancy = Block._fancy_graphics
        Block.set_fancy_graphics(self.settings.fancy_graphics)
        smooth_lighting = bool(self.settings.smooth_lighting)
        was_smooth_lighting = getattr(
            self, "_applied_smooth_lighting", smooth_lighting,
        )
        self._applied_smooth_lighting = smooth_lighting
        if self.world is not None:
            self.apply_render_distance()
            self.world.smooth_lighting_enabled = smooth_lighting
            if (was_fancy != self.settings.fancy_graphics
                    or was_smooth_lighting != smooth_lighting):
                for chunk in self.world.chunks.values():
                    if hasattr(chunk, "mark_all_dirty"):
                        chunk.mark_all_dirty()


    def apply_render_distance(self):
        from game.rendering.view_culling import (
            alpha_render_distance_chunk_radius, alpha_render_distance_far_clip,
        )
        index = max(0, min(3, int(self.settings.render_distance)))
        w = self.world
        if w is None:
            return
        w.view_distance = alpha_render_distance_chunk_radius(index)
        self.renderer.near_clip = 0.05
        self.renderer.far_clip = alpha_render_distance_far_clip(index)
        self.renderer.render_distance_index = index
        w._active_view_keys = None
        w._active_view_center = None


    def draw_loading(self, title, status, progress=0.0):
        d = self.display
        d.clear()
        d.setup_2d_projection()
        scale, sw, sh = self.scaled_resolution()
        glPushMatrix()
        glScalef(scale, scale, 1.0)
        draw_dirt_background(sw, sh)
        draw_centered_string(d, title, sw // 2, sh // 2 - 20, 0xFFFFFF)
        draw_centered_string(d, status, sw // 2, sh // 2 + 4, 0xFFFFFF)

        bar_x = sw // 2 - 50
        bar_y = sh // 2 + 15
        fill_w = max(0, min(100, int(round(100.0 * float(progress)))))
        glDisable(GL_TEXTURE_2D)
        glColor4f(0.25, 0.25, 0.25, 1.0)
        glBegin(GL_QUADS)
        glVertex2f(bar_x, bar_y)
        glVertex2f(bar_x + 100, bar_y)
        glVertex2f(bar_x + 100, bar_y + 2)
        glVertex2f(bar_x, bar_y + 2)
        glEnd()
        if fill_w:
            glColor4f(0.25, 0.75, 0.25, 1.0)
            glBegin(GL_QUADS)
            glVertex2f(bar_x, bar_y)
            glVertex2f(bar_x + fill_w, bar_y)
            glVertex2f(bar_x + fill_w, bar_y + 2)
            glVertex2f(bar_x, bar_y + 2)
            glEnd()
        glEnable(GL_TEXTURE_2D)
        glPopMatrix()
        d.update()


    @staticmethod
    def _localized_loading_status(status):
        from game.localization import translate
        text = str(status or "")
        exact = {
            "Reading world data": ("mcpy.loading.readingWorldData", "Reading world data"),
            "Building terrain": ("mcpy.loading.buildingTerrain", "Building terrain"),
            "Ready": ("mcpy.loading.ready", "Ready"),
        }
        if text in exact:
            key, fallback = exact[text]
            return translate(key, default=fallback)
        stages = (
            ("Building terrain", "mcpy.loading.buildingTerrainProgress"),
            ("Placing features", "mcpy.loading.placingFeatures"),
            ("Applying saved blocks", "mcpy.loading.applyingSavedBlocks"),
            ("Lighting terrain", "mcpy.loading.lightingTerrain"),
            ("Preparing chunks", "mcpy.loading.preparingChunks"),
        )
        for prefix, key in stages:
            marker = prefix + " ("
            if text.startswith(marker) and text.endswith(")"):
                values = text[len(marker):-1].split("/", 1)
                if len(values) == 2:
                    return translate(
                        key, values[0], values[1],
                        default=prefix + " (%s/%s)",
                    )
        return text

    def _world_loading_progress(self, progress, status):
        from game.localization import translate
        self.draw_loading(
            translate("mcpy.loading.generatingLevel"),
            self._localized_loading_status(status), progress,
        )


    def _convert_world_with_progress(self, save_dir):
        from game.localization import translate
        from game.world.savegame import convert_world_to_mcregion
        title = translate("mcpy.loading.convertingWorld")
        last_draw = [0.0]

        def report(done, total):
            now = time.monotonic()
            if 0 < done < total and now - last_draw[0] < 0.05:
                return
            last_draw[0] = now
            try:
                pg.event.pump()
            except Exception:
                pass
            fraction = (done / total) if total else 1.0
            status = translate("mcpy.loading.convertingChunks", done, total) if total else ""
            self.draw_loading(title, status, fraction)

        report(0, 0)
        return convert_world_to_mcregion(save_dir, report)

    def start_world(
        self, world_name="World1", *, display_name=None, seed=None,
        debug_world=False, accurate_generation=None,
    ):
        self._is_multiplayer_client_world = False
        world_name = str(world_name)
        reserved_devices = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        device_name = world_name.split(".", 1)[0].rstrip(" .").upper()
        if (not world_name or world_name in {".", ".."} or "/" in world_name
                or "\\" in world_name or any(ord(char) < 32 for char in world_name)
                or any(char in world_name for char in '<>:"|?*')
                or world_name.endswith((".", " ")) or device_name in reserved_devices):
            raise ValueError(f"Invalid world folder name: {world_name!r}")
        save_root = self.save_root.expanduser().resolve()
        unresolved_save_dir = save_root / world_name
        if unresolved_save_dir.is_symlink():
            raise ValueError("World save directory cannot be a symbolic link")
        save_dir = unresolved_save_dir.resolve()
        if save_dir.parent != save_root:
            raise ValueError("World save directory must be directly inside the saves folder")
        from game.localization import translate
        from game.world.savegame import world_needs_conversion
        if save_dir.is_dir() and world_needs_conversion(save_dir):
            self._convert_world_with_progress(save_dir)
        self.draw_loading(
            translate("mcpy.loading.generatingLevel",),
            translate("mcpy.loading.readingSavedWorld"),
            0.0,
        )
        self.save_root.mkdir(parents=True, exist_ok=True)
        self._active_save_dir = save_dir
        self._active_world_name = world_name
        saved_dimension = self.saved_world_dimension(world_name)
        dimension_save_dir = dimension_save_directory(save_dir, saved_dimension)
        print(f"Opening world: {dimension_save_dir} (dimension {saved_dimension})")
        self.world = World(
            renderer=self.renderer,
            seed=seed,
            view_distance=self.settings.render_chunks,
            progress_callback=self._world_loading_progress,
            save_dir=dimension_save_dir,
            debug_world=debug_world,
            accurate_generation=accurate_generation,
            dimension_id=saved_dimension,
        )
        self.world.smooth_lighting_enabled = bool(self.settings.smooth_lighting)
        self.world.level_name = str(
            display_name
            or (self.world._loaded_level_data or {}).get("LevelName", "")
            or world_name.replace("_", " ")
        )
        Entity.world = self.world
        self.world.sound_player = self.play_sound
        self.world.record_music_player = self.background_music
        if self.performance_log.active:
            self.world.profiler.set_event_sink(self.performance_log)
        print(f"World seed: {self.world.seed}")

        level_data = self.world._loaded_level_data
        saved_player = self.world.saved_player_state
        if saved_player is not None:
            player_pos = saved_player.get("pos", [0.0, 64.5, 0.0])
            self.player = Player(
                pos=list(player_pos),
                gamemode=int(saved_player.get("gamemode", 0)),
                username=self.settings.username,
            )
            self.world.save_store.restore_player(self.player, saved_player)
        elif level_data is not None:
            self.player = Player(
                pos=[self.world.spawn_x, self.world.spawn_y - 0.5, self.world.spawn_z],
                gamemode=0,
                username=self.settings.username,
            )
        else:
            spawn_x, spawn_y, spawn_z = self.world.find_spawn_position()
            self.player = Player(
                pos=[spawn_x, spawn_y, spawn_z],
                gamemode=0,
                username=self.settings.username,
            )
            self.world.set_spawn_from_player(self.player)

        self.spawn_pos = (
            float(self.world.spawn_x),
            float(self.world.spawn_y) - 0.5,
            float(self.world.spawn_z),
        )
        self.spawn_pos = self._safe_spawn_position(self.spawn_pos)
        self._bind_player(self.player)
        overworld_spawn_file = save_dir / ".mcpy_overworld_spawn"
        if saved_dimension == 0:
            try:
                temporary = overworld_spawn_file.with_suffix(".tmp")
                temporary.write_text(json.dumps(self.spawn_pos), encoding="utf-8")
                temporary.replace(overworld_spawn_file)
            except OSError:
                pass
        elif overworld_spawn_file.is_file():
            try:
                saved_spawn = json.loads(overworld_spawn_file.read_text(encoding="utf-8"))
                if isinstance(saved_spawn, (list, tuple)) and len(saved_spawn) == 3:
                    self.spawn_pos = tuple(float(value) for value in saved_spawn)
            except (OSError, ValueError, TypeError):
                pass
        else:
            try:
                from game.world.savegame import WorldSave
                overworld_store = WorldSave(save_dir)
                data = overworld_store.level_data or {}
                if "SpawnX" in data and "SpawnY" in data and "SpawnZ" in data:
                    self.spawn_pos = (
                        float(data["SpawnX"]), float(data["SpawnY"]) - 0.5,
                        float(data["SpawnZ"]),
                    )
                overworld_store.close(wait_for_completion=True)
            except Exception:
                pass
        self.spawn_y = self.spawn_pos[1]
        self.player.sound_player = self.play_sound
        self.world.player = self.player   
        self.player.workbench_opener = self.open_workbench
        self.player.chest_opener = self.open_chest
        self.player.furnace_opener = self.open_furnace
        self.player.dispenser_opener = self.open_dispenser
        self.player.minecart_chest_opener = self.open_minecart_chest
        self.player.sign_opener = self.open_sign
        self.player.on_death_callback = self.on_player_death
        self.test_items = []
        self._autosave_elapsed = 0.0
        if self.world.save_store is not None and level_data is None:
            self.world.save_store.save_level(self.world, self.player)
        if self.player.health <= 0:
            self.player._death_handled = True
            self.display_gui_screen(GuiGameOver())
        else:
            self.display_gui_screen(None)


    @staticmethod
    def _portal_arrival_position(world, x, y, z):
        px, pz = float(x), float(z)
        if world.get_block_type(x - 1, y, z) == "portal":
            px -= 0.5
        elif world.get_block_type(x + 1, y, z) == "portal":
            px += 0.5
        elif world.get_block_type(x, y, z - 1) == "portal":
            pz -= 0.5
        elif world.get_block_type(x, y, z + 1) == "portal":
            pz += 0.5
        return (px, float(y) - 0.5, pz)

    @staticmethod
    def _find_portal_site(world, target_x, target_y, target_z):
        from game.block import Block
        from game.block.fire_rules import is_normal_cube

        Block._load_defs()

        def column_loaded(x, z):
            chunk_index = getattr(world, "_get_chunk_index", None)
            chunks = getattr(world, "chunks", None)
            if not isinstance(chunks, dict) or not callable(chunk_index):
                return True
            return chunk_index(x, z) in chunks

        def solid_at(x, y, z):
            if not column_loaded(x, z):
                return False
            block_type = world.get_block_type(x, y, z)
            return is_normal_cube(Block._block_defs.get(block_type))

        def clear_at(x, y, z):
            return column_loaded(x, z) and world.get_block_type(x, y, z) is None

        base_x = math.floor(float(target_x))
        base_z = math.floor(float(target_z))
        best = None
        best_distance = float("inf")

        orientations = (
            ((0, 1), (1, 0)),
            ((1, 0), (0, -1)),
            ((0, -1), (-1, 0)),
            ((-1, 0), (0, 1)),
        )
        for x in range(base_x - 16, base_x + 17):
            for z in range(base_z - 16, base_z + 17):
                y = 127
                while y >= 1 and not clear_at(x, y, z):
                    y -= 1
                if y < 1:
                    continue
                while y > 1 and clear_at(x, y - 1, z):
                    y -= 1

                for width_axis, depth_axis in orientations:
                    wx, wz = width_axis
                    dx, dz = depth_axis
                    valid = True
                    for depth in range(3):
                        for width in range(-1, 3):
                            px = x + width * wx + depth * dx
                            pz = z + width * wz + depth * dz
                            if not solid_at(px, y - 1, pz):
                                valid = False
                                break
                            for dy in range(4):
                                if not clear_at(px, y + dy, pz):
                                    valid = False
                                    break
                            if not valid:
                                break
                        if not valid:
                            break
                    if not valid:
                        continue
                    arrival_x = x + 0.5 * wx
                    arrival_z = z + 0.5 * wz
                    distance = (
                        (arrival_x - float(target_x)) ** 2
                        + (float(y) - 0.5 - float(target_y)) ** 2
                        + (arrival_z - float(target_z)) ** 2
                    )
                    if distance < best_distance:
                        best_distance = distance
                        best = (x, y, z, width_axis, depth_axis)
        return best

    def _find_or_create_dimension_portal(self, world, target_x, target_y, target_z):
        force_chunks = getattr(world, "force_chunks_around", None)
        if callable(force_chunks):
            try:
                force_chunks(target_x, target_z, radius=2)
            except TypeError:
                force_chunks(target_x, target_z)
        chunks = getattr(world, "chunks", {})
        save_store = getattr(world, "save_store", None)
        chunk_exists = getattr(save_store, "chunk_exists", None)
        load_chunk = getattr(save_store, "load_chunk", None)
        if isinstance(chunks, dict) and callable(chunk_exists) and callable(load_chunk):
            from game.content_ids import classic_block_id
            portal_id = classic_block_id("portal")
            min_cx = math.floor((float(target_x) - 128.0 + 0.5) / 16.0)
            max_cx = math.floor((float(target_x) + 128.0 + 0.5) / 16.0)
            min_cz = math.floor((float(target_z) - 128.0 + 0.5) / 16.0)
            max_cz = math.floor((float(target_z) + 128.0 + 0.5) / 16.0)
            saved_candidates = []
            for cx in range(min_cx, max_cx + 1):
                for cz in range(min_cz, max_cz + 1):
                    if (cx, cz) in chunks:
                        continue
                    try:
                        if not chunk_exists(cx, cz):
                            continue
                        saved = load_chunk(cx, cz)
                    except Exception:
                        continue
                    raw_blocks = saved.get("raw_blocks", b"") if saved else b""
                    if raw_blocks and portal_id in raw_blocks:
                        saved_candidates.append((cx, cz, saved))
            for cx, cz, saved in saved_candidates:
                try:
                    chunk = world._generate_chunk(
                        cx, cz, saved_chunk=saved, saved_chunk_loaded=True,
                        save_ready_checked=True,
                    )
                    if chunk is not None and not chunk.light_ready:
                        world._init_chunk_lighting(chunk)
                except Exception as exc:
                    print(f"Could not inspect saved portal chunk {(cx, cz)}: {exc}")

        best = None
        best_distance = float("inf")
        for (cx, cz), chunk in tuple(getattr(world, "chunks", {}).items()):
            blocks = getattr(chunk, "blocks", ())
            depth = int(getattr(chunk, "depth", 16))
            height = int(getattr(chunk, "height", 128))
            start_x = int(getattr(chunk, "start_x", cx * 16))
            start_z = int(getattr(chunk, "start_z", cz * 16))
            for index, block_id in enumerate(blocks):
                if int(block_id) >= len(world.id_to_block_type):
                    continue
                if world.id_to_block_type[int(block_id)] != "portal":
                    continue
                lx = index // (depth * height)
                rem = index % (depth * height)
                lz, y = divmod(rem, height)
                x, z = start_x + lx, start_z + lz
                if abs(x - float(target_x)) > 128 or abs(z - float(target_z)) > 128:
                    continue
                while y > 0 and world.get_block_type(x, y - 1, z) == "portal":
                    y -= 1
                arrival = self._portal_arrival_position(world, x, y, z)
                distance = sum(
                    (arrival[i] - float((target_x, target_y, target_z)[i])) ** 2
                    for i in range(3)
                )
                if distance < best_distance:
                    best_distance = distance
                    best = arrival
        if best is not None:
            return best

        site = self._find_portal_site(world, target_x, target_y, target_z)
        fallback_platform = site is None
        if site is None:
            x = math.floor(float(target_x))
            z = math.floor(float(target_z))
            portal_y = max(70, min(118, int(round(float(target_y)))))
            orientation = (int(getattr(world, "seed", 0)) ^ x ^ (z << 1)) & 3
            orientations = (
                ((0, 1), (1, 0)),
                ((1, 0), (0, -1)),
                ((0, -1), (-1, 0)),
                ((-1, 0), (0, 1)),
            )
            width_axis, depth_axis = orientations[orientation]
        else:
            x, portal_y, z, width_axis, depth_axis = site

        wx, wz = width_axis
        dx, dz = depth_axis
        world._portal_update_depth = int(getattr(world, "_portal_update_depth", 0)) + 1
        try:
            with world.bulk_edit():
                if fallback_platform:
                    for depth_offset in range(3):
                        for width_offset in range(-1, 3):
                            px = x + width_offset * wx + depth_offset * dx
                            pz = z + width_offset * wz + depth_offset * dz
                            world.set_block(px, portal_y - 1, pz, "obsidian", immediate_mesh=False)
                            for dy in range(4):
                                world.set_block(px, portal_y + dy, pz, None, immediate_mesh=False)
                for width_offset in (-1, 0, 1, 2):
                    px = x + width_offset * wx
                    pz = z + width_offset * wz
                    world.set_block(px, portal_y - 1, pz, "obsidian", immediate_mesh=False)
                    world.set_block(px, portal_y + 3, pz, "obsidian", immediate_mesh=False)
                for py in range(portal_y, portal_y + 3):
                    for width_offset in (-1, 2):
                        px = x + width_offset * wx
                        pz = z + width_offset * wz
                        world.set_block(px, py, pz, "obsidian", immediate_mesh=False)
                    for width_offset in (0, 1):
                        px = x + width_offset * wx
                        pz = z + width_offset * wz
                        world.set_block(px, py, pz, "portal", immediate_mesh=False)
        finally:
            world._portal_update_depth = max(0, int(world._portal_update_depth) - 1)
        return (x + 0.5 * wx, portal_y - 0.5, z + 0.5 * wz)

    @staticmethod
    def _reset_player_dimension_interpolation(player, destination_world=None):
        try:
            tick_phase = float(getattr(destination_world, "_tick_accum", 0.0))
        except (TypeError, ValueError):
            tick_phase = 0.0
        tick_phase = min(max(tick_phase, 0.0), 0.25)
        player.prev_pos = list(player.pos)
        for previous, current in (
            ("prev_yaw", "yaw"),
            ("prev_pitch", "pitch"),
            ("prev_limb_swing", "limb_swing"),
            ("prev_limb_swing_amount", "limb_swing_amount"),
            ("prev_distance_walked_modified", "distance_walked_modified"),
            ("prev_camera_yaw", "camera_yaw"),
            ("prev_camera_pitch", "camera_pitch"),
            ("prev_render_yaw_offset", "render_yaw_offset"),
            ("prev_swing_progress", "swing_progress"),
            ("prev_equipped_progress", "equipped_progress"),
        ):
            if hasattr(player, current):
                setattr(player, previous, getattr(player, current))
        player.camera_yaw = player.prev_camera_yaw = 0.0
        player.camera_pitch = player.prev_camera_pitch = 0.0
        player.limb_swing = player.prev_limb_swing = 0.0
        player.limb_swing_amount = player.prev_limb_swing_amount = 0.0
        player.distance_walked_modified = player.prev_distance_walked_modified = 0.0
        player._distance_walked = 0.0
        player._limb_distance_accum = 0.0
        player._anim_accum = tick_phase
        player._anim_tick_accum = tick_phase
        player._survival_tick_accum = tick_phase
        player._death_tick_accum = tick_phase
        player._swing_progress_int = 0
        player._is_swing_in_progress = False
        player.swing_progress = player.prev_swing_progress = 0.0
        player._heading_accel = (0.0, 0.0)
        player._fall_distance = 0.0
        player._fall_pending = 0.0
        player._fall_tick_clock = 0.0
        player.prev_pos = list(player.pos)
        player.sync_camera()

    def _write_active_dimension(self, dimension_id):
        root = getattr(self, "_active_save_dir", None)
        if root is None:
            return
        root.mkdir(parents=True, exist_ok=True)
        marker = root / ".mcpy_dimension"
        temporary = root / ".mcpy_dimension.tmp"
        temporary.write_text(str(int(dimension_id)), encoding="ascii")
        temporary.replace(marker)

    def _travel_through_portal(self, *, for_respawn=False):
        source = self.world
        player = self.player
        if source is None or player is None:
            return False
        if for_respawn:
            if int(getattr(source, "dimension_id", 0)) != -1:
                return False
        else:
            if float(getattr(player, "portal_cooldown", 0.0)) > 0.0:
                return False
            if bool(getattr(player, "portal_requires_exit", False)):
                return False
            if float(getattr(player, "portal_time", 0.0)) < 1.0:
                return False
        session = getattr(self, "mp_session", None)
        if session is not None and getattr(session, "active", False):
            player.portal_time = 0.0
            player.prev_portal_time = 0.0
            player.portal_cooldown = 0.0
            player.portal_requires_exit = True
            return False

        save_root = getattr(self, "_active_save_dir", None)
        if save_root is None:
            return False

        if not for_respawn:
            self.play_sound(
                "portal.travel", volume=1.0,
                pitch=0.8 + random.random() * 0.4,
            )
        destination_dimension = (
            0 if for_respawn else
            (-1 if int(getattr(source, "dimension_id", 0)) == 0 else 0)
        )
        source_x, source_y, source_z = (float(v) for v in player.pos)
        if for_respawn:
            target_x, target_y, target_z = (float(v) for v in self.spawn_pos)
        else:
            target_x, target_z = scale_horizontal_coordinates(
                source_x, source_z, source.dimension_id, destination_dimension,
            )
            target_y = source_y
        target_directory = dimension_save_directory(save_root, destination_dimension)

        autosave = getattr(self, "_autosave_job", None)
        if autosave is not None:
            while not autosave.done:
                autosave.step(2)
            self._autosave_job = None
        if source.save_store is not None:
            if not self.save_world_with_progress(stop_workers=True):
                player.portal_time = 0.0
                return False

        destination = None
        try:
            from game.localization import translate
            dimension_title = translate(
                "mcpy.loading.enteringNether" if destination_dimension == -1
                else "mcpy.loading.enteringOverworld"
            )
            self.draw_loading(
                dimension_title,
                translate("mcpy.loading.buildingTerrain"),
                0.0,
            )
            destination = World(
                renderer=self.renderer,
                seed=source.seed,
                view_distance=self.settings.render_chunks,
                progress_callback=lambda progress, status: self.draw_loading(
                    dimension_title, self._localized_loading_status(status), progress,
                ),
                save_dir=target_directory,
                debug_world=False,
                accurate_generation=getattr(source, "accurate_generation", False),
                dimension_id=destination_dimension,
                initial_position=(target_x, target_y, target_z),
            )
            destination.smooth_lighting_enabled = bool(self.settings.smooth_lighting)
            arrival = (
                (target_x, target_y, target_z) if for_respawn else
                self._find_or_create_dimension_portal(
                    destination, target_x, target_y, target_z,
                )
            )
            player.pos = [float(arrival[0]), float(arrival[1]), float(arrival[2])]
            player.vel = [0.0, 0.0, 0.0]
            player.portal_time = 0.0
            player.prev_portal_time = 0.0
            player.in_portal = False
            player.portal_requires_exit = not for_respawn
            player.portal_cooldown = 0.0
            player.on_ground = not for_respawn
            self._reset_player_dimension_interpolation(player, destination)
            if destination_dimension == -1 and destination._loaded_level_data is None:
                destination.set_spawn_from_player(player)
            destination.player = player
            destination.sound_player = self.play_sound
            destination.record_music_player = self.background_music
            if self.performance_log.active:
                destination.profiler.set_event_sink(self.performance_log)
            if destination.save_store is not None:
                if not destination.save_store.save_level(destination, player):
                    raise RuntimeError("Could not save the destination dimension's player state")
            self._write_active_dimension(destination_dimension)
        except Exception as exc:
            if destination is not None:
                destination.shutdown(wait=True)
                for chunk in list(destination.chunks.values()):
                    chunk.release()
                destination.chunks.clear()
                if destination.save_store is not None:
                    destination.save_store.close(wait_for_completion=True)
            print(f"Could not travel through the portal: {exc}")
            player.portal_time = 0.0
            return False

        source.shutdown(wait=True)
        for chunk in list(source.chunks.values()):
            chunk.release()
        source.chunks.clear()
        if source.save_store is not None:
            source.save_store.close(wait_for_completion=True)
        self.world = destination
        Entity.world = destination
        destination.player = player
        player.sound_player = self.play_sound
        self._autosave_elapsed = 0.0
        self._autosave_job = None
        return True


    def on_player_death(self):
        if self.world is not None and not isinstance(self.current_screen, GuiGameOver):
            self.display_gui_screen(GuiGameOver())


    def respawn_player(self, *, _network_authorized=False, _spawn_override=None):
        if self.player is None or self.world is None:
            return
        session = getattr(self, "mp_session", None)
        if (not _network_authorized and session is not None
                and getattr(session, "mode", None) == "client"
                and getattr(session, "active", False)):
            if session.request_respawn():
                return

        if _spawn_override is None and bool(getattr(self.world, "is_hell_world", False)):
            if not self._travel_through_portal(for_respawn=True):
                return
            spawn = self._safe_spawn_position(self.spawn_pos)
            self.spawn_pos = tuple(spawn)
            self.spawn_y = float(spawn[1])
            self.player.respawn_at(spawn)
            if self.world.save_store is not None:
                self.world.save_store.save_level(self.world, self.player)
            force = getattr(self.world, "force_chunks_around", None)
            if callable(force):
                force(spawn[0], spawn[2])
            self.display_gui_screen(None)
            return

        if _spawn_override is not None:
            spawn = tuple(float(value) for value in _spawn_override[:3])
        else:
            spawn = getattr(self, "spawn_pos", None)
            if spawn is None:
                spawn = self.world.find_spawn_position()
            spawn = self._safe_spawn_position(spawn)
        self.spawn_pos = tuple(spawn)
        self.spawn_y = float(spawn[1])
        self.player.respawn_at(spawn)
        force = getattr(self.world, "force_chunks_around", None)
        if callable(force):
            force(spawn[0], spawn[2])
        self.display_gui_screen(None)

    def _bind_player(self, player):
        if player is None:
            return player
        player.mc = self
        player.game = self
        player.world = self.world
        player.settings = self.settings
        player.sound_player = getattr(self, "sound", None)
        return player

    def _safe_spawn_position(self, position, search_radius=8):

        from game.app.spawn_safety import safe_spawn_position
        return safe_spawn_position(self.player, self.world, position, search_radius)


    def save_world_with_progress(self, stop_workers=True):
        world = self.world
        if getattr(world, "_ephemeral_multiplayer_world", False):
            return True
        job = world.begin_save(self.player, stop_workers=stop_workers)
        if job is None:
            return False
        last_draw = 0.0
        while not job.done:
            job.step(2)
            pg.event.pump()
            now = time.monotonic()
            if job.done or now - last_draw >= 0.02:
                last_draw = now
                from game.localization import translate
                self.draw_loading(
                    translate("mcpy.loading.savingLevel"),
                    translate("mcpy.loading.savingChunks"),
                    job.progress,
                )
            if not job.done:
                time.sleep(0.005)
        return job.success


    def open_workbench(self):
        if self.player is not None:
            self.display_gui_screen(GuiCrafting(self.player))


    def open_chest(self, x, y, z):
        if self.player is None or self.world is None:
            return
        from game.block.tile_entity import get_chest_inventory
        inventory = get_chest_inventory(self.world, x, y, z)
        self.display_gui_screen(GuiChest(self.player, inventory))

    def open_minecart_chest(self, cart):
        if self.player is None or cart is None or getattr(cart, "dead", False):
            return
        self.display_gui_screen(GuiChest(self.player, cart))


    def open_furnace(self, x, y, z):
        if self.player is None or self.world is None:
            return
        te = self.world.get_tile_entity(x, y, z)
        if te is None:
            from game.block.tile_entity import TileEntityFurnace
            te = TileEntityFurnace(self.world, x, y, z)
            self.world.set_tile_entity(x, y, z, te)
        self.display_gui_screen(GuiFurnace(self.player, te))

    def open_dispenser(self, x, y, z):
        if self.player is None or self.world is None:
            return
        te = self.world.get_tile_entity(x, y, z)
        if te is None:
            from game.block.tile_entity import TileEntityDispenser
            te = TileEntityDispenser(self.world, x, y, z)
            self.world.set_tile_entity(x, y, z, te)
        if getattr(te, "SIZE", 0) == 9:
            self.display_gui_screen(GuiDispenser(self.player, te))

    def open_sign(self, x, y, z):
        if self.player is None or self.world is None:
            return
        te = self.world.get_tile_entity(x, y, z)
        if te is None:
            from game.block.tile_entity import TileEntitySign
            te = TileEntitySign(self.world, x, y, z)
            self.world.set_tile_entity(x, y, z, te)
        self.display_gui_screen(GuiEditSign(te))


    def quit_to_title(self, disconnect_reason=None):
        session = getattr(self, "mp_session", None)
        was_client_world = bool(getattr(self, "_is_multiplayer_client_world", False))
        if session is not None and session.active:
            was_client_world = was_client_world or session.mode == "client"
            session.stop()
        if self.world is not None:
            world = self.world
            is_client = was_client_world
            try:
                saved = self.save_world_with_progress(stop_workers=True)
            except Exception as exc:
                if not is_client:
                    print(f"Could not save the world; staying in the world: {exc}")
                    return
                saved = True
            if not saved and not is_client:
                print("Could not save the world; it remains open so you can retry.")
                return
            for chunk in list(world.chunks.values()):
                chunk.release()
            world.chunks.clear()
            self.world = None
            self.player = None
            self._is_multiplayer_client_world = False
            self.test_items = []
            self._autosave_elapsed = 0.0
            self._autosave_job = None
            if world.save_store is not None:
                world.save_store.close(wait_for_completion=True)
            Entity.world = None
        if disconnect_reason is not None or was_client_world:
            reason = disconnect_reason if disconnect_reason is not None else "Disconnected from server"
            self.display_gui_screen(GuiDisconnected(str(reason)[:200]))
        else:
            self.display_gui_screen(GuiMainMenu())


    def play_sound(self, name, volume=1.0, pitch=1.0, position=None):
        listener = None
        if self.player is not None:
            listener = (
                self.player.pos[0],
                self.player.pos[1] + self.player.EYE_HEIGHT,
                self.player.pos[2],
            )
        if not getattr(self, "_mp_suppress_world_events", False):
            session = getattr(self, "mp_session", None)
            if session is not None and session.active:
                event_position = position
                if event_position is None and self.player is not None:
                    event_position = tuple(float(v) for v in self.player.pos[:3])
                session.publish_world_event({
                    "kind": "sound", "name": str(name),
                    "volume": volume, "pitch": pitch,
                    "position": list(event_position) if event_position is not None else None,
                })
        return self.audio.play(
            name,
            volume=volume,
            pitch=pitch,
            position=position,
            listener_position=listener,
            enabled=self.settings.sound,
            master_volume=getattr(self.settings, "sound_volume", 1.0),
        )

    def _update_cave_ambient(self, dt):
        import random
        player = self.player
        world = self.world
        if player is None or world is None:
            return
        if not getattr(self.settings, "sound", True):
            return

        self._cave_ambient_ticks -= max(0.0, float(dt)) * 20.0
        if self._cave_ambient_ticks > 0.0:
            return

        self._cave_ambient_ticks = 6000.0 + random.random() * 12000.0

        try:
            px = float(player.pos[0])
            py = float(player.pos[1]) + float(getattr(player, "EYE_HEIGHT", 1.62))
            pz = float(player.pos[2])
        except Exception:
            return

        offsets = (-4, 0, 4)
        ox = random.choice(offsets)
        oy = random.choice(offsets)
        oz = random.choice(offsets)
        if ox == 0 and oy == 0 and oz == 0:
            return

        bx = int(math.floor(px + ox))
        by = int(math.floor(py + oy))
        bz = int(math.floor(pz + oz))

        try:
            if world.get_block_type(bx, by, bz) is not None:
                return
            sky = int(world._get_saved_light("sky", bx, by, bz))
            block = int(world._get_saved_light("block", bx, by, bz))
        except Exception:
            return

        if sky > 0:
            return
        if block > random.randrange(8):
            return

        dx = (bx + 0.5) - px
        dy = (by + 0.5) - py
        dz = (bz + 0.5) - pz
        if dx * dx + dy * dy + dz * dz <= 4.0:
            return

        pitch = 0.8 + random.random() * 0.2
        self.play_sound(
            "ambient.cave",
            volume=0.7,
            pitch=pitch,
            position=(bx + 0.5, by + 0.5, bz + 0.5),
        )
