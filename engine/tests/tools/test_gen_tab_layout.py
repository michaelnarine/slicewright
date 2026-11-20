# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for engine/tools/gen_tab_layout.py and check_tab_layout.py (real-Tab.cpp test needs SLICEWRIGHT_ORCA_TREE)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import check_tab_layout as chk  # noqa: E402
import gen_tab_layout as gen  # noqa: E402


def process(body):
    """Extract the process tab from a synthetic TabPrint::build body."""
    return gen.extract("void TabPrint::build()\n{\n" + body + "\n}\n", "process")


def keys(pages):
    return [k for p in pages for g in p["groups"] for k in g["keys"]]


def kinds(findings):
    return sorted(f["kind"] for f in findings)


BASE = 'auto page = add_options_page(L("Quality"), "icon_q");\n'


def test_pages_groups_wiki_order():
    pages, f = process(BASE + """
        auto optgroup = page->new_optgroup(L("Layer height"), L"param_x");
        optgroup->append_single_option_line("layer_height", "quality#lh");
        optgroup->append_single_option_line("initial_layer_print_height");
        optgroup = page->new_optgroup(L("Seam"));
        optgroup->append_single_option_line("seam_gap","quality#seam");
        page = add_options_page(L("Speed"), "icon_s");
        optgroup = page->new_optgroup(L("Other"));
        optgroup->append_single_option_line("travel_speed");
    """)
    assert [p["page"] for p in pages] == ["Quality", "Speed"]
    assert [p["icon"] for p in pages] == ["icon_q", "icon_s"]
    assert [g["title"] for g in pages[0]["groups"]] == ["Layer height", "Seam"]
    assert pages[0]["groups"][0] == {"title": "Layer height", "keys": ["layer_height", "initial_layer_print_height"],
                                     "wiki": {"layer_height": "quality#lh"}, "custom": None}
    assert f == []


def test_line_comments_are_reported_not_extracted():
    pages, f = process(BASE + """
        auto optgroup = page->new_optgroup(L("G"));
        optgroup->append_single_option_line("a");
        // optgroup->append_single_option_line("hidden_one");
        //optgroup->append_single_option_line("hidden_two", "x#y");
        optgroup->append_single_option_line("b"); // trailing append_single_option_line("not_this")
    """)
    assert keys(pages) == ["a", "b"]
    assert sorted(x["detail"] for x in f) == ["key hidden_one", "key hidden_two", "key not_this"]
    assert {x["kind"] for x in f} == {"commented_out"}
    assert f[0]["group"] == "G" and f[0]["page"] == "Quality"


def test_block_comments_and_slashes_in_strings():
    pages, f = process(BASE + """
        auto optgroup = page->new_optgroup(L("G"));
        /* optgroup->append_single_option_line("blocked");
           optgroup->append_single_option_line("blocked2"); */
        optgroup->append_single_option_line("a", "http://x/y#z");
        optgroup->append_single_option_line("b");
    """)
    assert keys(pages) == ["a", "b"]
    assert pages[0]["groups"][0]["wiki"] == {"a": "http://x/y#z"}
    assert [x["detail"] for x in f] == ["key blocked", "key blocked2"]


def test_preprocessor_if0_block_is_commented_out():
    pages, f = process(BASE + """
        auto optgroup = page->new_optgroup(L("G"));
#if 0
        optgroup->append_single_option_line("gone");
#endif
        optgroup->append_single_option_line("kept");
    """)
    assert keys(pages) == ["kept"]
    assert [x["detail"] for x in f] == ["key gone"]


def test_option_objects_resolve_to_keys():
    pages, f = process(BASE + """
        auto optgroup = page->new_optgroup(L("G"));
        Option option = optgroup->get_option("code_key");
        option.opt.full_width = true;
        optgroup->append_single_option_line(option, "p#code");
        option = optgroup->get_option("other_key");
        optgroup->append_single_option_line(option);
        optgroup->append_single_option_line(optgroup->get_option("direct_key"));
        optgroup->append_single_option_line(mystery);
    """)
    assert keys(pages) == ["code_key", "other_key", "direct_key"]
    assert pages[0]["groups"][0]["wiki"] == {"code_key": "p#code"}
    assert kinds(f) == ["option_object"] * 3 + ["unresolved_option"]


