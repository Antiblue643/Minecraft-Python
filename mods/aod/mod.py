
SETTINGS = ()

def initialize(game):
    from .blocks.materials import register_materials
    from .blocks.fallout import register_block as register_fallout
    from .systems.fallout_gravity import initialize as initialize_fallout_gravity
    from .blocks.corruptor_tnt import initialize as initialize_corruptor_tnt
    from .blocks.kinoko import initialize as initialize_kinoko
    from .blocks.photobomb import initialize as initialize_photobomb
    from .blocks.magnifying_glass import initialize as initialize_magnifying_glass
    from .blocks.photon_lamp import initialize as initialize_photon_lamp
    from .blocks.tesla import initialize as initialize_tesla
    from .entities.zapper import install_entity_hooks as install_zapper_entity_hooks
    from .items.corruptor_arrow import register_item, patch_bow
    from .recipes import register_recipes
    from .sound_hook import install_sound_hook
    from .rendering.atlas import initialize as initialize_atlas
    from .rendering.item_icons import get_item_texture, initialize as initialize_item_rendering
    from .rendering.arrows import initialize as initialize_arrow_rendering

    register_materials()
    initialize_atlas()
    register_fallout()
    initialize_fallout_gravity()
    initialize_corruptor_tnt()
    initialize_kinoko()
    initialize_photobomb()
    initialize_magnifying_glass(game)
    initialize_photon_lamp(game)
    initialize_tesla(game)
    install_zapper_entity_hooks()
    install_sound_hook()
    register_item()
    register_recipes()
    get_item_texture()
    initialize_arrow_rendering()
    initialize_item_rendering()

    try:
        from game.entities.player import Player
        patch_bow(Player)
    except Exception:
        pass

def on_config_changed(game_or_config, config=None):
    return None
