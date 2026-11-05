
from dataclasses import dataclass
import hashlib
from pathlib import Path
import tempfile
import zipfile

from game.app.paths import PROJECT_ROOT, user_data_root
from game.app.mods import discover_external_mods, is_mod_enabled

TEXTUREPACKS_ROOT = user_data_root() / "texturepacks"
VANILLA_TEXTURE_ROOT = PROJECT_ROOT / "texturepacks" / "vanilla"
MODS_ROOT = user_data_root() / "mods"

_LEGACY_LAYOUT = {
    "char.png": "mob/char.png",
    "clouds.png": "environment/clouds.png",
    "default.png": "font/default.png",
    "rain.png": "environment/rain.png",
    "shadow.png": "misc/shadow.png",
    "snow.png": "environment/snow.png",
    "water.png": "misc/water.png",
}
_ACTIVE_TEXTURE_PACK = "vanilla"


@dataclass(frozen=True)
class PackListEntry:

    kind: str
    identifier: str
    title: str
    description: str
    details: str
    icon_path: str
    setting_attr: str = ""
    enabled: bool = False
    default_enabled: bool = False


def get_active_texture_pack():
    return _ACTIVE_TEXTURE_PACK


def _pack_path(pack_name):
    if pack_name in (None, "", "vanilla", "Default"):
        return VANILLA_TEXTURE_ROOT
    candidate = TEXTUREPACKS_ROOT / str(pack_name)
    if candidate.is_dir() or (candidate.is_file() and candidate.suffix.lower() == ".zip"):
        return candidate
    if not str(pack_name).lower().endswith(".zip"):
        zipped = TEXTUREPACKS_ROOT / f"{pack_name}.zip"
        if zipped.is_file():
            return zipped
    return None


def set_active_texture_pack(pack_name):
    global _ACTIVE_TEXTURE_PACK
    candidate = _pack_path(pack_name)
    _ACTIVE_TEXTURE_PACK = (
        "vanilla" if candidate is None or candidate == VANILLA_TEXTURE_ROOT
        else candidate.name
    )
    return _ACTIVE_TEXTURE_PACK


def installed_texture_packs():
    if not VANILLA_TEXTURE_ROOT.is_dir():
        raise FileNotFoundError(
            f"Required vanilla texture pack is missing: {VANILLA_TEXTURE_ROOT}"
        )
    names = []
    if TEXTUREPACKS_ROOT.is_dir():
        names = [
            path.name for path in TEXTUREPACKS_ROOT.iterdir()
            if not path.name.startswith(".") and (
                path.is_dir() or (path.is_file() and path.suffix.lower() == ".zip")
            ) and path.name != "vanilla"
        ]
    return tuple(["vanilla", *sorted(names, key=str.casefold)])


def _read_pack_text(pack_path):
    try:
        if pack_path.is_dir():
            lines = (pack_path / "pack.txt").read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        else:
            with zipfile.ZipFile(pack_path) as archive:
                with archive.open("pack.txt") as stream:
                    lines = stream.read().decode("utf-8", "replace").splitlines()
        return tuple(line[:34] for line in lines[:2])
    except (OSError, KeyError, zipfile.BadZipFile, UnicodeError):
        return ()


def _extract_zip_asset(pack_path, relative_path):
    try:
        stat = pack_path.stat()
        digest = hashlib.sha1(
            f"{pack_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
        ).hexdigest()[:16]
        member = Path(relative_path)
        if member.is_absolute() or ".." in member.parts:
            return None
        cache_root = Path(tempfile.gettempdir()) / "minecraftpy_texturepacks" / digest
        target = (cache_root / member).resolve()
        target.relative_to(cache_root.resolve())
        if target.is_file():
            return target
        with zipfile.ZipFile(pack_path) as archive:
            try:
                info = archive.getinfo(member.as_posix())
            except KeyError:
                return None
            if info.is_dir():
                return None
            data = archive.read(info)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target
    except (OSError, ValueError, zipfile.BadZipFile):
        return None


def _pack_asset_path(pack_path, relative_path):
    relative = Path(relative_path)
    alias = _LEGACY_LAYOUT.get(relative.as_posix())
    if pack_path.is_dir():
        candidates = [Path(alias), relative] if alias else [relative]
        for candidate in candidates:
            target = (pack_path / candidate).resolve()
            try:
                target.relative_to(pack_path.resolve())
            except ValueError:
                continue
            if target.is_file():
                return target
        return None

    candidates = [relative, Path(alias)] if alias else [relative]
    for candidate in candidates:
        extracted = _extract_zip_asset(pack_path, candidate.as_posix())
        if extracted is not None:
            return extracted
    return None


