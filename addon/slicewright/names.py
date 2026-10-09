# SPDX-License-Identifier: GPL-3.0-or-later
"""The product name, in one place (03 header: "all come from one constant").

``Slicewright`` is a placeholder until the name check in the plan (section 1,
Phase 0 admin) is final. Everything user-visible or prefixed derives from these
constants, and ``blender_manifest.toml`` is checked against them by a unit test.
The package directory name must equal ``PACKAGE_ID``; renaming means renaming
that directory and editing this file and the manifest.
"""

PRODUCT_NAME = "Slicewright"
PACKAGE_ID = "slicewright"
OP_PREFIX = PACKAGE_ID.upper()          # bl_idname prefix: SLICEWRIGHT_OT_..., SLICEWRIGHT_PT_...
ENGINE_MODULE = "slicewright_engine"    # the engine's import name (04 section 2.1)
LOGGER_NAME = PACKAGE_ID
TAB_NAME = "Slicer"                     # N-panel tab (03 section 1.1)
