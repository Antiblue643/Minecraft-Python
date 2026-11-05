
from dataclasses import dataclass
import importlib.util
import json
import logging
import math
from pathlib import Path
import re
import sys

from game.app.paths import PROJECT_ROOT, user_data_root

MODS_ROOT = user_data_root() / "mods"
MOD_CONFIG_ROOT = MODS_ROOT / "_configs"
_BUNDLED_MODS_ROOT = PROJECT_ROOT / "mods"
_MOD_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_LOG = logging.getLogger(__name__)
_LOADED_MODULES = {}
_STARTUP_ENABLED = {}
_FAILED_HOOKS = set()


def _registered_content_keys():
    try:
        from game.entities.item import Item
        return set(Item.item_defs), set(Item.block_defs)
    except Exception:
        return set(), set()


def _tag_mod_content(identifier, previous):
    try:
        from game.entities.item import Item
        previous_items, previous_blocks = previous
        for item_id in set(Item.item_defs) - previous_items:
            definition = Item.item_defs.get(item_id)
            if isinstance(definition, dict):
                definition.setdefault("mod_id", identifier)
        for block_id in set(Item.block_defs) - previous_blocks:
            definition = Item.block_defs.get(block_id)
            if isinstance(definition, dict):
                definition.setdefault("mod_id", identifier)
    except Exception:
        _LOG.exception("Could not tag content registered by mod %s", identifier)


@dataclass(frozen=True)
class ExternalMod:
    identifier: str
    title: str
    description: str
    version: str
    default_enabled: bool
    directory: Path
    entry: Path
    icon_path: str
    multiplayer_compatible: bool = True


@dataclass(frozen=True)
class ModSettingsEntry:
    identifier: str
    title: str
    settings: tuple
    on_config_changed: object = None


def discover_external_mods(root=None):
    roots = (Path(root),) if root is not None else (
        _BUNDLED_MODS_ROOT, MODS_ROOT,
    )
    mods_by_id = {}
    seen_roots = set()
    for root in roots:
        root = root.resolve()
        if root in seen_roots or not root.is_dir():
            continue
        seen_roots.add(root)
        mods_by_id.update(_discover_mods_in_root(root))
    return tuple(mods_by_id[key] for key in sorted(mods_by_id))


def _discover_mods_in_root(root):
    mods = []
    for directory in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        manifest_path = directory / "mod.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest must contain a JSON object")
            identifier = manifest.get("id")
            if not isinstance(identifier, str) or not _MOD_ID.fullmatch(identifier):
                raise ValueError("id must contain lowercase letters, digits, '_' or '-'")
            if identifier != directory.name:
                raise ValueError("id must match the mod folder name")
            title = manifest.get("name", identifier)
            description = manifest.get("description", "")
            version = manifest.get("version", "")
            entry_name = manifest.get("entry", "mod.py")
            default_enabled = manifest.get("default_enabled", False)
            if not all(isinstance(value, str) for value in (title, description, version, entry_name)):
                raise ValueError("name, description, version, and entry must be strings")
            if not isinstance(default_enabled, bool):
                raise ValueError("default_enabled must be a boolean")
            multiplayer_compatible = manifest.get("multiplayer_compatible", None)
            content_heavy = manifest.get("content_heavy", False)
            if multiplayer_compatible is None:
                multiplayer_compatible = not bool(content_heavy)
            if not isinstance(multiplayer_compatible, bool):
                raise ValueError("multiplayer_compatible must be a boolean")
            if not isinstance(content_heavy, bool):
                raise ValueError("content_heavy must be a boolean")
            if content_heavy:
                multiplayer_compatible = False
            entry = (directory / entry_name).resolve()
            entry.relative_to(directory.resolve())
            if not entry.is_file() or entry.suffix.lower() != ".py":
                raise ValueError("entry must name an existing Python file inside the mod folder")
            icon = directory / "pack.png"
            mods.append(ExternalMod(
                identifier=identifier,
                title=title or identifier,
                description=description,
                version=version,
                default_enabled=default_enabled,
                directory=directory.resolve(),
                entry=entry,
                icon_path=str(icon if icon.is_file() else ""),
                multiplayer_compatible=multiplayer_compatible,
            ))
        except (OSError, ValueError) as exc:
            _LOG.warning("Skipping external mod %s: %s", manifest_path, exc)
    return {mod.identifier: mod for mod in mods}


