
from __future__ import annotations

import ast
import collections
import math
import operator
import re
from typing import Any, MutableMapping, Mapping, Optional

MAX_EXPR_LEN = 512
MAX_STEPS = 20000
MAX_RESULT_STR = 256
MAX_SEQUENCE = 256
MAX_POW_BITS = 4096  
MAX_VARIABLES = 64
MAX_BLOCK_DEPTH = 8
_EVAL_TOKEN = "eval("
_BLOCK_RE = re.compile(r"(for|while|if|elif|else)\b")
_RESERVED = frozenset({"x", "y", "z", "vars", "clear", "eval", "end"})


class ExpressionError(ValueError):
    pass


class ImportNotSupportedError(ExpressionError):
    pass


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_}
_CMP_OPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
    ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_FUNCTIONS = {
    "abs": abs, "min": min, "max": max, "round": round, "sum": sum,
    "int": int, "float": float, "str": str, "len": len,
    "sqrt": math.sqrt, "floor": math.floor, "ceil": math.ceil,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log2": math.log2, "log10": math.log10,
    "radians": math.radians, "degrees": math.degrees,
    "pow": None,    
    "range": None,  
    "chr": chr, "ord": ord, "ascii": ascii, "hex": hex, "bool": bool,
    "oct": oct, "bin": bin
}
_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "true": True, "false": False}
_STR_METHODS = {
    "upper", "lower", "title", "capitalize", "strip", "lstrip", "rstrip",
    "replace", "zfill", "swapcase"
}
_SEQUENCES = (str, list, tuple)


