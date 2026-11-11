from __future__ import annotations

import math
import threading
from io import BytesIO
from pathlib import Path
from typing import List, Optional, Sequence, Tuple
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen

import pygame as pg
from OpenGL.GL import (
    GL_ALPHA_TEST,
    GL_BLEND,
    GL_COMPILE,
    GL_CULL_FACE,
    GL_DEPTH_TEST,
    GL_EQUAL,
    GL_GREATER,
    GL_LEQUAL,
    GL_LIGHTING,
    GL_MODELVIEW,
    GL_NORMALIZE,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_PROJECTION,
    GL_QUADS,
    GL_SRC_ALPHA,
    GL_TEXTURE_2D,
    GL_TEXTURE_ENV,
    GL_TEXTURE_ENV_MODE,
    GL_MODULATE,
    GL_ALL_ATTRIB_BITS,
    glPushAttrib,
    glPopAttrib,
    glTexEnvi,
    glAlphaFunc,
    glBegin,
    glBlendFunc,
    glCallList,
    glColor4f,
    glDeleteLists,
    glDisable,
    glDepthFunc,
    glEnable,
    glEnd,
    glEndList,
    glGenLists,
    glLoadIdentity,
    glMatrixMode,
    glIsEnabled,
    glNewList,
    glNormal3f,
    glPopMatrix,
    glPushMatrix,
    glRotatef,
    glScalef,
    glTexCoord2f,
    glTranslatef,
    glVertex3f,
)

from engine.textures import Texture
from engine.legacy_lighting import (
    enable_standard_item_lighting, disable_standard_item_lighting,
)
from engine.lighting import brightness_from_level
from game.entities.player_cosmetics import (
    cape_body_attachment_pitch, cape_projection_yaw_for_world,
    cape_rotation_angles, deadmau5_ear_anchor,
)
from game.entities.cape_lookup import CapeLookupUnavailable, download_cape_bytes

_SKIN_W = 64.0
_SKIN_H = 32.0
_UNIT = 1.0 / 16.0

_skin_textures: dict[str, Texture] = {}
_skin_loaded: set[str] = set()
_skin_username = "Player"
_cape_textures: dict[str, Texture] = {}
_cape_texture_files: dict[str, Texture] = {}
_pending_cape_images: dict[str, bytes] = {}
_cape_fetching: set[str] = set()
_cape_fetch_lock = threading.Lock()
_cape_model: Optional["ModelPart"] = None
_pending_skin_images: dict[str, object] = {}
_skin_fetch_lock = threading.Lock()
_skin_fetching: set[str] = set()
_char_fallback: Optional[Texture] = None


def _char_texture_path() -> str:
    return "game/textures/char.png"


def _char_texture() -> Texture:
    global _char_fallback
    if _char_fallback is None:
        _char_fallback = Texture(_char_texture_path())
    return _char_fallback


def set_skin_username(username: str) -> None:
    global _skin_username
    if not username or not username.strip():
        raise ValueError("username must not be empty")
    _skin_username = username.strip()


def _mojang_username_to_uuid(username: str) -> str | None:
    url = f"https://api.mojang.com/users/profiles/minecraft/{quote(username, safe='')}"
    with urlopen(url, timeout=10) as response:
        import json
        payload = json.loads(response.read().decode("utf-8"))
    uuid = str(payload.get("id") or "").replace("-", "").strip()
    return uuid or None


def _mojang_uuid_to_skin_url(uuid: str) -> str | None:
    import base64
    import json
    url = f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid}"
    with urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    for prop in payload.get("properties") or ():
        if str(prop.get("name") or "") != "textures":
            continue
        raw = base64.b64decode(str(prop.get("value") or ""))
        textures = json.loads(raw.decode("utf-8"))
        skin = (textures.get("textures") or {}).get("SKIN") or {}
        skin_url = str(skin.get("url") or "").strip()
        if skin_url:
            return skin_url
    return None


def resolve_usernames_to_uuids(usernames: list[str]) -> dict[str, str]:
    import json
    result: dict[str, str] = {}
    cleaned = []
    seen = set()
    for name in usernames:
        key = str(name or "").strip()
        if not key:
            continue
        lower = key.lower()
        if lower in seen:
            continue
        seen.add(lower)
        cleaned.append(key[:16])
    for start in range(0, len(cleaned), 10):
        batch = cleaned[start:start + 10]
        try:
            from urllib.request import Request
            req = Request(
                "https://api.mojang.com/profiles/minecraft",
                data=json.dumps(batch).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, list):
                for entry in payload:
                    name = str(entry.get("name") or "").strip()
                    uuid = str(entry.get("id") or "").replace("-", "").strip()
                    if name and uuid:
                        result[name.lower()] = uuid
        except (OSError, URLError, ValueError, TypeError) as error:
            print(f"Batch UUID lookup failed: {error}")
    return result


SKIN_CACHE_TTL_SECONDS = 24 * 60 * 60
SKIN_MISS_TTL_SECONDS = 60 * 60
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_skin_cache_dir_override: Optional[Path] = None


def _skin_cache_dir() -> Path:
    if _skin_cache_dir_override is not None:
        return Path(_skin_cache_dir_override)
    from game.app.paths import user_data_root
    return user_data_root() / "skins"


def _skin_cache_paths(username: str) -> tuple[Path, Path]:
    import hashlib
    import re
    key = username.strip().casefold()
    safe = re.sub(r"[^a-z0-9_]", "_", key)[:32] or "_"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    base = _skin_cache_dir() / f"{safe}-{digest}"
    return base.with_suffix(".png"), base.with_suffix(".none")


def _file_age_seconds(path: Path) -> float | None:
    import time
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _read_skin_cache(username: str, *, allow_stale: bool = False) -> bytes | None:
    png, _ = _skin_cache_paths(username)
    age = _file_age_seconds(png)
    if age is None or (age > SKIN_CACHE_TTL_SECONDS and not allow_stale):
        return None
    try:
        data = png.read_bytes()
    except OSError:
        return None
    if not data.startswith(_PNG_SIGNATURE):
        try:
            png.unlink()
        except OSError:
            pass
        return None
    return data


def _write_skin_cache(username: str, data: bytes) -> None:
    import os
    if not data.startswith(_PNG_SIGNATURE):
        return
    png, miss = _skin_cache_paths(username)
    try:
        png.parent.mkdir(parents=True, exist_ok=True)
        tmp = png.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, png)
        miss.unlink(missing_ok=True)
    except OSError as error:
        print(f"Could not cache skin for {username!r}: {error}")


def _mark_skin_missing(username: str) -> None:
    _, miss = _skin_cache_paths(username)
    try:
        miss.parent.mkdir(parents=True, exist_ok=True)
        miss.write_bytes(b"")
    except OSError:
        pass


def _skin_recently_missing(username: str) -> bool:
    _, miss = _skin_cache_paths(username)
    age = _file_age_seconds(miss)
    return age is not None and age <= SKIN_MISS_TTL_SECONDS


def _cape_cache_paths(username: str) -> tuple[Path, Path]:
    png, miss = _skin_cache_paths(username)
    return png.with_name(png.stem + "-cape.png"), miss.with_name(miss.stem + "-cape-v2.none")


def _read_cape_cache(username: str, *, allow_stale: bool = False) -> bytes | None:
    png, _ = _cape_cache_paths(username)
    age = _file_age_seconds(png)
    if age is None or (age > SKIN_CACHE_TTL_SECONDS and not allow_stale):
        return None
    try:
        data = png.read_bytes()
    except OSError:
        return None
    if not data.startswith(_PNG_SIGNATURE):
        try:
            png.unlink()
        except OSError:
            pass
        return None
    return data


def _write_cape_cache(username: str, data: bytes) -> None:
    import os
    if not data.startswith(_PNG_SIGNATURE):
        return
    png, miss = _cape_cache_paths(username)
    try:
        png.parent.mkdir(parents=True, exist_ok=True)
        tmp = png.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, png)
        miss.unlink(missing_ok=True)
        _clear_cape_lookup_failure(username)
    except OSError as error:
        print(f"Could not cache cape for {username!r}: {error}")


def _cape_recently_missing(username: str) -> bool:
    _, miss = _cape_cache_paths(username)
    age = _file_age_seconds(miss)
    return age is not None and age <= SKIN_MISS_TTL_SECONDS


def _mark_cape_missing(username: str) -> None:
    png, miss = _cape_cache_paths(username)
    try:
        png.unlink(missing_ok=True)
        miss.parent.mkdir(parents=True, exist_ok=True)
        miss.write_bytes(b"")
    except OSError:
        pass
    _clear_cape_lookup_failure(username)


CAPE_LOOKUP_RETRY_SECONDS = 5 * 60


def _cape_retry_path(username: str) -> Path:
    png, _miss = _cape_cache_paths(username)
    return png.with_suffix(".retry")


def _cape_lookup_recently_failed(username: str) -> bool:
    age = _file_age_seconds(_cape_retry_path(username))
    return age is not None and age <= CAPE_LOOKUP_RETRY_SECONDS


def _mark_cape_lookup_failed(username: str) -> None:
    marker = _cape_retry_path(username)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(b"")
    except OSError:
        pass


def _clear_cape_lookup_failure(username: str) -> None:
    try:
        _cape_retry_path(username).unlink(missing_ok=True)
    except OSError:
        pass


def _download_cape_bytes(username: str) -> bytes | None:
    return download_cape_bytes(username, timeout=5.0)


def _fetch_cape_worker(username: str) -> None:
    data = None
    try:
        data = _read_cape_cache(username)
        if data is None:
            stale = _read_cape_cache(username, allow_stale=True)
            if _cape_recently_missing(username):
                data = None
            else:
                try:
                    data = _download_cape_bytes(username)
                except CapeLookupUnavailable as error:
                    if not _cape_lookup_recently_failed(username):
                        print(f"Cape lookup temporarily unavailable for {username!r}: {error}")
                    _mark_cape_lookup_failed(username)
                    data = stale
                else:
                    _clear_cape_lookup_failure(username)
                    if data:
                        _write_cape_cache(username, data)
                    else:
                        _mark_cape_missing(username)
                        data = None
    except Exception as error:
        print(f"Cape cache/worker failed for {username!r}: {error}")
        _mark_cape_lookup_failed(username)
        data = _read_cape_cache(username, allow_stale=True)
    finally:
        with _cape_fetch_lock:
            _cape_fetching.discard(username)
            if data:
                _pending_cape_images[username] = data


