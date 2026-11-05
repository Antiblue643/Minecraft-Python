from collections import deque
from datetime import datetime, timezone
import json
import platform
from pathlib import Path
import sys
import threading
import time


class PerformanceEventLog:
    _TIMING_EVENTS = {
        "terrain.worker": "chunk.terrain_worker_total",
        "terrain.worker.load_saved": "chunk.terrain_worker_load_saved",
        "terrain.worker.generate": "chunk.terrain_worker_generate",
        "chunk.integrate": "chunk.load_integrate",
        "chunk.integrate.save_gate": "chunk.integrate_save_gate",
        "chunk.integrate.save_load": "chunk.integrate_save_load",
        "chunk.integrate.saved_migration": "chunk.integrate_saved_migration",
        "chunk.integrate.surface": "chunk.surface_index",
        "chunk.integrate.construct": "chunk.construct",
        "chunk.integrate.overrides": "chunk.restore_overrides",
        "chunk.integrate.redstone_index": "chunk.redstone_index",
        "chunk.integrate.fluid_index": "chunk.fluid_index",
        "chunk.integrate.fluid_overrides": "chunk.fluid_override_schedule",
        "chunk.integrate.restore_entities": "chunk.restore_entities",
        "chunk.integrate.restore_tile_entities": "chunk.restore_tile_entities",
        "chunk.integrate.neighbors": "chunk.invalidate_neighbors",
        "chunk.terrain_future_latency": "chunk.terrain_future_latency",
        "chunk.terrain_integration_queue_wait": "chunk.terrain_integration_queue_wait",
        "chunk.terrain_integration_batch": "chunk.terrain_integration_batch",
        "chunk.stream_ready_latency": "chunk.stream_ready_latency",
        "chunk.save_wait": "chunk.save_wait",
        "population.chunk": "chunk.population",
        "population.commit_total": "chunk.population_commit_total",
        "population.snapshot": "chunk.population_snapshot",
        "population.commit": "chunk.population_commit",
        "population.terrain_fallback": "chunk.population_terrain_fallback",
        "lighting.chunk": "lighting.chunk_rebuild",
        "lighting.queue": "lighting.queue_process",
        "lighting.natural_flush": "lighting.natural_flush",
        "mesh.scan": "mesh.scan",
        "mesh.pack": "mesh.pack",
        "mesh.merge": "mesh.merge",
        "mesh.upload": "mesh.upload",
        "mesh.total": "mesh.build",
        "mesh.frame_build": "mesh.frame_build",
        "render.opaque_draw": "render.opaque_draw",
        "render.transparent_draw": "render.transparent_draw",
        "render.chunk_cull": "render.chunk_cull",
        "render.transparent_collect": "render.transparent_collect",
        "render.transparent_sort": "render.transparent_sort",
        "render.transparent_upload": "render.transparent_upload",
        "gpu.world_3d_total": "gpu.world_3d_total",
        "gpu.sky": "gpu.sky",
        "gpu.clouds": "gpu.clouds",
        "gpu.terrain_opaque": "gpu.terrain_opaque",
        "gpu.terrain_cutout": "gpu.terrain_cutout",
        "gpu.terrain_cutout_culled": "gpu.terrain_cutout_culled",
        "gpu.transparent_upload": "gpu.transparent_upload",
        "gpu.transparent_draw": "gpu.transparent_draw",
        "gpu.entities": "gpu.entities",
        "gpu.particles_inside": "gpu.particles_inside",
        "gpu.particles_outside": "gpu.particles_outside",
        "gpu.weather": "gpu.weather",
        "gpu.player_hand": "gpu.player_hand",
        "chunk.save_snapshot": "chunk.save_snapshot",
        "chunk.save_snapshot.fingerprint": "chunk.save_snapshot_fingerprint",
        "chunk.save_snapshot.blocks": "chunk.save_snapshot_blocks",
        "chunk.save_snapshot.arrays": "chunk.save_snapshot_arrays",
        "chunk.save_snapshot.entities": "chunk.save_snapshot_entities",
        "chunk.save_snapshot.tile_entities": "chunk.save_snapshot_tile_entities",
        "chunk.save_snapshot.nbt_assembly": "chunk.save_snapshot_nbt_assembly",
        "chunk.save_worker_write": "chunk.save_worker_write",
        "chunk.save_gate.entities": "chunk.save_gate_entities",
        "chunk.save_gate.tile_entities": "chunk.save_gate_tile_entities",
        "chunk.save_gate.needs_saving": "chunk.save_gate_needs_saving",
        "chunk.save_gate.empty_check": "chunk.save_gate_empty_check",
        "texture.fire_animation": "texture.fire_animation",
        "texture.fluid_animation": "texture.fluid_animation",
        "display.flip": "display.flip",
        "display.clock_tick": "display.clock_tick",
        "fluid.tick_batch": "fluid.tick_batch",
        "random.tick_batch": "random_tick.batch",
    }

    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.path = None
        self._file = None
        self._lock = threading.Lock()
        self._started = 0.0
        self._last_flush = 0.0
        self._event_count = 0

    @property
    def active(self):
        with self._lock:
            return self._file is not None

    @property
    def event_count(self):
        with self._lock:
            return self._event_count

    def start(self, metadata=None):
        with self._lock:
            if self._file is not None:
                return self.path
            self.output_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            self.path = self.output_dir / f"performance-{stamp}.jsonl"
            self._file = self.path.open("w", encoding="utf-8", buffering=8192)
            self._started = time.perf_counter()
            self._last_flush = self._started
            self._event_count = 0
            header = {
                "record": "session_start",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "metadata": metadata or {},
            }
            self._write_locked(header)
            self._file.flush()
            return self.path

    def stop(self):
        with self._lock:
            if self._file is None:
                return self.path
            path = self.path
            record = {
                "record": "session_end",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": round((time.perf_counter() - self._started) * 1000.0, 3),
                "event_count": self._event_count,
            }
            try:
                self._write_locked(record)
                self._file.flush()
            except OSError:
                pass
            finally:
                try:
                    self._file.close()
                except OSError:
                    pass
                self._file = None
            return path

    def record_event(self, event, **fields):
        with self._lock:
            if self._file is None:
                return False
            record = {
                "record": "event",
                "event": str(event),
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": round((time.perf_counter() - self._started) * 1000.0, 3),
            }
            record.update(fields)
            try:
                self._write_locked(record)
                self._event_count += 1
                now = time.perf_counter()
                if now - self._last_flush >= 1.0:
                    self._file.flush()
                    self._last_flush = now
            except OSError:
                try:
                    self._file.close()
                except OSError:
                    pass
                self._file = None
                return False
            return True

    def record_timing(self, stage, elapsed_ms, details=None):
        stage = str(stage)
        event_name = self._TIMING_EVENTS.get(stage)
        if event_name is None:
            return False
        if stage == "fluid.tick_batch" and details and int(details.get("processed", 0)) <= 0:
            if not (details.get("budget_exhausted", False)
                    or details.get("limit_exhausted", False)):
                return False
        payload = {"stage": str(stage), "duration_ms": round(float(elapsed_ms), 3)}
        if details:
            payload.update(details)
        return self.record_event(event_name, **payload)

    def _write_locked(self, record):
        self._file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


