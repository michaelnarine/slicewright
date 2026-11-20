# SPDX-License-Identifier: GPL-3.0-or-later
"""Where printer secrets (access codes, API keys, passwords) are kept (03 section 8.5). No ``bpy``.

A printer record holds only a ``secret_ref`` (an opaque id); the secret itself lives in a :class:`SecretStore`:

* :class:`MacKeychainStore`: the macOS login keychain through ``/usr/bin/security``. **The secret never appears on a
  command line** (``ps`` shows every argument of every process): ``set`` feeds ``security -i`` its command on standard
  input, with the password as ``-X <hex>`` (the tool's hexadecimal form, which also avoids all quoting), and ``get`` uses
  ``find-generic-password ... -w``, which prints the password on standard output. Only the service name and the
  reference are ever arguments. The stored text is ``sw1:<hex of the UTF-8 secret>``, because ``security -w`` prints a
  password with any non-ASCII byte as bare hex, which would be indistinguishable from a secret that happens to be hex.
* :class:`LibsecretStore` (Linux) and :class:`WindowsCredentialStore`: **stubs**, not implemented. They report
  themselves unavailable, so those platforms use the file fallback until someone writes them. (03 8.5 also mentions
  ``secret-tool`` for Linux; the stub is where that goes.)
* :class:`FileSecretStore`: a JSON file with mode ``0600`` in the user's config directory, written atomically, checked
  on every read (a group- or world-accessible file is repaired and reported; a symlink or a file owned by someone else is
  refused). It is **not encrypted**: the UI says so, once loudly (:meth:`FileSecretStore.take_warning`) and always in
  the status line.

:class:`SecretManager` picks the backend once (the OS keychain when it is available, the file otherwise) and never moves
a secret silently: if the keychain is available but a call fails, the error goes to the user and nothing falls back to
the file. Every secret it reads or writes is registered with :mod:`slicewright.core.redact`. Error messages are fixed
text, an exit status and the reference, never the tool's own output.

One caveat: the ``security`` call is a subprocess that the add-on waits for (up to 10 s), which is how a locked keychain
that asks the user to unlock it is handled. It runs only when the user saves or uses a secret, never on a timer.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Sequence

from .. import redact

SERVICE = "slicewright"
REF_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_SECRET = 4096
SECURITY_TOOL = "/usr/bin/security"
SECURITY_TIMEOUT_S = 10.0
ENCODING_TAG = "sw1:"
FILE_WARNING = ("Secrets are stored in a file in your user folder, unencrypted, because no system keychain is "
                "available here. Anyone who can read your files can read them. The file is readable only by you.")


class SecretStoreError(Exception):
    """A store could not do what was asked. The message is fixed text (no secret, no tool output)."""


class SecretStoreUnavailable(SecretStoreError):
    """This backend does not work on this system (or is not implemented yet)."""


def check_ref(ref: str) -> str:
    if not isinstance(ref, str) or not REF_RE.match(ref):
        raise ValueError("A secret reference is 1 to 64 letters, digits, '_' or '-'")
    return ref


def check_secret(secret: str) -> str:
    if not isinstance(secret, str) or not secret or len(secret) > MAX_SECRET or re.search(r"[\x00-\x1f\x7f]", secret):
        raise ValueError("A secret must be 1 to 4096 characters without control characters")
    return secret


def new_ref() -> str:
    """A fresh opaque reference for a printer's secret."""
    return str(uuid.uuid4())