def get_cape_texture(username: Optional[str] = None) -> Optional[Texture]:
    username = _skin_username if username is None else str(username).strip()
    if not username:
        return None

    with _cape_fetch_lock:
        pending = _pending_cape_images.pop(username, None)
    if pending is not None:
        try:
            cape_image = pg.image.load(BytesIO(pending), "cape.png")
            if cape_image.get_width() >= 64 and cape_image.get_height() >= 32:
                cape_image = cape_image.subsurface((0, 0, 64, 32)).copy()
                _cape_textures[username] = Texture(cape_image)
            else:
                raise ValueError(
                    f"cape image is {cape_image.get_width()}x{cape_image.get_height()}, expected at least 64x32"
                )
        except Exception as error:
            print(f"Failed to upload cape for {username!r}: {error}")
            png, _miss = _cape_cache_paths(username)
            try:
                png.unlink(missing_ok=True)
            except OSError:
                pass
            _mark_cape_missing(username)

    if username in _cape_textures:
        return _cape_textures[username]

    with _cape_fetch_lock:
        should_fetch = username not in _cape_fetching
        if (
            should_fetch
            and not _cape_recently_missing(username)
            and not _cape_lookup_recently_failed(username)
        ):
            _cape_fetching.add(username)
        else:
            should_fetch = False
    if should_fetch:
        threading.Thread(
            target=_fetch_cape_worker,
            args=(username,),
            name=f"cape-{username}",
            daemon=True,
        ).start()
    return None


def _download_skin_bytes(username: str) -> bytes | None:
    data = None
    sources = []

    try:
        uuid = _mojang_username_to_uuid(username)
        skin_url = _mojang_uuid_to_skin_url(uuid) if uuid else None
        if skin_url:
            sources.append(("Mojang", skin_url))
    except (OSError, URLError, ValueError, TypeError, KeyError) as error:
        print(f"Mojang skin lookup failed for {username!r}: {error}")

    safe = quote(username, safe="")
    sources.append(("mineskin", f"https://mineskin.eu/skin/{safe}"))
    sources.append(("minotar", f"https://minotar.net/skin/{safe}"))

    for label, url in sources:
        if data:
            break
        try:
            print(f"Loading texture {url} ({label})")
            with urlopen(url, timeout=10) as response:
                candidate = response.read()
            if candidate and len(candidate) >= 64 and candidate[:8] != b"<!DOCTYPE" and candidate[:5] != b"<html":
                data = candidate
        except (OSError, URLError) as error:
            print(f"Skin source {label} failed for {username!r}: {error}")
    return data


def _fetch_skin_worker(username: str) -> None:
    data = _read_skin_cache(username)
    if data is None:
        stale = _read_skin_cache(username, allow_stale=True)
        if _skin_recently_missing(username):
            data = stale
        else:
            data = _download_skin_bytes(username)
            if data:
                _write_skin_cache(username, data)
            else:
                _mark_skin_missing(username)
                data = stale
                if stale:
                    print(f"Using cached skin for {username!r} (network unavailable)")
    if not data:
        print(f"Unable to load skin for {username!r}; using char.png")
    with _skin_fetch_lock:
        _skin_fetching.discard(username)
        if data:
            _pending_skin_images[username] = data


def get_skin_texture(username: Optional[str] = None) -> Texture:
    username = _skin_username if username is None else username.strip()
    if not username:
        raise ValueError("username must not be empty")

    with _skin_fetch_lock:
        pending = _pending_skin_images.pop(username, None)
    if pending is not None:
        try:
            if isinstance(pending, (bytes, bytearray)):
                skin_image = pg.image.load(BytesIO(pending), "skin.png")
            else:
                skin_image = pending
            if skin_image.get_width() >= 64 and skin_image.get_height() >= 32:
                skin_image = skin_image.subsurface((0, 0, 64, 32)).copy()
                _skin_textures[username] = Texture(skin_image)
                _skin_loaded.add(username)
            else:
                _skin_loaded.discard(username)
                print(
                    f"Skin for {username!r} is {skin_image.get_width()}x"
                    f"{skin_image.get_height()}; expected at least 64x32"
                )
                _skin_textures[username] = Texture(
                    _char_texture_path()
                )
        except Exception as error:
            _skin_loaded.discard(username)
            print(f"Failed to upload skin for {username!r}: {error}")
            _skin_textures[username] = Texture(
                _char_texture_path()
            )

    if username in _skin_textures:
        return _skin_textures[username]

    _skin_textures[username] = _char_texture()
    with _skin_fetch_lock:
        if username not in _skin_fetching:
            _skin_fetching.add(username)
            threading.Thread(
                target=_fetch_skin_worker,
                args=(username,),
                name=f"skin-{username}",
                daemon=True,
            ).start()
    return _skin_textures[username]


def _uv(px_u: float, px_v: float) -> Tuple[float, float]:
    return (px_u / _SKIN_W, 1.0 - px_v / _SKIN_H)


class ModelPart:
    def __init__(
        self,
        tex_u: int,
        tex_v: int,
        off_x: float,
        off_y: float,
        off_z: float,
        size_x: int,
        size_y: int,
        size_z: int,
        *,
        inflate: float = 0.0,
        mirror: bool = False,
        texture_width: int = _SKIN_W,
        texture_height: int = _SKIN_H,
    ):
        self.rotation_point = (0.0, 0.0, 0.0)
        self.rotate_angle_x = 0.0
        self.rotate_angle_y = 0.0
        self.rotate_angle_z = 0.0
        self.show_model = True
        self.children = []
        self.mirror = mirror
        self._faces = self._build_faces(
            tex_u, tex_v, off_x, off_y, off_z, size_x, size_y, size_z,
            inflate, mirror, texture_width, texture_height,
        )
        self._face_normals = [self._normal_for_face(face) for face in self._faces]
        self._display_list = None

    def set_rotation_point(self, x: float, y: float, z: float) -> None:
        self.rotation_point = (x, y, z)

    def _ensure_display_list(self) -> None:
        if self._display_list is not None:
            return
        try:
            list_id = int(glGenLists(1))
        except Exception:
            self._display_list = 0
            return
        if list_id == 0:
            self._display_list = 0
            return
        glNewList(list_id, GL_COMPILE)
        glBegin(GL_QUADS)
        for face, normal in zip(self._faces, self._face_normals):
            glNormal3f(*normal)
            for (x, y, z), (uu, vv) in face:
                glTexCoord2f(uu, vv)
                glVertex3f(x, y, z)
        glEnd()
        glEndList()
        self._display_list = list_id

    @staticmethod
    def _build_faces(
        u: int,
        v: int,
        ox: float,
        oy: float,
        oz: float,
        w: int,
        h: int,
        d: int,
        inflate: float,
        mirror: bool,
        texture_width: int = _SKIN_W,
        texture_height: int = _SKIN_H,
    ) -> List[List[Tuple[Tuple[float, float, float], Tuple[float, float]]]]:
        x0 = ox - inflate
        y0 = oy - inflate
        z0 = oz - inflate
        x1 = ox + w + inflate
        y1 = oy + h + inflate
        z1 = oz + d + inflate

        if mirror:
            x0, x1 = x1, x0

        c0 = (x0, y0, z0)
        c1 = (x1, y0, z0)
        c2 = (x1, y1, z0)
        c3 = (x0, y1, z0)
        c4 = (x0, y0, z1)
        c5 = (x1, y0, z1)
        c6 = (x1, y1, z1)
        c7 = (x0, y1, z1)

        def face(
            verts: Sequence[Tuple[float, float, float]],
            u0: float,
            v0: float,
            u1: float,
            v1: float,
        ):
            u_eps = 0.1
            v_eps = 0.1
            def tex_uv(px_u, px_v):
                return (px_u / float(texture_width), 1.0 - px_v / float(texture_height))

            uvs = [
                tex_uv(u1 - u_eps, v0 + v_eps),
                tex_uv(u0 + u_eps, v0 + v_eps),
                tex_uv(u0 + u_eps, v1 - v_eps),
                tex_uv(u1 - u_eps, v1 - v_eps),
            ]
            return list(zip(verts, uvs))

        faces = [
            face([c5, c1, c2, c6], u + d + w, v + d, u + d + w + d, v + d + h),
            face([c0, c4, c7, c3], u + 0, v + d, u + d, v + d + h),
            face([c5, c4, c0, c1], u + d, v + 0, u + d + w, v + d),
            face([c2, c3, c7, c6], u + d + w, v + 0, u + d + w + w, v + d),
            face([c1, c0, c3, c2], u + d, v + d, u + d + w, v + d + h),
            face([c4, c5, c6, c7], u + d + w + d, v + d, u + d + w + d + w, v + d + h),
        ]

        if mirror:
            faces = [list(reversed(f)) for f in faces]

        return faces

    @staticmethod
    def _normal_for_face(face):
        p0 = face[0][0]
        p1 = face[1][0]
        p2 = face[2][0]
        a = tuple(p1[i] - p2[i] for i in range(3))
        b = tuple(p1[i] - p0[i] for i in range(3))
        normal = (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )
        length = math.sqrt(sum(value * value for value in normal))
        if length <= 1e-12:
            return (0.0, 0.0, 1.0)
        return tuple(value / length for value in normal)

    def render(self, scale: float = _UNIT) -> None:
        if not self.show_model:
            return
        glPushMatrix()
        rx, ry, rz = self.rotation_point
        glTranslatef(rx * scale, ry * scale, rz * scale)
        if self.rotate_angle_z:
            glRotatef(math.degrees(self.rotate_angle_z), 0.0, 0.0, 1.0)
        if self.rotate_angle_y:
            glRotatef(math.degrees(self.rotate_angle_y), 0.0, 1.0, 0.0)
        if self.rotate_angle_x:
            glRotatef(math.degrees(self.rotate_angle_x), 1.0, 0.0, 0.0)

        self._ensure_display_list()
        list_id = self._display_list
        if list_id:
            if scale != 1.0:
                glPushMatrix()
                glScalef(scale, scale, scale)
                glCallList(list_id)
                glPopMatrix()
            else:
                glCallList(list_id)
        else:
            glBegin(GL_QUADS)
            for face, normal in zip(self._faces, self._face_normals):
                glNormal3f(*normal)
                for (x, y, z), (uu, vv) in face:
                    glTexCoord2f(uu, vv)
                    glVertex3f(x * scale, y * scale, z * scale)
            glEnd()
        for child in self.children:
            child.render(scale)
        glPopMatrix()


