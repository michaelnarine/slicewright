// SPDX-License-Identifier: AGPL-3.0-only
// SliceResult (04 section 5): the finished slice. The numeric arrays live in a ResultStore that the numpy arrays
// share through a capsule, so they stay valid as long as any array does, after the result and the job are gone.
#pragma once

#include <nanobind/nanobind.h>

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "issues.hpp"
#include "stats.hpp"
#include "store.hpp"

namespace slicewright {

namespace nb = nanobind;

class SliceResult {
public:
    std::string              gcode_path;
    std::vector<std::string> objects;
    std::vector<Issue>       warnings;
    Stats                    stats;  // stats.threads is the size of the TBB arena the job ran in
    std::shared_ptr<ResultStore> store = std::make_shared<ResultStore>();

    ~SliceResult();

    // Runs without the GIL: touches no Python object. Returns an empty string on success, else the error text,
    // which the caller turns into an exception after it has the GIL back.
    std::string write_gcode(const std::string &path) const noexcept;
};

void bind_result(nb::module_ &m);

} // namespace slicewright
