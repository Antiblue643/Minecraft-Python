
import re

from game.multiplayer.commands import (
    CommandContext, CommandError, register_command, register_preprocessor,
)
from game.localization import translate_mod

from . import expressions

_PREPROCESSOR_KEY = "eval"
_VARIABLES: dict[str, dict] = {}
_CHR_CALL_RE = re.compile(r"\bchr\s*\(")
_CP437_GRAPHICS = (
    "\0☺☻♥♦♣♠•◘○◙♂♀♪♫☼"
    "►◄↕‼¶§▬↨↑↓→←∟↔▲▼"
)
_CP437_CHARACTERS = bytes(range(256)).decode("cp437")
_CP437_DISPLAY_CHARACTERS = (
    _CP437_GRAPHICS + _CP437_CHARACTERS[32:127] + "⌂" + _CP437_CHARACTERS[128:]
)


def _format_eval_result(value, raw_byte_string=False) -> str:
    if isinstance(value, str) and (
        raw_byte_string or any(
            ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in value
        )
    ):
        value = "".join(
            _CP437_DISPLAY_CHARACTERS[ord(char)] if ord(char) <= 0xFF else char
            for char in value
        )
    return expressions.format_value(value, True)


def eval_names(ctx: CommandContext) -> dict:
    pos = getattr(ctx.player, "pos", None) if ctx.sender_id <= 0 else None
    if pos is None:
        return {}
    try:
        return {"x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2])}
    except (TypeError, ValueError, IndexError):
        return {}


def sender_variables(ctx: CommandContext) -> dict:
    return _VARIABLES.setdefault(str(ctx.sender_name or "").casefold(), {})


def expand_command_text(body: str, ctx: CommandContext) -> str:
    if "eval(" not in body.lower():
        return body
    try:
        return expressions.expand_evals(body, eval_names(ctx), sender_variables(ctx))
    except expressions.ImportNotSupportedError as exc:
        raise CommandError(translate_mod("eval", "command.importsNotAllowed")) from exc
    except expressions.ExpressionError as exc:
        raise CommandError(translate_mod("eval", "command.evalError", str(exc))) from exc


def cmd_eval(ctx: CommandContext, args: list[str]) -> None:
    text = " ".join(args).strip()
    if not text:
        ctx.reply(translate_mod("eval", "command.usage"))
        return
    variables = sender_variables(ctx)
    keyword = text.lower()
    if keyword == "clear":
        variables.clear()
        ctx.reply(translate_mod("eval", "command.variablesCleared"))
        return
    if keyword == "vars":
        ctx.reply(
            ", ".join(f"{k}={expressions.format_value(v, True)}" for k, v in sorted(variables.items()))
            or translate_mod("eval", "command.noVariables")
        )
        return
    try:
        value = expressions.evaluate(text, eval_names(ctx), variables)
        ctx.reply(
            translate_mod("eval", "command.ok") if value is None
            else _format_eval_result(value, bool(_CHR_CALL_RE.search(text)))
        )
    except expressions.ImportNotSupportedError:
        ctx.reply(translate_mod("eval", "command.importsNotAllowed"))
    except expressions.ExpressionError as exc:
        ctx.reply(translate_mod("eval", "command.evalError", str(exc)))


def initialize(game):
    register_preprocessor(_PREPROCESSOR_KEY, expand_command_text)
    register_command("eval", cmd_eval)
