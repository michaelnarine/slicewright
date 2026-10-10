// SPDX-License-Identifier: AGPL-3.0-only
// The engine's own pre-flight checks (04 section 4.2): an object outside the bed, an object that is too tall,
// paint beyond the filament count. They run before Orca's Print::validate and report through `Issue`.
#pragma once

#include <string>
#include <vector>

#include "issues.hpp"

#include "libslic3r/Model.hpp"
#include "libslic3r/PrintConfig.hpp"

namespace slicewright {

// Number of filaments the composed config describes (the length of filament_colour, at least 1).
size_t filament_count(const Slic3r::DynamicPrintConfig &cfg);

// The bed as 04 defines it: `printable_area` minus `bed_exclude_area` (quads of four points, as in Orca's
// PartPlate). Returns errors `object_outside_bed` and `object_too_tall`, one per offending object.
std::vector<Issue> check_bed_and_height(const Slic3r::Model &model, const std::vector<std::string> &names,
                                        const Slic3r::DynamicPrintConfig &cfg);

} // namespace slicewright