def mod_config_path(identifier):
    if not isinstance(identifier, str) or not _MOD_ID.fullmatch(identifier):
        raise ValueError(f"Invalid mod identifier: {identifier!r}")
    return MOD_CONFIG_ROOT / identifier / "config.json"


def load_mod_config(identifier, defaults=None):
    config = dict(defaults or {})
    path = mod_config_path(identifier)
    if not path.is_file():
        return config
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise ValueError("config must contain a JSON object")
        config.update(saved)
    except (OSError, ValueError) as exc:
        _LOG.warning("Could not load config for mod %s from %s: %s", identifier, path, exc)
    return config


def save_mod_config(identifier, config):
    if not isinstance(config, dict):
        raise TypeError("mod config must be a dictionary")
    path = mod_config_path(identifier)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def is_mod_enabled(identifier, default=False):
    return bool(load_mod_config(identifier, {"enabled": bool(default)}).get("enabled", default))


def enabled_multiplayer_blocking_mods(root=None):
    blocking = []
    for mod in discover_external_mods(root):
        if not is_mod_enabled(mod.identifier, mod.default_enabled):
            continue
        if not getattr(mod, "multiplayer_compatible", True):
            blocking.append(mod)
    return tuple(blocking)


def multiplayer_blocked_by_mods(root=None):
    blocking = enabled_multiplayer_blocking_mods(root)
    if not blocking:
        return False, ""
    names = ", ".join(mod.title or mod.identifier for mod in blocking)
    from game.localization import translate
    if len(blocking) == 1:
        reason = translate(
            "mcpy.multiplayer.blockedByMod",
            blocking[0].title or blocking[0].identifier,
            default=(
                "Multiplayer is disabled while the content mod "
                "'%s' is enabled."
            ),
        )
    else:
        reason = translate(
            "mcpy.multiplayer.blockedByMods", names,
        )
    return True, reason


def set_mod_enabled(identifier, enabled, default=False):
    config = load_mod_config(identifier, {"enabled": bool(default)})
    config["enabled"] = bool(enabled)
    save_mod_config(identifier, config)


def mod_restart_required(identifier, default=False):
    current = is_mod_enabled(identifier, default)
    startup = _STARTUP_ENABLED.setdefault(identifier, current)
    return current != startup


def available_mod_settings():
    metadata = {mod.identifier: mod for mod in discover_external_mods()}
    entries = []
    for identifier, module in _LOADED_MODULES.items():
        definitions = getattr(module, "SETTINGS", ())
        if not isinstance(definitions, (tuple, list)) or not definitions:
            continue
        try:
            settings = _validate_mod_settings(identifier, definitions)
        except (TypeError, ValueError) as exc:
            _LOG.warning("Ignoring settings for external mod %s: %s", identifier, exc)
            continue
        mod = metadata.get(identifier)
        on_config_changed = getattr(module, "on_config_changed", None)
        from game.localization import translate_mod
        default_title = mod.title if mod is not None else identifier
        localized_title = translate_mod(identifier, "mod.name", default=default_title)
        entries.append(ModSettingsEntry(
            identifier=identifier,
            title=localized_title,
            settings=settings,
            on_config_changed=on_config_changed if callable(on_config_changed) else None,
        ))
    return tuple(entries)