class ModelBiped:
    def __init__(self, inflate: float = 0.0, y_offset: float = 0.0):
        self.swing_progress = 0.0
        self.held_item_right = False
        self.is_sneak = False
        self._base_y_offset = float(y_offset)
        self.biped_head = ModelPart(0, 0, -4, -8, -4, 8, 8, 8, inflate=inflate)
        self.biped_head.set_rotation_point(0.0, 0.0 + y_offset, 0.0)

        self.biped_headwear = ModelPart(32, 0, -4, -8, -4, 8, 8, 8, inflate=inflate + 0.5)
        self.biped_headwear.set_rotation_point(0.0, 0.0 + y_offset, 0.0)

        self.deadmau5_ears = ModelPart(24, 0, -3, -6, -1, 6, 6, 1)

        self.biped_body = ModelPart(16, 16, -4, 0, -2, 8, 12, 4, inflate=inflate)
        self.biped_body.set_rotation_point(0.0, 0.0 + y_offset, 0.0)

        self.biped_right_arm = ModelPart(40, 16, -3, -2, -2, 4, 12, 4, inflate=inflate)
        self.biped_right_arm.set_rotation_point(-5.0, 2.0 + y_offset, 0.0)

        self.biped_left_arm = ModelPart(
            40, 16, -1, -2, -2, 4, 12, 4, inflate=inflate, mirror=True
        )
        self.biped_left_arm.set_rotation_point(5.0, 2.0 + y_offset, 0.0)

        self.biped_right_leg = ModelPart(0, 16, -2, 0, -2, 4, 12, 4, inflate=inflate)
        self.biped_right_leg.set_rotation_point(-2.0, 12.0 + y_offset, 0.0)

        self.biped_left_leg = ModelPart(
            0, 16, -2, 0, -2, 4, 12, 4, inflate=inflate, mirror=True
        )
        self.biped_left_leg.set_rotation_point(2.0, 12.0 + y_offset, 0.0)

        self.parts = [
            self.biped_head,
            self.biped_body,
            self.biped_right_arm,
            self.biped_left_arm,
            self.biped_right_leg,
            self.biped_left_leg,
            self.biped_headwear,
        ]

    def set_rotation_angles(
        self,
        limb_swing: float,
        limb_swing_amount: float,
        age_in_ticks: float,
        net_head_yaw: float,
        head_pitch: float,
        *,
        raised_arms: bool = False,
        is_riding: bool = False,
        is_sneak: bool = False,
    ) -> None:
        self.biped_head.rotate_angle_y = math.radians(net_head_yaw)
        self.biped_head.rotate_angle_x = math.radians(head_pitch)
        self.biped_headwear.rotate_angle_y = self.biped_head.rotate_angle_y
        self.biped_headwear.rotate_angle_x = self.biped_head.rotate_angle_x

        self.biped_right_arm.rotate_angle_x = (
            math.cos(limb_swing * 0.6662 + math.pi) * 2.0 * limb_swing_amount * 0.5
        )
        self.biped_right_arm.rotate_angle_y = 0.0
        self.biped_right_arm.rotate_angle_z = 0.0
        self.biped_left_arm.rotate_angle_x = (
            math.cos(limb_swing * 0.6662) * 2.0 * limb_swing_amount * 0.5
        )
        self.biped_left_arm.rotate_angle_y = 0.0
        self.biped_left_arm.rotate_angle_z = 0.0
        self.biped_right_leg.rotate_angle_x = math.cos(limb_swing * 0.6662) * 1.4 * limb_swing_amount
        self.biped_right_leg.rotate_angle_y = 0.0
        self.biped_right_leg.rotate_angle_z = 0.0
        self.biped_left_leg.rotate_angle_x = (
            math.cos(limb_swing * 0.6662 + math.pi) * 1.4 * limb_swing_amount
        )
        self.biped_left_leg.rotate_angle_y = 0.0
        self.biped_left_leg.rotate_angle_z = 0.0

        if is_riding:
            self.biped_right_arm.rotate_angle_x += math.pi * -0.2
            self.biped_left_arm.rotate_angle_x += math.pi * -0.2
            self.biped_right_leg.rotate_angle_x = math.pi * -0.4
            self.biped_left_leg.rotate_angle_x = math.pi * -0.4
            self.biped_right_leg.rotate_angle_y = math.pi * 0.1
            self.biped_left_leg.rotate_angle_y = math.pi * -0.1

        if raised_arms:
            self.biped_right_arm.rotate_angle_y = 0.0
            self.biped_left_arm.rotate_angle_y = 0.0
            self.biped_right_arm.rotate_angle_z = 0.0
            self.biped_left_arm.rotate_angle_z = 0.0
            self.biped_right_arm.rotate_angle_x = -math.pi / 2.0
            self.biped_left_arm.rotate_angle_x = -math.pi / 2.0

        if self.held_item_right and not raised_arms:
            self.biped_right_arm.rotate_angle_x = (
                self.biped_right_arm.rotate_angle_x * 0.5 - math.pi * 0.1
            )

        swing = float(self.swing_progress)
        right_arm, left_arm = self.biped_right_arm, self.biped_left_arm
        right_arm.rotate_angle_y = 0.0
        left_arm.rotate_angle_y = 0.0
        body_twist = math.sin(math.sqrt(max(swing, 0.0)) * math.pi * 2.0) * 0.2
        self.biped_body.rotate_angle_y = body_twist
        y_off = self._base_y_offset
        right_arm.set_rotation_point(
            -math.cos(body_twist) * 5.0, 2.0 + y_off,
            math.sin(body_twist) * 5.0,
        )
        left_arm.set_rotation_point(
            math.cos(body_twist) * 5.0, 2.0 + y_off,
            -math.sin(body_twist) * 5.0,
        )
        right_arm.rotate_angle_y += body_twist
        left_arm.rotate_angle_y += body_twist
        left_arm.rotate_angle_x += body_twist
        eased = 1.0 - swing
        eased = 1.0 - eased * eased * eased * eased
        arc = math.sin(eased * math.pi)
        lift = (
            math.sin(swing * math.pi)
            * -(self.biped_head.rotate_angle_x - 0.7) * (12.0 / 16.0)
        )
        right_arm.rotate_angle_x -= arc * 1.2 + lift
        right_arm.rotate_angle_y += body_twist * 2.0
        right_arm.rotate_angle_z = math.sin(swing * math.pi) * -0.4

        if is_sneak or self.is_sneak:
            self.biped_body.rotate_angle_x = 0.5
            self.biped_right_arm.rotate_angle_x += 0.4
            self.biped_left_arm.rotate_angle_x += 0.4
            self.biped_right_leg.set_rotation_point(-2.0, 9.0 + y_off, 4.0)
            self.biped_left_leg.set_rotation_point(2.0, 9.0 + y_off, 4.0)
            self.biped_head.set_rotation_point(0.0, 1.0 + y_off, 0.0)
            self.biped_headwear.set_rotation_point(0.0, 1.0 + y_off, 0.0)
        else:
            self.biped_body.rotate_angle_x = 0.0
            self.biped_right_leg.set_rotation_point(-2.0, 12.0 + y_off, 0.0)
            self.biped_left_leg.set_rotation_point(2.0, 12.0 + y_off, 0.0)
            self.biped_head.set_rotation_point(0.0, 0.0 + y_off, 0.0)
            self.biped_headwear.set_rotation_point(0.0, 0.0 + y_off, 0.0)

        self.biped_right_arm.rotate_angle_z += math.cos(age_in_ticks * 0.09) * 0.05 + 0.05
        self.biped_left_arm.rotate_angle_z -= math.cos(age_in_ticks * 0.09) * 0.05 + 0.05
        self.biped_right_arm.rotate_angle_x += math.sin(age_in_ticks * 0.067) * 0.05
        self.biped_left_arm.rotate_angle_x -= math.sin(age_in_ticks * 0.067) * 0.05

    def render(
        self,
        limb_swing: float,
        limb_swing_amount: float,
        age_in_ticks: float,
        net_head_yaw: float,
        head_pitch: float,
        scale: float = _UNIT,
        *,
        raised_arms: bool = False,
        is_riding: bool = False,
        is_sneak: bool = False,
    ) -> None:
        self.set_rotation_angles(
            limb_swing, limb_swing_amount, age_in_ticks, net_head_yaw, head_pitch,
            raised_arms=raised_arms,
            is_riding=is_riding,
            is_sneak=is_sneak,
        )
        for part in self.parts:
            part.render(scale)

    def render_right_arm(self, scale: float = _UNIT) -> None:
        self.biped_right_arm.render(scale)


_model_main: Optional[ModelBiped] = None
_model_armor_chest: Optional[ModelBiped] = None
_model_armor_legs: Optional[ModelBiped] = None
_armor_textures: dict[str, Texture] = {}

_ARMOR_MATERIAL_PREFIX = {
    "leather": "cloth",
    "chain": "chain",
    "iron": "iron",
    "diamond": "diamond",
    "gold": "gold",
}


def _get_main_model() -> ModelBiped:
    global _model_main
    if _model_main is None:
        _model_main = ModelBiped(0.0)
    return _model_main


def _get_cape_model() -> ModelPart:
    global _cape_model
    if _cape_model is None:
        _cape_model = ModelPart(
            0, 0, -5, 0, -1, 10, 16, 1,
            texture_width=64, texture_height=32,
        )
    return _cape_model


def _get_armor_chest_model() -> ModelBiped:
    global _model_armor_chest
    if _model_armor_chest is None:
        _model_armor_chest = ModelBiped(1.0)
    return _model_armor_chest


def _get_armor_legs_model() -> ModelBiped:
    global _model_armor_legs
    if _model_armor_legs is None:
        _model_armor_legs = ModelBiped(0.5)
    return _model_armor_legs


def _armor_texture(prefix: str, layer: int) -> Optional[Texture]:
    key = f"{prefix}_{layer}"
    if key not in _armor_textures:
        from game.app.texturepacks import resolve_texture_path
        path = f"game/textures/armor/{key}.png"
        if not Path(resolve_texture_path(path)).is_file():
            return None
        _armor_textures[key] = Texture(path)
    return _armor_textures[key]


_entity_skin_files: dict[str, Texture] = {}


def resolve_skin_texture(entity=None) -> Texture:
    if entity is not None:
        tex = getattr(entity, "skin_texture", None)
        if tex is not None:
            return tex
        username = getattr(entity, "skin_username", None)
        if isinstance(username, str) and username.strip():
            return get_skin_texture(username)
        rel_path = getattr(entity, "skin_path", None)
        if isinstance(rel_path, str) and rel_path:
            if rel_path not in _entity_skin_files:
                _entity_skin_files[rel_path] = Texture(
                    f"game/textures/{rel_path}"
                )
            return _entity_skin_files[rel_path]
    return get_skin_texture()


def resolve_cape_texture(entity=None) -> Optional[Texture]:
    if entity is None:
        return None
    texture = getattr(entity, "cape_texture", None)
    if texture is not None and callable(getattr(texture, "bind", None)):
        return texture

    rel_path = getattr(entity, "cape_path", None)
    if isinstance(rel_path, str) and rel_path.strip():
        rel_path = rel_path.strip().replace("\\", "/")
        resource = rel_path if rel_path.startswith("game/") else f"game/textures/{rel_path}"
        try:
            from game.app.texturepacks import resolve_texture_path
            resolved = str(resolve_texture_path(resource))
            if Path(resolved).is_file():
                if resolved not in _cape_texture_files:
                    _cape_texture_files[resolved] = Texture(resource)
                return _cape_texture_files[resolved]
        except (OSError, TypeError, ValueError):
            return None

    username = getattr(entity, "skin_username", None)
    if isinstance(username, str) and username.strip():
        return get_cape_texture(username)
    return None


