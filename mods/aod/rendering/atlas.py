from ..common import MOD_DIR, AOD_ATLAS_TILE_IDS

_AOD_ATLAS_TEXTURE = None
_AOD_ATLAS_PACK = None
_ORIGINAL_BLOCK_GET_TEXTURE = None

def _compose_aod_terrain_surface(pack_name=None):
    import pygame as pg
    from game.app.texturepacks import get_active_texture_pack, resolve_texture_path

    pack_name = get_active_texture_pack() if pack_name is None else str(pack_name)
    base_path = resolve_texture_path("game/textures/terrain.png", pack_name=pack_name)
    mod_path = MOD_DIR / "blocks.png"
    if not mod_path.is_file():
        raise FileNotFoundError(f"Missing AOD block atlas: {mod_path}")

    base = pg.image.load(str(base_path)).convert_alpha()
    mod_atlas = pg.image.load(str(mod_path)).convert_alpha()
    if base.get_size() != (256, 256):
        raise ValueError(
            f"Expected a 256x256 terrain atlas, got {base.get_size()}"
        )
    if mod_atlas.get_size() != (96, 96):
        raise ValueError(
            f"Expected a 96x96 AOD block atlas, got {mod_atlas.get_size()}"
        )

    for source_tile, target_tile in enumerate(AOD_ATLAS_TILE_IDS):
        source_x = (source_tile % 6) * 16
        source_y = (source_tile // 6) * 16
        target_x = (target_tile % 16) * 16
        target_y = (target_tile // 16) * 16
        destination = pg.Rect(target_x, target_y, 16, 16)
        base.fill((0, 0, 0, 0), destination)
        base.blit(
            mod_atlas,
            (target_x, target_y),
            pg.Rect(source_x, source_y, 16, 16),
        )
    return base

def get_aod_terrain_texture(block_cls):
    global _AOD_ATLAS_TEXTURE, _AOD_ATLAS_PACK, _ORIGINAL_BLOCK_GET_TEXTURE
    from game.app.texturepacks import get_active_texture_pack

    pack_name = get_active_texture_pack()
    if _AOD_ATLAS_TEXTURE is not None and _AOD_ATLAS_PACK == pack_name:
        return _AOD_ATLAS_TEXTURE

    try:
        from engine.textures import Texture
        from OpenGL.GL import GL_CLAMP, glDeleteTextures
        surface = _compose_aod_terrain_surface(pack_name)
        texture = Texture(surface, repeat_mode=GL_CLAMP)
    except Exception:
        if _ORIGINAL_BLOCK_GET_TEXTURE is not None:
            return _ORIGINAL_BLOCK_GET_TEXTURE()
        return block_cls._shared_texture

    old_texture = _AOD_ATLAS_TEXTURE
    _AOD_ATLAS_TEXTURE = texture
    _AOD_ATLAS_PACK = pack_name
    if old_texture is not None and getattr(old_texture, "texture_id", None):
        try:
            glDeleteTextures([int(old_texture.texture_id)])
        except Exception:
            pass

    try:
        from game.entities.item import Item
        Item.block_texture = texture
    except Exception:
        pass
    return texture

def initialize():
    global _ORIGINAL_BLOCK_GET_TEXTURE
    from game.block import Block

    accessor = Block._get_texture
    if getattr(getattr(accessor, "__func__", accessor), "_aod_atlas_patched", False):
        return
    _ORIGINAL_BLOCK_GET_TEXTURE = Block._get_texture

    def _get_texture(cls):
        return get_aod_terrain_texture(cls)

    _get_texture._aod_atlas_patched = True
    Block._get_texture = classmethod(_get_texture)
    Block._shared_texture = None
    try:
        from game.entities.item import Item
        Item.block_texture = Block._get_texture()
    except Exception:
        pass
