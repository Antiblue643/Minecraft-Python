
from __future__ import annotations

import re
import zipfile
from pathlib import Path

DEFAULT_LANGUAGE = "en_US"


def _unescape(value: str) -> str:
    result = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\" or index + 1 >= len(value):
            result.append(char)
            index += 1
            continue
        index += 1
        escaped = value[index]
        index += 1
        simple = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}
        if escaped in simple:
            result.append(simple[escaped])
        elif escaped == "u" and index + 4 <= len(value):
            digits = value[index:index + 4]
            try:
                result.append(chr(int(digits, 16)))
                index += 4
            except ValueError:
                result.extend(("\\u", digits))
                index += 4
        else:
            result.append(escaped)
    return "".join(result)


def parse_lang_text(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    logical_lines = []
    pending = ""
    for raw in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.lstrip() if pending else raw
        if not pending and (not line.strip() or line.lstrip().startswith(("#", "!"))):
            continue
        slash_count = len(line) - len(line.rstrip("\\"))
        if slash_count % 2:
            pending += line[:-1]
            continue
        logical_lines.append(pending + line)
        pending = ""
    if pending:
        logical_lines.append(pending)

    for line in logical_lines:
        line = line.lstrip()
        if not line or line.startswith(("#", "!")):
            continue
        separator = None
        escaped = False
        for index, char in enumerate(line):
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char in "=:" or char.isspace():
                separator = index
                break
        if separator is None:
            key, value = line, ""
        else:
            key = line[:separator]
            value_start = separator
            while value_start < len(line) and line[value_start].isspace():
                value_start += 1
            if value_start < len(line) and line[value_start] in "=:":
                value_start += 1
            while value_start < len(line) and line[value_start].isspace():
                value_start += 1
            value = line[value_start:]
        key = _unescape(key)
        if key:
            result[key] = _unescape(value)
    return result


def _language_files(pack_name: str | None = None):
    from game.app import texturepacks

    selected_name = (
        texturepacks.get_active_texture_pack()
        if pack_name is None else str(pack_name)
    )
    vanilla = texturepacks._pack_path("vanilla")
    selected = texturepacks._pack_path(selected_name)
    packs = []
    for pack in (vanilla, selected):
        if pack is not None and pack not in packs:
            packs.append(pack)
    return texturepacks, packs, selected_name


def available_languages(pack_name: str | None = None) -> tuple[str, ...]:
    texturepacks, packs, _selected_name = _language_files(pack_name)
    languages = {DEFAULT_LANGUAGE}
    for pack in packs:
        try:
            if pack.is_dir():
                languages.update(
                    path.stem for path in (pack / "lang").glob("*.lang")
                    if path.is_file()
                )
            elif pack.is_file() and pack.suffix.lower() == ".zip":
                with zipfile.ZipFile(pack) as archive:
                    for name in archive.namelist():
                        match = re.fullmatch(r"lang/([^/]+)\.lang", name)
                        if match:
                            languages.add(match.group(1))
        except (OSError, zipfile.BadZipFile):
            continue
    return tuple(sorted(languages, key=lambda value: (value != DEFAULT_LANGUAGE, value.casefold())))


def _read_lang_file(path: Path) -> dict[str, str]:
    try:
        return parse_lang_text(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError):
        return {}


def _read_project_locale(locale: str) -> dict[str, str]:
    path = Path(__file__).resolve().parent / "lang" / f"{locale}.lang"
    return _read_lang_file(path)


def _discover_mod_language_dirs():
    try:
        from game.app.mods import discover_external_mods
        return tuple(
            (mod.identifier, Path(mod.directory))
            for mod in discover_external_mods()
        )
    except Exception:
        return ()


class LanguageManager:

    def __init__(self):
        self.language = DEFAULT_LANGUAGE
        self.pack_name = "vanilla"
        self.translations: dict[str, str] = {}
        self.mod_translations: dict[str, dict[str, str]] = {}
        self._loaded = False
        self._follow_active_pack = True

    @staticmethod
    def _read_pack_locale(texturepacks, pack, locale: str) -> dict[str, str]:
        relative = Path("lang") / f"{locale}.lang"
        path = texturepacks._pack_asset_path(pack, relative)
        if path is None:
            return {}
        return _read_lang_file(Path(path))

    @staticmethod
    def _read_mod_locale(directory: Path, locale: str) -> dict[str, str]:
        return _read_lang_file(directory / "lang" / f"{locale}.lang")

    def load(self, language: str = DEFAULT_LANGUAGE,
             pack_name: str | None = None) -> None:
        language = str(language or DEFAULT_LANGUAGE).strip() or DEFAULT_LANGUAGE
        texturepacks, packs, selected_name = _language_files(pack_name)
        self._follow_active_pack = pack_name is None

        merged: dict[str, str] = {}
        for pack in packs:
            merged.update(self._read_pack_locale(texturepacks, pack, DEFAULT_LANGUAGE))
        if language != DEFAULT_LANGUAGE:
            for pack in packs:
                merged.update(self._read_pack_locale(texturepacks, pack, language))
        merged.update(_read_project_locale(DEFAULT_LANGUAGE))
        if language != DEFAULT_LANGUAGE:
            merged.update(_read_project_locale(language))

        mod_tables: dict[str, dict[str, str]] = {}
        for mod_id, directory in _discover_mod_language_dirs():
            table = self._read_mod_locale(directory, DEFAULT_LANGUAGE)
            if language != DEFAULT_LANGUAGE:
                table.update(self._read_mod_locale(directory, language))
            mod_tables[mod_id] = table

        self.translations = merged
        self.mod_translations = mod_tables
        self.language = language
        self.pack_name = selected_name
        self._loaded = True

    def ensure_loaded(self) -> None:
        from game.app.texturepacks import get_active_texture_pack
        active_pack = get_active_texture_pack()
        if not self._loaded:
            self.load(self.language)
        elif self._follow_active_pack and self.pack_name != active_pack:
            self.load(self.language, active_pack)

    @staticmethod
    def _format(value: str, args) -> str:
        if args:
            try:
                value = value % args
            except (TypeError, ValueError, KeyError):
                pass
        return value

    def translate(self, key: str, *args, default=None) -> str:
        self.ensure_loaded()
        value = self.translations.get(str(key))
        if value is None:
            value = str(key) if default is None else str(default)
        return self._format(value, args)

    def translate_mod(self, mod_id: str, key: str, *args, default=None) -> str:
        self.ensure_loaded()
        translations = self.mod_translations.get(str(mod_id), {})
        value = translations.get(str(key))
        if value is None:
            value = str(key) if default is None else str(default)
        return self._format(value, args)


_LANGUAGE_MANAGER = LanguageManager()


def set_language(language: str = DEFAULT_LANGUAGE,
                 pack_name: str | None = None) -> None:
    _LANGUAGE_MANAGER.load(language, pack_name)


def translate(key: str, *args, default=None) -> str:
    return _LANGUAGE_MANAGER.translate(key, *args, default=default)


def translate_mod(mod_id: str, key: str, *args, default=None) -> str:
    return _LANGUAGE_MANAGER.translate_mod(mod_id, key, *args, default=default)


def current_language() -> str:
    _LANGUAGE_MANAGER.ensure_loaded()
    return _LANGUAGE_MANAGER.language
