// SPDX-License-Identifier: AGPL-3.0-only
// The `Issue` of 04 section 2.6, as the engine thread stores it (plain C++, no Python objects) and as Python sees it.
#pragma once

#include <nanobind/nanobind.h>

#include <algorithm>
#include <string>
#include <vector>

namespace slicewright {

namespace nb = nanobind;

struct Issue {
    std::string level, code, message, opt_key, object_name;
};

inline nb::dict issue_dict(const Issue &i)
{
    nb::dict d;
    d["level"] = nb::str(i.level.c_str());
    d["code"] = nb::str(i.code.c_str());
    d["message"] = nb::str(i.message.c_str());
    d["opt_key"] = i.opt_key.empty() ? nb::object(nb::none()) : nb::object(nb::str(i.opt_key.c_str()));
    d["object_name"] = i.object_name.empty() ? nb::object(nb::none()) : nb::object(nb::str(i.object_name.c_str()));
    return d;
}

inline nb::list issue_list(const std::vector<Issue> &issues)
{
    nb::list out;
    for (const Issue &i : issues)
        out.append(issue_dict(i));
    return out;
}

inline bool has_error(const std::vector<Issue> &issues)
{
    return std::any_of(issues.begin(), issues.end(), [](const Issue &i) { return i.level == "error"; });
}

// De-duplicates on (level, code, message, opt_key, object_name), keeping the first occurrence's position.
inline void append_unique(std::vector<Issue> &into, const std::vector<Issue> &more)
{
    for (const Issue &m : more) {
        const bool seen = std::any_of(into.begin(), into.end(), [&](const Issue &i) {
            return i.level == m.level && i.code == m.code && i.message == m.message && i.opt_key == m.opt_key && i.object_name == m.object_name;
        });
        if (!seen)
            into.push_back(m);
    }
}

} // namespace slicewright
