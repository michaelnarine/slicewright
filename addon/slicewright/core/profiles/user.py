# SPDX-License-Identifier: GPL-3.0-or-later
"""User presets: diff, save, revert, embed, and secret stripping (03 sections 2.5 and 3.8).

A user preset file has the shape Orca uses, ``{"from": "User", "inherits": parent, "name": ...,
"version": ..., <only the keys that differ from the parent>}``, so files interchange. Secrets
(host credentials, access codes) are stripped on save, import and embed. No ``bpy`` here.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from .index import ProfileIndex
from .resolve import IDENTITY_KEYS, ResolvedPreset, Resolver, normalise_value, own_keys
from .source import ProfileError

log = logging.getLogger("slicewright.profiles")

KINDS = ("machine", "filament", "process")
EMBED_FORMAT = 1

# Conservative: anything that could carry a credential or a way into the user's printer.
SECRET_KEY_PATTERNS = (
    re.compile(r"^print_?host"),            # print_host, printhost_apikey, printhost_cafile, printhost_user, ...
    re.compile(r"password|passwd|passphrase"),
    re.compile(r"api_?key"),
    re.compile(r"access_?code"),
    re.compile(r"token|secret|credential"),
)


def is_secret_key(key: str) -> bool:
    low = key.lower()
    return any(p.search(low) for p in SECRET_KEY_PATTERNS)


def strip_secrets(config: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``config`` without secret keys."""
    return {k: v for k, v in config.items() if not is_secret_key(k)}


# -- diff and revert ---------------------------------------------------------------------------

def _norm(value: Any) -> Any:
    """Compare form: a one-element list equals the bare scalar."""
    value = normalise_value(value)
    if isinstance(value, list):
        return value[0] if len(value) == 1 else tuple(value)
    return value


def diff_config(parent: dict[str, Any], edited: dict[str, Any]) -> dict[str, Any]:
    """Keys of ``edited`` whose value differs from ``parent`` (identity keys ignored)."""
    out = {}
    for key, value in edited.items():
        if key in IDENTITY_KEYS or normalise_value(value) is None:
            continue
        if key not in parent or _norm(parent[key]) != _norm(value):
            out[key] = normalise_value(value)
    return out


def changed_rows(parent: dict[str, Any], edited: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """``(key, parent value or None, edited value)`` per difference, sorted by key, for the diff popup."""
    return [(k, parent.get(k), v) for k, v in sorted(diff_config(parent, edited).items())]


def revert_key(edited: dict[str, Any], parent: dict[str, Any], key: str) -> None:
    """Put ``key`` back to the parent's value; a key the parent lacks is removed."""
    if key in parent:
        edited[key] = parent[key]
    else:
        edited.pop(key, None)


def revert_all(edited: dict[str, Any], parent: dict[str, Any]) -> None:
    for key in list(diff_config(parent, edited)):
        revert_key(edited, parent, key)


def build_preset_json(kind: str, name: str, parent: str | None, diff: dict[str, Any],
                      version: str) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"unknown preset kind {kind!r}")
    data: dict[str, Any] = {"type": kind, "from": "User"}
    if parent:
        data["inherits"] = parent
    data["name"], data["version"] = name, version
    data.update(strip_secrets({k: v for k, v in diff.items() if k not in IDENTITY_KEYS}))
    return data


# -- the store ---------------------------------------------------------------------------------

_SAFE = re.compile(r"[A-Za-z0-9 ._@()+\-]")


def _encode(name: str) -> str:
    """Filename for a preset name: unsafe characters become ``%XX`` (UTF-8), so it is reversible."""
    out = "".join(c if _SAFE.fullmatch(c) else "".join(f"%{b:02X}" for b in c.encode()) for c in name)
    return re.sub(r"^\.", "%2E", out).rstrip(" .") or "%20"


def _decode(stem: str) -> str:
    return re.sub(r"(?:%[0-9A-F]{2})+",
                  lambda m: bytes.fromhex(m.group().replace("%", "")).decode("utf-8", "replace"), stem)


def check_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("a preset name cannot be empty")
    if "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("a preset name cannot contain path separators")
    return name.strip()


