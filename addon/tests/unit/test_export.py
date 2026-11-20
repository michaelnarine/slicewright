# SPDX-License-Identifier: GPL-3.0-or-later
"""File names, folders and atomic writes of the export (``core/export.py``, 03 section 8.1), without Blender."""
from __future__ import annotations

import os

import pytest
from slicewright.core import export as ex


# -- names -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Cube_PLA_20m2s.gcode", "Cube_PLA_20m2s.gcode"),
    ("a/b\\c:d*e?f\"g<h>i|j", "a_b_c_d_e_f_g_h_i_j"),
    ("name. ", "name"),
    (".hidden", "hidden"),
    ("CON", "_CON"), ("nul.txt", "_nul.txt"), ("Com3", "_Com3"), ("console", "console"),
    ("", "slicewright"), ("   ", "slicewright"), ("...", "slicewright"),
    ("tab\there\n", "tab_here_"),
])
def test_safe_filename(name, expected):
    assert ex.safe_filename(name) == expected


def test_safe_filename_cuts_overlong_names_on_a_character_boundary():
    out = ex.safe_filename("é" * 200)
    assert len(out.encode("utf-8")) <= ex.MAX_NAME and out and set(out) == {"é"}


def test_the_gcode_name_only_ever_appends_an_extension():
    assert ex.gcode_filename("Cube_PLA_20m2s.gcode") == "Cube_PLA_20m2s.gcode"
    assert ex.gcode_filename("my.part_PLA_20m2s") == "my.part_PLA_20m2s.gcode"        # the dot is part of the name
    assert ex.gcode_filename("v1.2.3") == "v1.2.3.gcode"
    assert ex.gcode_filename("job.GCODE") == "job.GCODE" and ex.gcode_filename("job.gco") == "job.gco"
    assert ex.gcode_filename("a/b") == "a_b.gcode"                                      # the format can emit a slash


def test_the_3mf_name_swaps_the_gcode_extension_and_keeps_every_other_dot():
    assert ex.threemf_filename("Cube_PLA_20m2s.gcode") == "Cube_PLA_20m2s.gcode.3mf"
    assert ex.threemf_filename("my.part_PLA_1h.gcode") == "my.part_PLA_1h.gcode.3mf"
    assert ex.threemf_filename("my.part") == "my.part.gcode.3mf"
    assert ex.threemf_filename("x.gcode.3mf") == "x.gcode.3mf" and ex.threemf_filename("x.3MF") == "x.gcode.3mf"
    assert ex.threemf_filename("x.gcode.gcode") == "x.gcode.gcode.3mf"                  # only one extension is ours
    assert ex.threemf_filename("") == "slicewright.gcode.3mf"


def test_with_extension_fixes_the_typed_name_and_leaves_the_folder_alone():
    sep = os.sep
    assert ex.with_extension(f"{sep}out{sep}my.part", "gcode") == f"{sep}out{sep}my.part.gcode"
    assert ex.with_extension(f"{sep}out{sep}my.part", "3mf") == f"{sep}out{sep}my.part.gcode.3mf"
    assert ex.with_extension(f"{sep}out.d{sep}run.gcode", "3mf") == f"{sep}out.d{sep}run.gcode.3mf"
    assert ex.with_extension(f"{sep}out.d{sep}run.gcode.3mf", "3mf") == f"{sep}out.d{sep}run.gcode.3mf"


def test_the_basename_drops_only_dot_blend():
    assert ex.input_basename("/p/Benchy.blend") == "Benchy"
    assert ex.input_basename("/p/my.part.blend") == "my.part"
    assert ex.input_basename("/p/v1.2.3 final.BLEND") == "v1.2.3 final"
    assert ex.input_basename("/p/notes.txt") == "notes.txt" and ex.input_basename("/p/.blend") == ex.UNTITLED
    assert ex.input_basename("") == ex.UNTITLED and ex.input_basename(None) == ex.UNTITLED


def test_the_dialog_opens_next_to_the_blend_then_in_the_last_export_folder_then_home(tmp_path):
    blend_dir, last, home = tmp_path / "proj", tmp_path / "last", str(tmp_path / "home")
    blend_dir.mkdir(), last.mkdir()
    blend = str(blend_dir / "a.blend")
    assert ex.start_directory(blend, str(last), home) == str(blend_dir)
    assert ex.start_directory(None, str(last), home) == str(last)                      # never saved: the last folder
    assert ex.start_directory("", str(last), home) == str(last)
    assert ex.start_directory(str(tmp_path / "gone" / "a.blend"), str(last), home) == str(last)
    assert ex.start_directory(None, str(tmp_path / "gone"), home) == home              # a deleted last folder is skipped
    assert ex.start_directory(None, None, home) == home


