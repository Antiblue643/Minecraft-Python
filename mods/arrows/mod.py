
from __future__ import annotations

import json
import random
from pathlib import Path

from game.entities.arrow import ArrowEntity
from game.entities.entity import Entity
from game.entities.item import Item
from game.gameplay.crafting import CraftingManager

MOD_ID = "arrows"
MOD_DIR = Path(__file__).resolve().parent
ITEM_ID = "corruptor_arrow"
DEFAULT_RADIUS = 2
_CORRUPTION_EXCLUDED_BLOCKS = {"air", "portal", "nether_portal"}
ARROW_PURPLE_UV_OFFSET = 10

SETTINGS = (
    {
        "key": "radius",
        "label": "Corruption radius",
        "type": "slider",
        "min": 1.0,
        "max": 5.0,
        "default": float(DEFAULT_RADIUS),
    },
)

_config = {
    "radius": DEFAULT_RADIUS,
}
_ITEM_TEXTURE = None


def _apply_config_dict(cfg):
    try:
        radius = int(round(float(cfg.get("radius", DEFAULT_RADIUS))))
    except (TypeError, ValueError):
        radius = DEFAULT_RADIUS
    _config["radius"] = max(1, min(5, radius))


def _load_mod_settings():
    try:
        from game.app.mods import load_mod_config
        cfg = load_mod_config("arrows", {
            "radius": DEFAULT_RADIUS,
        })
        _apply_config_dict(cfg)
    except Exception:
        _config["radius"] = DEFAULT_RADIUS


def _item_png_path():
    return MOD_DIR / "item.png"


def _get_item_texture():
    global _ITEM_TEXTURE
    if _ITEM_TEXTURE is not None:
        return _ITEM_TEXTURE if getattr(_ITEM_TEXTURE, "texture_id", None) else None
    path = _item_png_path()
    if not path.is_file():
        return None
    try:
        from engine.textures import Texture
        from OpenGL.GL import GL_CLAMP_TO_EDGE
        _ITEM_TEXTURE = Texture(str(path), repeat_mode=GL_CLAMP_TO_EDGE)
    except Exception:
        _ITEM_TEXTURE = None
        return None
    return _ITEM_TEXTURE if getattr(_ITEM_TEXTURE, "texture_id", None) else None


def _register_item():
    Item.item_defs[ITEM_ID] = {
        "name": "Corruptor Arrow",
        "mod_id": MOD_ID,
        "texture_path": str(_item_png_path()),
        "type": "generic",
        "hold": "generic",
        "stack": 64,
        "id": 9001,
    }


