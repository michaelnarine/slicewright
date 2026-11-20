# SPDX-License-Identifier: AGPL-3.0-only
"""Generate the add-on's settings-page layout from OrcaSlicer's Tab.cpp (04 section 6.5, 02 section 6).

Usage:
    gen_tab_layout.py --tab-cpp <Tab.cpp> [--overrides tab_layout_overrides.json]
                      --out <tab_layout.json> [--report <report.json>] [--strict]

Output (page, group and key order follow the source):
    {"process"|"filament"|"printer": [{"page": str, "icon": str, "groups":
        [{"title": str, "keys": [str], "wiki": {key: anchor}, "custom": str | None}]}]}

Mechanical extraction: the generator strips comments (recording commented-out calls), blanks
lambda bodies, then walks statements of TabPrint::build, TabFilament::build (inlining
add_filament_overrides_page) and TabPrinter::build_fff (inlining build_unregular_pages and
build_kinematics_page).  It understands add_options_page, new_optgroup, append_single_option_line
(string keys and Option variables), Line objects (append_option / label_path / append_line),
create_line_with_widget and append_option_line.  A widget line (create_line_with_widget, or an
Option built from a hand-made ConfigOptionDef) sets the group's "custom" to the widget's key; a
widget-backed schema key also stays in "keys" so ordering and coverage are preserved.
Everything it cannot extract is a finding in the report; --strict exits 3 on unexplained ones.

Overrides file (JSON, hand-maintained):
    {"ops": {"<tab>": [op, ...]},          # applied in order after extraction
     "page_order": {"<tab>": [page, ...]}, # listed pages first, in this order; others keep order
     "explained": [entry, ...],            # findings that need no op (deliberate Orca choices)
     "known_gaps": [entry, ...]}           # findings we could not resolve (acknowledged)
  op (every op has "reason"; page/group are matched by title, first match):
    {"op": "add_page", "page": {page, icon, groups}, "after": "<page>"|null}
    {"op": "remove_page", "page": P}      {"op": "rename_page", "page": P, "to": T}
    {"op": "add_group", "page": P, "group": {title, keys, wiki?, custom?}, "after": "<group>"|null}
    {"op": "remove_group", "page": P, "group": G}
    {"op": "add_keys", "page": P, "group": G, "keys": [..], "wiki": {k: a}?, "after": "<key>"|null}
    {"op": "remove_keys", "page": P, "group": G, "keys": [..]}
    {"op": "set_custom", "page": P, "group": G, "custom": str|null}
  any op may carry "explains": [entry, ...] (page/group/tab default from the op).
  entry: {"kind": K, "page"?: P, "group"?: G, "match"?: substring of the finding detail,
          "reason": str}   (a missing field matches anything).
Finding kinds: commented_out, option_object (auto), custom_widget (auto), custom_line,
conditional, unresolved_call, unresolved_option, empty_line, synthetic_option, dynamic_page_title.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TABS = {
    "process": ("TabPrint", "build", ()),
    "filament": ("TabFilament", "build", ("add_filament_overrides_page",)),
    "printer": ("TabPrinter", "build_fff", ("build_unregular_pages", "build_kinematics_page")),
}
AUTO_KINDS = {"option_object", "custom_widget"}
STR = r'"((?:[^"\\]|\\.)*)"'


def strip_comments(text):
    """Blank // and /* */ comments and #if 0 blocks (newlines kept). Return (clean, [(line, text)])."""
    out, comments, i, n, line = [], [], 0, len(text), 1
    while i < n:
        c = text[i]
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            seg = text[i:j + 1]
            out.append(seg)
            line += seg.count("\n")
            i = j + 1
        elif text.startswith("//", i) or text.startswith("/*", i):
            end = text.find("\n", i) if text[i + 1] == "/" else text.find("*/", i + 2) + 2
            end = n if end in (-1, 1) else end
            seg = text[i:end]
            comments.append((line, seg))
            out.append(re.sub(r"[^\n]", " ", seg))
            line += seg.count("\n")
            i = end
        else:
            out.append(c)
            line += c == "\n"
            i += 1
    lines, depth = "".join(out).split("\n"), 0
    for k, ln in enumerate(lines):
        s = ln.strip()
        if depth:
            comments.append((k + 1, ln))
            lines[k] = ""
        if re.match(r"#\s*if\b", s) and (depth or re.match(r"#\s*if\s+0\b", s)):
            depth += 1
            lines[k] = ""
        elif depth and re.match(r"#\s*endif", s):
            depth -= 1
    return "\n".join(lines), sorted(comments)