def test_default_path_joins_the_folder_and_the_engines_name():
    assert ex.default_path("/out", "Cube_PLA_20m2s.gcode", "gcode") == os.path.join("/out", "Cube_PLA_20m2s.gcode")
    assert ex.default_path("/out", "Cube_PLA_20m2s.gcode", "3mf") == os.path.join("/out", "Cube_PLA_20m2s.gcode.3mf")


# -- writing -----------------------------------------------------------------------------------------------------

def writer(data: bytes):
    def write(path):
        with open(path, "wb") as f:
            f.write(data)
    return write


def test_an_atomic_write_creates_the_file_and_leaves_no_temp_behind(tmp_path):
    dest = str(tmp_path / "a.gcode")
    assert ex.atomic_write(dest, writer(b"G1 X1\n")) == 6
    assert (tmp_path / "a.gcode").read_bytes() == b"G1 X1\n"
    assert os.listdir(tmp_path) == ["a.gcode"]


def test_the_writer_sees_a_hidden_sibling_in_the_same_folder(tmp_path):
    seen = []
    dest = str(tmp_path / "a.gcode")
    ex.atomic_write(dest, lambda tmp: (seen.append(tmp), writer(b"x")(tmp)))
    assert os.path.dirname(seen[0]) == str(tmp_path) and os.path.basename(seen[0]).startswith(".a.gcode.")
    assert seen[0].endswith(".part")


def test_an_existing_file_is_refused_unless_overwrite_is_confirmed(tmp_path):
    dest = tmp_path / "a.gcode"
    dest.write_bytes(b"old")
    with pytest.raises(ex.ExportError) as err:
        ex.atomic_write(str(dest), writer(b"new"))
    assert err.value.exists and "already exists" in str(err.value)
    assert dest.read_bytes() == b"old" and os.listdir(tmp_path) == ["a.gcode"]          # untouched, no litter
    ex.atomic_write(str(dest), writer(b"new"), overwrite=True)
    assert dest.read_bytes() == b"new"


def test_a_failed_write_keeps_the_file_it_would_have_replaced_and_removes_the_temp(tmp_path):
    dest = tmp_path / "a.gcode"
    dest.write_bytes(b"old")

    def boom(path):
        writer(b"half")(path)
        raise RuntimeError("engine fell over")
    with pytest.raises(RuntimeError):
        ex.atomic_write(str(dest), boom, overwrite=True)
    assert dest.read_bytes() == b"old" and os.listdir(tmp_path) == ["a.gcode"]


def test_a_write_that_makes_no_file_is_an_error(tmp_path):
    with pytest.raises(ex.ExportError, match="no file"):
        ex.atomic_write(str(tmp_path / "a.gcode"), lambda path: None)
    assert os.listdir(tmp_path) == []


def test_a_missing_folder_or_a_folder_in_the_way_is_reported(tmp_path):
    with pytest.raises(ex.ExportError, match="does not exist"):
        ex.atomic_write(str(tmp_path / "nope" / "a.gcode"), writer(b"x"))
    (tmp_path / "dir.gcode").mkdir()
    with pytest.raises(ex.ExportError, match="is a folder"):
        ex.atomic_write(str(tmp_path / "dir.gcode"), writer(b"x"), overwrite=True)


def test_a_file_that_appears_while_writing_is_not_overwritten_unasked(tmp_path):
    dest = tmp_path / "a.gcode"

    def slow(path):
        writer(b"mine")(path)
        dest.write_bytes(b"someone else")               # created by another program while we were writing
    with pytest.raises(ex.ExportError) as err:
        ex.atomic_write(str(dest), slow)
    assert err.value.exists and dest.read_bytes() == b"someone else" and os.listdir(tmp_path) == ["a.gcode"]


def test_an_os_error_becomes_an_export_error_naming_the_file(tmp_path):
    def denied(path):
        raise PermissionError(13, "Permission denied")
    with pytest.raises(ex.ExportError, match="Could not write a.gcode: Permission denied"):
        ex.atomic_write(str(tmp_path / "a.gcode"), denied)
