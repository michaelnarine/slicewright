# SPDX-License-Identifier: AGPL-3.0-only
"""The sanitizer log checker: an allowlist of (file, kind), failing on new findings and on stale entries."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import summarize_sanitizer_log as s  # noqa: E402

ORCA = "/Users/runner/work/slicewright/slicewright/engine/build/orca-src/"
RUNNER = ORCA + "src/"
KNOWN = [
    RUNNER + "libslic3r/PrintConfig.cpp:10318:58: runtime error: nan is outside the range of representable values of type 'unsigned char'",
    RUNNER + "libslic3r/GCode.cpp:3699:25: runtime error: reference binding to null pointer of type 'Slic3r::SpiralVase'",
    ORCA + "deps_src/agg/agg_rasterizer_scanline_aa.h:243:55: runtime error: left shift of negative value -256",
    RUNNER + "libslic3r/PrintConfig.cpp:9992:52: runtime error: load of value 255, which is not a valid value for type 'bool'",
    RUNNER + "libslic3r/Config.hpp:2653:16: runtime error: downcast of address 0x6030004650d0 which does not point to an object of type 'Slic3r::ConfigOptionIntsTempl<false>'",
    RUNNER + "libslic3r/PrintConfig.cpp:9992:52: runtime error: member call on address 0x6030009a0f60 which does not point to an object of type 'Slic3r::ConfigOptionBoolsTempl<false>'",
    ORCA + "deps_src/miniz/miniz.c:1726:45: runtime error: applying zero offset to null pointer",
]


def run(lines):
    sites, asan = s.summarize(lines)
    return s.check(sites, asan)


def test_the_known_findings_pass():
    assert run(KNOWN) == []


def test_line_numbers_do_not_matter():
    assert run([l.replace(":10318:", ":10999:").replace(":3699:", ":1:").replace(":243:", ":7:").replace(":9992:", ":5:").replace(":2653:", ":9:").replace(":1726:", ":3:") for l in KNOWN]) == []


def test_a_new_finding_fails():
    extra = RUNNER + "libslic3r/Fill/Fill.cpp:77:9: runtime error: signed integer overflow: 2147483647 + 1 cannot be represented"
    problems = run(KNOWN + [extra])
    assert len(problems) == 1 and "Fill/Fill.cpp" in problems[0] and "integer-overflow" in problems[0]


def test_a_stale_entry_fails():
    problems = run([l for l in KNOWN if "GCode.cpp" not in l])
    assert len(problems) == 1 and "stale" in problems[0] and "GCode.cpp" in problems[0]


def test_asan_always_fails():
    problems = run(KNOWN + ["==1==ERROR: AddressSanitizer: heap-use-after-free on address 0x1"])
    assert any("AddressSanitizer" in p for p in problems)
