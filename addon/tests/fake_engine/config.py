# SPDX-License-Identifier: GPL-3.0-or-later
"""compose_config, normalize_config and eval_condition for the fake (04 section 6, 03 section 9.2).

Composition is deliberately naive: defaults, then printer, process and project
values, with the per-slot filament values concatenated into vectors.
"""
from __future__ import annotations

import re
from typing import Any

from .errors import ConfigError
from .schema_data import LEGACY_KEYS, META_KEYS, build_schema

SCHEMA = build_schema()

_VECTOR_ELEM = {"floats": "float", "ints": "int", "strings": "string", "points": "point"}


# ---------------------------------------------------------------------------
# value formats (04 section 2.5)
# ---------------------------------------------------------------------------

def fmt_float(x: float) -> str:
    return f"{x:.10g}"


def _parse_float(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"not a number: {text!r}") from None


def _parse_int(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"not an integer: {text!r}") from None


def split_vector(vtype: str, text: str) -> list[str]:
    """Split a serialized vector into element strings (no quotes)."""
    text = text.strip()
    if text == "":
        return []
    if vtype == "strings":
        return [p.strip().strip('"') for p in text.split(";")]
    return [p.strip() for p in text.split(",")]


def join_vector(vtype: str, elems: list[str]) -> str:
    if vtype == "strings":
        return ";".join(f'"{e}"' for e in elems)
    return ",".join(elems)


def canon_scalar(entry: dict, base: str, text: str) -> str:
    """Canonical serialized form of one element; raises ValueError if unparsable."""
    if base == "float":
        return fmt_float(_parse_float(text))
    if base == "int":
        return str(_parse_int(text))
    if base == "percent":
        t = text.strip()
        return fmt_float(_parse_float(t[:-1] if t.endswith("%") else t)) + "%"
    if base == "bool":
        t = text.strip().lower()
        if t in ("0", "false"):
            return "0"
        if t in ("1", "true"):
            return "1"
        raise ValueError(f"not a boolean: {text!r}")
    if base == "enum":
        allowed = [e["value"] for e in entry["enum"] or []]
        if text not in allowed:
            raise ValueError(f"{text!r} is not one of {allowed}")
        return text
    if base == "point":
        parts = text.lower().split("x")
        if len(parts) != 2:
            raise ValueError(f"not a point: {text!r}")
        return f"{fmt_float(_parse_float(parts[0]))}x{fmt_float(_parse_float(parts[1]))}"
    return text  # string


def canon(key: str, text: str) -> str:
    """Canonicalize a serialized value for ``key``; raises ValueError if unparsable."""
    entry = SCHEMA[key]
    t = entry["type"]
    if t in _VECTOR_ELEM:
        base = _VECTOR_ELEM[t]
        return join_vector(t, [canon_scalar(entry, base, e) for e in split_vector(t, text)])
    return canon_scalar(entry, t, text)


def range_errors(key: str, canonical: str) -> str | None:
    entry = SCHEMA[key]
    t = entry["type"]
    lo, hi = entry["min"], entry["max"]
    if lo is None and hi is None:
        return None
    if t in _VECTOR_ELEM:
        elems = split_vector(t, canonical)
    else:
        elems = [canonical]
    for e in elems:
        if e == "" or t in ("string", "strings", "enum", "bool"):
            continue
        v = float(e[:-1] if e.endswith("%") else e)
        if lo is not None and v < lo:
            return f"{key}: {fmt_float(v)} is below the minimum {fmt_float(lo)}"
        if hi is not None and v > hi:
            return f"{key}: {fmt_float(v)} is above the maximum {fmt_float(hi)}"
    return None


def defaults() -> dict[str, str]:
    return {k: canon(k, v["default"]) for k, v in SCHEMA.items()}


def vector_len(flat: dict[str, str], key: str) -> int:
    return len(split_vector(SCHEMA[key]["type"], flat[key]))


# ---------------------------------------------------------------------------
# compose_config
# ---------------------------------------------------------------------------

def _check_preset(preset: Any, what: str) -> None:
    if not isinstance(preset, dict):
        raise TypeError(f"{what} must be a dict, got {type(preset).__name__}")
    for k, v in preset.items():
        if not isinstance(k, str):
            raise TypeError(f"{what}: keys must be str")
        if isinstance(v, str):
            continue
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            continue
        raise TypeError(f"{what}[{k!r}] must be str or list[str], got {type(v).__name__}")
    if "name" not in preset:
        raise ConfigError(f"{what} has no 'name'", key="name")


