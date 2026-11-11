

def sleep_cycle_players(world):
    players = []
    remotes = getattr(world, "_mp_remote_players", ()) or ()
    for player in (getattr(world, "player", None), *remotes):
        if player is not None and all(player is not existing for existing in players):
            players.append(player)
    return players


def update_all_players_sleeping_flag(world) -> bool:
    players = sleep_cycle_players(world)
    world._all_players_sleeping = bool(players) and all(
        bool(getattr(player, "is_sleeping", False)) for player in players
    )
    return world._all_players_sleeping


def wake_all_sleeping_players(world) -> None:
    woken = []
    for player in sleep_cycle_players(world):
        if getattr(player, "is_sleeping", False):
            wake = getattr(player, "wake_up_player", None)
            if callable(wake):
                wake(reset_timer=False, update_world_flag=False)
                woken.append(player)
    world._all_players_sleeping = False
    callback = getattr(world, "_network_sleep_cycle_callback", None)
    if callable(callback) and woken:
        try:
            callback(tuple(woken))
        except Exception:
            pass


def try_skip_night(world) -> bool:
    if not update_all_players_sleeping_flag(world):
        return False
    if bool(getattr(world, "_multiplayer_client_world", False)):
        return False
    is_daytime = getattr(world, "is_daytime", None)
    if callable(is_daytime) and is_daytime():
        return False
    players = sleep_cycle_players(world)
    if not players or not all(
        callable(getattr(player, "is_player_fully_asleep", None))
        and player.is_player_fully_asleep()
        for player in players
    ):
        return False

    current = int(getattr(world, "world_time", 0))
    new_time = current + 24000
    new_time -= new_time % 24000
    world.world_time = new_time
    updater = getattr(world, "_update_skylight_subtracted", None)
    if callable(updater):
        updater()
    publish_time = getattr(world, "_network_world_event_callback", None)
    if callable(publish_time):
        try:
            publish_time({"kind": "time", "world_time": new_time})
        except Exception:
            pass
    wake_all_sleeping_players(world)
    if callable(updater):
        updater()
    return True