class SecretStore(ABC):
    """The interface every backend implements. ``get`` returns ``None`` for a reference that is not stored."""

    name = "secret store"
    encrypted = True                         # protected by the operating system rather than a plain file

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def get(self, ref: str) -> str | None: ...

    @abstractmethod
    def set(self, ref: str, secret: str) -> None: ...

    @abstractmethod
    def delete(self, ref: str) -> bool:
        """Remove the secret; False if there was none."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__}>"


# -- macOS -------------------------------------------------------------------------------------------------------

Runner = Callable[[Sequence[str], "str | None"], "tuple[int, str]"]


def _run_security(argv: Sequence[str], stdin: str | None) -> tuple[int, str]:
    try:
        done = subprocess.run(list(argv), input=stdin, capture_output=True, text=True, timeout=SECURITY_TIMEOUT_S,
                              check=False)
    except subprocess.TimeoutExpired:
        raise SecretStoreError("The macOS keychain did not answer in time (is it waiting for you to unlock it?)") \
            from None
    except OSError:
        raise SecretStoreUnavailable("The macOS 'security' tool could not be run") from None
    return done.returncode, done.stdout


class MacKeychainStore(SecretStore):
    name = "macOS keychain"

    def __init__(self, service: str = SERVICE, *, tool: str = SECURITY_TOOL, runner: Runner | None = None,
                 platform: str | None = None) -> None:
        if not SERVICE_RE.match(service):
            raise ValueError("invalid keychain service name")
        self.service = service
        self.tool = tool
        self._runner = runner or _run_security
        self._platform = platform if platform is not None else sys.platform

    def available(self) -> bool:
        return self._platform == "darwin" and (self._runner is not _run_security or os.path.exists(self.tool))

    def _require(self) -> None:
        if not self.available():
            raise SecretStoreUnavailable("The macOS keychain is not available here")

    def _fail(self, what: str, code: int) -> SecretStoreError:
        if code == 128:
            return SecretStoreError(f"The macOS keychain {what} was cancelled or denied")
        return SecretStoreError(f"The macOS keychain {what} failed (security exit status {code})")

    def get(self, ref: str) -> str | None:
        check_ref(ref)
        self._require()
        code, out = self._runner([self.tool, "find-generic-password", "-s", self.service, "-a", ref, "-w"], None)
        if code == 44:
            return None
        if code != 0:
            raise self._fail("lookup", code)
        text = out[:-1] if out.endswith("\n") else out
        if text.startswith(ENCODING_TAG):
            try:
                text = bytes.fromhex(text[len(ENCODING_TAG):]).decode("utf-8")
            except ValueError:
                raise SecretStoreError("The stored keychain item is damaged") from None
        redact.register(text)
        return text

    def set(self, ref: str, secret: str) -> None:
        check_ref(ref)
        check_secret(secret)
        self._require()
        redact.register(secret)
        stored = ENCODING_TAG + secret.encode("utf-8").hex()
        # the command goes in on standard input, not in argv: the password is never visible to `ps`
        command = f"add-generic-password -U -s {self.service} -a {ref} -X {stored.encode('ascii').hex()}\n"
        code, _out = self._runner([self.tool, "-i"], command)
        if code != 0:
            raise self._fail("write", code)

    def delete(self, ref: str) -> bool:
        check_ref(ref)
        self._require()
        code, _out = self._runner([self.tool, "delete-generic-password", "-s", self.service, "-a", ref], None)
        if code == 44:
            return False
        if code != 0:
            raise self._fail("delete", code)
        return True


# -- other platforms: stubs --------------------------------------------------------------------------------------

class _UnimplementedStore(SecretStore):
    def available(self) -> bool:
        return False

    def _no(self):
        raise SecretStoreUnavailable(f"The {self.name} backend is not implemented yet")

    def get(self, ref: str) -> str | None:
        check_ref(ref)
        self._no()

    def set(self, ref: str, secret: str) -> None:
        self._no()

    def delete(self, ref: str) -> bool:
        self._no()


class LibsecretStore(_UnimplementedStore):
    """UNIMPLEMENTED. Linux: ``libsecret-1`` through ``ctypes`` (or ``secret-tool``). macOS is the only platform
    with a keychain backend for now."""
    name = "Linux Secret Service (not implemented)"


class WindowsCredentialStore(_UnimplementedStore):
    """UNIMPLEMENTED. Windows: ``advapi32.CredWriteW`` / ``CredReadW`` through ``ctypes``."""
    name = "Windows Credential Manager (not implemented)"


# -- the file fallback -------------------------------------------------------------------------------------------

class FileSecretStore(SecretStore):
    """``{"format": 1, "warned": bool, "secrets": {ref: secret}}`` in a ``0600`` file. Not encrypted."""

    name = "unencrypted file"
    encrypted = False

    def __init__(self, path: str) -> None:
        self.path = path
        self.repaired = False                # the permissions were too open and were fixed (set on the last read)

    def available(self) -> bool:
        return True

    # -- file handling -----------------------------------------------------------------------------------------

    def _load(self) -> dict:
        try:
            info = os.lstat(self.path)
        except FileNotFoundError:
            return {"format": 1, "warned": False, "secrets": {}}
        except OSError:
            raise SecretStoreError("The secrets file cannot be read") from None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise SecretStoreError("The secrets file is not a regular file; it was not used")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise SecretStoreError("The secrets file belongs to another user; it was not used")
        if os.name != "nt" and info.st_mode & 0o077:
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                raise SecretStoreError("The secrets file is readable by other users and could not be repaired") \
                    from None
            self.repaired = True
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            raise SecretStoreError("The secrets file is damaged; move it away to start again") from None
        if not isinstance(data, dict) or not isinstance(data.get("secrets"), dict):
            raise SecretStoreError("The secrets file is damaged; move it away to start again")
        data.setdefault("format", 1)
        data.setdefault("warned", False)
        return data

    def _save(self, data: dict) -> None:
        directory = os.path.dirname(self.path) or "."
        try:
            os.makedirs(directory, mode=0o700, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".secrets.", suffix=".part")    # created 0600
        except OSError:
            raise SecretStoreError("The secrets file cannot be written") from None
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            if os.name != "nt":
                os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise SecretStoreError("The secrets file cannot be written") from None

    # -- the interface -----------------------------------------------------------------------------------------

    def get(self, ref: str) -> str | None:
        check_ref(ref)
        value = self._load()["secrets"].get(ref)
        if value is not None:
            redact.register(value)
        return value

    def set(self, ref: str, secret: str) -> None:
        check_ref(ref)
        check_secret(secret)
        redact.register(secret)
        data = self._load()
        data["secrets"][ref] = secret
        self._save(data)

    def delete(self, ref: str) -> bool:
        check_ref(ref)
        data = self._load()
        if ref not in data["secrets"]:
            return False
        del data["secrets"][ref]
        self._save(data)
        return True

    def has_any(self) -> bool:
        try:
            return bool(self._load()["secrets"])
        except SecretStoreError:
            return False

    def take_warning(self) -> str | None:
        """The one-time "stored unencrypted" warning: the text the first time a secret is in the file, then ``None``
        (the flag is saved in the file, so it is once per user, not once per session)."""
        data = self._load()
        if data["warned"] or not data["secrets"]:
            return None
        data["warned"] = True
        self._save(data)
        return FILE_WARNING


# -- choosing a backend ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SecretsStatus:
    backend: str
    encrypted: bool
    text: str                                # one line for the preferences ("Secrets are kept in the macOS keychain")
    permissions_repaired: bool = False


class SecretManager:
    """The add-on's view of secret storage: one active backend, the file as the only fallback."""

    def __init__(self, primary: SecretStore | None, fallback: FileSecretStore) -> None:
        self.primary = primary
        self.fallback = fallback
        self.active: SecretStore = primary if primary is not None and primary.available() else fallback

    @property
    def using_file(self) -> bool:
        return self.active is self.fallback

    def new_ref(self) -> str:
        return new_ref()

    def set(self, ref: str, secret: str) -> None:
        check_ref(ref)
        check_secret(secret)
        self.active.set(ref, secret)

    def get(self, ref: str) -> str | None:
        """The secret, or ``None``. A secret saved in the file earlier is still found once a keychain is in use."""
        check_ref(ref)
        value = self.active.get(ref)
        if value is None and not self.using_file:
            try:
                value = self.fallback.get(ref)
            except SecretStoreError:
                value = None
        return value

    def has(self, ref: str) -> bool:
        return self.get(ref) is not None

    def delete(self, ref: str) -> bool:
        removed = self.active.delete(ref)
        if not self.using_file:
            try:
                removed = self.fallback.delete(ref) or removed
            except SecretStoreError:
                pass
        return removed

    def take_warning(self) -> str | None:
        """The one-time file-storage warning if the file is the backend (see :meth:`FileSecretStore.take_warning`)."""
        return self.fallback.take_warning() if self.using_file else None

    def status(self) -> SecretsStatus:
        if self.using_file:
            return SecretsStatus(self.fallback.name, False, "Secrets are stored in an unencrypted file "
                                 "(no system keychain is available here)", self.fallback.repaired)
        return SecretsStatus(self.active.name, True, f"Secrets are kept in the {self.active.name}")


def default_primary(platform: str | None = None, **kw) -> SecretStore:
    platform = platform if platform is not None else sys.platform
    if platform == "darwin":
        return MacKeychainStore(**kw)
    if platform.startswith("win"):
        return WindowsCredentialStore()
    return LibsecretStore()


def default_manager(file_path: str, *, platform: str | None = None, primary: SecretStore | None = None) -> SecretManager:
    """The platform's keychain when it works, the ``0600`` file at ``file_path`` otherwise."""
    return SecretManager(primary if primary is not None else default_primary(platform), FileSecretStore(file_path))