def _limit(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value.bit_length() > MAX_POW_BITS:
        raise ExpressionError("number too large")
    if isinstance(value, str) and len(value) > MAX_RESULT_STR:
        raise ExpressionError("string too long")
    if isinstance(value, (list, tuple)) and len(value) > MAX_SEQUENCE:
        raise ExpressionError("list too long")
    return value


def _safe_pow(base, exp):
    if isinstance(base, int) and isinstance(exp, int) and exp > 0:
        bits = max(1, abs(base).bit_length()) * exp
        if bits > MAX_POW_BITS:
            raise ExpressionError("exponent too large")
    elif isinstance(exp, (int, float)) and abs(exp) > 1e6:
        raise ExpressionError("exponent too large")
    try:
        return base ** exp
    except (OverflowError, ZeroDivisionError) as exc:
        raise ExpressionError(str(exc) or "math error") from exc


def _find_top_level(text: str, target: str, start: int = 0) -> int:
    depth = 0
    quote = ""
    i = start
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == target and depth == 0:
            return i
        i += 1
    return -1


def _split_top_level(text: str, sep: str) -> list[str]:
    parts = []
    start = 0
    while True:
        index = _find_top_level(text, sep, start)
        if index < 0:
            parts.append(text[start:])
            return parts
        parts.append(text[start:index])
        start = index + 1


def _structure(source: str) -> str:
    queue = collections.deque(part.strip() for part in _split_top_level(source, ";"))
    lines = []
    depth = 0
    while queue:
        segment = queue.popleft()
        if not segment:
            continue
        if segment == "end":
            if depth == 0:
                raise ExpressionError("'end' without an open block")
            depth -= 1
            continue
        match = _BLOCK_RE.match(segment)
        if match:
            colon = _find_top_level(segment, ":")
            if colon < 0:
                raise ExpressionError("syntax error")
            if match.group(1) in ("elif", "else"):
                if depth == 0:
                    raise ExpressionError("syntax error")
                depth -= 1
            lines.append("    " * depth + segment[:colon + 1])
            depth += 1
            if depth > MAX_BLOCK_DEPTH:
                raise ExpressionError("blocks nested too deeply")
            rest = segment[colon + 1:].strip()
            if rest:
                queue.appendleft(rest)
            continue
        lines.append("    " * depth + segment)
    return "\n".join(lines)


class _Evaluator:
    def __init__(self, names: Mapping[str, Any], variables: Optional[Mapping[str, Any]]):
        self.names = {k.lower(): v for k, v in names.items()}
        self.vars = dict(variables or {})
        self.steps = 0

    def run(self, source: str):
        if len(source) > MAX_EXPR_LEN:
            raise ExpressionError("expression too long")
        try:
            tree = ast.parse(_structure(source), mode="exec")
        except SyntaxError as exc:
            raise ExpressionError("syntax error") from exc
        if any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree)):
            raise ImportNotSupportedError("imports are not supported")
        result = None
        try:
            for statement in tree.body:
                result = self.execute(statement)
        except (_Break, _Continue):
            raise ExpressionError("'break'/'continue' outside a loop") from None
        return result

    def tick(self):
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise ExpressionError("expression too complex")

    def block(self, statements):
        for statement in statements:
            self.execute(statement)

    def execute(self, node):
        self.tick()
        if isinstance(node, ast.Expr):
            return self.visit(node.value)
        if isinstance(node, ast.Assign):
            value = self.visit(node.value)
            for target in node.targets:
                self.assign(target, value)
            return None
        if isinstance(node, ast.AugAssign):
            if not isinstance(node.target, ast.Name):
                raise ExpressionError("unsupported assignment target")
            key = self.writable(node.target.id)
            if key not in self.vars:
                raise ExpressionError(f"unknown name '{node.target.id}'")
            self.vars[key] = self.binary(node.op, self.vars[key], self.visit(node.value))
            return None
        if isinstance(node, ast.If):
            self.block(node.body if self.visit(node.test) else node.orelse)
            return None
        if isinstance(node, ast.While):
            if node.orelse:
                raise ExpressionError("unsupported statement")
            while self.visit(node.test):
                self.tick()
                try:
                    self.block(node.body)
                except _Break:
                    break
                except _Continue:
                    continue
            return None
        if isinstance(node, ast.For):
            if node.orelse:
                raise ExpressionError("unsupported statement")
            iterable = self.visit(node.iter)
            if not isinstance(iterable, (range, str, list, tuple)):
                raise ExpressionError("can only loop over range, text or lists")
            for item in iterable:
                self.tick()
                self.assign(node.target, item)
                try:
                    self.block(node.body)
                except _Break:
                    break
                except _Continue:
                    continue
            return None
        if isinstance(node, ast.Pass):
            return None
        if isinstance(node, ast.Break):
            raise _Break()
        if isinstance(node, ast.Continue):
            raise _Continue()
        raise ExpressionError("unsupported statement")

    def writable(self, name: str) -> str:
        key = name.lower()
        if key in _RESERVED or key in _CONSTANTS or key in _FUNCTIONS or key in self.names:
            raise ExpressionError(f"'{name}' is read-only")
        return key

    def assign(self, target, value):
        if isinstance(target, ast.Name):
            self.vars[self.writable(target.id)] = _limit(value)
            return
        if isinstance(target, ast.Tuple) and all(isinstance(t, ast.Name) for t in target.elts):
            if not isinstance(value, _SEQUENCES) or len(value) != len(target.elts):
                raise ExpressionError("cannot unpack that value")
            for sub_target, item in zip(target.elts, value):
                self.assign(sub_target, item)
            return
        raise ExpressionError("unsupported assignment target")

    def binary(self, op_node, left, right):
        if isinstance(op_node, ast.Pow):
            return _limit(_safe_pow(left, right))
        op = _BIN_OPS.get(type(op_node))
        if op is None:
            raise ExpressionError("unsupported operator")
        left_seq, right_seq = isinstance(left, _SEQUENCES), isinstance(right, _SEQUENCES)
        if isinstance(op_node, ast.Mult) and left_seq != right_seq:
            sequence, reps = (left, right) if left_seq else (right, left)
            cap = MAX_RESULT_STR if isinstance(sequence, str) else MAX_SEQUENCE
            if not isinstance(reps, int) or len(sequence) * max(reps, 0) > cap:
                raise ExpressionError("result too long")
        try:
            return _limit(op(left, right))
        except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
            raise ExpressionError(str(exc) or "math error") from exc

    def visit(self, node):
        self.tick()

        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, str, bool)):
                return node.value
            raise ExpressionError("unsupported literal")

        if isinstance(node, ast.Name):
            key = node.id.lower()
            if key in self.vars:
                return self.vars[key]
            if key in self.names:
                return self.names[key]
            if key in _CONSTANTS:
                return _CONSTANTS[key]
            raise ExpressionError(f"unknown name '{node.id}'")

        if isinstance(node, (ast.Tuple, ast.List)):
            items = [self.visit(element) for element in node.elts]
            if len(items) > MAX_SEQUENCE:
                raise ExpressionError("list too long")
            return tuple(items) if isinstance(node, ast.Tuple) else items

        if isinstance(node, ast.BinOp):
            return self.binary(node.op, self.visit(node.left), self.visit(node.right))

        if isinstance(node, ast.UnaryOp):
            op = _UNARY_OPS.get(type(node.op))
            if op is None:
                raise ExpressionError("unsupported operator")
            try:
                return op(self.visit(node.operand))
            except TypeError as exc:
                raise ExpressionError(str(exc)) from exc

        if isinstance(node, ast.BoolOp):
            values = [self.visit(v) for v in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)

        if isinstance(node, ast.Compare):
            left = self.visit(node.left)
            for op_node, comparator in zip(node.ops, node.comparators):
                op = _CMP_OPS.get(type(op_node))
                if op is None:
                    raise ExpressionError("unsupported comparison")
                right = self.visit(comparator)
                try:
                    if not op(left, right):
                        return False
                except TypeError as exc:
                    raise ExpressionError(str(exc)) from exc
                left = right
            return True

        if isinstance(node, ast.IfExp):
            return self.visit(node.body if self.visit(node.test) else node.orelse)

        if isinstance(node, ast.Subscript):
            target = self.visit(node.value)
            if not isinstance(target, _SEQUENCES):
                raise ExpressionError("only text and lists can be indexed")
            index = node.slice
            try:
                if isinstance(index, ast.Slice):
                    parts = [None if p is None else self.visit(p) for p in (index.lower, index.upper, index.step)]
                    return target[slice(*parts)]
                return target[self.visit(index)]
            except (TypeError, IndexError, ValueError) as exc:
                raise ExpressionError(str(exc)) from exc

        if isinstance(node, ast.Call):
            return self._call(node)

        raise ExpressionError("unsupported expression")

    def _call(self, node: ast.Call):
        if node.keywords:
            raise ExpressionError("keyword arguments are not supported")
        args = [self.visit(a) for a in node.args]
        func = node.func
        try:
            if isinstance(func, ast.Name):
                key = func.id.lower()
                if key == "pow":
                    if len(args) != 2:
                        raise ExpressionError("pow takes two arguments")
                    return _limit(_safe_pow(*args))
                if key == "range":
                    if not 1 <= len(args) <= 3 or not all(
                        isinstance(a, int) and not isinstance(a, bool) for a in args
                    ):
                        raise ExpressionError("range takes one to three integers")
                    if len(args) == 3 and args[2] == 0:
                        raise ExpressionError("range step cannot be zero")
                    return range(*args)
                fn = _FUNCTIONS.get(key)
                if fn is None:
                    raise ExpressionError(f"unknown function '{func.id}'")
                return _limit(fn(*args))
            if isinstance(func, ast.Attribute):
                target = self.visit(func.value)
                if isinstance(target, str) and func.attr in _STR_METHODS:
                    return _limit(getattr(target, func.attr)(*args))
                raise ExpressionError(f"unsupported method '{func.attr}'")
        except ExpressionError:
            raise
        except (TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
            raise ExpressionError(str(exc) or "math error") from exc
        raise ExpressionError("unsupported call")


def evaluate(
    source: str,
    names: Optional[Mapping[str, Any]] = None,
    variables: Optional[MutableMapping[str, Any]] = None,
):
    evaluator = _Evaluator(names or {}, variables)
    result = evaluator.run(source)
    if variables is not None:
        if len(evaluator.vars) > MAX_VARIABLES:
            raise ExpressionError("too many variables")
        variables.clear()
        variables.update(evaluator.vars)
    return result


def format_value(value, display: bool = False) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value != value or value in (math.inf, -math.inf):
            raise ExpressionError("result is not a finite number")
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return repr(value)
    if isinstance(value, (list, tuple)):
        parts = [format_value(item, display) for item in value]
        if not display:
            return " ".join(parts)  
        inner = ", ".join(parts)
        return f"[{inner}]" if isinstance(value, list) else f"({inner})"
    return str(value)


def _find_matching_paren(text: str, open_index: int) -> int:
    depth = 0
    quote = ""
    i = open_index
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def expand_evals(
    text: str,
    names: Optional[Mapping[str, Any]] = None,
    variables: Optional[MutableMapping[str, Any]] = None,
) -> str:
    out = []
    pos = 0
    lowered = text.lower()
    while True:
        start = lowered.find(_EVAL_TOKEN, pos)
        if start < 0:
            out.append(text[pos:])
            break
        open_index = start + len(_EVAL_TOKEN) - 1
        close = _find_matching_paren(text, open_index)
        if close < 0:
            raise ExpressionError("missing ')' in eval(...)")
        out.append(text[pos:start])
        value = evaluate(text[open_index + 1:close], names, variables)
        if value is None:
            raise ExpressionError("eval(...) produced no value")
        out.append(format_value(value))
        pos = close + 1
    return "".join(out)
