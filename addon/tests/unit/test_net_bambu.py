# SPDX-License-Identifier: GPL-3.0-or-later
"""core/network/bambu.py: Bambu LAN mode (FTPS upload, MQTT status and print start) against the in-process fakes."""
from __future__ import annotations

import json
import logging
import ssl
import threading

import pytest
from fake_net import FakeFtpsServer, FakeMqttBroker
from slicewright.core import redact
from slicewright.core.network import bambu, errors
from slicewright.core.network.nbsocket import CancelToken, Timeouts, run_blocking
from slicewright.core.network.tls import MemoryPinStore, TlsPolicy, fingerprint_of
from slicewright.core.network.webapi import Transfer
from slicewright.core.redact import SecretStr

CODE = "AC-98765432"
SERIAL = "01P00A123456789"
FAST = Timeouts(3.0, 3.0)
QUICK = bambu.PrintOptions(settle=0.0, status_wait=1.0, confirm_wait=1.5)


def fp(files):
    return fingerprint_of(ssl.PEM_cert_to_DER_cert(open(files[0]).read()))


def endpoint(ftps, mqtt, certs, pinned=0, code=CODE, serial=SERIAL, **kw):
    store = MemoryPinStore({"p": fp(certs[pinned])})
    return bambu.BambuEndpoint("127.0.0.1", TlsPolicy(mode="pin", pin_key="p", pin_store=store), SecretStr(code), serial,
                               ftps.port, mqtt.port if mqtt is not None else 8883, timeouts=FAST, upload_timeouts=FAST,
                               **kw)


def pair(certs, **kw):
    return FakeFtpsServer(CODE, tls_files=certs[0]), FakeMqttBroker(CODE, serial=SERIAL, tls_files=certs[0], **kw)