class PipelineProfiler:
    def __init__(self, samples=180, enabled=True):
        self.enabled = bool(enabled)
        self._samples_per_stage = max(10, int(samples))
        self._samples = {}
        self._counts = {}
        self._lock = threading.Lock()
        self._event_sink = None

    def set_event_sink(self, sink):
        with self._lock:
            self._event_sink = sink

    def record_event(self, event, **fields):
        with self._lock:
            sink = self._event_sink
        if sink is not None:
            sink.record_event(event, **fields)

    def record_ms(self, stage, elapsed_ms, event_details=None):
        if not self.enabled:
            return
        stage = str(stage)
        value = max(0.0, float(elapsed_ms))
        with self._lock:
            samples = self._samples.get(stage)
            if samples is None:
                samples = self._samples[stage] = deque(maxlen=self._samples_per_stage)
                self._counts[stage] = 0
            samples.append(value)
            self._counts[stage] += 1
            sink = self._event_sink
        if sink is not None:
            sink.record_timing(stage, value, details=event_details)

    def record_since(self, stage, started_at, event_details=None):
        self.record_ms(
            stage,
            (time.perf_counter() - started_at) * 1000.0,
            event_details=event_details,
        )

    def snapshot(self, stage):
        with self._lock:
            values = tuple(self._samples.get(stage, ()))
            count = int(self._counts.get(stage, 0))
        if not values:
            return None
        ordered = sorted(values)
        p95_index = min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))
        return {
            "last_ms": values[-1],
            "avg_ms": sum(values) / len(values),
            "p95_ms": ordered[p95_index],
            "max_ms": max(values),
            "count": count,
            "window": len(values),
        }

    def compact(self, stage, label=None):
        stats = self.snapshot(stage)
        if stats is None:
            return f"{label or stage}: --"
        return (
            f"{label or stage}: {stats['last_ms']:.1f}ms "
            f"avg {stats['avg_ms']:.1f} p95 {stats['p95_ms']:.1f}"
        )
