

def is_sleeping_player(player) -> bool:
    return bool(player is not None and getattr(player, "is_sleeping", False))


def should_render_player_held_item(player) -> bool:
    return player is not None and not is_sleeping_player(player)


def should_render_first_person_hand(
    player, *, third_person_view: bool = False, hide_gui: bool = False,
) -> bool:
    return bool(
        should_render_player_held_item(player)
        and not third_person_view
        and not hide_gui
    )
