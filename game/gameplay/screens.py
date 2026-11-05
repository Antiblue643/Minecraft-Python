import math
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pygame as pg
from OpenGL.GL import *

from engine.textures import Texture
from game.entities import player_model
from game.multiplayer.netutils import format_host_port
from game.rendering.block_text import AnimatedBlockText, MINECRAFT_LOGO_LAYERS
from game.localization import translate
from .crafting import CraftingGrid, CraftingManager, CraftResult
from .splash_text import select_splash_text

_GUI_TEXTURE_PATH = "game/textures/gui/gui.png"
_LOGO_TEXTURE_PATH = "game/textures/gui/logo.png"

_BG_TOP = 0xC0101010
_BG_BOTTOM = 0xD0303030
VERSION_TEXT = "Beta 1.1_02"
_LOG = logging.getLogger(__name__)

def _rgb(color):
    return (color >> 16) & 255, (color >> 8) & 255, color & 255


def draw_string(display, text, x, y, color=0xFFFFFF, shadow=True):
    display.draw_string(text, (x, y), _rgb(color), 1.0, shadow=shadow)


def draw_centered_string(display, text, center_x, y, color=0xFFFFFF):
    draw_string(display, text, center_x - display.string_width(text) // 2, y, color)

def draw_centered_shadow_string(display, text, center_x, y, color=0xFFFFFF):
    draw_string(display, text, center_x - display.string_width(text) // 2, y, color, shadow=True)


def draw_gradient_rect(x1, y1, x2, y2, top_argb, bottom_argb):
    def unpack(c):
        return ((c >> 16) & 255) / 255.0, ((c >> 8) & 255) / 255.0, (c & 255) / 255.0, ((c >> 24) & 255) / 255.0

    glDisable(GL_TEXTURE_2D)
    glDisable(GL_ALPHA_TEST)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glBegin(GL_QUADS)
    glColor4f(*unpack(top_argb))
    glVertex2f(x2, y1)
    glVertex2f(x1, y1)
    glColor4f(*unpack(bottom_argb))
    glVertex2f(x1, y2)
    glVertex2f(x2, y2)
    glEnd()
    glDisable(GL_BLEND)


def draw_texture_pack_list_edge_fades(width, list_top, list_bottom):
    if width <= 0 or list_bottom <= list_top:
        return

    fade = 4.0  
    glDisable(GL_TEXTURE_2D)
    glDisable(GL_ALPHA_TEST)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glShadeModel(GL_SMOOTH)
    glBegin(GL_QUADS)

    glColor4f(0.0, 0.0, 0.0, 1.0)
    glVertex2f(0.0, float(list_top))
    glVertex2f(float(width), float(list_top))
    glColor4f(0.0, 0.0, 0.0, 0.0)
    glVertex2f(float(width), float(list_top) + fade)
    glVertex2f(0.0, float(list_top) + fade)

    glColor4f(0.0, 0.0, 0.0, 0.0)
    glVertex2f(0.0, float(list_bottom) - fade)
    glVertex2f(float(width), float(list_bottom) - fade)
    glColor4f(0.0, 0.0, 0.0, 1.0)
    glVertex2f(float(width), float(list_bottom))
    glVertex2f(0.0, float(list_bottom))

    glEnd()
    glShadeModel(GL_FLAT)
    glDisable(GL_BLEND)
    glEnable(GL_ALPHA_TEST)
    glColor4f(1.0, 1.0, 1.0, 1.0)


def draw_pack_screen_background(width, height):
    tex = Texture("game/textures/gui/background.png")
    glEnable(GL_TEXTURE_2D)
    glDisable(GL_BLEND)
    tex.bind()
    base_tint = 64.0 / 255.0
    glColor4f(base_tint, base_tint, base_tint, 1.0)
    glBegin(GL_QUADS)
    glTexCoord2f(0.0, 1.0 - height / 32.0); glVertex2f(0, height)
    glTexCoord2f(width / 32.0, 1.0 - height / 32.0); glVertex2f(width, height)
    glTexCoord2f(width / 32.0, 1.0); glVertex2f(width, 0)
    glTexCoord2f(0.0, 1.0); glVertex2f(0, 0)
    glEnd()
    tex.unbind()
    glDisable(GL_TEXTURE_2D)
    glColor4f(1.0, 1.0, 1.0, 1.0)


def draw_pack_list_panel(width, list_top, list_bottom, scroll=0):
    if list_bottom <= list_top:
        return
    tex = Texture("game/textures/gui/background.png")
    glEnable(GL_TEXTURE_2D)
    glDisable(GL_BLEND)
    tex.bind()
    list_tint = 32.0 / 255.0
    glColor4f(list_tint, list_tint, list_tint, 1.0)
    top_v = 1.0 - (list_top + scroll) / 32.0
    bottom_v = 1.0 - (list_bottom + scroll) / 32.0
    glBegin(GL_QUADS)
    glTexCoord2f(0.0, bottom_v); glVertex2f(0.0, list_bottom)
    glTexCoord2f(width / 32.0, bottom_v); glVertex2f(width, list_bottom)
    glTexCoord2f(width / 32.0, top_v); glVertex2f(width, list_top)
    glTexCoord2f(0.0, top_v); glVertex2f(0.0, list_top)
    glEnd()
    tex.unbind()
    glDisable(GL_TEXTURE_2D)
    glColor4f(1.0, 1.0, 1.0, 1.0)


def draw_tex_rect(tex, x, y, u, v, w, h):
    aw, ah = max(tex.width, 1), max(tex.height, 1)
    u0, u1 = u / aw, (u + w) / aw
    v0, v1 = 1.0 - (v + h) / ah, 1.0 - v / ah

    glEnable(GL_TEXTURE_2D)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    tex.bind()
    glBegin(GL_QUADS)
    glTexCoord2f(u0, v0); glVertex2f(x, y + h)
    glTexCoord2f(u1, v0); glVertex2f(x + w, y + h)
    glTexCoord2f(u1, v1); glVertex2f(x + w, y)
    glTexCoord2f(u0, v1); glVertex2f(x, y)
    glEnd()
    tex.unbind()
    glDisable(GL_BLEND)
    glDisable(GL_TEXTURE_2D)


def draw_dirt_background(width, height):
    from game.block import Block

    Block._load_defs()
    tex = Block._get_texture()
    faces = Block._block_defs.get("dirt", {}).get("faces", 2)
    idx = faces[0] if isinstance(faces, list) else faces

    t = 1.0 / 16
    col, row = idx % 16, idx // 16
    u0, u1 = col * t, col * t + t
    v1 = 1.0 - row * t
    v0 = v1 - t
    size = 32

    glEnable(GL_TEXTURE_2D)
    glDisable(GL_BLEND)
    tex.bind()
    glColor3f(0.25, 0.25, 0.25)
    glBegin(GL_QUADS)
    for y in range(0, height, size):
        for x in range(0, width, size):
            glTexCoord2f(u0, v0); glVertex2f(x, y + size)
            glTexCoord2f(u1, v0); glVertex2f(x + size, y + size)
            glTexCoord2f(u1, v1); glVertex2f(x + size, y)
            glTexCoord2f(u0, v1); glVertex2f(x, y)
    glEnd()
    tex.unbind()
    glDisable(GL_TEXTURE_2D)

class GuiButton:
    def __init__(self, button_id, x, y, text, width=200, height=20,
                 colored_suffix="", suffix_color=0xFF5555):
        self.id = button_id
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.text = text
        self.colored_suffix = str(colored_suffix)
        self.suffix_color = int(suffix_color)
        self.enabled = True
        self.visible = True

    def _contains(self, mx, my):
        return self.x <= mx < self.x + self.width and self.y <= my < self.y + self.height

    def draw_button(self, mc, mx, my):
        if not self.visible:
            return
        hovered = self._contains(mx, my)
        if not self.enabled:
            state, color = 0, 0xA0A0A0
        elif hovered:
            state, color = 2, 0xFFFFA0
        else:
            state, color = 1, 0xE0E0E0

        tex = Texture(_GUI_TEXTURE_PATH)
        glColor4f(1.0, 1.0, 1.0, 1.0)
        half = self.width // 2
        v = 46 + state * 20
        draw_tex_rect(tex, self.x, self.y, 0, v, half, self.height)
        draw_tex_rect(tex, self.x + half, self.y, 200 - half, v, half, self.height)
        text_y = self.y + (self.height - 8) // 2
        draw_centered_string(mc.display, self.text, self.x + half, text_y, color)
        if self.colored_suffix:
            text_x = self.x + half - mc.display.string_width(self.text) // 2
            draw_string(
                mc.display, self.colored_suffix,
                text_x + mc.display.string_width(self.text) + 2, text_y,
                self.suffix_color,
            )

    def mouse_pressed(self, mx, my):
        return self.enabled and self.visible and self._contains(mx, my)


class GuiSmallButton(GuiButton):
    def __init__(self, button_id, x, y, text, width=150):
        super().__init__(button_id, x, y, text, width=width, height=20)

class GuiSlider(GuiButton):

    def __init__(self, button_id, x, y, option, settings, width=150):
        super().__init__(button_id, x, y, option.display(settings), width=width)
        self.option = option
        self.settings = settings
        self.dragging = False

    def _set_from_x(self, mx):
        span = max(1, self.width - 8)
        self.option.set_value(self.settings, (mx - (self.x + 4)) / span)
        self.text = self.option.display(self.settings)

    def mouse_pressed(self, mx, my):
        if not super().mouse_pressed(mx, my):
            return False
        self.dragging = True
        self._set_from_x(mx)
        return True

    def release(self):
        was_dragging = self.dragging
        self.dragging = False
        return was_dragging

    def draw_button(self, mc, mx, my):
        if not self.visible:
            return
        if self.dragging:
            self._set_from_x(mx)
        hovered = self.enabled and (self.dragging or self._contains(mx, my))
        color = 0xA0A0A0 if not self.enabled else (0xFFFFA0 if hovered else 0xE0E0E0)

        tex = Texture(_GUI_TEXTURE_PATH)
        glColor4f(1.0, 1.0, 1.0, 1.0)
        half = self.width // 2
        draw_tex_rect(tex, self.x, self.y, 0, 46, half, self.height)
        draw_tex_rect(tex, self.x + half, self.y, 200 - half, 46, half, self.height)
        knob_x = self.x + int(self.option.value(self.settings) * (self.width - 8))
        draw_tex_rect(tex, knob_x, self.y, 0, 66, 4, self.height)
        draw_tex_rect(tex, knob_x + 4, self.y, 196, 66, 4, self.height)
        draw_centered_string(mc.display, self.text, self.x + half, self.y + (self.height - 8) // 2, color)


class GuiScreen:
    def __init__(self):
        self.mc = None
        self.width = 0
        self.height = 0
        self.controls = []

    def set_world_and_resolution(self, mc, width, height):
        self.mc = mc
        self.width = width
        self.height = height
        self.controls = []
        self.init_gui()

    def init_gui(self):
        pass

    def on_gui_closed(self):
        pass

    def update_screen(self):
        pass

    def does_gui_pause_game(self):
        return True

    def key_typed(self, char, key):
        if key == pg.K_ESCAPE:
            self.mc.display_gui_screen(None)

    def text_typed(self, text):
        self.key_typed(text, pg.K_UNKNOWN)

    def mouse_clicked(self, x, y, button):
        if button != 1:
            return
        for control in list(self.controls):
            if control.mouse_pressed(x, y):
                self.mc.play_sound("random.click")
                self.action_performed(control)
                break

    def mouse_released(self, x, y, button):
        pass

    def mouse_moved(self, x, y, buttons=()):
        pass

    def navigation_targets(self):
        return [
            (control.x + control.width / 2.0, control.y + control.height / 2.0)
            for control in self.controls
            if getattr(control, "visible", True) and getattr(control, "enabled", True)
        ]

    def action_performed(self, button):
        pass

    def draw_screen(self, mx, my):
        for control in self.controls:
            control.draw_button(self.mc, mx, my)

    def draw_default_background(self):
        if self.mc.world is not None:
            draw_gradient_rect(0, 0, self.width, self.height, _BG_TOP, _BG_BOTTOM)
        else:
            draw_dirt_background(self.width, self.height)

class GuiGameOver(GuiScreen):
    def init_gui(self):
        self.controls.append(GuiButton(1, self.width // 2 - 100,
                                       self.height // 4 + 72, "Respawn"))
        self.controls.append(GuiButton(2, self.width // 2 - 100,
                                       self.height // 4 + 96, "Title menu"))

    def key_typed(self, char, key):
        return

    def does_gui_pause_game(self):
        return False

    def action_performed(self, button):
        if button.id == 1:
            self.mc.respawn_player()
        elif button.id == 2:
            self.mc.quit_to_title()

    def draw_screen(self, mx, my):
        draw_gradient_rect(0, 0, self.width, self.height,
                           1615855616, -1602211792)
        glPushMatrix()
        glScalef(2.0, 2.0, 1.0)
        draw_centered_string(self.mc.display, "Game over!", self.width / 2 / 2, 30, 0xFFFFFF)
        glPopMatrix()
        draw_centered_string(self.mc.display, "Score: &e0", self.width / 2, 100, 0xFFFFFF)
        super().draw_screen(mx, my)

class GuiIngameMenu(GuiScreen):
    CHUNKS_PER_FRAME = 2

    def __init__(self):
        super().__init__()
        self._save_job = None
        self._save_started = False
        self._ticks = 0

    def init_gui(self):
        w, h = self.width, self.height
        self._save_job = None
        self._save_started = False
        self._ticks = 0
        self.controls.append(GuiButton(4, w // 2 - 100, h // 4 + 24, "Back to game"))
        self.controls.append(GuiButton(1, w // 2 - 100, h // 4 + 48, "Save and quit to title"))
        session = getattr(self.mc, "mp_session", None)
        if session is None or not session.active or session.mode == "host":
            label = translate(
                "mcpy.multiplayer.closeLan" if session is not None and session.mode == "host"
                else "mcpy.multiplayer.openLan"
            )
            self.controls.append(GuiButton(5, w // 2 - 100, h // 4 + 72, label))
        self.controls.append(GuiButton(0, w // 2 - 100, h // 4 + 96, translate("menu.options")))

    def on_gui_closed(self):
        self._save_job = None

    def update_screen(self):
        self._ticks += 1

    def action_performed(self, button):
        if button.id == 0:
            self.mc.display_gui_screen(GuiOptions(self, self.mc.settings))
        elif button.id == 1:
            self.mc.quit_to_title()
        elif button.id == 4:
            self.mc.display_gui_screen(None)
        elif button.id == 5:
            session = getattr(self.mc, "mp_session", None)
            if session is not None and session.mode == "host":
                session.stop()
                self.mc.add_chat_message(translate(
                    "mcpy.chat.lanClosed"
                ))
                self.mc.display_gui_screen(None)
            elif session is None or not session.active:
                if not self.mc.start_host_multiplayer():
                    reason = ""
                    if hasattr(self.mc, "multiplayer_host_block_reason"):
                        reason = self.mc.multiplayer_host_block_reason()
                    elif hasattr(self.mc, "multiplayer_mod_block_reason"):
                        reason = self.mc.multiplayer_mod_block_reason()
                    self.mc.add_chat_message(
                        reason or translate(
                            "mcpy.chat.couldNotOpenLan",
                        )
                    )

    def _advance_autosave(self):
        world = getattr(self.mc, "world", None)
        if world is None or not hasattr(world, "begin_save"):
            return False
        if not self._save_started:
            self._save_started = True
            try:
                self._save_job = world.begin_save(getattr(self.mc, "player", None))
            except Exception as exc:
                print(f"Could not start pause autosave: {exc}")
                self._save_job = None
        job = self._save_job
        if job is None:
            return False
        try:
            if not job.step(self.CHUNKS_PER_FRAME):
                return True
        except Exception as exc:
            print(f"Pause autosave failed: {exc}")
        self._save_job = None
        return False

    def draw_screen(self, mx, my):
        self.draw_default_background()
        saving = self._advance_autosave()
        if saving or self._ticks < 20:
            level = int(255 * (math.sin((self._ticks % 10) / 10.0 * math.pi * 2.0) * 0.2 + 0.8))
            draw_string(self.mc.display, "Saving level..", 8, self.height - 16,
                        level << 16 | level << 8 | level)
        draw_centered_string(self.mc.display, "Game menu", self.width // 2, 40, 0xFFFFFF)
        super().draw_screen(mx, my)

class GuiOptions(GuiScreen):
    def __init__(self, parent, settings):
        super().__init__()
        self.parent = parent
        self.settings = settings
        self.title = translate("options.title")

    def init_gui(self):
        s = self.settings
        from game.app.mods import available_mod_settings
        from .layout import options_screen_layout

        self.has_mod_settings = bool(available_mod_settings())
        layout = options_screen_layout(
            self.width, self.height, len(s.options), self.has_mod_settings,
        )
        for index, (x, y, button_width, _button_height) in enumerate(
            layout["option_rects"]
        ):
            opt = s.options[index]
            if getattr(opt, "is_slider", False):
                self.controls.append(GuiSlider(
                    index, x, y, opt, s, width=button_width,
                ))
            else:
                self.controls.append(GuiSmallButton(
                    index, x, y, opt.display(s), width=button_width,
                ))

        username_x, username_y, utility_width, _ = layout["username_rect"]
        self.controls.append(GuiSmallButton(
            101, username_x, username_y,
            translate("mcpy.username.button"),
            width=utility_width,
        ))
        mod_rect = layout["mod_settings_rect"]
        if mod_rect is not None:
            mod_x, mod_y, mod_width, _ = mod_rect
            self.controls.append(GuiSmallButton(
                102, mod_x, mod_y,
                translate("mcpy.modSettings.button"),
                width=mod_width,
            ))

        controls_x, controls_y, controls_width, _ = layout["controls_rect"]
        self.controls.append(GuiButton(
            100, controls_x, controls_y, translate("options.controls"), width=controls_width,
        ))
        done_x, done_y, done_width, _ = layout["done_rect"]
        self.controls.append(GuiButton(
            200, done_x, done_y, translate("gui.done"),
            width=done_width,
        ))

    def _finish_slider_drags(self):
        released = False
        for control in self.controls:
            if isinstance(control, GuiSlider) and control.release():
                released = True
        if released:
            self.settings.save()
            self.mc.on_options_changed()

    def mouse_released(self, x, y, button):
        self._finish_slider_drags()

    def on_gui_closed(self):
        self._finish_slider_drags()

    def action_performed(self, button):
        if not button.enabled:
            return
        if isinstance(button, GuiSlider):
            self.mc.on_options_changed()
        elif button.id < 100:
            self.settings.toggle_option(button.id)
            self.mc.on_options_changed()
            button.text = self.settings.options[button.id].display(self.settings)
        elif button.id == 100:
            self.mc.display_gui_screen(GuiControls(self, self.settings))
        elif button.id == 101:
            self.mc.display_gui_screen(GuiUsername(self))
        elif button.id == 102 and self.has_mod_settings:
            self.mc.display_gui_screen(GuiModSettings(self))
        elif button.id == 200:
            self.mc.display_gui_screen(self.parent)

    def draw_screen(self, mx, my):
        self.draw_default_background()
        draw_centered_shadow_string(self.mc.display, self.title, self.width // 2, 20, 0xFFFFFF)
        super().draw_screen(mx, my)


class GuiModSettings(GuiScreen):

    LIST_TOP = 32

    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.entries = ()
        self.error_text = ""
        self.settings_entry_by_button = {}
        self.list_bottom = 0

    def init_gui(self):
        from game.app.mods import available_mod_settings
        self.entries = available_mod_settings()
        self.list_bottom = max(self.LIST_TOP, self.height - 48)
        top = self.LIST_TOP + 12
        for index, entry in enumerate(self.entries):
            button = GuiButton(
                index, self.width // 2 - 100, top + index * 24, entry.title
            )
            self.controls.append(button)
            self.settings_entry_by_button[index] = entry
        self.controls.append(GuiButton(
            200, self.width // 2 - 100, self.height - 38, translate("gui.done")
        ))

    def action_performed(self, button):
        if button.id == 200:
            self.mc.display_gui_screen(self.parent)
            return
        entry = self.settings_entry_by_button.get(button.id)
        if entry is None:
            return
        self.mc.display_gui_screen(GuiGeneratedModSettings(self, entry))

    def key_typed(self, char, key):
        if key == pg.K_ESCAPE:
            self.mc.display_gui_screen(self.parent)

    def draw_screen(self, mx, my):
        draw_pack_screen_background(self.width, self.height)
        draw_pack_list_panel(self.width, self.LIST_TOP, self.list_bottom)
        draw_centered_string(
            self.mc.display, translate("mcpy.modSettings.title"), self.width // 2, 16, 0xFFFFFF
        )
        if self.error_text:
            draw_centered_shadow_string(
                self.mc.display, self.error_text, self.width // 2,
                self.height // 2, 0xFF5555,
            )
        for control in self.controls[:-1]:
            control.draw_button(self.mc, mx, my)
        draw_texture_pack_list_edge_fades(self.width, self.LIST_TOP, self.list_bottom)
        self.controls[-1].draw_button(self.mc, mx, my)


class _ModSliderOption:
    is_slider = True

    def __init__(self, definition):
        self.definition = definition

    def value(self, config):
        low = float(self.definition["min"])
        high = float(self.definition["max"])
        value = float(config[self.definition["key"]])
        return max(0.0, min(1.0, (value - low) / (high - low)))

    def set_value(self, config, value):
        definition = self.definition
        low = float(definition["min"])
        high = float(definition["max"])
        value = low + max(0.0, min(1.0, float(value))) * (high - low)
        config[definition["key"]] = max(low, min(high, value))

    def display(self, config):
        definition = self.definition
        value = float(config[definition["key"]])
        if definition["min"] == 0 and definition["max"] == 1:
            rendered = f"{int(round(value * 100))}%"
        else:
            rendered = f"{value:.2f}".rstrip("0").rstrip(".")
        return f"{definition['label']}: {rendered}"


class GuiGeneratedModSettings(GuiScreen):

    ROW_HEIGHT = 34
    LIST_TOP = 32
    ROW_PAD = 8

    def __init__(self, parent, entry):
        super().__init__()
        self.parent = parent
        self.entry = entry
        self.settings = entry.settings
        self.config = {}
        self.scroll = 0
        self.list_bottom = 0
        self._drag_y = None
        self._toggle_controls = {}
        self._slider_controls = {}
        self._reset_controls = {}
        self._slider_setting_ids = {}

    def init_gui(self):
        from game.app.mods import load_mod_config
        from game.localization import translate_mod

        self.settings = tuple(
            {
                **setting,
                "label": translate_mod(
                    self.entry.identifier, f"settings.{setting['key']}",
                    default=setting["label"],
                ),
            }
            for setting in self.settings
        )
        defaults = {setting["key"]: setting["default"] for setting in self.settings}
        self.config = load_mod_config(self.entry.identifier, defaults)
        self._sanitize_config()
        self.list_bottom = max(self.LIST_TOP, self.height - 48)
        self.scroll = 0
        self._drag_y = None
        self._toggle_controls.clear()
        self._slider_controls.clear()
        self._reset_controls.clear()
        self._slider_setting_ids.clear()
        self._text_controls = {}
        self._focused_text = None

        left = self.width // 2 - 110
        for index, setting in enumerate(self.settings):
            if setting["type"] == "toggle":
                control = GuiButton(index, left, 0, "", width=166)
                self._toggle_controls[index] = control
            elif setting["type"] == "text":
                control = GuiButton(4000 + index, left, 0, "", width=166)
                self._text_controls[index] = control
            else:
                option = _ModSliderOption(setting)
                control = GuiSlider(1000 + index, left, 0, option, self.config, width=166)
                self._slider_controls[index] = control
                self._slider_setting_ids[control.id] = index
            reset = GuiButton(
                2000 + index, left + 172, 0,
                translate("mcpy.modSettings.reset"), width=48,
            )
            self._reset_controls[index] = reset
        self.controls.extend(self._toggle_controls.values())
        self.controls.extend(self._slider_controls.values())
        self.controls.extend(self._text_controls.values())
        self.controls.extend(self._reset_controls.values())
        self.controls.append(GuiButton(
            3000, self.width // 2 - 100, self.height - 38, translate("gui.done")
        ))
        self._position_controls()

    def _sanitize_config(self):
        for setting in self.settings:
            key = setting["key"]
            default = setting["default"]
            value = self.config.get(key, default)
            if setting["type"] == "toggle":
                self.config[key] = value if isinstance(value, bool) else default
            elif setting["type"] == "text":
                if not isinstance(value, str):
                    value = default if isinstance(default, str) else str(value)
                max_length = int(setting.get("max_length", 256))
                self.config[key] = value[:max_length]
            elif (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                self.config[key] = default
            else:
                low, high = float(setting["min"]), float(setting["max"])
                self.config[key] = max(low, min(high, float(value)))

    def _max_scroll(self):
        visible_height = max(0, self.list_bottom - self.LIST_TOP)
        return max(0, len(self.settings) * self.ROW_HEIGHT + self.ROW_PAD - visible_height)

    def _set_scroll(self, value):
        self.scroll = max(0, min(self._max_scroll(), int(value)))
        self._position_controls()

    def _position_controls(self):
        from game.localization import translate_mod
        from game.localization import translate
        for index, setting in enumerate(self.settings):
            y = self.LIST_TOP + self.ROW_PAD + index * self.ROW_HEIGHT - self.scroll
            visible = y + 20 > self.LIST_TOP and y < self.list_bottom
            control = (
                self._toggle_controls.get(index)
                or self._slider_controls.get(index)
                or self._text_controls.get(index)
            )
            control.y = y
            control.visible = visible
            reset = self._reset_controls[index]
            reset.y = y
            reset.visible = visible
            if setting["type"] == "toggle":
                state_text = translate(
                    "options.on" if self.config[setting["key"]] else "options.off",
                )
                control.text = f"{setting['label']}: {state_text}"
            elif setting["type"] == "text":
                value = str(self.config.get(setting["key"], ""))
                focused = self._focused_text == index
                cursor = "_" if focused and (getattr(self, "_text_blink", 0) // 10) % 2 == 0 else ""
                display = value if len(value) <= 22 else value[:19] + "..."
                control.text = f"{setting['label']}: {display}{cursor}"
            else:
                control.text = control.option.display(self.config)

    def _save_config(self):
        from game.app.mods import save_mod_config
        save_mod_config(self.entry.identifier, self.config)
        if self.entry.on_config_changed is not None:
            try:
                self.entry.on_config_changed(self.mc, self.config)
            except Exception:
                _LOG.exception(
                    "Could not apply updated settings for mod %s",
                    self.entry.identifier,
                )

    def _finish_slider_drags(self):
        released = False
        for slider in self._slider_controls.values():
            released = slider.release() or released
        if released:
            self._save_config()

    def action_performed(self, button):
        if button.id == 3000:
            self._finish_slider_drags()
            self.mc.display_gui_screen(self.parent)
            return
        setting_index = self._slider_setting_ids.get(button.id)
        if setting_index is not None:
            return
        reset_index = button.id - 2000
        if reset_index in self._reset_controls:
            definition = self.settings[reset_index]
            self.config[definition["key"]] = definition["default"]
            self._position_controls()
            self._save_config()
            return
        if button.id in self._toggle_controls:
            definition = self.settings[button.id]
            key = definition["key"]
            self.config[key] = not self.config[key]
            self._position_controls()
            self._save_config()
            return
        if 4000 <= button.id < 5000:
            index = button.id - 4000
            if index in self._text_controls:
                self._focused_text = index
                self._position_controls()

    def mouse_clicked(self, x, y, button):
        if button in (4, 5):
            if self.LIST_TOP <= y <= self.list_bottom:
                self._set_scroll(self.scroll + (-self.ROW_HEIGHT if button == 4 else self.ROW_HEIGHT))
            return
        if button != 1:
            return
        if not self.LIST_TOP <= y <= self.list_bottom:
            return super().mouse_clicked(x, y, button)
        for control in self.controls:
            if control.mouse_pressed(x, y):
                self.mc.play_sound("random.click")
                self.action_performed(control)
                return
        self._drag_y = y

    def mouse_moved(self, x, y, buttons=()):
        if self._drag_y is None or not buttons or not buttons[0]:
            return
        self._set_scroll(self.scroll + self._drag_y - y)
        self._drag_y = y

    def mouse_released(self, x, y, button):
        self._drag_y = None
        if button == 1:
            self._finish_slider_drags()

    def on_gui_closed(self):
        self._finish_slider_drags()
        self._focused_text = None

    def key_typed(self, char, key):
        if self._focused_text is not None and self._focused_text < len(self.settings):
            setting = self.settings[self._focused_text]
            if setting["type"] == "text":
                key_name = setting["key"]
                value = str(self.config.get(key_name, ""))
                max_length = int(setting.get("max_length", 256))
                if key == pg.K_ESCAPE:
                    self._focused_text = None
                    self._position_controls()
                    self._save_config()
                    return
                if key in (pg.K_RETURN, pg.K_KP_ENTER):
                    self._focused_text = None
                    self._position_controls()
                    self._save_config()
                    return
                if key == pg.K_BACKSPACE:
                    self.config[key_name] = value[:-1]
                    self._position_controls()
                    self._save_config()
                    return
                if char and char.isprintable() and len(value) < max_length:
                    self.config[key_name] = value + char
                    self._position_controls()
                    self._save_config()
                    return
        if key == pg.K_ESCAPE:
            self._finish_slider_drags()
            self.mc.display_gui_screen(self.parent)

    def navigation_targets(self):
        return [
            (control.x + control.width / 2.0, control.y + control.height / 2.0)
            for control in self.controls
            if control.visible and control.enabled
            and (control is self.controls[-1]
                 or self.LIST_TOP <= control.y + control.height / 2 <= self.list_bottom)
        ]

    def draw_screen(self, mx, my):
        draw_pack_screen_background(self.width, self.height)
        draw_pack_list_panel(self.width, self.LIST_TOP, self.list_bottom, self.scroll)
        draw_centered_string(
            self.mc.display, self.entry.title, self.width // 2, 16, 0xFFFFFF
        )
        self._position_controls()
        scale = self.mc.scaled_resolution()[0]
        physical_height = self.mc.display.height
        scissor_bottom = max(0, physical_height - self.list_bottom * scale)
        scissor_height = max(0, (self.list_bottom - self.LIST_TOP) * scale)
        glEnable(GL_SCISSOR_TEST)
        glScissor(0, scissor_bottom, self.mc.display.width, scissor_height)
        for control in self.controls[:-1]:
            control.draw_button(self.mc, mx, my)
        glDisable(GL_SCISSOR_TEST)
        draw_texture_pack_list_edge_fades(self.width, self.LIST_TOP, self.list_bottom)
        self.controls[-1].draw_button(self.mc, mx, my)


class GuiControls(GuiScreen):

    BUTTON_WIDTH = 70
    COLUMN_SPACING = 160
    LABEL_OFFSET_X = BUTTON_WIDTH + 6

    def __init__(self, parent, settings):
        super().__init__()
        self.parent = parent
        self.settings = settings
        self.title = translate("controls.title")
        self.listening = -1

    def init_gui(self):
        w, h = self.width, self.height
        left = w // 2 - 155
        first_y = h // 6
        for i, kb in enumerate(self.settings.key_bindings):
            self.controls.append(GuiSmallButton(
                i,
                left + (i % 2) * self.COLUMN_SPACING,
                first_y + 24 * (i >> 1),
                kb.key_name,
                width=self.BUTTON_WIDTH,
            ))
        self.controls.append(GuiButton(
            200, w // 2 - 100, h // 6 + 168,
            translate("gui.done"),
        ))
        self.listening = -1

    def _refresh_labels(self):
        for i, kb in enumerate(self.settings.key_bindings):
            self.controls[i].text = kb.key_name

    def action_performed(self, button):
        self._refresh_labels()
        if button.id == 200:
            self.mc.display_gui_screen(self.parent)
        else:
            self.listening = button.id
            button.text = f"> {self.settings.key_bindings[button.id].key_name} <"

    def key_typed(self, char, key):
        if self.listening >= 0:
            self.settings.set_key_binding(self.listening, key)
            self.listening = -1
            self._refresh_labels()
        else:
            super().key_typed(char, key)

    def draw_screen(self, mx, my):
        self.draw_default_background()
        draw_centered_shadow_string(self.mc.display, self.title, self.width // 2, 20, 0xFFFFFF)
        left = self.width // 2 - 155
        first_y = self.height // 6
        for i, kb in enumerate(self.settings.key_bindings):
            x = left + (i % 2) * self.COLUMN_SPACING + self.LABEL_OFFSET_X
            y = first_y + 24 * (i >> 1) + 7
            draw_string(self.mc.display, kb.translated_description, x, y, 0xFFFFFF)
        super().draw_screen(mx, my)

def _default_save_root():
    return Path(__file__).resolve().parents[1] / "saves"


def _slot_directory(save_root, slot):
    slot = int(slot)
    if slot < 1 or slot > 5:
        raise ValueError("world slot must be between 1 and 5")
    root = Path(save_root).expanduser().resolve()
    path = root / f"World{slot}"
    if path.resolve().parent != root:
        raise ValueError("world save directory escaped the save root")
    return path


def _world_slot_exists(save_root, slot):
    path = _slot_directory(save_root, slot)
    return (path / "level.dat").is_file() or (path / "level.dat_old").is_file()


def _world_slot_size_bytes(save_root, slot):
    path = _slot_directory(save_root, slot)
    for metadata_path in (path / "level.dat", path / "level.dat_old"):
        if not metadata_path.is_file():
            continue
        try:
            import nbtlib
            nbt = nbtlib.load(str(metadata_path), gzipped=True)
            data = nbt.get("Data", {})
            size = int(data.get("SizeOnDisk", -1))
            if size >= 0:
                return size
        except Exception:
            continue
    try:
        return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())
    except OSError:
        return 0


def _world_slot_label(save_root, slot):
    if not _world_slot_exists(save_root, slot):
        return translate("mcpy.world.emptySlot")
    size_mb = _world_slot_size_bytes(save_root, slot) / (1024.0 * 1024.0)
    return translate(
        "mcpy.world.slotLabel", slot, f"{size_mb:.2f}",
    )


def _world_slot_is_debug(save_root, slot):
    path = _slot_directory(save_root, slot)
    for metadata_path in (path / "level.dat", path / "level.dat_old"):
        if not metadata_path.is_file():
            continue
        try:
            import nbtlib
            nbt = nbtlib.load(str(metadata_path), gzipped=True)
            data = nbt.root.get("Data", {})
            return bool(int(data.get("DebugWorld", 0)))
        except Exception:
            continue
    return False


def _world_slot_button(button_id, x, y, save_root, slot):
    return GuiButton(
        button_id, x, y, _world_slot_label(save_root, slot),
        colored_suffix=" D" if _world_slot_is_debug(save_root, slot) else "",
    )


class GuiSelectWorld(GuiScreen):
    def __init__(self, parent, save_root=None):
        super().__init__()
        self.parent = parent
        self.save_root = Path(save_root) if save_root is not None else _default_save_root()
        self.title = translate("selectWorld.title")
        self.error_text = None
        self.accurate_generation = False

    def _world_type_label(self):
        return (
            translate("mcpy.world.generationAccurate")
            if self.accurate_generation
            else translate("mcpy.world.generationFaster")
        )

    def init_gui(self):
        w, h = self.width, self.height
        for slot in range(1, 6):
            self.controls.append(_world_slot_button(
                slot - 1, w // 2 - 100, h // 6 + 24 * (slot - 1),
                self.save_root, slot,
            ))
        self.controls.append(GuiButton(
            7, w // 2 - 100, h // 6 + 120, self._world_type_label(),
        ))
        self.controls.append(GuiButton(
            5, w // 2 - 100, h // 6 + 144,
            translate("selectWorld.delete"),
        ))
        self.controls.append(GuiButton(
            6, w // 2 - 100, h // 6 + 168,
            translate("gui.cancel"),
        ))

    def action_performed(self, button):
        if 0 <= button.id < 5:
            try:
                self.error_text = None
                world_name = f"World{button.id + 1}"
                alt_held = False
                try:
                    alt_held = bool(pg.key.get_mods() & pg.KMOD_ALT)
                except pg.error:
                    pass
                slot_exists = _world_slot_exists(self.save_root, button.id + 1)
                debug_world = (not slot_exists) and alt_held
                accurate = self.accurate_generation if not slot_exists else None
                if debug_world:
                    self.mc.start_world(
                        world_name,
                        debug_world=True,
                        accurate_generation=accurate,
                    )
                else:
                    self.mc.start_world(
                        world_name,
                        accurate_generation=accurate,
                    )
            except Exception as exc:
                self.error_text = translate(
                    "mcpy.world.openError", button.id + 1, str(exc),
                )
        elif button.id == 7:
            self.accurate_generation = not self.accurate_generation
            button.text = self._world_type_label()
        elif button.id == 5:
            self.mc.display_gui_screen(GuiDeleteWorld(self, self.save_root))
        elif button.id == 6:
            self.mc.display_gui_screen(self.parent)

    def key_typed(self, char, key):
        if key == pg.K_ESCAPE:
            self.mc.display_gui_screen(self.parent)

    def draw_screen(self, mx, my):
        self.draw_default_background()
        draw_centered_string(self.mc.display, self.title, self.width // 2, 20, 0xFFFFFF)
        if self.error_text:
            message = self.error_text
            if len(message) > 100:
                message = message[:97] + "..."
            draw_centered_string(self.mc.display, message, self.width // 2, 42, 0xFF6666)
        super().draw_screen(mx, my)


class GuiDeleteWorld(GuiScreen):
    def __init__(self, parent, save_root=None):
        super().__init__()
        self.parent = parent
        self.save_root = Path(save_root) if save_root is not None else _default_save_root()
        self.title = translate("selectWorld.delete")

    def init_gui(self):
        w, h = self.width, self.height
        for slot in range(1, 6):
            self.controls.append(_world_slot_button(
                slot - 1, w // 2 - 100, h // 6 + 24 * (slot - 1),
                self.save_root, slot,
            ))
        self.controls.append(GuiButton(
            6, w // 2 - 100, h // 6 + 168,
            translate("gui.cancel"),
        ))

    def action_performed(self, button):
        if 0 <= button.id < 5:
            slot = button.id + 1
            if _world_slot_exists(self.save_root, slot):
                self.mc.display_gui_screen(GuiConfirmDeleteWorld(self, slot))
        elif button.id == 6:
            self.mc.display_gui_screen(self.parent)

    def key_typed(self, char, key):
        if key == pg.K_ESCAPE:
            self.mc.display_gui_screen(self.parent)

    def draw_screen(self, mx, my):
        self.draw_default_background()
        draw_centered_string(self.mc.display, self.title, self.width // 2, 20, 0xFFFFFF)
        super().draw_screen(mx, my)


class GuiConfirmDeleteWorld(GuiScreen):
    def __init__(self, parent, slot):
        super().__init__()
        self.parent = parent
        self.slot = int(slot)
        self.error_text = None

    def init_gui(self):
        w, h = self.width, self.height
        self.controls.append(GuiSmallButton(0, w // 2 - 155, h // 6 + 96, "Yes"))  
        self.controls.append(GuiSmallButton(1, w // 2 + 5, h // 6 + 96, "No"))  

    def action_performed(self, button):
        if button.id == 1:
            self.mc.display_gui_screen(self.parent)
            return
        if button.id != 0:
            return
        try:
            target = _slot_directory(self.parent.save_root, self.slot)
            if target.exists():
                shutil.rmtree(target)
            destination = getattr(self.parent, "parent", None) or self.parent
            self.mc.display_gui_screen(destination)
        except (OSError, ValueError) as exc:
            self.error_text = translate(
                "mcpy.world.deleteError", self.slot, str(exc),
            )

    def key_typed(self, char, key):
        if key == pg.K_ESCAPE:
            self.mc.display_gui_screen(self.parent)

    def draw_screen(self, mx, my):
        self.draw_default_background()
        draw_centered_string(
            self.mc.display, "Are you sure you want to delete this world?",
            self.width // 2, 70, 0xFFFFFF,
        )
        draw_centered_string(
            self.mc.display, f"'World {self.slot}' will be lost forever!",
            self.width // 2, 90, 0xFFFFFF,
        )
        if self.error_text:
            draw_centered_string(self.mc.display, self.error_text, self.width // 2,
                                 120, 0xFF5555)
        super().draw_screen(mx, my)


class GuiUsername(GuiScreen):
    MAX_USERNAME_LENGTH = 16
    ALLOWED = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"

    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.username = ""
        self.error_text = ""
        self.update_counter = 0
        self._username_edited = False
        self.block_text = AnimatedBlockText(material="diamond_block")

    def init_gui(self):
        self.username = self.mc.settings.username[:self.MAX_USERNAME_LENGTH]
        self.error_text = ""
        self.update_counter = 0
        self._username_edited = False
        self.block_text = AnimatedBlockText(material="diamond_block")
        self.block_text.show_caret = True
        self.block_text.set_text(self.username or "", animate=True)
        center_x = self.width // 2
        y = self.height // 2 + 48
        self.controls.append(GuiButton(0, center_x - 100, y, translate("gui.done")))
        self.controls.append(GuiButton(1, center_x - 100, y + 24, translate("gui.cancel")))

    def update_screen(self):
        self.update_counter += 1
        self.block_text.update()
        self.block_text.show_caret = True
        self.block_text.caret_visible = (self.update_counter % 20) < 10

    def _save_username(self):
        username = self.username.strip()
        if not username:
            self.error_text = translate("mcpy.username.empty")
            return
        self.mc.settings.username = username
        self.mc.settings.username_set = True
        self.mc.settings.save()
        player_model.set_skin_username(username)
        self.mc.display_gui_screen(self.parent)

    def action_performed(self, button):
        if button.id == 0:
            self._save_username()
        elif button.id == 1:
            self.mc.display_gui_screen(self.parent)

    def _sync_block_text(self):
        self.block_text.set_text(self.username, animate=True)

    def key_typed(self, char, key):
        if key == pg.K_ESCAPE:
            self.mc.display_gui_screen(self.parent)
        elif key in (pg.K_RETURN, pg.K_KP_ENTER):
            self._save_username()
        elif key == pg.K_BACKSPACE:
            self._username_edited = True
            self.username = self.username[:-1]
            self.error_text = ""
            self._sync_block_text()
        elif char and (
            len(self.username) < self.MAX_USERNAME_LENGTH
            or not self._username_edited
        ):
            typed = "".join(ch for ch in char if ch in self.ALLOWED)
            if typed and not self._username_edited:
                self.username = ""
                self._username_edited = True
            self.username += typed
            self.username = self.username[:self.MAX_USERNAME_LENGTH]
            self.error_text = ""
            self._sync_block_text()

    def draw_screen(self, mx, my):
        d = self.mc.display
        self.draw_default_background()
        draw_centered_string(
            d,
            translate("mcpy.username.title"),
            self.width // 2,
            20,
            0xFFFFFF,
        )
        name_len = max(len(self.username), 1)
        extra = max(0.55, min(1.15, 14.0 / (name_len * 1.1 + 3.0)))
        self.block_text.draw(
            d,
            logo_viewport_fraction=180.0,
            extra_scale=extra,
            gui_width=self.width,
            gui_height=self.height,
            vertical_center=True,
        )
        if self.error_text:
            draw_centered_string(
                d,
                self.error_text,
                self.width // 2,
                self.height // 2 + 28,
                0xFF5555,
            )
        super().draw_screen(mx, my)



class GuiModsTexturePacks(GuiScreen):

    LIST_TOP = 32
    ROW_HEIGHT = 36
    ROW_ICON_SIZE = 32

    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.entries = ()
        self.scroll = 0
        self.list_bottom = 0
        self._drag_y = None
        self._icon_textures = {}

    def init_gui(self):
        from game.app.texturepacks import (
            MODS_ROOT, TEXTUREPACKS_ROOT, discover_pack_entries,
        )

        TEXTUREPACKS_ROOT.mkdir(parents=True, exist_ok=True)
        MODS_ROOT.mkdir(parents=True, exist_ok=True)
        self.entries = discover_pack_entries(self.mc.settings)
        self.scroll = 0
        self.list_bottom = self.height - 72
        self._drag_y = None
        from game.app.mods import available_mod_settings
        has_mod_settings = bool(available_mod_settings())
        button_width = min(150, max(100, (self.width - 40) // 3 - 8))
        button_gap = 8
        row_width = button_width * 3 + button_gap * 2
        row_left = (self.width - row_width) // 2
        settings_button = GuiButton(
            8, row_left + (button_width + button_gap) * 2, self.height - 68,
            translate("mcpy.modSettings.title"), button_width,
        )
        settings_button.enabled = has_mod_settings
        self.controls.extend((
            GuiButton(5, row_left, self.height - 68, translate("texturePack.openFolder"), button_width),
            GuiButton(
                7, row_left + button_width + button_gap, self.height - 68,
                translate("mcpy.mods.folder"), button_width,
            ),
            settings_button,
            GuiButton(6, self.width // 2 - 100, self.height - 42, translate("gui.done")),
        ))
        self.has_mod_settings = has_mod_settings

    def _max_scroll(self):
        visible_height = max(0, self.list_bottom - self.LIST_TOP - 4)
        return max(0, len(self.entries) * self.ROW_HEIGHT - visible_height)

    def _entry_at(self, x, y):
        left = self.width // 2 - 110
        right = self.width // 2 + 110
        if not (left <= x <= right and self.LIST_TOP <= y <= self.list_bottom):
            return None
        index = (y - self.LIST_TOP + self.scroll - 2) // self.ROW_HEIGHT
        if 0 <= index < len(self.entries):
            return self.entries[index]
        return None

    def _refresh_entries(self):
        from game.app.texturepacks import discover_pack_entries
        self.entries = discover_pack_entries(self.mc.settings)
        self.scroll = min(self.scroll, self._max_scroll())

    def _apply_texture_pack(self, entry):
        from game.app.texturepacks import set_active_texture_pack
        from engine.textures import Texture

        selected = set_active_texture_pack(entry.identifier)
        self.mc.settings.texture_pack = selected
        from game.localization import set_language
        self.mc.settings.save()
        Texture.refresh_all_for_active_pack()
        set_language(pack_name=selected)
        reload_font = getattr(self.mc.display, "reload_bitmap_font", None)
        if callable(reload_font):
            reload_font()
        world = getattr(self.mc, "world", None)
        if world is not None:
            for chunk in getattr(world, "chunks", {}).values():
                mark_dirty = getattr(chunk, "mark_all_dirty", None)
                if callable(mark_dirty):
                    mark_dirty()

    def _toggle_mod(self, entry):
        if entry.setting_attr.startswith("mod_enabled:"):
            from game.app.mods import is_mod_enabled, set_mod_enabled
            identifier = entry.setting_attr.partition(":")[2]
            value = not is_mod_enabled(identifier, entry.default_enabled)
            set_mod_enabled(identifier, value, entry.default_enabled)
        self._refresh_entries()

    def _open_folder(self, folder, label):
        folder.mkdir(parents=True, exist_ok=True)
        path = str(folder.resolve())
        try:
            if hasattr(os, "startfile"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            print(f"Could not open {label}: {exc}")

    def _open_pack_folder(self):
        from game.app.texturepacks import TEXTUREPACKS_ROOT
        self._open_folder(TEXTUREPACKS_ROOT, "texture pack folder")

    def _open_mods_folder(self):
        from game.app.texturepacks import MODS_ROOT
        self._open_folder(MODS_ROOT, "mods folder")

    def action_performed(self, button):
        if button.id == 5:
            self._open_pack_folder()
        elif button.id == 7:
            self._open_mods_folder()
        elif button.id == 8 and self.has_mod_settings:
            self.mc.display_gui_screen(GuiModSettings(self))
        elif button.id == 6:
            self.mc.display_gui_screen(self.parent)

    def mouse_clicked(self, x, y, button):
        if button in (4, 5):
            if self.LIST_TOP <= y <= self.list_bottom:
                self.scroll += -self.ROW_HEIGHT if button == 4 else self.ROW_HEIGHT
                self.scroll = max(0, min(self._max_scroll(), self.scroll))
            return
        if button != 1:
            return
        self._drag_y = y if self.LIST_TOP <= y <= self.list_bottom else None
        for control in list(self.controls):
            if control.mouse_pressed(x, y):
                self.mc.play_sound("random.click")
                self.action_performed(control)
                return
        entry = self._entry_at(x, y)
        if entry is None:
            return
        self.mc.play_sound("random.click")
        if entry.kind == "mod":
            self._toggle_mod(entry)
        else:
            from game.app.texturepacks import get_active_texture_pack
            if entry.identifier != get_active_texture_pack():
                self._apply_texture_pack(entry)
                self._refresh_entries()

    def mouse_moved(self, x, y, buttons=()):
        if self._drag_y is None:
            return
        left_down = bool(buttons and buttons[0])
        if not left_down:
            return
        self.scroll += self._drag_y - y
        self._drag_y = y
        self.scroll = max(0, min(self._max_scroll(), self.scroll))

    def mouse_released(self, x, y, button):
        if button == 1:
            self._drag_y = None

    def key_typed(self, char, key):
        if key == pg.K_ESCAPE:
            self.mc.display_gui_screen(self.parent)

    def navigation_targets(self):
        targets = [
            (control.x + control.width / 2.0, control.y + control.height / 2.0)
            for control in self.controls
            if getattr(control, "visible", True) and getattr(control, "enabled", True)
        ]
        first = max(0, self.scroll // self.ROW_HEIGHT)
        last = min(len(self.entries), first + max(1, (self.list_bottom - self.LIST_TOP) // self.ROW_HEIGHT))
        for index in range(first, last):
            y = self.LIST_TOP + index * self.ROW_HEIGHT - self.scroll + 18
            if self.LIST_TOP <= y <= self.list_bottom:
                targets.append((self.width // 2, y))
        return targets

    @staticmethod
    def _draw_scaled_icon(tex, x, y, width, height):
        glEnable(GL_TEXTURE_2D)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        tex.bind()
        glBegin(GL_QUADS)
        glTexCoord2f(0.0, 1.0); glVertex2f(x, y)
        glTexCoord2f(1.0, 1.0); glVertex2f(x + width, y)
        glTexCoord2f(1.0, 0.0); glVertex2f(x + width, y + height)
        glTexCoord2f(0.0, 0.0); glVertex2f(x, y + height)
        glEnd()
        tex.unbind()
        glDisable(GL_BLEND)
        glDisable(GL_TEXTURE_2D)

    def _draw_pack_background(self):
        draw_pack_screen_background(self.width, self.height)

    def _draw_pack_list_background(self):
        draw_pack_list_panel(self.width, self.LIST_TOP, self.list_bottom, self.scroll)

    def _draw_selection_border(self, x, y):
        glDisable(GL_TEXTURE_2D)
        glColor4f(0.50, 0.50, 0.50, 1.0)
        glBegin(GL_QUADS)
        glVertex2f(x, y); glVertex2f(x + 220, y)
        glVertex2f(x + 220, y + 34); glVertex2f(x, y + 34)
        glEnd()
        glColor4f(0.0, 0.0, 0.0, 1.0)
        glBegin(GL_QUADS)
        glVertex2f(x + 1, y + 1); glVertex2f(x + 219, y + 1)
        glVertex2f(x + 219, y + 33); glVertex2f(x + 1, y + 33)
        glEnd()
        glEnable(GL_TEXTURE_2D)

    @staticmethod
    def _draw_entry_backing(x, y):
        glDisable(GL_TEXTURE_2D)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glColor4f(0.0, 0.0, 0.0, 0.88)
        glBegin(GL_QUADS)
        glVertex2f(x, y); glVertex2f(x + 220, y)
        glVertex2f(x + 220, y + 34); glVertex2f(x, y + 34)
        glEnd()
        glDisable(GL_BLEND)
        glEnable(GL_TEXTURE_2D)

    @staticmethod
    def _fit_text(display, value, max_width):
        text = str(value or "")
        while text and display.string_width(text) > max_width:
            text = text[:-1]
        if text != str(value or "") and len(text) > 3:
            text = text[:-3] + "..."
        return text

    def draw_screen(self, mx, my):
        from game.app.texturepacks import get_active_texture_pack

        self._draw_pack_background()
        self._draw_pack_list_background()
        draw_centered_string(
            self.mc.display, translate("mcpy.mods.screenTitle"), self.width // 2, 16, 0xFFFFFF,
        )
        icon_x = self.width // 2 - 108
        text_x = icon_x + self.ROW_ICON_SIZE
        text_width = max(48, self.width // 2 + 108 - text_x)
        active_pack = get_active_texture_pack()

        scale = self.mc.scaled_resolution()[0]
        physical_height = self.mc.display.height
        scissor_bottom = max(0, physical_height - self.list_bottom * scale)
        scissor_height = max(0, (self.list_bottom - self.LIST_TOP) * scale)
        glEnable(GL_SCISSOR_TEST)
        glScissor(0, scissor_bottom, self.mc.display.width, scissor_height)
        for index, entry in enumerate(self.entries):
            row_y = 36 + index * self.ROW_HEIGHT - self.scroll
            selected = (
                entry.kind == "texturepack" and entry.identifier == active_pack
            ) or (entry.kind == "mod" and entry.enabled)
            row_left = self.width // 2 - 110
            row_top = row_y - 2
            if selected:
                self._draw_selection_border(row_left, row_top)
            else:
                self._draw_entry_backing(row_left, row_top)

            icon = self._icon_textures.get(entry.icon_path)
            if icon is None:
                icon = Texture(entry.icon_path)
                self._icon_textures[entry.icon_path] = icon
            glColor4f(1.0, 1.0, 1.0, 1.0)
            icon_size = self.ROW_ICON_SIZE - 2
            self._draw_scaled_icon(
                icon, icon_x, row_top + 2, icon_size, icon_size,
            )

            if entry.kind == "mod":
                from game.app.mods import mod_restart_required
                status = translate(
                    "mcpy.mods.enabled" if entry.enabled else "mcpy.mods.disabled",
                )
                if mod_restart_required(entry.identifier, entry.default_enabled):
                    status += translate(
                        "mcpy.mods.restartRequired",
                    )
                description = entry.description
                details = status
                type_label = translate("mcpy.mods.typeMod")
            else:
                description = entry.description
                details = entry.details
                type_label = translate("mcpy.mods.typeTexturePack")
            label_width = self.mc.display.string_width(type_label)
            fitted_title = self._fit_text(
                self.mc.display, entry.title, max(0, text_width - label_width),
            )
            draw_string(
                self.mc.display, fitted_title + " " + type_label,
                text_x, row_y + 1, 0xFFFFFF,
            )
            if description:
                draw_string(
                    self.mc.display, self._fit_text(self.mc.display, description, text_width),
                    text_x, row_y + 12, 0xA0A0A0,
                )
            if details:
                draw_string(
                    self.mc.display, self._fit_text(self.mc.display, details, text_width),
                    text_x, row_y + 22, 0xA0A0A0,
                )
        glDisable(GL_SCISSOR_TEST)
        draw_texture_pack_list_edge_fades(
            self.width, self.LIST_TOP, self.list_bottom,
        )
        super().draw_screen(mx, my)


class GuiMainMenu(GuiScreen):
    def __init__(self):
        super().__init__()
        self.splash = select_splash_text()
        self._normal_splash = self.splash
        self.logo = None
        self._set_logo(animate=False)
        self.update_counter = 0

    def _set_logo(self, *, animate=True):
        self.logo = AnimatedBlockText(material="stone", layers=MINECRAFT_LOGO_LAYERS)
        self.logo.set_layers(MINECRAFT_LOGO_LAYERS, animate=animate)

    def key_typed(self, char, key):
        return

    def init_gui(self):
        w, h = self.width, self.height
        settings = getattr(getattr(self, "mc", None), "settings", None)
        self._normal_splash = select_splash_text(username=getattr(settings, "username", ""))
        self.splash = self._normal_splash
        self._set_logo(animate=True)
        self.controls.append(GuiButton(1, w // 2 - 100, h // 4 + 48, translate("menu.singleplayer")))
        multi = GuiButton(2, w // 2 - 100, h // 4 + 72, translate("menu.multiplayer"))
        mods = GuiButton(4, w // 2 - 100, h // 4 + 96, translate("menu.mods"))
        tutorial = GuiButton(
            3, w // 2 - 100, h // 4 + 120,
            translate("mcpy.menu.tutorial"),
        )
        multi.enabled = True
        tutorial.enabled = False
        self.controls.extend((multi, mods, tutorial))
        self.controls.append(GuiButton(0, w // 2 - 100, h // 4 + 144, translate("menu.options")))

    def update_screen(self):
        self.update_counter += 1
        self.logo.update()

    def action_performed(self, button):
        if button.id == 0:
            self.mc.display_gui_screen(GuiOptions(self, self.mc.settings))
        elif button.id == 1:
            self.mc.display_gui_screen(GuiSelectWorld(self, getattr(self.mc, "save_root", None)))
        elif button.id == 2:
            self.mc.display_gui_screen(GuiMultiplayer(self))
        elif button.id == 4:
            self.mc.display_gui_screen(GuiModsTexturePacks(self))
        elif button.id == 5:
            self.mc.running = False

    def draw_screen(self, mx, my):
        d = self.mc.display
        self.draw_default_background()
        self.logo.draw(d, logo_viewport_fraction=120.0, gui_width=self.width)

        glColor4f(1.0, 1.0, 1.0, 1.0)
        glPushMatrix()
        glTranslatef(self.width / 2 + 90, 70.0, 0.0)
        glRotatef(-20.0, 0.0, 0.0, 1.0)
        pulse = 1.8 - abs(math.sin((time.time() * 1000 % 1000) / 1000.0 * math.pi * 2.0) * 0.1)
        scale = pulse * 100.0 / (d.string_width(self.splash) + 32)
        glScalef(scale, scale, scale)
        draw_centered_string(d, self.splash, 0, -8, 0xFFFF00)
        glPopMatrix()

        draw_string(d, "Minecraft " + VERSION_TEXT, 2, 2, 0x505050)
        draw_string(d, translate("mcpy.brand"), 2, self.height - 10, 0xFFFFFF)
        if not getattr(self.mc.settings, "username_set", True):
            draw_centered_string(
                d, translate("mcpy.username.hint"),
                self.width // 2, self.height // 4 + 144 + 24, 0xFFFF55,
            )
        super().draw_screen(mx, my)


_IP_ALLOWED = (
    " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`"
    "abcdefghijklmnopqrstuvwxyz{|}~"
)


class GuiMultiplayer(GuiScreen):

    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.ip_text = "127.0.0.1"
        self.update_counter = 0
        self.status = ""

    def init_gui(self):
        w, h = self.width, self.height
        connect = GuiButton(0, w // 2 - 100, h // 4 + 96 + 12, translate("multiplayer.connect"))
        host = GuiButton(
            2, w // 2 - 100, h // 4 + 72,
            translate("mcpy.multiplayer.hostButton"),
        )
        cancel = GuiButton(1, w // 2 - 100, h // 4 + 120 + 12, translate("gui.cancel"))
        self.controls.append(connect)
        self.controls.append(host)
        self.controls.append(cancel)
        connect.enabled = len(self.ip_text) > 0

    def update_screen(self):
        self.update_counter += 1

    def key_typed(self, char, key):
        if key == pg.K_ESCAPE:
            self.mc.display_gui_screen(self.parent)
            return
        if key in (pg.K_RETURN, pg.K_KP_ENTER):
            if self.controls and self.controls[0].enabled:
                self.action_performed(self.controls[0])
            return
        if key == pg.K_BACKSPACE:
            if self.ip_text:
                self.ip_text = self.ip_text[:-1]
        elif char and char in _IP_ALLOWED and len(self.ip_text) < 64:
            self.ip_text += char
        if self.controls:
            self.controls[0].enabled = len(self.ip_text.strip()) > 0

    def action_performed(self, button):
        if button.id == 1:
            self.mc.display_gui_screen(self.parent)
        elif button.id == 0:
            text = self.ip_text.strip()
            if not text:
                return
            host, port = text, 25565
            if text.startswith("["):
                closing = text.find("]")
                if closing < 0:
                    self.status = translate("mcpy.multiplayer.invalidIpv6")
                    return
                host = text[1:closing].strip()
                suffix = text[closing + 1:]
                if suffix:
                    if not suffix.startswith(":"):
                        self.status = translate("mcpy.multiplayer.invalidServerAddress")
                        return
                    try:
                        port = int(suffix[1:])
                    except ValueError:
                        self.status = translate("mcpy.multiplayer.invalidPort")
                        return
            elif text.count(":") == 1:
                candidate_host, candidate_port = text.rsplit(":", 1)
                if not candidate_port.isdigit():
                    self.status = translate("mcpy.multiplayer.invalidPort")
                    return
                host = candidate_host.strip() or "127.0.0.1"
                port = int(candidate_port)
            if not 1 <= port <= 65535:
                self.status = translate("mcpy.multiplayer.invalidPort")
                return
            self.mc.display_gui_screen(GuiConnecting(self, host, port))
        elif button.id == 2:
            self.mc.display_gui_screen(GuiHostWorld(self, getattr(self.mc, "save_root", None)))

    def draw_screen(self, mx, my):
        self.draw_default_background()
        d = self.mc.display
        draw_centered_string(d, translate("multiplayer.title"), self.width // 2, self.height // 4 - 40, 0xFFFFFF)
        draw_string(
            d,
            translate("mcpy.multiplayer.notJavaCompatible"),
            self.width // 2 - 140,
            self.height // 4 + 10,
            0xA0A0A0,
        )
        draw_string(
            d,
            translate("multiplayer.ipinfo"),
            self.width // 2 - 140,
            self.height // 4 + 28,
            0xA0A0A0,
        )
        fx = self.width // 2 - 100
        fy = self.height // 4 + 40
        fw, fh = 200, 20
        glDisable(GL_TEXTURE_2D)
        glColor4f(0.625, 0.625, 0.625, 1.0)
        glBegin(GL_QUADS)
        glVertex2f(fx - 1, fy - 1)
        glVertex2f(fx + fw + 1, fy - 1)
        glVertex2f(fx + fw + 1, fy + fh + 1)
        glVertex2f(fx - 1, fy + fh + 1)
        glEnd()
        glColor4f(0.0, 0.0, 0.0, 1.0)
        glBegin(GL_QUADS)
        glVertex2f(fx, fy)
        glVertex2f(fx + fw, fy)
        glVertex2f(fx + fw, fy + fh)
        glVertex2f(fx, fy + fh)
        glEnd()
        glColor4f(1.0, 1.0, 1.0, 1.0)
        cursor = "_" if (self.update_counter // 6) % 2 == 0 else ""
        draw_string(d, self.ip_text + cursor, fx + 4, fy + (fh - 8) // 2, 0xE0E0E0, shadow=False)
        if self.status:
            draw_centered_string(d, self.status, self.width // 2, fy + fh + 8, 0xFF5555)
        super().draw_screen(mx, my)


class GuiHostWorld(GuiScreen):

    def __init__(self, parent, save_root=None):
        super().__init__()
        self.parent = parent
        self.save_root = Path(save_root) if save_root is not None else Path("saves")
        self.error_text = None

    def init_gui(self):
        w, h = self.width, self.height
        for slot in range(1, 6):
            self.controls.append(_world_slot_button(
                slot - 1, w // 2 - 100, h // 6 + 24 * (slot - 1),
                self.save_root, slot,
            ))
        self.controls.append(GuiButton(
            6, w // 2 - 100, h // 6 + 168,
            translate("gui.cancel"),
        ))

    def action_performed(self, button):
        if 0 <= button.id < 5:
            try:
                self.error_text = None
                saved_dimension = getattr(self.mc, "saved_world_dimension", None)
                if callable(saved_dimension) and saved_dimension(f"World{button.id + 1}") != 0:
                    self.error_text = (
                        translate(
                            "mcpy.multiplayer.netherHostBlocked", button.id + 1,
                        )
                    )
                    return
                self.mc.start_world(f"World{button.id + 1}")
                ok = self.mc.start_host_multiplayer()
                if not ok:
                    reason = ""
                    if hasattr(self.mc, "multiplayer_host_block_reason"):
                        reason = self.mc.multiplayer_host_block_reason()
                    elif hasattr(self.mc, "multiplayer_mod_block_reason"):
                        reason = self.mc.multiplayer_mod_block_reason()
                    self.error_text = reason or translate(
                        "mcpy.multiplayer.bindFailed",
                    )
            except Exception as exc:
                self.error_text = translate(
                    "mcpy.multiplayer.hostWorldError", button.id + 1, str(exc),
                )
        elif button.id == 6:
            self.mc.display_gui_screen(self.parent)

    def key_typed(self, char, key):
        if key == pg.K_ESCAPE:
            self.mc.display_gui_screen(self.parent)

    def draw_screen(self, mx, my):
        self.draw_default_background()
        draw_centered_string(
            self.mc.display,
            translate("mcpy.multiplayer.hostTitle"),
            self.width // 2, 20, 0xFFFFFF,
        )
        if self.error_text:
            draw_centered_string(self.mc.display, self.error_text[:80], self.width // 2, self.height - 40, 0xFF5555)
        super().draw_screen(mx, my)


class GuiDisconnected(GuiScreen):

    def __init__(self, reason: str | None = None):
        super().__init__()
        self.reason = str(
            reason or translate("disconnect.closed")
        )[:200]

    def init_gui(self):
        self.controls.append(GuiButton(
            0, self.width // 2 - 100, self.height // 4 + 92,
            translate("gui.toMenu"),
        ))

    def action_performed(self, button):
        if button.id == 0:
            self.mc.display_gui_screen(GuiMainMenu())

    def key_typed(self, char, key):
        if key in (pg.K_ESCAPE, pg.K_RETURN, pg.K_KP_ENTER):
            self.mc.display_gui_screen(GuiMainMenu())

    def draw_screen(self, mx, my):
        self.draw_default_background()
        draw_centered_string(
            self.mc.display, translate("disconnect.lost"),
            self.width // 2, self.height // 4 + 20, 0xFFFFFF,
        )
        draw_centered_string(
            self.mc.display, translate("disconnect.disconnected"),
            self.width // 2, self.height // 4 + 42, 0xA0A0A0,
        )
        reason = self.reason
        max_width = max(40, self.width - 32)
        if self.mc.display.string_width(reason) > max_width:
            while reason and self.mc.display.string_width(reason + "...") > max_width:
                reason = reason[:-1]
            reason += "..."
        draw_centered_string(self.mc.display, reason,
                             self.width // 2, self.height // 4 + 64, 0xFF7777)
        super().draw_screen(mx, my)


class GuiConnecting(GuiScreen):
    def __init__(self, parent, host: str, port: int):
        super().__init__()
        self.parent = parent
        self.host = host
        self.port = port
        self.status = translate("connect.connecting")
        self._started = False
        self._done = False
        self._error = None
        self.update_counter = 0

    def does_gui_pause_game(self):
        return True

    def init_gui(self):
        self.controls.append(GuiButton(
            0, self.width // 2 - 100, self.height // 4 + 120 + 12,
            translate("gui.cancel"),
        ))

    def update_screen(self):
        self.update_counter += 1
        if self._started or self._done:
            session = getattr(self.mc, "mp_session", None)
            if session is not None and session.client is not None:
                for ev in session.client.poll_events():
                    if ev.get("t") == "welcome":
                        self.status = translate(
                            "multiplayer.downloadingTerrain",
                        )
                        try:
                            self.mc.join_multiplayer_world(ev)
                            self._done = True
                        except Exception as exc:
                            self._error = str(exc)
                            self.status = translate(
                                "mcpy.multiplayer.connectFailed", str(exc),
                            )
                            session.stop()
                        return
                    if ev.get("t") == "kick":
                        self._error = ev.get("reason") or translate("disconnect.kicked")
                        session.stop()
                        self.mc.display_gui_screen(GuiDisconnected(self._error))
                        return
                    if ev.get("t") == "disconnect":
                        self._error = ev.get("reason") or translate("disconnect.disconnected")
                        session.stop()
                        self.mc.display_gui_screen(GuiDisconnected(self._error))
                        return
                if session.client.error and not session.client.connected:
                    self._error = session.client.error
                    session.stop()
                    self.mc.display_gui_screen(GuiDisconnected(self._error))
            return
        self._started = True
        name = getattr(self.mc.settings, "username", "Player") or "Player"
        client = self.mc.begin_multiplayer_connect(self.host, self.port, name)
        if client is None or not getattr(client, "connected", False):
            reason = ""
            if client is None and hasattr(self.mc, "multiplayer_mod_block_reason"):
                reason = self.mc.multiplayer_mod_block_reason()
            self._error = reason or getattr(client, "error", None) or translate(
                "connect.failed"
            )
            self.status = self._error
            self._done = True
            session = getattr(self.mc, "mp_session", None)
            if session is not None:
                session.stop()
            self.mc.display_gui_screen(GuiDisconnected(self._error))

    def action_performed(self, button):
        if button.id == 0:
            session = getattr(self.mc, "mp_session", None)
            if session is not None:
                session.stop()
            self.mc.display_gui_screen(self.parent)

    def key_typed(self, char, key):
        if key == pg.K_ESCAPE:
            self.action_performed(self.controls[0])

    def draw_screen(self, mx, my):
        self.draw_default_background()
        draw_centered_string(self.mc.display, self.status, self.width // 2, self.height // 2 - 20, 0xFFFFFF)
        draw_centered_string(
            self.mc.display,
            format_host_port(self.host, self.port),
            self.width // 2,
            self.height // 2,
            0xA0A0A0,
        )
        super().draw_screen(mx, my)


class GuiChat(GuiScreen):

    def __init__(self):
        super().__init__()
        self.message = ""
        self.update_counter = 0

    def does_gui_pause_game(self):
        return False

    def init_gui(self):
        pg.key.set_repeat(400, 50)

    def on_gui_closed(self):
        pg.key.set_repeat(0, 0)

    def update_screen(self):
        self.update_counter += 1

    def _append_text(self, text):
        typed = "".join(ch for ch in str(text) if ch.isprintable())
        self.message += typed[:max(0, 100 - len(self.message))]

    def paste_text(self):
        if pg.scrap.has_text():
            self._append_text(pg.scrap.get_text())

    def key_typed(self, char, key):
        if key == pg.K_ESCAPE:
            self.mc.display_gui_screen(None)
            return
        if key in (pg.K_RETURN, pg.K_KP_ENTER):
            text = self.message.strip()
            self.mc.display_gui_screen(None)
            if text:
                send = getattr(self.mc, "send_chat_message", None)
                if callable(send):
                    send(text)
            return
        if key == pg.K_BACKSPACE:
            if self.message:
                self.message = self.message[:-1]
            return
        if char:
            self._append_text(char)

    def draw_screen(self, mx, my):
        glDisable(GL_TEXTURE_2D)
        glDisable(GL_ALPHA_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glColor4f(0.0, 0.0, 0.0, 0.5)
        glBegin(GL_QUADS)
        glVertex2f(2, self.height - 14)
        glVertex2f(self.width - 2, self.height - 14)
        glVertex2f(self.width - 2, self.height - 2)
        glVertex2f(2, self.height - 2)
        glEnd()
        glDisable(GL_BLEND)
        glEnable(GL_ALPHA_TEST)
        glColor4f(1.0, 1.0, 1.0, 1.0)

        cursor = "_" if (self.update_counter // 6) % 2 == 0 else ""
        draw_string(
            self.mc.display,
            "> " + self.message + cursor,
            4,
            self.height - 12,
            0xE0E0E0,
            shadow=False,
        )


_INVENTORY_TEX_PATH = "game/textures/gui/inventory.png"
_CRAFTING_TEX_PATH = "game/textures/gui/crafting.png"


class Slot:
    def __init__(self, inventory, index, x, y, *, armor_type=None):
        self.inventory = inventory
        self.index = index
        self.x = x
        self.y = y
        self.armor_type = armor_type

    def get_stack(self):
        return self.inventory.get_stack(self.index)

    def put_stack(self, stack):
        self.inventory.set_stack(self.index, stack)

    def is_item_valid(self, stack):
        if self.armor_type is None:
            return True
        if stack is None:
            return True
        from game.entities.item import Item
        if self.armor_type == 0 and stack.id in Item.HELMET_BLOCKS:
            return True
        return Item.armor_slot(stack.id) == self.armor_type

    def contains(self, mx, my, gui_left, gui_top):
        lx = mx - gui_left
        ly = my - gui_top
        return (self.x - 1 <= lx < self.x + 17) and (self.y - 1 <= ly < self.y + 17)


class ResultSlot(Slot):
    is_result = True

    def is_item_valid(self, stack):
        return False


class GuiContainer(GuiScreen):
    x_size = 176
    y_size = 166

    def __init__(self):
        super().__init__()
        self.slots = []
        self.dragged = None

    def does_gui_pause_game(self):
        return False

    def on_gui_closed(self):
        if self.dragged is not None and self.mc.player is not None:
            multiplayer = getattr(self.mc, "mp_session", None)
            if multiplayer is not None and getattr(multiplayer, "mode", None) == "client":
                self.dragged = None
                return
            self.mc.player.drop_player_item(self.dragged)
            self.dragged = None

    def key_typed(self, char, key):
        inv_key = getattr(self.mc.settings, "key_inventory", None)
        inv_code = inv_key.key if inv_key is not None else pg.K_e
        if key == pg.K_ESCAPE or key == inv_code:
            self.mc.display_gui_screen(None)

    def _gui_origin(self):
        return (self.width - self.x_size) // 2, (self.height - self.y_size) // 2

    def _sync_authoritative_cursor(self):
        multiplayer = getattr(self.mc, "mp_session", None)
        if multiplayer is not None and getattr(multiplayer, "mode", None) == "client":
            player = getattr(self.mc, "player", None)
            cursor = getattr(player, "_mp_cursor_stack", None) if player is not None else None
            if cursor is not None:
                self.dragged = cursor.copy()

    def _slot_at(self, mx, my):
        left, top = self._gui_origin()
        for slot in self.slots:
            if slot.contains(mx, my, left, top):
                return slot
        return None

    def navigation_targets(self):
        left, top = self._gui_origin()
        return [
            (left + slot.x + 8.0, top + slot.y + 8.0)
            for slot in self.slots
        ]

    def mouse_clicked(self, x, y, button):
        if button not in (1, 3):
            return
        self._sync_authoritative_cursor()
        left_click = button == 1
        slot = self._slot_at(x, y)
        player = self.mc.player
        if player is None:
            return

        multiplayer = getattr(self.mc, "mp_session", None)
        if multiplayer is not None and getattr(multiplayer, "mode", None) == "client":
            if slot is not None:
                multiplayer.request_inventory_click(self, slot, left_click)
            return

        if slot is not None:
            self._click_slot(slot, left_click)
            self.on_container_changed()
        else:
            left, top = self._gui_origin()
            if not (left <= x < left + self.x_size and top <= y < top + self.y_size):
                if self.dragged is not None:
                    if left_click:
                        player.drop_player_item(self.dragged)
                        self.dragged = None
                    else:
                        one = self.dragged.split(1)
                        player.drop_player_item(one)
                        if self.dragged.count <= 0:
                            self.dragged = None

    def on_container_changed(self):
        pass

    def on_result_taken(self):
        pass

    def _click_result_slot(self, slot):
        result = slot.get_stack()
        if result is None:
            return
        held = self.dragged
        if held is None:
            self.dragged = result
            slot.put_stack(None)
        elif (held.id == result.id and held.damage == result.damage
              and held.max_stack_size > 1
              and held.count + result.count <= held.max_stack_size):
            held.count += result.count
            slot.put_stack(None)
        else:
            return
        self.on_result_taken()

    def _click_slot(self, slot, left_click):

        if getattr(slot, "is_result", False):
            self._click_result_slot(slot)
            return

        stack = slot.get_stack()
        held = self.dragged

        if held is None:
            if stack is None:
                return
            if left_click:
                self.dragged = stack
                slot.put_stack(None)
            else:
                half = (stack.count + 1) // 2
                self.dragged = stack.split(half)
                if stack.count <= 0:
                    slot.put_stack(None)
            return

        if not slot.is_item_valid(held):
            return

        if stack is None:
            if left_click:
                slot.put_stack(held)
                self.dragged = None
            else:
                one = held.split(1)
                slot.put_stack(one)
                if held.count <= 0:
                    self.dragged = None
            return

        if stack.id == held.id and stack.damage == held.damage:
            limit = min(stack.max_stack_size, slot.inventory.stack_limit)
            room = limit - stack.count
            if room <= 0:
                return
            if left_click:
                moved = min(held.count, room)
            else:
                moved = min(1, room)
            if moved <= 0:
                return
            stack.count += moved
            held.count -= moved
            if held.count <= 0:
                self.dragged = None
            return

        if left_click:
            slot.put_stack(held)
            self.dragged = stack

    def draw_gui_container_background(self):
        pass

    def draw_gui_container_foreground(self):
        pass

    def _draw_slot_item(self, slot, left, top):
        stack = slot.get_stack()
        if stack is None:
            return
        ix = left + slot.x
        iy = top + slot.y
        partial = 0.0
        if self.mc.player is not None:
            partial = getattr(self.mc.player, "inventory_animation_partial", 0.0)
        anim = float(getattr(stack, "animations_to_go", 0) or 0) - partial
        if anim > 0.0:
            s = 1.0 + anim / 5.0
            sx = 1.0 / s
            sy = (s + 1.0) / 2.0
            cx = ix + 8.0
            cy = iy + 12.0
            glPushMatrix()
            glTranslatef(cx, cy, 0.0)
            glScalef(sx, sy, 1.0)
            glTranslatef(-cx, -cy, 0.0)
            self.mc.gui._draw_item_icon(stack, ix, iy, 16)
            glPopMatrix()
        else:
            self.mc.gui._draw_item_icon(stack, ix, iy, 16)
        if stack.count > 1:
            label = str(stack.count)
            d = self.mc.display
            tw = d.string_width(label)
            draw_string(d, label, left + slot.x + 17 - tw, top + slot.y + 9, 0xFFFFFF)

    def draw_screen(self, mx, my):
        self._sync_authoritative_cursor()
        self._last_mx = mx
        self._last_my = my
        self.draw_default_background()
        left, top = self._gui_origin()
        self.draw_gui_container_background()

        self.mc.gui.begin_flat_icon_batch()
        try:
            glColor4f(1.0, 1.0, 1.0, 1.0)
            for slot in self.slots:
                self._draw_slot_item(slot, left, top)
                if slot.contains(mx, my, left, top):
                    glDisable(GL_TEXTURE_2D)
                    glEnable(GL_BLEND)
                    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
                    glColor4f(1.0, 1.0, 1.0, 0.5)
                    glBegin(GL_QUADS)
                    glVertex2f(left + slot.x, top + slot.y)
                    glVertex2f(left + slot.x + 16, top + slot.y)
                    glVertex2f(left + slot.x + 16, top + slot.y + 16)
                    glVertex2f(left + slot.x, top + slot.y + 16)
                    glEnd()
                    glColor4f(1.0, 1.0, 1.0, 1.0)
        finally:
            self.mc.gui.end_flat_icon_batch()

        self.draw_gui_container_foreground()

        if self.dragged is not None:
            self.mc.gui._draw_item_icon(self.dragged, mx - 8, my - 8, 16)
            if self.dragged.count > 1:
                label = str(self.dragged.count)
                d = self.mc.display
                tw = d.string_width(label)
                draw_string(d, label, mx - 8 + 17 - tw, my - 8 + 9, 0xFFFFFF)

        hovered_slot = self._slot_at(mx, my)
        if self.dragged is None and hovered_slot is not None:
            hovered_stack = hovered_slot.get_stack()
            if hovered_stack is not None:
                self.mc.gui.draw_item_tooltip(hovered_stack, mx, my)


class GuiCraftingContainer(GuiContainer):
    GRID_WIDTH = 3
    GRID_HEIGHT = 3

    def __init__(self):
        super().__init__()
        self.grid = CraftingGrid(self.GRID_WIDTH, self.GRID_HEIGHT)
        self.result_inv = CraftResult()

    def _add_crafting_slots(self, result_pos, grid_origin):
        self.slots.append(ResultSlot(self.result_inv, 0, *result_pos))
        gx, gy = grid_origin
        for row in range(self.grid.height):
            for col in range(self.grid.width):
                self.slots.append(
                    Slot(self.grid, col + row * self.grid.width, gx + col * 18, gy + row * 18)
                )

    def update_crafting_result(self):
        self.result_inv.set_stack(
            0, CraftingManager.get_instance().find_matching_recipe(self.grid.cells())
        )

    def on_container_changed(self):
        self.update_crafting_result()

    def on_result_taken(self):
        self.grid.consume_one_each()
        self.update_crafting_result()

    def on_gui_closed(self):
        super().on_gui_closed()
        player = getattr(self.mc, "player", None)
        for stack in self.grid.take_all():
            if player is not None:
                player.drop_player_item(stack)
        self.result_inv.set_stack(0, None)


class GuiInventory(GuiCraftingContainer):
    GRID_WIDTH = 2
    GRID_HEIGHT = 2

    def does_gui_pause_game(self):
        return False

    def __init__(self, player):
        super().__init__()
        self.player = player
        self._inv_tex = None

    def init_gui(self):
        inv = self.player.inventory
        self.slots = []
        self._add_crafting_slots((144, 36), (88, 26))

        for i in range(4):
            self.slots.append(Slot(inv, inv.MAIN_SIZE + i, 8, 8 + i * 18, armor_type=i))

        for row in range(3):
            for col in range(9):
                self.slots.append(Slot(inv, col + (row + 1) * 9, 8 + col * 18, 84 + row * 18))

        for col in range(9):
            self.slots.append(Slot(inv, col, 8 + col * 18, 142))
        self.update_crafting_result()

    def draw_gui_container_background(self):
        if self._inv_tex is None:
            self._inv_tex = Texture(_INVENTORY_TEX_PATH)
        left, top = self._gui_origin()
        glColor4f(1.0, 1.0, 1.0, 1.0)
        draw_tex_rect(self._inv_tex, left, top, 0, 0, self.x_size, self.y_size)

    def draw_gui_container_foreground(self):
        left, top = self._gui_origin()
        draw_string(self.mc.display, "Crafting", left + 86, top + 16, 0x404040, shadow=False)
        mx = getattr(self, "_last_mx", left + 51)
        my = getattr(self, "_last_my", top + 75)
        world = getattr(self.mc, "world", None)
        partial = world.partial_ticks() if world is not None else 0.0
        player_model.render_inventory_player(
            self.player,
            left + 51,
            top + 75,
            scale_px=30.0,
            mouse_x=mx,
            mouse_y=my,
            partial=partial,
        )


class GuiCrafting(GuiCraftingContainer):
    def __init__(self, player):
        super().__init__()
        self.player = player
        self._tex = None

    def init_gui(self):
        inv = self.player.inventory
        self.slots = []
        self._add_crafting_slots((124, 35), (30, 17))
        for row in range(3):
            for col in range(9):
                self.slots.append(Slot(inv, col + (row + 1) * 9, 8 + col * 18, 84 + row * 18))
        for col in range(9):
            self.slots.append(Slot(inv, col, 8 + col * 18, 142))
        self.update_crafting_result()

    def draw_gui_container_background(self):
        if self._tex is None:
            self._tex = Texture(_CRAFTING_TEX_PATH)
        left, top = self._gui_origin()
        glColor4f(1.0, 1.0, 1.0, 1.0)
        draw_tex_rect(self._tex, left, top, 0, 0, self.x_size, self.y_size)

    def draw_gui_container_foreground(self):
        left, top = self._gui_origin()
        d = self.mc.display
        draw_string(d, "Crafting", left + 28, top + 6, 0x404040, shadow=False)
        draw_string(d, translate("key.inventory"), left + 8, top + self.y_size - 96 + 2, 0x404040, shadow=False)


_CONTAINER_TEX_PATH = "game/textures/gui/container.png"
_FURNACE_TEX_PATH = "game/textures/gui/furnace.png"


class TileSlot(Slot):
    def __init__(self, tile_entity, index, x, y, *, armor_type=None, fuel_only=False, input_only=False, output_only=False):
        super().__init__(tile_entity, index, x, y, armor_type=armor_type)
        self.fuel_only = fuel_only
        self.input_only = input_only
        self.output_only = output_only

    def is_item_valid(self, stack):
        if self.output_only:
            return False
        if stack is None:
            return True
        if self.fuel_only:
            from game.block.tile_entity import get_item_burn_time
            return get_item_burn_time(stack.id) > 0
        return True


class GuiChest(GuiContainer):
    ROWS = 3
    x_size = 176
    y_size = 114 + ROWS * 18

    def __init__(self, player, tile_entity):
        super().__init__()
        self.player = player
        self.tile_entity = tile_entity
        self.ROWS = 6 if int(getattr(tile_entity, "SIZE", 27)) >= 54 else 3
        self.y_size = 114 + self.ROWS * 18
        if self.ROWS == 6:
            self.chest_title = "Large Chest"
        elif callable(getattr(tile_entity, "get_inv_name", None)):
            self.chest_title = str(tile_entity.get_inv_name())
        else:
            self.chest_title = translate("tile.chest.name")
        self._tex = None

    def init_gui(self):
        inv = self.player.inventory
        te = self.tile_entity
        rows = self.ROWS
        offset = (rows - 4) * 18
        self.slots = []
        for row in range(rows):
            for col in range(9):
                self.slots.append(TileSlot(te, col + row * 9, 8 + col * 18, 18 + row * 18))
        for row in range(3):
            for col in range(9):
                self.slots.append(Slot(inv, col + (row + 1) * 9, 8 + col * 18, 103 + row * 18 + offset))
        for col in range(9):
            self.slots.append(Slot(inv, col, 8 + col * 18, 161 + offset))

    def draw_gui_container_background(self):
        if self._tex is None:
            self._tex = Texture(_CONTAINER_TEX_PATH)
        left, top = self._gui_origin()
        glColor4f(1.0, 1.0, 1.0, 1.0)
        upper = self.ROWS * 18 + 17
        draw_tex_rect(self._tex, left, top, 0, 0, self.x_size, upper)
        draw_tex_rect(self._tex, left, top + upper, 0, 126, self.x_size, 96)

    def draw_gui_container_foreground(self):
        left, top = self._gui_origin()
        d = self.mc.display
        draw_string(d, self.chest_title, left + 8, top + 6, 0x404040, shadow=False)
        draw_string(d, translate("key.inventory"), left + 8, top + self.y_size - 96 + 2, 0x404040, shadow=False)


class GuiFurnace(GuiContainer):
    x_size = 176
    y_size = 166

    def __init__(self, player, tile_entity):
        super().__init__()
        self.player = player
        self.tile_entity = tile_entity
        self._tex = None

    def init_gui(self):
        inv = self.player.inventory
        te = self.tile_entity
        self.slots = []
        self.slots.append(TileSlot(te, 0, 56, 17, input_only=True))   
        self.slots.append(TileSlot(te, 1, 56, 53, fuel_only=True))    
        self.slots.append(TileSlot(te, 2, 116, 35, output_only=True)) 
        for row in range(3):
            for col in range(9):
                self.slots.append(Slot(inv, col + (row + 1) * 9, 8 + col * 18, 84 + row * 18))
        for col in range(9):
            self.slots.append(Slot(inv, col, 8 + col * 18, 142))

    def draw_gui_container_background(self):
        if self._tex is None:
            self._tex = Texture(_FURNACE_TEX_PATH)
        left, top = self._gui_origin()
        glColor4f(1.0, 1.0, 1.0, 1.0)
        draw_tex_rect(self._tex, left, top, 0, 0, self.x_size, self.y_size)
        te = self.tile_entity
        if te is not None and te.is_burning():
            burn = te.get_burn_time_remaining_scaled(12)
            if burn > 0:
                draw_tex_rect(self._tex, left + 56, top + 36 + 12 - burn, 176, 12 - burn, 14, burn)
        if te is not None:
            cook = te.get_cook_progress_scaled(24)
            if cook > 0:
                draw_tex_rect(self._tex, left + 79, top + 34, 176, 14, cook, 16)

    def draw_gui_container_foreground(self):
        left, top = self._gui_origin()
        d = self.mc.display
        draw_string(d, translate("tile.furnace.name"), left + 60, top + 6, 0x404040, shadow=False)
        draw_string(d, translate("key.inventory"), left + 8, top + self.y_size - 96 + 2, 0x404040, shadow=False)

class GuiEditSign(GuiScreen):
    ALLOWED = (
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789 ,.:-_'\"*!\"#%/()=+?[]{}<>"
    )

    def __init__(self, tile_entity):
        super().__init__()
        self.tile_entity = tile_entity
        self.edit_line = 0
        self.update_counter = 0

    def init_gui(self):
        self.controls = []
        self.controls.append(GuiButton(0, self.width // 2 - 100, self.height // 4 + 120, translate("gui.done")))

    def does_gui_pause_game(self):
        return True

    def on_gui_closed(self):
        te = self.tile_entity
        if te is not None:
            te.line_being_edited = -1

    def update_screen(self):
        self.update_counter += 1

    def action_performed(self, button):
        if button.id == 0:
            self.mc.display_gui_screen(None)

    def key_typed(self, char, key):
        te = self.tile_entity
        if te is None:
            return
        if key == pg.K_ESCAPE:
            self.mc.display_gui_screen(None)
            return
        if key in (pg.K_UP, pg.K_KP8):
            self.edit_line = (self.edit_line - 1) & 3
            return
        if key in (pg.K_DOWN, pg.K_KP2, pg.K_RETURN, pg.K_KP_ENTER):
            self.edit_line = (self.edit_line + 1) & 3
            return
        if key in (pg.K_BACKSPACE, pg.K_DELETE):
            line = te.sign_text[self.edit_line]
            if line:
                te.sign_text[self.edit_line] = line[:-1]
            return
        if not char:
            return
        ch = char[0] if isinstance(char, str) and char else ""
        if ch and ch in self.ALLOWED and len(te.sign_text[self.edit_line]) < 15:
            te.sign_text[self.edit_line] = te.sign_text[self.edit_line] + ch

    def draw_screen(self, mx, my):
        draw_gradient_rect(0, 0, self.width, self.height, 0xC0101010, 0xD0303030)
        d = self.mc.display
        te = self.tile_entity
        if te is not None:
            from game.block.sign_renderer import render_sign_in_gui
            render_sign_in_gui(
                te, d, self.width, self.height,
                self.update_counter, self.edit_line,
            )
            d.setup_2d_projection()
            scale, _, _ = self.mc.scaled_resolution()
            glMatrixMode(GL_MODELVIEW)
            glLoadIdentity()
            glScalef(scale, scale, 1.0)
            glColor4f(1.0, 1.0, 1.0, 1.0)
        glDisable(GL_DEPTH_TEST)
        draw_centered_string(d, "Edit sign message:", self.width // 2, 40, 0xFFFFFF)
        super().draw_screen(mx, my)