def match_close(text, i, open_c="{", close_c="}"):
    d = 0
    while i < len(text):
        c = text[i]
        if c == '"':
            i += 1
            while text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == open_c:
            d += 1
        elif c == close_c:
            d -= 1
            if d == 0:
                return i
        i += 1
    raise ValueError("unbalanced")


LAMBDA = re.compile(r"\[[^\[\]]*\]\s*(?:\((?:[^()]|\([^()]*\))*\))?\s*(?:mutable\s*)?\{")


def blank_lambdas(body):
    out, pos = [], 0
    while (m := LAMBDA.search(body, pos)):
        end = match_close(body, m.end() - 1)
        out += [body[pos:m.end()], re.sub(r"[^\n]", "", body[m.end():end])]
        pos = end
    return "".join(out) + body[pos:]


def function_body(clean, cls, fn):
    m = re.search(rf"\b{cls}::{fn}\s*\([^)]*\)\s*\{{", clean)
    if not m:
        raise ValueError(f"{cls}::{fn} not found")
    end = match_close(clean, m.end() - 1)
    return clean[m.end():end], clean.count("\n", 0, m.end()) + 1


def split_args(s):
    args, d, cur, i = [], 0, [], 0
    while i < len(s):
        c = s[i]
        if c == '"':
            j = i + 1
            while s[j] != '"':
                j += 2 if s[j] == "\\" else 1
            cur.append(s[i:j + 1])
            i = j
        elif c in "([{":
            d += 1
            cur.append(c)
        elif c in ")]}":
            d -= 1
            cur.append(c)
        elif c == "," and d == 0:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    if "".join(cur).strip():
        args.append("".join(cur).strip())
    return args


def calls(s, name):
    """Argument lists of every call `name(...)` in statement s."""
    res = []
    for m in re.finditer(rf"\b{name}\s*\(", s):
        try:
            res.append(split_args(s[m.end():match_close(s, m.end() - 1, "(", ")")]))
        except (ValueError, IndexError):
            pass
    return res


def parse_str(e):
    """Text of "x", L("x"), _L("x"), adjacent or '+'-joined literals; else None."""
    if e is None:
        return None
    e = e.strip()
    m = re.fullmatch(r"_?L\((.*)\)", e, re.S)
    e = m.group(1).strip() if m else e
    parts = re.fullmatch(rf'(?:{STR}\s*\+?\s*)+', e)
    if not parts:
        return None
    s = "".join(re.findall(STR, e))
    return re.sub(r"\\(.)", lambda k: {"n": "\n", "t": "\t"}.get(k.group(1), k.group(1)), s)


def statements(body, first_line):
    """Yield (line, kind, text): kind in stmt/open/close; blocks vs initializers decided by header."""
    items, buf, d, i, line, start = [], [], 0, 0, first_line, first_line

    def flush(kind="stmt"):
        t = "".join(buf).strip()
        if t:
            items.append((start, kind, t))
        buf.clear()

    while i < len(body):
        c = body[i]
        if not buf and not c.isspace():
            start = line
        if c == '"' or c == "'":
            j = i + 1
            while body[j] != c:
                j += 2 if body[j] == "\\" else 1
            buf.append(body[i:j + 1])
            i = j
        elif c in "([":
            d += 1
            buf.append(c)
        elif c in ")]":
            d -= 1
            buf.append(c)
        elif d == 0 and c == ";":
            flush()
        elif d == 0 and c == "{":
            h = "".join(buf).strip()
            if h == "" or re.match(r"(if|else|for|while|do|switch)\b", h):
                flush("open")
                if not h:
                    items.append((start, "open", ""))
            else:
                j = match_close(body, i)
                buf.append(body[i:j + 1])
                line += body.count("\n", i, j + 1)
                i = j + 1
                continue
        elif d == 0 and c == "}":
            flush()
            items.append((line, "close", ""))
        elif buf or not c.isspace():
            buf.append(c)
        line += c == "\n"
        i += 1
    flush()
    return items


