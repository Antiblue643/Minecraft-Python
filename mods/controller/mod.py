
from types import SimpleNamespace
from pathlib import Path
import math
import time

import pygame as pg

from game.app.mods import load_mod_config, save_mod_config
from game.gameplay.screens import (
    GuiChat, GuiContainer, GuiIngameMenu, GuiInventory, GuiSlider,
)

from . import controller as controller_api
from .controller import screen_supports_scroll as _screen_supports_scroll
from .controller import scroll_screen as _scroll_screen

_POINTER_PATH = Path(__file__).resolve().parent / "pointer.png"
_POINTER_SIZE = 48.0
_POINTER_HOTSPOT = (24.0, 24.0)
_SLIDER_STEP_FRACTION = 0.05
_WARP_IGNORE_SECONDS = 0.15
_GUI_SCROLL_SPEED = 420.0  


SETTINGS = (
    {
        "key": "sneak_toggle",
        "label": "Sneak toggle",
        "type": "toggle",
        "default": True,
    },
    {
        "key": "sensitivity",
        "label": "Controller sensitivity",
        "type": "slider",
        "min": 0.0,
        "max": 1.0,
        "default": 0.5,
    },
)
_CONFIG_DEFAULTS = {
    "enabled": True,
    **{setting["key"]: setting["default"] for setting in SETTINGS},
}


def initialize(game):
    config = load_mod_config("controller", _CONFIG_DEFAULTS)
    config["enabled"] = (
        config["enabled"]
        if isinstance(config.get("enabled"), bool) else True
    )
    try:
        sensitivity = float(config.get("sensitivity", 0.5))
    except (TypeError, ValueError):
        sensitivity = 0.5
    config["sensitivity"] = max(
        0.0, min(1.0, sensitivity if math.isfinite(sensitivity) else 0.5)
    )
    config["sneak_toggle"] = (
        config["sneak_toggle"]
        if isinstance(config.get("sneak_toggle"), bool) else True
    )
    save_mod_config("controller", config)
    game.input_integration = (
        ControllerIntegration(game, config) if config["enabled"] else None
    )


def on_config_changed(game, config):
    integration = getattr(game, "input_integration", None)
    if isinstance(integration, ControllerIntegration):
        integration.config.update(config)


