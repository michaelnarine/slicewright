# SPDX-License-Identifier: GPL-3.0-or-later
"""core/printers.py: printer records, validation, options, the secret's life cycle."""
from __future__ import annotations

import dataclasses

import pytest
from slicewright.core import printers as p
from slicewright.core.network import secrets as sec

SECRET = "API-KEY-0123456789abcdef"


def fields(rec) -> set[str]:
    return {f.name for f in dataclasses.fields(rec)}


def manager(tmp_path):
    return sec.SecretManager(None, sec.FileSecretStore(str(tmp_path / "s.json")))


def good(**kw):
    base = dict(kind=p.OCTOPRINT, host="octopi.local")
    base.update(kw)
    return p.new_record(**base)


# -- the record --------------------------------------------------------------------------------------------------

def test_the_record_has_no_secret_field_only_a_reference():
    names = fields(p.PrinterRecord())
    assert "secret_ref" in names and "secret_saved" in names
    assert not any(w in n for n in names for w in ("password", "access_code", "api_key", "token")) and "secret" not in names - {"secret_ref", "secret_saved"}


def test_new_records_get_unique_ids_refs_and_names():
    first = p.new_record()
    second = p.new_record([first])
    third = p.new_record([first, second], name="Printer")
    assert first.id != second.id and first.secret_ref != second.secret_ref and first.id != first.secret_ref
    assert (first.name, second.name, third.name) == ("Printer", "Printer 2", "Printer 3")
    assert sec.REF_RE.match(first.id) and sec.REF_RE.match(first.secret_ref)
    assert not first.secret_saved


def test_repr_is_short_and_has_no_secret():
    rec = good(name="Voron")
    assert repr(rec) == "<PrinterRecord 'Voron' octoprint octopi.local:80>"


def test_effective_ports_and_urls():
    assert good(kind=p.OCTOPRINT).effective_port == 80 and good(kind=p.MOONRAKER).effective_port == 7125
    assert good(kind=p.BAMBU_LAN).effective_port == 990 and p.BAMBU_MQTT_PORT == 8883
    assert good(options={"https": True}).effective_port == 443
    assert p.base_url(good()) == "http://octopi.local"
    assert p.base_url(good(kind=p.MOONRAKER, host="10.0.0.5")) == "http://10.0.0.5:7125"
    assert p.base_url(good(port=5000, options={"base_path": "/octoprint/"})) == "http://octopi.local:5000/octoprint"
    assert p.base_url(good(options={"https": True})) == "https://octopi.local"
    assert p.base_url(good(host="fe80::1", port=7125)) == "http://[fe80::1]:7125"
    with pytest.raises(ValueError):
        p.base_url(good(kind=p.BAMBU_LAN))


# -- validation --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("host", ["octopi.local", "printer", "192.168.1.20", "fe80::1", "a-b.example.org", "Printer.Local.",
                                  "x" * 63 + ".com"])
def test_valid_hosts(host):
    assert p.validate_host(host) is None


@pytest.mark.parametrize("host", ["", "  ", "http://octopi.local", "octopi.local/api", "octopi.local:5000", "user@host",
                                  "[::1]", "bad host", "-bad.com", "bad-.com", "a..b", "300.1.1.1", "1.2.3", "host\x00",
                                  "x" * 64 + ".com", "ho_st.com", "a" * 300, "host?x", "host#x", "ho\\st"])
def test_invalid_hosts(host):
    assert p.validate_host(host)


def test_host_error_does_not_echo_credentials():
    assert "hunter2" not in p.validate_host("admin:hunter2@octopi.local")


def test_validate_fields():
    assert p.validate(good()) == []
    assert [x.field for x in p.validate(dataclasses.replace(good(), name="  "))] == ["name"]
    assert [x.field for x in p.validate(good(name="x" * 65))] == ["name"]
    assert [x.field for x in p.validate(good(host="bad host"))] == ["host"]
    assert [x.field for x in p.validate(good(port=70000))] == ["port"]
    assert [x.field for x in p.validate(good(port=-1))] == ["port"]
    assert [x.field for x in p.validate(good(kind="ender"))] == ["kind"]
    assert [x.field for x in p.validate(good(secret_ref="bad ref!"))] == ["secret_ref"]
    assert [x.field for x in p.validate(good(preset="a\nb"))] == ["preset"]