class Gen:
    def __init__(self, tab, clean, comments, inline):
        self.tab, self.clean, self.inline = tab, clean, inline
        self.pages, self.findings, self.page, self.group = [], [], None, None
        self.vars, self.synth, self.lines, self.lambdas = {}, set(), {}, set()
        self.comments = comments

    def find(self, kind, line, detail):
        self.findings.append({"kind": kind, "tab": self.tab, "page": self.page and self.page["page"],
                              "group": self.group and self.group["title"], "line": line, "detail": detail})

    def run(self, cls, fn, depth=0, base=()):
        body, line0 = function_body(self.clean, cls, fn)
        self.lambdas |= set(re.findall(r"\bauto\s+(\w+)\s*=\s*\[", body))
        body = blank_lambdas(body)
        saved = self.vars, self.synth, self.lines  # variables are function-scoped
        self.vars, self.synth, self.lines = {}, set(), {}
        end_line = line0 + body.count("\n")
        cmts = [c for c in self.comments if line0 <= c[0] <= end_line]
        conds = list(base)
        for line, kind, text in sorted(statements(body, line0) + [(l, "comment", t) for l, t in cmts],
                                       key=lambda x: x[0]):
            if kind == "comment":
                self.comment(line, text)
            elif kind == "open":
                conds.append(text)
            elif kind == "close":
                conds and conds.pop()
            else:
                m = re.match(r"(?:else\s+)?(?:if|while|for)\s*\(", text)
                if m:
                    try:
                        e = match_close(text, m.end() - 1, "(", ")")
                    except ValueError:
                        continue
                    rest = text[e + 1:].strip()
                    if rest:
                        self.stmt(line, rest, tuple(c for c in conds if c) + (text[:e + 1],), depth)
                else:
                    self.stmt(line, text, tuple(c for c in conds if c), depth)
        self.vars, self.synth, self.lines = saved

    def comment(self, line, text):
        for key in re.findall(r'append_single_option_line\(\s*"(\w+)"', text):
            self.find("commented_out", line, f"key {key}")
        for key in re.findall(r'get_option\(\s*"(\w+)"', text):
            self.find("commented_out", line, f"option {key}")
        for t in re.findall(rf'add_options_page\(\s*(?:L\({STR}\)|{STR})', text):
            self.find("commented_out", line, f"page {t[0] or t[1]}")
        for t in re.findall(rf"new_optgroup\(\s*L\({STR}\)", text):
            self.find("commented_out", line, f"group {t}")
        for key in re.findall(r'append_option_line\(\s*\w+\s*,\s*"(\w+)"', text):
            self.find("commented_out", line, f"key {key}")
        for key in re.findall(r'create_line_with_widget\([^,]*,\s*"(\w+)"', text):
            self.find("commented_out", line, f"widget {key}")

    def key_of(self, e):
        """(key, synthetic) for a key expression, or None."""
        s = parse_str(e)
        if s is not None:
            return s, False
        m = re.search(rf"get_option\(\s*{STR}", e)
        if m:
            return m.group(1), False
        if e in self.vars:
            return self.vars[e], e in self.synth
        return None

    def add_key(self, line, ke, anchor, conds):
        if self.group is None:
            return self.find("unresolved_option", line, f"key outside group: {ke[0]}")
        if len(conds) > len(self.group["_conds"]):
            self.find("conditional", line, f"key {ke[0]} under {conds[len(self.group['_conds']):]}")
        if ke[1]:  # synthetic Option built from a hand-made ConfigOptionDef: a custom widget line
            self.find("synthetic_option", line, f"widget {ke[0]}")
            self.group["custom"] = self.group["custom"] or ke[0]
            return
        self.group["keys"].append(ke[0])
        if anchor:
            self.group["wiki"][ke[0]] = anchor

    def stmt(self, line, s, conds, depth):
        for a in calls(s, "add_options_page"):
            title = parse_str(a[0])
            if title is None:
                title = f"<dynamic:{a[0]}>"
                self.page = {"page": title, "icon": parse_str(a[1]) or "", "groups": [], "_conds": conds}
                self.group = None
                self.find("dynamic_page_title", line, f"title expression {a[0]}")
            else:
                self.page = {"page": title, "icon": parse_str(a[1]) or "", "groups": [], "_conds": conds}
                self.group = None
            self.pages.append(self.page)
            if conds:
                self.find("conditional", line, f"page under {conds}")
        for a in calls(s, "new_optgroup"):
            if self.page is None:
                return self.find("unresolved_option", line, "group outside page")
            t = parse_str(a[0])
            self.group = {"title": t if t is not None else a[0], "keys": [], "wiki": {}, "custom": None,
                          "_conds": conds}
            self.page["groups"].append(self.group)
            if len(conds) > len(self.page["_conds"]):
                self.find("conditional", line, f"group under {conds[len(self.page['_conds']):]}")
        if (m := re.match(r"(?:Option\s+|auto\s+)?(\w+)\s*=\s*\w+->get_option\(\s*\"(\w+)\"", s)):
            self.vars[m.group(1)], _ = m.group(2), self.synth.discard(m.group(1))
        for pat in (r"\b(\w+)\s*=\s*Option\s*\(\s*def\s*,\s*\"(\w+)\"", r"\bOption\s+(\w+)\s*\(\s*def\s*,\s*\"(\w+)\""):
            if (m := re.search(pat, s)):
                self.vars[m.group(1)] = m.group(2)
                self.synth.add(m.group(1))
        if (m := re.match(r"(?:(?:Line|auto)\s+)?(\w+)\s*=\s*(?:Line\s*)?\{", s)) or (m := re.match(r"Line\s+(\w+)\s*\{", s)):
            self.lines[m.group(1)] = {"keys": [], "path": None}
        if (m := re.match(rf"(\w+)\.label_path\s*=\s*{STR}", s)) and m.group(1) in self.lines:
            self.lines[m.group(1)]["path"] = m.group(2)
        for a in calls(s, "append_single_option_line"):
            ke = self.key_of(a[0])
            if ke is None:
                self.find("unresolved_option", line, f"append_single_option_line({a[0]})")
                continue
            if parse_str(a[0]) is None:
                self.find("option_object", line, f"{a[0]} -> {ke[0]}")
            self.add_key(line, ke, parse_str(a[1]) if len(a) > 1 else None, conds)
        for a in calls(s, "append_option"):
            nm = re.match(r"(\w+)\.append_option", s)
            ke = self.key_of(a[0])
            if nm and nm.group(1) in self.lines and ke:
                self.lines[nm.group(1)]["keys"].append(ke)
            else:
                self.find("unresolved_option", line, f"append_option({a[0]})")
        for a in calls(s, "append_line"):
            ln = self.lines.get(a[0])
            if not ln or not ln["keys"]:
                self.find("empty_line", line, f"append_line({a[0]})")
            for ke in (ln or {"keys": []})["keys"]:
                self.add_key(line, ke, ln["path"], conds)
        for a in calls(s, "append_option_line"):
            ke = self.key_of(a[1]) if len(a) > 1 else None
            if ke:
                self.add_key(line, ke, parse_str(a[2]) if len(a) > 2 else None, conds)
            else:
                self.find("unresolved_call", line, f"append_option_line({', '.join(a)})")
        for a in calls(s, "create_line_with_widget"):
            key = parse_str(a[1])
            self.find("custom_widget", line, f"widget line for {key}")
            self.add_key(line, (key, False), parse_str(a[2]), conds)
            if self.group["custom"]:
                self.find("custom_line", line, f"second custom widget in group: {key}")
            self.group["custom"] = self.group["custom"] or key
        if re.search(r"\.(near_label_widget|widget)\s*=|append_widget\(|build_preset_description_line\(", s):
            self.find("custom_line", line, s.split("=")[0].strip()[:60])
        for name in self.lambdas:
            if re.match(rf"{name}\s*\(", s):
                self.find("unresolved_call", line, f"helper lambda {name}()")
        if depth < 3:
            for name in self.inline:
                if re.search(rf"\b{name}\s*\(", s):
                    cls = TABS[self.tab][0]
                    self.run(cls, name, depth + 1, conds)


