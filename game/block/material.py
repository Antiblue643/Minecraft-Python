from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class StepSound:
    name: str
    volume: float = 1.0
    pitch: float = 1.0


@dataclass(frozen=True)
class Material:
    name: str
    step_sound: StepSound
    blocks_grass: bool = True
    is_liquid: bool = False
    is_solid: bool = True

    @property
    def can_block_grass(self) -> bool:
        return self.blocks_grass


STEP_SOUNDS: Mapping[str, StepSound] = {
    name: StepSound(name)
    for name in (
        "stone", "wood", "gravel", "grass", "metal", "glass",
        "cloth", "sand", "powder",
    )
}


def _sound(name: str) -> StepSound:
    return STEP_SOUNDS[name]


ROCK = Material("rock", _sound("stone"))
WOOD = Material("wood", _sound("wood"))
GRASS = Material("grass", _sound("grass"))
GROUND = Material("ground", _sound("gravel"))
GRAVEL = Material("gravel", _sound("gravel"))
SAND = Material("sand", _sound("sand"))
METAL = Material("metal", StepSound("metal", 1.0, 1.5))
GLASS = Material("glass", _sound("glass"), blocks_grass=False)
CLOTH = Material("cloth", _sound("cloth"))
POWDER = Material("powder", _sound("powder"))
PLANT = Material("plant", _sound("grass"), blocks_grass=False, is_solid=False)
CACTUS = Material("plant", _sound("cloth"), blocks_grass=False, is_solid=False)
CIRCUITS = Material("circuits", _sound("stone"), blocks_grass=False, is_solid=False)
LEAVES = Material("leaves", _sound("grass"), blocks_grass=False)
WATER = Material("water", _sound("powder"), is_liquid=True, is_solid=False)
LAVA = Material("lava", _sound("powder"), is_liquid=True, is_solid=False)
AIR = Material("air", _sound("powder"), blocks_grass=False, is_solid=False)
SNOW = Material("snow", _sound("cloth"), blocks_grass=False, is_solid=False)
ICE = Material("ice", _sound("glass"))
PORTAL = Material("portal", _sound("powder"), blocks_grass=False, is_solid=False)

BLOCK_MATERIALS: Mapping[str, Material] = {
    "grass_block": GRASS,
    "dirt": GROUND,
    "stone": ROCK,
    "sandstone": ROCK,
    "cobblestone": ROCK,
    "planks": WOOD,
    "stairs": WOOD,
    "sign": WOOD,
    "wall_sign": WOOD,
    "cobblestone_stairs": ROCK,
    "wooden_door": WOOD,
    "sapling": PLANT,
    "cactus": CACTUS,
    "clay_block": GROUND,
    "reed": PLANT,
    "bedrock": ROCK,
    "water": WATER,
    "lava": LAVA,
    "sand": SAND,
    "gravel": GRAVEL,
    "gold_ore": ROCK,
    "iron_ore": ROCK,
    "coal_ore": ROCK,
    "lapis_ore": ROCK,
    "lapis_block": ROCK,
    "dispenser": ROCK,
    "log": WOOD,
    "leaves": LEAVES,
    "sponge": CLOTH,
    "glass": GLASS,
    "wool": CLOTH,
    "cake": CLOTH,
    "yellow_flower": PLANT,
    "red_flower": PLANT,
    "brown_mushroom": PLANT,
    "red_mushroom": PLANT,
    "gold_block": METAL,
    "iron_block": METAL,
    "double_stone_slab": ROCK,
    "stone_slab": ROCK,
    "bricks": ROCK,
    "tnt": WOOD,
    "bookshelf": WOOD,
    "mossy_cobblestone": ROCK,
    "obsidian": ROCK,
    "torch": WOOD,
    "fire": WOOD,
    "mob_spawner": ROCK,
    "diamond_ore": ROCK,
    "redstone_ore": ROCK,
    "redstone_ore_lit": ROCK,
    "diamond_block": METAL,
    "crafting_table": WOOD,
    "crops": PLANT,
    "farmland": GROUND,
    "furnace": ROCK,
    "furnace_lit": ROCK,
    "chest": WOOD,
    "jukebox": WOOD,
    "note_block": WOOD,
    "fence": WOOD,
    "ladder": WOOD,
    "rail": METAL,
    "redstone_wire": CIRCUITS,
    "lever": CIRCUITS,
    "stone_button": CIRCUITS,
    "stone_pressure_plate": CIRCUITS,
    "wooden_pressure_plate": CIRCUITS,
    "redstone_torch": CIRCUITS,
    "unlit_redstone_torch": CIRCUITS,
    "iron_door": METAL,
    "snow": SNOW,
    "snow_block": SNOW,
    "ice": ICE,
    "pumpkin": WOOD,
    "jack_o_lantern": WOOD,
    "netherrack": ROCK,
    "soul_sand": SAND,
    "glowstone": GLASS,
    "portal": PORTAL,
}


_MATERIAL_BLOCK_DEFS: dict | None = None


def _block_definitions_for_materials() -> dict:
    global _MATERIAL_BLOCK_DEFS
    if _MATERIAL_BLOCK_DEFS is None:
        from game.content_data import load_block_definitions
        _MATERIAL_BLOCK_DEFS = load_block_definitions()
    return _MATERIAL_BLOCK_DEFS


def material_for_block(block_type: str | None, definition: dict | None = None) -> Material:
    if block_type is None or block_type == "air":
        return AIR
    if block_type == "cactus":
        return CACTUS

    if definition is None and block_type:
        try:
            definition = _block_definitions_for_materials().get(block_type)
        except Exception:
            definition = None

    explicit_name = (definition or {}).get("material")
    if explicit_name:
        material = MATERIALS.get(str(explicit_name).lower())
        if material is not None:
            return material

    return BLOCK_MATERIALS.get(block_type, POWDER)


MATERIALS: Mapping[str, Material] = {
    material.name: material
    for material in (
        ROCK, WOOD, GRASS, GROUND, GRAVEL, SAND, METAL, GLASS, CLOTH,
        POWDER, PLANT, CIRCUITS, LEAVES, WATER, LAVA, AIR, SNOW, ICE, PORTAL,
    )
}


_BLOCK_STEP_SOUND_OVERRIDES: Mapping[str, StepSound] = {
    "lapis_ore": _sound("stone"),
    "lapis_block": _sound("stone"),
    "jukebox": _sound("stone"),
    "lever": _sound("wood"),
    "wooden_pressure_plate": _sound("wood"),
    "redstone_torch": _sound("wood"),
    "unlit_redstone_torch": _sound("wood"),
    "stone_pressure_plate": _sound("stone"),
    "stone_button": _sound("stone"),
    "redstone_wire": _sound("stone"),
    "redstone_repeater": _sound("wood"),
    "redstone_repeater_on": _sound("wood"),
    "stone_button": _sound("stone"),
    "iron_door": StepSound("metal", 1.0, 1.5),
}


def step_sound_for_block(block_type: str | None, definition: dict | None = None) -> StepSound:
    if block_type in _BLOCK_STEP_SOUND_OVERRIDES:
        return _BLOCK_STEP_SOUND_OVERRIDES[block_type]
    return material_for_block(block_type, definition).step_sound