class ControllerIntegration:

    def __init__(self, game, config):
        self.game = game
        self.config = config
        self.manager = controller_api.ControllerManager(pg)
        self.state = controller_api.ControllerState()
        self.cursor_pos = None
        self.warp_pos = None
        self.held_mouse_buttons = set()
        self.nav_screen = None
        self.hat_direction = (0, 0)
        self.hat_elapsed = 0.0
        self.jump_suppressed = False
        self.sneak_latched = False
        self.sneak_was_down = False
        self.using_controller = False
        self.captured_slider = None
        self._pointer_texture = None
        self._last_warp_time = 0.0

    def handle_event(self, event, screen):
        game = self.game
        self.manager.handle_event(event)
        if event.type in (
            getattr(pg, "MOUSEBUTTONDOWN", None), getattr(pg, "KEYDOWN", None),
        ):
            self._release_captured_slider(screen)
            self.using_controller = False
        if event.type == getattr(pg, "MOUSEMOTION", None) and screen is None:
            self.using_controller = False
        if event.type == getattr(pg, "WINDOWFOCUSLOST", None):
            self.jump_suppressed = False
            self.warp_pos = None
            return False

        if event.type == getattr(pg, "MOUSEMOTION", None) and screen is not None:
            event_pos = tuple(getattr(event, "pos", ()))
            if self.warp_pos is not None and event_pos == tuple(self.warp_pos):
                self.warp_pos = None
            else:
                if time.monotonic() - self._last_warp_time > _WARP_IGNORE_SECONDS:
                    self._release_captured_slider(screen)
                    self.using_controller = False
                self.cursor_pos = list(game.to_scaled(event.pos))
                self.warp_pos = None
                moved = getattr(screen, "mouse_moved", None)
                if callable(moved):
                    moved(*self.cursor_pos, getattr(event, "buttons", ()))
            return True

        if event.type == getattr(pg, "JOYBUTTONDOWN", None):
            self.using_controller = True
            self._handle_button(getattr(event, "button", -1), True)
            return True
        if event.type == getattr(pg, "JOYBUTTONUP", None):
            self._handle_button(getattr(event, "button", -1), False)
            return True
        if event.type == getattr(pg, "JOYDEVICEREMOVED", None):
            self.jump_suppressed = False
            self.cursor_pos = None
            self.warp_pos = None
            self.using_controller = False
            self._release_captured_slider(screen)
            return True
        return False

    def _cursor_position(self):
        position = self.cursor_pos
        if position is None:
            position = self.game.to_scaled(pg.mouse.get_pos())
        return int(position[0]), int(position[1])

    def _slider_at(self, screen, x, y):
        for control in getattr(screen, "controls", ()):
            if (
                isinstance(control, GuiSlider) and control.visible
                and control.enabled and control._contains(x, y)
            ):
                return control
        return None

    def _release_captured_slider(self, screen):
        slider = self.captured_slider
        if slider is None:
            return False
        self.captured_slider = None
        if screen is not None:
            x, y = self._cursor_position()
            screen.mouse_released(x, y, 1)
        else:
            slider.release()
        return True

    def _handle_button(self, button, pressed):
        game = self.game
        screen = game.current_screen
        if button == 0 and not pressed:
            self.jump_suppressed = False
        if self.captured_slider is not None and (
            screen is None or self.captured_slider not in getattr(screen, "controls", ())
        ):
            self.captured_slider = None
        if screen is not None:
            if button == 0 and pressed:
                self.jump_suppressed = True
            if self.captured_slider is not None:
                if pressed and button in (0, 1, 7):
                    self._release_captured_slider(screen)
                return
            if (not pressed and button in (0, 2)) or (
                pressed and button in (0, 2)
            ):
                x, y = self._cursor_position()
                if pressed:
                    screen.mouse_clicked(x, y, 1 if button == 0 else 3)
                    if button == 0:
                        slider = self._slider_at(screen, x, y)
                        if slider is not None and slider.dragging:
                            self.captured_slider = slider
                else:
                    screen.mouse_released(x, y, 1 if button == 0 else 3)
            elif pressed and button in (1, 7):
                screen.key_typed("", pg.K_ESCAPE)
            return

        player = game.player
        if player is None or game.world is None:
            return
        if button == 1 and pressed:
            player.drop_current_item()
        elif button in (2, 3) and pressed:
            player.stop_digging()
            player.stop_using_item()
            game.display_gui_screen(GuiInventory(player))
        elif button == 4 and pressed:
            player.inventory.scroll(-1)
        elif button == 5 and pressed:
            player.inventory.scroll(1)
        elif button == 6 and pressed:
            player.stop_digging()
            player.stop_using_item()
            game.display_gui_screen(GuiChat())
        elif button == 7 and pressed:
            player.stop_digging()
            player.stop_using_item()
            game.display_gui_screen(GuiIngameMenu())
        elif button == 8 and pressed:
            game._toggle_third_person_view(cycle_front=True)

    def update(self, dt):
        game = self.game
        self.state = self.manager.poll()
        screen = game.current_screen
        active = screen is None and game.world is not None and game.player is not None
        desired = {
            1: active and self.state.right_trigger >= 0.55,
            3: active and self.state.left_trigger >= 0.55,
        }
        for button in (1, 3):
            down = bool(desired[button])
            was_down = button in self.held_mouse_buttons
            if down != was_down:
                if game.player is not None:
                    game._game_mouse(SimpleNamespace(
                        type=pg.MOUSEBUTTONDOWN if down else pg.MOUSEBUTTONUP,
                        button=button,
                    ))
                if down:
                    self.held_mouse_buttons.add(button)
                else:
                    self.held_mouse_buttons.discard(button)

        if screen is None:
            self.cursor_pos = None
            self.warp_pos = None
            self.nav_screen = None
            self.hat_direction = (0, 0)
            self.hat_elapsed = 0.0
            self.captured_slider = None
        else:
            self._update_gui_cursor(screen, dt)
            try:
                pg.mouse.set_visible(
                    not self.using_controller
                )
            except Exception:
                pass

        sneak_down = 9 in self.state.buttons
        if bool(self.config["sneak_toggle"]):
            if sneak_down and not self.sneak_was_down and screen is None:
                self.sneak_latched = not self.sneak_latched
        else:
            self.sneak_latched = False
        self.sneak_was_down = sneak_down
        return self.state

    def _update_gui_cursor(self, screen, dt):
        if self.nav_screen is not screen:
            self.nav_screen = screen
            self.hat_direction = (0, 0)
            self.hat_elapsed = 0.0
        hat = self.state.hat or (0, 0)
        direction = (
            1 if hat[0] > 0 else -1 if hat[0] < 0 else 0,
            -1 if hat[1] > 0 else 1 if hat[1] < 0 else 0,
        )
        if direction[0] and direction[1]:
            direction = (direction[0], 0)
        navigate = False
        if direction == (0, 0):
            self.hat_elapsed = 0.0
        elif direction != self.hat_direction:
            navigate = True
            self.hat_elapsed = 0.0
        else:
            self.hat_elapsed += max(0.0, min(float(dt), 0.1))
            if self.hat_elapsed >= 0.34:
                navigate = True
                self.hat_elapsed = 0.20
        self.hat_direction = direction

        cursor = self.cursor_pos or list(self.game.to_scaled(pg.mouse.get_pos()))
        slider = self.captured_slider
        if slider is not None and slider not in getattr(screen, "controls", ()):
            slider = self.captured_slider = None
        if direction != (0, 0) or any(
            abs(value) > 0.001 for value in (
                self.state.move_x, self.state.move_y,
                self.state.look_x, self.state.look_y,
            )
        ):
            self.using_controller = True

        scrollable = _screen_supports_scroll(screen)
        right_x = float(getattr(self.state, "look_x", 0.0))
        right_y = float(getattr(self.state, "look_y", 0.0))
        right_active = abs(right_x) > 0.001 or abs(right_y) > 0.001
        if scrollable and right_active:
            if abs(right_y) > 0.001:
                _scroll_screen(
                    screen, right_y * _GUI_SCROLL_SPEED
                    * max(0.0, min(float(dt), 0.1)),
                )
            self.using_controller = True

        if slider is not None:
            lo = slider.x + 4
            hi = slider.x + slider.width - 4
            x, y = cursor
            if navigate and direction[0]:
                step = max(1.0, (hi - lo) * _SLIDER_STEP_FRACTION)
                x += direction[0] * step
            stick = self.state.move_x
            if abs(stick) > 0.001:
                x += stick * (hi - lo) * 0.9 * max(0.0, min(float(dt), 0.1))
            x = max(lo, min(hi, x))
            y = slider.y + slider.height // 2
            self.cursor_pos = [x, y]
            if (x, y) != tuple(cursor):
                self._warp_cursor(self.cursor_pos)
            return

        if navigate:
            targets = getattr(screen, "navigation_targets", None)
            if callable(targets):
                target = controller_api.controller_grid_step(
                    cursor, targets(), direction[0], direction[1]
                )
                if target is not None:
                    cursor = [int(target[0]), int(target[1])]
                    self.cursor_pos = cursor
                    self._warp_cursor(cursor)

        if scrollable:
            dx = self.state.move_x
            dy = -self.state.move_y
        else:
            dx = self.state.move_x + self.state.look_x
            dy = -self.state.move_y + self.state.look_y
        if abs(dx) > 0.001 or abs(dy) > 0.001:
            x, y = cursor
            speed = 650.0 * max(0.0, min(float(dt), 0.1))
            _, width, height = self.game.scaled_resolution()
            x = max(0, min(max(0, width - 1), x + dx * speed))
            y = max(0, min(max(0, height - 1), y + dy * speed))
            self.cursor_pos = [x, y]
            self._warp_cursor(self.cursor_pos)

    def _warp_cursor(self, cursor):
        try:
            scale = self.game.scaled_resolution()[0]
            target = (int(cursor[0] * scale), int(cursor[1] * scale))
            if tuple(pg.mouse.get_pos()) != target:
                self.warp_pos = target
                self._last_warp_time = time.monotonic()
                pg.mouse.set_pos(target)
        except Exception:
            self.warp_pos = None

    def draw_cursor(self, game):
        if (
            not self.using_controller
            or game.current_screen is None
        ):
            return
        if self._pointer_texture is None:
            try:
                from engine.textures import Texture
                self._pointer_texture = Texture(
                    pg.image.load(str(_POINTER_PATH)).convert_alpha()
                )
            except Exception:
                self._pointer_texture = False
        texture = self._pointer_texture
        if not texture:
            return
        from OpenGL.GL import (
            GL_BLEND, GL_DEPTH_TEST, GL_LIGHTING, GL_ONE_MINUS_SRC_ALPHA,
            GL_QUADS, GL_SRC_ALPHA, GL_TEXTURE_2D, glBegin, glBlendFunc,
            glColor4f, glDisable, glEnable, glEnd, glIsEnabled,
            glTexCoord2f, glVertex2f,
        )
        scale = max(1, int(game.scaled_resolution()[0]))
        x, y = self._cursor_position()
        size = _POINTER_SIZE / scale
        left = x - _POINTER_HOTSPOT[0] / scale
        top = y - _POINTER_HOTSPOT[1] / scale

        previous_states = {
            GL_DEPTH_TEST: bool(glIsEnabled(GL_DEPTH_TEST)),
            GL_LIGHTING: bool(glIsEnabled(GL_LIGHTING)),
            GL_BLEND: bool(glIsEnabled(GL_BLEND)),
            GL_TEXTURE_2D: bool(glIsEnabled(GL_TEXTURE_2D)),
        }
        try:
            glDisable(GL_DEPTH_TEST)
            glDisable(GL_LIGHTING)
            glEnable(GL_TEXTURE_2D)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glColor4f(1.0, 1.0, 1.0, 1.0)
            texture.bind()
            glBegin(GL_QUADS)
            glTexCoord2f(0.0, 0.0); glVertex2f(left, top + size)
            glTexCoord2f(1.0, 0.0); glVertex2f(left + size, top + size)
            glTexCoord2f(1.0, 1.0); glVertex2f(left + size, top)
            glTexCoord2f(0.0, 1.0); glVertex2f(left, top)
            glEnd()
        finally:
            texture.unbind()
            glColor4f(1.0, 1.0, 1.0, 1.0)
            for capability, was_enabled in previous_states.items():
                (glEnable if was_enabled else glDisable)(capability)

    def key_state(self, keyboard_state, allow_movement):
        return controller_api.ControllerKeyState(
            keyboard_state,
            self.game.settings,
            self.state,
            allow_movement=allow_movement,
            allow_controller_jump=not self.jump_suppressed,
            controller_sneak_latched=self.sneak_latched,
            sneak_toggle=self.config["sneak_toggle"],
        )

    def update_look(self, dt, enabled):
        if not enabled or self.game.player is None:
            return
        if _screen_supports_scroll(getattr(self.game, "current_screen", None)):
            return
        look_x = self.state.look_x
        look_y = self.state.look_y
        if not look_x and not look_y:
            return
        speed = 180.0 * max(0.0, min(float(dt), 0.1)) / 0.15
        speed *= _sensitivity_scale(self.config["sensitivity"])
        if self.game.settings.invert_mouse:
            look_y = -look_y
        self.game.player.handle_mouse(look_x * speed, look_y * speed)


def _sensitivity_scale(value):
    fraction = max(0.0, min(1.0, float(value)))
    scaled = fraction * 0.6 + 0.2
    return scaled * scaled * scaled * 8.0
