import gc
import math
import time
from contextlib import nullcontext

import pygame as pg
from OpenGL.GL import *

from engine.fire_texture import fire_animator
from engine.fluid_texture import fluid_animator
from engine.portal_texture import portal_animator
from engine.compass_texture import compass_animator
from engine.clock_texture import clock_animator
from game.block import Block
from game.gameplay.input import MovementFilteredKeyState
from game.entities.item import Item
from game.entities import player_model
from game.gameplay.screens import GuiContainer, GuiInventory
from game.rendering.view_culling import (
    alpha_scale_fog_color, alpha_sky_fog_color,
)
from game.rendering.overlays import (
    check_gl_error, destroy_overlay_is_entity_pass, draw_block_destroy_progress, draw_block_outline,
    draw_chunk_borders, draw_entity_hitboxes, render_precipitation,
)


class GameLoopMixin:
    @staticmethod
    def _f1_is_held() -> bool:
        try:
            return bool(pg.key.get_pressed()[pg.K_F1])
        except (IndexError, KeyError, TypeError, AttributeError):
            return False

    def _update(self, dt):
        cur = self.current_screen

        if cur is not None:
            _, sw, sh = self.scaled_resolution()
            if (sw, sh) != self._screen_size:
                self._init_current_screen()
            self._screen_tick_accum += min(dt, 0.25)
            while self._screen_tick_accum >= 0.05:
                self._screen_tick_accum -= 0.05
                cur.update_screen()

        world, player = self.world, self.player
        input_integration = getattr(self, "input_integration", None)
        if input_integration is not None:
            input_integration.update(dt)
        music_player = getattr(self, "background_music", None)
        if music_player is not None:
            music_player.master_volume = float(
                getattr(self.settings, "music_volume", 1.0)
            )
            listener_position = tuple(float(v) for v in player.pos) if player is not None else None
            music_player.update(
                dt,
                enabled=bool(getattr(self.settings, "music", True)),
                in_world=world is not None,
                listener_position=listener_position,
            )
        if world is not None:
            session = getattr(self, "mp_session", None)
            multiplayer_active = bool(session is not None and session.active)
            paused = (
                cur is not None
                and cur.does_gui_pause_game()
                and not multiplayer_active
            )
            if not paused:
                keyboard_state = pg.key.get_pressed()
                keys = (
                    input_integration.key_state(keyboard_state, cur is None)
                    if input_integration is not None else keyboard_state
                )
                if cur is not None:
                    movement_bindings = (
                        "key_forward", "key_back", "key_left", "key_right",
                        "key_jump", "key_sneak",
                    )
                    blocked_keycodes = (
                        getattr(getattr(self.settings, binding, None), "key", None)
                        for binding in movement_bindings
                    )
                    keys = MovementFilteredKeyState(keys, blocked_keycodes)
                old_world_time = int(getattr(world, "world_time", 0))
                world.update(player.pos, dt)
                self.renderer.render_distance_index = max(
                    0, min(3, int(getattr(self.settings, "render_distance", 1))),
                )
                fog_ticks = max(
                    0, int(getattr(world, "world_time", old_world_time)) - old_world_time,
                )
                for _ in range(min(fog_ticks, 4)):
                    self.renderer.update_alpha_fog_brightness(world, player)
                player.update(keys, dt)
                world.update_entities(dt)
                self._update_cave_ambient(dt)
                for item in self.test_items:
                    item.update(dt)
                if player.gamemode == 1 and player.pos[1] <= -100:
                    spawn = getattr(self, "spawn_pos", (0.0, self.spawn_y, 0.0))
                    resolver = getattr(self, "_safe_spawn_position", None)
                    if callable(resolver):
                        spawn = resolver(spawn)
                        self.spawn_pos = spawn
                    player.respawn_at(spawn)
                if (float(getattr(player, "portal_time", 0.0)) >= 1.0
                        and float(getattr(player, "portal_cooldown", 0.0)) <= 0.0
                        and not bool(getattr(player, "portal_requires_exit", False))):
                    travel = getattr(self, "_travel_through_portal", None)
                    if callable(travel) and travel():
                        world, player = self.world, self.player

            from game.app.mods import dispatch_mod_hook
            dispatch_mod_hook("on_world_tick", self, world, player, dt)

            if session is not None and session.active:
                session.update(dt, paused=paused)

            if self._grabbed:
                mrx, mry = pg.mouse.get_rel()
                if self.settings.invert_mouse:
                    mry = -mry
                mouse_scale = float(getattr(self.settings, "mouse_sensitivity_scale", 1.0))
                player.handle_mouse(mrx * mouse_scale, mry * mouse_scale)

            if input_integration is not None:
                input_integration.update_look(dt, enabled=not paused)
            if not paused and world.save_store is not None and getattr(self, "_autosave_enabled", True):
                self._autosave_elapsed += max(0.0, float(dt))
                autosave_job = getattr(self, "_autosave_job", None)
                if (autosave_job is None
                        and self._autosave_elapsed >= self._autosave_interval):
                    self._autosave_elapsed %= self._autosave_interval
                    try:
                        self._autosave_job = world.begin_save(player)
                    except Exception as exc:
                        print(f"Could not start autosave: {exc}")
                        self._autosave_job = None
                job = getattr(self, "_autosave_job", None)
                if job is not None:
                    try:
                        if job.step(1):
                            if not job.success:
                                print("Warning: autosave did not complete successfully; see errors above.")
                            self._autosave_job = None
                    except Exception as exc:
                        print(f"Warning: autosave failed: {exc}")
                        self._autosave_job = None

        try:
            terrain_texture = Block._get_texture()
            profiler = getattr(world, "profiler", None)

            animation_started = time.perf_counter()
            fire_animator.update(dt, terrain_texture)
            if profiler is not None:
                profiler.record_ms(
                    "texture.fire_animation",
                    (time.perf_counter() - animation_started) * 1000.0,
                )

            animation_started = time.perf_counter()
            thread_clock = getattr(time, "thread_time", time.process_time)
            animation_thread_started = thread_clock()
            fluid_animator.update(dt, terrain_texture)
            if profiler is not None:
                terrain_futures = getattr(world, "_terrain_futures", {})
                profiler.record_ms(
                    "texture.fluid_animation",
                    (time.perf_counter() - animation_started) * 1000.0,
                    event_details={
                        "thread_cpu_ms": round(
                            (thread_clock() - animation_thread_started) * 1000.0, 3
                        ),
                        "terrain_worker_count": int(
                            getattr(world, "_terrain_worker_count", 0) or 0
                        ),
                        "terrain_inflight": len(terrain_futures),
                    },
                )

            animation_started = time.perf_counter()
            portal_animator.update(
                dt, terrain_texture,
                anaglyph=bool(getattr(getattr(self, "settings", None), "anaglyph", False)),
            )
            if profiler is not None:
                profiler.record_ms(
                    "texture.portal_animation",
                    (time.perf_counter() - animation_started) * 1000.0,
                )

            animation_started = time.perf_counter()
            item_texture = getattr(Item, "item_texture", None)
            anaglyph = bool(getattr(getattr(self, "settings", None), "anaglyph", False))
            compass_animator.update(
                dt, item_texture,
                player=player, world=world, anaglyph=anaglyph,
            )
            if profiler is not None:
                profiler.record_ms(
                    "texture.compass_animation",
                    (time.perf_counter() - animation_started) * 1000.0,
                )

            animation_started = time.perf_counter()
            clock_animator.update(
                dt, item_texture,
                world=world, anaglyph=anaglyph,
            )
            if profiler is not None:
                profiler.record_ms(
                    "texture.clock_animation",
                    (time.perf_counter() - animation_started) * 1000.0,
                )
        except Exception:
            pass


    def _render(self, _anaglyph_pass=False, _eye_offset=0.0, _eye_index=0, _skip_world=False):
        d = self.display
        world, player = self.world, self.player
        gpu_profiler = getattr(self, "gpu_timer_profiler", None)
        if gpu_profiler is not None and not gpu_profiler.enabled:
            gpu_profiler = None
        if world is not None:
            world.gpu_timer_profiler = gpu_profiler
        if (self.settings.anaglyph and world is not None
                and not _anaglyph_pass and not _skip_world):
            for eye_index, eye_offset in enumerate((-0.07, 0.07)):
                self._render(
                    _anaglyph_pass=True,
                    _eye_offset=eye_offset,
                    _eye_index=eye_index,
                )
            d.composite_anaglyph()
            self._render(_skip_world=True)
            return

        partial = world.partial_ticks() if world is not None else 0.0
        player_partial = getattr(player, "animation_partial", partial) if player is not None else partial
        f1_held = self._f1_is_held()
        if world is not None:
            sky_rgb = world.get_sky_color(partial, player=player)
            base_fog_rgb = world.get_fog_color(partial)
            if bool(getattr(world, "is_hell_world", False)):
                fog_rgb = base_fog_rgb
            else:
                fog_rgb = alpha_sky_fog_color(
                    sky_rgb, base_fog_rgb, getattr(self.settings, "render_distance", 1),
                )
        else:
            fog_rgb = (0.6, 0.8, 1.0)
        in_fluid = None
        if player is not None:
            if getattr(player, "submerged", False):
                in_fluid = "water"
                fog_rgb = (0.02, 0.02, 0.2)
            try:
                ex = math.floor(player.pos[0] + 0.5)
                ey = math.floor(player.pos[1] + player.EYE_HEIGHT + 0.5)
                ez = math.floor(player.pos[2] + 0.5)
                if world is not None and world.get_block_type(ex, ey, ez) in ("lava", "flowing_lava"):
                    in_fluid = "lava"
                    fog_rgb = (0.6, 0.1, 0.0)
            except Exception:
                pass
        if world is not None and not _skip_world and not bool(getattr(world, "is_hell_world", False)):
            fog_brightness = self.renderer.interpolated_alpha_fog_brightness(partial)
            fog_rgb = alpha_scale_fog_color(fog_rgb, fog_brightness)
        if not _skip_world:
            d.clear(fog_rgb)

        portal_distortion_pushed = False
        if world is not None and not _skip_world:
            self.renderer.set_fog_color(fog_rgb)
            self.renderer.fov = 60.0 if in_fluid == "water" else 70.0
            self.renderer.setup_3d_projection(d.aspect)
            if gpu_profiler is not None:
                gpu_profiler.begin("gpu.world_3d_total", {
                    "anaglyph_pass": bool(_anaglyph_pass),
                    "eye_index": int(_eye_index) if _anaglyph_pass else None,
                })
            third_person_camera = None
            first_person_camera = False
            if self.third_person_view and player is not None:
                front_view = bool(f1_held) or bool(
                    getattr(self, "third_person_front_view", False)
                )
                camera_distance = 6.0 if front_view else 4.0
                side, up, back = player_model.third_person_camera_offset(
                    player, player_partial, camera_distance, front=front_view
                )
                glMatrixMode(GL_MODELVIEW)
                glLoadIdentity()
                if _anaglyph_pass:
                    glTranslatef(-float(_eye_offset), 0.0, 0.0)
                player_model.apply_hurt_camera_effect(player, player_partial)
                third_person_camera = (front_view, side, up, back)
            elif player is not None:
                player_model.begin_first_person_camera_transform(
                    player, player_partial,
                    eye_offset=float(_eye_offset) if _anaglyph_pass else 0.0,
                )
                first_person_camera = True
            else:
                self.renderer.sync_view_to_camera()
                if _anaglyph_pass:
                    glTranslatef(-float(_eye_offset), 0.0, 0.0)

            portal_progress = 0.0
            if player is not None:
                portal_previous = float(getattr(player, "prev_portal_time", 0.0))
                portal_current = float(getattr(player, "portal_time", 0.0))
                portal_progress = max(
                    0.0,
                    min(1.0, portal_previous
                        + (portal_current - portal_previous) * float(player_partial)),
                )
            if portal_progress > 0.0:
                warp = 5.0 / (portal_progress * portal_progress + 5.0)
                warp = (warp - portal_progress * 0.04) ** 2
                warp_angle = portal_progress * portal_progress * 1500.0
                glMatrixMode(GL_MODELVIEW)
                glPushMatrix()
                glRotatef(
                    warp_angle,
                    0.0, 1.0, 1.0,
                )
                glScalef(1.0 / warp, 1.0, 1.0)
                glRotatef(
                    -warp_angle,
                    0.0, 1.0, 1.0,
                )
                portal_distortion_pushed = True

            if third_person_camera is not None:
                front_view, side, up, back = third_person_camera
                if front_view:
                    glTranslatef(0.0, 0.0, back)
                    glRotatef(float(player.pitch), 1.0, 0.0, 0.0)
                    glRotatef(-(float(player.yaw) + 180.0), 0.0, 1.0, 0.0)
                else:
                    glTranslatef(side, up, back)
                    glRotatef(-float(player.pitch), 1.0, 0.0, 0.0)
                    glRotatef(-float(player.yaw), 0.0, 1.0, 0.0)
                dist = abs(float(back))
                bx, by, bz = player_model._third_person_behind_direction(
                    float(player.yaw), float(player.pitch),
                )
                eye_x = float(self.renderer.camX)
                eye_y = float(self.renderer.camY)
                eye_z = float(self.renderer.camZ)
                direction = -1.0 if front_view else 1.0
                self.renderer.view_x = eye_x + direction * bx * dist
                self.renderer.view_y = eye_y + direction * by * dist
                self.renderer.view_z = eye_z + direction * bz * dist
                if front_view:
                    self.renderer.yaw = (float(player.yaw) + 180.0) % 360.0
                    self.renderer.pitch = -float(player.pitch)
            elif first_person_camera:
                player_model.orient_first_person_camera(player)
                self.renderer.sync_view_to_camera()
            if in_fluid == "water":
                self.renderer.setup_underwater_fog(lava=False)
            elif in_fluid == "lava":
                self.renderer.setup_underwater_fog(lava=True)
            else:
                self.renderer.setup_linear_fog()
            self.renderer.fluid_mode = in_fluid
            if int(getattr(self.settings, "render_distance", 1)) < 2:
                with (gpu_profiler.measure("gpu.sky") if gpu_profiler is not None else nullcontext()):
                    world.render_sky(partial, aspect=d.aspect, player=player)
            world.draw(aspect=d.aspect)
            if in_fluid == "water":
                self.renderer.setup_underwater_fog(lava=False)
            elif in_fluid == "lava":
                self.renderer.setup_underwater_fog(lava=True)
            if player is not None:
                with (gpu_profiler.measure("gpu.clouds") if gpu_profiler is not None else nullcontext()):
                    world.render_clouds(player, partial)

            if self.gui.debug and self.gui.show_chunk_borders:
                draw_chunk_borders(world, player, self.renderer)

            dig_target = player._dig_target if player is not None else None
            dig_overlay_active = (player is not None and player._digging
                                  and dig_target is not None
                                  and player._destroy_progress > 0.0)
            dig_overlay_deferred = dig_overlay_active and destroy_overlay_is_entity_pass(dig_target, world)
            if dig_overlay_active and not dig_overlay_deferred:
                draw_block_destroy_progress(
                    dig_target, player._destroy_progress, world, self.renderer
                )
            session = getattr(self, "mp_session", None)
            if session is not None and session.active:
                session.render_break_progress(world, self.renderer, entity_pass=False)

            check_gl_error()

            with (gpu_profiler.measure("gpu.entities") if gpu_profiler is not None else nullcontext()):
                if player is not None:
                    player._third_person_view = bool(self.third_person_view)
                world.draw_entities()
                session = getattr(self, "mp_session", None)
                if session is not None and session.active:
                    session.render_players(player_partial)
                    session.render_name_tags(player_partial)
                if self.gui.debug:
                    draw_entity_hitboxes(world, self.renderer)
                targeted = player.get_targeted_block()
                if targeted:
                    draw_block_outline(targeted, world, self.renderer)
                for item in self.test_items:
                    item.draw()
                if player is not None and self.third_person_view:
                    prev = float(getattr(player, "prev_render_yaw_offset", player.render_yaw_offset))
                    cur = float(getattr(player, "render_yaw_offset", player.yaw))
                    diff = (cur - prev + 180.0) % 360.0 - 180.0
                    body_yaw = prev + diff * player_partial
                    try:
                        from game.entities.entity_shadow import (
                            SHADOW_SIZE_LIVING, render_entity_shadow,
                            shadow_opacity_for_distance,
                        )
                        px, py, pz = player.pos[0], player.pos[1], player.pos[2]
                        opacity = shadow_opacity_for_distance(world, px, py, pz)
                        if opacity > 0.0:
                            render_entity_shadow(
                                world, px, py, pz, SHADOW_SIZE_LIVING, opacity=opacity,
                            )
                    except Exception:
                        pass
                    player_model.render_player_model(
                        player,
                        player.pos[0],
                        player.pos[1] - getattr(player, "render_y_offset", 0.0),
                        player.pos[2],
                        body_yaw,
                        player_partial,
                    )
                    if getattr(player, "_fire_ticks", 0) > 0:
                        player_model.render_player_fire_effect(
                            player, player.pos[0],
                            player.pos[1] - getattr(player, "render_y_offset", 0.0),
                            player.pos[2],
                        )
                if dig_overlay_deferred:
                    draw_block_destroy_progress(
                        dig_target, player._destroy_progress, world, self.renderer
                    )
                session = getattr(self, "mp_session", None)
                if session is not None and session.active:
                    session.render_break_progress(world, self.renderer, entity_pass=True)

            with (gpu_profiler.measure("gpu.particles_inside") if gpu_profiler is not None else nullcontext()):
                world.draw_particles(
                    (self.renderer.camX, self.renderer.camY, self.renderer.camZ),
                    self.renderer.yaw, self.renderer.pitch, partial, fluid_mode="inside",
                )

            world.draw_transparent()

            with (gpu_profiler.measure("gpu.particles_outside") if gpu_profiler is not None else nullcontext()):
                world.draw_particles(
                    (self.renderer.camX, self.renderer.camY, self.renderer.camZ),
                    self.renderer.yaw, self.renderer.pitch, partial, fluid_mode="outside",
                )

            if player is not None:
                with (gpu_profiler.measure("gpu.weather") if gpu_profiler is not None else nullcontext()):
                    render_precipitation(world, player, self.renderer, partial)

            if portal_distortion_pushed:
                glPopMatrix()

            if player is not None and not self.third_person_view and not f1_held:
                with (gpu_profiler.measure("gpu.player_hand") if gpu_profiler is not None else nullcontext()):
                    glClear(GL_DEPTH_BUFFER_BIT)
                    player_model.render_first_person_hand(
                        player,
                        player_partial,
                        inventory_open=isinstance(self.current_screen, GuiInventory),
                    )
                    if getattr(player, "_fire_ticks", 0) > 0:
                        player_model.render_first_person_fire_overlay(player)

            if gpu_profiler is not None:
                gpu_profiler.end("gpu.world_3d_total")

        if _anaglyph_pass:
            d.capture_anaglyph_eye(_eye_index)
            return

        d.setup_2d_projection()
        cur = self.current_screen
        hide_hud = f1_held or (cur is not None and cur.does_gui_pause_game())
        if world is not None:
            if player is not None and not self.third_person_view and not f1_held:
                block_tile = player_model.inside_opaque_block_texture(player, world)
                if block_tile is not None:
                    self.gui.draw_inside_block_overlay(block_tile)
                self.gui.draw_pumpkin_overlay(player)
                self.gui.draw_portal_overlay(player, partial)
                if not hide_hud and in_fluid == "water":
                    self.gui.draw_water_overlay(player)
        if world is not None and not hide_hud:
            fps = int(self.clock.get_fps())
            self.gui.draw(player, world, fps, sound_debug_line=self.audio.debug_line())
            if self._performance_log_status and time.monotonic() < self._performance_log_status_until:
                d.draw_string(self._performance_log_status, (4, 44), (255, 255, 255), 2)

        if cur is not None:
            scale, _, _ = self.scaled_resolution()
            mx, my = self.to_scaled(pg.mouse.get_pos())
            glPushMatrix()
            glScalef(scale, scale, 1.0)
            cur.draw_screen(mx, my)
            draw_pointer = getattr(
                getattr(self, "input_integration", None), "draw_cursor", None
            )
            if callable(draw_pointer):
                draw_pointer(self)
            glPopMatrix()

        d.update()
        profiler = getattr(world, "profiler", None) if world is not None else None
        if profiler is not None:
            profiler.record_ms("display.flip", getattr(d, "_last_flip_ms", 0.0))
            profiler.record_ms(
                "display.clock_tick", getattr(d, "_last_clock_tick_ms", 0.0)
            )


    def run(self):
        while self.running:
            self._frame_index = int(getattr(self, "_frame_index", 0)) + 1
            loop_entry = time.perf_counter()
            idle_gap_ms = max(0.0, (loop_entry - self._last_frame_end_perf) * 1000.0)
            cap = 0 if getattr(self.display, "vsync_active", False) else (
                60 if self.settings.limit_framerate else 0
            )
            clock_started = time.perf_counter()
            dt_ms = self.clock.tick(cap)
            now = time.perf_counter()
            last_start = getattr(self, "_last_frame_start_perf", None)
            self._last_frame_start_perf = now
            dt = (now - last_start) if last_start is not None else dt_ms / 1000.0
            dt = min(max(dt, 0.0), 0.25)
            clock_tick_call_ms = (now - clock_started) * 1000.0
            self.gui.record_frame(dt)
            self._flush_gc_profile_events()
            if self.performance_log.active and dt >= 0.05:
                self.performance_log.record_event(
                    "frame.hitch", duration_ms=round(dt * 1000.0, 3),
                    fps=round(1.0 / dt, 2) if dt > 0 else 0.0,
                    frame_index=int(self._frame_index),
                    loaded_chunks=len(self.world.chunks) if self.world is not None else 0,
                    previous_frame=dict(self._last_frame_stage_metrics),
                    idle_gap_ms=round(idle_gap_ms, 3),
                    clock_tick_call_ms=round(clock_tick_call_ms, 3),
                    gc_counts=list(gc.get_count()),
                    gc_enabled=bool(gc.isenabled()),
                )
            event_started = time.perf_counter()
            self._handle_events()
            event_handling_ms = (time.perf_counter() - event_started) * 1000.0
            update_started = time.perf_counter()
            self._update(dt)
            update_ms = (time.perf_counter() - update_started) * 1000.0
            gpu_profiler = getattr(self, "gpu_timer_profiler", None)
            if gpu_profiler is not None:
                world_profiler = getattr(self.world, "profiler", None) if self.world is not None else None
                gpu_profiler.begin_frame(world_profiler)
            render_started = time.perf_counter()
            try:
                self._render()
            finally:
                if gpu_profiler is not None:
                    gpu_profiler.end_open_scopes()
            render_ms = (time.perf_counter() - render_started) * 1000.0
            self._last_frame_stage_metrics = {
                "frame_index": int(self._frame_index),
                "event_handling_ms": round(event_handling_ms, 3),
                "update_ms": round(update_ms, 3),
                "render_ms": round(render_ms, 3),
            }
            self._last_frame_end_perf = time.perf_counter()