def extract(text, tab):
    clean, comments = strip_comments(text)
    cls, fn, inline = TABS[tab]
    g = Gen(tab, clean, comments, inline)
    g.run(cls, fn)
    pages = [{"page": p["page"], "icon": p["icon"], "groups": [
        {k: v for k, v in grp.items() if k != "_conds"} for grp in p["groups"]]} for p in g.pages]
    return pages, g.findings


def find_idx(items, key, title):
    for i, it in enumerate(items):
        if it[key] == title:
            return i
    raise KeyError(f"{key} {title!r} not found")


def apply_ops(pages, ops, stats):
    for op in ops:
        kind = op["op"]
        if kind == "add_page":
            i = 0 if op.get("after") is None else find_idx(pages, "page", op["after"]) + 1
            pages.insert(i, json.loads(json.dumps(op["page"])))
            stats["added"] += sum(len(g["keys"]) for g in op["page"]["groups"])
            continue
        pi = find_idx(pages, "page", op["page"])
        if kind == "remove_page":
            stats["removed"] += sum(len(g["keys"]) for g in pages[pi]["groups"])
            del pages[pi]
        elif kind == "rename_page":
            pages[pi]["page"] = op["to"]
        elif kind == "add_group":
            gs = pages[pi]["groups"]
            g = {"title": op["group"]["title"], "keys": list(op["group"]["keys"]),
                 "wiki": dict(op["group"].get("wiki", {})), "custom": op["group"].get("custom")}
            gs.insert(0 if op.get("after") is None else find_idx(gs, "title", op["after"]) + 1, g)
            stats["added"] += len(g["keys"])
        else:
            gs = pages[pi]["groups"]
            gi = find_idx(gs, "title", op["group"])
            if kind == "remove_group":
                stats["removed"] += len(gs[gi]["keys"])
                del gs[gi]
            elif kind == "add_keys":
                ks = gs[gi]["keys"]
                at = 0 if op.get("after") is None else ks.index(op["after"]) + 1
                ks[at:at] = op["keys"]
                gs[gi]["wiki"].update(op.get("wiki", {}))
                stats["added"] += len(op["keys"])
            elif kind == "remove_keys":
                for k in op["keys"]:
                    gs[gi]["keys"].remove(k)
                    gs[gi]["wiki"].pop(k, None)
                stats["removed"] += len(op["keys"])
            elif kind == "set_custom":
                gs[gi]["custom"] = op["custom"]
            else:
                raise ValueError(f"unknown op {kind}")