def _validate_mod_settings(identifier, definitions):
    settings = []
    seen_keys = set()
    for definition in definitions:
        if not isinstance(definition, dict):
            raise TypeError("each setting must be a dictionary")
        key, label, kind = (
            definition.get("key"), definition.get("label"), definition.get("type")
        )
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", key):
            raise ValueError("setting keys must contain letters, digits, '_' or '-'")
        if key in seen_keys:
            raise ValueError(f"duplicate setting key {key!r}")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"setting {key!r} needs a non-empty label")
        if kind not in ("toggle", "slider", "text"):
            raise ValueError(f"setting {key!r} has unsupported type {kind!r}")
        if kind == "toggle":
            if not isinstance(definition.get("default"), bool):
                raise ValueError(f"toggle setting {key!r} needs a boolean default")
            setting = {"key": key, "label": label, "type": kind,
                       "default": definition["default"]}
        elif kind == "text":
            default = definition.get("default", "")
            if not isinstance(default, str):
                raise ValueError(f"text setting {key!r} needs a string default")
            max_length = definition.get("max_length", 256)
            if (
                isinstance(max_length, bool)
                or not isinstance(max_length, int)
                or max_length < 1
            ):
                raise ValueError(f"text setting {key!r} needs a positive integer max_length")
            setting = {
                "key": key, "label": label, "type": kind,
                "default": default, "max_length": max_length,
            }
        else:
            minimum = definition.get("min", 0.0)
            maximum = definition.get("max", 1.0)
            default = definition.get("default")
            if (
                isinstance(minimum, bool) or not isinstance(minimum, (int, float))
                or isinstance(maximum, bool) or not isinstance(maximum, (int, float))
                or isinstance(default, bool) or not isinstance(default, (int, float))
            ):
                raise ValueError(f"slider setting {key!r} needs numeric min, max and default")
            if (
                not all(math.isfinite(value) for value in (minimum, maximum, default))
                or not minimum < maximum
                or not minimum <= default <= maximum
            ):
                raise ValueError(f"slider setting {key!r} has an invalid range or default")
            setting = {
                "key": key, "label": label, "type": kind,
                "default": default, "min": minimum, "max": maximum,
            }
        seen_keys.add(key)
        settings.append(setting)
    if not settings:
        raise ValueError(f"mod {identifier!r} has no settings")
    return tuple(settings)


def load_enabled_mods(game, root=None):
    loaded = []
    for mod in discover_external_mods(root):
        enabled = is_mod_enabled(mod.identifier, mod.default_enabled)
        _STARTUP_ENABLED[mod.identifier] = enabled
        if not enabled:
            continue
        if mod.identifier in _LOADED_MODULES:
            initialize = getattr(_LOADED_MODULES[mod.identifier], "initialize", None)
            if callable(initialize):
                try:
                    previous_content = _registered_content_keys()
                    initialize(game)
                    _tag_mod_content(mod.identifier, previous_content)
                except Exception:
                    _LOG.exception("Failed to initialize external mod %s", mod.identifier)
                    continue
            loaded.append(mod.identifier)
            continue

        module_name = f"_minecraftpy_mod_{mod.identifier.replace('-', '_')}"
        previous_content = _registered_content_keys()
        try:
            spec = importlib.util.spec_from_file_location(
                module_name,
                mod.entry,
                submodule_search_locations=[str(mod.directory)],
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"could not create an import loader for {mod.entry}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            initialize = getattr(module, "initialize", None)
            if initialize is not None:
                if not callable(initialize):
                    raise TypeError("initialize must be callable")
                initialize(game)
            _tag_mod_content(mod.identifier, previous_content)
            _LOADED_MODULES[mod.identifier] = module
            loaded.append(mod.identifier)
        except Exception:
            sys.modules.pop(module_name, None)
            _LOG.exception("Failed to load external mod %s", mod.identifier)
    return tuple(loaded)


def dispatch_mod_hook(name, *args, **kwargs):
    for identifier, module in tuple(_LOADED_MODULES.items()):
        if (identifier, name) in _FAILED_HOOKS:
            continue
        hook = getattr(module, name, None)
        if not callable(hook):
            continue
        try:
            hook(*args, **kwargs)
        except Exception:
            _FAILED_HOOKS.add((identifier, name))
            _LOG.exception("Mod %s hook %s failed; disabling that hook", identifier, name)
