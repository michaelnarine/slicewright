# SPDX-License-Identifier: GPL-3.0-or-later
"""``core.preset_names``: readable preset names, ids unchanged (03 section 3.7)."""
from slicewright.core import preset_names as pn


def test_a_system_id_loses_its_internal_prefix_and_keeps_the_whole_name():
    assert pn.display_name("sys:BBL/0.20mm Standard @BBL A1") == "0.20mm Standard @BBL A1"
    assert pn.display_name("sys:BBL/Bambu PLA Matte @BBL A1") == "Bambu PLA Matte @BBL A1"
    assert pn.display_name("sys:Acme/Generic/PLA") == "Generic/PLA"             # only the first slash splits the vendor


def test_a_user_preset_is_marked_and_other_text_is_untouched():
    assert pn.display_name("user:My PLA") == "My PLA (user)"
    assert pn.display_name("") == "" and pn.display_name("Some name") == "Some name"
    assert pn.display_name("sys:odd") == "odd" and pn.display_name("sys:V/") == "V/"


def test_vendor_and_tooltip_carry_the_full_id():
    assert pn.vendor_of("sys:BBL/X") == "BBL" and pn.vendor_of("user:X") == "" and pn.vendor_of("sys:odd") == ""
    tip = pn.tooltip("Process", "sys:BBL/0.20mm Standard @BBL A1")
    assert tip.splitlines()[0] == "Process: 0.20mm Standard @BBL A1"
    assert "Vendor: BBL" in tip and "Preset id: sys:BBL/0.20mm Standard @BBL A1" in tip
    assert "No process chosen" in pn.tooltip("Process", "")
    assert "Preset id: user:Mine" in pn.tooltip("Filament 2", "user:Mine")