def _bind_skin(entity=None) -> None:
    tex = resolve_skin_texture(entity)
    glEnable(GL_TEXTURE_2D)
    tex.bind()
    glColor4f(1.0, 1.0, 1.0, 1.0)
    glEnable(GL_ALPHA_TEST)
    glAlphaFunc(GL_GREATER, 0.1)


def _set_armor_part_visibility(model: ModelBiped, pass_index: int) -> None:
    model.biped_head.show_model = pass_index == 0
    model.biped_headwear.show_model = pass_index == 0
    model.biped_body.show_model = pass_index in (1, 2)
    model.biped_right_arm.show_model = pass_index == 1
    model.biped_left_arm.show_model = pass_index == 1
    model.biped_right_leg.show_model = pass_index in (2, 3)
    model.biped_left_leg.show_model = pass_index in (2, 3)


def _render_armor_passes(
    player,
    limb_swing: float,
    limb_amount: float,
    age: float,
    net_head: float,
    head_pitch: float,
    brightness: float = 1.0,
    *,
    overlay: bool = False,
    is_riding: bool = False,
    is_sneak: bool = False,
    partial: float = 0.0,
) -> None:
    inventory = getattr(player, "inventory", None)
    if inventory is None:
        return
    armor = getattr(inventory, "armor", None)
    if not armor:
        return

    from game.entities.item import Item

    for pass_index in range(4):
        stack = armor[pass_index] if pass_index < len(armor) else None
        if stack is None:
            continue
        defn = Item.item_defs.get(stack.id) if hasattr(Item, "item_defs") else None
        if not defn or defn.get("type") != "armor":
            continue
        material = str(defn.get("material", "iron"))
        prefix = _ARMOR_MATERIAL_PREFIX.get(material)
        if prefix is None:
            continue
        layer = 2 if pass_index == 2 else 1
        tex = _armor_texture(prefix, layer)
        if tex is None:
            continue
        model = _get_armor_legs_model() if pass_index == 2 else _get_armor_chest_model()
        _set_armor_part_visibility(model, pass_index)
        _apply_player_pose(model, player, partial)
        if not overlay:
            glEnable(GL_TEXTURE_2D)
            tex.bind()
            glEnable(GL_ALPHA_TEST)
            glAlphaFunc(GL_GREATER, 0.1)
            glColor4f(brightness, brightness, brightness, 1.0)
        model.render(
            limb_swing, limb_amount, age, net_head, head_pitch, _UNIT,
            is_riding=is_riding,
            is_sneak=is_sneak,
        )


def _player_anim(player, partial: float):
    current_amount = float(getattr(player, "limb_swing_amount", 0.0))
    previous_amount = float(getattr(player, "prev_limb_swing_amount", current_amount))
    limb_amount = previous_amount + (current_amount - previous_amount) * partial
    limb_amount = min(1.0, max(0.0, limb_amount))

    limb_swing = float(getattr(player, "limb_swing", 0.0))
    limb_swing -= current_amount * (1.0 - partial)

    age = float(getattr(player, "ticks_existed", 0.0)) + partial
    return limb_swing, limb_amount, age


_PLAYER_SCALE = 15.0 / 16.0


def _player_swing_progress(player, partial: float) -> float:
    current = float(getattr(player, "swing_progress", 0.0))
    previous = float(getattr(player, "prev_swing_progress", current))
    diff = current - previous
    if diff < 0.0:
        diff += 1.0
    return previous + diff * partial


def _has_held_item(player) -> bool:
    inventory = getattr(player, "inventory", None)
    if inventory is None:
        return False
    try:
        return inventory.get_active_item() is not None
    except Exception:
        return False


def _apply_player_pose(model, player, partial: float) -> None:
    sleeping = bool(getattr(player, "is_sleeping", False))
    model.swing_progress = 0.0 if sleeping else _player_swing_progress(player, partial)
    model.held_item_right = (
        should_render_player_held_item(player) and _has_held_item(player)
    )


def _view_bob_values(player, partial: float):
    def interp(name, prev_name):
        cur = float(getattr(player, name, 0.0))
        prev = float(getattr(player, prev_name, cur))
        return prev + (cur - prev) * partial

    return (
        interp("distance_walked_modified", "prev_distance_walked_modified"),
        interp("camera_yaw", "prev_camera_yaw"),
        interp("camera_pitch", "prev_camera_pitch"),
    )


def _apply_view_bobbing(player, partial: float) -> None:
    settings = getattr(player, "settings", None)
    if settings is not None and not getattr(settings, "view_bobbing", True):
        return
    walk, camera_yaw, camera_pitch = _view_bob_values(player, partial)
    phase = walk * math.pi
    glTranslatef(math.sin(phase) * camera_yaw * 0.5,
                 -abs(math.cos(phase) * camera_yaw), 0.0)
    glRotatef(math.sin(phase) * camera_yaw * 3.0, 0.0, 0.0, 1.0)
    glRotatef(abs(math.cos(phase + 0.2) * camera_yaw) * 5.0,
              1.0, 0.0, 0.0)
    glRotatef(camera_pitch, 1.0, 0.0, 0.0)


def apply_view_bobbing(player, partial: float = 0.0) -> None:
    _apply_view_bobbing(player, partial)


def apply_hurt_camera_effect(player, partial: float = 0.0) -> None:
    partial = float(partial)
    death_partial = float(getattr(player, "death_animation_partial", partial))
    hurt_partial = float(getattr(player, "hurt_animation_partial", partial))
    if getattr(player, "health", 20) <= 0:
        death_time = float(getattr(player, "death_time", 0.0)) + death_partial
        angle = 40.0 - 8000.0 / (death_time + 200.0)
        glRotatef(angle, 0.0, 0.0, 1.0)

    hurt_time = float(getattr(player, "hurt_time", 0.0)) - hurt_partial
    max_hurt_time = max(1.0, float(getattr(player, "max_hurt_time", 10.0)))
    if hurt_time < 0.0:
        return
    hurt = max(0.0, min(1.0, hurt_time / max_hurt_time))
    hurt = math.sin(hurt * hurt * hurt * hurt * math.pi)
    attacked_yaw = float(getattr(player, "attacked_at_yaw", 0.0))
    glRotatef(-attacked_yaw, 0.0, 1.0, 0.0)
    glRotatef(-hurt * 14.0, 0.0, 0.0, 1.0)
    glRotatef(attacked_yaw, 0.0, 1.0, 0.0)


def begin_first_person_camera_transform(
    player, partial: float = 0.0, *, eye_offset: float = 0.0,
) -> None:
    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()
    if eye_offset:
        glTranslatef(-float(eye_offset), 0.0, 0.0)
    apply_hurt_camera_effect(player, partial)
    _apply_view_bobbing(player, partial)


def orient_first_person_camera(player) -> None:
    glRotatef(-float(player.pitch), 1.0, 0.0, 0.0)
    glRotatef(-float(player.yaw), 0.0, 1.0, 0.0)


def apply_first_person_camera_transform(
    player, partial: float = 0.0, *, inventory_open: bool = False,
) -> None:
    begin_first_person_camera_transform(player, partial)
    orient_first_person_camera(player)



_HELD_3D_BLOCK_MODELS = frozenset({
    "cube", "slab", "farmland", "snow_layer", "pressure_plate", "button", "stairs", "cactus", "fence",
})


def _held_block_uses_3d(defn) -> bool:
    if not defn:
        return False
    return defn.get("model", "cube") in _HELD_3D_BLOCK_MODELS


def _held_item_uses_3d_block(item_id, item_api) -> bool:
    if item_id in ("cake", "bed", "redstone_repeater"):
        return False
    definition = getattr(item_api, "block_defs", {}).get(item_id)
    return bool(
        definition
        and _held_block_uses_3d(definition)
        and getattr(item_api, "block_texture", None) is not None
    )


def _render_third_person_held_item(player, model, brightness: float) -> None:
    if not should_render_player_held_item(player):
        return
    pushed = False
    try:
        from game.entities.item import Item
        inventory = getattr(player, "inventory", None)
        if inventory is not None:
            held = inventory.get_active_item()
        else:
            held = getattr(player, "_equipped_item", None)
        hook = getattr(player, "fishing_hook", None)
        active_fishing_cast = hook is not None and not getattr(hook, "dead", True)
        if active_fishing_cast:
            from game.entities.item import ItemStack
            held = ItemStack("stick", 1)
        if held is None:
            return
        arm = model.biped_right_arm
        glPushMatrix()
        pushed = True
        rx, ry, rz = arm.rotation_point
        glTranslatef(rx * _UNIT, ry * _UNIT, rz * _UNIT)
        if arm.rotate_angle_z:
            glRotatef(math.degrees(arm.rotate_angle_z), 0.0, 0.0, 1.0)
        if arm.rotate_angle_y:
            glRotatef(math.degrees(arm.rotate_angle_y), 0.0, 1.0, 0.0)
        if arm.rotate_angle_x:
            glRotatef(math.degrees(arm.rotate_angle_x), 1.0, 0.0, 0.0)
        glTranslatef(-1.0 * _UNIT, 7.0 * _UNIT, 1.0 * _UNIT)
        glColor4f(brightness, brightness, brightness, 1.0)

        is_block = held.id in getattr(Item, "block_defs", {}) and held.id not in ("cake", "bed", "redstone_repeater")
        defn = Item.block_defs.get(held.id) if is_block else None
        use_3d_block = (
            is_block and _held_block_uses_3d(defn)
            and Item.block_texture is not None
        )
        item_def = Item.item_defs.get(held.id, {})
        hold = item_def.get("hold")
        if active_fishing_cast:
            full_3d_item = True
        elif hold in ("tool", "generic"):
            full_3d_item = hold == "tool"
        else:
            tool_type = item_def.get("tooltype")
            full_3d_item = (
                item_def.get("type") == "melee_weapon"
                or (item_def.get("type") == "tool" and tool_type in {
                    "axe", "hoe", "pickaxe", "shovel",
                })
            )

        if use_3d_block:
            glTranslatef(0.0, 3.0 * _UNIT, -5.0 * _UNIT)
            scale = 0.5 * (12.0 / 16.0)
            glRotatef(20.0, 1.0, 0.0, 0.0)
            glRotatef(45.0, 0.0, 1.0, 0.0)
            glScalef(scale, -scale, scale)
            glTranslatef(-0.5, -0.5, -0.5)
            if defn.get("model") == "stairs":
                _draw_held_stairs(player, held, brightness=brightness)
            elif defn.get("model") == "fence":
                _draw_held_fence(player, held, brightness=brightness)
            else:
                _draw_held_block_cube(player, held, brightness=brightness)
        elif full_3d_item:
            if item_def.get("rotate_around", False) or active_fishing_cast:
                glRotatef(180.0, 0.0, 0.0, 1.0)
                glTranslatef(0.0, -2.0 * _UNIT, 0.0)
            scale = 10.0 / 16.0
            glTranslatef(0.0, 3.0 * _UNIT, 0.0)
            glScalef(scale, -scale, scale)
            glRotatef(-100.0, 1.0, 0.0, 0.0)
            glRotatef(45.0, 0.0, 1.0, 0.0)
            _draw_held_sprite(held, brightness=brightness)
        else:
            scale = 6.0 / 16.0
            glTranslatef(0.25, 3.0 * _UNIT, -3.0 * _UNIT)
            glScalef(scale, scale, scale)
            glRotatef(60.0, 0.0, 0.0, 1.0)
            glRotatef(-90.0, 1.0, 0.0, 0.0)
            glRotatef(20.0, 0.0, 0.0, 1.0)
            _draw_held_sprite(held, brightness=brightness)
    except Exception:
        pass
    finally:
        if pushed:
            glPopMatrix()


