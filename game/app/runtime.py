
from engine.display import Display
from engine.models import Model
from engine.raster import Renderer
from engine.textures import Texture
from game.app.paths import PROJECT_ROOT
from game.content_data import load_item_definitions
from game.app.preload import preload_texture_resources
from game.block import Block
from game.entities.entity import Entity
from game.entities.item import Item

screen = Display()
try:
    from game.app.loading_screen import draw_mojang_loading_screen
    draw_mojang_loading_screen(screen)
except Exception as exc:  
    print(f"Warning: could not draw the startup loading screen: {exc}")
rndr = Renderer()
Model.renderer = rndr
Entity.renderer = rndr
rndr.display = screen

Block._load_defs()
Item.block_defs = Block._block_defs
Item.block_texture = Block._get_texture()
Item.item_defs = load_item_definitions()
Item.item_texture = Texture("game/textures/gui/items.png")

preload_texture_resources()
