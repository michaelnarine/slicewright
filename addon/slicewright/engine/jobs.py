# SPDX-License-Identifier: GPL-3.0-or-later
"""Slicing job orchestration without Blender (03 section 6.1).

No ``bpy`` here: everything runs in plain Python against ``sc`` (the engine module, real or fake),
so the state machine is unit-tested without Blender. The job states are defined first because the
Scene's runtime job state (``blender/props.py``) mirrors them.
"""
from __future__ import annotations

IDLE, VALIDATING, SLICING, DONE, FAILED, CANCELLED = (
    "idle", "validating", "slicing", "done", "failed", "cancelled")

# (state, label) in the order the Scene enum lists them.
JOB_STATES = (
    (IDLE, "Idle"), (VALIDATING, "Validating"), (SLICING, "Slicing"),
    (DONE, "Done"), (FAILED, "Failed"), (CANCELLED, "Cancelled"))
ACTIVE_STATES = (VALIDATING, SLICING)
