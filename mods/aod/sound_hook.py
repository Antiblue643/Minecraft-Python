
from .common import MOD_DIR

SOUND_NAME = "aod.tesla_spark"
SOUND_PATH = MOD_DIR / "sounds" / "tesla_spark.ogg"


def install_sound_hook():
    from engine.audio import SoundManager

    original_pool_paths = SoundManager.pool_paths
    if getattr(original_pool_paths, "_aod_sound_hook", False):
        return

    def pool_paths(manager, name):
        normalized = manager._normal_name(name)
        if normalized == SOUND_NAME:
            if normalized not in manager._pools:
                manager._pools[normalized] = (
                    (SOUND_PATH.resolve(),) if SOUND_PATH.is_file() else ()
                )
            return manager._pools[normalized]
        return original_pool_paths(manager, normalized)

    pool_paths._aod_sound_hook = True
    SoundManager.pool_paths = pool_paths