def _elements(key: str, value: str | list[str]) -> list[str]:
    """A preset value as a list of element strings for a vector key."""
    vtype = SCHEMA[key]["type"]
    if isinstance(value, list):
        return list(value)
    return split_vector(vtype, value)


def _scalar_text(key: str, value: str | list[str]) -> str:
    if isinstance(value, str):
        return value
    if SCHEMA[key]["type"] == "points":
        return ",".join(value)
    if len(value) != 1:
        raise ConfigError(f"{key} is a scalar option but got a list of {len(value)}", key=key)
    return value[0]


def _apply(flat: dict[str, str], preset: dict, *, only_preset: str | None) -> None:
    for k, v in preset.items():
        if k in META_KEYS or k not in SCHEMA:
            continue
        if only_preset is not None and SCHEMA[k]["preset"] != only_preset:
            continue
        vtype = SCHEMA[k]["type"]
        try:
            if vtype in _VECTOR_ELEM:
                flat[k] = canon(k, join_vector(vtype, _elements(k, v)) if isinstance(v, list)
                                else v)
            else:
                flat[k] = canon(k, _scalar_text(k, v))
        except ValueError as exc:
            raise ConfigError(str(exc), key=k, value=str(v)) from None


def compose_config(printer: dict, process: dict, filaments: list[dict],
                   project: dict[str, str] | None = None) -> dict[str, str]:
    _check_preset(printer, "printer")
    _check_preset(process, "process")
    if not isinstance(filaments, list):
        raise TypeError("filaments must be a list")
    for i, f in enumerate(filaments):
        _check_preset(f, f"filaments[{i}]")
    if project is not None:
        if not isinstance(project, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in project.items()):
            raise TypeError("project must be dict[str, str]")

    flat = defaults()
    _apply(flat, printer, only_preset=None)
    _apply(flat, process, only_preset=None)

    # Per-slot filament vectors are concatenated in slot order.
    slots = filaments or [{"name": "default"}]
    for key, entry in SCHEMA.items():
        if entry["preset"] != "filament":
            continue
        vtype = entry["type"]
        elems: list[str] = []
        for slot in slots:
            if key in slot:
                try:
                    slot_elems = [canon_scalar(entry, _VECTOR_ELEM[vtype], e)
                                  for e in _elements(key, slot[key])]
                except ValueError as exc:
                    raise ConfigError(str(exc), key=key, value=str(slot[key])) from None
                elems.append(slot_elems[0] if slot_elems else "")
            else:
                elems.append(split_vector(vtype, canon(key, entry["default"]))[0])
        flat[key] = join_vector(vtype, elems)

    for k, v in (project or {}).items():
        if k not in SCHEMA:
            continue
        try:
            flat[k] = canon(k, v)
        except ValueError as exc:
            raise ConfigError(str(exc), key=k, value=v) from None
    return flat


# ---------------------------------------------------------------------------
# normalize_config
# ---------------------------------------------------------------------------

def normalize_config(flat: dict[str, str]) -> dict:
    if not isinstance(flat, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in flat.items()):
        raise TypeError("flat config must be dict[str, str]")
    config = defaults()
    errors: dict[str, str] = {}
    substitutions: list[dict] = []
    for key, value in flat.items():
        key = LEGACY_KEYS.get(key, key)
        if key not in SCHEMA:
            continue
        try:
            canonical = canon(key, value)
        except ValueError as exc:
            errors[key] = str(exc)
            continue  # keep the default
        config[key] = canonical
    for key, value in config.items():
        if key in errors:
            continue
        msg = range_errors(key, value)
        if msg:
            errors[key] = msg
    return {"config": config, "substitutions": substitutions, "errors": errors}


# ---------------------------------------------------------------------------
# eval_condition: ==, !=, =~, <, >, <=, >=, and/or/not (also &&, ||, !), indexing
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"""
    \s*(?:
      (?P<num>\d+(?:\.\d+)?(?:[eE][-+]?\d+)?) |
      (?P<str>"(?:[^"\\]|\\.)*") |
      (?P<re>/(?:[^/\\]|\\.)*/) |
      (?P<op>==|!=|=~|<=|>=|&&|\|\||[<>!()\[\]]) |
      (?P<id>[A-Za-z_][A-Za-z_0-9]*)
    )""", re.VERBOSE)


