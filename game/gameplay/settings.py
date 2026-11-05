import os

import pygame as pg

from game.app.paths import user_data_root

OPTIONS_FILE = user_data_root() / "options.txt"


class KeyBinding:
    TRANSLATION_KEYS = {
        "Forward": "key.forward", "Left": "key.left",
        "Back": "key.back", "Right": "key.right", "Jump": "key.jump",
        "Sneak": "key.sneak", "Inventory": "key.inventory",
        "Drop": "key.drop", "Chat": "key.chat",
        "Toggle fog": "key.fog",
    }

    def __init__(self, description, key):
        self.description = description
        self.key = key

    @property
    def key_name(self):
        aliases = {
            getattr(pg, "K_LSHIFT", -1001): "LSHIFT",
            getattr(pg, "K_RSHIFT", -1002): "RSHIFT",
            getattr(pg, "K_LCTRL", -1003): "LCTRL",
            getattr(pg, "K_RCTRL", -1004): "RCTRL",
            getattr(pg, "K_LALT", -1005): "LALT",
            getattr(pg, "K_RALT", -1006): "RALT",
            getattr(pg, "K_RETURN", -1007): "RETURN",
            getattr(pg, "K_ESCAPE", -1008): "ESC",
            getattr(pg, "K_BACKSPACE", -1009): "BACK",
            getattr(pg, "K_PAGEUP", -1010): "PGUP",
            getattr(pg, "K_PAGEDOWN", -1011): "PGDN",
            getattr(pg, "K_KP_ENTER", -1012): "NUMPADENTER",
            getattr(pg, "K_CAPSLOCK", -1013): "CAPSLOCK",
        }
        if self.key in aliases:
            return aliases[self.key]
        try:
            name = pg.key.name(self.key)
        except Exception:
            name = ""
        return name.upper() if name else f"KEY {self.key}"

    @property
    def translated_description(self):
        from game.localization import translate
        translation_key = self.TRANSLATION_KEYS.get(self.description)
        if translation_key:
            return translate(translation_key, default=self.description)
        return self.description

    def label(self):
        return f"{self.translated_description}: {self.key_name}"


class Option:
    LABEL_KEYS = {
        "music_volume": "options.music",
        "sound_volume": "options.sound",
        "mouse_sensitivity": "options.sensitivity",
        "invert_mouse": "options.invertMouse",
        "render_distance": "options.renderDistance",
        "view_bobbing": "options.viewBobbing",
        "anaglyph": "options.anaglyph",
        "limit_framerate": "options.limitFramerate",
        "fancy_graphics": "options.graphics",
        "difficulty": "options.difficulty",
    }

    def __init__(self, attr, label, choices=None):
        self.attr = attr
        self.label = label
        self.choices = choices

    def display(self, settings):
        from game.localization import translate

        value = getattr(settings, self.attr)
        label = translate(self.LABEL_KEYS.get(self.attr, ""), default=self.label)
        if self.choices:
            text = self.choices[value]
            if self.attr == "render_distance":
                text = translate(
                    f"options.renderDistance.{str(text).lower()}", default=text,
                )
            elif self.attr == "difficulty":
                text = translate(
                    f"options.difficulty.{str(text).lower()}", default=text,
                )
        elif self.attr == "fancy_graphics":
            key = "options.graphics.fancy" if value else "options.graphics.fast"
            text = translate(key)
        else:
            text = translate("options.on" if value else "options.off")
        return f"{label}: {text}"

    def toggle(self, settings, step=1):
        value = getattr(settings, self.attr)
        if self.choices:
            setattr(settings, self.attr, (value + step) % len(self.choices))
        else:
            setattr(settings, self.attr, not value)

    def to_text(self, settings):
        value = getattr(settings, self.attr)
        return str(value) if self.choices else ("true" if value else "false")

    def from_text(self, settings, text):
        if self.choices:
            try:
                value = int(text)
            except ValueError:
                return
            setattr(settings, self.attr, max(0, min(value, len(self.choices) - 1)))
        else:
            setattr(settings, self.attr, text.strip().lower() == "true")


class SliderOption(Option):

    is_slider = True

    def __init__(self, attr, label, kind="volume"):
        super().__init__(attr, label)
        self.kind = kind

    def value(self, settings):
        return max(0.0, min(1.0, float(getattr(settings, self.attr))))

    def set_value(self, settings, value):
        value = round(max(0.0, min(1.0, float(value))) * 100.0) / 100.0
        setattr(settings, self.attr, value)

    def display(self, settings):
        from game.localization import translate

        value = self.value(settings)
        label = translate(Option.LABEL_KEYS.get(self.attr, ""), default=self.label)
        if self.kind == "sensitivity":
            if value <= 0.0:
                text = translate("options.sensitivity.min")
            elif value >= 1.0:
                text = translate("options.sensitivity.max")
            else:
                text = f"{int(round(value * 100))}%"
        else:
            text = translate("options.off") if value <= 0.0 else f"{int(round(value * 100))}%"
        return f"{label}: {text}"

    def toggle(self, settings, step=1):
        if self.kind == "volume":
            self.set_value(settings, 1.0 if self.value(settings) <= 0.0 else 0.0)

    def to_text(self, settings):
        return f"{self.value(settings):.2f}"

    def from_text(self, settings, text):
        try:
            self.set_value(settings, float(text))
        except ValueError:
            return


