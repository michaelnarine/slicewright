# SPDX-License-Identifier: AGPL-3.0-only
"""check_symbols.py: parsing and the forbidden-family patterns (no toolchain needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import check_symbols as cs  # noqa: E402

NM = """\
libfoo.a(a.o):
__ZN5Slic3r5PrintC1Ev T 1000 0
_PyInit_slicewright_engine T 2000 0
__ZN2cv3MatC1Ev U 0 0
_MD5_Init U 0 0
_mcDispatch U 0 0
__ZN12TopoDS_Shape4NullEv U 0 0
_memcpy U 0 0
"""


def test_parse_skips_member_headers():
    syms = cs.parse_nm(NM)
    assert ("_PyInit_slicewright_engine", "T") in syms
    assert all(not n.endswith(":") for n, _ in syms)


def test_each_family_is_detected():
    hits = cs.forbidden_hits(cs.parse_nm(NM))
    assert set(hits) == {"OpenCV", "OpenSSL", "mcut", "OCCT"}


def test_clean_symbols_pass():
    clean = cs.parse_nm("__ZN5Slic3r5PrintC1Ev T 1 0\n_PyInit_x T 2 0\n__ZNSt3__16vectorIiE U 0 0\n")
    assert cs.forbidden_hits(clean) == {}


def test_boost_md5_shim_is_not_openssl():
    # C++ linkage: mangled, so it must not match the C API pattern.
    assert cs.forbidden_hits(cs.parse_nm("__Z8MD5_InitP7MD5_CTX T 1 0\n")) == {}


def test_only_pyinit_exports():
    syms = cs.parse_nm("_PyInit_m T 1 0\n__ZN3FooC1Ev T 2 0\n_printf U 0 0\n_weak w 0 0\n")
    assert cs.non_pyinit_exports(syms) == ["__ZN3FooC1Ev"]


def test_bad_dependencies():
    out = "mod.so:\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n\t@rpath/libtbb.dylib (compatibility version 1.0.0)\n"
    assert cs.bad_dependencies(out) == ["@rpath/libtbb.dylib"]
