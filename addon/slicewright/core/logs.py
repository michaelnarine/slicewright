# SPDX-License-Identifier: GPL-3.0-or-later
"""Logging setup (03 section 9.4): console plus an optional rotating file.

``setup`` and ``teardown`` are idempotent and symmetric, so register/unregister
leaves the ``logging`` module exactly as it was.
"""
from __future__ import annotations

import logging
import logging.handlers
import os

from ..names import LOGGER_NAME

LEVELS = {"ERROR": logging.ERROR, "WARNING": logging.WARNING, "INFO": logging.INFO,
          "DEBUG": logging.DEBUG}
LOG_FILE_NAME = f"{LOGGER_NAME}.log"
_MARK = "_slicewright_handler"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def get_logger(suffix: str | None = None) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{suffix}" if suffix else LOGGER_NAME)


def setup(level: str | int = "WARNING", log_dir: str | None = None) -> None:
    """Attach a console handler and, if ``log_dir`` is given, a rotating file handler."""
    teardown()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(LEVELS.get(level, level) if isinstance(level, str) else level)
    logger.propagate = False
    formatter = logging.Formatter(FORMAT)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            handlers.append(logging.handlers.RotatingFileHandler(
                os.path.join(log_dir, LOG_FILE_NAME), maxBytes=1_000_000, backupCount=3,
                encoding="utf-8"))
        except OSError:
            logger.warning("cannot write logs to %s; logging to the console only", log_dir)
    for h in handlers:
        h.setFormatter(formatter)
        setattr(h, _MARK, True)
        logger.addHandler(h)


def set_level(level: str | int) -> None:
    logging.getLogger(LOGGER_NAME).setLevel(LEVELS.get(level, level) if isinstance(level, str) else level)


def teardown() -> None:
    """Remove and close every handler ``setup`` added; restore propagation."""
    logger = logging.getLogger(LOGGER_NAME)
    for h in list(logger.handlers):
        if getattr(h, _MARK, False):
            logger.removeHandler(h)
            h.close()
    logger.propagate = True
    logger.setLevel(logging.NOTSET)


def tail(log_dir: str | None, max_lines: int = 40) -> str:
    """The last lines of the log file, for "Copy diagnostics"."""
    if not log_dir:
        return ""
    try:
        with open(os.path.join(log_dir, LOG_FILE_NAME), encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-max_lines:])
    except OSError:
        return ""