def entry_matches(e, f, default_tab=None, default_page=None, default_group=None):
    for field, dflt in (("tab", default_tab), ("page", default_page), ("group", default_group)):
        want = e.get(field, dflt)
        if want is not None and want != f[field]:
            return False
    return e.get("kind") in (None, f["kind"]) and e.get("match", "") in f["detail"]


def classify(findings, ov):
    explainers = [(e, "explained", {}) for e in ov.get("explained", [])]
    explainers += [(e, "known_gap", {}) for e in ov.get("known_gaps", [])]
    for tab, ops in ov.get("ops", {}).items():
        for op in ops:
            for e in op.get("explains", []):
                explainers.append((e, "explained", {"default_tab": tab, "default_page": op.get("page") if isinstance(op.get("page"), str) else None, "default_group": op.get("group") if isinstance(op.get("group"), str) else None}))
    for f in findings:
        f["status"] = "auto" if f["kind"] in AUTO_KINDS else "unexplained"
        for e, status, d in explainers:
            if entry_matches(e, f, **d):
                f["status"], f["reason"] = status, e.get("reason", "")
                break


def generate(text, overrides=None):
    ov = overrides or {}
    layout, findings, stats = {}, [], {}
    for tab in TABS:
        pages, fnd = extract(text, tab)
        st = stats[tab] = {"added": 0, "removed": 0}
        apply_ops(pages, ov.get("ops", {}).get(tab, []), st)
        order = ov.get("page_order", {}).get(tab)
        if order:
            rank = {t: i for i, t in enumerate(order)}
            pages.sort(key=lambda p: rank.get(p["page"], len(rank)))
        layout[tab] = pages
        findings += fnd
    classify(findings, ov)
    summary = {}
    for tab, pages in layout.items():
        summary[tab] = {"pages": len(pages), "groups": sum(len(p["groups"]) for p in pages),
                        "keys": sum(len(g["keys"]) for p in pages for g in p["groups"]),
                        "override_keys_added": stats[tab]["added"], "override_keys_removed": stats[tab]["removed"]}
    counts = {}
    for f in findings:
        k = f"{f['kind']}:{f['status']}"
        counts[k] = counts.get(k, 0) + 1
    return layout, {"summary": summary, "finding_counts": counts, "known_gaps": ov.get("known_gaps", []),
                    "findings": findings}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tab-cpp", required=True)
    ap.add_argument("--overrides", default=str(Path(__file__).with_name("tab_layout_overrides.json")))
    ap.add_argument("--out", required=True)
    ap.add_argument("--report")
    ap.add_argument("--strict", action="store_true", help="exit 3 if any finding is unexplained")
    a = ap.parse_args(argv)
    ov = json.loads(Path(a.overrides).read_text("utf-8")) if Path(a.overrides).exists() else {}
    layout, report = generate(Path(a.tab_cpp).read_text("utf-8", "replace"), ov)
    Path(a.out).write_text(json.dumps(layout, indent=1, ensure_ascii=False) + "\n", "utf-8")
    if a.report:
        Path(a.report).write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", "utf-8")
    bad = sum(f["status"] == "unexplained" for f in report["findings"])
    print(json.dumps(report["summary"]), f"unexplained={bad}", file=sys.stderr)
    return 3 if a.strict and bad else 0


if __name__ == "__main__":
    sys.exit(main())