class UserStore:
    """``<root>/{machine,filament,process}/<name>.json``; ids are ``user:<name>``."""

    def __init__(self, root: str) -> None:
        self.root = root

    def _path(self, kind: str, name: str) -> str:
        if kind not in KINDS:
            raise ValueError(f"unknown preset kind {kind!r}")
        return os.path.join(self.root, kind, _encode(check_name(name)) + ".json")

    @staticmethod
    def id_of(name: str) -> str:
        return f"user:{name}"

    def exists(self, kind: str, name: str) -> bool:
        return os.path.isfile(self._path(kind, name))

    def list(self, kind: str) -> list[str]:
        folder = os.path.join(self.root, kind)
        if kind not in KINDS or not os.path.isdir(folder):
            return []
        names = []
        for fn in sorted(os.listdir(folder)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(folder, fn), encoding="utf-8") as fh:
                    data = json.load(fh)
                if not isinstance(data, dict):
                    raise ValueError("not a JSON object")
            except (OSError, ValueError) as exc:
                log.warning("skipping user preset %s: %s", fn, exc)
                continue
            names.append(str(data.get("name") or _decode(fn[:-5])))
        return names

    def load(self, kind: str, name: str) -> dict[str, Any]:
        try:
            with open(self._path(kind, name), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ProfileError(f"cannot load user preset {name!r}: {exc}") from exc
        if not isinstance(data, dict):
            raise ProfileError(f"user preset {name!r} is not a JSON object")
        return data

    def write(self, kind: str, name: str, data: dict[str, Any]) -> str:
        """Atomically write a finished preset object (secrets stripped, name set)."""
        path = self._path(kind, name)
        data = {**strip_secrets(data), "name": check_name(name)}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        part = path + ".part"
        with open(part, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(part, path)
        return path

    def save(self, kind: str, name: str, parent: str | None, diff: dict[str, Any],
             version: str = "1.0.0.0") -> str:
        return self.write(kind, name, build_preset_json(kind, check_name(name), parent, diff, version))

    def duplicate(self, kind: str, name: str, new_name: str) -> str:
        if self.exists(kind, new_name):
            raise ValueError(f"{new_name!r} already exists")
        return self.write(kind, new_name, self.load(kind, name))

    def rename(self, kind: str, name: str, new_name: str) -> str:
        if self.exists(kind, new_name) and _encode(new_name) != _encode(name):
            raise ValueError(f"{new_name!r} already exists")
        data = self.load(kind, name)
        old = self._path(kind, name)
        path = self.write(kind, new_name, data)
        if os.path.abspath(old) != os.path.abspath(path):
            os.remove(old)
        return path

    def delete(self, kind: str, name: str) -> bool:
        try:
            os.remove(self._path(kind, name))
            return True
        except FileNotFoundError:
            return False


# -- resolving ---------------------------------------------------------------------------------

def parent_id(index: ProfileIndex, kind: str, name: str) -> tuple[str | None, list[str]]:
    """The system preset called ``name``: first vendor in sorted order, plus the other matches."""
    hits = sorted((e for e in index.entries if e.kind == kind and e.name == name),
                  key=lambda e: e.vendor)
    if not hits:
        return None, []
    return hits[0].id, [e.id for e in hits[1:]]


def resolve_user_preset(resolver: Resolver, kind: str, store: UserStore, name: str) -> ResolvedPreset:
    """A user preset: its system parent resolved, then the file's own keys on top."""
    data = store.load(kind, name)
    parent_name = str(data.get("inherits") or "")
    warnings: list[str] = []
    if parent_name:
        pid, others = parent_id(resolver.index, kind, parent_name)
        if pid is None:
            raise ProfileError(f"user preset {name!r}: parent {parent_name!r} not found")
        if others:
            warnings.append(f"parent {parent_name!r} exists in several vendors; using {pid}")
        base = resolver.resolve(kind, pid)
        config, chain, includes = dict(base.config), list(base.chain), list(base.includes)
        warnings = base.warnings + warnings
    else:
        config, chain, includes = dict(resolver._defaults[kind]), [], []
    config.update(own_keys(data))
    return ResolvedPreset(store.id_of(name), kind, name, config, [store.id_of(name)] + chain,
                          includes, warnings)


# -- embedding in the .blend -------------------------------------------------------------------

def embed_payload(printer: dict | None, process: dict | None,
                  filaments: list[dict]) -> dict[str, Any]:
    """Each argument: ``{"kind", "id", "name", "inherits", "config": flattened full config}``."""
    def one(item: dict | None):
        if not item:
            return None
        return {"kind": item["kind"], "id": item["id"], "name": item["name"],
                "inherits": item.get("inherits", ""), "config": strip_secrets(item["config"])}
    return {"format": EMBED_FORMAT, "printer": one(printer), "process": one(process),
            "filaments": [one(f) for f in filaments if f]}


def dumps_embedded(printer: dict | None, process: dict | None, filaments: list[dict]) -> str:
    return json.dumps(embed_payload(printer, process, filaments), separators=(",", ":"), sort_keys=True)


def loads_embedded(text: str) -> dict[str, Any]:
    """The embedded payload, or ``{}`` for empty, corrupt or newer-format text (logged)."""
    if not text:
        return {}
    try:
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("format") != EMBED_FORMAT:
            raise ValueError("unsupported embedded preset format")
    except ValueError as exc:
        log.warning("ignoring embedded presets: %s", exc)
        return {}
    for item in [data.get("printer"), data.get("process"), *(data.get("filaments") or [])]:
        if isinstance(item, dict):
            item["config"] = strip_secrets(item.get("config") or {})
    return data


def embedded_fallback(embedded: dict[str, Any], kind: str, preset_id: str) -> dict[str, Any] | None:
    """The embedded flattened config for ``preset_id`` when the preset itself is missing."""
    items = [embedded.get("printer"), embedded.get("process"), *(embedded.get("filaments") or [])]
    for item in items:
        if isinstance(item, dict) and item.get("kind") == kind and item.get("id") == preset_id:
            return strip_secrets(item.get("config") or {})
    return None
