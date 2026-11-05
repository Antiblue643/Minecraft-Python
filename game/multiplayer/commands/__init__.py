
from __future__ import annotations

from typing import Callable, Optional


def _tr(key: str, *args, default=None) -> str:
    from game.localization import translate
    return translate(f"mcpy.command.{key}", *args, default=default)


def _reply(ctx, key: str, *args, default=None) -> None:
    ctx.reply(_tr(key, *args, default=default))


class CommandError(Exception):
    pass


class CommandContext:

    __slots__ = ("game", "player", "world", "sender_name", "reply", "session", "sender_id", "is_operator", "is_host")

    def __init__(
        self, game, player, world, sender_name: str, reply: Callable[[str], None],
        *, session=None, sender_id: int = 0, is_operator: bool = True, is_host: bool = True,
    ):
        self.game = game
        self.player = player
        self.world = world
        self.sender_name = sender_name
        self.reply = reply
        self.session = session if session is not None else getattr(game, "mp_session", None)
        self.sender_id = int(sender_id or 0)
        self.is_operator = bool(is_operator)
        self.is_host = bool(is_host)


def _give_item(ctx: CommandContext, item_id: str, count: int = 1) -> None:
    from game.entities.item import Item, ItemStack
    from game.content_ids import item_name_from_classic_id

    if isinstance(item_id, int) or (isinstance(item_id, str) and item_id.isdigit()):
        resolved = item_name_from_classic_id(int(item_id))
        if resolved is None:
            _reply(ctx, "unknownItemId", item_id)
            return
        item_id = resolved

    if not Item.exists(item_id):
        _reply(ctx, "unknownItem", item_id)
        return

    limit = Item.max_stack_size(item_id)
    count = max(1, min(limit * 4, int(count)))  
    inv = ctx.player.inventory
    remaining = count
    for i in range(getattr(inv, "MAIN_SIZE", 36)):
        if remaining <= 0:
            break
        stack = inv.get_stack(i)
        if stack is None:
            put = min(remaining, limit)
            inv.set_stack(i, ItemStack(item_id, put))
            remaining -= put
        elif getattr(stack, "id", None) == item_id and stack.count < limit:
            space = limit - stack.count
            add = min(space, remaining)
            stack.count += add
            remaining -= add
    given = count - remaining
    if given > 0:
        _reply(ctx, "givenItems", given, item_id)
    if remaining > 0:
        _reply(ctx, "inventoryFull", remaining)


def cmd_give(ctx: CommandContext, args: list[str]) -> None:
    if not _require_operator(ctx):
        return
    if not _require_local_player(ctx):
        return
    if not args:
        _reply(ctx, "giveUsage")
        return
    token = args[0].lower()
    count = 1
    if len(args) >= 2:
        try:
            count = int(args[1])
        except ValueError:
            _reply(ctx, "invalidCount")
            return
    _give_item(ctx, token, count)


def cmd_list(ctx: CommandContext, args: list[str]) -> None:
    name = ctx.sender_name or "Player"
    session = getattr(ctx.game, "mp_session", None)
    connected_names = None
    if session is not None and getattr(session, "active", False):
        get_names = getattr(session, "connected_player_names", None)
        if callable(get_names):
            try:
                connected_names = get_names()
            except Exception:
                connected_names = None
    if not connected_names:
        connected_names = [name]
    count = len(connected_names)
    _reply(ctx, "connectedPlayers", count, ", ".join(connected_names))


def cmd_kill(ctx: CommandContext, args: list[str]) -> None:
    if ctx.sender_id > 0:
        session = ctx.session
        kill = getattr(session, "kill_player", None)
        if getattr(session, "mode", None) != "host" or not callable(kill) or not kill(ctx.sender_id):
            _reply(ctx, "couldNotKillYou")
        return
    player = ctx.player
    if player is None:
        _reply(ctx, "noPlayerToKill")
        return
    from game.multiplayer.protocol import KILL_DAMAGE
    player.damage_player(
        KILL_DAMAGE, source="kill", bypass_armor=True,
        force=True, bypass_gamemode=True,
    )


_TP_USAGE_DEFAULT = (
    "Usage: /tp <x> <y> <z> | /tp <player> | /tp <player> <player> | "
    "/tp <player> <x> <y> <z>"
)


def _tp_usage():
    return _tr("tpUsage", default=_TP_USAGE_DEFAULT)
_TP_LIMIT = (2**31)-1


def _same_name(a: str, b: str) -> bool:
    return str(a or "").strip().casefold() == str(b or "").strip().casefold()


def _tp_sender_is_local(ctx: CommandContext) -> bool:
    return ctx.sender_id <= 0 and ctx.player is not None