def resolve_texture_path(filepath, pack_name=None):
    if not isinstance(filepath, (str, Path)):
        return filepath

    path = Path(filepath).expanduser()
    old_root = (PROJECT_ROOT / "game" / "textures").resolve()
    try:
        relative = path.resolve().relative_to(old_root)
    except (OSError, ValueError):
        normalized = str(filepath).replace("\\", "/")
        prefix = "game/textures/"
        if normalized.startswith(prefix):
            relative = Path(normalized[len(prefix):])
        else:
            return str(filepath)

    selected_name = _ACTIVE_TEXTURE_PACK if pack_name is None else str(pack_name)
    selected_root = _pack_path(selected_name)
    if selected_root is None:
        selected_root = VANILLA_TEXTURE_ROOT
    if selected_root == VANILLA_TEXTURE_ROOT and not selected_root.is_dir():
        raise FileNotFoundError(
            f"Required texture pack folder is missing: {selected_root}"
        )
    resolved = _pack_asset_path(selected_root, relative)
    if resolved is not None:
        return str(resolved)

    if selected_root != VANILLA_TEXTURE_ROOT:
        fallback = _pack_asset_path(VANILLA_TEXTURE_ROOT, relative)
        if fallback is not None:
            return str(fallback)

    vanilla_relative = Path(_LEGACY_LAYOUT.get(relative.as_posix(), relative.as_posix()))
    return str(VANILLA_TEXTURE_ROOT / vanilla_relative)


def _pack_icon_path(pack_path):
    icon = _pack_asset_path(pack_path, "pack.png")
    if icon is not None:
        return str(icon)
    return str(VANILLA_TEXTURE_ROOT / "gui" / "unknown_pack.png")


def discover_pack_entries(settings=None):
    from game.localization import translate, translate_mod

    if not VANILLA_TEXTURE_ROOT.is_dir():
        raise FileNotFoundError(
            f"Required vanilla texture pack is missing: {VANILLA_TEXTURE_ROOT}"
        )

    entries = [PackListEntry(
        kind="texturepack",
        identifier="vanilla",
        title=translate("mcpy.texturePack.defaultName"),
        description=translate(
            "mcpy.texturePack.defaultDescription",
        ),
        details="",
        icon_path=str(VANILLA_TEXTURE_ROOT / "pack.png"),
    )]

    if TEXTUREPACKS_ROOT.is_dir():
        custom_paths = sorted(
            (
                path for path in TEXTUREPACKS_ROOT.iterdir()
                if not path.name.startswith(".") and path.name != "vanilla"
                and (path.is_dir() or (path.is_file() and path.suffix.lower() == ".zip"))
            ),
            key=lambda item: item.name.casefold(),
        )
        for path in custom_paths:
            metadata = _read_pack_text(path)
            description = metadata[0] if len(metadata) > 0 else ""
            details = metadata[1] if len(metadata) > 1 else ""
            entries.append(PackListEntry(
                kind="texturepack",
                identifier=path.name,
                title=path.stem if path.suffix.lower() == ".zip" else path.name,
                description=description,
                details=details,
                icon_path=_pack_icon_path(path),
            ))

    external_mods = {
        mod.identifier: mod for mod in discover_external_mods()
    }
    external_mods.update({
        mod.identifier: mod for mod in discover_external_mods(MODS_ROOT)
    })
    for mod in (external_mods[key] for key in sorted(external_mods)):
        entries.append(PackListEntry(
            kind="mod",
            identifier=mod.identifier,
            title=translate_mod(mod.identifier, "mod.name", default=mod.title),
            description=translate_mod(
                mod.identifier, "mod.description", default=mod.description,
            ),
            details=(
                translate("mcpy.mods.version", mod.version)
                if mod.version else translate(
                    "mcpy.mods.externalPythonMod"
                )
            ),
            icon_path=mod.icon_path or str(
                VANILLA_TEXTURE_ROOT / "gui" / "unknown_pack.png"
            ),
            setting_attr=f"mod_enabled:{mod.identifier}",
            enabled=is_mod_enabled(mod.identifier, mod.default_enabled),
            default_enabled=mod.default_enabled,
        ))

    return tuple(entries)