def test_an_unfinished_record_is_only_checked_for_what_it_says_unless_complete():
    rec = p.new_record()                                # no host yet
    assert p.validate(rec) == []
    fields_ = {x.field for x in p.validate(rec, complete=True)}
    assert fields_ == {"host", "secret"}
    ready = dataclasses.replace(good(), secret_saved=True)
    assert p.validate(ready, complete=True) == []
    assert {x.field for x in p.validate(good(kind=p.BAMBU_LAN, secret_saved=True), complete=True)} == {"serial"}
    assert p.validate(good(kind=p.MOONRAKER), complete=True) == []          # the Moonraker key is optional
    assert {x.field for x in p.validate(good(kind=p.BAMBU_LAN, options={"serial": "01P00A123456789"}, secret_saved=True), complete=True)} == set()


def test_names_are_unique_case_insensitively_but_a_record_does_not_clash_with_itself():
    a, b = good(name="Voron"), good(name="voron")
    assert p.validate(a, [a]) == []
    assert [x.field for x in p.validate(b, [a])] == ["name"]


@pytest.mark.parametrize("options,field_", [
    ({"api_key": "x"}, "options"), ({"access_code": "12345678"}, "options"), ({"password": "p"}, "options"),
    ({"token": "t"}, "options"), ({"x_secret": "s"}, "options"), ({"authorization": "a"}, "options"),
    ({"serial": "ABC12345"}, "options"),                    # not for OctoPrint
    ({"https": "yes"}, "options"), ({"tls": "maybe", "https": True}, "options"),
    ({"base_path": "api"}, "options"), ({"base_path": "/a b"}, "options"), ({"base_path": "/a?x=1"}, "options"),
    ({"Bad Key": 1}, "options"), ({"https": True, "base_path": "/" + "x" * 200}, "options"),
])
def test_bad_options(options, field_):
    assert [x.field for x in p.validate(good(options=options))] == [field_]


def test_credential_looking_option_is_rejected_by_name_without_echoing_a_value():
    problems = p.validate(good(options={"api_key": SECRET}))
    assert problems and all(SECRET not in x.message for x in problems)


def test_good_options():
    assert p.validate(good(options={"https": True, "tls": "pin", "base_path": "/octoprint"})) == []
    assert p.validate(good(kind=p.BAMBU_LAN, options={"serial": "01P00A123456789"})) == []
    assert [x.field for x in p.validate(good(kind=p.BAMBU_LAN, options={"serial": "no"}))] == ["serial"]


def test_options_text_round_trip_and_damage_tolerance():
    opts = {"https": True, "base_path": "/x", "tls": "pin"}
    assert p.options_from_text(p.options_to_text(opts)) == opts
    for bad in ("", "{not json", "[1]", "null", "x" * 5000):
        assert p.options_from_text(bad) == {}


# -- the secret's life cycle -------------------------------------------------------------------------------------

def test_save_secret_uses_the_records_ref_and_marks_it_saved(tmp_path):
    mgr = manager(tmp_path)
    rec = good()
    saved = p.save_secret(rec, SECRET, mgr)
    assert saved.secret_saved and saved.secret_ref == rec.secret_ref and mgr.get(rec.secret_ref) == SECRET
    assert not rec.secret_saved                            # the original record is not mutated
    assert all(SECRET not in str(getattr(saved, f)) for f in fields(saved))
    assert SECRET not in repr(saved)
    again = p.save_secret(saved, "second-value-1", mgr)    # replace under the same ref
    assert again.secret_ref == rec.secret_ref and mgr.get(rec.secret_ref) == "second-value-1"


def test_a_record_without_a_ref_gets_one_when_a_secret_is_saved(tmp_path):
    mgr = manager(tmp_path)
    saved = p.save_secret(p.PrinterRecord(id="x", name="N"), SECRET, mgr)
    assert saved.secret_ref and mgr.get(saved.secret_ref) == SECRET


def test_a_failing_store_leaves_the_record_unsaved(tmp_path):
    class Broken(sec.FileSecretStore):
        def set(self, ref, secret):
            raise sec.SecretStoreError("keychain said no")
    mgr = sec.SecretManager(None, Broken(str(tmp_path / "s.json")))
    with pytest.raises(sec.SecretStoreError):
        p.save_secret(good(), SECRET, mgr)
    with pytest.raises(ValueError):
        p.save_secret(good(), "bad\nsecret", manager(tmp_path))


def test_clear_secret(tmp_path):
    mgr = manager(tmp_path)
    saved = p.save_secret(good(), SECRET, mgr)
    cleared = p.clear_secret(saved, mgr)
    assert not cleared.secret_saved and cleared.secret_ref == saved.secret_ref and mgr.get(saved.secret_ref) is None
    assert p.clear_secret(p.PrinterRecord(), mgr).secret_saved is False


