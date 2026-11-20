# SPDX-License-Identifier: GPL-3.0-or-later
"""Child process of check_printers.py (not a ``check_*`` script: the runner does not start it by itself).

Adds a printer with a secret and saves the user preferences, so the parent can search the saved file. It refuses to save
unless Blender's config directory is inside ``SLICEWRIGHT_TEST_CONFIG_ROOT``: ``save_userpref`` would otherwise overwrite
the real user's preferences (Blender ignores ``BLENDER_USER_RESOURCES`` when that directory does not exist).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_common  # noqa: E402,F401

import bpy  # noqa: E402

root = os.path.realpath(os.environ["SLICEWRIGHT_TEST_CONFIG_ROOT"])
config = os.path.realpath(bpy.utils.user_resource("CONFIG"))
if os.path.commonpath([root, config]) != root:
    sys.stderr.write("refusing to save preferences outside the test directory\n")
    sys.exit(3)

os.environ["SLICEWRIGHT_ENGINE_MODULE"] = "fake_engine"
import slicewright  # noqa: E402
from slicewright import prefs  # noqa: E402
from slicewright.blender import paths, secrets_rt  # noqa: E402
from slicewright.core.network import secrets as core  # noqa: E402

paths.set_override(os.path.join(root, "addon-user"))
slicewright.register()


class Memory(core.SecretStore):                    # keeps the secret in memory only: no file may hold it
    name = "test keychain"

    def __init__(self):
        self.items = {}

    def available(self):
        return True

    def get(self, ref):
        return self.items.get(ref)

    def set(self, ref, secret):
        self.items[ref] = secret

    def delete(self, ref):
        return self.items.pop(ref, None) is not None


secrets_rt.set_manager(core.SecretManager(Memory(), core.FileSecretStore(os.path.join(root, "addon-user", "secrets.json"))))
entry = bpy.context.preferences.addons.new()
entry.module = prefs._package()
p = bpy.context.preferences.addons[prefs._package()].preferences
bpy.ops.slicewright.printer_add(kind="octoprint")
pg = p.printers[0]
pg.host = os.environ["SLICEWRIGHT_TEST_HOST"]
bpy.ops.slicewright.printer_secret("EXEC_DEFAULT", printer_id=pg.id, secret=os.environ["SLICEWRIGHT_TEST_SECRET_VALUE"])
bpy.ops.wm.save_userpref()
print("SAVED", pg.secret_ref)
slicewright.unregister()
