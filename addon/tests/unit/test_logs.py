# SPDX-License-Identifier: GPL-3.0-or-later
"""core/logs.py: setup and teardown leave the logging module as they found it."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from slicewright.core import logs


@pytest.fixture(autouse=True)
def _clean():
    yield
    logs.teardown()


def _ours():
    return [h for h in logging.getLogger("slicewright").handlers if getattr(h, "_slicewright_handler", False)]


def test_setup_adds_console_and_file_handlers(tmp_path):
    logs.setup("INFO", str(tmp_path / "logs"))
    assert len(_ours()) == 2
    logs.get_logger("test").info("hello world")
    for h in _ours():
        h.flush()
    assert "hello world" in (tmp_path / "logs" / logs.LOG_FILE_NAME).read_text()
    assert "hello world" in logs.tail(str(tmp_path / "logs"))


def test_setup_without_a_directory_is_console_only():
    logs.setup("WARNING")
    assert len(_ours()) == 1


def test_setup_is_idempotent():
    logs.setup("WARNING")
    logs.setup("DEBUG")
    assert len(_ours()) == 1
    assert logging.getLogger("slicewright").level == logging.DEBUG


def test_teardown_restores_the_logger(tmp_path):
    logger = logging.getLogger("slicewright")
    before = list(logger.handlers)
    logs.setup("INFO", str(tmp_path))
    logs.teardown()
    assert logger.handlers == before and logger.propagate is True
    assert logger.level == logging.NOTSET
    logs.teardown()   # twice is fine


def test_the_log_file_can_be_deleted_after_teardown(tmp_path):
    logs.setup("INFO", str(tmp_path))
    logs.get_logger().error("x")
    logs.teardown()
    Path(tmp_path / logs.LOG_FILE_NAME).unlink()   # fails on Windows if the handle is still open


def test_an_unwritable_directory_falls_back_to_the_console(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    logs.setup("INFO", str(blocker / "sub"))     # a path below a regular file
    assert len(_ours()) == 1


def test_set_level_and_tail_edge_cases(tmp_path):
    logs.setup("ERROR")
    logs.set_level("DEBUG")
    assert logging.getLogger("slicewright").level == logging.DEBUG
    assert logs.tail(None) == "" and logs.tail(str(tmp_path / "missing")) == ""


def test_levels_table():
    assert logs.LEVELS["ERROR"] > logs.LEVELS["WARNING"] > logs.LEVELS["INFO"] > logs.LEVELS["DEBUG"]
