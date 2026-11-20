# SPDX-License-Identifier: GPL-3.0-or-later
"""Keeping secrets out of text (03 section 8.5). No ``bpy``.

Three tools, used together:

* :class:`SecretStr`: a wrapper whose ``str()``, ``repr()`` and ``format()`` are ``***``; only ``reveal()`` gives the
  value back. Code that carries a secret around holds one of these.
* :func:`register` / :func:`redact`: the secrets the add-on has read from, or written to, a store are remembered in
  memory for the session, and :func:`redact` replaces any occurrence of them in a text with ``***``. On top of that,
  patterns catch the usual shapes of a credential in text we did not expect to hold one: ``X-Api-Key: ...``,
  ``access_code=...``, ``password: ...``, ``Authorization: Bearer ...``, ``https://user:pass@host``.
* :class:`RedactingFilter`: a ``logging`` filter (attached to every handler ``core.logs`` installs) that applies
  :func:`redact` to the message, its arguments, the traceback and the stack of every record.

This is a net under the real rule, which is not to put a secret in a message in the first place.
"""
from __future__ import annotations

import logging
import re
import traceback

MASK = "***"
MIN_LENGTH = 4                  # shorter strings are not remembered: masking "1" everywhere would wreck every message

_known: set[str] = set()

_KEY = r"(?:access[_ -]?code|api[_ -]?key|x-api-key|password|passwd|passphrase|secret|token|authorization|auth)"
_PATTERNS = [
    # key: value / key=value / "key": "value" / 'key': 'value'  (the value runs to a quote, whitespace, & , ; or })
    (re.compile(rf"(?i)((?:\b|_|-){_KEY}[\"']?\s*[:=]\s*[\"']?)(?:Bearer\s+|Basic\s+)?([^\s\"'&,;}}]+)"), rf"\1{MASK}"),
    (re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}"), rf"\1 {MASK}"),
    (re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"), MASK),                 # user:password@host
]


class SecretStr:
    """A secret that does not print. ``reveal()`` is the one way to get the text."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = str(value)

    def reveal(self) -> str:
        return self._value

    def __str__(self) -> str:
        return MASK

    def __repr__(self) -> str:
        return f"SecretStr('{MASK}')"

    def __format__(self, spec: str) -> str:
        return format(MASK, spec)

    def __bool__(self) -> bool:
        return bool(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SecretStr) and other._value == self._value

    def __hash__(self) -> int:
        return hash(("SecretStr", self._value))

    def __reduce__(self):
        raise TypeError("a SecretStr cannot be pickled")

    def __copy__(self) -> "SecretStr":
        return self

    def __deepcopy__(self, memo) -> "SecretStr":
        return self


def register(secret: str | SecretStr | None) -> None:
    """Remember ``secret`` so :func:`redact` masks it from now on (ignored when shorter than ``MIN_LENGTH``)."""
    text = secret.reveal() if isinstance(secret, SecretStr) else secret
    if text and len(text) >= MIN_LENGTH:
        _known.add(text)


def forget(secret: str | SecretStr | None) -> None:
    text = secret.reveal() if isinstance(secret, SecretStr) else secret
    _known.discard(text)


def clear() -> None:
    _known.clear()


def known_count() -> int:
    return len(_known)


def redact(text: object) -> str:
    """``text`` with every remembered secret and every credential-shaped value replaced by ``***``."""
    out = str(text)
    known = sorted(_known, key=len, reverse=True)
    for secret in known:
        if secret in out:
            out = out.replace(secret, MASK)
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


class RedactingFilter(logging.Filter):
    """Scrubs a record's message, traceback and stack. Always lets the record through."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = None
            if record.exc_info and not record.exc_text:
                record.exc_text = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
        except Exception:  # noqa: BLE001 - a logging filter must never raise
            record.msg, record.args, record.exc_info, record.exc_text = "(log message withheld)", None, None, None
        return True