def test_removing_a_record_deletes_its_secret_and_only_its_own(tmp_path):
    mgr = manager(tmp_path)
    a = p.save_secret(good(name="A"), SECRET, mgr)
    b = p.save_secret(good(name="B"), "other-secret-9", mgr)
    records = [a, b]
    assert p.remove_record(records, a.id, mgr) is a
    assert records == [b] and mgr.get(a.secret_ref) is None and mgr.get(b.secret_ref) == "other-secret-9"
    assert p.remove_record(records, "missing", mgr) is None


def test_a_store_that_refuses_to_delete_keeps_the_record(tmp_path):
    class Stubborn(sec.FileSecretStore):
        def delete(self, ref):
            raise sec.SecretStoreError("denied")
    mgr = sec.SecretManager(None, Stubborn(str(tmp_path / "s.json")))
    rec = dataclasses.replace(good(), secret_saved=True)
    records = [rec]
    with pytest.raises(sec.SecretStoreError):
        p.remove_record(records, rec.id, mgr)
    assert records == [rec]


def test_the_secret_does_not_appear_in_the_record_or_its_serialisations(tmp_path):
    mgr = manager(tmp_path)
    saved = p.save_secret(good(options={"https": True}), SECRET, mgr)
    blob = repr(saved) + str(dataclasses.asdict(saved)) + p.options_to_text(saved.options)
    assert SECRET not in blob


# -- the pinned certificate ---------------------------------------------------------------------------------------

FP = "ab" * 32


def test_a_pin_is_a_public_option_that_is_validated_not_a_secret():
    rec = good(options={"https": True, "tls": "pin", "pin": FP})
    assert p.validate(rec) == []
    assert p.validate(good(kind=p.BAMBU_LAN, options={"pin": FP})) == []
    assert [x.field for x in p.validate(good(options={"pin": "not-a-fingerprint"}))] == ["pin"]
    assert [x.field for x in p.validate(good(options={"pin": "ab" * 31}))] == ["pin"]


def test_pin_helpers_round_trip_without_touching_the_original():
    rec = good(options={"https": True, "tls": "pin"})
    assert p.pinned_fingerprint(rec) is None
    pinned = p.with_pin(rec, "AB:" + ":".join(["AB"] * 31))
    assert p.pinned_fingerprint(pinned) == FP and "pin" not in rec.options
    assert p.pinned_fingerprint(p.without_pin(pinned)) is None and pinned.options["pin"] == FP
    assert p.pinned_fingerprint(good(options={"pin": "damaged"})) is None
    with pytest.raises(ValueError):
        p.with_pin(rec, "nope")


def test_the_pin_survives_the_options_text_round_trip():
    opts = {"https": True, "tls": "pin", "pin": FP}
    assert p.options_from_text(p.options_to_text(opts)) == opts


def test_tls_policy_pins_only_when_asked_and_keys_the_store_by_record_id():
    plain = good(options={"https": True})
    assert p.tls_policy(plain, None).mode == "verify"
    rec = good(options={"https": True, "tls": "pin", "pin": FP})
    store = p.record_pin_store(rec)
    policy = p.tls_policy(rec, store)
    assert (policy.mode, policy.pin_key) == ("pin", rec.id) and store.get(rec.id) == FP
    assert p.uses_pinning(rec) and not p.uses_pinning(plain)
    with pytest.raises(ValueError):
        p.tls_policy(rec, None)


def test_a_bambu_printer_always_pins_its_certificate_and_has_no_way_to_turn_that_off():
    for options in ({}, {"serial": "01P00A123456789"}, {"pin": FP}):
        rec = good(kind=p.BAMBU_LAN, options=options)
        store = p.record_pin_store(rec)
        policy = p.tls_policy(rec, store)
        assert (policy.mode, policy.pin_key) == ("pin", rec.id) and p.uses_pinning(rec)
        with pytest.raises(ValueError):
            p.tls_policy(rec, None)                       # never a silent fall back to "verify" or "off"
    # "tls" is not an option of a Bambu record (so "verify" cannot be chosen), and neither is anything credential-like
    assert [x.field for x in p.validate(good(kind=p.BAMBU_LAN, options={"tls": "verify"}))] == ["options"]
    assert [x.field for x in p.validate(good(kind=p.BAMBU_LAN, options={"access_code": "1"}))] == ["options"]
