# SPDX-License-Identifier: GPL-3.0-or-later
"""The on-disk slice result cache (03 section 6.4). No ``bpy``: plain Python and numpy.

One directory per slice key under a root (``<user cache>/slices``)::

    <key>/manifest.json            written last; its presence is what makes an entry valid
          out.gcode                the engine's G-code (``SliceResult.write_gcode``)
          moves.<field>.npy        one array per moves field
          layers.<field>.npy       one array per layers field
          gcode_line_ends.npy
          stats.json  warnings.json

An entry is built in a staging directory (``.tmp-<key>-<n>``) and published with one directory rename, so a
reader never sees a half-written entry: a crash or a cancelled write leaves only staging litter, which the next
eviction sweeps. Every file inside is itself written to a ``.part`` name and renamed, so even the staging
directory only ever holds whole files. :meth:`ResultCache.store` is a generator that finishes one array per
step, for the tick runner (``core/ticking.py``), so the UI does not hitch; :meth:`Entry.load_steps` is the
mirror for reading.

Eviction is LRU on the manifest's modification time (a hit touches it) against a byte cap; the entry just
stored or hit is protected, so one entry larger than the cap is still kept. Anything that is not a valid
entry under the root (a key-named directory without a manifest, old staging directories) is removed by the
same sweep. A key must look like a hash (hex), which also keeps it a safe path component.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import numpy as np

log = logging.getLogger("slicewright.cache")

FORMAT = 1
MB = 1024 * 1024
DEFAULT_CAP_MB = 2048
KEY_RE = re.compile(r"^[0-9a-f]{8,64}$")
MANIFEST = "manifest.json"
GCODE_FILE = "out.gcode"
STATS_FILE = "stats.json"
WARNINGS_FILE = "warnings.json"
STAGING_PREFIX = ".tmp-"
STAGING_MAX_AGE_S = 3600.0          # staging older than this is abandoned (a crash), not a write in progress


class CacheError(Exception):
    """An entry could not be written or read."""


def valid_key(key: str) -> bool:
    return isinstance(key, str) and KEY_RE.match(key) is not None


def _json_default(value: Any):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def _write_atomic(path: str, write: Callable[[Any], None]) -> int:
    """Write through ``write(file)`` to ``path + '.part'``, rename into place, return the size."""
    part = path + ".part"
    try:
        with open(part, "wb") as f:
            write(f)
        size = os.path.getsize(part)
        os.replace(part, path)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return size


def _rmtree(path: str) -> None:
    """Remove an entry directory; the manifest goes first so an interrupted removal leaves an invalid entry."""
    try:
        os.remove(os.path.join(path, MANIFEST))
    except OSError:
        pass
    shutil.rmtree(path, ignore_errors=True)


class CachedResult:
    """What a cache hit hands the preview: the fields of a ``SliceResult`` that the add-on reads, from disk.

    The arrays are read-only like the engine's. There is no ``write_gcode_3mf``: that needs the live result
    (the engine renders the plate metadata), so export of a 3MF after a cache hit slices again."""
    from_cache = True

    def __init__(self, *, key: str, gcode_path: str, moves: dict, layers: dict, gcode_line_ends, stats: dict,
                 warnings: list, objects: list, wipe_tower) -> None:
        self.key = key
        self.gcode_path = gcode_path
        self.moves = moves
        self.layers = layers
        self.gcode_line_ends = gcode_line_ends
        self.stats = stats
        self.warnings = warnings
        self.objects = objects
        self.wipe_tower = wipe_tower

    def write_gcode(self, path: str) -> None:
        shutil.copyfile(self.gcode_path, path)


@dataclass
class Entry:
    """A valid entry on disk (its manifest parsed, its files present at the recorded sizes)."""
    key: str
    path: str
    manifest: dict
    warnings: list

    @property
    def bytes(self) -> int:
        return int(self.manifest.get("bytes", 0))

    @property
    def gcode_path(self) -> str:
        return os.path.join(self.path, GCODE_FILE)

    def load_steps(self) -> Iterator:
        """Read the entry one array per step (a generator for the tick runner); returns a :class:`CachedResult`.
        Raises :class:`CacheError` if a file is unreadable or does not match the manifest."""
        arrays = self.manifest["arrays"]
        loaded: dict[str, np.ndarray] = {}
        total = len(arrays)
        for i, (name, meta) in enumerate(arrays.items()):
            try:
                arr = np.load(os.path.join(self.path, meta["file"]), allow_pickle=False)
            except (OSError, ValueError) as exc:
                raise CacheError(f"{name}: {exc}") from exc
            if str(arr.dtype) != meta["dtype"] or list(arr.shape) != list(meta["shape"]):
                raise CacheError(f"{name}: dtype or shape differs from the manifest")
            arr.flags.writeable = False
            loaded[name] = arr
            yield (i + 1) / (total + 1), f"Loading {name}"
        try:
            with open(os.path.join(self.path, STATS_FILE), encoding="utf-8") as f:
                stats = json.load(f)
        except (OSError, ValueError) as exc:
            raise CacheError(f"stats: {exc}") from exc
        yield 1.0, "Loaded"
        return CachedResult(
            key=self.key, gcode_path=self.gcode_path,
            moves={n[6:]: a for n, a in loaded.items() if n.startswith("moves.")},
            layers={n[7:]: a for n, a in loaded.items() if n.startswith("layers.")},
            gcode_line_ends=loaded["gcode_line_ends"], stats=stats, warnings=self.warnings,
            objects=list(self.manifest.get("objects", [])), wipe_tower=self.manifest.get("wipe_tower"))

    def load(self) -> CachedResult:
        """Blocking read (tests, scripts): drive :meth:`load_steps` to its end."""
        gen = self.load_steps()
        while True:
            try:
                next(gen)
            except StopIteration as stop:
                return stop.value


@dataclass(frozen=True)
class EntryInfo:
    key: str
    path: str
    bytes: int
    used: float                      # last use (the manifest's mtime)


class ResultCache:
    """The cache under ``root``, capped at ``cap_bytes`` (``<= 0`` disables storing)."""

    def __init__(self, root: str, cap_bytes: int = DEFAULT_CAP_MB * MB,
                 clock: Callable[[], float] = time.time) -> None:
        self.root = root
        self.cap_bytes = int(cap_bytes)
        self._clock = clock
        self._serial = 0

    # -- reading ------------------------------------------------------------------------------------------

    def entry_path(self, key: str) -> str:
        if not valid_key(key):
            raise ValueError(f"not a cache key: {key!r}")
        return os.path.join(self.root, key)

    def lookup(self, key: str, *, touch: bool = True) -> Entry | None:
        """The valid entry for ``key`` (and mark it used), or ``None``: missing, partial, corrupted or foreign."""
        if not valid_key(key):
            return None
        path = os.path.join(self.root, key)
        entry = self._read(key, path)
        if entry is not None and touch:
            self._touch(path)
        return entry

    def discard(self, key: str) -> None:
        """Remove the entry for ``key`` (one that proved unreadable, so the next slice replaces it)."""
        if valid_key(key):
            _rmtree(os.path.join(self.root, key))

    def has(self, key: str) -> bool:
        return self.lookup(key, touch=False) is not None

    def _read(self, key: str, path: str) -> Entry | None:
        try:
            with open(os.path.join(path, MANIFEST), encoding="utf-8") as f:
                manifest = json.load(f)
            if manifest.get("format") != FORMAT or manifest.get("key") != key:
                return None                                   # a copy under the wrong name, or another format
            arrays = manifest["arrays"]
            if "gcode_line_ends" not in arrays or "moves.type" not in arrays:
                return None
            for meta in arrays.values():
                if os.path.getsize(os.path.join(path, meta["file"])) != meta["file_bytes"]:
                    return None
            if os.path.getsize(os.path.join(path, GCODE_FILE)) != manifest["gcode_bytes"]:
                return None
            moves = {int(m["shape"][0]) for n, m in arrays.items() if n.startswith("moves.")}
            layers = {int(m["shape"][0]) for n, m in arrays.items() if n.startswith("layers.")}
            if moves != {manifest["n_moves"]} or len(layers) != 1:
                return None
            os.path.getsize(os.path.join(path, STATS_FILE))
            with open(os.path.join(path, WARNINGS_FILE), encoding="utf-8") as f:
                warnings = json.load(f)
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            return None
        return Entry(key, path, manifest, warnings if isinstance(warnings, list) else [])

    def _touch(self, path: str) -> None:
        now = self._clock()
        try:
            os.utime(os.path.join(path, MANIFEST), (now, now))
        except OSError:
            pass

    # -- writing ------------------------------------------------------------------------------------------

    def store(self, key: str, result) -> Iterator:
        """Write ``result`` (a ``SliceResult``) as the entry for ``key``, one array per step: a generator for the
        tick runner that yields ``(fraction, message)`` and returns the :class:`Entry` (``None`` when the cache is
        disabled). Raises :class:`CacheError`; whatever was staged is removed, however it ends (``close()`` too)."""
        if self.cap_bytes <= 0:
            return None
        final = self.entry_path(key)
        existing = self.lookup(key)
        if existing is not None:                              # content-addressed: the same key is the same slice
            return existing
        os.makedirs(self.root, exist_ok=True)
        self._serial += 1
        staging = os.path.join(self.root, f"{STAGING_PREFIX}{key}-{os.getpid()}-{self._serial}")
        os.makedirs(staging)
        committed = False
        try:
            fields = [(f"moves.{n}", a) for n, a in result.moves.items()]
            fields += [(f"layers.{n}", a) for n, a in result.layers.items()]
            fields.append(("gcode_line_ends", result.gcode_line_ends))
            total = len(fields) + 2
            arrays: dict[str, dict] = {}
            gcode_dest = os.path.join(staging, GCODE_FILE)
            try:
                result.write_gcode(gcode_dest + ".part")
                os.replace(gcode_dest + ".part", gcode_dest)
            except BaseException:
                try:
                    os.remove(gcode_dest + ".part")
                except OSError:
                    pass
                raise
            gcode_bytes = os.path.getsize(gcode_dest)
            yield 1 / total, "Writing G-code"
            for i, (name, arr) in enumerate(fields):
                arr = np.asarray(arr)
                if arr.dtype.hasobject:
                    raise CacheError(f"{name}: object arrays are not cacheable")
                file = f"{name}.npy"
                size = _write_atomic(os.path.join(staging, file),
                                     lambda f, a=arr: np.save(f, a, allow_pickle=False))
                arrays[name] = {"file": file, "dtype": str(arr.dtype), "shape": list(arr.shape), "file_bytes": size}
                yield (i + 2) / total, f"Writing {name}"
            stats_bytes = _write_atomic(os.path.join(staging, STATS_FILE), lambda f: f.write(
                json.dumps(result.stats, default=_json_default).encode("utf-8")))
            warnings_bytes = _write_atomic(os.path.join(staging, WARNINGS_FILE), lambda f: f.write(
                json.dumps(list(result.warnings or ()), default=_json_default).encode("utf-8")))
            yield (total - 1) / total, "Writing the summary"
            manifest = {
                "format": FORMAT, "key": key, "created": self._clock(),
                "n_moves": int(len(result.moves["type"])), "arrays": arrays,
                "gcode_bytes": gcode_bytes,
                "bytes": gcode_bytes + stats_bytes + warnings_bytes + sum(a["file_bytes"] for a in arrays.values()),
                "objects": list(getattr(result, "objects", None) or []),
                "wipe_tower": getattr(result, "wipe_tower", None)}
            _write_atomic(os.path.join(staging, MANIFEST), lambda f: f.write(
                json.dumps(manifest, default=_json_default).encode("utf-8")))
            self._publish(staging, final, key)
            committed = True
        except CacheError:
            raise
        except Exception as exc:  # noqa: BLE001 - a disk problem, a result without a field: one error type
            raise CacheError(str(exc)) from exc
        finally:
            if not committed:
                shutil.rmtree(staging, ignore_errors=True)
        self._touch(final)
        self.evict(protect=(key,))
        entry = self.lookup(key, touch=False)
        yield 1.0, "Cached"
        return entry

    def store_now(self, key: str, result) -> Entry | None:
        """Blocking write (tests, scripts): drive :meth:`store` to its end."""
        gen = self.store(key, result)
        while True:
            try:
                next(gen)
            except StopIteration as stop:
                return stop.value

    def _publish(self, staging: str, final: str, key: str) -> None:
        """Rename the finished staging directory into place (replacing an invalid entry, never a valid one)."""
        if os.path.lexists(final):
            if self._read(key, final) is not None:
                shutil.rmtree(staging, ignore_errors=True)   # raced: an identical entry is already there
                return
            _rmtree(final)
        os.rename(staging, final)

    # -- eviction -----------------------------------------------------------------------------------------

    def entries(self) -> list[EntryInfo]:
        """Valid entries, oldest use first."""
        out = []
        for name in self._names():
            if not valid_key(name):
                continue
            path = os.path.join(self.root, name)
            entry = self._read(name, path)
            if entry is None:
                continue
            try:
                used = os.path.getmtime(os.path.join(path, MANIFEST))
            except OSError:
                continue
            out.append(EntryInfo(name, path, entry.bytes, used))
        return sorted(out, key=lambda e: (e.used, e.key))

    def usage(self) -> int:
        return sum(e.bytes for e in self.entries())

    def evict(self, protect=()) -> list[str]:
        """Sweep litter, then remove least recently used entries until the total fits ``cap_bytes``. The keys in
        ``protect`` are never removed. Returns the evicted keys, oldest first."""
        self._sweep()
        infos = self.entries()
        total = sum(e.bytes for e in infos)
        removed = []
        for info in infos:
            if total <= self.cap_bytes:
                break
            if info.key in protect:
                continue
            _rmtree(info.path)
            total -= info.bytes
            removed.append(info.key)
            log.info("evicted cache entry %s (%d MB)", info.key, info.bytes // MB)
        return removed

    def clear(self) -> int:
        """Remove every entry (and litter); returns how many entries went."""
        n = 0
        for name in self._names():
            path = os.path.join(self.root, name)
            if valid_key(name) or name.startswith(STAGING_PREFIX):
                _rmtree(path)
                n += valid_key(name)
        return n

    def _names(self) -> list[str]:
        try:
            return sorted(os.listdir(self.root))
        except OSError:
            return []

    def _sweep(self) -> None:
        """Remove key-named directories that are not valid entries and staging directories nobody is writing."""
        now = self._clock()
        for name in self._names():
            path = os.path.join(self.root, name)
            if not os.path.isdir(path):
                continue
            if name.startswith(STAGING_PREFIX):
                try:
                    age = now - os.path.getmtime(path)
                except OSError:
                    continue
                if age > STAGING_MAX_AGE_S:
                    shutil.rmtree(path, ignore_errors=True)
            elif valid_key(name) and self._read(name, path) is None:
                _rmtree(path)