def _tokenize(expr: str) -> list[tuple[str, str]]:
    pos, out = 0, []
    expr = expr.rstrip()
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if not m or m.end() == pos:
            raise ConfigError(f"cannot parse condition at {expr[pos:pos + 10]!r}")
        pos = m.end()
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
    return out


class _Parser:
    def __init__(self, tokens, config):
        self.t, self.i, self.cfg = tokens, 0, config

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self, want=None):
        tok = self.peek()
        if tok[0] is None or (want is not None and tok[1] != want):
            raise ConfigError(f"expected {want!r}" if want else "unexpected end of condition")
        self.i += 1
        return tok

    def parse(self):
        v = self.or_()
        if self.i != len(self.t):
            raise ConfigError(f"unexpected {self.peek()[1]!r}")
        return v

    def or_(self):
        v = self.and_()
        while self.peek()[1] in ("or", "||"):
            self.i += 1
            r = self.and_()
            v = bool(v) or bool(r)
        return v

    def and_(self):
        v = self.not_()
        while self.peek()[1] in ("and", "&&"):
            self.i += 1
            r = self.not_()
            v = bool(v) and bool(r)
        return v

    def not_(self):
        if self.peek()[1] in ("not", "!"):
            self.i += 1
            return not bool(self.not_())
        return self.cmp()

    def cmp(self):
        left = self.atom()
        op = self.peek()[1]
        if op in ("==", "!=", "<", ">", "<=", ">=", "=~"):
            self.i += 1
            right = self.atom()
            return self._compare(op, left, right)
        return left

    @staticmethod
    def _num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    def _compare(self, op, a, b):
        if op == "=~":
            if not isinstance(b, re.Pattern):
                raise ConfigError("right side of =~ must be a /regex/")
            return b.fullmatch(str(a)) is not None
        na, nb = self._num(a), self._num(b)
        if na is not None and nb is not None and not isinstance(a, bool):
            a, b = na, nb
        else:
            a, b = str(a), str(b)
        return {"==": a == b, "!=": a != b, "<": a < b, ">": a > b,
                "<=": a <= b, ">=": a >= b}[op]

    def atom(self):
        kind, text = self.take()
        if text == "(":
            v = self.or_()
            self.take(")")
            return v
        if kind == "num":
            return float(text)
        if kind == "str":
            return bytes(text[1:-1], "utf-8").decode("unicode_escape")
        if kind == "re":
            try:
                return re.compile(text[1:-1])
            except re.error as exc:
                raise ConfigError(f"bad regex: {exc}") from None
        if kind == "id":
            if text in ("true", "false"):
                return text == "true"
            if text not in self.cfg:
                raise ConfigError(f"unknown option {text!r}")
            value = self.cfg[text]
            if self.peek()[1] == "[":
                self.i += 1
                idx = self.take()
                self.take("]")
                if idx[0] != "num":
                    raise ConfigError("index must be a number")
                elems = value if isinstance(value, list) else [value]
                try:
                    return elems[int(float(idx[1]))]
                except IndexError:
                    raise ConfigError(f"{text}[{idx[1]}] is out of range") from None
            if isinstance(value, list):
                raise ConfigError(f"{text} is a vector; index it")
            return value
        raise ConfigError(f"unexpected {text!r}")


def eval_condition(expr: str, config: dict) -> bool:
    if not isinstance(expr, str):
        raise TypeError("expr must be str")
    cfg = {}
    for k, v in config.items():
        cfg[k] = [str(x) for x in v] if isinstance(v, list) else str(v)
    result = _Parser(_tokenize(expr), cfg).parse()
    if isinstance(result, (bool, int)):
        return bool(result)
    raise ConfigError("condition does not evaluate to a boolean")


class ConditionContext:
    def __init__(self, config: dict) -> None:
        self._config = {k: ([str(x) for x in v] if isinstance(v, list) else str(v))
                        for k, v in config.items()}

    def eval(self, expr: str) -> bool:
        return eval_condition(expr, self._config)