def f3mf(tmp_path, size=10_000, name="a.gcode.3mf"):
    path = tmp_path / name
    path.write_bytes(b"PK" * (size // 2))
    return str(path)


def project_file(broker):
    return [p for p in broker.published if p.topic == broker.request_topic and "print" in p.json()]


# -- the connection test --------------------------------------------------------------------------------------------

def test_the_connection_test_says_what_worked_and_labels_the_mode(certs):
    ftps, mqtt = pair(certs)
    with ftps, mqtt:
        info = run_blocking(bambu.test_connection(endpoint(ftps, mqtt, certs)))
    assert info.summary() == ("Bambu LAN mode (unsupported by Bambu): access code accepted (FTPS and MQTT), certificate "
                              "trusted, printer state IDLE, firmware 01.08.00.00, storage reachable (2 entries in its root)")
    assert [p.json() for p in mqtt.published] == [
        {"pushing": {"sequence_id": "1", "command": "pushall", "version": 1, "push_target": 1}},
        {"info": {"sequence_id": "2", "command": "get_version"}}]
    assert (mqtt.connects[0][1], mqtt.subscriptions, ftps.stored) == ("bblp", [f"device/{SERIAL}/report"], [])
    assert mqtt.clean_disconnects == 1


def test_a_broken_ftps_data_channel_is_reported_in_the_summary_not_raised(certs):
    ftps, mqtt = pair(certs)
    with ftps, mqtt:
        ftps.bad_pasv = True
        info = run_blocking(bambu.test_connection(endpoint(ftps, mqtt, certs)))
    assert not info.data_ok and "FTPS data channel failed" in info.summary() and "passive mode" in info.summary()
    assert "printer state IDLE" in info.summary()


def test_a_printer_that_logs_in_but_sends_no_status_is_reported_with_its_serial_number(certs):
    ftps, mqtt = pair(certs)
    with ftps, mqtt:
        mqtt.answer_pushall = mqtt.answer_version = False
        info = run_blocking(bambu.test_connection(endpoint(ftps, mqtt, certs), status_wait=0.4))
    assert not info.status_heard
    assert f"sent no status for serial number {SERIAL}" in info.summary()


def test_a_bad_serial_number_is_refused_before_any_connection(certs):
    ftps, mqtt = pair(certs)
    with ftps, mqtt:
        for serial in ("", "ab", "x" * 40, "01P/../#", "a+b+c+d"):
            with pytest.raises(errors.InvalidRequest, match="serial number"):
                run_blocking(bambu.test_connection(endpoint(ftps, mqtt, certs, serial=serial)))
    assert (ftps.commands, mqtt.connects) == ([], [])


@pytest.mark.parametrize("which", ["ftps", "mqtt"])
def test_a_refused_login_on_either_protocol_stops_and_points_to_developer_mode_without_a_retry(certs, which):
    ftps, mqtt = pair(certs)
    with ftps, mqtt:
        (ftps if which == "ftps" else mqtt).__dict__["password" if which == "ftps" else "access_code"] = "different-code"
        with pytest.raises(errors.LoginRejected) as exc:
            run_blocking(bambu.test_connection(endpoint(ftps, mqtt, certs)))
    text = str(exc.value)
    assert "Developer Mode" in text and "LAN Only" in text and CODE not in text
    assert len(ftps.logins) == 1 and len(mqtt.connects) == (1 if which == "mqtt" else 0)
    assert (ftps.stored, mqtt.published) == ([], [])


def test_the_certificate_must_be_the_pinned_one_for_the_test_the_upload_and_the_print(tmp_path, certs):
    f = f3mf(tmp_path)
    with FakeFtpsServer(CODE, tls_files=certs[1]) as ftps, FakeMqttBroker(CODE, serial=SERIAL, tls_files=certs[1]) as mqtt:
        for gen in (bambu.test_connection(endpoint(ftps, mqtt, certs)),
                    bambu.upload(endpoint(ftps, mqtt, certs), f, "a.gcode.3mf"),
                    bambu.upload(endpoint(ftps, mqtt, certs), f, "a.gcode.3mf", start_print=True, options=QUICK),
                    bambu.print_file(endpoint(ftps, mqtt, certs), "a.gcode.3mf", QUICK)):
            with pytest.raises(errors.TlsPinMismatch):
                run_blocking(gen)
    for server in (ftps, mqtt):
        assert bytes(server.raw_received) == b""
    assert (ftps.logins, mqtt.connects) == ([], [])


def test_the_endpoint_cannot_be_built_without_pinning_and_hides_the_code(certs):
    with pytest.raises(ValueError, match="pinning"):
        bambu.BambuEndpoint("h", TlsPolicy(), SecretStr(CODE))
    ep = bambu.BambuEndpoint("h", TlsPolicy(mode="pin", pin_key="k", pin_store=MemoryPinStore()), SecretStr(CODE))
    assert CODE not in repr(ep) and "***" in repr(ep) and CODE not in repr(ep.ftps()) and CODE not in repr(ep.mqtt())
    assert (ep.port, ep.mqtt_port, bambu.USER) == (990, 8883, "bblp")


# -- upload, and starting a print ------------------------------------------------------------------------------------

def test_an_upload_without_a_start_never_touches_mqtt(tmp_path, certs):
    ftps, mqtt = pair(certs)
    with ftps, mqtt:
        for flag in (False, None, 1, "yes"):                         # only the identity True starts a print
            result = run_blocking(bambu.upload(endpoint(ftps, mqtt, certs), f3mf(tmp_path), "a.gcode.3mf", start_print=flag))
            assert (result.printing, result.selected, result.note) == (False, False, "")
        assert result.summary() == "Sent a.gcode.3mf to the printer's storage"
    assert len(ftps.stored) == 4 and mqtt.connects == [] and mqtt.published == []


def test_an_upload_and_start_sends_project_file_and_reports_printing_only_from_the_printers_report(tmp_path, certs):
    ftps, mqtt = pair(certs)
    transfer = Transfer()
    with ftps, mqtt:
        result = run_blocking(bambu.upload(endpoint(ftps, mqtt, certs), f3mf(tmp_path), "my case.gcode.3mf",
                                           start_print=True, options=QUICK, transfer=transfer))
    assert (result.selected, result.printing, result.note) == (True, True, "")
    assert result.summary() == "Sent my case.gcode.3mf to the printer's storage and the printer reports it is printing it"
    assert ftps.stored[0].name == "my case.gcode.3mf" and transfer.phase == "done"
    (sent,) = project_file(mqtt)
    cmd = sent.json()["print"]
    assert sent.qos == 1 and sent.topic == f"device/{SERIAL}/request"
    assert cmd["command"] == "project_file" and cmd["param"] == "Metadata/plate_1.gcode"
    assert cmd["url"] == "ftp:///my case.gcode.3mf" and cmd["subtask_name"] == "my case" and cmd["file"] == ""
    assert (cmd["use_ams"], cmd["ams_mapping"], cmd["bed_leveling"], cmd["timelapse"], cmd["bed_type"]) == (
        False, [], True, False, "auto")
    assert all(cmd[k] == "0" for k in ("project_id", "profile_id", "task_id", "subtask_id"))
    assert (cmd["flow_cali"], cmd["vibration_cali"], cmd["layer_inspect"], cmd["md5"]) == (False, False, False, "")
    order = [list(p.json())[0] for p in mqtt.published]
    assert order == ["pushing", "info", "print"]                      # status first, the command last
    assert mqtt.started_file == "my case"


def test_ams_options_are_carried_into_the_command(tmp_path, certs):
    ftps, mqtt = pair(certs)
    with ftps, mqtt:
        options = bambu.PrintOptions(use_ams=True, ams_mapping=(2, -1, 0), bed_leveling=False, settle=0, status_wait=1,
                                     confirm_wait=1.5)
        run_blocking(bambu.upload(endpoint(ftps, mqtt, certs), f3mf(tmp_path), "a.gcode.3mf", start_print=True,
                                  options=options))
    cmd = project_file(mqtt)[0].json()["print"]
    assert (cmd["use_ams"], cmd["ams_mapping"], cmd["bed_leveling"]) == (True, [2, -1, 0], False)


@pytest.mark.parametrize("script,expect", [
    ({"gcode_state": "RUNNING"}, "busy (RUNNING)"), ({"gcode_state": "PAUSE"}, "busy (PAUSE)"),
    ({"gcode_state": "PREPARE"}, "busy (PREPARE)")])
def test_a_busy_printer_is_not_asked_to_print_and_the_file_is_kept(tmp_path, certs, script, expect):
    ftps, mqtt = pair(certs)
    with ftps, mqtt:
        mqtt.gcode_state = script["gcode_state"]
        result = run_blocking(bambu.upload(endpoint(ftps, mqtt, certs), f3mf(tmp_path), "a.gcode.3mf", start_print=True,
                                           options=QUICK))
    assert (result.selected, result.printing) == (False, False) and expect in result.note
    assert project_file(mqtt) == [] and len(ftps.stored) == 1


def test_a_refused_command_is_reported_with_the_printers_reason_and_never_as_printing(tmp_path, certs):
    ftps, mqtt = pair(certs)
    with ftps, mqtt:
        mqtt.start = "reject"
        result = run_blocking(bambu.upload(endpoint(ftps, mqtt, certs), f3mf(tmp_path), "a.gcode.3mf", start_print=True,
                                           options=QUICK))
    assert (result.selected, result.printing) == (False, False)
    assert "refused the print command" in result.note and "busy or rejected" in result.note


def test_silence_after_the_command_is_not_a_start(tmp_path, certs):
    ftps, mqtt = pair(certs)
    with ftps, mqtt:
        mqtt.start = "silent"
        result = run_blocking(bambu.upload(endpoint(ftps, mqtt, certs), f3mf(tmp_path), "a.gcode.3mf", start_print=True,
                                           options=QUICK))
    assert (result.selected, result.printing) == (False, False) and "did not confirm" in result.note
    assert len(project_file(mqtt)) == 1


def test_an_accepted_command_without_a_printing_report_is_selected_but_not_printing(tmp_path, certs):
    ftps, mqtt = pair(certs)
    with ftps, mqtt:
        mqtt.start = "ack_only"
        result = run_blocking(bambu.upload(endpoint(ftps, mqtt, certs), f3mf(tmp_path), "a.gcode.3mf", start_print=True,
                                           options=QUICK))
    assert (result.selected, result.printing) == (True, False)
    assert "accepted the print command" in result.summary() and "not reported that it is printing" in result.note


def test_a_report_of_some_other_file_printing_is_not_taken_for_ours(tmp_path, certs):
    ftps, mqtt = pair(certs)
    with ftps, mqtt:
        mqtt.start = "ack_only"
        timer = threading.Timer(0.3, lambda: mqtt.emit(mqtt.status_report(gcode_state="RUNNING", gcode_file="other.gcode.3mf",
                                                                          subtask_name="other")))
        timer.start()
        result = run_blocking(bambu.upload(endpoint(ftps, mqtt, certs), f3mf(tmp_path), "a.gcode.3mf", start_print=True,
                                           options=QUICK))
        timer.join()
    assert result.printing is False and result.selected is True


def test_a_printer_that_sends_no_status_is_not_sent_the_command(tmp_path, certs):
    ftps, mqtt = pair(certs)
    with ftps, mqtt:
        mqtt.answer_pushall = mqtt.answer_version = False
        result = run_blocking(bambu.upload(endpoint(ftps, mqtt, certs), f3mf(tmp_path), "a.gcode.3mf", start_print=True,
                                           options=QUICK))
    assert result.printing is False and "no command was sent" in result.note and "serial number" in result.note
    assert project_file(mqtt) == []


def test_a_refused_mqtt_login_after_the_upload_stops_there_and_says_so(tmp_path, certs):
    ftps, mqtt = pair(certs)
    with ftps, mqtt:
        mqtt.access_code = "not-the-code"
        result = run_blocking(bambu.upload(endpoint(ftps, mqtt, certs), f3mf(tmp_path), "a.gcode.3mf", start_print=True,
                                           options=QUICK))
    assert len(ftps.stored) == 1                                       # the file is on the printer ...
    assert (result.selected, result.printing) == (False, False)        # ... and nothing is claimed about the print
    assert "Developer Mode" in result.note and "print was not started" in result.note and CODE not in result.note
    assert len(mqtt.connects) == 1 and mqtt.published == []            # one attempt: no retry, no other route


def test_a_certificate_change_between_ftps_and_mqtt_is_an_error_not_a_note(tmp_path, certs):
    with FakeFtpsServer(CODE, tls_files=certs[0]) as ftps, FakeMqttBroker(CODE, serial=SERIAL, tls_files=certs[1]) as mqtt:
        with pytest.raises(errors.TlsPinMismatch):
            run_blocking(bambu.upload(endpoint(ftps, mqtt, certs), f3mf(tmp_path), "a.gcode.3mf", start_print=True,
                                      options=QUICK))
    assert len(ftps.stored) == 1 and bytes(mqtt.raw_received) == b"" and mqtt.connects == []


def test_a_name_that_is_not_a_3mf_is_refused_before_anything_is_sent(tmp_path, certs):
    ftps, mqtt = pair(certs)
    with ftps, mqtt:
        with pytest.raises(errors.InvalidRequest, match="3mf"):
            run_blocking(bambu.upload(endpoint(ftps, mqtt, certs), f3mf(tmp_path, name="a.gcode"), "a.gcode", start_print=True))
        with pytest.raises(errors.InvalidRequest, match="3mf"):
            run_blocking(bambu.print_file(endpoint(ftps, mqtt, certs), "a.gcode"))
        with pytest.raises(errors.InvalidRequest, match="serial"):
            run_blocking(bambu.upload(endpoint(ftps, mqtt, certs, serial=""), f3mf(tmp_path), "a.gcode.3mf", start_print=True))
    assert (ftps.commands, mqtt.connects) == ([], [])


def test_cancel_while_waiting_for_the_printer_is_a_cancel_and_closes_the_connection(tmp_path, certs):
    token = CancelToken()
    ftps, mqtt = pair(certs)
    with ftps, mqtt:
        mqtt.start = "silent"
        ep = endpoint(ftps, mqtt, certs, cancel=token)
        gen = bambu.upload(ep, f3mf(tmp_path), "a.gcode.3mf", start_print=True,
                           options=bambu.PrintOptions(settle=0, status_wait=1, confirm_wait=30))
        with pytest.raises(errors.Cancelled):
            while True:
                next(gen)
                if project_file(mqtt):
                    token.cancel()
        for _ in range(100):
            if mqtt.connections_closed_by_client or mqtt.clean_disconnects:
                break
            threading.Event().wait(0.02)
    assert mqtt.clean_disconnects + mqtt.connections_closed_by_client == 1


def test_the_command_message_is_what_the_documentation_describes():
    cmd = bambu.project_file_command("Gehäuse.gcode.3mf", bambu.PrintOptions(plate=2), 9)["print"]
    assert cmd["sequence_id"] == "9" and cmd["param"] == "Metadata/plate_2.gcode" and cmd["url"] == "ftp:///Gehäuse.gcode.3mf"
    assert json.loads(json.dumps(cmd)) == cmd                          # plain JSON


def test_the_access_code_never_appears_in_results_notes_messages_or_logs(tmp_path, certs, caplog):
    redact.register(CODE)
    seen = []
    ftps, mqtt = pair(certs)
    with ftps, mqtt, caplog.at_level(logging.DEBUG):
        ep = endpoint(ftps, mqtt, certs)
        for start in ("accept", "reject", "silent", "ack_only"):
            mqtt.start = start
            result = run_blocking(bambu.upload(ep, f3mf(tmp_path), "a.gcode.3mf", start_print=True, options=QUICK))
            seen += [repr(result), result.summary(), result.note]
            mqtt.gcode_state = "IDLE"
        seen.append(run_blocking(bambu.test_connection(ep)).summary())
        mqtt.access_code = "other"
        for gen in (bambu.test_connection(ep), bambu.upload(ep, f3mf(tmp_path), "a.gcode.3mf", start_print=True, options=QUICK)):
            try:
                seen.append(repr(run_blocking(gen)))
            except errors.NetError as exc:
                seen += [str(exc), repr(exc)]
        assert all(CODE not in m.payload.decode() for m in mqtt.published)
    assert all(CODE not in text for text in seen) and CODE not in caplog.text
    redact.clear()
