from __future__ import annotations


def mushroom_can_stay(light_level: int, support_is_opaque: bool, *, light_known=True) -> bool:
    if not support_is_opaque:
        return False
    return (not light_known) or int(light_level) <= 13
