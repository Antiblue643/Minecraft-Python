import json

from ..common import MOD_DIR, MOD_ID, FALLOUT_BLOCK_ID, map_aod_texture_indices

def register_block():
    from game.block import Block
    from game.entities.item import Item

    Block._load_defs()
    data_path = MOD_DIR / "data" / "blocks.json"
    try:
        mod_block_defs = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        mod_block_defs = {}
    source = mod_block_defs.get("fallout", {})
    definition = dict(source) if isinstance(source, dict) else {}

    definition.update({
        "name": "Fallout",
        "mod_id": MOD_ID,
        "id": FALLOUT_BLOCK_ID,
        "dropTool": definition.get("dropTool", "shovel"),
    })
    definition["faces"] = map_aod_texture_indices(definition.get("faces", 6))
    particle_texture = definition.get("particleTexture")
    if particle_texture is None:
        faces = definition.get("faces", 66)
        particle_texture = (
            int(faces[0]) if isinstance(faces, (list, tuple)) and faces
            else int(faces) if not isinstance(faces, (list, tuple)) else 66
        )
    definition["particleTexture"] = map_aod_texture_indices(particle_texture)

    block_item = definition.get("blockitem")
    block_item = dict(block_item) if isinstance(block_item, dict) else {}
    block_item.setdefault("type", "block_item")
    block_item.setdefault("stack", 64)
    block_item.setdefault("id", 9100)
    definition["blockitem"] = block_item

    Block._block_defs["fallout"] = definition
    Item.block_defs = Block._block_defs