def sensitivity_scale(value):
    f = max(0.0, min(1.0, float(value))) * 0.6 + 0.2
    return f * f * f * 8.0


class GameSettings:
    RENDER_DISTANCES = ["FAR", "NORMAL", "SHORT", "TINY"]
    DIFFICULTIES = ["Peaceful", "Easy", "Normal", "Hard"]
    RENDER_CHUNKS = [13, 8, 4, 2]

    def __init__(self, path=OPTIONS_FILE):
        self.path = path

        self.music_volume = 1.0
        self.sound_volume = 1.0
        self.mouse_sensitivity = 0.5
        self.invert_mouse = False
        self.render_distance = 1
        self.view_bobbing = True
        self.anaglyph = False
        self.limit_framerate = False
        self.difficulty = 2
        self.fancy_graphics = True
        self.texture_pack = "vanilla"
        self.username = "Player"
        self.username_set = False

        self.key_forward = KeyBinding("Forward", pg.K_w)
        self.key_left = KeyBinding("Left", pg.K_a)
        self.key_back = KeyBinding("Back", pg.K_s)
        self.key_right = KeyBinding("Right", pg.K_d)
        self.key_jump = KeyBinding("Jump", pg.K_SPACE)
        self.key_sneak = KeyBinding("Sneak", pg.K_LSHIFT)
        self.key_inventory = KeyBinding("Inventory", pg.K_i)
        self.key_drop = KeyBinding("Drop", pg.K_q)
        self.key_chat = KeyBinding("Chat", pg.K_t)
        self.key_toggle_fog = KeyBinding("Toggle fog", pg.K_f)
        self.key_debug = KeyBinding("Debug", pg.K_F3)

        self.key_bindings = [
            self.key_forward, self.key_left, self.key_back, self.key_right,
            self.key_jump, self.key_sneak, self.key_drop, self.key_inventory,
            self.key_chat, self.key_toggle_fog,
        ]

        self.options = [
            SliderOption("music_volume", "Music"),
            SliderOption("sound_volume", "Sound"),
            SliderOption("mouse_sensitivity", "Sensitivity", "sensitivity"),
            Option("invert_mouse", "Invert mouse"),
            Option("render_distance", "Render distance", self.RENDER_DISTANCES),
            Option("view_bobbing", "View bobbing"),
            Option("anaglyph", "3d anaglyph"),
            Option("limit_framerate", "Limit framerate"),
            Option("fancy_graphics", "Graphics"),
            Option("difficulty", "Difficulty", self.DIFFICULTIES),
        ]

        self.load()

    @property
    def music(self):
        return self.music_volume > 0.0

    @music.setter
    def music(self, enabled):
        if not enabled:
            self.music_volume = 0.0
        elif self.music_volume <= 0.0:
            self.music_volume = 1.0

    @property
    def sound(self):
        return self.sound_volume > 0.0

    @sound.setter
    def sound(self, enabled):
        if not enabled:
            self.sound_volume = 0.0
        elif self.sound_volume <= 0.0:
            self.sound_volume = 1.0

    @property
    def mouse_sensitivity_scale(self):
        return sensitivity_scale(self.mouse_sensitivity)

    @property
    def render_chunks(self):
        return self.RENDER_CHUNKS[self.render_distance]

    def toggle_option(self, index, step=1):
        self.options[index].toggle(self, step)
        self.save()

    def set_key_binding(self, index, key):
        self.key_bindings[index].key = key
        self.save()

    def load(self):
        if not os.path.exists(self.path):
            return
        self._seen_username_set = False
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    name, _, value = line.strip().partition(":")
                    if name:
                        self._apply_saved(name, value)
        except Exception as e:
            print(f"Failed to load options: {e}")
            return
        if not self._seen_username_set and self.username != "Player":
            self.username_set = True

    def _apply_saved(self, name, value):
        if name == "texture_pack":
            self.texture_pack = value.strip() or "vanilla"
            return
        if name == "username_set":
            self._seen_username_set = True
            self.username_set = value.strip().lower() == "true"
            return
        if name == "username":
            self.username = value.strip() or "Player"
            return
        for opt in self.options:
            if opt.attr == name:
                opt.from_text(self, value)
                return
        if name in ("music", "sound"):
            if value.strip().lower() != "true":
                setattr(self, name + "_volume", 0.0)
            return
        if name.startswith("key_"):
            for kb in self.key_bindings:
                if name == "key_" + kb.description:
                    try:
                        saved_key = int(value)
                        kb.key = saved_key
                    except ValueError:
                        pass
                    return

    def save(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(f"username:{self.username}\n")
                f.write(f"username_set:{'true' if self.username_set else 'false'}\n")
                f.write(f"texture_pack:{self.texture_pack}\n")
                for opt in self.options:
                    f.write(f"{opt.attr}:{opt.to_text(self)}\n")
                for kb in self.key_bindings:
                    f.write(f"key_{kb.description}:{kb.key}\n")
        except Exception as e:
            print(f"Failed to save options: {e}")