def _render_pumpkin_helmet(player, model, brightness: float) -> None:
    inventory = getattr(player, "inventory", None)
    if inventory is None:
        return
    armor = getattr(inventory, "armor", None)
    if not armor:
        return
    helmet = armor[0] if len(armor) > 0 else None
    if helmet is None or getattr(helmet, "id", None) != "pumpkin":
        return
    from game.entities.item import Item
    if Item.block_texture is None or "pumpkin" not in Item.block_defs:
        return

    head = model.biped_head
    glPushMatrix()
    try:
        rx, ry, rz = head.rotation_point
        glTranslatef(rx * _UNIT, ry * _UNIT, rz * _UNIT)
        if head.rotate_angle_z:
            glRotatef(math.degrees(head.rotate_angle_z), 0.0, 0.0, 1.0)
        if head.rotate_angle_y:
            glRotatef(math.degrees(head.rotate_angle_y), 0.0, 1.0, 0.0)
        if head.rotate_angle_x:
            glRotatef(math.degrees(head.rotate_angle_x), 1.0, 0.0, 0.0)
        glTranslatef(0.0, -0.25, 0.0)
        glRotatef(180.0, 0.0, 1.0, 0.0)
        scale = 10.0 / 16.0
        glScalef(scale, -scale, scale)
        glTranslatef(-0.5, -0.5, -0.5)
        glColor4f(brightness, brightness, brightness, 1.0)
        _draw_held_block_cube(player, helmet, brightness=brightness)
    finally:
        glPopMatrix()


def _render_deadmau5_ears(player, model: ModelBiped) -> None:
    username = str(getattr(player, "skin_username", "") or "").strip()
    if username.casefold() != "deadmau5":
        return
    has_explicit_skin = getattr(player, "skin_texture", None) is not None
    skin_path = str(getattr(player, "skin_path", "") or "").replace("\\", "/")
    has_explicit_skin = has_explicit_skin or bool(
        skin_path and skin_path not in {"char.png", "game/textures/char.png"}
    )
    if not has_explicit_skin and username not in _skin_loaded:
        return
    ears = getattr(model, "deadmau5_ears", None)
    head = getattr(model, "biped_head", None)
    if ears is None or head is None:
        return

    head_pitch = float(head.rotate_angle_x)
    head_yaw = float(head.rotate_angle_y)
    ears.rotate_angle_x = head_pitch
    ears.rotate_angle_y = head_yaw
    ears.rotate_angle_z = 0.0
    ears.set_rotation_point(0.0, 0.0, 0.0)
    glPushMatrix()
    try:
        for side in (-1.0, 1.0):
            anchor = deadmau5_ear_anchor(side, head_yaw, head_pitch)
            glPushMatrix()
            try:
                glTranslatef(anchor[0] * _UNIT, anchor[1] * _UNIT, anchor[2] * _UNIT)
                glScalef(4.0 / 3.0, 4.0 / 3.0, 4.0 / 3.0)
                ears.render(_UNIT)
            finally:
                glPopMatrix()
    finally:
        glPopMatrix()


def _render_player_cape(
    player, body_yaw: float, partial: float, brightness: float = 1.0,
    *, body_pitch: float = 0.0,
) -> bool:
    cape_texture = resolve_cape_texture(player)
    if cape_texture is None:
        return False

    cape_pitch, cape_roll, cape_yaw = cape_rotation_angles(
        player, body_yaw, partial,
    )
    glPushMatrix()
    try:
        glEnable(GL_TEXTURE_2D)
        cape_texture.bind()
        glEnable(GL_ALPHA_TEST)
        glAlphaFunc(GL_GREATER, 0.1)
        glColor4f(brightness, brightness, brightness, 1.0)
        if abs(float(body_pitch)) > 1e-6:
            glRotatef(math.degrees(float(body_pitch)), 1.0, 0.0, 0.0)
        glTranslatef(0.0, 0.0, 2.0 * _UNIT)
        glRotatef(cape_pitch, 1.0, 0.0, 0.0)
        glRotatef(cape_roll, 0.0, 0.0, 1.0)
        glRotatef(cape_yaw, 0.0, 1.0, 0.0)
        glRotatef(180.0, 0.0, 1.0, 0.0)
        _get_cape_model().render(_UNIT)
    finally:
        glPopMatrix()
        _bind_skin(player)
    return True