def _tp_find_player(ctx: CommandContext, name: str):
    if _tp_sender_is_local(ctx) and _same_name(name, ctx.sender_name):
        return ctx.sender_name, tuple(float(v) for v in ctx.player.pos[:3])
    session = ctx.session
    if session is not None and getattr(session, "mode", None) == "host":
        return session.resolve_player(name)
    return None


def _tp_move(ctx: CommandContext, name: str, position) -> bool:
    if _tp_sender_is_local(ctx) and _same_name(name, ctx.sender_name):
        player = ctx.player
        if hasattr(player, "set_position"):
            player.set_position(*position)
        else:
            player.pos[0], player.pos[1], player.pos[2] = position
            if hasattr(player, "prev_pos"):
                player.prev_pos = list(player.pos)
        return True
    session = ctx.session
    if session is not None and getattr(session, "mode", None) == "host":
        return bool(session.teleport_named_player(name, position))
    return False


def _tp_parse_coords(tokens: list[str], base) -> Optional[tuple[float, float, float]]:
    values = []
    for axis, token in enumerate(tokens):
        try:
            if token.startswith("~"):
                if base is None:
                    return None
                value = float(base[axis]) + (float(token[1:]) if len(token) > 1 else 0.0)
            else:
                value = float(token)
        except ValueError:
            return None
        if value != value or abs(value) > _TP_LIMIT:
            return None
        values.append(value)
    return values[0], values[1], values[2]


def cmd_tp(ctx: CommandContext, args: list[str]) -> None:
    if not _require_operator(ctx):
        return
    count = len(args)
    if count not in (1, 2, 3, 4):
        ctx.reply(_tp_usage())
        return

    mover_name: Optional[str] = None
    mover_pos = None
    destination = None
    destination_label = ""

    if count == 3 or count == 1:
        if _tp_sender_is_local(ctx):
            mover_name, mover_pos = ctx.sender_name, tuple(float(v) for v in ctx.player.pos[:3])
        elif ctx.sender_id > 0:
            found = _tp_find_player(ctx, ctx.sender_name)
            if found is not None:
                mover_name, mover_pos = found
        if mover_name is None:
            _reply(ctx, "tpNamePlayer")
            return
        rest = args
    else:
        found = _tp_find_player(ctx, args[0])
        if found is None:
            _reply(ctx, "playerNotConnected", args[0])
            return
        mover_name, mover_pos = found
        rest = args[1:]

    if len(rest) == 1:
        target = _tp_find_player(ctx, rest[0])
        if target is None:
            _reply(ctx, "playerNotConnected", rest[0])
            return
        destination = target[1]
        destination_label = target[0]
    else:
        destination = _tp_parse_coords(rest, mover_pos)
        if destination is None:
            ctx.reply(_tp_usage())
            return
        destination_label = f"{destination[0]:.1f} {destination[1]:.1f} {destination[2]:.1f}"

    if not _tp_move(ctx, mover_name, destination):
        _reply(ctx, "couldNotTeleport", mover_name)
        return
    _reply(ctx, "teleported", mover_name, destination_label)


def cmd_save_all(ctx: CommandContext, args: list[str]) -> None:
    if not _require_operator(ctx):
        return
    game = ctx.game
    save = getattr(game, "save_world", None) or getattr(game, "_save_world", None)
    if callable(save):
        save()
        _reply(ctx, "worldSaved")
    else:
        world = ctx.world
        if world is not None and hasattr(world, "save"):
            world.save()
            _reply(ctx, "worldSaved")
        else:
            _reply(ctx, "saveNotAvailable")


def cmd_stop(ctx: CommandContext, args: list[str]) -> None:
    if not ctx.is_host:
        _reply(ctx, "onlyHostStopServer")
        return
    _reply(ctx, "stopping")
    ctx.game.running = False


def cmd_help(ctx: CommandContext, args: list[str]) -> None:
    names = sorted(COMMANDS.keys())
    _reply(ctx, "commands", ", ".join("/" + n for n in names))


def _require_operator(ctx: CommandContext) -> bool:
    if ctx.is_operator:
        return True
    _reply(ctx, "noPermission")
    return False


def _require_local_player(ctx: CommandContext) -> bool:
    if ctx.sender_id <= 0:
        return True
    _reply(ctx, "hostPlayerOnly")
    return False


def cmd_msg(ctx: CommandContext, args: list[str]) -> None:
    if len(args) < 2:
        _reply(ctx, "msgUsage")
        return
    target = args[0]
    msg = " ".join(args[1:]).strip()
    session = ctx.session
    if session is not None and getattr(session, "mode", None) == "host":
        if session.send_private_message(ctx.sender_name, target, msg, sender_id=ctx.sender_id):
            return
        _reply(ctx, "playerNotConnected", target)
        return
    from game.localization import translate
    ctx.reply(translate(
        "mcpy.chat.privateMessageSent", target, msg
    ))


