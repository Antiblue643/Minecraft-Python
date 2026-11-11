
from functools import wraps

from ..common import (
    CORRUPTOR_TNT_VARIANT, KINOKO_VARIANT, PHOTOBOMB_VARIANT,
)

_PATCHED = False


def patch_player_breaking():
    global _PATCHED
    if _PATCHED:
        return

    from game.entities.player import Player

    original_break_block = Player.break_block
    if not getattr(original_break_block, "_aod_tnt_breaking", False):
        @wraps(original_break_block)
        def break_block(self, target=None, *args, **kwargs):
            hit = target
            if hit is None:
                hit, _normal = self._raycast(include_intangible=True)
            if hit and self.world is not None:
                block_type = self.world.get_block_type(*hit)
                if block_type in (
                    CORRUPTOR_TNT_VARIANT, KINOKO_VARIANT, PHOTOBOMB_VARIANT,
                ):
                    from game.entities.tnt import ignite_tnt
                    ignite_tnt(self.world, *hit)
            return original_break_block(self, target, *args, **kwargs)

        break_block._aod_tnt_breaking = True
        Player.break_block = break_block

    _PATCHED = True
