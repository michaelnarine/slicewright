# SPDX-License-Identifier: GPL-3.0-or-later
"""core/network/secrets.py: the keychain backend (fake runner, and the real keychain on macOS), the 0600 file, the manager."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import uuid

import pytest
from slicewright.core import redact
from slicewright.core.network import secrets as sec
from slicewright.core.network.secrets import (FileSecretStore, LibsecretStore, MacKeychainStore, SecretManager,
                                              SecretStoreError, SecretStoreUnavailable, WindowsCredentialStore)

SECRET = "ACCESS-code-Ünï-12345678"
REF = "0b0f2a8e-6f0c-4d5e-8a0f-1d2e3f405162"


@pytest.fixture(autouse=True)
def clean():
    redact.clear()
    yield
    redact.clear()


class FakeSecurity:
    """Stands in for /usr/bin/security: keeps items, records argv and stdin, can be told to fail."""

    def __init__(self):
        self.items: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[list[str], str | None]] = []
        self.fail_with: int | None = None

    def __call__(self, argv, stdin):
        self.calls.append((list(argv), stdin))
        if self.fail_with is not None:
            return self.fail_with, f"noise with {SECRET} in it"
        args = list(argv[1:])
        if args[0] == "-i":
            words = stdin.split()
            assert words[0] == "add-generic-password"
            svc, acct, hexed = (words[words.index(k) + 1] for k in ("-s", "-a", "-X"))
            self.items[(svc, acct)] = bytes.fromhex(hexed).decode("ascii")
            return 0, ""
        svc, acct = args[args.index("-s") + 1], args[args.index("-a") + 1]
        if args[0] == "find-generic-password":
            if (svc, acct) not in self.items:
                return 44, ""
            return 0, self.items[(svc, acct)] + "\n"
        if args[0] == "delete-generic-password":
            return (0, "password has been deleted.\n") if self.items.pop((svc, acct), None) is not None else (44, "")
        raise AssertionError(args)


def mac(runner=None):
    return MacKeychainStore("svc-test", runner=runner or FakeSecurity(), platform="darwin")


# -- the keychain backend (fake runner) --------------------------------------------------------------------------

def test_keychain_round_trip_and_the_secret_never_in_argv():
    fake = FakeSecurity()
    store = mac(fake)
    assert store.available() and store.get(REF) is None
    store.set(REF, SECRET)
    assert store.get(REF) == SECRET
    store.set(REF, "other-value-1")                       # -U: replaced, not a duplicate error
    assert store.get(REF) == "other-value-1"
    assert store.delete(REF) is True and store.delete(REF) is False and store.get(REF) is None
    for argv, stdin in fake.calls:
        assert not any(SECRET in a or SECRET.encode().hex() in a for a in argv), argv
        assert "other-value-1" not in " ".join(argv)
    writes = [(argv, stdin) for argv, stdin in fake.calls if argv[1:] == ["-i"]]
    assert writes and all(SECRET not in (stdin or "") for _, stdin in writes)          # hex only, even on stdin
    assert writes[0][1].startswith(f"add-generic-password -U -s svc-test -a {REF} -X ")


def test_keychain_stores_non_ascii_unambiguously():
    fake = FakeSecurity()
    store = mac(fake)
    for secret in (SECRET, "deadbeef", "sw1:00", "a b  c", "quote\"s'\\"):
        store.set(REF, secret)
        assert store.get(REF) == secret


def test_keychain_failures_have_fixed_messages_without_tool_output():
    fake = FakeSecurity()
    store = mac(fake)
    fake.fail_with = 128
    with pytest.raises(SecretStoreError, match="cancelled or denied") as exc:
        store.get(REF)
    assert SECRET not in str(exc.value)
    fake.fail_with = 36
    for op in (lambda: store.get(REF), lambda: store.set(REF, SECRET), lambda: store.delete(REF)):
        with pytest.raises(SecretStoreError, match="exit status 36") as exc:
            op()
        assert SECRET not in str(exc.value) and SECRET not in repr(exc.value)


def test_keychain_rejects_bad_refs_services_and_secrets():
    store = mac()
    for bad in ("", "a b", "x;y", "a\nb", "x" * 65, "../etc"):
        with pytest.raises(ValueError):
            store.get(bad)
    for bad in ("", "a\nb", "x" * 5000, "tab\there"):
        with pytest.raises(ValueError) as exc:
            store.set(REF, bad)
        assert bad not in str(exc.value) or bad == ""
    with pytest.raises(ValueError):
        MacKeychainStore("bad service; rm")


def test_keychain_unavailable_off_macos():
    store = MacKeychainStore(runner=FakeSecurity(), platform="linux")
    assert not store.available()
    with pytest.raises(SecretStoreUnavailable):
        store.set(REF, SECRET)


def test_keychain_registers_secrets_for_redaction():
    store = mac()
    store.set(REF, SECRET)
    assert redact.redact(f"leak {SECRET}") == "leak ***"


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS keychain")
def test_real_macos_keychain_round_trip_cleans_up_after_itself():
    service = f"slicewright-test-{uuid.uuid4().hex}"
    store = MacKeychainStore(service)
    ref = sec.new_ref()
    secret = "t3st-" + uuid.uuid4().hex + "-Ünï"
    try:
        assert store.available() and store.get(ref) is None
        store.set(ref, secret)
        assert store.get(ref) == secret
        store.set(ref, secret + "2")
        assert store.get(ref) == secret + "2"
        # the secret is not in the process list while the tool runs: argv never held it
        assert store.delete(ref) is True
        assert store.get(ref) is None
    finally:
        subprocess.run([sec.SECURITY_TOOL, "delete-generic-password", "-s", service, "-a", ref],
                       capture_output=True, check=False)
    found = subprocess.run([sec.SECURITY_TOOL, "find-generic-password", "-s", service, "-a", ref],
                           capture_output=True, check=False)
    assert found.returncode != 0                           # nothing left behind


# -- stubs -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("cls", [LibsecretStore, WindowsCredentialStore])
def test_unimplemented_backends_say_so(cls):
    store = cls()
    assert not store.available() and "not implemented" in store.name
    for op in (lambda: store.get(REF), lambda: store.set(REF, "abcd"), lambda: store.delete(REF)):
        with pytest.raises(SecretStoreUnavailable, match="not implemented"):
            op()


# -- the file fallback -------------------------------------------------------------------------------------------

def test_file_store_round_trip_mode_and_atomic_write(tmp_path):
    path = tmp_path / "cfg" / "secrets.json"
    store = FileSecretStore(str(path))
    assert store.get(REF) is None and not store.has_any()
    store.set(REF, SECRET)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert FileSecretStore(str(path)).get(REF) == SECRET
    assert [p.name for p in path.parent.iterdir()] == ["secrets.json"]         # no temp file left over
    assert store.delete(REF) and not store.delete(REF) and store.get(REF) is None


def test_file_store_failed_write_keeps_the_old_file(tmp_path, monkeypatch):
    path = tmp_path / "s.json"
    store = FileSecretStore(str(path))
    store.set(REF, "first-value")
    before = path.read_bytes()

    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(SecretStoreError, match="cannot be written") as exc:
        store.set(REF, "second-value")
    assert "disk full" not in str(exc.value)
    monkeypatch.undo()
    assert path.read_bytes() == before and [p.name for p in tmp_path.iterdir()] == ["s.json"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_file_store_repairs_open_permissions_and_says_so(tmp_path):
    path = tmp_path / "s.json"
    store = FileSecretStore(str(path))
    store.set(REF, SECRET)
    os.chmod(path, 0o644)
    assert store.get(REF) == SECRET
    assert store.repaired and stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="symlinks")
def test_file_store_refuses_a_symlink_and_a_damaged_file(tmp_path):
    real = tmp_path / "real.json"
    real.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(SecretStoreError, match="not a regular file"):
        FileSecretStore(str(link)).get(REF)
    bad = tmp_path / "bad.json"
    bad.write_text("not json" + SECRET)
    os.chmod(bad, 0o600)
    with pytest.raises(SecretStoreError, match="damaged") as exc:
        FileSecretStore(str(bad)).get(REF)
    assert SECRET not in str(exc.value)
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"secrets": []}))
    os.chmod(wrong, 0o600)
    with pytest.raises(SecretStoreError, match="damaged"):
        FileSecretStore(str(wrong)).set(REF, "abcd")


def test_file_warning_is_once_per_user_and_only_with_a_secret(tmp_path):
    store = FileSecretStore(str(tmp_path / "s.json"))
    assert store.take_warning() is None                  # nothing stored, nothing to warn about
    store.set(REF, SECRET)
    first = store.take_warning()
    assert first and "unencrypted" in first and SECRET not in first
    assert store.take_warning() is None
    assert FileSecretStore(str(tmp_path / "s.json")).take_warning() is None     # persisted, not per session
    store.set("other", "abcd")
    assert store.take_warning() is None


# -- the manager -------------------------------------------------------------------------------------------------

def test_manager_prefers_the_keychain_and_reports_it(tmp_path):
    mgr = SecretManager(mac(), FileSecretStore(str(tmp_path / "s.json")))
    assert not mgr.using_file and mgr.status().encrypted and "macOS keychain" in mgr.status().text
    ref = mgr.new_ref()
    mgr.set(ref, SECRET)
    assert mgr.get(ref) == SECRET and mgr.has(ref) and not (tmp_path / "s.json").exists()
    assert mgr.take_warning() is None
    assert mgr.delete(ref) and not mgr.has(ref)


def test_manager_uses_the_file_where_there_is_no_keychain_and_warns_loudly(tmp_path):
    mgr = sec.default_manager(str(tmp_path / "s.json"), platform="linux")
    assert mgr.using_file and not mgr.status().encrypted and "unencrypted file" in mgr.status().text
    mgr.set("r1", SECRET)
    warning = mgr.take_warning()
    assert warning and mgr.take_warning() is None and mgr.get("r1") == SECRET
    assert json.loads((tmp_path / "s.json").read_text())["secrets"] == {"r1": SECRET}
    assert sec.default_manager(str(tmp_path / "w.json"), platform="win32").using_file


def test_manager_does_not_fall_back_silently_when_the_keychain_errors(tmp_path):
    fake = FakeSecurity()
    mgr = SecretManager(mac(fake), FileSecretStore(str(tmp_path / "s.json")))
    fake.fail_with = 128
    with pytest.raises(SecretStoreError):
        mgr.set(REF, SECRET)
    assert not (tmp_path / "s.json").exists()


def test_manager_finds_a_secret_saved_in_the_file_before_a_keychain_existed(tmp_path):
    file_store = FileSecretStore(str(tmp_path / "s.json"))
    file_store.set(REF, SECRET)
    mgr = SecretManager(mac(), file_store)
    assert mgr.get(REF) == SECRET
    assert mgr.delete(REF) and file_store.get(REF) is None


def test_new_refs_are_valid_and_unique():
    refs = {sec.new_ref() for _ in range(50)}
    assert len(refs) == 50 and all(sec.REF_RE.match(r) for r in refs)


def test_no_secret_in_repr_str_or_log_of_any_store(tmp_path, caplog):
    import logging
    from slicewright.core.redact import RedactingFilter
    stores = [mac(), FileSecretStore(str(tmp_path / "s.json")), LibsecretStore(), WindowsCredentialStore()]
    mgr = SecretManager(mac(), FileSecretStore(str(tmp_path / "m.json")))
    mgr.set(REF, SECRET)
    texts = [repr(s) + str(s) for s in stores] + [repr(mgr), str(mgr.status()), repr(mgr.status())]
    for s in stores[:2]:
        s.set(REF, SECRET)
        texts += [repr(s), str(s), s.name]
    assert all(SECRET not in t for t in texts)
    logger = logging.getLogger("slicewright.test_secrets")
    scrub = RedactingFilter()
    logger.addFilter(scrub)
    try:
        with caplog.at_level(logging.DEBUG, logger="slicewright.test_secrets"):
            logger.info("loaded %s for %s", SECRET, REF)
    finally:
        logger.removeFilter(scrub)
    assert SECRET not in caplog.text and REF in caplog.text
