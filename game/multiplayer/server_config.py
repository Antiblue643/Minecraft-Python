
from __future__ import annotations

import json
import ipaddress
import math
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


_MAX_COORD = 30_000_000.0


def normalize_player_name(value: Any) -> str:
    return str(value or "").strip()[:16]


def player_key(value: Any) -> str:
    return normalize_player_name(value).casefold()


def normalize_ip_network(value: Any) -> str | None:
    try:
        network = ipaddress.ip_network(str(value or "").strip(), strict=False)
        mapped = network.network_address.ipv4_mapped if isinstance(network, ipaddress.IPv6Network) else None
        if mapped is not None and network.prefixlen >= 96:
            return str(ipaddress.ip_network((mapped, network.prefixlen - 96), strict=False))
        return str(network)
    except (TypeError, ValueError):
        return None


def _position(value: Any):
    if not isinstance(value, (tuple, list)) or len(value) < 3:
        return None
    try:
        result = tuple(float(part) for part in value[:3])
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(part) and abs(part) <= _MAX_COORD for part in result):
        return None
    return list(result)


def _finite(value: Any, default: float = 0.0, low: float = -3600.0, high: float = 3600.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        result = float(default)
    if not math.isfinite(result):
        result = float(default)
    return max(low, min(high, result))


class ServerConfig:

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.RLock()
        self.bans: dict[str, dict] = {}
        self.ip_bans: dict[str, dict] = {}
        self.operators: set[str] = set()
        self.whitelist: set[str] = set()
        self.whitelist_display: dict[str, str] = {}
        self.whitelist_enabled = False
        self.profiles: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self.path.is_file():
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return
            if not isinstance(raw, dict):
                return
            bans = raw.get("bans", {})
            if isinstance(bans, list):  
                bans = {str(name): {} for name in bans}
            if isinstance(bans, dict):
                loaded_bans = {}
                for name, value in bans.items():
                    display = normalize_player_name(name)
                    key = player_key(display)
                    if not key:
                        continue
                    value = value if isinstance(value, dict) else {}
                    loaded_bans[key] = {
                        "name": display,
                        "reason": str(value.get("reason") or "Banned by host")[:200],
                        "banned_at": max(0.0, _finite(value.get("banned_at", 0), 0, 0, 1e12)),
                    }
                self.bans = loaded_bans
            raw_ip_bans = raw.get("ip_bans", {})
            if isinstance(raw_ip_bans, dict):
                loaded_ip_bans = {}
                for network_text, value in raw_ip_bans.items():
                    network = normalize_ip_network(network_text)
                    if network is None:
                        continue
                    value = value if isinstance(value, dict) else {}
                    loaded_ip_bans[network] = {
                        "reason": str(value.get("reason") or "IP banned by host")[:200],
                        "banned_at": max(0.0, _finite(value.get("banned_at", 0), 0, 0, 1e12)),
                    }
                self.ip_bans = loaded_ip_bans
            ops = raw.get("operators", [])
            if isinstance(ops, (list, tuple, set)):
                self.operators = {player_key(name) for name in ops if player_key(name)}
            whitelist = raw.get("whitelist", [])
            if isinstance(whitelist, (list, tuple, set)):
                self.whitelist_display = {
                    player_key(name): normalize_player_name(name)
                    for name in whitelist if player_key(name)
                }
                self.whitelist = set(self.whitelist_display)
            self.whitelist_enabled = bool(raw.get("whitelist_enabled", False))
            profiles = raw.get("players", {})
            if isinstance(profiles, dict):
                loaded_profiles = {}
                for name, value in profiles.items():
                    display = normalize_player_name(name)
                    key = player_key(display)
                    if not key or not isinstance(value, dict):
                        continue
                    profile = {"name": display}
                    pos = _position(value.get("pos"))
                    if pos is not None:
                        profile["pos"] = pos
                    profile["yaw"] = _finite(value.get("yaw", 0.0))
                    profile["pitch"] = _finite(value.get("pitch", 0.0), 0, -90, 90)
                    profile["last_seen"] = max(0.0, _finite(value.get("last_seen", 0), 0, 0, 1e12))
                    if isinstance(value.get("inventory"), dict):
                        from game.multiplayer.protocol import normalize_inventory_state
                        profile["inventory"] = normalize_inventory_state(value["inventory"])
                    loaded_profiles[key] = profile
                self.profiles = loaded_profiles

    def save(self) -> bool:
        with self._lock:
            data = {
                "version": 2,
                "bans": self.bans,
                "ip_bans": self.ip_bans,
                "operators": sorted(self.operators),
                "whitelist": sorted(self.whitelist_display.get(key, key) for key in self.whitelist),
                "whitelist_enabled": bool(self.whitelist_enabled),
                "players": self.profiles,
            }
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(tmp_name, self.path)
                finally:
                    try:
                        os.unlink(tmp_name)
                    except FileNotFoundError:
                        pass
                return True
            except OSError:
                return False

    def is_banned(self, name: Any) -> bool:
        with self._lock:
            return player_key(name) in self.bans

    def ban(self, name: Any, reason: str = "Banned by host") -> bool:
        display = normalize_player_name(name)
        key = player_key(display)
        if not key:
            return False
        with self._lock:
            self.bans[key] = {
                "name": display,
                "reason": str(reason or "Banned by host")[:200],
                "banned_at": time.time(),
            }
            return self.save()

    def pardon(self, name: Any) -> bool:
        key = player_key(name)
        if not key:
            return False
        with self._lock:
            existed = self.bans.pop(key, None) is not None
            if existed:
                self.save()
            return existed

    def ban_names(self) -> list[str]:
        with self._lock:
            return sorted(value.get("name", key) for key, value in self.bans.items())

    def ban_reason(self, name: Any) -> str:
        with self._lock:
            return str(self.bans.get(player_key(name), {}).get("reason") or "Banned by host")

    def add_ip_ban(self, address_or_network: Any, reason: str = "IP banned by host") -> bool:
        network = normalize_ip_network(address_or_network)
        if network is None:
            return False
        with self._lock:
            self.ip_bans[network] = {
                "reason": str(reason or "IP banned by host")[:200],
                "banned_at": time.time(),
            }
            return self.save()

    def pardon_ip(self, address_or_network: Any) -> bool:
        network = normalize_ip_network(address_or_network)
        if network is None:
            return False
        with self._lock:
            key = network
            if key not in self.ip_bans:
                try:
                    candidate = ipaddress.ip_network(network, strict=False)
                    if candidate.prefixlen == candidate.max_prefixlen:
                        matches = [
                            text for text in self.ip_bans
                            if candidate.network_address in ipaddress.ip_network(text, strict=False)
                        ]
                        if matches:
                            key = max(matches, key=lambda text: ipaddress.ip_network(text).prefixlen)
                except ValueError:
                    pass
            existed = self.ip_bans.pop(key, None) is not None
            if existed:
                self.save()
            return existed

    def ip_ban_reason(self, address: Any) -> str | None:
        try:
            ip = ipaddress.ip_address(str(address).strip())
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
                ip = ip.ipv4_mapped
        except ValueError:
            return None
        with self._lock:
            for network_text, record in self.ip_bans.items():
                try:
                    if ip in ipaddress.ip_network(network_text, strict=False):
                        return str(record.get("reason") or "IP banned by host")
                except ValueError:
                    continue
        return None

    def ip_ban_list(self) -> list[str]:
        with self._lock:
            return sorted(self.ip_bans)

    def add_whitelist(self, name: Any) -> bool:
        key = player_key(name)
        if not key:
            return False
        with self._lock:
            self.whitelist.add(key)
            self.whitelist_display[key] = normalize_player_name(name)
            return self.save()

    def remove_whitelist(self, name: Any) -> bool:
        key = player_key(name)
        if not key:
            return False
        with self._lock:
            existed = key in self.whitelist
            self.whitelist.discard(key)
            self.whitelist_display.pop(key, None)
            return bool(existed and self.save())

    def is_whitelisted(self, name: Any) -> bool:
        with self._lock:
            return player_key(name) in self.whitelist

    def whitelist_names(self) -> list[str]:
        with self._lock:
            display_names = dict(self.whitelist_display)
            for key, profile in self.profiles.items():
                display_names.setdefault(key, profile.get("name", key))
            return sorted(display_names.get(key, key) for key in self.whitelist)

    def set_whitelist_enabled(self, enabled: Any) -> bool:
        with self._lock:
            self.whitelist_enabled = bool(enabled)
            return self.save()

    def is_operator(self, name: Any) -> bool:
        with self._lock:
            return player_key(name) in self.operators

    def add_operator(self, name: Any) -> bool:
        key = player_key(name)
        if not key:
            return False
        with self._lock:
            self.operators.add(key)
            return self.save()

    def remove_operator(self, name: Any) -> bool:
        key = player_key(name)
        if not key:
            return False
        with self._lock:
            existed = key in self.operators
            self.operators.discard(key)
            if existed:
                self.save()
            return existed

    def operator_names(self) -> list[str]:
        with self._lock:
            names = []
            for key in sorted(self.operators):
                profile_name = self.profiles.get(key, {}).get("name")
                names.append(profile_name or key)
            return names

    def get_profile(self, name: Any) -> dict:
        with self._lock:
            return dict(self.profiles.get(player_key(name), {}))

    def get_last_position(self, name: Any):
        profile = self.get_profile(name)
        return _position(profile.get("pos"))

    def update_position(self, name: Any, pos: Any, yaw: Any = 0.0, pitch: Any = 0.0) -> bool:
        display = normalize_player_name(name)
        key = player_key(display)
        clean_pos = _position(pos)
        if not key or clean_pos is None:
            return False
        with self._lock:
            profile = self.profiles.setdefault(key, {"name": display})
            profile.update({
                "name": display,
                "pos": clean_pos,
                "yaw": _finite(yaw),
                "pitch": _finite(pitch, 0, -90, 90),
                "last_seen": time.time(),
            })
            return self.save()

    def update_inventory(self, name: Any, inventory: Any) -> bool:
        display = normalize_player_name(name)
        key = player_key(display)
        if not key or not isinstance(inventory, dict):
            return False
        from game.multiplayer.protocol import normalize_inventory_state
        normalized = normalize_inventory_state(inventory)
        with self._lock:
            profile = self.profiles.setdefault(key, {"name": display})
            profile["name"] = display
            profile["inventory"] = normalized
            profile["last_seen"] = time.time()
            return self.save()