def render_player_model(
    player,
    world_x: float,
    world_y: float,
    world_z: float,
    body_yaw: float,
    partial: float = 0.0,
    *,
    head_yaw: Optional[float] = None,
    head_pitch: Optional[float] = None,
) -> None:
    renderer = getattr(player, "renderer", None)
    if renderer is None:
        return

    dx, dy, dz = renderer.camera_relative_offset(world_x, world_y, world_z)

    yaw = float(player.yaw if head_yaw is None else head_yaw)
    pitch = float(player.pitch if head_pitch is None else head_pitch)
    limb_swing, limb_amount, age = _player_anim(player, partial)
    if bool(getattr(player, "is_sleeping", False)):
        limb_swing = 0.0
        limb_amount = 0.0

    net_head = body_yaw - yaw
    while net_head < -180.0:
        net_head += 360.0
    while net_head >= 180.0:
        net_head -= 360.0

    model = _get_main_model()
    glPushMatrix()
    from game.entities.entity import Entity as _Entity
    if not getattr(_Entity, "_entity_draw_lighting_batched", False):
        enable_standard_item_lighting()
    glEnable(GL_NORMALIZE)
    glTranslatef(dx, dy, dz)
    glRotatef(body_yaw, 0.0, 1.0, 0.0)
    death_time = float(getattr(player, "death_time", 0.0))
    if death_time > 0.0:
        death_progress = max(0.0, min(1.0, ((death_time + partial - 1.0) / 20.0 * 1.6) ** 0.5))
        glRotatef(death_progress * 90.0, 0.0, 0.0, 1.0)
    elif bool(getattr(player, "is_sleeping", False)):
        glRotatef(90.0, 0.0, 0.0, 1.0)
        glRotatef(270.0, 0.0, 1.0, 0.0)
    glScalef(-1.0, -1.0, 1.0)
    glScalef(_PLAYER_SCALE, _PLAYER_SCALE, _PLAYER_SCALE)  
    glTranslatef(0.0, -24.0 * _UNIT - 0.0078125, 0.0)

    _bind_skin(player)
    try:
        brightness = float(player.get_entity_brightness())
    except Exception:
        brightness = _player_render_brightness(player)
    brightness = max(0.0, min(1.0, brightness))
    glColor4f(brightness, brightness, brightness, 1.0)
    glDisable(GL_CULL_FACE)
    glEnable(GL_DEPTH_TEST)
    raised_arms = bool(getattr(player, "raised_arms", False))
    is_riding = getattr(player, "riding_entity", None) is not None
    is_sneak = bool(getattr(player, "is_sneaking", False))
    if is_riding:
        limb_amount = 0.0
    _apply_player_pose(model, player, partial)
    model.render(
        limb_swing, limb_amount, age, net_head, -pitch, _UNIT,
        raised_arms=raised_arms,
        is_riding=is_riding,
        is_sneak=is_sneak,
    )
    _render_deadmau5_ears(player, model)
    _render_player_cape(
        player, cape_projection_yaw_for_world(body_yaw), partial, brightness,
        body_pitch=cape_body_attachment_pitch(model, is_sneak),
    )
    _render_armor_passes(
        player, limb_swing, limb_amount, age, net_head, -pitch, brightness,
        is_riding=is_riding, is_sneak=is_sneak, partial=partial,
    )
    _render_pumpkin_helmet(player, model, brightness)
    _render_third_person_held_item(player, model, brightness)

    if float(getattr(player, "hurt_time", 0.0)) > 0.0 or death_time > 0.0:
        glDisable(GL_TEXTURE_2D)
        glDisable(GL_ALPHA_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glDepthFunc(GL_EQUAL)
        glColor4f(brightness, 0.0, 0.0, 0.4)
        model.render(
            limb_swing, limb_amount, age, net_head, -pitch, _UNIT,
            raised_arms=raised_arms,
            is_riding=is_riding,
            is_sneak=is_sneak,
        )
        _render_armor_passes(
            player, limb_swing, limb_amount, age, net_head, -pitch,
            brightness, overlay=True, is_riding=is_riding, is_sneak=is_sneak,
            partial=partial,
        )
        glDepthFunc(GL_LEQUAL)
        glDisable(GL_BLEND)
        glEnable(GL_ALPHA_TEST)
        glEnable(GL_TEXTURE_2D)
    glEnable(GL_CULL_FACE)
    glDisable(GL_NORMALIZE)
    if not getattr(_Entity, "_entity_draw_lighting_batched", False):
        disable_standard_item_lighting()
    glColor4f(1.0, 1.0, 1.0, 1.0)
    glPopMatrix()


def _fire_tile_uv(atlas_idx: int):
    col = int(atlas_idx) & 15
    row = (int(atlas_idx) >> 4) & 15
    u0 = col / 16.0
    u1 = (col * 16.0 + 15.99) / 256.0
    v0 = 1.0 - (row + 1) / 16.0
    v1 = 1.0 - row / 16.0
    return u0, v0, u1, v1


def _draw_first_person_fire_layer(layer: int) -> None:
    u0, v0, u1, v1 = _fire_tile_uv(31 + int(layer) * 16)
    glPushMatrix()
    glTranslatef(-((layer * 2 - 1) * 0.24), -0.3, 0.0)
    glRotatef((layer * 2 - 1) * 10.0, 0.0, 1.0, 0.0)
    glBegin(GL_QUADS)
    glTexCoord2f(u1, v0); glVertex3f(-0.5, -0.5, -0.5)
    glTexCoord2f(u0, v0); glVertex3f( 0.5, -0.5, -0.5)
    glTexCoord2f(u0, v1); glVertex3f( 0.5,  0.5, -0.5)
    glTexCoord2f(u1, v1); glVertex3f(-0.5,  0.5, -0.5)
    glEnd()
    glPopMatrix()


def render_first_person_fire_overlay(player) -> None:
    if int(getattr(player, "_fire_ticks", 0)) <= 0:
        return
    from game.block import Block

    texture = Block._get_texture()
    if texture is None:
        return
    alpha_was_enabled = bool(glIsEnabled(GL_ALPHA_TEST))
    blend_was_enabled = bool(glIsEnabled(GL_BLEND))
    lighting_was_enabled = bool(glIsEnabled(GL_LIGHTING))
    cull_was_enabled = bool(glIsEnabled(GL_CULL_FACE))
    texture_was_enabled = bool(glIsEnabled(GL_TEXTURE_2D))
    glPushMatrix()
    try:
        glLoadIdentity()
        texture.bind()
        try:
            glEnable(GL_TEXTURE_2D)
            glDisable(GL_ALPHA_TEST)
            glDisable(GL_CULL_FACE)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glDisable(GL_LIGHTING)
            glColor4f(1.0, 1.0, 1.0, 0.9)
            _draw_first_person_fire_layer(0)
            _draw_first_person_fire_layer(1)
        finally:
            texture.unbind()
            glColor4f(1.0, 1.0, 1.0, 1.0)
            (glEnable if alpha_was_enabled else glDisable)(GL_ALPHA_TEST)
            (glEnable if blend_was_enabled else glDisable)(GL_BLEND)
            (glEnable if lighting_was_enabled else glDisable)(GL_LIGHTING)
            (glEnable if cull_was_enabled else glDisable)(GL_CULL_FACE)
            (glEnable if texture_was_enabled else glDisable)(GL_TEXTURE_2D)
    finally:
        glPopMatrix()


def render_player_fire_effect(player, world_x: float, world_y: float, world_z: float) -> None:
    if int(getattr(player, "_fire_ticks", 0)) <= 0:
        return
    from game.block import Block
    from game.entities.entity import Entity

    texture = Block._get_texture()
    if texture is None:
        return
    renderer = getattr(player, "renderer", None) or Entity.renderer
    if renderer is not None:
        ox, oy, oz = renderer.camera_relative_offset(world_x, world_y, world_z)
    else:
        ox, oy, oz = float(world_x), float(world_y), float(world_z)

    lighting_was_enabled = bool(glIsEnabled(GL_LIGHTING))
    cull_was_enabled = bool(glIsEnabled(GL_CULL_FACE))
    alpha_was_enabled = bool(glIsEnabled(GL_ALPHA_TEST))
    texture_was_enabled = bool(glIsEnabled(GL_TEXTURE_2D))
    glPushMatrix()
    try:
        glEnable(GL_TEXTURE_2D)
        glEnable(GL_ALPHA_TEST)
        glAlphaFunc(GL_GREATER, 0.1)
        glDisable(GL_CULL_FACE)
        glTranslatef(ox, oy, oz)
        view_yaw = float(getattr(renderer, "yaw", 0.0)) if renderer is not None else 0.0
        if renderer is None:
            view_yaw = float(getattr(player, "yaw", 0.0))
        glRotatef(view_yaw, 0.0, 1.0, 0.0)
        width = max(0.01, float(getattr(player, "size", (0.6, 1.8, 0.6))[0]))
        height = max(0.0, float(getattr(player, "size", (0.6, 1.8, 0.6))[1]))
        size = width * 1.4
        layers_left = height / width
        glScalef(size, size, size)
        glTranslatef(0.0, 0.0, 0.4 + int(layers_left) * 0.02)
        texture.bind()
        glAlphaFunc(GL_GREATER, 0.1)
        glDisable(GL_LIGHTING)
        glColor4f(1.0, 1.0, 1.0, 1.0)
        layer = 0
        y_offset = 0.0
        edge = 1.0
        while layers_left > 0.0:
            u0, v0, u1, v1 = _fire_tile_uv(31)
            glBegin(GL_QUADS)
            glTexCoord2f(u1, v0); glVertex3f(edge - 0.5, -y_offset, 0.0)
            glTexCoord2f(u0, v0); glVertex3f(-0.5, -y_offset, 0.0)
            glTexCoord2f(u0, v1); glVertex3f(-0.5, 1.4 - y_offset, 0.0)
            glTexCoord2f(u1, v1); glVertex3f(edge - 0.5, 1.4 - y_offset, 0.0)
            glEnd()
            layers_left -= 1.0
            y_offset -= 1.0
            edge *= 0.9
            layer += 1
            glTranslatef(0.0, 0.0, -0.04)
    finally:
        texture.unbind()
        glColor4f(1.0, 1.0, 1.0, 1.0)
        (glEnable if lighting_was_enabled else glDisable)(GL_LIGHTING)
        (glEnable if cull_was_enabled else glDisable)(GL_CULL_FACE)
        (glEnable if alpha_was_enabled else glDisable)(GL_ALPHA_TEST)
        (glEnable if texture_was_enabled else glDisable)(GL_TEXTURE_2D)
        glPopMatrix()


def _atlas_uv(atlas_idx, cols=16):
    tile = 1.0 / cols
    col = atlas_idx % cols
    row = atlas_idx // cols
    u0 = col * tile
    u1 = u0 + tile
    v1 = 1.0 - row * tile
    v0 = v1 - tile
    return u0, v0, u1, v1


def _draw_item_sprite(u0, v0, u1, v1, thickness=0.0625):
    glBegin(GL_QUADS)
    glNormal3f(0.0, 0.0, 1.0)
    glTexCoord2f(u1, v0); glVertex3f(0.0, 0.0, 0.0)
    glTexCoord2f(u0, v0); glVertex3f(1.0, 0.0, 0.0)
    glTexCoord2f(u0, v1); glVertex3f(1.0, 1.0, 0.0)
    glTexCoord2f(u1, v1); glVertex3f(0.0, 1.0, 0.0)
    glEnd()

    glBegin(GL_QUADS)
    glNormal3f(0.0, 0.0, -1.0)
    glTexCoord2f(u1, v1); glVertex3f(0.0, 1.0, -thickness)
    glTexCoord2f(u0, v1); glVertex3f(1.0, 1.0, -thickness)
    glTexCoord2f(u0, v0); glVertex3f(1.0, 0.0, -thickness)
    glTexCoord2f(u1, v0); glVertex3f(0.0, 0.0, -thickness)
    glEnd()

    eps = 0.001953125
    width = 1.0 / 16.0
    for i in range(16):
        t = i / 16.0
        u = u1 + (u0 - u1) * t - eps
        x = width * i

        glBegin(GL_QUADS)
        glNormal3f(-1.0, 0.0, 0.0)
        glTexCoord2f(u, v0); glVertex3f(x, 0.0, -thickness)
        glTexCoord2f(u, v0); glVertex3f(x, 0.0, 0.0)
        glTexCoord2f(u, v1); glVertex3f(x, 1.0, 0.0)
        glTexCoord2f(u, v1); glVertex3f(x, 1.0, -thickness)
        glEnd()

        glBegin(GL_QUADS)
        glNormal3f(1.0, 0.0, 0.0)
        glTexCoord2f(u, v1); glVertex3f(x + width, 1.0, -thickness)
        glTexCoord2f(u, v1); glVertex3f(x + width, 1.0, 0.0)
        glTexCoord2f(u, v0); glVertex3f(x + width, 0.0, 0.0)
        glTexCoord2f(u, v0); glVertex3f(x + width, 0.0, -thickness)
        glEnd()

    for i in range(16):
        t = i / 16.0
        v = v0 + (v1 - v0) * t + eps
        y = width * i

        glBegin(GL_QUADS)
        glNormal3f(0.0, 1.0, 0.0)
        glTexCoord2f(u1, v); glVertex3f(0.0, y + width, 0.0)
        glTexCoord2f(u0, v); glVertex3f(1.0, y + width, 0.0)
        glTexCoord2f(u0, v); glVertex3f(1.0, y + width, -thickness)
        glTexCoord2f(u1, v); glVertex3f(0.0, y + width, -thickness)
        glEnd()

        glBegin(GL_QUADS)
        glNormal3f(0.0, -1.0, 0.0)
        glTexCoord2f(u0, v); glVertex3f(1.0, y, 0.0)
        glTexCoord2f(u1, v); glVertex3f(0.0, y, 0.0)
        glTexCoord2f(u1, v); glVertex3f(0.0, y, -thickness)
        glTexCoord2f(u0, v); glVertex3f(1.0, y, -thickness)
        glEnd()


def _draw_held_sprite(held, brightness=1.0):
    try:
        from game.entities.item import Item
    except Exception:
        return
    tex = None
    atlas_idx = 0
    is_block = held.id in getattr(Item, "block_defs", {})
    if held.id in ("redstone_repeater", "bed"):
        block_item = Item.block_defs.get(held.id, {}).get("blockitem", {})
        item_def = Item.item_defs.get(held.id, {})
        atlas_idx = item_def.get("texture", block_item.get("texture"))
        tex = Item.item_texture
        if atlas_idx is None or tex is None:
            return
    elif (held.id in getattr(Item, "item_defs", {})
            and "texture" in Item.item_defs[held.id] and Item.item_texture is not None):
        defn = Item.item_defs[held.id]
        atlas_idx = Item.icon_texture(held.id, getattr(held, "damage", 0))
        tex = Item.item_texture
    elif is_block and Item.block_texture is not None:
        defn = Item.block_defs[held.id]
        faces = Item.block_faces(held.id, getattr(held, "damage", 0))
        if isinstance(faces, list):
            atlas_idx = faces[2] if len(faces) > 2 else faces[0]
        else:
            atlas_idx = faces
        tex = Item.block_texture
    else:
        return
    u0, v0, u1, v1 = _atlas_uv(atlas_idx)
    glEnable(GL_TEXTURE_2D)
    tex.bind()
    glEnable(GL_ALPHA_TEST)
    glAlphaFunc(GL_GREATER, 0.1)
    glColor4f(brightness, brightness, brightness, 1.0)
    glTranslatef(-0.0, -0.3, 0.0)
    glScalef(1.5, 1.5, 1.5)
    glRotatef(50.0, 0.0, 1.0, 0.0)
    glRotatef(335.0, 0.0, 0.0, 1.0)
    glTranslatef(-(15.0 / 16.0), -(1.0 / 16.0), 0.0)
    _draw_item_sprite(u0, v0, u1, v1)
    tex.unbind()



def _draw_held_fence(player, held, brightness=1.0):
    try:
        from game.entities.item import Item
        from game.entities.item_model import _fence_inventory_mesh
    except Exception:
        return
    if Item.block_texture is None:
        return
    mesh_data = _fence_inventory_mesh()
    if not mesh_data:
        return
    tex = Item.block_texture
    glEnable(GL_TEXTURE_2D)
    tex.bind()
    try:
        for normal, vertices, uvs, color in mesh_data:
            glColor4f(
                color[0] * brightness, color[1] * brightness, color[2] * brightness, 1.0,
            )
            glBegin(GL_QUADS)
            glNormal3f(*normal)
            for (uu, vv), (vx, vy, vz) in zip(uvs, vertices):
                glTexCoord2f(uu, vv)
                glVertex3f(vx + 0.5, vy + 0.5, vz + 0.5)
            glEnd()
    finally:
        glColor4f(1.0, 1.0, 1.0, 1.0)
        tex.unbind()


def _draw_held_stairs(player, held, brightness=1.0):
    try:
        from game.entities.item import Item
    except Exception:
        return
    if Item.block_texture is None:
        return
    defn = Item.block_defs.get(held.id)
    if not defn:
        return
    old_bounds = defn.get("bounds")
    for bounds in (
        [0.0, 0.0, 0.0, 1.0, 1.0, 0.5],
        [0.0, 0.0, 0.5, 1.0, 0.5, 1.0],
    ):
        defn["bounds"] = bounds
        try:
            _draw_held_block_cube(player, held, brightness=brightness)
        finally:
            pass
    if old_bounds is None:
        defn.pop("bounds", None)
    else:
        defn["bounds"] = old_bounds



def _draw_held_block_cube(player, held, brightness=1.0):
    try:
        from game.entities.item import Item
    except Exception:
        return
    if Item.block_texture is None:
        return
    defn = Item.block_defs.get(held.id)
    if not defn:
        return
    damage = getattr(held, "damage", 0)
    faces = Item.block_faces(held.id, damage)
    def face_tint_rgb(slot):
        tint = Item.block_item_tint(held.id, damage, slot=slot)
        return tuple(channel / 255.0 for channel in tint)

    def face_idx(slot):
        if isinstance(faces, list):
            return faces[slot] if slot < len(faces) else faces[0]
        return faces

    if isinstance(faces, list) and len(faces) >= 6:
        tex_top = Item.block_face_texture(held.id, 3, damage)
        tex_bottom = Item.block_face_texture(held.id, 2, damage)
        tex_px = Item.block_face_texture(held.id, 0, damage)
        tex_nx = Item.block_face_texture(held.id, 1, damage)
        tex_pz = Item.block_face_texture(held.id, 4, damage)
        tex_nz = Item.block_face_texture(held.id, 5, damage)
    elif isinstance(faces, list) and len(faces) >= 3:
        tex_top = faces[2]
        tex_bottom = faces[2]
        tex_px = tex_nx = tex_pz = tex_nz = faces[0]
    else:
        tex_top = tex_bottom = tex_px = tex_nx = tex_pz = tex_nz = face_idx(0)

    bounds = defn.get("bounds", [0.0, 0.0, 0.0, 1.0, 1.0, 1.0])
    try:
        x0, y0, z0, x1, y1, z1 = (float(v) for v in bounds[:6])
    except (TypeError, ValueError):
        x0, y0, z0, x1, y1, z1 = 0.0, 0.0, 0.0, 1.0, 1.0, 1.0

    tex = Item.block_texture
    glPushAttrib(GL_ALL_ATTRIB_BITS)
    try:
        glEnable(GL_TEXTURE_2D)
        glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
        tex.bind()
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        def face_uv(atlas_idx, x, y, z, face):
            u0, v0, u1, v1 = _atlas_uv(atlas_idx)
            if face == "+x":
                uf, vf = 1.0 - z, y
            elif face == "-x":
                uf, vf = z, y
            elif face == "+z":
                uf, vf = x, y
            elif face == "-z":
                uf, vf = 1.0 - x, y
            elif face == "+y":
                uf, vf = x, 1.0 - z
            else:
                uf, vf = x, 1.0 - z
            return u0 + (u1 - u0) * uf, v0 + (v1 - v0) * vf

        def quad(atlas_idx, verts, normal, face, slot, shade=1.0):
            tint_rgb = face_tint_rgb(slot)
            glColor4f(
                shade * brightness * tint_rgb[0],
                shade * brightness * tint_rgb[1],
                shade * brightness * tint_rgb[2],
                1.0,
            )
            glBegin(GL_QUADS)
            glNormal3f(*normal)
            for x, y, z in verts:
                u, v = face_uv(atlas_idx, x, y, z, face)
                glTexCoord2f(u, v)
                glVertex3f(x, y, z)
            glEnd()

        quad(tex_top,
             [(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)],
             (0, 1, 0), "+y", 3, 1.0)
        quad(tex_bottom,
             [(x0, y0, z1), (x1, y0, z1), (x1, y0, z0), (x0, y0, z0)],
             (0, -1, 0), "-y", 2, 0.5)
        quad(tex_pz,
             [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
             (0, 0, 1), "+z", 4, 0.8)
        quad(tex_nz,
             [(x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0)],
             (0, 0, -1), "-z", 5, 0.8)
        quad(tex_px,
             [(x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1)],
             (1, 0, 0), "+x", 0, 0.6)
        quad(tex_nx,
             [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)],
             (-1, 0, 0), "-x", 1, 0.6)
    finally:
        tex.unbind()
        glPopAttrib()


def _draw_held_item(player, brightness=1.0, held=None):
    if held is None:
        return
    try:
        from game.entities.item import Item
    except Exception:
        return

    is_block = held.id in getattr(Item, "block_defs", {}) and held.id not in ("cake", "bed", "redstone_repeater")
    defn = Item.block_defs.get(held.id) if is_block else None

    if _held_item_uses_3d_block(held.id, Item):
        glPushMatrix()
        glTranslatef(-0.5, -0.5, -0.5)
        if defn.get("model") == "stairs":
            _draw_held_stairs(player, held, brightness=brightness)
        elif defn.get("model") == "fence":
            _draw_held_fence(player, held, brightness=brightness)
        else:
            _draw_held_block_cube(player, held, brightness=brightness)
        glPopMatrix()
        return

    _draw_held_sprite(held, brightness=brightness)

def _player_render_brightness(player) -> float:
    world = getattr(player, "world", None)
    if world is None:
        return 1.0
    try:
        x = int(math.floor(float(player.pos[0]) + 0.5))
        y = int(math.floor(float(player.pos[1]) + 0.5))
        z = int(math.floor(float(player.pos[2]) + 0.5))
        light = world.get_light(x, y, z)
        if light <= 0:
            best = 0
            for nx, ny, nz in (
                (x, y + 1, z),
                (x + 1, y, z), (x - 1, y, z),
                (x, y, z + 1), (x, y, z - 1),
            ):
                n = world.get_light(nx, ny, nz, use_neighbor_max=False)
                if n > best:
                    best = n
            if best > 0:
                light = best
        return brightness_from_level(light, hell=bool(getattr(world, "is_hell_world", False)))
    except Exception:
        try:
            return float(player.get_entity_brightness())
        except Exception:
            return 1.0


def inside_opaque_block_texture(player, world=None) -> Optional[int]:
    if world is None:
        world = getattr(player, "world", None)
    if world is None or not hasattr(player, "pos"):
        return None
    try:
        px, py, pz = (float(v) for v in player.pos[:3])
        eye_height = float(getattr(player, "EYE_HEIGHT", 1.62))
        bx = math.floor(px + 0.5)
        by = math.floor(py + eye_height + 0.5)
        bz = math.floor(pz + 0.5)
        block_type = world.get_block_type(bx, by, bz)
    except Exception:
        return None
    if not block_type or block_type in {"air", "flowing_water", "water", "flowing_lava", "lava"}:
        return None

    try:
        from game.block import Block
        Block._load_defs()
        definition = Block._block_defs.get(block_type)
    except Exception:
        return None
    if not definition:
        return None
    bounds = definition.get("bounds", [0.0, 0.0, 0.0, 1.0, 1.0, 1.0])
    is_full_cube = (
        isinstance(bounds, (list, tuple)) and len(bounds) == 6
        and all(abs(float(actual) - float(expected)) < 1e-9
                for actual, expected in zip(bounds, (0, 0, 0, 1, 1, 1)))
    )
    if (definition.get("model", "cube") != "cube"
            or not is_full_cube
            or definition.get("transparent", False)
            or definition.get("collision", True) is False
            or int(definition.get("lightOpacity", 255)) < 255):
        return None

    faces = definition.get("faces", 0)
    if isinstance(faces, (list, tuple)):
        tile = faces[5] if len(faces) > 5 else (faces[0] if faces else 0)
    else:
        tile = faces
    try:
        return int(tile)
    except (TypeError, ValueError):
        return None


from game.player_pose import (
    should_render_first_person_hand,
    should_render_player_held_item,
)


def render_first_person_hand(
    player, partial: float = 0.0, *, inventory_open: bool = False
) -> None:
    if not should_render_first_person_hand(player):
        return
    swing = 0.0 if inventory_open else _player_swing_progress(player, partial)

    equipped = float(getattr(player, "equipped_progress", 1.0))
    prev_eq = float(getattr(player, "prev_equipped_progress", equipped))
    eq = prev_eq + (equipped - prev_eq) * partial
    active_fishing_cast = False
    try:
        if hasattr(player, "_equipped_item"):
            held = player._equipped_item
        else:
            held = player.inventory.get_active_item()
        hook = getattr(player, "fishing_hook", None)
        active_fishing_cast = hook is not None and not getattr(hook, "dead", True)
        if active_fishing_cast:
            from game.entities.item import ItemStack
            held = ItemStack("stick", 1)
    except Exception:
        held = None

    glMatrixMode(GL_MODELVIEW)
    glPushMatrix()
    glLoadIdentity()
    glEnable(GL_DEPTH_TEST)
    glDisable(GL_CULL_FACE)
    _apply_view_bobbing(player, partial)
    glPushMatrix()
    glRotatef(float(player.pitch), 1.0, 0.0, 0.0)
    glRotatef(float(player.yaw), 0.0, 1.0, 0.0)
    enable_standard_item_lighting()
    glPopMatrix()
    glEnable(GL_NORMALIZE)
    brightness = _player_render_brightness(player)
    glColor4f(brightness, brightness, brightness, 1.0)

    if held is not None:
        f = 0.8
        if swing > 0.0:
            s = swing
            sin_s = math.sin(s * math.pi)
            sin_sqrt = math.sin(math.sqrt(s) * math.pi)
            glTranslatef(-sin_sqrt * 0.4, math.sin(math.sqrt(s) * math.pi * 2.0) * 0.2, -sin_s * 0.2)

        glTranslatef(0.7 * f, -0.65 * f - (1.0 - eq) * 0.6, -0.9 * f)
        glRotatef(45.0, 0.0, 1.0, 0.0)

        if swing > 0.0:
            s = swing
            sin_sq = math.sin(s * s * math.pi)
            sin_sqrt = math.sin(math.sqrt(s) * math.pi)
            glRotatef(-sin_sq * 20.0, 0.0, 1.0, 0.0)
            glRotatef(-sin_sqrt * 20.0, 0.0, 0.0, 1.0)
            glRotatef(-sin_sqrt * 80.0, 1.0, 0.0, 0.0)

        glScalef(0.4, 0.4, 0.4)
        try:
            from game.entities.item import Item
            held_def = Item.item_defs.get(getattr(held, "id", None), {}) if held is not None else {}
        except Exception:
            held_def = {}
        if held_def.get("rotate_around", False) or active_fishing_cast:
            glRotatef(180.0, 0.0, 1.0, 0.0)
        _draw_held_item(player, brightness=brightness, held=held)
    else:
        f = 0.8
        if swing > 0.0:
            s = swing
            sin_s = math.sin(s * math.pi)
            sin_sqrt = math.sin(math.sqrt(s) * math.pi)
            glTranslatef(
                -sin_sqrt * 0.3,
                math.sin(math.sqrt(s) * math.pi * 2.0) * 0.4,
                -sin_s * 0.4,
            )

        glTranslatef(0.8 * f, -(12.0 / 16.0) * f - (1.0 - eq) * 0.6, -0.9 * f)
        glRotatef(45.0, 0.0, 1.0, 0.0)

        if swing > 0.0:
            s = swing
            sin_sq = math.sin(s * s * math.pi)
            sin_sqrt = math.sin(math.sqrt(s) * math.pi)
            glRotatef(sin_sqrt * 70.0, 0.0, 1.0, 0.0)
            glRotatef(-sin_sq * 20.0, 0.0, 0.0, 1.0)

        glTranslatef(-1.0, 3.6, 3.5)
        glRotatef(120.0, 0.0, 0.0, 1.0)
        glRotatef(200.0, 1.0, 0.0, 0.0)
        glRotatef(-135.0, 0.0, 1.0, 0.0)
        glTranslatef(5.6, 0.0, 0.0)

        model = _get_main_model()
        model.biped_right_arm.rotate_angle_x = 0.0
        model.biped_right_arm.rotate_angle_y = 0.0
        model.biped_right_arm.rotate_angle_z = 0.0
        model.biped_right_arm.set_rotation_point(-5.0, 2.0, 0.0)
        tex = resolve_skin_texture(player)
        glEnable(GL_TEXTURE_2D)
        tex.bind()
        glColor4f(brightness, brightness, brightness, 1.0)
        glEnable(GL_ALPHA_TEST)
        glAlphaFunc(GL_GREATER, 0.1)
        model.render_right_arm(_UNIT)

    glEnable(GL_CULL_FACE)
    glDisable(GL_NORMALIZE)
    disable_standard_item_lighting()
    glColor4f(1.0, 1.0, 1.0, 1.0)
    glPopMatrix()


def render_inventory_player(
    player,
    x: float,
    y: float,
    scale_px: float = 30.0,
    mouse_x: float = 0.0,
    mouse_y: float = 0.0,
    partial: float = 0.0,
) -> None:
    from OpenGL.GL import glOrtho, glGetIntegerv, GL_VIEWPORT, glClear, GL_DEPTH_BUFFER_BIT

    var9 = float(x) - float(mouse_x)
    var10 = float(y - 50) - float(mouse_y)
    body_yaw = math.atan(var9 / 40.0) * 20.0
    head_yaw = math.atan(var9 / 40.0) * 40.0
    head_pitch = -math.atan(var10 / 40.0) * 20.0

    model = _get_main_model()
    limb_swing, limb_amount, age = _player_anim(player, partial)
    is_riding = getattr(player, "riding_entity", None) is not None
    is_sneak = bool(getattr(player, "is_sneaking", False))
    if is_riding:
        limb_amount = 0.0

    cull_was_enabled = bool(glIsEnabled(GL_CULL_FACE))
    depth_was_enabled = bool(glIsEnabled(GL_DEPTH_TEST))
    vp = glGetIntegerv(GL_VIEWPORT)
    win_w = float(vp[2]) if vp is not None and len(vp) >= 3 and vp[2] else 854.0
    win_h = float(vp[3]) if vp is not None and len(vp) >= 4 and vp[3] else 480.0

    glMatrixMode(GL_PROJECTION)
    glPushMatrix()
    glLoadIdentity()
    glOrtho(0.0, win_w, win_h, 0.0, -1000.0, 1000.0)

    glMatrixMode(GL_MODELVIEW)
    glPushMatrix()

    glEnable(GL_DEPTH_TEST)
    glClear(GL_DEPTH_BUFFER_BIT)
    glEnable(GL_ALPHA_TEST)
    glAlphaFunc(GL_GREATER, 0.1)
    glDisable(GL_BLEND)
    glDisable(GL_CULL_FACE)
    glEnable(GL_TEXTURE_2D)

    glTranslatef(float(x), float(y), 50.0)
    s = float(scale_px)
    glScalef(-s, s, s)
    glRotatef(180.0, 0.0, 0.0, 1.0)
    glRotatef(135.0, 0.0, 1.0, 0.0)
    enable_standard_item_lighting()
    glRotatef(-135.0, 0.0, 1.0, 0.0)
    glRotatef(-math.atan(var10 / 40.0) * 20.0, 1.0, 0.0, 0.0)

    glRotatef(180.0 - body_yaw, 0.0, 1.0, 0.0)
    glScalef(-1.0, -1.0, 1.0)
    glScalef(_PLAYER_SCALE, _PLAYER_SCALE, _PLAYER_SCALE)  
    glTranslatef(0.0, -24.0 * _UNIT, 0.0)

    _bind_skin(player)
    net_head = head_yaw - body_yaw
    glEnable(GL_NORMALIZE)
    _apply_player_pose(model, player, partial)
    model.render(
        limb_swing, limb_amount, age, net_head, head_pitch, _UNIT,
        is_riding=is_riding,
        is_sneak=is_sneak,
    )
    _render_deadmau5_ears(player, model)
    _render_player_cape(
        player, body_yaw, partial, 1.0,
        body_pitch=cape_body_attachment_pitch(model, is_sneak),
    )
    _render_armor_passes(
        player, limb_swing, limb_amount, age, net_head, head_pitch, 1.0,
        is_riding=is_riding, is_sneak=is_sneak, partial=partial,
    )
    _render_pumpkin_helmet(player, model, brightness=1.0)
    _render_third_person_held_item(player, model, brightness=1.0)
    glDisable(GL_NORMALIZE)
    disable_standard_item_lighting()

    if cull_was_enabled:
        glEnable(GL_CULL_FACE)
    else:
        glDisable(GL_CULL_FACE)
    if depth_was_enabled:
        glEnable(GL_DEPTH_TEST)
    else:
        glDisable(GL_DEPTH_TEST)
    glColor4f(1.0, 1.0, 1.0, 1.0)

    glPopMatrix()
    glMatrixMode(GL_PROJECTION)
    glPopMatrix()
    glMatrixMode(GL_MODELVIEW)



def _third_person_behind_direction(yaw: float, pitch: float) -> Tuple[float, float, float]:
    yaw_r = math.radians(yaw)
    pitch_r = math.radians(pitch)
    return (
        math.sin(yaw_r) * math.cos(pitch_r),
        -math.sin(pitch_r),
        math.cos(yaw_r) * math.cos(pitch_r),
    )


def third_person_camera_offset(
    player, partial: float = 0.0, distance: float = 4.0, *, front: bool = False
) -> Tuple[float, float, float]:
    dx, dy, dz = _third_person_behind_direction(player.yaw, player.pitch)
    if front:
        dx, dy, dz = -dx, -dy, -dz

    eye_x = float(player.pos[0])
    eye_y = float(player.pos[1]) + float(getattr(player, "EYE_HEIGHT", 1.62)) - float(
        getattr(player, "render_y_offset", 0.0)
    )
    eye_z = float(player.pos[2])

    world = getattr(player, "world", None) or getattr(type(player), "world", None)
    dist = float(distance)
    if world is not None:
        transparent = {"air", "water", "flowing_water", "lava", "flowing_lava", "", None}
        step = 1.0 / 64.0
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                for sz in (-1.0, 1.0):
                    ox, oy, oz = sx * 0.1, sy * 0.1, sz * 0.1
                    ray_steps = max(1, int(math.ceil(dist / step)))
                    for i in range(1, ray_steps + 1):
                        t = min(dist, i * step)
                        px = eye_x + ox + dx * t
                        py = eye_y + oy + dy * t
                        pz = eye_z + oz + dz * t
                        bx = int(math.floor(px + 0.5))
                        by = int(math.floor(py + 0.5))
                        bz = int(math.floor(pz + 0.5))
                        try:
                            btype = world.get_block_type(bx, by, bz)
                        except Exception:
                            btype = None
                        if btype in transparent:
                            continue

                        try:
                            block = world.get_block(bx, by, bz)
                            if block is None:
                                local_boxes = [(-0.5, -0.5, -0.5, 0.5, 0.5, 0.5)]
                            elif hasattr(block, "get_selection_boxes"):
                                local_boxes = block.get_selection_boxes()
                            else:
                                local_boxes = [block.get_selection_bounds()]
                            if not local_boxes and hasattr(block, "get_selection_bounds"):
                                local_boxes = [block.get_selection_bounds()]
                        except Exception:
                           local_boxes = [(-0.5, -0.5, -0.5, 0.5, 0.5, 0.5)]

                        lx, ly, lz = px - bx, py - by, pz - bz
                        intersects = any(
                            box[0] - 1e-6 <= lx <= box[3] + 1e-6
                            and box[1] - 1e-6 <= ly <= box[4] + 1e-6
                            and box[2] - 1e-6 <= lz <= box[5] + 1e-6
                            for box in local_boxes
                        )
                        if intersects:
                            dist = min(dist, max(0.0, t - step))
                            break
                    if dist <= step:
                        break
                if dist <= step:
                    break
            if dist <= step:
                break
    return 0.0, 0.0, -dist
