
from __future__ import annotations

import math
import random
import re
from pathlib import Path


class SoundManager:

    _ALIASES = {
        "step.metal": "step.stone",
        "step.glass": "step.stone",
        "step.powder": "step.gravel",
        "damage.fallbig": "step.stone",
        "damage.fallsmall": "step.stone",
        "ambient.cave": "ambient.cave.cave",
    }

    def __init__(self, sound_root, pygame_module=None, rng=None):
        self.sound_root = Path(sound_root).resolve()
        self.pg = pygame_module
        self.rng = rng or random.Random()
        self._pools: dict[str, tuple[Path, ...]] = {}
        self._loaded: dict[Path, object | None] = {}
        self._pitched: dict[tuple[Path, float], object | None] = {}
        self._last_debug_event: dict[str, object] | None = None
        self._channels_configured = False

    def debug_line(self) -> str:
        event = self._last_debug_event
        if event is None:
            return "sound: (none played yet)"
        name = str(event["name"])
        gain = float(event["gain"])
        pitch = float(event["pitch"])
        volume = float(event["volume"])
        distance = event["distance"]
        if not bool(event["positional"]):
            distance_text = "2D"
        elif distance is None:
            distance_text = "d=?"
        else:
            distance_text = f"d={float(distance):.1f}"
        return (
            f"sound: {name}  vol={volume:.2f} "
            f"pitch={pitch:.2f} gain={gain:.2f} {distance_text}"
        )

    def _pygame(self):
        if self.pg is None:
            import pygame as pg
            self.pg = pg
        return self.pg

    @staticmethod
    def _normal_name(name: str) -> str:
        return str(name).replace("/", ".").strip(".")

    def pool_paths(self, name: str) -> tuple[Path, ...]:
        name = self._normal_name(name)
        if name in self._pools:
            return self._pools[name]

        canonical = self._ALIASES.get(name, name)
        if canonical != name:
            paths = self.pool_paths(canonical)
            self._pools[name] = paths
            return paths

        parts = canonical.split(".")
        if not parts or any(part in ("", ".", "..") for part in parts):
            self._pools[name] = ()
            return ()
        parent = self.sound_root.joinpath(*parts[:-1])
        stem = parts[-1]
        if not parent.is_dir():
            self._pools[name] = ()
            return ()

        paths = []
        for candidate in parent.glob(stem + "*.ogg"):
            stripped = re.sub(r"\d+$", "", candidate.stem)
            if stripped == stem:
                paths.append(candidate.resolve())
        paths.sort(key=lambda p: (int(re.search(r"\d+$", p.stem).group())
                                  if re.search(r"\d+$", p.stem) else 0, p.name))
        self._pools[name] = tuple(paths)
        return self._pools[name]

    def _load_sound(self, path: Path):
        if path not in self._loaded:
            try:
                pg = self._pygame()
                self._loaded[path] = pg.mixer.Sound(str(path))
            except Exception:
                self._loaded[path] = None
        return self._loaded[path]

    def _pitch_sound(self, path: Path, sound, pitch: float):
        pitch = max(0.5, min(2.0, float(pitch)))
        if abs(pitch - 1.0) < 1e-3:
            return sound
        key = (path, round(pitch, 3))
        if key in self._pitched:
            return self._pitched[key]

        pitched = sound
        try:
            import numpy as np
            pg = self._pygame()
            samples = pg.sndarray.array(sound)
            if samples.size:
                frame_count = samples.shape[0]
                new_count = max(1, int(round(frame_count / pitch)))
                old_x = np.arange(frame_count, dtype=np.float64)
                new_x = np.linspace(0.0, max(frame_count - 1, 0), new_count)
                if samples.ndim == 1:
                    shifted = np.interp(new_x, old_x, samples).astype(samples.dtype)
                else:
                    shifted = np.empty((new_count, samples.shape[1]), dtype=samples.dtype)
                    for channel in range(samples.shape[1]):
                        shifted[:, channel] = np.interp(
                            new_x, old_x, samples[:, channel]
                        ).astype(samples.dtype)
                pitched = pg.sndarray.make_sound(shifted)
        except Exception:
            pitched = sound
        self._pitched[key] = pitched
        return pitched

    def play(
        self,
        name: str,
        volume: float = 1.0,
        pitch: float = 1.0,
        position=None,
        listener_position=None,
        enabled: bool = True,
        master_volume: float = 1.0,
    ) -> bool:
        if not enabled:
            return False
        try:
            pg = self._pygame()
            if not pg.mixer.get_init():
                return False
            if not self._channels_configured:
                get_count = getattr(pg.mixer, "get_num_channels", None)
                set_count = getattr(pg.mixer, "set_num_channels", None)
                if callable(get_count) and callable(set_count):
                    current_count = int(get_count())
                    if current_count < 32:
                        set_count(32)
                self._channels_configured = True
        except Exception:
            return False

        requested_volume = max(0.0, float(volume))
        distance = None
        if position is None:
            gain = requested_volume * 0.25
        elif listener_position is not None:
            distance = math.sqrt(sum(
                (float(position[i]) - float(listener_position[i])) ** 2
                for i in range(3)
            ))
            radius = 16.0 * max(1.0, requested_volume)
            gain = requested_volume * max(0.0, 1.0 - distance / radius)
        else:
            gain = requested_volume
        gain = min(1.0, gain) * max(0.0, min(1.0, float(master_volume)))
        if gain <= 0.0:
            return False

        paths = self.pool_paths(name)
        if not paths:
            return False
        path = self.rng.choice(paths)
        applied_pitch = max(0.5, min(2.0, float(pitch)))
        sound = self._load_sound(path)
        if sound is None:
            return False
        sound = self._pitch_sound(path, sound, applied_pitch)
        find_channel = getattr(pg.mixer, "find_channel", None)
        if callable(find_channel):
            channel = find_channel(force=False)
            if channel is None:
                return False
            channel.set_volume(gain)
            channel.play(sound)
        else:
            channel = sound.play()
            if channel is None:
                return False
            channel.set_volume(gain)
        self._last_debug_event = {
            "name": self._normal_name(name),
            "file": path.name,
            "volume": requested_volume,
            "pitch": applied_pitch,
            "gain": gain,
            "distance": distance,
            "positional": position is not None,
        }
        return True



