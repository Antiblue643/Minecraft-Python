
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from game.entities.player_cosmetics import cape_url_from_profile_payload
from urllib.request import Request, urlopen

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_DEFAULT_TIMEOUT = 5.0
_USER_AGENT = "MinecraftPy/1.0 (player cape lookup)"


class CapeLookupUnavailable(RuntimeError):
    pass


def _request_bytes(url: str, *, timeout: float, opener):
    request = Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json, image/png, */*",
        },
    )
    with opener(request, timeout=timeout) as response:
        return response.read()


def _profile_json(url: str, *, timeout: float, opener) -> dict | None:
    try:
        payload = json.loads(_request_bytes(url, timeout=timeout, opener=opener).decode("utf-8"))
    except HTTPError as error:
        if error.code == 404:
            return None
        raise CapeLookupUnavailable(f"profile service returned HTTP {error.code}") from error
    except (OSError, URLError, TimeoutError, ValueError, UnicodeDecodeError) as error:
        raise CapeLookupUnavailable(f"profile request failed: {error}") from error
    if not isinstance(payload, dict):
        raise CapeLookupUnavailable("profile service returned an invalid response")
    return payload


def _cape_url_from_profile(payload: dict) -> str | None:
    return cape_url_from_profile_payload(payload)


def download_cape_bytes(
    username: str,
    *,
    opener=None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> bytes | None:
    open_url = opener or urlopen
    name = str(username or "").strip()
    if not name:
        return None

    username_url = f"https://api.mojang.com/users/profiles/minecraft/{quote(name, safe='')}"
    name_profile = _profile_json(username_url, timeout=timeout, opener=open_url)
    if name_profile is None:
        return None
    uuid = str(name_profile.get("id") or "").replace("-", "").strip()
    if not uuid:
        return None

    session_url = f"https://sessionserver.mojang.com/session/minecraft/profile/{quote(uuid, safe='')}"
    session_profile = _profile_json(session_url, timeout=timeout, opener=open_url)
    if session_profile is None:
        return None
    cape_url = _cape_url_from_profile(session_profile)
    if not cape_url:
        return None

    try:
        data = _request_bytes(cape_url, timeout=timeout, opener=open_url)
    except (OSError, URLError, TimeoutError) as error:
        raise CapeLookupUnavailable(f"cape texture download failed: {error}") from error
    if not data.startswith(_PNG_SIGNATURE):
        raise CapeLookupUnavailable("cape texture endpoint did not return a PNG")
    return data
