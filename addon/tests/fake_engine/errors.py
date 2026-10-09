# SPDX-License-Identifier: GPL-3.0-or-later
"""Exception hierarchy, 04 section 7."""
from __future__ import annotations


class Error(Exception):
    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message


class Cancelled(Error):
    pass


class Busy(Error):
    pass


class StateError(Error):
    def __init__(self, message: str = "", state: str | None = None) -> None:
        super().__init__(message)
        self.state = state


class ConfigError(Error):
    def __init__(self, message: str = "", key: str | None = None, value: str | None = None) -> None:
        super().__init__(message)
        self.key = key
        self.value = value


class ValidationError(Error):
    def __init__(self, message: str = "", issues: list[dict] | None = None) -> None:
        super().__init__(message)
        self.issues = list(issues or [])
        first = next((i for i in self.issues if i.get("level") == "error"), None)
        self.opt_key = first.get("opt_key") if first else None
        self.object_name = first.get("object_name") if first else None


class SliceError(Error):
    def __init__(self, message: str = "", object_name: str | None = None) -> None:
        super().__init__(message)
        self.object_name = object_name


class ArrangeError(Error):
    def __init__(self, message: str = "", object_names: list[str] | None = None) -> None:
        super().__init__(message)
        self.object_names = list(object_names or [])


class EngineError(Error):
    def __init__(self, message: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


def issue(level: str, code: str, message: str, opt_key: str | None = None,
          object_name: str | None = None) -> dict:
    """Build an Issue dict (04 section 2.6)."""
    return {"level": level, "code": code, "message": message,
            "opt_key": opt_key, "object_name": object_name}