def cmd_banlist(ctx: CommandContext, args: list[str]) -> None:
    if not _require_operator(ctx):
        return
    session = ctx.session
    config = getattr(session, "server_config", None) if session is not None else None
    bans = config.ban_names() if config is not None else sorted(getattr(ctx.game, "_ban_list", set()) or set())
    if not bans:
        _reply(ctx, "banListEmpty")
    else:
        _reply(ctx, "bannedList", ", ".join(bans))


def cmd_ban(ctx: CommandContext, args: list[str]) -> None:
    if len(args) < 1:
        _reply(ctx, "banUsage")
        return
    if not _require_operator(ctx):
        return
    session = ctx.session
    if session is None or getattr(session, "mode", None) != "host":
        _reply(ctx, "onlyHostManageBans")
        return
    name = args[0]
    reason = " ".join(args[1:]).strip() or "Banned by host"
    if session.ban_player(name, reason):
        _reply(ctx, "bannedPlayer", name)
    else:
        _reply(ctx, "couldNotBanPlayer", name)


def cmd_pardon(ctx: CommandContext, args: list[str]) -> None:
    if not args:
        _reply(ctx, "pardonUsage")
        return
    if not _require_operator(ctx):
        return
    session = ctx.session
    if session is None or getattr(session, "mode", None) != "host":
        _reply(ctx, "onlyHostManageBans")
        return
    name = args[0]
    if session.pardon_player(name):
        _reply(ctx, "pardonedPlayer", name)
    else:
        _reply(ctx, "playerNotBanned", name)


def cmd_op(ctx: CommandContext, args: list[str]) -> None:
    if not args:
        _reply(ctx, "opUsage")
        return
    if not ctx.is_host:
        _reply(ctx, "onlyHostGrantOperator")
        return
    session = ctx.session
    if session is None or getattr(session, "mode", None) != "host":
        _reply(ctx, "operatorRequiresHostWorld")
        return
    if session.set_operator(args[0], True):
        _reply(ctx, "oppedPlayer", args[0])
    else:
        _reply(ctx, "couldNotOpPlayer", args[0])


def cmd_deop(ctx: CommandContext, args: list[str]) -> None:
    if not args:
        _reply(ctx, "deopUsage")
        return
    if not ctx.is_host:
        _reply(ctx, "onlyHostRevokeOperator")
        return
    session = ctx.session
    if session is None or getattr(session, "mode", None) != "host":
        _reply(ctx, "operatorRequiresHostWorld")
        return
    if session.set_operator(args[0], False):
        _reply(ctx, "deoppedPlayer", args[0])
    else:
        _reply(ctx, "playerNotOperator", args[0])


def cmd_ipban(ctx: CommandContext, args: list[str]) -> None:
    if not _require_operator(ctx):
        return
    session = getattr(ctx.game, "mp_session", None)
    if session is None or getattr(session, "mode", None) != "host":
        _reply(ctx, "onlyHostManageIpBans")
        return
    if not args:
        _reply(ctx, "ipbanUsage")
        return
    reason = " ".join(args[1:]).strip() or "IP banned by host"
    network = session.ip_ban(args[0], reason)
    if network is None:
        _reply(ctx, "couldNotIpBan", args[0])
    else:
        _reply(ctx, "ipBanned", network, reason)


def cmd_pardon_ip(ctx: CommandContext, args: list[str]) -> None:
    if not _require_operator(ctx):
        return
    session = getattr(ctx.game, "mp_session", None)
    if session is None or getattr(session, "mode", None) != "host":
        _reply(ctx, "onlyHostManageIpBans")
        return
    if not args:
        _reply(ctx, "pardonIpUsage")
        return
    if session.pardon_ip(args[0]):
        _reply(ctx, "removedIpBan", args[0])
    else:
        _reply(ctx, "noMatchingIpBan", args[0])


