
from functools import wraps

from ..common import CORRUPTOR_TNT_VARIANT

_PATCHED = False


def _is_tnt_target(block_type):
    return block_type in ("tnt", CORRUPTOR_TNT_VARIANT)


def patch_player_interactions():
    global _PATCHED
    if _PATCHED:
        return

    from game.entities.player import Player

    original_place_block = Player.place_block
    if not getattr(original_place_block, "_aod_tnt_interactions", False):
        @wraps(original_place_block)
        def place_block(self, *args, **kwargs):
            selected = self.inventory.get_active_item()
            if selected is not None and getattr(selected, "id", None) == "flint_and_steel":
                targeted_entity = self._raycast_attack_entity()
                hit = None
                if targeted_entity is None:
                    hit, _normal = self._raycast(include_intangible=True)
                if hit:
                    block_type = self.world.get_block_type(*hit)
                    if _is_tnt_target(block_type):
                        from game.entities.tnt import ignite_tnt

                        ignite_tnt(self.world, *hit)
                        self.world.remove_block(*hit, immediate_mesh=True)
                        if self.gamemode != 1 and selected.damage_item(1):
                            self.inventory.set_stack(self.inventory.active_idx, None)
                        self.swing_item()
                        record_event = getattr(self, "_record_performance_event", None)
                        if callable(record_event):
                            record_event(
                                "block.ignite",
                                block_type=block_type,
                                x=int(hit[0]), y=int(hit[1]), z=int(hit[2]),
                                method="flint_and_steel",
                            )
                        return True
            return original_place_block(self, *args, **kwargs)

        place_block._aod_tnt_interactions = True
        Player.place_block = place_block

    original_break_block = Player.break_block
    if not getattr(original_break_block, "_aod_tnt_interactions", False):
        @wraps(original_break_block)
        def break_block(self, target=None, *args, **kwargs):
            hit = target
            if hit is None:
                hit, _normal = self._raycast(include_intangible=True)
            if hit:
                block_type = self.world.get_block_type(*hit)
                if block_type == CORRUPTOR_TNT_VARIANT:
                    from game.entities.tnt import ignite_tnt

                    ignite_tnt(self.world, *hit)
            return original_break_block(self, target, *args, **kwargs)

        break_block._aod_tnt_interactions = True
        Player.break_block = break_block

    _PATCHED = True
