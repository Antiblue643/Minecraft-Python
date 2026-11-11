import gc
import time
from pathlib import Path


class GamePerformanceMixin:
    def _set_gc_profile_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled and not self._gc_profile_registered:
            self._gc_profile_started.clear()
            gc.callbacks.append(self._gc_profile_callback)
            self._gc_profile_registered = True
        elif not enabled and self._gc_profile_registered:
            try:
                gc.callbacks.remove(self._gc_profile_callback)
            except ValueError:
                pass
            self._gc_profile_registered = False
            self._gc_profile_started.clear()


    def _on_gc_profile_event(self, phase, info):
        if not self._gc_profile_registered:
            return
        generation = int(info.get("generation", -1))
        if phase == "start":
            self._gc_profile_started[generation] = time.perf_counter()
            return
        if phase != "stop":
            return
        started = self._gc_profile_started.pop(generation, None)
        duration_ms = ((time.perf_counter() - started) * 1000.0) if started is not None else None
        self._gc_profile_events.append((
            generation,
            round(duration_ms, 3) if duration_ms is not None else None,
            int(info.get("collected", 0)),
            int(info.get("uncollectable", 0)),
        ))


    def _flush_gc_profile_events(self):
        if not self.performance_log.active:
            self._gc_profile_events.clear()
            return
        while self._gc_profile_events:
            generation, duration_ms, collected, uncollectable = self._gc_profile_events.popleft()
            self.performance_log.record_event(
                "gc.collection", generation=generation, duration_ms=duration_ms,
                collected=collected, uncollectable=uncollectable,
            )


    def toggle_performance_log(self):
        if self.performance_log.active:
            gpu_profiler = getattr(self, "gpu_timer_profiler", None)
            if gpu_profiler is not None:
                gpu_profiler.set_enabled(False)
                if self.world is not None:
                    gpu_profiler.report_diagnostics(self.world.profiler, final=True)
            self._flush_gc_profile_events()
            self._set_gc_profile_enabled(False)
            path = self.performance_log.stop()
            if self.world is not None:
                self.world.profiler.set_event_sink(None)
            message = f"Performance log saved: {path}"
            print(message)
            from game.localization import translate
            self.add_chat_message(translate(
                "mcpy.performance.saved", Path(path).name if path else "(unknown)",
            ))
        else:
            metadata = {}
            if self.world is not None:
                metadata.update({
                    "world_seed": int(self.world.seed),
                    "world_time": int(self.world.world_time),
                    "loaded_chunks": len(self.world.chunks),
                    "debug_world": bool(getattr(self.world, "debug_world", False)),
                    "terrain_worker_count": int(
                        getattr(self.world, "_terrain_worker_count", 0) or 0
                    ),
                    "terrain_max_inflight": int(
                        getattr(self.world, "_terrain_max_inflight", 0) or 0
                    ),
                    "player_position": [round(float(v), 3) for v in self.player.pos] if self.player else None,
                    "render_distance": int(self.settings.render_chunks),
                    "fancy_graphics": bool(getattr(self.settings, "fancy_graphics", False)),
                    "vsync_active": bool(getattr(self.display, "vsync_active", False)),
                })
            try:
                gpu_profiler = getattr(self, "gpu_timer_profiler", None)
                if gpu_profiler is not None:
                    gpu_info = gpu_profiler.initialize()
                    metadata["gpu"] = gpu_info
                metadata["display_resolution"] = [
                    int(getattr(self.display, "width", 0)),
                    int(getattr(self.display, "height", 0)),
                ]
                path = self.performance_log.start(metadata=metadata)
            except OSError as exc:
                if self.world is not None:
                    self.world.profiler.set_event_sink(None)
                message = f"Could not start performance log: {exc}"
                print(message)
                from game.localization import translate
                self.add_chat_message(translate(
                    "mcpy.performance.startFailed",
                ))
                return
            if self.world is not None:
                self.world.profiler.set_event_sink(self.performance_log)
                if gpu_profiler is not None:
                    gpu_profiler.reset_status_report()
                    gpu_profiler.report_status(self.world.profiler)
            if gpu_profiler is not None:
                gpu_profiler.set_enabled(True, discard_pending=True)
                if self.world is not None:
                    gpu_profiler.report_diagnostics(self.world.profiler, final=False)
            self._set_gc_profile_enabled(True)
            message = f"Performance log recording: {path} (Alt+F3 to stop)"
            print(message)
            from game.localization import translate
            self.add_chat_message(translate(
                "mcpy.performance.recording", Path(path).name,
            ))