def test_line_objects_and_label_path():
    pages, f = process(BASE + """
        auto optgroup = page->new_optgroup(L("G"));
        Line line = { L("Bridge"), L("tip, with comma") };
        line.label_path = "speed#bridge";
        line.append_option(optgroup->get_option("bridge_speed"));
        line.append_option(optgroup->get_option("internal_bridge_speed", 0));
        optgroup->append_line(line);
        line = { L("Next"), "" };
        line.append_option(optgroup->get_option("second"));
        optgroup->append_line(line);
    """)
    g = pages[0]["groups"][0]
    assert g["keys"] == ["bridge_speed", "internal_bridge_speed", "second"]
    assert g["wiki"] == {"bridge_speed": "speed#bridge", "internal_bridge_speed": "speed#bridge"}


def test_custom_widget_line_sets_custom_and_keeps_key():
    pages, f = process(BASE + """
        auto optgroup = page->new_optgroup(L("G"));
        create_line_with_widget(optgroup.get(), "printable_area", "bed#shape", [this](wxWindow* parent) {
            optgroup->append_single_option_line("inside_lambda_is_ignored");
            return create_bed_shape_widget(parent);
        });
        optgroup->append_single_option_line("after");
    """)
    g = pages[0]["groups"][0]
    assert g["keys"] == ["printable_area", "after"] and g["custom"] == "printable_area"
    assert g["wiki"] == {"printable_area": "bed#shape"}
    assert kinds(f) == ["custom_widget"]


def test_conditional_pages_and_keys_are_flagged():
    pages, f = process("""
        auto page = add_options_page(L("Always"), "i");
        auto optgroup = page->new_optgroup(L("G"));
        optgroup->append_single_option_line("a");
        if (printer_technology() == ptFFF) {
            page = add_options_page(L("FFF only"), "i");
            optgroup = page->new_optgroup(L("H"));
            optgroup->append_single_option_line("b");
        }
        optgroup = page->new_optgroup(L("Tail"));
        if (is_bbl)
            optgroup->append_single_option_line("c");
    """)
    assert keys(pages) == ["a", "b", "c"]
    cond = [x for x in f if x["kind"] == "conditional"]
    assert len(cond) == 2
    assert "ptFFF" in cond[0]["detail"] and cond[0]["page"] == "FFF only"
    assert "key c" in cond[1]["detail"]


def test_dynamic_page_title_and_empty_line_reported():
    pages, f = process("""
        const wxString& name = foo();
        auto page = add_options_page(name, "i", true);
        auto optgroup = page->new_optgroup(L("G"));
        Line line = { "x", "" };
        optgroup->append_line(line);
    """)
    assert pages[0]["page"] == "<dynamic:name>"
    assert kinds(f) == ["dynamic_page_title", "empty_line"]


def test_escaped_quotes_L_wrapping_and_concatenation():
    pages, _ = process("""
        auto page = add_options_page(L("Say \\"hi\\""), "i");
        auto optgroup = page->new_optgroup(L("First " "second"));
        optgroup->append_single_option_line("k", "a#" "b");
    """)
    assert pages[0]["page"] == 'Say "hi"'
    assert pages[0]["groups"][0]["title"] == "First second"
    assert pages[0]["groups"][0]["wiki"] == {"k": "a#b"}


def test_helper_lambda_loop_is_unresolved_and_inline_call_runs():
    text = """
void TabFilament::add_filament_overrides_page()
{
    auto page = add_options_page(L("Inlined"), "i");
    auto og = page->new_optgroup(L("R"));
    auto helper = [this, og](const std::string& k) { og->append_single_option_line(k); };
    for (const std::string k : { "a", "b" })
        helper(k);
}
void TabFilament::build()
{
    auto page = add_options_page(L("Main"), "i");
    add_filament_overrides_page();
}
"""
    pages, f = gen.extract(text, "filament")
    assert [p["page"] for p in pages] == ["Main", "Inlined"]
    assert kinds(f) == ["unresolved_call"]