class BackgroundMusicPlayer:

    TRACK_NAMES = (
        "calm1", "calm2", "calm3", "piano1", "piano2", "piano3",
        "hal1", "hal2", "hal3", "hal4", "nuance1", "nuance2",
    )

    def __init__(
        self,
        music_root,
        pygame_module=None,
        rng=None,
        *,
        delay_seconds=(300.0, 600.0),
        volume=0.5,
    ):
        self.music_root = Path(music_root).resolve()
        self.pg = pygame_module
        self.rng = rng or random.Random()
        self.delay_seconds = tuple(float(v) for v in delay_seconds)
        self.volume = max(0.0, min(1.0, float(volume)))
        self.ambient_gain = 1.0
        self.master_volume = 1.0
        self._paths = tuple(
            path for name in self.TRACK_NAMES
            if (path := self.music_root / f"{name}.ogg").is_file()
        )
        self._current_track = None
        self._record_track = None
        self._record_name = None
        self._record_position = None
        self._seconds_until_next = self._new_delay()

    def _base_volume(self):
        return self.volume * max(0.0, min(1.0, float(self.master_volume)))

    def _new_delay(self):
        low, high = self.delay_seconds
        return float(self.rng.uniform(low, high))

    def _music(self):
        if self.pg is None:
            import pygame as pg
            self.pg = pg
        return self.pg.mixer.music

    def _record_path(self, record_name):
        name = str(record_name).strip().lower()
        if name not in ("13", "cat"):
            return None
        for path in (
            self.music_root / "records" / f"{name}.ogg",
            self.music_root / f"{name}.ogg",
        ):
            if path.is_file():
                return path
        fallback_name = "calm1" if name == "13" else "piano1"
        fallback = self.music_root / f"{fallback_name}.ogg"
        return fallback if fallback.is_file() else None

    def play_record(self, record_name, position):
        track = self._record_path(record_name)
        if track is None:
            return False
        try:
            music = self._music()
            music.stop()
            music.load(str(track))
            set_volume = getattr(music, "set_volume", None)
            if callable(set_volume):
                set_volume(self._base_volume())
            music.play()
        except Exception:
            self._current_track = None
            self._record_track = None
            self._record_name = None
            self._record_position = None
            self._seconds_until_next = self._new_delay()
            return False
        self._current_track = None
        self._record_track = track
        self._record_name = str(record_name).strip().lower()
        self._record_position = tuple(float(v) for v in position)
        return True

    def stop_all(self):
        try:
            self._music().stop()
        except Exception:
            pass
        self._current_track = None
        self._record_track = None
        self._record_name = None
        self._record_position = None
        self._seconds_until_next = self._new_delay()

    def stop_record(self, position=None):
        if self._record_track is None:
            return False
        if position is not None and self._record_position is not None:
            requested = tuple(float(v) for v in position)
            if requested != self._record_position:
                return False
        try:
            self._music().stop()
        except Exception:
            pass
        self._record_track = None
        self._record_name = None
        self._record_position = None
        self._current_track = None
        self._seconds_until_next = self._new_delay()
        return True

    def update(self, dt, *, enabled=True, in_world=True, listener_position=None):
        try:
            music = self._music()
        except Exception:
            return

        if not enabled or not in_world:
            if self._current_track is not None or self._record_track is not None:
                try:
                    music.stop()
                except Exception:
                    pass
                self._current_track = None
                self._record_track = None
                self._record_name = None
                self._record_position = None
                self._seconds_until_next = self._new_delay()
            return

        if self._record_track is not None:
            try:
                if music.get_busy():
                    gain = self._base_volume()
                    if listener_position is not None and self._record_position is not None:
                        distance = math.sqrt(sum(
                            (float(listener_position[i]) - self._record_position[i]) ** 2
                            for i in range(3)
                        ))
                        gain *= max(0.0, 1.0 - distance / 64.0)
                    set_volume = getattr(music, "set_volume", None)
                    if callable(set_volume):
                        set_volume(max(0.0, min(1.0, gain)))
                    return
            except Exception:
                return
            self._record_track = None
            self._record_name = None
            self._record_position = None
            self._current_track = None
            self._seconds_until_next = self._new_delay()
            return

        if not self._paths:
            return

        if self._current_track is not None:
            try:
                if music.get_busy():
                    set_volume = getattr(music, "set_volume", None)
                    if callable(set_volume):
                        gain = self._base_volume() * max(0.0, min(1.0, float(self.ambient_gain)))
                        set_volume(max(0.0, min(1.0, gain)))
                    return
            except Exception:
                return
            self._current_track = None
            self._seconds_until_next = self._new_delay()

        self._seconds_until_next -= max(0.0, float(dt))
        if self._seconds_until_next > 0.0:
            return

        track = self.rng.choice(self._paths)
        try:
            music.load(str(track))
            set_volume = getattr(music, "set_volume", None)
            if callable(set_volume):
                gain = self._base_volume() * max(0.0, min(1.0, float(self.ambient_gain)))
                set_volume(max(0.0, min(1.0, gain)))
            music.play()
        except Exception:
            self._seconds_until_next = self._new_delay()
            return
        self._current_track = track
