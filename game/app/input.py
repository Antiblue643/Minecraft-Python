import pygame as pg

from game.entities.item import Item
from game.gameplay.screens import GuiIngameMenu, GuiInventory, GuiChat
from game.app.runtime import screen


class GameInputMixin:
    def _handle_events(self):
        for event in pg.event.get():
            cur = self.current_screen
            input_integration = getattr(self, "input_integration", None)
            if (
                input_integration is not None
                and input_integration.handle_event(event, cur)
            ):
                continue

            if event.type == pg.QUIT:
                self.running = False

            elif event.type == pg.VIDEORESIZE:
                screen.handle_resize(event)

            elif event.type == pg.WINDOWFOCUSLOST:
                if self.player is not None:
                    self.player.stop_using_item()
                if self.world is not None and cur is None:
                    self.display_gui_screen(GuiIngameMenu())

            elif event.type == pg.KEYDOWN:
                if event.key == pg.K_F11:
                    screen.toggle_fullscreen()
                elif cur is not None:
                    paste_text = getattr(cur, "paste_text", None)
                    if (
                        event.key == pg.K_v
                        and event.mod & pg.KMOD_CTRL
                        and callable(paste_text)
                    ):
                        paste_text()
                    else:
                        cur.key_typed("", event.key)
                elif self.world is not None:
                    self._game_key(event)

            elif event.type == pg.TEXTINPUT:
                if cur is not None:
                    text_typed = getattr(cur, "text_typed", None)
                    if callable(text_typed):
                        text_typed(event.text)

            elif event.type == pg.MOUSEMOTION:
                if cur is not None:
                    x, y = self.to_scaled(event.pos)
                    moved = getattr(cur, "mouse_moved", None)
                    if callable(moved):
                        moved(x, y, getattr(event, "buttons", ()))

            elif event.type == pg.MOUSEBUTTONDOWN:
                if cur is not None:
                    x, y = self.to_scaled(event.pos)
                    cur.mouse_clicked(x, y, event.button)
                elif self.world is not None:
                    self._game_mouse(event)

            elif event.type == pg.MOUSEBUTTONUP:
                if event.button == 3 and self.player is not None:
                    self.player.stop_using_item()
                if cur is not None:
                    x, y = self.to_scaled(event.pos)
                    cur.mouse_released(x, y, event.button)
                elif self.world is not None:
                    self._game_mouse(event)


    def _toggle_third_person_view(self, cycle_front=False):
        was_third_person = bool(getattr(self, "third_person_view", False))
        was_front = bool(getattr(self, "third_person_front_view", False))
        if cycle_front and was_third_person and not was_front:
            self.third_person_front_view = True
            return
        self.third_person_front_view = False
        self.third_person_view = not was_third_person
        if was_third_person and self.player is not None:
            self.player._is_swing_in_progress = False
            self.player._swing_progress_int = 0
            self.player.swing_progress = 0.0
            self.player.prev_swing_progress = 0.0

    def _game_key(self, event):
        s = self.settings
        player = self.player
        key = event.key
        if key == pg.K_F2:
            from game.app.screenshot import save_screenshot
            try:
                path = save_screenshot()
            except (OSError, pg.error) as exc:
                self.add_chat_message(f"Screenshot failed: {exc}")
            else:
                if path is not None:
                    self.add_chat_message(f"Saved screenshot: {path.name}")
        elif key == pg.K_ESCAPE:
            self.display_gui_screen(GuiIngameMenu())
        elif key == s.key_inventory.key:
            player.stop_digging()
            self.display_gui_screen(GuiInventory(player))
        elif key == s.key_debug.key:
            if pg.key.get_mods() & pg.KMOD_SHIFT:
                self.gui.toggle_chunk_borders()
            else:
                self.gui.toggle_debug()
        elif key == pg.K_F5:
            self._toggle_third_person_view()
        elif key == pg.K_F8:
            self.toggle_performance_log()
        elif key == s.key_drop.key:
            player.drop_current_item()
        elif key == s.key_toggle_fog.key:
            s.toggle_option(
                next(i for i, o in enumerate(s.options) if o.attr == "render_distance")
            )
            if self.world is not None:
                self.apply_render_distance()
        elif key == s.key_chat.key:
            self.display_gui_screen(GuiChat())
        elif key in self.num_keys:
            player.inventory.select_hotbar(self.num_keys[key])


    def _game_mouse(self, event):
        player = self.player
        if event.button == 1:
            if event.type == pg.MOUSEBUTTONDOWN:
                player.start_digging()
            elif event.type == pg.MOUSEBUTTONUP:
                player.stop_digging()
        elif event.button == 2:
            if event.type == pg.MOUSEBUTTONDOWN:
                player.pick_block()
        elif event.button == 3:
            if event.type == pg.MOUSEBUTTONDOWN:
                held = player.inventory.get_active_item()
                held_id = str(getattr(held, "id", "")) if held is not None else ""
                repeats_use = (
                    (held is not None and Item.placed_block(held_id) is not None)
                    or held_id in {"snowball", "egg", "fishing_rod"}
                )
                if player.place_block():
                    if held_id not in {"bow", "fishing_rod"}:
                        player.swing_item()
                    if repeats_use:
                        player.start_using_item()
            elif event.type == pg.MOUSEBUTTONUP:
                player.stop_using_item()
        elif event.type == pg.MOUSEBUTTONDOWN and event.button == 4:
            player.inventory.scroll(-1)
        elif event.type == pg.MOUSEBUTTONDOWN and event.button == 5:
            player.inventory.scroll(1)