def test_overrides_ops_and_classification():
    text = "void TabPrint::build()\n{\n" + BASE + """
        auto optgroup = page->new_optgroup(L("G"));
        optgroup->append_single_option_line("a");
        // optgroup->append_single_option_line("hidden");
    }
void TabFilament::build() {}
void TabPrinter::build_fff() {}
"""
    ov = {"ops": {"process": [
        {"op": "add_keys", "page": "Quality", "group": "G", "keys": ["x", "y"], "after": None, "reason": "r"},
        {"op": "remove_keys", "page": "Quality", "group": "G", "keys": ["a"], "reason": "r"},
        {"op": "add_group", "page": "Quality", "group": {"title": "N", "keys": ["z"], "custom": "w"}, "reason": "r"},
        {"op": "add_page", "page": {"page": "P2", "icon": "i", "groups": []}, "after": None, "reason": "r"}]},
        "page_order": {"process": ["Quality", "P2"]}, "explained": [{"kind": "commented_out", "reason": "hidden"}]}
    layout, report = gen.generate(text, ov)
    assert [p["page"] for p in layout["process"]] == ["Quality", "P2"]
    assert [g["title"] for g in layout["process"][0]["groups"]] == ["N", "G"]
    assert layout["process"][0]["groups"][1]["keys"] == ["x", "y"]
    assert report["summary"]["process"]["override_keys_added"] == 3
    assert report["summary"]["process"]["override_keys_removed"] == 1
    assert [f["status"] for f in report["findings"]] == ["explained"]


def test_deterministic_output():
    text = "void TabPrint::build()\n{\n" + BASE + 'auto g = page->new_optgroup(L("G")); g->append_single_option_line("a");\n}\n'
    text += "void TabFilament::build() {}\nvoid TabPrinter::build_fff() {}\n"
    a, b = gen.generate(text), gen.generate(text)
    assert json.dumps(a) == json.dumps(b)


GOOD = {"process": [{"page": "P", "icon": "i", "groups": [{"title": "G", "keys": ["a"], "wiki": {"a": "x"}, "custom": None}]}],
        "filament": [], "printer": []}


def test_checker_accepts_good_and_reports_unplaced():
    errs, cov, _ = chk.check(GOOD, {"a", "b"}, {"process": {"a", "b"}, "filament": set(), "printer": set()})
    assert errs == [] and cov["process"] == ["b"]


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d["process"][0]["groups"][0].update(keys=["a", "a"]), "duplicate key a"),
    (lambda d: d["process"][0]["groups"][0].update(wiki={"zz": "x"}), "wiki key zz"),
    (lambda d: d["process"][0]["groups"][0].update(custom=3), "custom must be str or null"),
    (lambda d: d["process"][0]["groups"][0].update(keys=["nope"], wiki={}), "not in the config schema"),
    (lambda d: d["process"][0].pop("icon"), "bad page object"),
    (lambda d: d.pop("printer"), "exactly the tabs"),
])
def test_checker_violations(mutate, needle):
    d = json.loads(json.dumps(GOOD))
    mutate(d)
    errs, _, _ = chk.check(d, {"a"})
    assert any(needle in e for e in errs), errs


def test_schema_key_parsing():
    src = '''def = this->add("alpha", coFloat);\n def = this->add_nullable("beta", coInt);
    def = this->add("machine_max_speed_" + axis.name, coFloats);
    { "x", { 1 }, { 2 } }, { "y", { 1 } }
    const std::vector<std::string> filament_extruder_override_keys = { "filament_z_hop", // c
      "filament_wipe" };'''
    assert chk.schema_keys(src) == {"alpha", "beta", "machine_max_speed_x", "machine_max_speed_y",
                                    "filament_z_hop", "filament_wipe"}


@pytest.mark.skipif(not os.environ.get("SLICEWRIGHT_ORCA_TREE"), reason="SLICEWRIGHT_ORCA_TREE not set")
def test_real_tab_cpp(tmp_path):
    tree = Path(os.environ["SLICEWRIGHT_ORCA_TREE"])
    out, rep = tmp_path / "tab_layout.json", tmp_path / "report.json"
    rc = gen.main(["--tab-cpp", str(tree / "src/slic3r/GUI/Tab.cpp"), "--out", str(out), "--report", str(rep), "--strict"])
    assert rc == 0, "unexplained findings in report"
    layout = json.loads(out.read_text("utf-8"))
    assert set(layout) == {"process", "filament", "printer"}
    assert len(layout["process"]) >= 5 and len(layout["filament"]) >= 6 and len(layout["printer"]) >= 5
    assert rep.exists()
    assert chk.main(["--layout", str(out), "--orca-tree", str(tree)]) == 0