def _register_recipe():
    recipe_path = MOD_DIR / "recipe.json"
    if not recipe_path.is_file():
        return
    try:
        data = json.loads(recipe_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    manager = CraftingManager.get_instance()
    for name, spec in data.items():
        if not isinstance(spec, dict):
            continue
        if any(getattr(r, "name", None) == name for r in manager.recipes):
            continue
        recipe = CraftingManager._build(name, {
            **spec,
            "result": spec.get("result", name),
        })
        if recipe is not None:
            manager.recipes.append(recipe)
    manager.recipes.sort(key=lambda r: -r.size)


def _all_block_names():
    try:
        from game.content_data import load_block_definitions
        return list(load_block_definitions().keys())
    except Exception:
        return ["stone", "dirt", "cobblestone", "sand", "gravel", "grass_block"]


def _candidate_block_types(world):
    names = _all_block_names()
    world_names = list((getattr(world, "block_type_to_id", None) or {}).keys())
    pool = world_names or names
    types = []
    for name in pool:
        if not name or name in _CORRUPTION_EXCLUDED_BLOCKS:
            continue
        types.append(name)
    if len(types) < 3:
        for name in names:
            if (name and name not in _CORRUPTION_EXCLUDED_BLOCKS
                    and name not in types):
                types.append(name)
    return types or ["stone", "dirt", "cobblestone", "sand", "gravel"]


def _corrupt_around(world, cx, cy, cz, radius, rng):
    if world is None:
        return
    candidates = _candidate_block_types(world)
    if not candidates:
        return
    r = max(1, min(5, int(radius)))
    changed = 0
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            for dz in range(-r, r + 1):
                if dx * dx + dy * dy + dz * dz > r * r + 0.01:
                    continue
                x, y, z = int(cx + dx), int(cy + dy), int(cz + dz)
                current = world.get_block_type(x, y, z)
                if current is None:
                    continue
                new_type = rng.choice(candidates)
                if new_type == current:
                    new_type = rng.choice(candidates)
                    if new_type == current:
                        continue
                try:
                    world.set_block(x, y, z, new_type, meta=rng.randrange(16))
                    changed += 1
                except Exception:
                    continue
    return changed


class CorruptorArrowEntity(ArrowEntity):
    is_corruptor_arrow = True

    def _handle_block_hit(self, start, end, ticks, block_hit):
        super()._handle_block_hit(start, end, ticks, block_hit)
        cell = (int(self.x_tile), int(self.y_tile), int(self.z_tile))
        rng = random.Random(
            (cell[0] * 734287 + cell[1] * 912391 + cell[2] * 1934963)
            ^ int(getattr(self.world_obj, "world_time", 0) or 0)
            ^ random.getrandbits(16)
        )
        _corrupt_around(
            self.world_obj, cell[0], cell[1], cell[2],
            _config["radius"], rng,
        )
        self.dead = True

    def _try_pickup(self):
        player = self.shooting_entity
        if not self.in_ground or self.arrow_shake > 0 or player is None:
            return False
        if getattr(player, "dead", False) or getattr(player, "health", 20) <= 0:
            return False
        player_bounds = self._entity_bounds(player, 0.0)
        pickup_bounds = (
            player_bounds[0] - 1.0, player_bounds[1], player_bounds[2] - 1.0,
            player_bounds[3] + 1.0, player_bounds[4], player_bounds[5] + 1.0,
        )
        half = 0.25
        arrow_bounds = (
            self.pos[0] - half, self.pos[1] - half, self.pos[2] - half,
            self.pos[0] + half, self.pos[1] + half, self.pos[2] + half,
        )
        if not all((
            arrow_bounds[0] < pickup_bounds[3],
            arrow_bounds[3] > pickup_bounds[0],
            arrow_bounds[1] < pickup_bounds[4],
            arrow_bounds[4] > pickup_bounds[1],
            arrow_bounds[2] < pickup_bounds[5],
            arrow_bounds[5] > pickup_bounds[2],
        )):
            return False
        try:
            from game.entities.item import ItemStack
            if not player.inventory.add_stack(ItemStack(ITEM_ID, 1)):
                return False
        except Exception:
            return False
        self._play_sound(
            "random.pop", volume=0.2,
            pitch=((random.random() - random.random()) * 0.7 + 1.0) * 2.0,
        )
        self.dead = True
        return True


def _draw_custom_item_quad(x, y, size, texture):
    from OpenGL.GL import (
        GL_QUADS, GL_TEXTURE_2D, glBegin, glEnd, glEnable, glTexCoord2f, glVertex2f,
        glColor4f,
    )
    glEnable(GL_TEXTURE_2D)
    texture.bind()
    glColor4f(1.0, 1.0, 1.0, 1.0)
    glBegin(GL_QUADS)
    glTexCoord2f(0.0, 0.0); glVertex2f(x, y + size)
    glTexCoord2f(1.0, 0.0); glVertex2f(x + size, y + size)
    glTexCoord2f(1.0, 1.0); glVertex2f(x + size, y)
    glTexCoord2f(0.0, 1.0); glVertex2f(x, y)
    glEnd()
    texture.unbind()


def _patch_gui_item_icons():
    try:
        from game.gameplay import gui as gui_mod
    except Exception:
        return
    target = None
    for name in dir(gui_mod):
        obj = getattr(gui_mod, name)
        if isinstance(obj, type) and hasattr(obj, "_draw_item_icon_impl"):
            target = obj
            break
    if target is None:
        return
    if getattr(target._draw_item_icon_impl, "_corruptor_patched", False):
        return
    original = target._draw_item_icon_impl

    def _draw_item_icon_impl(self, item, x, y, size=16):
        if item is not None and getattr(item, "id", None) == ITEM_ID:
            tex = _get_item_texture()
            if tex is not None:
                from OpenGL.GL import (
                    GL_BLEND, GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_TEXTURE_2D,
                    GL_ALPHA_TEST, GL_GREATER, glEnable, glBlendFunc, glAlphaFunc,
                    glColor4f,
                )
                glEnable(GL_BLEND)
                glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
                glEnable(GL_TEXTURE_2D)
                glEnable(GL_ALPHA_TEST)
                glAlphaFunc(GL_GREATER, 0.01)
                glColor4f(1.0, 1.0, 1.0, 1.0)
                _draw_custom_item_quad(x, y, size, tex)
                draw_bar = getattr(self, "_draw_durability_bar", None)
                if callable(draw_bar):
                    draw_bar(item, x, y, size)
                return
        return original(self, item, x, y, size)

    _draw_item_icon_impl._corruptor_patched = True
    target._draw_item_icon_impl = _draw_item_icon_impl


def _patch_held_item_draw():
    try:
        import game.entities.player_model as pm
    except Exception:
        return
    original = getattr(pm, "_draw_held_sprite", None)
    if original is None or getattr(original, "_corruptor_patched", False):
        return

    def _draw_held_sprite(held, brightness=1.0):
        if held is not None and getattr(held, "id", None) == ITEM_ID:
            tex = _get_item_texture()
            if tex is not None:
                try:
                    from OpenGL.GL import (
                        GL_TEXTURE_2D, GL_ALPHA_TEST, GL_GREATER,
                        glEnable, glAlphaFunc, glColor4f, glTranslatef, glScalef,
                        glRotatef,
                    )
                    glEnable(GL_TEXTURE_2D)
                    tex.bind()
                    glEnable(GL_ALPHA_TEST)
                    glAlphaFunc(GL_GREATER, 0.1)
                    glColor4f(brightness, brightness, brightness, 1.0)
                    glTranslatef(-0.0, -0.3, 0.0)
                    glScalef(1.5, 1.5, 1.5)
                    glRotatef(50.0, 0.0, 1.0, 0.0)
                    glRotatef(335.0, 0.0, 0.0, 1.0)
                    glTranslatef(-(15.0 / 16.0), -(1.0 / 16.0), 0.0)
                    pm._draw_item_sprite(0.0, 0.0, 1.0, 1.0)
                    tex.unbind()
                    return
                except Exception:
                    pass
        return original(held, brightness)

    _draw_held_sprite._corruptor_patched = True
    pm._draw_held_sprite = _draw_held_sprite


def _patch_ground_item_billboard():
    try:
        from game.entities.item_model import ItemRendererMixin
    except Exception:
        return
    if not hasattr(ItemRendererMixin, "_draw_billboard"):
        return
    if getattr(ItemRendererMixin._draw_billboard, "_corruptor_patched", False):
        return
    original = ItemRendererMixin._draw_billboard

    def _draw_billboard(self, x, y, z, copies, rng):
        item = getattr(self, "item", None)
        if item is not None and getattr(item, "id", None) == ITEM_ID:
            tex = _get_item_texture()
            if tex is not None:
                try:
                    from OpenGL.GL import (
                        GL_TEXTURE_2D, GL_QUADS, glEnable, glDisable, glPushMatrix,
                        glPopMatrix, glTranslatef, glRotatef, glBegin, glEnd,
                        glTexCoord2f, glVertex3f, glNormal3f, glColor3f,
                    )
                    from game.entities.entity import Entity
                    s = 0.25
                    renderer = Entity.renderer
                    angle = float(getattr(renderer, "yaw", 0.0)) if renderer else 0.0
                    b = float(getattr(self, "_draw_brightness", 1.0))
                    tex.bind()
                    glEnable(GL_TEXTURE_2D)
                    glColor3f(b, b, b)
                    for copy in range(copies):
                        glPushMatrix()
                        if renderer is not None:
                            ox, oy, oz = renderer.camera_relative_offset(x, y, z)
                        else:
                            ox, oy, oz = float(x), float(y), float(z)
                        glTranslatef(ox, oy, oz)
                        if copy > 0:
                            glTranslatef(
                                (rng.random() * 2.0 - 1.0) * 0.15,
                                (rng.random() * 2.0 - 1.0) * 0.15,
                                (rng.random() * 2.0 - 1.0) * 0.15,
                            )
                        glRotatef(angle, 0, 1, 0)
                        glBegin(GL_QUADS)
                        glNormal3f(0, 0, 1)
                        glTexCoord2f(0.0, 0.0); glVertex3f(-s, 0, 0)
                        glTexCoord2f(1.0, 0.0); glVertex3f(s, 0, 0)
                        glTexCoord2f(1.0, 1.0); glVertex3f(s, s * 2, 0)
                        glTexCoord2f(0.0, 1.0); glVertex3f(-s, s * 2, 0)
                        glEnd()
                        glPopMatrix()
                    glDisable(GL_TEXTURE_2D)
                    tex.unbind()
                    return
                except Exception:
                    pass
        return original(self, x, y, z, copies, rng)

    _draw_billboard._corruptor_patched = True
    ItemRendererMixin._draw_billboard = _draw_billboard


def _patch_arrow_draw():
    try:
        from game.entities import arrow_model
    except Exception:
        return
    if getattr(arrow_model.ArrowRendererMixin.draw, "_corruptor_patched", False):
        return
    original = arrow_model.ArrowRendererMixin.draw

    def draw(self):
        if not getattr(self, "is_corruptor_arrow", False):
            return original(self)
        old_v = arrow_model._arrow_texture_v

        def purple_v(image_y):
            return old_v(float(image_y) + ARROW_PURPLE_UV_OFFSET)

        arrow_model._arrow_texture_v = purple_v
        try:
            return original(self)
        finally:
            arrow_model._arrow_texture_v = old_v

    draw._corruptor_patched = True
    arrow_model.ArrowRendererMixin.draw = draw


def _patch_bow(player_cls):
    original = player_cls._use_bow

    def _use_bow(self, selected):
        if Entity.world is None:
            return False
        inv = self.inventory
        use_corruptor = inv.consume_inventory_item(ITEM_ID, 1)
        if not use_corruptor:
            if not inv.consume_inventory_item("arrow", 1):
                return False
        self._play_world_sound(
            "random.bow", volume=1.0,
            pitch=1.0 / (random.random() * 0.4 + 0.8),
            position=tuple(self.pos),
        )
        if use_corruptor:
            arrow = CorruptorArrowEntity.from_shooter(Entity.world, self)
        else:
            arrow = ArrowEntity.from_shooter(Entity.world, self)
        Entity.world.spawn_entity(arrow)
        return True

    player_cls._use_bow = _use_bow


def initialize(game):
    _load_mod_settings()
    _register_item()
    _register_recipe()
    _get_item_texture()
    _patch_arrow_draw()
    _patch_gui_item_icons()
    _patch_held_item_draw()
    _patch_ground_item_billboard()
    try:
        from game.entities.player import Player
        _patch_bow(Player)
    except Exception:
        pass


def on_config_changed(game_or_config, config=None):
    if config is None and isinstance(game_or_config, dict):
        config = game_or_config
    if not isinstance(config, dict):
        return
    _apply_config_dict(config)