def cmd_whitelist(ctx: CommandContext, args: list[str]) -> None:
    if not _require_operator(ctx):
        return
    session = getattr(ctx.game, "mp_session", None)
    config = getattr(session, "server_config", None) if session is not None else None
    if session is None or getattr(session, "mode", None) != "host" or config is None:
        _reply(ctx, "whitelistRequiresHost")
        return
    action = args[0].lower() if args else "status"
    if action in ("on", "enable", "enabled"):
        session.set_whitelist_enabled(True)
        _reply(ctx, "whitelistEnabled")
    elif action in ("off", "disable", "disabled"):
        session.set_whitelist_enabled(False)
        _reply(ctx, "whitelistDisabled")
    elif action == "add":
        if len(args) < 2:
            _reply(ctx, "whitelistAddUsage")
        elif session.whitelist_player(args[1], True):
            _reply(ctx, "whitelistAdded", args[1])
        else:
            _reply(ctx, "couldNotWhitelist", args[1])
    elif action in ("remove", "del", "delete"):
        if len(args) < 2:
            _reply(ctx, "whitelistRemoveUsage")
        elif session.whitelist_player(args[1], False):
            _reply(ctx, "whitelistRemoved", args[1])
        else:
            _reply(ctx, "notOnWhitelist", args[1])
    elif action == "list":
        names = config.whitelist_names()
        empty_label = _tr("whitelistEmpty")
        _reply(ctx, "whitelistList", len(names), ", ".join(names) if names else empty_label)
    elif action == "status":
        _reply(
            ctx, "whitelistStatus",
            _tr("whitelistEnabledWord") if config.whitelist_enabled else _tr("whitelistDisabledWord"),
        )
    else:
        _reply(ctx, "whitelistUsage")


def cmd_kick(ctx: CommandContext, args: list[str]) -> None:
    if not args:
        _reply(ctx, "kickUsage")
        return
    session = getattr(ctx.game, "mp_session", None)
    if session is None or getattr(session, "mode", None) != "host":
        _reply(ctx, "onlyLanHostKick")
        return
    if not _require_operator(ctx):
        return
    target = args[0]
    reason = " ".join(args[1:]).strip() or "Kicked by host"
    kick = getattr(session, "kick_player", None)
    if callable(kick) and kick(target, reason):
        _reply(ctx, "kickedPlayer", target)
    else:
        _reply(ctx, "playerNotConnected", target)


def cmd_save_on(ctx: CommandContext, args: list[str]) -> None:
    if not _require_operator(ctx):
        return
    ctx.game._autosave_enabled = True
    _reply(ctx, "savingEnabled")


def cmd_save_off(ctx: CommandContext, args: list[str]) -> None:
    if not _require_operator(ctx):
        return
    ctx.game._autosave_enabled = False
    _reply(ctx, "savingDisabled")


COMMANDS: dict[str, Callable[[CommandContext, list[str]], None]] = {
    "give": cmd_give,
    "list": cmd_list,
    "kill": cmd_kill,
    "tp": cmd_tp,
    "save-all": cmd_save_all,
    "save-on": cmd_save_on,
    "save-off": cmd_save_off,
    "stop": cmd_stop,
    "help": cmd_help,
    "msg": cmd_msg,
    "w": cmd_msg,
    "tell": cmd_msg,
    "banlist": cmd_banlist,
    "ban": cmd_ban,
    "pardon": cmd_pardon,
    "op": cmd_op,
    "deop": cmd_deop,
    "kick": cmd_kick,
    "ipban": cmd_ipban,
    "ban-ip": cmd_ipban,
    "pardon-ip": cmd_pardon_ip,
    "unipban": cmd_pardon_ip,
    "whitelist": cmd_whitelist,
}

_PREPROCESSORS: dict[str, Callable[[str, CommandContext], str]] = {}


def register_command(name: str, handler: Callable[[CommandContext, list[str]], None]) -> None:
    key = str(name).strip().lstrip("/").lower()
    if not key or any(ch.isspace() for ch in key):
        raise ValueError(f"Invalid command name: {name!r}")
    if not callable(handler):
        raise TypeError("command handler must be callable")
    COMMANDS[key] = handler


def unregister_command(name: str) -> None:
    COMMANDS.pop(str(name).strip().lstrip("/").lower(), None)


def register_preprocessor(key: str, func: Callable[[str, CommandContext], str]) -> None:
    if not callable(func):
        raise TypeError("preprocessor must be callable")
    _PREPROCESSORS[str(key)] = func


def unregister_preprocessor(key: str) -> None:
    _PREPROCESSORS.pop(str(key), None)


def dispatch(text: str, ctx: CommandContext) -> bool:
    text = text.strip()
    if not text.startswith("/"):
        return False
    body = text[1:].strip()
    if not body:
        return True
    for preprocess in tuple(_PREPROCESSORS.values()):
        try:
            body = preprocess(body, ctx)
        except CommandError as exc:
            ctx.reply(str(exc))
            return True
        except Exception as exc:
            _reply(ctx, "commandError", str(exc))
            return True
    parts = body.split()
    if not parts:
        return True
    name = parts[0].lower()
    args = parts[1:]
    handler = COMMANDS.get(name)
    if handler is None:
        _reply(ctx, "unknownCommand")
        return True
    try:
        handler(ctx, args)
    except Exception as exc:
        _reply(ctx, "commandError")
    return True
