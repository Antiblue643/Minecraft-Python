from pathlib import Path

MOD_ID = "aod"
MOD_DIR = Path(__file__).resolve().parent
ITEM_ID = "corruptor_arrow"
FALLOUT_BLOCK_ID = 95
CORRUPTOR_TNT_BLOCK_ID = 96
KINOKO_BLOCK_ID = 97
PHOTON_LAMP_BLOCK_ID = 98
PHOTON_LAMP_LIT_BLOCK_ID = 99
TESLA_BLOCK_ID = 100
PHOTOBOMB_BLOCK_ID = 101
MAGNIFYING_GLASS_BLOCK_ID = 102
DEFAULT_RADIUS = 2.5
CORRUPTOR_TNT_RADIUS = 5.0
CORRUPTOR_TNT_VARIANT = "corruptor_tnt"
KINOKO_VARIANT = "kinoko"
PHOTON_LAMP_VARIANT = "photon_lamp"
PHOTON_LAMP_LIT_VARIANT = "photon_lamp_lit"
TESLA_VARIANT = "tesla"
PHOTOBOMB_VARIANT = "photobomb"
MAGNIFYING_GLASS_VARIANT = "magnifying_glass"
KINOKO_EXPLOSION_POWER = 32.0
CORRUPTION_EXCLUDED_BLOCKS = {"air", "portal", "nether_portal"}
ARROW_PURPLE_UV_OFFSET = 10
AOD_ATLAS_TILE_IDS = tuple(range(179, 192)) + (195, 196)

def map_aod_texture_index(index):
    try:
        value = int(index)
    except (TypeError, ValueError):
        return index
    if 0 <= value < len(AOD_ATLAS_TILE_IDS):
        return AOD_ATLAS_TILE_IDS[value]
    return value

def map_aod_texture_indices(value):
    if isinstance(value, dict):
        return {key: map_aod_texture_indices(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [map_aod_texture_indices(item) for item in value]
    return map_aod_texture_index(value)
