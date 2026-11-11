
from __future__ import annotations

import functools
import math
import sys
from pathlib import Path

import pygame as pg

from game.localization import translate_mod

MOD_ID = "creative"
SETTINGS = (
    {
        "key": "flight_speed", "label": "Flight speed", "type": "slider",
        "default": 0.25, "min": 0.05, "max": 0.8,
    },
    {
        "key": "give_stacks", "label": "Give", "type": "toggle",
        "default": True,
    },
    {
        "key": "noclip", "label": "Noclip", "type": "toggle",
        "default": False,
    },
)

_DEFAULTS = {setting["key"]: setting["default"] for setting in SETTINGS}
_CONFIG = dict(_DEFAULTS)
_ORIGINALS = {}
_INSTALLED = False
_ALL_ITEMS_TEXTURE = None
_DEBUG_TOOLS = None


def _debug_tools():
    global _DEBUG_TOOLS
    if _DEBUG_TOOLS is None:
        import importlib.util
        name = "_minecraftpy_mod_creative_debug_tools"
        module = sys.modules.get(name)
        if module is None:
            spec = importlib.util.spec_from_file_location(
                name, Path(__file__).with_name("debug_tools.py"),
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        _DEBUG_TOOLS = module
    return _DEBUG_TOOLS

_B18_CREATIVE_BLOCK_IDS = (
    4, 1, 56, 14, 15, 16, 21, 73, 98, 98, 98, 82, 57, 41, 42, 7, 22, 45,
    48, 44, 44, 44, 44, 44, 44, 49, 87, 88, 89, 17, 17, 17, 18, 3, 2, 12,
    24, 13, 30, 5, 6, 6, 6, 32, 19, 79, 80, 37, 38, 39, 40, 83, 81, 103,
    86, 91, 106, 101, 102, 54, 58, 20, 46, 47, *(35 for _ in range(16)),
    23, 61, 25, 84, 29, 33, 85, 107, 65, 66, 27, 28, 50, 53, 67, 108,
    109, 69, 70, 72, 76, 77, 92, 96,
)

_B12_SUPPORTED_CREATIVE_VARIANTS = {
    6: frozenset((0,)),
    17: frozenset((0, 1, 2)),
    35: frozenset(range(16)),
    44: frozenset(range(4)),
}
_B12_CORE_BLOCK_IDS = frozenset({
    1, 2, 3, 4, 5, 6, 7, 9, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
    21, 22, 23, 24, 25, 35, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46,
    47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62,
    63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78,
    79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92,
})


def _apply_config(config):
    for key, default in _DEFAULTS.items():
        value = config.get(key, default)
        if key == "flight_speed":
            try:
                value = max(0.05, min(0.8, float(value)))
            except (TypeError, ValueError):
                value = default
        else:
            value = value if isinstance(value, bool) else default
        _CONFIG[key] = value


def on_config_changed(game_or_config, config=None):
    if config is None and isinstance(game_or_config, dict):
        config = game_or_config
    if isinstance(config, dict):
        _apply_config(config)


def _give_choice_label(give_stacks):
    from game.localization import translate_mod
    key = "settings.give_stacks.stack" if bool(give_stacks) else "settings.give_stacks.one"
    default = "Give: STACK" if bool(give_stacks) else "Give: ONE"
    return translate_mod("creative", key, default=default)


def _creative_active(player):
    return bool(player is not None and int(getattr(player, "gamemode", 0)) == 1)


def _restore_player_gravity(player):
    gravity = getattr(player, "_creative_original_gravity", None)
    if gravity is None:
        try:
            from game.entities.entity import Entity
            gravity = Entity.GRAVITY
        except Exception:
            return
    player.GRAVITY = float(gravity)


def _creative_ground_contact(player):
    if bool(getattr(player, "on_ground", False)):
        return True
    if bool(_CONFIG.get("noclip", False)):
        return False
    collide = getattr(player, "_collides", None)
    position = getattr(player, "pos", None)
    if not callable(collide) or position is None or len(position) < 3:
        return False
    try:
        return bool(collide(float(position[0]), float(position[1]) - 0.05, float(position[2])))
    except (TypeError, ValueError, IndexError):
        return False


def _player_handle_keys(original):
    @functools.wraps(original)
    def wrapped(player, keys, dt):
        old_velocity = tuple(float(value) for value in player.vel)
        was_on_ground = bool(getattr(player, "on_ground", False))
        was_flying = bool(getattr(player, "_creative_flight_enabled", False))
        was_touching_ground = _creative_ground_contact(player) if was_flying else was_on_ground
        original(player, keys, dt)
        if not _creative_active(player):
            player._creative_noclip_active = False
            player._creative_flight_active = False
            player._creative_flight_enabled = False
            _restore_player_gravity(player)
            return

        if not hasattr(player, "_creative_original_gravity"):
            player._creative_original_gravity = float(getattr(player, "GRAVITY", 0.08))
        settings_key = player._key
        jump = bool(keys[settings_key("jump", pg.K_SPACE)])
        was_jump_down = bool(getattr(player, "_creative_jump_was_down", False))
        toggle_window = max(
            0.0,
            float(getattr(player, "_creative_jump_toggle_window", 0.0))
            - min(max(float(dt), 0.0), 0.1) * 20.0,
        )
        if jump and not was_jump_down:
            if toggle_window > 0.0:
                player._creative_flight_enabled = not was_flying
                toggle_window = 0.0
            else:
                toggle_window = 7.0
        player._creative_jump_was_down = jump
        player._creative_jump_toggle_window = toggle_window

        flying = bool(getattr(player, "_creative_flight_enabled", False))
        if was_flying and was_touching_ground:
            flying = False
            player._creative_flight_enabled = False

        if not flying:
            player._creative_noclip_active = False
            player._creative_flight_active = False
            _restore_player_gravity(player)
            return

        speed_scale = float(_CONFIG["flight_speed"]) / 0.25
        flight_acceleration = 0.05 * speed_scale
        forward_amount = (
            float(bool(keys[settings_key("forward", pg.K_w)]))
            - float(bool(keys[settings_key("back", pg.K_s)]))
        )
        strafe_amount = (
            float(bool(keys[settings_key("right", pg.K_d)]))
            - float(bool(keys[settings_key("left", pg.K_a)]))
        )
        analog_axes = getattr(keys, "movement_axes", None)
        if analog_axes is not None:
            try:
                analog_strafe, analog_forward = analog_axes
                strafe_amount += max(-1.0, min(1.0, float(analog_strafe)))
                forward_amount += max(-1.0, min(1.0, float(analog_forward)))
            except (TypeError, ValueError):
                pass
        length = math.hypot(forward_amount, strafe_amount)
        if length > 1.0:
            forward_amount /= length
            strafe_amount /= length

        yaw = math.radians(float(player.yaw))
        move_x = -math.sin(yaw) * forward_amount + math.cos(yaw) * strafe_amount
        move_z = -math.cos(yaw) * forward_amount - math.sin(yaw) * strafe_amount
        ticks = min(max(float(dt), 0.0), 0.1) * 20.0

        original_ax, original_az = getattr(player, "_heading_accel", (0.0, 0.0))
        player.vel[0] -= float(original_ax) * ticks
        player.vel[2] -= float(original_az) * ticks
        if was_on_ground:
            player.vel[1] = old_velocity[1]
        player.vel[0] += move_x * flight_acceleration * ticks
        player.vel[2] += move_z * flight_acceleration * ticks
        descend = bool(keys[settings_key("sneak", pg.K_LSHIFT)])
        vertical_acceleration = 0.15 * speed_scale
        player.vel[1] += (float(jump) - float(descend)) * vertical_acceleration * ticks
        player._heading_accel = (
            move_x * flight_acceleration,
            move_z * flight_acceleration,
        )
        player.jump_requested = False
        player.is_sneaking = False
        player.GRAVITY = 0.0
        player._creative_noclip_active = bool(_CONFIG["noclip"])
        player._creative_flight_active = True
        player._jump_airborne = False
        player.on_ground = False
    return wrapped


def _player_update(original):
    @functools.wraps(original)
    def wrapped(player, keys, dt):
        result = original(player, keys, dt)
        if _creative_active(player) and getattr(player, "_creative_flight_active", False):
            ticks = min(max(float(dt), 0.0), 0.1) * 20.0
            player.vel[1] *= 0.6 ** ticks
        else:
            player._creative_flight_active = False
        return result
    return wrapped


def _entity_move(original):
    @functools.wraps(original)
    def wrapped(entity, dx, dy, dz):
        if getattr(entity, "_creative_noclip_active", False):
            entity.pos[0] += float(dx)
            entity.pos[1] += float(dy)
            entity.pos[2] += float(dz)
            entity.horizontal_collision = False
            entity.vertical_collision = False
            entity._blocked_down = False
            entity.on_ground = False
            return
        return original(entity, dx, dy, dz)
    return wrapped


def _inside_block_overlay(original):
    @functools.wraps(original)
    def wrapped(player, world=None):
        if getattr(player, "_creative_noclip_active", False):
            return None
        return original(player, world)
    return wrapped


def _player_inside_opaque_block(original):
    @functools.wraps(original)
    def wrapped(player, *args, **kwargs):
        if getattr(player, "_creative_noclip_active", False):
            return False
        return original(player, *args, **kwargs)
    return wrapped


def _player_can_be_collided_with(original):
    @functools.wraps(original)
    def wrapped(player):
        if getattr(player, "_creative_noclip_active", False):
            return False
        return original(player)
    return wrapped


def _player_can_be_pushed(original):
    @functools.wraps(original)
    def wrapped(player):
        if getattr(player, "_creative_noclip_active", False):
            return False
        return original(player)
    return wrapped


def _apply_entity_collisions(original):
    @functools.wraps(original)
    def wrapped(entity):
        if getattr(entity, "_creative_noclip_active", False):
            return
        return original(entity)
    return wrapped


def _creative_items():
    from game.block import Block
    from game.entities.item import Item, ItemStack

    Block._load_defs()
    block_id_to_key = {
        int(definition["id"]): key
        for key, definition in (Block._block_defs or {}).items()
        if isinstance(definition, dict) and definition.get("id") is not None
    }
    variant_counts = {6: 0, 17: 0, 35: 0, 44: 0, 98: 0}
    entries = []
    for block_id in _B18_CREATIVE_BLOCK_IDS:
        key = block_id_to_key.get(block_id)
        if key is None or block_id not in _B12_CORE_BLOCK_IDS:
            continue
        damage = variant_counts.get(block_id, 0)
        if block_id in variant_counts:
            variant_counts[block_id] += 1
        supported = _B12_SUPPORTED_CREATIVE_VARIANTS.get(block_id, frozenset((0,)))
        if damage not in supported:
            continue
        entries.append(ItemStack(key, 1, damage))

    item_entries = sorted(
        (
            (int(definition.get("id", 10**9)), key)
            for key, definition in (Item.item_defs or {}).items()
            if isinstance(definition, dict)
            and int(definition.get("id", -1)) >= 256
        ),
        key=lambda pair: pair[0],
    )
    for _item_id, key in item_entries:
        entries.append(ItemStack(key, 1))

    mod_block_defs = getattr(Item, "block_defs", {}) or {}
    existing_block_keys = {
        stack.id for stack in entries if stack.id in mod_block_defs
    }
    mod_block_entries = sorted(
        (
            (int(definition.get("id", 10**9)), key)
            for key, definition in mod_block_defs.items()
            if isinstance(definition, dict)
            and definition.get("mod_id")
            and definition.get("blockitem")
            and key not in existing_block_keys
        ),
        key=lambda pair: pair[0],
    )
    for _block_id, key in mod_block_entries:
        entries.append(ItemStack(key, 1))

    if any(key == "dye" for _item_id, key in item_entries):
        entries.extend(ItemStack("dye", 1, damage) for damage in range(1, 16))
    return entries


def _creative_catalog_pages():
    from game.entities.item import Item

    all_items = _creative_items()
    vanilla_items = []
    mod_items = {}
    for stack in all_items:
        mod_id = Item.definition(stack.id).get("mod_id")
        if mod_id:
            mod_items.setdefault(str(mod_id), []).append(stack)
        else:
            vanilla_items.append(stack)

    all_label = translate_mod("creative", "screen.itemSelection")
    pages = [{
        "id": "Minecraft",
        "label": translate_mod("creative", "screen.page.minecraft", default=all_label),
        "items": vanilla_items,
    }]
    for mod_id in sorted(mod_items, key=str.casefold):
        pages.append({
            "id": mod_id,
            "label": translate_mod(mod_id, "mod.name", default=mod_id.replace("_", " ").title()),
            "items": mod_items[mod_id],
        })
    return pages


def _metadata_block_faces(stack, base_definition):
    if stack is None or not isinstance(base_definition, dict):
        return None
    damage = int(getattr(stack, "damage", 0) or 0) & 15
    item_id = getattr(stack, "id", None)
    definition = dict(base_definition)

    if item_id == "wool":
        from game.block.wool import wool_texture_index
        definition["faces"] = wool_texture_index(damage, base_definition)
    elif item_id == "sapling":
        return None
    elif item_id == "log":
        side = 116 if damage == 1 else 117 if damage == 2 else 20
        definition["faces"] = [side, side, 21, 21, side, side]
    elif item_id == "leaves":
        from game.block import Block
        base_tile = 52 if bool(getattr(Block, "_fancy_graphics", True)) else 53
        definition["faces"] = base_tile + (80 if (damage & 3) == 1 else 0)
    elif item_id == "stone_slab":
        variants = base_definition.get("metaFaces") or {}
        found = variants.get(str(damage & 3))
        if found is not None:
            definition["faces"] = found
    else:
        return None
    return definition


def _draw_stack_count(display, stack, left, top):
    if stack is None or int(getattr(stack, "count", 0)) <= 1:
        return
    from game.gameplay.screens import draw_string
    label = str(stack.count)
    width = display.string_width(label)
    draw_string(display, label, left + 17 - width, top + 9, 0xFFFFFF)


class _CatalogInventory:

    stack_limit = 64

    def __init__(self):
        self.items = [None] * 72

    def get_stack(self, slot):
        return self.items[slot] if 0 <= int(slot) < len(self.items) else None

    def set_stack(self, slot, stack):
        if 0 <= int(slot) < len(self.items):
            self.items[slot] = stack




def _preserve_legacy_gl_state(draw_method):
    @functools.wraps(draw_method)
    def wrapped(self, *args, **kwargs):
        from OpenGL.GL import GL_ALL_ATTRIB_BITS, glPopAttrib, glPushAttrib
        glPushAttrib(GL_ALL_ATTRIB_BITS)
        try:
            return draw_method(self, *args, **kwargs)
        finally:
            glPopAttrib()
    return wrapped

class CreativeInventoryScreen:

    COLUMNS = 8
    ROWS = 9
    PAGE_SIZE = COLUMNS * ROWS
    PANEL_WIDTH = 176
    PANEL_HEIGHT = 208
    SLOT_X = 8
    SLOT_Y = 18
    SLOT_STEP = 18
    SCROLL_X = 154
    SCROLL_TOP = 17
    SCROLL_HEIGHT = 162
    SCROLL_THUMB_HEIGHT = 16

    def __init__(self):
        self.mc = None
        self.width = 0
        self.height = 0
        self.controls = []
        self.items = []
        self.pages = []
        self.page_index = 0
        self.scroll_row = 0
        self._scroll_dragging = False
        self.catalog_inventory = _CatalogInventory()
        self.slots = []
        self.dragged = None

    def set_world_and_resolution(self, mc, width, height):
        global _ALL_ITEMS_TEXTURE
        from engine.textures import Texture
        from game.gameplay.screens import Slot

        self.mc, self.width, self.height = mc, width, height
        self.pages = _creative_catalog_pages()
        self.page_index = 0
        self.items = self.pages[0]["items"] if self.pages else []
        self.scroll_row = 0
        self._scroll_dragging = False
        self.dragged = None
        self.catalog_inventory = _CatalogInventory()
        self.slots = [
            Slot(self.catalog_inventory, index,
                 self.SLOT_X + (index % self.COLUMNS) * self.SLOT_STEP,
                 self.SLOT_Y + (index // self.COLUMNS) * self.SLOT_STEP)
            for index in range(self.PAGE_SIZE)
        ]
        player = getattr(mc, "player", None)
        if player is not None:
            self.slots.extend(
                Slot(player.inventory, index, 8 + index * 18, 184)
                for index in range(9)
            )
        if _ALL_ITEMS_TEXTURE is None:
            asset_path = Path(__file__).resolve().parent / "allitems.png"
            surface = pg.image.load(str(asset_path)).convert_alpha()
            _ALL_ITEMS_TEXTURE = Texture(surface)
        self._populate_catalog_slots()

    def _set_page(self, page_index):
        if not self.pages:
            return
        self.page_index = max(0, min(len(self.pages) - 1, int(page_index)))
        self.items = self.pages[self.page_index]["items"]
        self.scroll_row = 0
        self._populate_catalog_slots()

    def _page_label(self):
        if not self.pages:
            return translate_mod("creative", "screen.page.all", default="All")
        return str(self.pages[self.page_index].get("label", "All"))

    def _survival_inventory_bounds(self):
        left, top = self._panel_origin()
        return left + self.PANEL_WIDTH + 8, top + 4, left + self.PANEL_WIDTH + 32, top + 28

    def _inventory_button_bounds(self):
        return self._survival_inventory_bounds()

    def _open_survival_inventory(self):
        player = getattr(self.mc, "player", None)
        if player is None:
            return False
        from game.gameplay.screens import GuiInventory
        self.mc.display_gui_screen(GuiInventory(player))
        return True

    def _populate_catalog_slots(self):
        first = self.scroll_row * self.COLUMNS
        for index, slot in enumerate(self.slots[:self.PAGE_SIZE]):
            item_index = first + index
            slot.inventory.set_stack(
                slot.index,
                self.items[item_index] if item_index < len(self.items) else None,
            )

    def does_gui_pause_game(self):
        return False

    def on_gui_closed(self):
        if self.dragged is not None and getattr(self.mc, "player", None) is not None:
            self.mc.player.drop_player_item(self.dragged)
            self.dragged = None
        self._scroll_dragging = False

    def update_screen(self):
        pass

    def key_typed(self, char, key):
        inventory_binding = getattr(self.mc.settings, "key_inventory", None)
        inventory_key = inventory_binding.key if inventory_binding else pg.K_e
        if key in (pg.K_ESCAPE, inventory_key):
            self.mc.display_gui_screen(None)

    def _panel_origin(self):
        return (self.width - self.PANEL_WIDTH) // 2, (self.height - self.PANEL_HEIGHT) // 2

    def _max_scroll_rows(self):
        return max(0, len(self.items) // self.COLUMNS - self.ROWS + 2)

    def _set_scroll_row(self, row):
        self.scroll_row = max(0, min(self._max_scroll_rows(), int(row)))
        self._populate_catalog_slots()

    def _set_scroll_from_y(self, y):
        top = self._panel_origin()[1] + self.SCROLL_TOP
        travel = max(1, self.SCROLL_HEIGHT - self.SCROLL_THUMB_HEIGHT)
        fraction = max(0.0, min(1.0, (float(y) - top - self.SCROLL_THUMB_HEIGHT / 2) / travel))
        self._set_scroll_row(round(fraction * self._max_scroll_rows()))

    def _scroll_by(self, rows):
        self._set_scroll_row(self.scroll_row + int(rows))

    def controller_scroll(self, delta_pixels):
        accumulated = float(getattr(self, "_controller_scroll_pixels", 0.0))
        accumulated += float(delta_pixels)
        rows = int(accumulated / float(self.SLOT_STEP))
        if rows:
            accumulated -= rows * float(self.SLOT_STEP)
            self._scroll_by(rows)
        self._controller_scroll_pixels = accumulated
        return True

    def _slot_at(self, x, y):
        left, top = self._panel_origin()
        for slot in self.slots:
            if slot.contains(x, y, left, top):
                return slot
        return None

    def _item_at(self, x, y):
        left, top = self._panel_origin()
        local_x = float(x) - left - self.SLOT_X
        local_y = float(y) - top - self.SLOT_Y
        if local_x < 0 or local_y < 0:
            return None
        col = int(local_x // self.SLOT_STEP)
        row = int(local_y // self.SLOT_STEP)
        if not (0 <= col < self.COLUMNS and 0 <= row < self.ROWS):
            return None
        index = (self.scroll_row + row) * self.COLUMNS + col
        return self.items[index] if index < len(self.items) else None

    def navigation_targets(self):
        left, top = self._panel_origin()
        targets = [
            (left + slot.x + 8.0, top + slot.y + 8.0)
            for slot in self.slots
        ]
        chest_left, chest_top, chest_right, chest_bottom = self._survival_inventory_bounds()
        targets.append(((chest_left + chest_right) / 2.0,
                        (chest_top + chest_bottom) / 2.0))
        return targets

    @staticmethod
    def _same_stack(first, second):
        return (
            first is not None and second is not None
            and first.id == second.id and first.damage == second.damage
        )

    def _click_catalog_slot(self, slot, left_click):
        from game.entities.item import ItemStack

        source = slot.get_stack()
        held = self.dragged
        if held is not None and source is not None and held.id == source.id:
            maximum = held.max_stack_size
            if left_click:
                if bool(_CONFIG["give_stacks"]):
                    held.count = maximum
                elif held.count < maximum:
                    held.count += 1
            elif held.count <= 1:
                self.dragged = None
            else:
                held.count -= 1
            return
        if held is not None:
            self.dragged = None
            return
        if source is None:
            return
        count = source.max_stack_size if bool(_CONFIG["give_stacks"]) else 1
        self.dragged = ItemStack(source.id, count, source.damage)

    def _click_inventory_slot(self, slot, left_click):
        from game.gameplay.screens import GuiContainer
        GuiContainer._click_slot(self, slot, left_click)

    def mouse_clicked(self, x, y, button):
        left, top = self._panel_origin()
        if button == 1:
            bx1, by1, bx2, by2 = self._survival_inventory_bounds()
            if bx1 <= x < bx2 and by1 <= y < by2:
                self._open_survival_inventory()
                return
            if len(self.pages) > 1 and left <= x < left + 20 and top <= y < top + 18:
                self._set_page((self.page_index - 1) % len(self.pages))
                return
            if len(self.pages) > 1 and left + self.PANEL_WIDTH - 20 <= x < left + self.PANEL_WIDTH and top <= y < top + 18:
                self._set_page((self.page_index + 1) % len(self.pages))
                return
        if button in (4, 5):
            if left <= x < left + self.PANEL_WIDTH and top <= y < top + self.PANEL_HEIGHT:
                self._scroll_by(-1 if button == 4 else 1)
            return
        if button not in (1, 3):
            return

        scroll_left = left + self.SCROLL_X
        scroll_top = top + self.SCROLL_TOP
        if button == 1 and scroll_left <= x < scroll_left + 16 and scroll_top <= y < scroll_top + self.SCROLL_HEIGHT:
            self._scroll_dragging = True
            self._set_scroll_from_y(y)
            return

        slot = self._slot_at(x, y)
        if slot is not None:
            if slot.inventory is self.catalog_inventory:
                self._click_catalog_slot(slot, button == 1)
            else:
                self._click_inventory_slot(slot, button == 1)
            return

        if self.dragged is not None and not (
            left <= x < left + self.PANEL_WIDTH and top <= y < top + self.PANEL_HEIGHT
        ):
            player = getattr(self.mc, "player", None)
            if player is not None:
                if button == 1:
                    player.drop_player_item(self.dragged)
                    self.dragged = None
                else:
                    player.drop_player_item(self.dragged.split(1))
                    if self.dragged.count <= 0:
                        self.dragged = None

    def mouse_moved(self, x, y, buttons=()):
        if self._scroll_dragging and buttons and bool(buttons[0]):
            self._set_scroll_from_y(y)

    def mouse_released(self, x, y, button):
        if button == 1:
            self._scroll_dragging = False

    def draw_default_background(self):
        from game.gameplay.screens import draw_gradient_rect
        draw_gradient_rect(0, 0, self.width, self.height, 0xA0101010, 0xA0101010)

    def _draw_slot_item(self, slot, left, top):
        from types import SimpleNamespace
        from game.entities.item import Item
        from game.gameplay.screens import GuiContainer

        stack = slot.get_stack()
        if stack is None:
            return GuiContainer._draw_slot_item(self, slot, left, top)

        base = Item.block_defs.get(stack.id)
        variant = _metadata_block_faces(stack, base)
        if variant is None:
            return GuiContainer._draw_slot_item(self, slot, left, top)

        proxy_id = f"__creative_icon_{stack.id}_{int(stack.damage) & 15}__"
        while proxy_id in Item.block_defs or proxy_id in Item.item_defs:
            proxy_id += "_"
        Item.block_defs[proxy_id] = variant
        proxy_stack = SimpleNamespace(
            id=proxy_id, count=stack.count, damage=stack.damage,
            tint_item_id=stack.id,
            animations_to_go=getattr(stack, "animations_to_go", 0),
        )
        x, y = left + slot.x, top + slot.y
        try:
            self.mc.gui._draw_item_icon(proxy_stack, x, y, 16)
        finally:
            Item.block_defs.pop(proxy_id, None)
        _draw_stack_count(self.mc.display, stack, x, y)

    @_preserve_legacy_gl_state
    def draw_screen(self, mx, my):
        from OpenGL.GL import (
            GL_BLEND, GL_ONE_MINUS_SRC_ALPHA, GL_QUADS, GL_SRC_ALPHA,
            GL_TEXTURE_2D, glBegin, glBlendFunc, glColor4f, glDisable,
            glEnable, glEnd, glVertex2f,
        )
        from game.gameplay.screens import GuiContainer, draw_tex_rect

        left, top = self._panel_origin()
        self.draw_default_background()
        glColor4f(1.0, 1.0, 1.0, 1.0)
        draw_tex_rect(_ALL_ITEMS_TEXTURE, left, top, 0, 0, self.PANEL_WIDTH, self.PANEL_HEIGHT)

        player = getattr(self.mc, "player", None)
        self.mc.gui.begin_flat_icon_batch()
        try:
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

            chest_left, chest_top, chest_right, chest_bottom = self._survival_inventory_bounds()
            chest_hovered = (
                chest_left <= mx < chest_right and chest_top <= my < chest_bottom
            )
            if chest_hovered:
                glDisable(GL_TEXTURE_2D)
                glEnable(GL_BLEND)
                glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
                glColor4f(1.0, 1.0, 1.0, 0.32)
                glBegin(GL_QUADS)
                glVertex2f(chest_left, chest_top)
                glVertex2f(chest_right, chest_top)
                glVertex2f(chest_right, chest_bottom)
                glVertex2f(chest_left, chest_bottom)
                glEnd()
                glColor4f(1.0, 1.0, 1.0, 1.0)
                glEnable(GL_TEXTURE_2D)
            from game.entities.item import ItemStack
            self.mc.gui._draw_item_icon(
                ItemStack("chest", 1), chest_left + 4, chest_top + 4, 16,
            )

            thumb_travel = self.SCROLL_HEIGHT - self.SCROLL_THUMB_HEIGHT - 1
            max_rows = self._max_scroll_rows()
            fraction = self.scroll_row / max_rows if max_rows else 0.0
            thumb_y = top + self.SCROLL_TOP + round(thumb_travel * fraction)
            draw_tex_rect(_ALL_ITEMS_TEXTURE, left + self.SCROLL_X, thumb_y, 0, 208, 16, 16)
        finally:
            self.mc.gui.end_flat_icon_batch()

        from OpenGL.GL import GL_DEPTH_TEST, GL_LIGHTING, glDisable, glEnable
        from game.gameplay.screens import draw_string
        glDisable(GL_DEPTH_TEST)
        glDisable(GL_LIGHTING)
        try:
            page_label = self._page_label()
            draw_string(
                self.mc.display, page_label,
                left + (self.PANEL_WIDTH - self.mc.display.string_width(page_label)) // 2,
                top + 6, 0x404040, shadow=False,
            )
            if len(self.pages) > 1:
                draw_string(self.mc.display, "<", left + 6, top + 6, 0x404040, shadow=False)
                draw_string(self.mc.display, ">", left + self.PANEL_WIDTH - 13, top + 6, 0x404040, shadow=False)

            if self.dragged is not None:
                cursor_left, cursor_top = mx - 8, my - 8
                self.mc.gui._draw_item_icon(
                    self.dragged, cursor_left, cursor_top, 16,
                )
                _draw_stack_count(
                    self.mc.display, self.dragged, cursor_left, cursor_top,
                )
        finally:
            glEnable(GL_DEPTH_TEST)
            glEnable(GL_LIGHTING)
            from OpenGL.GL import glColor4f
            glColor4f(1.0, 1.0, 1.0, 1.0)

        hovered_slot = self._slot_at(mx, my)
        if hovered_slot is not None:
            hovered_stack = hovered_slot.get_stack()
            if hovered_stack is not None:
                self.mc.gui.draw_item_tooltip(hovered_stack, mx, my)


def _player_collect_items(original):
    @functools.wraps(original)
    def wrapped(player, *args, **kwargs):
        if not _creative_active(player):
            return original(player, *args, **kwargs)
        gamemode = player.gamemode
        try:
            player.gamemode = 0
            return original(player, *args, **kwargs)
        finally:
            player.gamemode = gamemode
    return wrapped


def _player_tick_survival(original):
    @functools.wraps(original)
    def wrapped(player, dt, *args, **kwargs):
        result = original(player, dt, *args, **kwargs)
        if not _creative_active(player) or player.health <= 0 or player.pos[1] >= -64.0:
            player._creative_void_accum = 0.0
            return result

        accum = float(getattr(player, "_creative_void_accum", 0.0))
        accum += min(max(float(dt), 0.0), 0.25)
        while accum >= 0.05 and player.health > 0:
            accum -= 0.05
            gamemode = player.gamemode
            try:
                player.gamemode = 0
                player.damage_player(4, source="void", bypass_armor=True)
            finally:
                player.gamemode = gamemode
        player._creative_void_accum = accum
        return result
    return wrapped


def _game_loop_update(original):
    @functools.wraps(original)
    def wrapped(game, dt, *args, **kwargs):
        player = getattr(game, "player", None)
        if not _creative_active(player):
            return original(game, dt, *args, **kwargs)

        had_instance_method = "respawn_at" in getattr(player, "__dict__", {})
        saved_method = getattr(player, "respawn_at")

        def guarded_respawn(position):
            if _creative_active(player) and float(player.pos[1]) <= -100.0:
                return None
            return saved_method(position)

        player.respawn_at = guarded_respawn
        try:
            return original(game, dt, *args, **kwargs)
        finally:
            if had_instance_method:
                player.respawn_at = saved_method
            else:
                try:
                    del player.respawn_at
                except AttributeError:
                    pass
    return wrapped


def _install_controller_button_hook():
    if "controller_button" in _ORIGINALS:
        return True
    try:
        controller_module = sys.modules.get("_minecraftpy_mod_controller")
        if controller_module is None:
            from game.app.mods import _LOADED_MODULES
            controller_module = _LOADED_MODULES.get("controller")
        controller_class = getattr(controller_module, "ControllerIntegration", None)
        if controller_class is None:
            return False
        original = controller_class._handle_button

        @functools.wraps(original)
        def controller_button(integration, button, pressed):
            game = integration.game
            player = getattr(game, "player", None)
            if pressed and button in (2, 3) and _creative_active(player):
                player.stop_digging()
                player.stop_using_item()
                game.display_gui_screen(CreativeInventoryScreen())
                return
            return original(integration, button, pressed)

        _ORIGINALS["controller_button"] = original
        controller_class._handle_button = controller_button
        return True
    except Exception:
        return False


def _wrap_game_start_world(original_start_world):
    @functools.wraps(original_start_world)
    def start_world(mc, world_name="World1", *, display_name=None, seed=None,
                    debug_world=False, accurate_generation=None, creative_mode=False):
        current_screen = getattr(mc, "current_screen", None)
        if current_screen is not None and current_screen.__class__.__name__ == "GuiCreateWorld":
            creative_mode = bool(getattr(current_screen, "creative_mode", creative_mode))
        result = original_start_world(
            mc, world_name, display_name=display_name, seed=seed,
            debug_world=debug_world,
            accurate_generation=accurate_generation,
        )
        if creative_mode and getattr(mc, "player", None) is not None:
            mc.player.gamemode = 1
            save_store = getattr(getattr(mc, "world", None), "save_store", None)
            save_level = getattr(save_store, "save_level", None)
            if callable(save_level):
                save_level(mc.world, mc.player)
        if getattr(mc, "player", None) is not None:
            mc.player._creative_mod_active = int(getattr(mc.player, "gamemode", 0)) == 1
        return result
    return start_world


def _install_hooks(game):
    global _INSTALLED
    if _INSTALLED:
        return
    from game.entities.player import Player
    from game.entities.entity import Entity
    from game.app.game import Game
    from game.app.loop import GameLoopMixin
    from game.app.input import GameInputMixin
    from game.world import World
    from game.gameplay.screens import (
        GuiSelectWorld, GuiCreateWorld, GuiButton, GuiGeneratedModSettings,
    )
    from game.entities import player_model

    _ORIGINALS["player_handle_keys"] = Player.handle_keys
    Player.handle_keys = _player_handle_keys(Player.handle_keys)
    _ORIGINALS["player_update"] = Player.update
    Player.update = _player_update(Player.update)
    _ORIGINALS["player_collect_items"] = Player.collect_items
    Player.collect_items = _player_collect_items(Player.collect_items)
    _ORIGINALS["player_tick_survival"] = Player._tick_survival
    Player._tick_survival = _player_tick_survival(Player._tick_survival)
    _ORIGINALS["game_loop_update"] = GameLoopMixin._update
    GameLoopMixin._update = _game_loop_update(GameLoopMixin._update)
    _ORIGINALS["player_inside_opaque_block"] = Player._inside_opaque_block
    Player._inside_opaque_block = _player_inside_opaque_block(Player._inside_opaque_block)
    _ORIGINALS["player_can_be_collided_with"] = Player.can_be_collided_with
    Player.can_be_collided_with = _player_can_be_collided_with(Player.can_be_collided_with)
    _ORIGINALS["player_can_be_pushed"] = Player.can_be_pushed
    Player.can_be_pushed = _player_can_be_pushed(Player.can_be_pushed)
    _ORIGINALS["apply_entity_collisions"] = Entity._apply_entity_collisions
    Entity._apply_entity_collisions = _apply_entity_collisions(Entity._apply_entity_collisions)
    _ORIGINALS["entity_move"] = Entity.move
    Entity.move = _entity_move(Entity.move)
    _ORIGINALS["inside_block_overlay"] = player_model.inside_opaque_block_texture
    player_model.inside_opaque_block_texture = _inside_block_overlay(
        player_model.inside_opaque_block_texture
    )

    _ORIGINALS["mod_settings_position"] = GuiGeneratedModSettings._position_controls
    original_position_controls = GuiGeneratedModSettings._position_controls

    @functools.wraps(original_position_controls)
    def position_mod_controls(screen):
        original_position_controls(screen)
        if getattr(getattr(screen, "entry", None), "identifier", None) != MOD_ID:
            return
        for index, setting in enumerate(screen.settings):
            if setting.get("key") == "give_stacks":
                control = screen._toggle_controls.get(index)
                if control is not None:
                    control.text = _give_choice_label(
                        screen.config.get("give_stacks", True)
                    )

    GuiGeneratedModSettings._position_controls = position_mod_controls

    _ORIGINALS["game_start_world"] = Game.start_world
    Game.start_world = _wrap_game_start_world(Game.start_world)

    _ORIGINALS["game_key"] = GameInputMixin._game_key
    original_game_key = GameInputMixin._game_key

    @functools.wraps(original_game_key)
    def game_key(mc, event):
        player = getattr(mc, "player", None)
        inventory_binding = getattr(mc.settings, "key_inventory", None)
        inventory_key = inventory_binding.key if inventory_binding else pg.K_e
        if event.key == inventory_key and _creative_active(player):
            player.stop_digging()
            player.stop_using_item()
            mc.display_gui_screen(CreativeInventoryScreen())
            return
        if _creative_active(player) and _debug_tools().handle_key(mc, event):
            return
        return original_game_key(mc, event)

    GameInputMixin._game_key = game_key

    _install_controller_button_hook()

    _ORIGINALS["input_events"] = GameInputMixin._handle_events
    original_handle_events = GameInputMixin._handle_events

    @functools.wraps(original_handle_events)
    def handle_events(mc):
        original_event_get = pg.event.get
        inventory_open = isinstance(
            getattr(mc, "current_screen", None), CreativeInventoryScreen,
        )

        def event_get(*args, **kwargs):
            events = original_event_get(*args, **kwargs)
            translated = []
            wheel_type = getattr(pg, "MOUSEWHEEL", None)
            for event in events:
                event_type = getattr(event, "type", None)
                if event_type == getattr(pg, "KEYUP", object()):
                    _debug_tools().release_held_key(mc, event)
                elif event_type == getattr(pg, "WINDOWFOCUSLOST", object()):
                    mc._creative_debug_f4_held = False

                if inventory_open and wheel_type is not None and event_type == wheel_type:
                    amount = int(getattr(event, "y", 0))
                    if getattr(event, "flipped", False):
                        amount = -amount
                    if amount:
                        translated.append(pg.event.Event(
                            pg.MOUSEBUTTONDOWN,
                            {"button": 4 if amount > 0 else 5,
                             "pos": pg.mouse.get_pos()},
                        ))
                else:
                    translated.append(event)
            return translated

        pg.event.get = event_get
        try:
            return original_handle_events(mc)
        finally:
            pg.event.get = original_event_get

    GameInputMixin._handle_events = handle_events

    _ORIGINALS["world_select_init"] = GuiSelectWorld.init_gui
    original_select_init = GuiSelectWorld.init_gui

    def select_init(screen):
        original_select_init(screen)
        screen.creative_mode = bool(getattr(screen, "creative_mode", False))

    GuiSelectWorld.init_gui = select_init

    _ORIGINALS["create_world_init"] = GuiCreateWorld.init_gui
    original_create_init = GuiCreateWorld.init_gui

    @functools.wraps(original_create_init)
    def create_world_init(screen):
        original_create_init(screen)
        screen.creative_mode = bool(getattr(screen.parent, "creative_mode", False))
        mode_rect = getattr(screen, "create_layout", {}).get(
            "mode_rect",
            (screen.width // 2 - 100, screen.height // 4 + 180, 200, 20),
        )
        screen.controls.append(GuiButton(
            9, *mode_rect[:2],
            translate_mod(
                "creative", "worldSelect.gamemodeCreative" if screen.creative_mode else "worldSelect.gamemodeSurvival",
            ),
            mode_rect[2], mode_rect[3],
        ))

    GuiCreateWorld.init_gui = create_world_init
    _ORIGINALS["create_world_action"] = GuiCreateWorld.action_performed
    original_create_action = GuiCreateWorld.action_performed

    @functools.wraps(original_create_action)
    def create_world_action(screen, button):
        if button.id == 9:
            screen.creative_mode = not screen.creative_mode
            if getattr(screen, "parent", None) is not None:
                screen.parent.creative_mode = screen.creative_mode
            button.text = translate_mod(
                "creative", "worldSelect.gamemodeCreative" if screen.creative_mode else "worldSelect.gamemodeSurvival",
            )
            return
        return original_create_action(screen, button)

    GuiCreateWorld.action_performed = create_world_action

    _ORIGINALS["world_select_action"] = GuiSelectWorld.action_performed
    original_select_action = GuiSelectWorld.action_performed

    def select_action(screen, button):
        if 0 <= button.id < 5 and not hasattr(screen, "entries"):
            try:
                screen.error_text = None
                world_name = f"World{button.id + 1}"
                try:
                    alt_held = bool(pg.key.get_mods() & pg.KMOD_ALT)
                except pg.error:
                    alt_held = False
                from game.gameplay.screens import _world_slot_exists
                slot_exists = _world_slot_exists(screen.save_root, button.id + 1)
                debug_world = (not slot_exists) and alt_held
                accurate = screen.accurate_generation if not slot_exists else None
                screen.mc.start_world(
                    world_name, debug_world=debug_world,
                    accurate_generation=accurate,
                    creative_mode=bool(screen.creative_mode and not slot_exists),
                )
            except Exception as error:
                from game.localization import translate_mod
                screen.error_text = translate_mod(
                    "creative", "worldSelect.openError", button.id + 1, str(error),
                )
            return
        return original_select_action(screen, button)

    GuiSelectWorld.action_performed = select_action
    _INSTALLED = True


def initialize(game):
    from game.app.mods import load_mod_config
    _apply_config(load_mod_config(MOD_ID, _DEFAULTS))
    _install_hooks(game)


def on_world_tick(game, world, player, dt):
    if "controller_button" not in _ORIGINALS:
        _install_controller_button_hook()
    if player is None:
        return
    if int(getattr(player, "gamemode", 0)) == 1:
        player._creative_mod_active = True
        if not hasattr(player, "_creative_flight_enabled"):
            player._creative_flight_enabled = False
        if not hasattr(player, "_creative_jump_was_down"):
            player._creative_jump_was_down = False
        if not hasattr(player, "_creative_jump_toggle_window"):
            player._creative_jump_toggle_window = 0.0
    else:
        player._creative_mod_active = False
        player._creative_flight_enabled = False
        player._creative_flight_active = False
        player._creative_noclip_active = False
