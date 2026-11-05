
from contextlib import contextmanager
import ctypes
import re


class GpuTimerQueryProfiler:

    def __init__(self, *, slots_per_stage=6, gl_api=None, raw_query_result_u64=None):
        self.slots_per_stage = max(2, int(slots_per_stage))
        self._gl = gl_api
        self._raw_query_result_u64 = raw_query_result_u64
        self._query_result_api = (
            "injected_raw" if raw_query_result_u64 is not None
            else ("injected_gl_api" if gl_api is not None else None)
        )
        self._slots = {}
        self._active = {}
        self._next_slot = {}
        self._enabled = False
        self._initialized = False
        self._frame_index = 0
        self._device_info = {
            "supported": False,
            "api": "GL_TIMESTAMP",
            "asynchronous": True,
            "reason": "not_initialized",
        }
        self._status_reported = False
        self._pending_runtime_error = None
        self._stats = self._new_stats()

    @staticmethod
    def _new_stats():
        return {
            "begin_calls": 0,
            "begin_armed": 0,
            "begin_disabled": 0,
            "begin_nested": 0,
            "begin_ring_full": 0,
            "end_calls": 0,
            "end_written": 0,
            "end_missing_scope": 0,
            "poll_calls": 0,
            "poll_pending_slots": 0,
            "poll_availability_checks": 0,
            "poll_available_slots": 0,
            "results_completed": 0,
            "samples_recorded": 0,
            "samples_discarded": 0,
            "runtime_errors": 0,
            "allocated_query_pairs": 0,
            "active_scopes_at_report": 0,
            "pending_query_pairs_at_report": 0,
        }

    @property
    def enabled(self):
        return self._enabled and self._device_info.get("supported", False)

    @property
    def device_info(self):
        return dict(self._device_info)

    @staticmethod
    def _decode_gl_string(value):
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("ascii", errors="replace")
        return str(value)

    @staticmethod
    def _query_id(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(value[0])

    def initialize(self):
        if self._initialized:
            return self.device_info
        self._initialized = True
        try:
            if self._gl is None:
                from OpenGL import GL as gl
                self._gl = gl
                if self._raw_query_result_u64 is None:
                    try:
                        from OpenGL.raw.GL.VERSION.GL_3_3 import (
                            glGetQueryObjectui64v as raw_query_result_u64,
                        )
                        self._query_result_api = "raw_core_gl_3_3"
                    except Exception as core_exc:
                        try:
                            from OpenGL.raw.GL.ARB.timer_query import (
                                glGetQueryObjectui64v as raw_query_result_u64,
                            )
                            self._query_result_api = "raw_arb_timer_query"
                        except Exception as arb_exc:
                            raise RuntimeError(
                                "PyOpenGL raw glGetQueryObjectui64v entry point "
                                f"unavailable (core: {core_exc}; ARB: {arb_exc})"
                            ) from arb_exc
                    self._raw_query_result_u64 = raw_query_result_u64
            gl = self._gl
            version = self._decode_gl_string(gl.glGetString(gl.GL_VERSION))
            vendor = self._decode_gl_string(gl.glGetString(gl.GL_VENDOR))
            renderer = self._decode_gl_string(gl.glGetString(gl.GL_RENDERER))
            version_match = re.match(r"\s*(\d+)\.(\d+)", version or "")
            version_tuple = (
                (int(version_match.group(1)), int(version_match.group(2)))
                if version_match else (0, 0)
            )

            extension_supported = version_tuple >= (3, 3)
            if not extension_supported:
                try:
                    extensions = self._decode_gl_string(
                        gl.glGetString(gl.GL_EXTENSIONS)
                    ) or ""
                except Exception:
                    extensions = ""
                extension_supported = "GL_ARB_timer_query" in extensions.split()

            required_functions = (
                "glGenQueries", "glDeleteQueries", "glQueryCounter",
                "glGetQueryObjectiv",
            )
            missing = [
                name for name in required_functions
                if not callable(getattr(gl, name, None))
            ]
            if not extension_supported:
                reason = "OpenGL 3.3 or GL_ARB_timer_query is unavailable"
            elif missing:
                reason = "missing OpenGL entry points: " + ", ".join(missing)
            elif self._raw_query_result_u64 is None and not callable(
                getattr(gl, "glGetQueryObjectui64v", None)
            ):
                reason = "missing OpenGL entry point: glGetQueryObjectui64v"
            else:
                reason = None

            self._device_info = {
                "supported": reason is None,
                "api": "GL_TIMESTAMP",
                "asynchronous": True,
                "query_result_api": self._query_result_api or "pyopengl_wrapper",
                "gl_version": version,
                "gl_vendor": vendor,
                "gl_renderer": renderer,
                "reason": reason,
            }
        except Exception as exc:
            self._device_info = {
                "supported": False,
                "api": "GL_TIMESTAMP",
                "asynchronous": True,
                "reason": f"initialization failed: {type(exc).__name__}: {exc}",
            }
        return self.device_info

    def set_enabled(self, enabled, *, discard_pending=False):
        self._enabled = bool(enabled)
        if discard_pending:
            self._stats = self._new_stats()
            for slots in self._slots.values():
                for slot in slots:
                    if slot["pending"]:
                        slot["discard"] = True

    def _new_slot(self):
        gl = self._gl
        start_query = self._query_id(gl.glGenQueries(1))
        try:
            end_query = self._query_id(gl.glGenQueries(1))
        except Exception:
            try:
                self._delete_query(start_query)
            except Exception:
                pass
            raise
        self._stats["allocated_query_pairs"] += 1
        return {
            "start": start_query,
            "end": end_query,
            "pending": False,
            "discard": False,
            "frame": 0,
            "details": {},
        }

    def _delete_query(self, query_id):
        gl = self._gl
        try:
            gl.glDeleteQueries(1, [int(query_id)])
        except TypeError:
            gl.glDeleteQueries([int(query_id)])

    def _slot_for_begin(self, stage):
        slots = self._slots.setdefault(stage, [])
        if not slots:
            slots.append(self._new_slot())
        start = self._next_slot.get(stage, 0) % len(slots)
        for offset in range(len(slots)):
            index = (start + offset) % len(slots)
            slot = slots[index]
            if not slot["pending"]:
                self._next_slot[stage] = (index + 1) % len(slots)
                return slot
        if len(slots) < self.slots_per_stage:
            slot = self._new_slot()
            slots.append(slot)
            self._next_slot[stage] = 0
            return slot
        return None

    def begin(self, stage, details=None):
        self._stats["begin_calls"] += 1
        if not self.enabled:
            self._stats["begin_disabled"] += 1
            return False
        stage = str(stage)
        if stage in self._active:
            self._stats["begin_nested"] += 1
            return False
        try:
            slot = self._slot_for_begin(stage)
            if slot is None:
                self._stats["begin_ring_full"] += 1
                return False
            self._gl.glQueryCounter(slot["start"], self._gl.GL_TIMESTAMP)
            slot["frame"] = self._frame_index
            slot["details"] = dict(details or {})
            slot["discard"] = False
            self._active[stage] = slot
            self._stats["begin_armed"] += 1
            return True
        except Exception as exc:
            self._disable_after_error(exc)
            return False

    def end(self, stage, details=None):
        self._stats["end_calls"] += 1
        stage = str(stage)
        slot = self._active.pop(stage, None)
        if slot is None:
            self._stats["end_missing_scope"] += 1
            return False
        try:
            if details:
                slot["details"].update(details)
            self._gl.glQueryCounter(slot["end"], self._gl.GL_TIMESTAMP)
            slot["pending"] = True
            self._stats["end_written"] += 1
            return True
        except Exception as exc:
            slot["pending"] = False
            self._disable_after_error(exc)
            return False

    @contextmanager
    def measure(self, stage, details=None):
        armed = self.begin(stage, details=details)
        try:
            yield
        finally:
            if armed:
                self.end(stage)

    def end_open_scopes(self):
        for stage in tuple(self._active):
            self.end(stage, {"scope_closed_by_safety_net": True})

    def begin_frame(self, profiler=None):
        self._frame_index += 1
        self.poll(profiler)

    @staticmethod
    def _scalar_result(value):
        if hasattr(value, "value"):
            value = value.value
        try:
            return int(value)
        except (TypeError, ValueError):
            if value is None:
                raise RuntimeError("OpenGL query getter returned None")
            try:
                if len(value) == 1:
                    return int(value[0])
            except (TypeError, ValueError, IndexError):
                pass
            raise RuntimeError(
                f"OpenGL query getter returned a non-scalar value: {type(value).__name__}"
            )

    def _get_query_scalar(self, query_id, pname, *, unsigned=False):
        if unsigned and self._raw_query_result_u64 is not None:
            output = ctypes.c_uint64()
            self._raw_query_result_u64(
                int(query_id), int(pname), ctypes.byref(output)
            )
            return int(output.value)

        name = "glGetQueryObjectui64v" if unsigned else "glGetQueryObjectiv"
        getter = getattr(self._gl, name)
        try:
            value = getter(int(query_id), pname)
            return self._scalar_result(value)
        except TypeError:
            output = ctypes.c_uint64() if unsigned else ctypes.c_int()
            getter(int(query_id), pname, ctypes.byref(output))
            return int(output.value)

    def poll(self, profiler=None):
        self._stats["poll_calls"] += 1
        if self._pending_runtime_error is not None and profiler is not None:
            profiler.record_event(
                "gpu.timer_query_error", reason=self._pending_runtime_error
            )
            self._pending_runtime_error = None
        if not self._initialized or not self._device_info.get("supported"):
            return 0
        gl = self._gl
        completed = 0
        try:
            for stage, slots in tuple(self._slots.items()):
                for slot in slots:
                    if not slot["pending"]:
                        continue
                    self._stats["poll_pending_slots"] += 1
                    self._stats["poll_availability_checks"] += 1
                    available = self._get_query_scalar(
                        slot["end"], gl.GL_QUERY_RESULT_AVAILABLE
                    )
                    if not bool(available):
                        continue
                    self._stats["poll_available_slots"] += 1
                    start_ns = self._get_query_scalar(
                        slot["start"], gl.GL_QUERY_RESULT, unsigned=True
                    )
                    end_ns = self._get_query_scalar(
                        slot["end"], gl.GL_QUERY_RESULT, unsigned=True
                    )
                    duration_ms = max(0, end_ns - start_ns) / 1_000_000.0
                    details = dict(slot["details"])
                    details.update({
                        "timer_source": "GL_TIMESTAMP",
                        "query_delay_frames": max(0, self._frame_index - slot["frame"]),
                        "query_submitted_frame": int(slot["frame"]),
                        "query_completed_frame": int(self._frame_index),
                    })
                    if (not slot["discard"] and self._enabled and profiler is not None):
                        profiler.record_ms(stage, duration_ms, event_details=details)
                        self._stats["samples_recorded"] += 1
                    else:
                        self._stats["samples_discarded"] += 1
                    slot["pending"] = False
                    slot["discard"] = False
                    slot["details"] = {}
                    completed += 1
            self._stats["results_completed"] += completed
        except Exception as exc:
            self._disable_after_error(exc)
        return completed

    def diagnostics(self):
        result = dict(self._stats)
        result.update({
            "enabled": bool(self._enabled),
            "supported": bool(self._device_info.get("supported")),
            "frame_index": int(self._frame_index),
            "stage_count": len(self._slots),
            "active_scopes_at_report": len(self._active),
            "pending_query_pairs_at_report": sum(
                1 for slots in self._slots.values() for slot in slots
                if slot["pending"]
            ),
            "runtime_error": self._device_info.get("reason")
                if "runtime query failure" in str(self._device_info.get("reason"))
                else None,
        })
        return result

    def report_diagnostics(self, profiler, *, final=False):
        if profiler is None:
            return
        payload = self.diagnostics()
        payload["final"] = bool(final)
        profiler.record_event("gpu.timer_query_diagnostics", **payload)

    def report_status(self, profiler):
        if profiler is None:
            return
        if not self._initialized:
            self.initialize()
        if not self._status_reported:
            profiler.record_event("gpu.timer_query_status", **self.device_info)
            self._status_reported = True

    def reset_status_report(self):
        self._status_reported = False

    def _disable_after_error(self, exc):
        self._stats["runtime_errors"] += 1
        self._enabled = False
        self._device_info = dict(self._device_info)
        self._device_info["supported"] = False
        self._device_info["reason"] = f"runtime query failure: {type(exc).__name__}: {exc}"
        self._pending_runtime_error = self._device_info["reason"]
        for slot in (item for slots in self._slots.values() for item in slots):
            slot["pending"] = False
        self._active.clear()

    def close(self):
        if self._gl is not None:
            for slot in (item for slots in self._slots.values() for item in slots):
                for key in ("start", "end"):
                    query_id = slot.get(key)
                    if query_id:
                        try:
                            self._delete_query(query_id)
                        except Exception:
                            pass
        self._slots.clear()
        self._active.clear()
        self._initialized = False
        self._enabled = False
